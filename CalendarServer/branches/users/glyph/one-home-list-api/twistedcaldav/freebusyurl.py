##
# Copyright (c) 2005-2012 Apple Inc. All rights reserved.
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

"""
Free-busy-URL resources.
"""

__all__ = [
    "FreeBusyURLResource",
]

from twisted.internet.defer import inlineCallbacks, returnValue, succeed

from twext.python.log import Logger
from twext.web2 import responsecode
from txdav.xml import element as davxml
from twext.web2.dav.http import ErrorResponse
from twext.web2.dav.util import joinURL
from twext.web2.http import HTTPError
from twext.web2.http import Response
from twext.web2.http import StatusResponse
from twext.web2.http_headers import MimeType
from twext.web2.stream import MemoryStream

from twistedcaldav import caldavxml
from twistedcaldav.caldavxml import TimeRange
from twistedcaldav.config import config
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.ical import Property
from twistedcaldav.resource import CalDAVResource, ReadOnlyNoCopyResourceMixIn
from twistedcaldav.scheduling.caldav.delivery import ScheduleViaCalDAV
from twistedcaldav.scheduling.caldav.resource import deliverSchedulePrivilegeSet
from twistedcaldav.scheduling.cuaddress import LocalCalendarUser
from twistedcaldav.scheduling.scheduler import Scheduler

from pycalendar.datetime import PyCalendarDateTime
from pycalendar.duration import PyCalendarDuration

log = Logger()


class FreeBusyURLResource (ReadOnlyNoCopyResourceMixIn, CalDAVResource):
    """
    Free-busy URL resource.

    Extends L{DAVResource} to provide free-busy URL functionality.
    """

    def __init__(self, parent):
        """
        @param parent: the parent resource of this one.
        """
        assert parent is not None

        CalDAVResource.__init__(self, principalCollections=parent.principalCollections())

        self.parent = parent


    def __repr__(self):
        return "<%s (free-busy URL resource): %s>" % (self.__class__.__name__, joinURL(self.parent.url(), "freebusy"))


    def defaultAccessControlList(self):
        privs = (
            davxml.Privilege(davxml.Read()),
            davxml.Privilege(caldavxml.ScheduleDeliver()),
        )
        if config.Scheduling.CalDAV.OldDraftCompatibility:
            privs += (davxml.Privilege(caldavxml.Schedule()),)

        aces = (
            # DAV:Read, CalDAV:schedule-deliver for all principals (does not include anonymous)
            davxml.ACE(
                davxml.Principal(davxml.Authenticated()),
                davxml.Grant(*privs),
                davxml.Protected(),
            ),
        )
        if config.FreeBusyURL.AnonymousAccess:
            aces += (
                # DAV:Read, for unauthenticated principals
                davxml.ACE(
                    davxml.Principal(davxml.Unauthenticated()),
                    davxml.Grant(
                        davxml.Privilege(davxml.Read()),
                    ),
                    davxml.Protected(),
                ),
            )
        return davxml.ACL(*aces)


    def resourceType(self):
        return davxml.ResourceType.freebusyurl


    def contentType(self):
        return MimeType("text", "calendar", charset="utf-8")


    def isCollection(self):
        return False


    def isCalendarCollection(self):
        return False


    def isPseudoCalendarCollection(self):
        return False


    def render(self, request):
        output = """<html>
<head>
<title>Free-Busy URL Resource</title>
</head>
<body>
<h1>Free-busy URL Resource.</h1>
</body
</html>"""

        response = Response(200, {}, output)
        response.headers.setHeader("content-type", MimeType("text", "html"))
        return response


    def http_GET(self, request):
        """
        The free-busy URL POST method.
        """
        return self._processFBURL(request)


    def http_POST(self, request):
        """
        The free-busy URL POST method.
        """
        return self._processFBURL(request)


    @inlineCallbacks
    def _processFBURL(self, request):
        #
        # Check authentication and access controls
        #
        yield self.authorize(request, (davxml.Read(),))

        # Extract query parameters from the URL
        args = ('start', 'end', 'duration', 'token', 'format', 'user',)
        for arg in args:
            setattr(self, arg, request.args.get(arg, [None])[0])

        # Some things we do not handle
        if self.token or self.user:
            raise HTTPError(ErrorResponse(
                responsecode.NOT_ACCEPTABLE,
                (calendarserver_namespace, "supported-query-parameter"),
                "Invalid query parameter",
            ))

        # Check format
        if self.format:
            self.format = self.format.split(";")[0]
            if self.format not in ("text/calendar", "text/plain"):
                raise HTTPError(ErrorResponse(
                    responsecode.NOT_ACCEPTABLE,
                    (calendarserver_namespace, "supported-format"),
                    "Invalid return format requested",
                ))
        else:
            self.format = "text/calendar"

        # Start/end/duration must be valid iCalendar DATE-TIME UTC or DURATION values
        try:
            if self.start:
                self.start = PyCalendarDateTime.parseText(self.start)
                if not self.start.utc():
                    raise ValueError()
            if self.end:
                self.end = PyCalendarDateTime.parseText(self.end)
                if not self.end.utc():
                    raise ValueError()
            if self.duration:
                self.duration = PyCalendarDuration.parseText(self.duration)
        except ValueError:
            raise HTTPError(ErrorResponse(
                responsecode.BAD_REQUEST,
                (calendarserver_namespace, "valid-query-parameters"),
                "Invalid query parameters",
            ))

        # Sanity check start/end/duration

        # End and duration cannot both be present
        if self.end and self.duration:
            raise HTTPError(ErrorResponse(
                responsecode.NOT_ACCEPTABLE,
                (calendarserver_namespace, "valid-query-parameters"),
                "Invalid query parameters",
            ))

        # Duration must be positive
        if self.duration and self.duration.getTotalSeconds() < 0:
            raise HTTPError(ErrorResponse(
                responsecode.BAD_REQUEST,
                (calendarserver_namespace, "valid-query-parameters"),
                "Invalid query parameters",
            ))

        # Now fill in the missing pieces
        if self.start is None:
            self.start = PyCalendarDateTime.getNowUTC()
            self.start.setHHMMSS(0, 0, 0)
        if self.duration:
            self.end = self.start + self.duration
        if self.end is None:
            self.end = self.start + PyCalendarDuration(days=config.FreeBusyURL.TimePeriod)

        # End > start
        if self.end <= self.start:
            raise HTTPError(ErrorResponse(
                responsecode.BAD_REQUEST,
                (calendarserver_namespace, "valid-query-parameters"),
                "Invalid query parameters",
            ))

        # TODO: We should probably verify that the actual time-range is within sensible bounds (e.g. not too far in the past or future and not too long)

        # Now lookup the principal details for the targeted user
        principal = self.parent.principalForRecord()

        # Pick the first mailto cu address or the first other type
        cuaddr = None
        for item in principal.calendarUserAddresses():
            if cuaddr is None:
                cuaddr = item
            if item.startswith("mailto"):
                cuaddr = item
                break

        # Get inbox details
        inboxURL = principal.scheduleInboxURL()
        if inboxURL is None:
            raise HTTPError(StatusResponse(responsecode.INTERNAL_SERVER_ERROR, "No schedule inbox URL for principal: %s" % (principal,)))
        try:
            inbox = (yield request.locateResource(inboxURL))
        except:
            log.err("No schedule inbox for principal: %s" % (principal,))
            inbox = None
        if inbox is None:
            raise HTTPError(StatusResponse(responsecode.INTERNAL_SERVER_ERROR, "No schedule inbox for principal: %s" % (principal,)))

        scheduler = Scheduler(request, self)
        scheduler.timeRange = TimeRange(start="20000101T000000Z", end="20070102T000000Z")
        scheduler.timeRange.start = self.start
        scheduler.timeRange.end = self.end

        scheduler.organizer = LocalCalendarUser(cuaddr, principal, inbox, inboxURL)

        attendeeProp = Property("ATTENDEE", scheduler.organizer.cuaddr)

        requestor = ScheduleViaCalDAV(scheduler, (), [], True)
        fbresult = (yield requestor.generateAttendeeFreeBusyResponse(
            scheduler.organizer,
            None,
            None,
            None,
            attendeeProp,
            True,
        ))

        response = Response()
        response.stream = MemoryStream(str(fbresult))
        response.headers.setHeader("content-type", MimeType.fromString("%s; charset=utf-8" % (self.format,)))

        returnValue(response)


    ##
    # ACL
    ##

    def supportedPrivileges(self, request):
        return succeed(deliverSchedulePrivilegeSet)
