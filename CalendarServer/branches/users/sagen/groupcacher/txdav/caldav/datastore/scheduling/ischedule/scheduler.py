
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

from twext.python.log import Logger
from twext.web2 import responsecode
from twext.web2.http import HTTPError, Response
from twext.web2.http_headers import MimeType

from twisted.internet.abstract import isIPAddress
from twisted.internet.defer import inlineCallbacks, returnValue

from twistedcaldav.config import config
from twistedcaldav.ical import normalizeCUAddress

from txdav.caldav.datastore.scheduling import addressmapping
from txdav.caldav.datastore.scheduling.cuaddress import RemoteCalendarUser
from txdav.caldav.datastore.scheduling.cuaddress import calendarUserFromPrincipal
from txdav.caldav.datastore.scheduling.ischedule import xml
from txdav.caldav.datastore.scheduling.ischedule.dkim import DKIMVerifier, \
    DKIMVerificationError, DKIMMissingError
from txdav.caldav.datastore.scheduling.ischedule.localservers import Servers
from txdav.caldav.datastore.scheduling.ischedule.remoteservers import IScheduleServers
from txdav.caldav.datastore.scheduling.ischedule.utils import getIPsFromHost
from txdav.caldav.datastore.scheduling.ischedule.xml import ischedule_namespace
import txdav.caldav.datastore.scheduling.ischedule.xml as ixml
from txdav.caldav.datastore.scheduling.scheduler import RemoteScheduler, \
    ScheduleResponseQueue
from txdav.caldav.datastore.util import normalizationLookup
from txdav.xml.base import WebDAVUnknownElement

import itertools
import re
import socket
import urlparse

"""
L{IScheduleScheduler} - handles deliveries for scheduling messages being POSTed to the iSchedule inbox.
"""

__all__ = [
    "IScheduleScheduler",
]


log = Logger()

class ErrorResponse(Response):
    """
    A L{Response} object which contains a status code and a L{element.Error}
    element.
    Renders itself as a DAV:error XML document.
    """
    error = None
    unregistered = True     # base class is already registered

    def __init__(self, code, error, description=None):
        """
        @param code: a response code.
        @param error: an L{WebDAVElement} identifying the error, or a
            tuple C{(namespace, name)} with which to create an empty element
            denoting the error.  (The latter is useful in the case of
            preconditions and postconditions, not all of which have defined
            XML element classes.)
        @param description: an optional string that, if present, will get
            wrapped in a (twisted_dav_namespace, error-description) element.
        """
        if type(error) is tuple:
            xml_namespace, xml_name = error
            error = WebDAVUnknownElement()
            error.namespace = xml_namespace
            error.name = xml_name

        self.description = description
        if self.description:
            output = ixml.Error(error, ixml.ResponseDescription(self.description)).toxml()
        else:
            output = ixml.Error(error).toxml()

        Response.__init__(self, code=code, stream=output)

        self.headers.setHeader("content-type", MimeType("text", "xml"))

        self.error = error


    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__, self.code, self.error.sname())



class IScheduleResponseQueue (ScheduleResponseQueue):
    """
    Stores a list of (typically error) responses for use in a
    L{ScheduleResponse}.
    """

    schedule_response_element = xml.ScheduleResponse
    response_element = xml.Response
    recipient_element = xml.Recipient
    recipient_uses_href = False
    request_status_element = xml.RequestStatus
    error_element = xml.Error
    response_description_element = xml.ResponseDescription
    calendar_data_element = xml.CalendarData



class IScheduleScheduler(RemoteScheduler):
    """
    Handles iSchedule and podding requests.
    """

    scheduleResponse = IScheduleResponseQueue

    errorResponse = ErrorResponse

    errorElements = {
        "originator-missing": (ischedule_namespace, "originator-missing"),
        "originator-invalid": (ischedule_namespace, "originator-invalid"),
        "originator-denied": (ischedule_namespace, "originator-denied"),
        "recipient-missing": (ischedule_namespace, "recipient-missing"),
        "recipient-invalid": (ischedule_namespace, "recipient-invalid"),
        "organizer-denied": (ischedule_namespace, "organizer-denied"),
        "attendee-denied": (ischedule_namespace, "attendee-denied"),
        "invalid-calendar-data-type": (ischedule_namespace, "invalid-calendar-data-type"),
        "invalid-calendar-data": (ischedule_namespace, "invalid-calendar-data"),
        "invalid-scheduling-message": (ischedule_namespace, "invalid-scheduling-message"),
        "max-recipients": (ischedule_namespace, "max-recipients"),
    }

    def __init__(self, txn, originator_uid, logItems=None, noAttendeeRefresh=False, podding=False):
        super(IScheduleScheduler, self).__init__(txn, originator_uid, logItems=logItems, noAttendeeRefresh=noAttendeeRefresh)
        self._podding = podding


    @inlineCallbacks
    def doSchedulingViaPOST(self, remoteAddr, headers, body, calendar, originator, recipients):
        """
        Carry out iSchedule specific processing.
        """

        self.remoteAddr = remoteAddr
        self.headers = headers
        self.verified = False

        if not self._podding and config.Scheduling.iSchedule.DKIM.Enabled:
            verifier = DKIMVerifier(self.headers, body, protocol_debug=config.Scheduling.iSchedule.DKIM.ProtocolDebug)
            try:
                yield verifier.verify()
                self.verified = True

            except DKIMMissingError:
                # Carry on processing, but we will do extra checks on the originator as we would
                # when DKIM is not enabled, so that any local policy via remoteservers.xml can be used.
                pass

            except DKIMVerificationError, e:
                # If DKIM is enabled and there was a DKIM header present, then fail
                msg = "Failed to verify DKIM signature"
                _debug_msg = str(e)
                log.debug("%s:%s" % (msg, _debug_msg,))
                if config.Scheduling.iSchedule.DKIM.ProtocolDebug:
                    msg = "%s:%s" % (msg, _debug_msg,)
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    (ischedule_namespace, "verification-failed"),
                    msg,
                ))

        if self._podding and self.headers.getRawHeaders('x-calendarserver-itip-refreshonly', ("F"))[0] == "T":
            self.txn.doing_attendee_refresh = 1

        # Normalize recipient addresses
        results = []
        for recipient in recipients:
            normalized = normalizeCUAddress(recipient, normalizationLookup, self.txn.directoryService().recordWithCalendarUserAddress)
            self.recipientsNormalizationMap[normalized] = recipient
            results.append(normalized)
        recipients = results

        result = (yield super(IScheduleScheduler, self).doSchedulingViaPOST(originator, recipients, calendar))
        returnValue(result)


    def preProcessCalendarData(self):
        """
        For data coming in from outside we need to normalize the calendar user addresses so that later iTIP
        processing will match calendar users against those in stored calendar data. Only do that for invites
        not freebusy.
        """

        if not self.checkForFreeBusy():
            # Need to normalize the calendar data and recipient values to keep those in sync,
            # as we might later try to match them
            self.calendar.normalizeCalendarUserAddresses(normalizationLookup, self.txn.directoryService().recordWithCalendarUserAddress)


    def checkAuthorization(self):
        # Must have an unauthenticated user
        if self.originator_uid is not None:
            log.error("Authenticated originators not allowed: %s" % (self.originator_uid,))
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["originator-denied"],
                "Authentication not allowed",
            ))


    @inlineCallbacks
    def checkOriginator(self):
        """
        Check the validity of the Originator header.
        """

        # For remote requests we do not allow the originator to be a local user or one within our domain.
        originatorPrincipal = self.txn.directoryService().recordWithCalendarUserAddress(self.originator)
        localUser = (yield addressmapping.mapper.isCalendarUserInMyDomain(self.originator))
        if originatorPrincipal or localUser:
            if originatorPrincipal.thisServer():
                log.error("Cannot use originator that is on this server: %s" % (self.originator,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["originator-denied"],
                    "Originator cannot be local to server",
                ))
            else:
                self.originator = calendarUserFromPrincipal(self.originator, originatorPrincipal)
                self._validAlternateServer(originatorPrincipal)
        else:
            self.originator = RemoteCalendarUser(self.originator)
            self._validiScheduleServer()


    def _validiScheduleServer(self):
        """
        Check the validity of the iSchedule host.
        """

        # Check for DKIM verification first and treat as valid
        if self.verified:
            return

        # We will only accept originator in known domains.
        servermgr = IScheduleServers()
        server = servermgr.mapDomain(self.originator.domain)
        if not server or not server.allow_from:
            log.error("Originator not on recognized server: %s" % (self.originator,))
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["originator-denied"],
                "Originator not recognized by server",
            ))
        else:
            # Get the request IP and map to hostname.
            clientip = self.remoteAddr.host

            # First compare as dotted IP
            matched = False
            compare_with = (server.host,) + tuple(server.client_hosts)
            if clientip in compare_with:
                matched = True
            else:
                # Now do hostname lookup
                try:
                    host, aliases, _ignore_ips = socket.gethostbyaddr(clientip)
                    for host in itertools.chain((host,), aliases):
                        # Try simple match first
                        if host in compare_with:
                            matched = True
                            break

                        # Try pattern match next
                        for pattern in compare_with:
                            try:
                                if re.match(pattern, host) is not None:
                                    matched = True
                                    break
                            except re.error:
                                log.debug("Invalid regular expression for ServerToServer white list for server domain %s: %s" % (self.originator.domain, pattern,))
                        else:
                            continue
                        break
                except socket.herror, e:
                    log.debug("iSchedule cannot lookup client ip '%s': %s" % (clientip, str(e),))

            if not matched:
                log.error("Originator not on allowed server: %s" % (self.originator,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["originator-denied"],
                    "Originator not allowed to send to this server",
                ))


    def _validAlternateServer(self, principal):
        """
        Check the validity of the podded host.
        """

        # Extract expected host/port. This will be the serverURI.
        expected_uri = principal.serverURI()
        expected_uri = urlparse.urlparse(expected_uri)

        # Get the request IP and map to hostname.
        clientip = self.remoteAddr.host

        # Check against this server.
        matched = False
        if Servers.getThisServer().checkThisIP(clientip):
            matched = True

        # Checked allowed IPs - if any were defined we only check against them, we do not
        # go on to check the expected server host ip
        elif Servers.getThisServer().hasAllowedFromIP():
            matched = Servers.getThisServer().checkAllowedFromIP(clientip)
            if not matched:
                log.error("Invalid iSchedule connection from client: %s" % (clientip,))

        # Next compare as dotted IP
        elif isIPAddress(expected_uri.hostname):
            if clientip == expected_uri.hostname:
                matched = True
        else:
            # Now do expected hostname -> IP lookup
            try:
                # So now try the lookup of the expected host
                for ip in getIPsFromHost(expected_uri.hostname):
                    if ip == clientip:
                        matched = True
                        break
            except socket.herror, e:
                log.debug("iSchedule cannot lookup client ip '%s': %s" % (clientip, str(e),))

        # Check possible shared secret
        if matched and not Servers.getThisServer().checkSharedSecret(self.headers):
            log.error("Invalid iSchedule shared secret")
            matched = False

        if not matched:
            log.error("Originator not on allowed server: %s" % (self.originator,))
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["originator-denied"],
                "Originator not allowed to send to this server",
            ))


    @inlineCallbacks
    def checkOrganizerAsOriginator(self):
        """
        Check the validity of the ORGANIZER value. ORGANIZER must not be local.
        """

        # Verify that the ORGANIZER's cu address does not map to a valid user
        organizer = self.calendar.getOrganizer()
        if organizer:
            organizerPrincipal = self.txn.directoryService().recordWithCalendarUserAddress(organizer)
            if organizerPrincipal:
                if organizerPrincipal.thisServer():
                    log.error("Invalid ORGANIZER in calendar data: %s" % (self.calendar,))
                    raise HTTPError(self.errorResponse(
                        responsecode.FORBIDDEN,
                        self.errorElements["organizer-denied"],
                        "Organizer is not local to server",
                    ))
                else:
                    # Check that the origin server is the correct pod
                    self.organizer = calendarUserFromPrincipal(organizer, organizerPrincipal)
                    self._validAlternateServer(self.organizer.principal)
            else:
                localUser = (yield addressmapping.mapper.isCalendarUserInMyDomain(organizer))
                if localUser:
                    log.error("Unsupported ORGANIZER in calendar data: %s" % (self.calendar,))
                    raise HTTPError(self.errorResponse(
                        responsecode.FORBIDDEN,
                        self.errorElements["organizer-denied"],
                        "Organizer not allowed to be originator",
                    ))
                else:
                    self.organizer = RemoteCalendarUser(organizer)
        else:
            log.error("ORGANIZER missing in calendar data: %s" % (self.calendar,))
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["organizer-denied"],
                "No organizer in calendar data",
            ))


    @inlineCallbacks
    def checkAttendeeAsOriginator(self):
        """
        Check the validity of the ATTENDEE value as this is the originator of the iTIP message.
        Only local attendees are allowed for message originating from this server.
        """

        # Attendee cannot be local.
        attendeePrincipal = self.txn.directoryService().recordWithCalendarUserAddress(self.attendee)
        if attendeePrincipal:
            if attendeePrincipal.thisServer():
                log.error("Invalid ATTENDEE in calendar data: %s" % (self.calendar,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["attendee-denied"],
                    "Local attendee cannot send to this server",
                ))
            else:
                self._validAlternateServer(attendeePrincipal)
        else:
            localUser = (yield addressmapping.mapper.isCalendarUserInMyDomain(self.attendee))
            if localUser:
                log.error("Unknown ATTENDEE in calendar data: %s" % (self.calendar,))
                raise HTTPError(self.errorResponse(
                    responsecode.FORBIDDEN,
                    self.errorElements["attendee-denied"],
                    "Attendee not allowed to schedule",
                ))

        # TODO: in this case we should check that the ORGANIZER is the sole recipient.


    @inlineCallbacks
    def securityChecks(self):
        """
        Check that the originator has the appropriate rights to send this type of iTIP message.
        """

        # Prevent spoofing of ORGANIZER with specific METHODs when local
        if self.calendar.propertyValue("METHOD") in ("PUBLISH", "REQUEST", "ADD", "CANCEL", "DECLINECOUNTER"):
            yield self.checkOrganizerAsOriginator()

        # Prevent spoofing when doing reply-like METHODs
        elif self.calendar.propertyValue("METHOD") in ("REPLY", "COUNTER", "REFRESH"):
            yield self.checkAttendeeAsOriginator()

        else:
            log.error("Unknown iTIP METHOD for security checks: %s" % (self.calendar.propertyValue("METHOD"),))
            raise HTTPError(self.errorResponse(
                responsecode.FORBIDDEN,
                self.errorElements["invalid-scheduling-message"],
                "Unknown iTIP method",
            ))
