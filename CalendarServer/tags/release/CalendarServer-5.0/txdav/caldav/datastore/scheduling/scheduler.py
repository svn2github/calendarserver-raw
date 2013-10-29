
# Copyright (c) 2005-2013 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python.failure import Failure

from twext.enterprise.locking import NamedLock
from twext.python.log import Logger, LoggingMixIn
from twext.web2 import responsecode
from twext.web2.http import HTTPError, Response
from twext.web2.http_headers import MimeType
from txdav.xml import element as davxml
from twext.web2.dav.http import messageForFailure, statusForFailure, \
    ErrorResponse

from twistedcaldav import caldavxml
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.accounting import accountingEnabled, emitAccounting
from twistedcaldav.config import config
from twistedcaldav.ical import Component
from txdav.caldav.datastore.scheduling import addressmapping
from txdav.caldav.datastore.scheduling.caldav.delivery import ScheduleViaCalDAV
from txdav.caldav.datastore.scheduling.cuaddress import InvalidCalendarUser, \
    calendarUserFromPrincipal, OtherServerCalendarUser
from txdav.caldav.datastore.scheduling.cuaddress import LocalCalendarUser
from txdav.caldav.datastore.scheduling.cuaddress import RemoteCalendarUser
from txdav.caldav.datastore.scheduling.cuaddress import EmailCalendarUser
from txdav.caldav.datastore.scheduling.cuaddress import PartitionedCalendarUser
from txdav.caldav.datastore.scheduling.imip.delivery import ScheduleViaIMip
from txdav.caldav.datastore.scheduling.ischedule.delivery import ScheduleViaISchedule
from txdav.caldav.datastore.scheduling.itip import iTIPRequestStatus
import hashlib

"""
CalDAV/Server-to-Server scheduling behavior.

This module handles the delivery of scheduling messages to organizer and attendees. The basic idea is to first
confirm the integrity of the incoming scheduling message, check authorization. Appropriate L{DeliveryService}s
are then used to deliver the message to attendees or organizer. Delivery responses are processed and returned.
This takes into account partitioning and podding of users by detecting the appropriate host for a calendar
user and then dispatching the delivery accordingly.

The L{Scheduler} class defines the basic behavior for processing deliveries. Sub-classes are defined for the
different ways a deliver can be triggered.

L{CalDAVScheduler} - handles deliveries for scheduling messages originating from inside the CalDAV server
i.e. user PUTs or POSTs.

L{IScheduleScheduler} - handles deliveries for scheduling messages being POSTed to the iSchedule inbox.

L{IMIPScheduler} - handles deliveries for POSTs on the iMIP inbox (coming from the mail gateway).

L{DirectScheduler} - used when doing some internal processing (e.g., inbox item processing during an
upgrade.

Here is a typical flow of activity for a iTIP between users on the server:

iTIP PUT request
\
 \_L{ImplicitScheduler}           - does CalDAV-schedule logic and sends iTIP message
   \
    \_L{CalDAVScheduler}          - receives iTIP message
      \
       \_L{ScheduleViaCalDAV}     - handles delivery of iTIP message
         \
          \_L{ImplicitProcessor}  - dispatches iTIP message (also auto-accept)
            \
             \_L{iTipProcessing}  - processes iTIP message

Here is a typical flow of activity for a iTIP between an organizer on the server and an iMIP attendee:

iTIP PUT request
\
 \_L{ImplicitScheduler}
   \
    \_L{CalDAVScheduler}
      \
       \_L{ScheduleViaIMip}

Here is a typical flow of activity for a iTIP between an organizer not on the server and attendee on the server:

iTIP POST on /ischedule
\
 \_L{IScheduleScheduler}
   \
    \_L{ScheduleViaCalDAV}
      \
       \_L{ImplicitProcessor}
         \
          \_L{iTipProcessing}

"""

__all__ = [
    "Scheduler",
    "RemoteScheduler",
    "DirectScheduler",
]


log = Logger()

class Scheduler(object):

    scheduleResponse = None

    errorResponse = None # The class used for generating an HTTP XML error response

    errorElements = {
        "originator-missing": (),
        "originator-invalid": (),
        "originator-denied": (),
        "recipient-missing": (),
        "recipient-invalid": (),
        "organizer-denied": (),
        "attendee-denied": (),
        "invalid-calendar-data-type": (),
        "invalid-calendar-data": (),
        "invalid-scheduling-message": (),
        "max-recipients": (),
    }

    def __init__(self, txn, originator_uid, logItems=None, noAttendeeRefresh=False):

        self.txn = txn
        self.originator_uid = originator_uid
        self.logItems = logItems
        self.noAttendeeRefresh = noAttendeeRefresh

        self.originator = None
        self.recipients = None
        self.calendar = None
        self.organizer = None
        self.attendee = None
        self.isiTIPRequest = None
        self.timeRange = None
        self.excludeUID = None
        self.fakeTheResult = False
        self.method = "Unknown"
        self.internal_request = False


    @inlineCallbacks
    def doSchedulingViaPOST(self, originator, recipients, calendar):
        """
        The Scheduling POST operation on an Outbox.
        """

        self.calendar = calendar
        self.preProcessCalendarData()

        if self.logItems is not None:
            self.logItems["recipients"] = len(recipients)
            self.logItems["cl"] = str(len(str(calendar)))

        # We might trigger an implicit scheduling operation here that will require consistency
        # of data for all events with the same UID. So detect this and use a lock
        if calendar.resourceType() != "VFREEBUSY":
            uid = calendar.resourceUID()
            yield NamedLock.acquire(self.txn, "ImplicitUIDLock:%s" % (hashlib.md5(uid).hexdigest(),))

        result = (yield self.doSchedulingDirectly("POST", originator, recipients, calendar))
        returnValue(result)


    def doSchedulingViaPUT(self, originator, recipients, calendar, internal_request=False, suppress_refresh=False):
        """
        The implicit scheduling PUT operation.
        """
        return self.doSchedulingDirectly("PUT", originator, recipients, calendar, internal_request, suppress_refresh)


    def doSchedulingDirectly(self, descriptor, originator, recipients, calendar, internal_request=False, suppress_refresh=False):
        """
        The implicit scheduling operation.
        """

        self.method = descriptor

        # Load various useful bits doing some basic checks on those
        self.originator = originator
        self.recipients = recipients
        self.calendar = calendar
        self.internal_request = internal_request
        self.suppress_refresh = suppress_refresh

        # Do some extra authorization checks
        self.checkAuthorization()

        return self.doScheduling()


    @inlineCallbacks
    def doScheduling(self):
        # Check validity of Originator header.
        yield self.checkOriginator()

        # Get recipient details.
        yield self.checkRecipients()

        # Check calendar data.
        self.checkCalendarData()

        # Check validity of ORGANIZER
        yield self.checkOrganizer()

        # Do security checks (e.g. spoofing)
        yield self.securityChecks()

        # Generate accounting information
        self.doAccounting()

        # Do some final checks after we have gathered all our information
        self.finalChecks()

        # Do scheduling tasks
        result = (yield self.generateSchedulingResponse())

        returnValue(result)


    @inlineCallbacks
    def loadFromRequestData(self):
        self.loadOriginatorFromRequestDetails()
        self.loadRecipientsFromCalendarData()


    def loadOriginatorFromRequestDetails(self):
        # Get the originator who is the authenticated user
        originatorPrincipal = self.txn.directoryService().recordWithUID(self.originator_uid)

        # Pick the canonical CUA:
        originator = originatorPrincipal.canonicalCalendarUserAddress() if originatorPrincipal else ""

        if not originator:
            log.error("%s request must have Originator" % (self.method,))
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["originator-missing"],
                "Missing originator",
            ))
        else:
            self.originator = originator


    def loadRecipientsFromCalendarData(self):

        # Get the ATTENDEEs
        attendees = list()
        unique_set = set()
        for attendee, _ignore in self.calendar.getAttendeesByInstance():
            if attendee not in unique_set:
                attendees.append(attendee)
                unique_set.add(attendee)

        if not attendees:
            log.error("%s request must have at least one Recipient" % (self.method,))
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["recipient-missing"],
                "Must have recipients",
            ))
        else:
            self.recipients = list(attendees)


    def preProcessCalendarData(self):
        """
        After loading calendar data from the request, do some optional processing of it. This method will be
        overridden by those schedulers that need to do special things to the data.
        """
        pass


    def checkAuthorization(self):
        raise NotImplementedError


    def checkOriginator(self):
        raise NotImplementedError


    def checkRecipients(self):
        raise NotImplementedError


    def checkOrganizer(self):
        raise NotImplementedError


    def checkOrganizerAsOriginator(self):
        raise NotImplementedError


    def checkAttendeeAsOriginator(self):
        raise NotImplementedError


    def checkCalendarData(self):

        # Skip all the valid data checks for an internal request as we are going to assume all the internal
        # request data has been generated properly.

        if not self.internal_request:
            # Must be a valid calendar
            try:
                self.calendar.validCalendarData()
            except ValueError, e:
                log.error("%s request calendar component is not valid:%s %s" % (self.method, e, self.calendar,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["invalid-calendar-data"],
                    description="Calendar component is not valid"
                ))

            # Must have a METHOD
            if not self.calendar.isValidMethod():
                log.error("%s request must have valid METHOD property in calendar component: %s" % (self.method, self.calendar,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["invalid-scheduling-message"],
                    description="Must have valid METHOD property"
                ))

            # Verify iTIP behavior
            if not self.calendar.isValidITIP():
                log.error("%s request must have a calendar component that satisfies iTIP requirements: %s" % (self.method, self.calendar,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["invalid-scheduling-message"],
                    description="Must have a calendar component that satisfies iTIP requirements"
                ))

            # X-CALENDARSERVER-ACCESS is not allowed in Outbox POSTs
            if self.calendar.hasProperty(Component.ACCESS_PROPERTY):
                log.error("X-CALENDARSERVER-ACCESS not allowed in a calendar component %s request: %s" % (self.method, self.calendar,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    (calendarserver_namespace, "no-access-restrictions"),
                    "Private events cannot be scheduled",
                ))

        # Determine iTIP method mode
        if self.calendar.propertyValue("METHOD") in ("PUBLISH", "REQUEST", "ADD", "CANCEL", "DECLINECOUNTER"):
            self.isiTIPRequest = True

        elif self.calendar.propertyValue("METHOD") in ("REPLY", "COUNTER", "REFRESH"):
            self.isiTIPRequest = False

            # Verify that there is a single ATTENDEE property
            attendees = self.calendar.getAttendees()

            # Must have only one
            if len(attendees) != 1:
                log.error("Wrong number of ATTENDEEs in calendar data: %s" % (str(self.calendar),))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["invalid-scheduling-message"],
                    "Wrong number of attendees",
                ))
            self.attendee = attendees[0]

        else:
            msg = "Unknown iTIP METHOD: %s" % (self.calendar.propertyValue("METHOD"),)
            log.error(msg)
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["invalid-scheduling-message"],
                description=msg
            ))


    def checkForFreeBusy(self):
        if not hasattr(self, "isfreebusy"):
            if (self.calendar.propertyValue("METHOD") == "REQUEST") and (self.calendar.mainType() == "VFREEBUSY"):
                # Extract time range from VFREEBUSY object
                vfreebusies = [v for v in self.calendar.subcomponents() if v.name() == "VFREEBUSY"]
                if len(vfreebusies) != 1:
                    log.error("iTIP data is not valid for a VFREEBUSY request: %s" % (self.calendar,))
                    raise HTTPError(self.errorResponse(
                        responsecode.FORBIDDEN,
                        self.errorElements["invalid-scheduling-message"],
                        "iTIP data is not valid for a VFREEBUSY request",
                    ))
                dtstart = vfreebusies[0].getStartDateUTC()
                dtend = vfreebusies[0].getEndDateUTC()
                if dtstart is None or dtend is None:
                    log.error("VFREEBUSY start/end not valid: %s" % (self.calendar,))
                    raise HTTPError(self.errorResponse(
                        responsecode.FORBIDDEN,
                        self.errorElements["invalid-scheduling-message"],
                        "VFREEBUSY start/end not valid",
                    ))

                # Some clients send floating instead of UTC - coerce to UTC
                if not dtstart.utc() or not dtend.utc():
                    log.error("VFREEBUSY start or end not UTC: %s" % (self.calendar,))
                    raise HTTPError(self.errorResponse(
                        responsecode.FORBIDDEN,
                        self.errorElements["invalid-scheduling-message"],
                        "VFREEBUSY start or end not UTC",
                    ))

                self.timeRange = caldavxml.TimeRange(start=dtstart.getText(), end=dtend.getText())
                self.timeRange.start = dtstart
                self.timeRange.end = dtend

                # Look for masked UID
                self.excludeUID = self.calendar.getMaskUID()

                # Do free busy operation
                self.isfreebusy = True
            else:
                # Do regular invite (fan-out)
                self.isfreebusy = False

        return self.isfreebusy


    def securityChecks(self):
        raise NotImplementedError


    def doAccounting(self):
        #
        # Accounting
        #
        # Note that we associate logging with the organizer, not the
        # originator, which is good for looking for why something
        # shows up in a given principal's calendars, rather than
        # tracking the activities of a specific user.
        #
        if isinstance(self.organizer, LocalCalendarUser):
            accountingType = "iTIP-VFREEBUSY" if self.calendar.mainType() == "VFREEBUSY" else "iTIP"
            if accountingEnabled(accountingType, self.organizer.principal):
                emitAccounting(
                    accountingType, self.organizer.principal,
                    "Originator: %s\nRecipients:\n%sMethod:%s\n\n%s"
                    % (
                        str(self.originator),
                        str("".join(["    %s\n" % (recipient,) for recipient in self.recipients])),
                        str(self.method),
                        str(self.calendar),
                    )
                )


    def finalChecks(self):
        """
        Final checks before doing the actual scheduling.
        """
        pass


    @inlineCallbacks
    def generateSchedulingResponse(self):

        log.info("METHOD: %s, Component: %s" % (self.calendar.propertyValue("METHOD"), self.calendar.mainType(),))

        # For free-busy do immediate determination of iTIP result rather than fan-out
        freebusy = self.checkForFreeBusy()

        # Prepare for multiple responses
        responses = self.scheduleResponse(self.method, responsecode.OK)

        # Loop over each recipient and aggregate into lists by service types.
        caldav_recipients = []
        partitioned_recipients = []
        otherserver_recipients = []
        remote_recipients = []
        imip_recipients = []
        for ctr, recipient in enumerate(self.recipients):

            # Check for freebusy limit
            if freebusy and config.Scheduling.Options.LimitFreeBusyAttendees and ctr >= config.Scheduling.Options.LimitFreeBusyAttendees:
                err = HTTPError(self.errorResponse(
                    responsecode.NOT_FOUND,
                    self.errorElements["max-recipients"],
                    "Too many attendees",
                ))
                responses.add(recipient.cuaddr, Failure(exc_value=err), reqstatus=iTIPRequestStatus.SERVICE_UNAVAILABLE)
                continue

            if self.fakeTheResult:
                responses.add(recipient.cuaddr, responsecode.OK, reqstatus=iTIPRequestStatus.SUCCESS if freebusy else iTIPRequestStatus.MESSAGE_DELIVERED)

            elif isinstance(recipient, LocalCalendarUser):
                caldav_recipients.append(recipient)

            elif isinstance(recipient, PartitionedCalendarUser):
                partitioned_recipients.append(recipient)

            elif isinstance(recipient, OtherServerCalendarUser):
                otherserver_recipients.append(recipient)

            elif isinstance(recipient, RemoteCalendarUser):
                remote_recipients.append(recipient)

            elif isinstance(recipient, EmailCalendarUser):
                imip_recipients.append(recipient)

            else:
                err = HTTPError(self.errorResponse(
                    responsecode.NOT_FOUND,
                    self.errorElements["recipient-invalid"],
                    "Unknown recipient",
                ))
                responses.add(recipient.cuaddr, Failure(exc_value=err), reqstatus=iTIPRequestStatus.INVALID_CALENDAR_USER)

        # Now process local recipients
        if caldav_recipients:
            yield self.generateLocalSchedulingResponses(caldav_recipients, responses, freebusy)

        # Now process partitioned recipients
        if partitioned_recipients:
            yield self.generateRemoteSchedulingResponses(partitioned_recipients, responses, freebusy, getattr(self.txn, 'doing_attendee_refresh', False))

        # Now process other server recipients
        if otherserver_recipients:
            yield self.generateRemoteSchedulingResponses(otherserver_recipients, responses, freebusy, getattr(self.txn, 'doing_attendee_refresh', False))

        # To reduce chatter, we suppress certain messages
        if not self.suppress_refresh:

            # Now process remote recipients
            if remote_recipients:
                yield self.generateRemoteSchedulingResponses(remote_recipients, responses, freebusy)

            # Now process iMIP recipients
            if imip_recipients:
                yield self.generateIMIPSchedulingResponses(imip_recipients, responses, freebusy)

        # Return with final response if we are done
        returnValue(responses)


    def generateLocalSchedulingResponses(self, recipients, responses, freebusy):
        """
        Generate scheduling responses for CalDAV recipients.
        """

        # Create the scheduler and run it.
        requestor = ScheduleViaCalDAV(self, recipients, responses, freebusy)
        return requestor.generateSchedulingResponses()


    def generateRemoteSchedulingResponses(self, recipients, responses, freebusy, refreshOnly=False):
        """
        Generate scheduling responses for remote recipients.
        """

        # Create the scheduler and run it.
        requestor = ScheduleViaISchedule(self, recipients, responses, freebusy)
        return requestor.generateSchedulingResponses(refreshOnly)


    def generateIMIPSchedulingResponses(self, recipients, responses, freebusy):
        """
        Generate scheduling responses for iMIP recipients.
        """

        # Create the scheduler and run it.
        requestor = ScheduleViaIMip(self, recipients, responses, freebusy)
        return requestor.generateSchedulingResponses()



class RemoteScheduler(Scheduler):

    def checkOrganizer(self):
        """
        Delay ORGANIZER check until we know what their role is.
        """
        pass


    @inlineCallbacks
    def checkRecipients(self):
        """
        Check the validity of the Recipient header values. These must all be local as there
        is no concept of server-to-server relaying.
        """

        results = []
        for recipient in self.recipients:
            # Get the principal resource for this recipient
            principal = self.txn.directoryService().recordWithCalendarUserAddress(recipient)

            # If no principal we may have a remote recipient but we should check whether
            # the address is one that ought to be on our server and treat that as a missing
            # user. Also if server-to-server is not enabled then remote addresses are not allowed.
            if principal is None:
                localUser = (yield addressmapping.mapper.isCalendarUserInMyDomain(recipient))
                if localUser:
                    log.error("No principal for calendar user address: %s" % (recipient,))
                else:
                    log.error("Unknown calendar user address: %s" % (recipient,))
                results.append(InvalidCalendarUser(recipient))
            else:
                # Map recipient to their inbox
                inbox = None
                if principal.calendarsEnabled() and principal.thisServer():
                    if principal.locallyHosted():
                        recipient_home = yield self.txn.calendarHomeWithUID(principal.uid, create=True)
                        if recipient_home:
                            inbox = (yield recipient_home.calendarWithName("inbox"))
                    else:
                        inbox = "dummy"

                if inbox:
                    results.append(calendarUserFromPrincipal(recipient, principal, inbox))
                else:
                    log.error("No schedule inbox for principal: %s" % (principal,))
                    results.append(InvalidCalendarUser(recipient))

        self.recipients = results



class DirectScheduler(Scheduler):
    """ An implicit scheduler meant for use by local processes which don't
        need to go through all these checks. """

    errorResponse = ErrorResponse

    def checkAuthorization(self):
        pass


    def checkOrganizer(self):
        pass


    def checkOrganizerAsOriginator(self):
        pass


    def checkAttendeeAsOriginator(self):
        pass


    def securityChecks(self):
        pass


    def checkOriginator(self):
        pass


    def checkRecipients(self):
        pass



class ScheduleResponseResponse (Response):
    """
    ScheduleResponse L{Response} object.
    Renders itself as a CalDAV:schedule-response XML document.
    """
    def __init__(self, schedule_response_element, xml_responses, location=None):
        """
        @param xml_responses: an iterable of davxml.Response objects.
        @param location:      the value of the location header to return in the response,
            or None.
        """

        Response.__init__(self, code=responsecode.OK,
                          stream=schedule_response_element(*xml_responses).toxml())

        self.headers.setHeader("content-type", MimeType("text", "xml"))

        if location is not None:
            self.headers.setHeader("location", location)



class ScheduleResponseQueue (LoggingMixIn):
    """
    Stores a list of (typically error) responses for use in a
    L{ScheduleResponse}.
    """

    schedule_response_element = caldavxml.ScheduleResponse
    response_element = caldavxml.Response
    recipient_element = caldavxml.Recipient
    recipient_uses_href = True
    request_status_element = caldavxml.RequestStatus
    error_element = davxml.Error
    response_description_element = davxml.ResponseDescription
    calendar_data_element = caldavxml.CalendarData

    def __init__(self, method, success_response):
        """
        @param method: the name of the method generating the queue.
        @param success_response: the response to return in lieu of a
            L{ScheduleResponse} if no responses are added to this queue.
        """
        self.responses = []
        self.method = method
        self.success_response = success_response
        self.location = None


    def setLocation(self, location):
        """
        @param location:      the value of the location header to return in the response,
            or None.
        """
        self.location = location


    def add(self, recipient, what, reqstatus=None, calendar=None, suppressErrorLog=False):
        """
        Add a response.
        @param recipient: the recipient for this response.
        @param what: a status code or a L{Failure} for the given recipient.
        @param status: the iTIP request-status for the given recipient.
        @param calendar: the calendar data for the given recipient response.
        @param suppressErrorLog: whether to suppress a log message for errors; primarily
            this is used when trying to process a VFREEBUSY over iMIP, which isn't
            supported.
        """
        if type(what) is int:
            code = what
            error = None
            message = responsecode.RESPONSES[code]
        elif isinstance(what, Failure):
            code = statusForFailure(what)
            error = self.errorForFailure(what)
            message = messageForFailure(what)
        else:
            raise AssertionError("Unknown data type: %r" % (what,))

        if not suppressErrorLog and code > 400: # Error codes only
            self.log_error("Error during %s for %s: %s" % (self.method, recipient, message))

        children = []
        children.append(self.recipient_element(davxml.HRef.fromString(recipient)) if self.recipient_uses_href else self.recipient_element.fromString(recipient))
        children.append(self.request_status_element(reqstatus))
        if calendar is not None:
            children.append(self.calendar_data_element.fromCalendar(calendar))
        if error is not None:
            children.append(error)
        if message is not None:
            children.append(self.response_description_element(message))
        self.responses.append(self.response_element(*children))


    def errorForFailure(self, failure):
        if failure.check(HTTPError) and isinstance(failure.value.response, ErrorResponse):
            return self.error_element(failure.value.response.error)
        else:
            return None


    def clone(self, recipient, request_status, calendar_data, error, desc):
        """
        Add a response cloned from existing data.
        @param clone: the response to clone.
        """

        children = []
        children.append(self.recipient_element(davxml.HRef.fromString(recipient)) if self.recipient_uses_href else self.recipient_element.fromString(recipient))
        children.append(self.request_status_element.fromString(request_status))
        if calendar_data is not None:
            children.append(self.calendar_data_element.fromCalendar(calendar_data))
        if error is not None:
            children.append(self.error_element(*error))
        if desc is not None:
            children.append(self.response_description_element.fromString(desc))
        self.responses.append(self.response_element(*children))


    def response(self):
        """
        Generate a L{ScheduleResponseResponse} with the responses contained in the
        queue or, if no such responses, return the C{success_response} provided
        to L{__init__}.
        @return: the response.
        """
        if self.responses:
            return ScheduleResponseResponse(self.schedule_response_element, self.responses, self.location)
        else:
            return self.success_response
