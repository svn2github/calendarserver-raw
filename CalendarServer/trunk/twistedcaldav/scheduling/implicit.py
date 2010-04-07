#
# Copyright (c) 2005-2010 Apple Inc. All rights reserved.
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

from twext.python.log import Logger
from twext.web2.dav.http import ErrorResponse

from twisted.internet.defer import inlineCallbacks, returnValue
from twext.web2 import responsecode
from twext.web2.dav import davxml
from twext.web2.dav.util import joinURL
from twext.web2.dav.util import parentForURL
from twext.web2.http import HTTPError

from twistedcaldav import caldavxml
from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.customxml import TwistedSchedulingObjectResource
from twistedcaldav.directory.principal import DirectoryCalendarPrincipalResource
from twistedcaldav.ical import Property
from twistedcaldav.method import report_common
from twistedcaldav.scheduling import addressmapping
from twistedcaldav.scheduling.cuaddress import InvalidCalendarUser,\
    LocalCalendarUser, PartitionedCalendarUser
from twistedcaldav.scheduling.icaldiff import iCalDiff
from twistedcaldav.scheduling.itip import iTipGenerator, iTIPRequestStatus
from twistedcaldav.scheduling.scheduler import CalDAVScheduler
from twistedcaldav.scheduling.utils import getCalendarObjectForPrincipals
from twistedcaldav.config import config

__all__ = [
    "ImplicitScheduler",
]

log = Logger()

# TODO:
#
# Handle the case where a PUT removes the ORGANIZER property. That should be equivalent to cancelling the entire meeting.
# Support Schedule-Reply header
#

class ImplicitScheduler(object):
    
    # Return Status codes
    STATUS_OK                       = 0
    STATUS_ORPHANED_CANCELLED_EVENT = 1
    STATUS_ORPHANED_EVENT           = 2

    def __init__(self):
        
        self.return_status = ImplicitScheduler.STATUS_OK

    @inlineCallbacks
    def testImplicitSchedulingPUT(self, request, resource, resource_uri, calendar, internal_request=False):
        
        self.request = request
        self.resource = resource
        self.calendar = calendar
        self.internal_request = internal_request

        existing_resource = resource.exists()
        is_scheduling_object = (yield self.checkSchedulingObjectResource(resource))
        existing_type = "schedule" if is_scheduling_object else "calendar"
        new_type = "schedule" if (yield self.checkImplicitState()) else "calendar"

        if existing_type == "calendar":
            self.action = "create" if new_type == "schedule" else "none"
        else:
            self.action = "modify" if new_type == "schedule" else "remove"
                
        # Cannot create new resource with existing UID
        if not existing_resource or self.action == "create":
            yield self.hasCalendarResourceUIDSomewhereElse(resource, resource_uri, new_type)

        # If action is remove we actually need to get state from the existing scheduling object resource
        if self.action == "remove":
            # Also make sure that we return the new calendar being written rather than the old one
            # when the implicit action is executed
            self.return_calendar = calendar
            self.calendar = (yield resource.iCalendarForUser(request))
            yield self.checkImplicitState()
        
        # Attendees are not allowed to overwrite one type with another
        if not self.internal_request and self.state == "attendee" and (existing_type != new_type) and existing_resource:
            raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-attendee-change")))

        returnValue((self.action != "none", new_type == "schedule",))

    @inlineCallbacks
    def testImplicitSchedulingMOVE(self, request, srcresource, srccal, src_uri, destresource, destcal, dest_uri, calendar, internal_request=False):
        
        self.request = request
        self.resource = destresource
        self.calendar = calendar
        self.internal_request = internal_request

        new_type = "schedule" if (yield self.checkImplicitState()) else "calendar"

        dest_exists = destresource.exists()
        dest_is_implicit = (yield self.checkSchedulingObjectResource(destresource))
        src_is_implicit = (yield self.checkSchedulingObjectResource(srcresource)) or new_type == "schedule"

        if srccal and destcal:
            if src_is_implicit and dest_exists or dest_is_implicit:
                log.debug("Implicit - cannot MOVE with a scheduling object resource")
                raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "unique-scheduling-object-resource")))
            else:
                self.action = "none"
        elif srccal and not destcal:
            result = (yield self.testImplicitSchedulingDELETE(request, srcresource, calendar))
            returnValue((result[0], new_type == "schedule",))
        elif not srccal and destcal:
            result = (yield self.testImplicitSchedulingPUT(request, destresource, dest_uri, calendar))
            returnValue(result)
        else:
            self.action = "none"

        returnValue((self.action != "none", new_type == "schedule",))

    @inlineCallbacks
    def testImplicitSchedulingCOPY(self, request, srcresource, srccal, src_uri, destresource, destcal, dest_uri, calendar, internal_request=False):
        
        self.request = request
        self.resource = destresource
        self.calendar = calendar
        self.internal_request = internal_request

        new_type = "schedule" if (yield self.checkImplicitState()) else "calendar"

        dest_is_implicit = (yield self.checkSchedulingObjectResource(destresource))
        src_is_implicit = (yield self.checkSchedulingObjectResource(srcresource)) or new_type == "schedule"

        if srccal and destcal:
            if src_is_implicit or dest_is_implicit:
                log.debug("Implicit - cannot COPY with a scheduling object resource")
                raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "unique-scheduling-object-resource")))
            else:
                self.action = "none"
        elif srccal and not destcal:
            self.action = "none"
        elif not srccal and destcal:
            result = (yield self.testImplicitSchedulingPUT(request, destresource, dest_uri, calendar))
            returnValue(result)
        else:
            self.action = "none"

        returnValue((self.action != "none", src_is_implicit,))

    @inlineCallbacks
    def testImplicitSchedulingDELETE(self, request, resource, calendar, internal_request=False):
        
        self.request = request
        self.resource = resource
        self.calendar = calendar
        self.internal_request = internal_request

        yield self.checkImplicitState()

        is_scheduling_object = (yield self.checkSchedulingObjectResource(resource))
        resource_type = "schedule" if is_scheduling_object else "calendar"
        self.action = "remove" if resource_type == "schedule" else "none"

        returnValue((self.action != "none", False,))

    @inlineCallbacks
    def checkSchedulingObjectResource(self, resource):
        
        if resource and resource.exists():
            try:
                implicit = resource.readDeadProperty(TwistedSchedulingObjectResource)
            except HTTPError:
                implicit = None
            if implicit is not None:
                returnValue(implicit != "false")
            else:
                calendar = (yield resource.iCalendarForUser(self.request))
                # Get the ORGANIZER and verify it is the same for all components
                try:
                    organizer = calendar.validOrganizerForScheduling()
                except ValueError:
                    # We have different ORGANIZERs in the same iCalendar object - this is an error
                    returnValue(False)
                organizerPrincipal = resource.principalForCalendarUserAddress(organizer) if organizer else None
                resource.writeDeadProperty(TwistedSchedulingObjectResource("true" if organizerPrincipal != None else "false"))
                log.debug("Implicit - checked scheduling object resource state for UID: '%s', result: %s" % (
                    calendar.resourceUID(),
                    "true" if organizerPrincipal != None else "false",
                ))
                returnValue(organizerPrincipal != None)

        returnValue(False)
        
    @inlineCallbacks
    def checkImplicitState(self):
        # Get some useful information from the calendar
        yield self.extractCalendarData()
        self.calendar_owner = (yield self.resource.owner(self.request))

        # Determine what type of scheduling this is: Organizer triggered or Attendee triggered
        organizer_scheduling = (yield self.isOrganizerScheduling())
        if organizer_scheduling:
            self.state = "organizer"
        elif self.isAttendeeScheduling():
            self.state = "attendee"
        else:
            self.state = None

        returnValue(self.state is not None)

    @inlineCallbacks
    def doImplicitScheduling(self, do_smart_merge=False):
        """
        Do implicit scheduling operation based on the data already set by call to checkImplicitScheduling.

        @param do_smart_merge: if True, merge attendee data on disk with new data being stored,
            else overwrite data on disk.
        @return: a new calendar object modified with scheduling information,
            or C{None} if nothing happened or C{int} if some other state occurs
        """
        
        # Setup some parameters
        self.do_smart_merge = do_smart_merge
        self.except_attendees = ()

        # Determine what type of scheduling this is: Organizer triggered or Attendee triggered
        if self.state == "organizer":
            yield self.doImplicitOrganizer()
        elif self.state == "attendee":
            yield self.doImplicitAttendee()
        else:
            returnValue(None)

        if self.return_status:
            returnValue(self.return_status)
        else:
            returnValue(self.return_calendar if hasattr(self, "return_calendar") else self.calendar)

    @inlineCallbacks
    def refreshAllAttendeesExceptSome(self, request, resource, calendar, attendees):
        """
        
        @param request:
        @type request:
        @param attendee:
        @type attendee:
        @param calendar:
        @type calendar:
        """

        self.request = request
        self.resource = resource
        self.calendar = calendar
        self.state = "organizer"
        self.action = "modify"

        self.calendar_owner = None
        self.internal_request = True
        self.except_attendees = attendees
        self.changed_rids = None
        self.reinvites = None

        
        # Get some useful information from the calendar
        yield self.extractCalendarData()
        self.organizerPrincipal = self.resource.principalForCalendarUserAddress(self.organizer)
        self.organizerAddress = (yield addressmapping.mapper.getCalendarUser(self.organizer, self.organizerPrincipal))

        # Originator is the organizer in this case
        self.originatorPrincipal = self.organizerPrincipal
        self.originator = self.organizer
        
        # We want to suppress chatty iMIP messages when other attendees reply
        self.request.suppressRefresh = False

        for attendee in self.calendar.getAllAttendeeProperties():
            if attendee.params().get("PARTSTAT", ["NEEDS-ACTION"])[0] == "NEEDS-ACTION":
                self.request.suppressRefresh = True
        
        if hasattr(self.request, "doing_attendee_refresh"):
            self.request.doing_attendee_refresh += 1
        else:
            self.request.doing_attendee_refresh = 1
        try:
            refreshCount = (yield self.processRequests())
        finally:
            self.request.doing_attendee_refresh -= 1
            if self.request.doing_attendee_refresh == 0:
                delattr(self.request, "doing_attendee_refresh")

        if refreshCount:
            if not hasattr(self.request, "extendedLogItems"):
                self.request.extendedLogItems = {}
            self.request.extendedLogItems["itip.refreshes"] = refreshCount

    @inlineCallbacks
    def sendAttendeeReply(self, request, resource, calendar, attendee):
        
        self.request = request
        self.resource = resource
        self.calendar = calendar
        self.action = "modify"
        self.state = "attendee"

        self.calendar_owner = None
        self.internal_request = True
        self.changed_rids = None
        
        # Get some useful information from the calendar
        yield self.extractCalendarData()        

        self.originator = self.attendee = attendee.cuaddr
        self.attendeePrincipal = attendee.principal
        
        result = (yield self.scheduleWithOrganizer())

        returnValue(result)

    @inlineCallbacks
    def extractCalendarData(self):
        
        # Get the originator who is the authenticated user
        # TODO: the originator actually needs to be the owner of the calendar collection not the authenticated
        # principal, who might be a proxy or admin
        self.originatorPrincipal = None
        self.originator = ""
        authz_principal = self.resource.currentPrincipal(self.request).children[0]
        if isinstance(authz_principal, davxml.HRef):
            originatorPrincipalURL = str(authz_principal)
            if originatorPrincipalURL:
                self.originatorPrincipal = (yield self.request.locateResource(originatorPrincipalURL))
                if not isinstance(self.originatorPrincipal, DirectoryCalendarPrincipalResource):
                    log.error("Originator '%s' is not enabled for calendaring" % (originatorPrincipalURL,))
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "invalid-originator")))

                if self.originatorPrincipal:
                    # Pick the first mailto cu address or the first other type
                    for item in self.originatorPrincipal.calendarUserAddresses():
                        if not self.originator:
                            self.originator = item
                        if item.startswith("mailto:"):
                            self.originator = item
                            break

        # Get the ORGANIZER and verify it is the same for all components
        try:
            self.organizer = self.calendar.validOrganizerForScheduling()
        except ValueError:
            # We have different ORGANIZERs in the same iCalendar object - this is an error
            log.error("Only one ORGANIZER is allowed in an iCalendar object:\n%s" % (self.calendar,))
            raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "single-organizer")))
        
        # Get the ATTENDEEs
        self.attendeesByInstance = self.calendar.getAttendeesByInstance(True, onlyScheduleAgentServer=True)
        self.attendees = set()
        for attendee, _ignore in self.attendeesByInstance:
            self.attendees.add(attendee)
            
        # Some other useful things
        self.uid = self.calendar.resourceUID()
    
    @inlineCallbacks
    def hasCalendarResourceUIDSomewhereElse(self, check_resource, check_uri, type):
        """
        See if a calendar component with a matching UID exists anywhere in the calendar home of the
        current recipient owner and is not the resource being targeted.
        """

        # Don't care in some cases
        if self.internal_request or self.action == "remove":
            returnValue(None)

        # Get owner's calendar-home
        calendar_owner_principal = (yield self.resource.resourceOwnerPrincipal(self.request))
        calendar_home = calendar_owner_principal.calendarHome()
        
        check_parent_uri = parentForURL(check_uri)[:-1] if check_uri else None

        # FIXME: because of the URL->resource request mapping thing, we have to force the request
        # to recognize this resource
        self.request._rememberResource(calendar_home, calendar_home.url())

        # Run a UID query against the UID

        @inlineCallbacks
        def queryCalendarCollection(collection, collection_uri):
            rname = collection.index().resourceNameForUID(self.uid)
            if rname:
                child = (yield self.request.locateResource(joinURL(collection_uri, rname)))
                if child == check_resource:
                    returnValue(True)
                is_scheduling_object = (yield self.checkSchedulingObjectResource(child))
                matched_type = "schedule" if is_scheduling_object else "calendar"
                if (
                    collection_uri != check_parent_uri and
                    (type == "schedule" or matched_type == "schedule")
                ):
                    log.debug("Implicit - found component with same UID in a different collection: %s" % (check_uri,))
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "unique-scheduling-object-resource")))

                # Here we can always return true as the unique UID in a calendar collection
                # requirement will already have been tested.

            returnValue(True)

        # NB We are by-passing privilege checking here. That should be OK as the data found is not
        # exposed to the user.
        yield report_common.applyToCalendarCollections(calendar_home, self.request, calendar_home.url(), "infinity", queryCalendarCollection, None)

    @inlineCallbacks
    def isOrganizerScheduling(self):
        """
        Test whether this is a scheduling operation by an organizer
        """
        
        # First must have organizer property
        if not self.organizer:
            returnValue(False)
        
        # Organizer must map to a valid principal
        self.organizerPrincipal = self.resource.principalForCalendarUserAddress(self.organizer)
        self.organizerAddress = (yield addressmapping.mapper.getCalendarUser(self.organizer, self.organizerPrincipal))
        if not self.organizerPrincipal:
            returnValue(False)
        
        # Organizer must be the owner of the calendar resource
        if str(self.calendar_owner) != self.organizerPrincipal.principalURL():
            returnValue(False)

        returnValue(True)

    def isAttendeeScheduling(self):
        
        # First must have organizer property
        if not self.organizer:
            return False
        
        # Check to see whether any attendee is the owner
        for attendee in self.attendees:
            attendeePrincipal = self.resource.principalForCalendarUserAddress(attendee)
            if attendeePrincipal and attendeePrincipal.principalURL() == str(self.calendar_owner):
                self.attendee = attendee
                self.attendeePrincipal = attendeePrincipal
                return True
        
        return False

    @inlineCallbacks
    def doAccessControl(self, principal, is_organizer):
        """
        Check that the currently authorized user has the appropriate scheduling privilege
        on the principal's Outbox.

        @param principal:
        @type principal:
        @param is_organizer:
        @type is_organizer:
        """
        
        # Find outbox
        outboxURL = principal.scheduleOutboxURL()
        outbox = (yield self.request.locateResource(outboxURL))
        yield outbox.authorize(self.request, (caldavxml.ScheduleSend(),))

    @inlineCallbacks
    def doImplicitOrganizer(self):
        
        # Do access control
        if not self.internal_request:
            yield self.doAccessControl(self.organizerPrincipal, True)

        self.oldcalendar = None
        self.changed_rids = None
        self.cancelledAttendees = ()
        self.reinvites = None

        # Check for a delete
        if self.action == "remove":

            log.debug("Implicit - organizer '%s' is removing UID: '%s'" % (self.organizer, self.uid))
            self.oldcalendar = self.calendar

            # Cancel all attendees
            self.cancelledAttendees = [(attendee, None) for attendee in self.attendees]

        # Check for a new resource or an update
        elif self.action == "modify":

            # Read in existing data
            self.oldcalendar = (yield self.resource.iCalendarForUser(self.request))
            
            # Significant change
            no_change, self.changed_rids, reinvites, recurrence_reschedule = self.isOrganizerChangeInsignificant()
            if no_change:
                if reinvites:
                    log.debug("Implicit - organizer '%s' is re-inviting UID: '%s', attendees: %s" % (self.organizer, self.uid, ", ".join(reinvites)))
                    self.reinvites = reinvites
                else:
                    # Nothing to do
                    log.debug("Implicit - organizer '%s' is modifying UID: '%s' but change is not significant" % (self.organizer, self.uid))
                    returnValue(None)
            else:
                log.debug("Implicit - organizer '%s' is modifying UID: '%s'" % (self.organizer, self.uid))
    
                # Check for removed attendees
                if not recurrence_reschedule:
                    self.findRemovedAttendees()

        elif self.action == "create":
            log.debug("Implicit - organizer '%s' is creating UID: '%s'" % (self.organizer, self.uid))
            
        # Always set RSVP=TRUE for any NEEDS-ACTION
        for attendee in self.calendar.getAllAttendeeProperties():
            if attendee.params().get("PARTSTAT", ["NEEDS-ACTION"])[0] == "NEEDS-ACTION":
                attendee.params()["RSVP"] = ["TRUE",]

        yield self.scheduleWithAttendees()
        
        # Always clear SCHEDULE-FORCE-SEND from all attendees after scheduling
        for attendee in self.calendar.getAllAttendeeProperties():
            try:
                del attendee.params()["SCHEDULE-FORCE-SEND"]
            except KeyError:
                pass

    def isOrganizerChangeInsignificant(self):
        
        rids = None
        reinvites = None
        recurrence_reschedule = False
        differ = iCalDiff(self.oldcalendar, self.calendar, self.do_smart_merge)
        no_change = differ.organizerDiff()
        if not no_change:
            # ORGANIZER change is absolutely not allowed!
            diffs = differ.whatIsDifferent()
            rids = set()
            checkOrganizerValue = False
            for rid, props in diffs.iteritems():
                if "ORGANIZER" in props:
                    checkOrganizerValue = True
                rids.add(rid)
                
                # Check to see whether a change to R-ID's happened
                if rid == "":
                    if "RRULE" in props or "DTSTART" in props and self.calendar.masterComponent().hasProperty("RRULE"):
                        recurrence_reschedule = True

            if checkOrganizerValue:
                oldOrganizer = self.oldcalendar.getOrganizer()
                newOrganizer = self.calendar.getOrganizer()
                if oldOrganizer != newOrganizer:
                    log.error("Cannot change ORGANIZER: UID:%s" % (self.uid,))
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-attendee-change")))
        else:
            # Special case of SCHEDULE-FORCE-SEND added to attendees and no other change
            reinvites = set()
            for attendee in self.calendar.getAllAttendeeProperties():
                try:
                    if attendee.params()["SCHEDULE-FORCE-SEND"][0] == "REQUEST":
                        reinvites.add(attendee.value())
                except KeyError:
                    pass

        return no_change, rids, reinvites, recurrence_reschedule
    
    def findRemovedAttendees(self):
        """
        Look for attendees that have been removed from any instances. Save those off
        as users that need to be sent a cancel.
        """
        
        # Several possibilities for when CANCELs need to be sent:
        #
        # Remove ATTENDEE property
        # Add EXDATE
        # Remove overridden component
        # Remove RDATE
        # Truncate RRULE
        # Change RRULE
        
        # TODO: the later three will be ignored for now.

        oldAttendeesByInstance = self.oldcalendar.getAttendeesByInstance(onlyScheduleAgentServer=True)
        
        mappedOld = set(oldAttendeesByInstance)
        mappedNew = set(self.attendeesByInstance)
        
        # Get missing instances
        oldInstances = set(self.oldcalendar.getComponentInstances())
        newInstances = set(self.calendar.getComponentInstances())
        removedInstances = oldInstances - newInstances
        addedInstances = newInstances - oldInstances

        # Also look for new EXDATEs
        oldexdates = set()
        for property in self.oldcalendar.masterComponent().properties("EXDATE"):
            oldexdates.update(property.value())
        newexdates = set()
        for property in self.calendar.masterComponent().properties("EXDATE"):
            newexdates.update(property.value())

        addedexdates = newexdates - oldexdates

        # Now figure out the attendees that need to be sent CANCELs
        self.cancelledAttendees = set()
        
        for item in mappedOld:
            if item not in mappedNew:
                
                # Several possibilities:
                #
                # 1. removed from master component - always a CANCEL
                # 2. removed from overridden component - always a CANCEL
                # 3. removed overridden component - only CANCEL if not in master or exdate added
                 
                new_attendee, rid = item
                
                # 1. & 2.
                if rid is None or rid not in removedInstances:
                    self.cancelledAttendees.add(item)
                else:
                    # 3.
                    if (new_attendee, None) not in mappedNew or rid in addedexdates:
                        self.cancelledAttendees.add(item)

        master_attendees = self.oldcalendar.masterComponent().getAttendeesByInstance(onlyScheduleAgentServer=True)
        for attendee, _ignore in master_attendees:
            for exdate in addedexdates:
                # Don't remove the master attendee's when an EXDATE is added for a removed overridden component
                # as the set of attendees in the override may be different from the master set, but the override
                # will have been accounted for by the previous attendee/instance logic.
                if exdate not in removedInstances:
                    self.cancelledAttendees.add((attendee, exdate))

        # For overridden instances added, check whether any attendees were removed from the master
        for attendee, _ignore in master_attendees:
            for rid in addedInstances:
                if (attendee, rid) not in mappedNew and rid not in oldexdates:
                    self.cancelledAttendees.add((attendee, rid))

    @inlineCallbacks
    def scheduleWithAttendees(self):
        
        # First process cancelled attendees
        total = (yield self.processCancels())
        
        # Process regular requests next
        if self.action in ("create", "modify",):
            total += (yield self.processRequests())

        if not hasattr(self.request, "extendedLogItems"):
            self.request.extendedLogItems = {}
        self.request.extendedLogItems["itip.requests"] = total

    @inlineCallbacks
    def processCancels(self):
        
        # TODO: a better policy here is to aggregate by attendees with the same set of instances
        # being cancelled, but for now we will do one scheduling message per attendee.

        # Do one per attendee
        aggregated = {}
        for attendee, rid in self.cancelledAttendees:
            aggregated.setdefault(attendee, []).append(rid)
        
        count = 0
        for attendee, rids in aggregated.iteritems():
            
            # Don't send message back to the ORGANIZER
            if attendee in self.organizerPrincipal.calendarUserAddresses():
                continue

            # Generate an iTIP CANCEL message for this attendee, cancelling
            # each instance or the whole
            
            if None in rids:
                # One big CANCEL will do
                itipmsg = iTipGenerator.generateCancel(self.oldcalendar, (attendee,), None, self.action == "remove")
            else:
                # Multiple CANCELs
                itipmsg = iTipGenerator.generateCancel(self.oldcalendar, (attendee,), rids)

            # Send scheduling message
            
            # This is a local CALDAV scheduling operation.
            scheduler = CalDAVScheduler(self.request, self.resource)
    
            # Do the PUT processing
            log.info("Implicit CANCEL - organizer: '%s' to attendee: '%s', UID: '%s', RIDs: '%s'" % (self.organizer, attendee, self.uid, rids))
            response = (yield scheduler.doSchedulingViaPUT(self.originator, (attendee,), itipmsg, self.internal_request))
            self.handleSchedulingResponse(response, True)
            
            count += 1
            
        returnValue(count)
            
    @inlineCallbacks
    def processRequests(self):
        
        # TODO: a better policy here is to aggregate by attendees with the same set of instances
        # being requested, but for now we will do one scheduling message per attendee.

        # Do one per attendee
        count = 0
        for attendee in self.attendees:

            # Don't send message back to the ORGANIZER
            if attendee in self.organizerPrincipal.calendarUserAddresses():
                continue

            # Don't send message to specified attendees
            if attendee in self.except_attendees:
                continue

            # If SCHEDULE-FORCE-SEND only change, only send message to those Attendees
            if self.reinvites and attendee in self.reinvites:
                continue

            itipmsg = iTipGenerator.generateAttendeeRequest(self.calendar, (attendee,), self.changed_rids)

            # Send scheduling message
            if itipmsg is not None:
                # This is a local CALDAV scheduling operation.
                scheduler = CalDAVScheduler(self.request, self.resource)
        
                # Do the PUT processing
                log.info("Implicit REQUEST - organizer: '%s' to attendee: '%s', UID: '%s'" % (self.organizer, attendee, self.uid,))
                response = (yield scheduler.doSchedulingViaPUT(self.originator, (attendee,), itipmsg, self.internal_request))
                self.handleSchedulingResponse(response, True)
                
                count += 1
                
        returnValue(count)

    def handleSchedulingResponse(self, response, is_organizer):
        
        # Map each recipient in the response to a status code
        responses = {}
        for item in response.responses:
            assert isinstance(item, caldavxml.Response), "Wrong element in response"
            recipient = str(item.children[0].children[0])
            status = str(item.children[1])
            responses[recipient] = status
            
            # Now apply to each ATTENDEE/ORGANIZER in the original data
            self.calendar.setParameterToValueForPropertyWithValue(
                "SCHEDULE-STATUS",
                status.split(";")[0],
                "ATTENDEE" if is_organizer else "ORGANIZER",
                recipient)

    @inlineCallbacks
    def doImplicitAttendee(self):

        # Do access control
        if not self.internal_request:
            yield self.doAccessControl(self.attendeePrincipal, False)

        # Check SCHEDULE-AGENT
        doScheduling = self.checkOrganizerScheduleAgent()

        if self.action == "remove":
            if self.calendar.hasPropertyValueInAllComponents(Property("STATUS", "CANCELLED")):
                log.debug("Implicit - attendee '%s' is removing cancelled UID: '%s'" % (self.attendee, self.uid))
                # Nothing else to do
            elif doScheduling:
                log.debug("Implicit - attendee '%s' is cancelling UID: '%s'" % (self.attendee, self.uid))
                yield self.scheduleCancelWithOrganizer()
            else:
                log.debug("Implicit - attendee '%s' is removing UID without server scheduling: '%s'" % (self.attendee, self.uid))
                # Nothing else to do
        
        else:
            # Make sure ORGANIZER is not changed
            if self.resource.exists():
                self.oldcalendar = (yield self.resource.iCalendarForUser(self.request))
                oldOrganizer = self.oldcalendar.getOrganizer()
                newOrganizer = self.calendar.getOrganizer()
                if oldOrganizer != newOrganizer:
                    log.error("Cannot change ORGANIZER: UID:%s" % (self.uid,))
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-attendee-change")))
            else:
                self.oldcalendar = None
            
            # Get the ORGANIZER's current copy of the calendar object
            yield self.getOrganizersCopy()
            if self.organizer_calendar:

                # Determine whether the current change is allowed
                changeAllowed, doITipReply, changedRids, newCalendar = self.isAttendeeChangeInsignificant()
                if changeAllowed:
                    self.return_calendar = self.calendar = newCalendar

                if not changeAllowed:
                    if self.calendar.hasPropertyValueInAllComponents(Property("STATUS", "CANCELLED")):
                        log.debug("Attendee '%s' is creating CANCELLED event for mismatched UID: '%s' - removing entire event" % (self.attendee, self.uid,))
                        self.return_status = ImplicitScheduler.STATUS_ORPHANED_CANCELLED_EVENT
                        returnValue(None)
                    else:
                        log.error("Attendee '%s' is not allowed to make an unauthorized change to an organized event: UID:%s" % (self.attendeePrincipal, self.uid,))
                        raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-attendee-change")))

                if not doITipReply:
                    log.debug("Implicit - attendee '%s' is updating UID: '%s' but change is not significant" % (self.attendee, self.uid))
                    returnValue(None)
                log.debug("Attendee '%s' is allowed to update UID: '%s' with local organizer '%s'" % (self.attendee, self.uid, self.organizer))

            elif isinstance(self.organizerAddress, LocalCalendarUser):
                # Check to see whether all instances are CANCELLED
                if self.calendar.hasPropertyValueInAllComponents(Property("STATUS", "CANCELLED")):
                    log.debug("Attendee '%s' is creating CANCELLED event for missing UID: '%s' - removing entire event" % (self.attendee, self.uid,))
                    self.return_status = ImplicitScheduler.STATUS_ORPHANED_CANCELLED_EVENT
                    returnValue(None)
                else:
                    log.debug("Attendee '%s' is not allowed to update UID: '%s' - missing organizer copy - removing entire event" % (self.attendee, self.uid,))
                    self.return_status = ImplicitScheduler.STATUS_ORPHANED_EVENT
                    returnValue(None)

            elif isinstance(self.organizerAddress, InvalidCalendarUser):
                # We will allow the attendee to do anything in this case, but we will mark the organizer
                # with an schedule-status error
                log.debug("Attendee '%s' is allowed to update UID: '%s' with invalid organizer '%s'" % (self.attendee, self.uid, self.organizer))
                if doScheduling:
                    self.calendar.setParameterToValueForPropertyWithValue(
                        "SCHEDULE-STATUS",
                        iTIPRequestStatus.NO_USER_SUPPORT_CODE,
                        "ORGANIZER",
                        self.organizer)
                returnValue(None)

            else:
                # We have a remote Organizer of some kind. For now we will allow the Attendee
                # to make any change they like as we cannot verify what is reasonable. In reality
                # we ought to be comparing the Attendee changes against the attendee's own copy
                # and restrict changes based on that when the organizer's copy is not available.
                log.debug("Attendee '%s' is allowed to update UID: '%s' with remote organizer '%s'" % (self.attendee, self.uid, self.organizer))
                changedRids = None

            if doScheduling:
                log.debug("Implicit - attendee '%s' is updating UID: '%s'" % (self.attendee, self.uid))
                yield self.scheduleWithOrganizer(changedRids)
            else:
                log.debug("Implicit - attendee '%s' is updating UID without server scheduling: '%s'" % (self.attendee, self.uid))
                # Nothing else to do

    def checkOrganizerScheduleAgent(self):

        is_server = self.calendar.getOrganizerScheduleAgent()
        local_organizer = isinstance(self.organizerAddress, LocalCalendarUser)

        if config.Scheduling.iMIP.Enabled and self.organizerAddress.cuaddr.startswith("mailto:"):
            return True

        if local_organizer and not is_server:
            log.error("Attendee '%s' is not allowed to change SCHEDULE-AGENT on organizer: UID:%s" % (self.attendeePrincipal, self.uid,))
            raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-attendee-change")))
        elif not local_organizer and is_server:
            # Coerce ORGANIZER to SCHEDULE-AGENT=NONE
            log.debug("Attendee '%s' is not allowed to use SCHEDULE-AGENT=SERVER on organizer: UID:%s" % (self.attendeePrincipal, self.uid,))
            self.calendar.setParameterToValueForPropertyWithValue("SCHEDULE-AGENT", "NONE", "ORGANIZER", None)
            self.calendar.setParameterToValueForPropertyWithValue("SCHEDULE-STATUS", iTIPRequestStatus.NO_USER_SUPPORT_CODE, "ORGANIZER", None)
            is_server = False
            
        return is_server

    @inlineCallbacks
    def getOrganizersCopy(self):
        """
        Get the Organizer's copy of the event being processed.
        
        NB it is possible that the Organizer is not hosted on this server
        so the result here will be None. In that case we have to trust that
        the attendee does the right thing about changing the details in the event.
        """
        
        self.organizer_calendar = None
        calendar_resource, _ignore_name, _ignore_collection, _ignore_uri = (yield getCalendarObjectForPrincipals(self.request, self.organizerPrincipal, self.uid))
        if calendar_resource:
            self.organizer_calendar = (yield calendar_resource.iCalendarForUser(self.request))
        elif isinstance(self.organizerAddress, PartitionedCalendarUser):
            # For partitioning where the organizer is on a different node, we will assume that the attendee's copy
            # of the event is up to date and "authoritative". So we pretend that is the organizer copy
            self.organizer_calendar = self.oldcalendar
        
    def isAttendeeChangeInsignificant(self):
        """
        Check whether the change is significant (PARTSTAT) or allowed
        (attendee can only change their property, alarms, TRANSP, and
        instances. Raise an exception if it is not allowed.
        """
        
        oldcalendar = self.oldcalendar
        if oldcalendar is None:
            oldcalendar = self.organizer_calendar
            oldcalendar.attendeesView((self.attendee,), onlyScheduleAgentServer=True)
        differ = iCalDiff(oldcalendar, self.calendar, self.do_smart_merge)
        return differ.attendeeMerge(self.attendee)

    def scheduleWithOrganizer(self, changedRids=None):

        if not hasattr(self.request, "extendedLogItems"):
            self.request.extendedLogItems = {}
        self.request.extendedLogItems["itip.reply"] = "reply"
    
        itipmsg = iTipGenerator.generateAttendeeReply(self.calendar, self.attendee, changedRids=changedRids)

        # Send scheduling message
        return self.sendToOrganizer("REPLY", itipmsg)

    def scheduleCancelWithOrganizer(self):

        if not hasattr(self.request, "extendedLogItems"):
            self.request.extendedLogItems = {}
        self.request.extendedLogItems["itip.reply"] = "cancel"
    
        itipmsg = iTipGenerator.generateAttendeeReply(self.calendar, self.attendee, force_decline=True)

        # Send scheduling message
        return self.sendToOrganizer("CANCEL", itipmsg)

    def sendToOrganizer(self, action, itipmsg):

        # Send scheduling message

        # This is a local CALDAV scheduling operation.
        scheduler = CalDAVScheduler(self.request, self.resource)

        # Do the PUT processing
        def _gotResponse(response):
            self.handleSchedulingResponse(response, False)
            
        log.info("Implicit %s - attendee: '%s' to organizer: '%s', UID: '%s'" % (action, self.attendee, self.organizer, self.uid,))
        d = scheduler.doSchedulingViaPUT(self.originator, (self.organizer,), itipmsg, self.internal_request)
        d.addCallback(_gotResponse)
        return d
