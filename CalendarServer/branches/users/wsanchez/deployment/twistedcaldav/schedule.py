##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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
CalDAV scheduling resources.
"""

__all__ = [
    "ScheduleInboxResource",
    "ScheduleOutboxResource",
    "IScheduleInboxResource",
]

from twisted.internet import reactor
from twisted.internet.defer import succeed, inlineCallbacks, returnValue
from twisted.python.failure import Failure
from twisted.web2 import responsecode
from twisted.web2.dav import davxml
from twisted.web2.dav.http import ErrorResponse, errorForFailure, messageForFailure, statusForFailure
from twisted.web2.dav.idav import IDAVResource
from twisted.web2.dav.resource import AccessDeniedError
from twisted.web2.dav.util import joinURL
from twisted.web2.http import HTTPError, Response
from twisted.web2.http_headers import MimeType

from twistedcaldav import caldavxml
from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.config import config
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.log import LoggingMixIn
from twistedcaldav.resource import CalDAVResource
from twistedcaldav.resource import isCalendarCollectionResource
from twistedcaldav.scheduling.itip import handleRequest
from twistedcaldav.scheduling.scheduler import CalDAVScheduler, IScheduleScheduler
    

class CalendarSchedulingCollectionResource (CalDAVResource):
    """
    CalDAV principal resource.

    Extends L{DAVResource} to provide CalDAV scheduling collection
    functionality.
    """
    def __init__(self, parent):
        """
        @param parent: the parent resource of this one.
        """
        assert parent is not None

        CalDAVResource.__init__(self, principalCollections=parent.principalCollections())

        self.parent = parent

    def isCollection(self):
        return True

    def isCalendarCollection(self):
        return False

    def isPseudoCalendarCollection(self):
        return True

    def supportedReports(self):
        result = super(CalDAVResource, self).supportedReports()
        result.append(davxml.Report(caldavxml.CalendarQuery(),))
        result.append(davxml.Report(caldavxml.CalendarMultiGet(),))
        # free-busy report not allowed
        return result

class ScheduleInboxResource (CalendarSchedulingCollectionResource):
    """
    CalDAV schedule Inbox resource.

    Extends L{DAVResource} to provide CalDAV functionality.
    """

    liveProperties = CalendarSchedulingCollectionResource.liveProperties + (
        (caldav_namespace, "calendar-free-busy-set"),
    )

    def resourceType(self):
        return davxml.ResourceType.scheduleInbox

    def defaultAccessControlList(self):
        return davxml.ACL(
            # CalDAV:schedule for any authenticated user
            davxml.ACE(
                davxml.Principal(davxml.Authenticated()),
                davxml.Grant(
                    davxml.Privilege(caldavxml.Schedule()),
                ),
            ),
        )

    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        if qname == (caldav_namespace, "calendar-free-busy-set"):
            # Always return at least an empty list
            if not self.hasDeadProperty(property):
                return succeed(caldavxml.CalendarFreeBusySet())
            
        return super(ScheduleInboxResource, self).readProperty(property, request)

    @inlineCallbacks
    def writeProperty(self, property, request):
        assert isinstance(property, davxml.WebDAVElement)

        # Strictly speaking CS:calendar-availability is a live property in the sense that the
        # server enforces what can be stored, however it need not actually
        # exist so we cannot list it in liveProperties on this resource, since its
        # its presence there means that hasProperty will always return True for it.
        if property.qname() == (calendarserver_namespace, "calendar-availability"):
            if not property.valid():
                raise HTTPError(ErrorResponse(
                    responsecode.CONFLICT,
                    (caldav_namespace, "valid-calendar-data")
                ))

        elif property.qname() == (caldav_namespace, "calendar-free-busy-set"):
            # Verify that the calendars added in the PROPPATCH are valid. We do not check
            # whether existing items in the property are still valid - only new ones.
            new_calendars = set([str(href) for href in property.children])
            if not self.hasDeadProperty(property):
                old_calendars = set()
            else:
                old_calendars = set([str(href) for href in self.readDeadProperty(property).children])
            added_calendars = new_calendars.difference(old_calendars)
            for href in added_calendars:
                cal = yield request.locateResource(str(href))
                if cal is None or not cal.exists() or not isCalendarCollectionResource(cal):
                    # Validate that href's point to a valid calendar.
                    raise HTTPError(ErrorResponse(
                        responsecode.CONFLICT,
                        (caldav_namespace, "valid-calendar-url")
                    ))

        yield super(ScheduleInboxResource, self).writeProperty(property, request)

    @inlineCallbacks
    def http_GET(self, request):
        """
        Special behavior - if the inbox belongs to an auto-accept calendar user, then trigger
        auto-accept processing on any stored items in the inbox.
        """

        if config.EnableAutoAcceptTrigger:

            # Look for auto-accept request parameter
            if request.args:
                autoaccept = request.args.get("auto-accept", ("",))
                if len(autoaccept) != 1:
                    raise HTTPError(ErrorResponse(responsecode.BAD_REQUEST, (calendarserver_namespace, "valid-autoaccept")))
    
                # Check authentication and access controls
                yield self.authorize(request, (davxml.Write(),))
                
                # Find out who the inbox belongs to
                principal = self.parent.principalForRecord()
        
                # If they are an auto-accept calendar user, do special processing
                if principal.autoSchedule() and autoaccept[0].lower() == "true":
                    
                    # Now look at each child resource - order by last-modified
                    children = []
                    for name, uid, type in self.index().search(None): #@UnusedVariable
                        try:
                            child = yield request.locateChildResource(self, name)
                            child = IDAVResource(child)
                        except TypeError:
                            child = None
        
                        if child is not None:
                            # Check privileges of child - skip if access denied
                            try:
                                yield child.checkPrivileges(request, (davxml.Write(),))
                            except AccessDeniedError:
                                continue
                            calendar = self.iCalendar(name)
                            children.append((child, calendar,))
                            
                    # Sort by last modified
                    children.sort(key=lambda x: x[0].lastModified())
                    
                    # Now process each one in order
                    for child, calendar in children:
                        reactor.callLater(0.0, handleRequest, *(request, principal, self, calendar, child)) #@UndefinedVariable
    
                    if (len(children)):
                        response_text = "Started auto-processing of %d iTIP messages." % (len(children,))
                    else:
                        response_text = "There are no iTIP messages to auto-process."
    
                else:
                    response_text = "This calendar user does not auto-accept invites."
    
                # Return some useful information
                response = Response(200, {}, """<html>
<head>
<title>Schedule Inbox Auto Processing</title>
</head>
<body>
<h1>%s</h1>
</body>
</html>""" % (response_text,)
                )
    
                response.headers.setHeader("content-type", MimeType("text", "html",  {"charset": "utf-8"}))
                returnValue(response)

        response = (yield super(ScheduleInboxResource, self).http_GET(request))
        returnValue(response)

class ScheduleOutboxResource (CalendarSchedulingCollectionResource):
    """
    CalDAV schedule Outbox resource.

    Extends L{DAVResource} to provide CalDAV functionality.
    """

    def defaultAccessControlList(self):
        if config.EnableProxyPrincipals:
            myPrincipal = self.parent.principalForRecord()
    
            return davxml.ACL(
                # CalDAV:schedule for associated write proxies
                davxml.ACE(
                    davxml.Principal(davxml.HRef(joinURL(myPrincipal.principalURL(), "calendar-proxy-write"))),
                    davxml.Grant(davxml.Privilege(caldavxml.Schedule()),),
                    davxml.Protected(),
                ),
            )
        else:
            return super(ScheduleOutboxResource, self).defaultAccessControlList()

    def resourceType(self):
        return davxml.ResourceType.scheduleOutbox

    @inlineCallbacks
    def http_POST(self, request):
        """
        The CalDAV POST method.
    
        This uses a generator function yielding either L{waitForDeferred} objects or L{Response} objects.
        This allows for code that follows a 'linear' execution pattern rather than having to use nested
        L{Deferred} callbacks. The logic is easier to follow this way plus we don't run into deep nesting
        issues which the other approach would have with large numbers of recipients.
        """
        # Check authentication and access controls
        yield self.authorize(request, (caldavxml.Schedule(),))

        # This is a local CALDAV scheduling operation.
        scheduler = CalDAVScheduler(request, self)

        # Do the POST processing treating
        result = (yield scheduler.doSchedulingViaPOST(use_request_headers=True))
        returnValue(result.response())


class ScheduleResponseResponse (Response):
    """
    ScheduleResponse L{Response} object.
    Renders itself as a CalDAV:schedule-response XML document.
    """
    def __init__(self, xml_responses, location=None):
        """
        @param xml_responses: an interable of davxml.Response objects.
        @param location:      the value of the location header to return in the response,
            or None.
        """

        Response.__init__(self, code=responsecode.OK,
                          stream=caldavxml.ScheduleResponse(*xml_responses).toxml())

        self.headers.setHeader("content-type", MimeType("text", "xml"))
    
        if location is not None:
            self.headers.setHeader("location", location)

class ScheduleResponseQueue (LoggingMixIn):
    """
    Stores a list of (typically error) responses for use in a
    L{ScheduleResponse}.
    """
    def __init__(self, method, success_response):
        """
        @param method: the name of the method generating the queue.
        @param success_response: the response to return in lieu of a
            L{ScheduleResponse} if no responses are added to this queue.
        """
        self.responses         = []
        self.method            = method
        self.success_response  = success_response
        self.location          = None

    def setLocation(self, location):
        """
        @param location:      the value of the location header to return in the response,
            or None.
        """
        self.location = location

    def add(self, recipient, what, reqstatus=None, calendar=None):
        """
        Add a response.
        @param recipient: the recipient for this response.
        @param what: a status code or a L{Failure} for the given recipient.
        @param status: the iTIP request-status for the given recipient.
        @param calendar: the calendar data for the given recipient response.
        """
        if type(what) is int:
            code    = what
            error   = None
            message = responsecode.RESPONSES[code]
        elif isinstance(what, Failure):
            code    = statusForFailure(what)
            error   = errorForFailure(what)
            message = messageForFailure(what)
        else:
            raise AssertionError("Unknown data type: %r" % (what,))

        if code > 400: # Error codes only
            self.log_error("Error during %s for %s: %s" % (self.method, recipient, message))

        children = []
        children.append(caldavxml.Recipient(davxml.HRef.fromString(recipient)))
        children.append(caldavxml.RequestStatus(reqstatus))
        if calendar is not None:
            children.append(caldavxml.CalendarData.fromCalendar(calendar))
        if error is not None:
            children.append(error)
        if message is not None:
            children.append(davxml.ResponseDescription(message))
        self.responses.append(caldavxml.Response(*children))

    def response(self):
        """
        Generate a L{ScheduleResponseResponse} with the responses contained in the
        queue or, if no such responses, return the C{success_response} provided
        to L{__init__}.
        @return: the response.
        """
        if self.responses:
            return ScheduleResponseResponse(self.responses, self.location)
        else:
            return self.success_response

class IScheduleInboxResource (CalDAVResource):
    """
    iSchedule Inbox resource.

    Extends L{DAVResource} to provide iSchedule inbox functionality.
    """

    def __init__(self, parent):
        """
        @param parent: the parent resource of this one.
        """
        assert parent is not None

        CalDAVResource.__init__(self, principalCollections=parent.principalCollections())

        self.parent = parent

    def defaultAccessControlList(self):
        privs = (
            davxml.Privilege(davxml.Read()),
            davxml.Privilege(caldavxml.Schedule()),
        )
        if config.Scheduling.CalDAV.OldDraftCompatibility:
            privs += (davxml.Privilege(caldavxml.Schedule()),)

        return davxml.ACL(
            # DAV:Read, CalDAV:schedule-deliver for all principals (includes anonymous)
            davxml.ACE(
                davxml.Principal(davxml.All()),
                davxml.Grant(*privs),
                davxml.Protected(),
            ),
        )

    def exists(self):
        return True

    def resourceType(self):
        return davxml.ResourceType.ischeduleinbox

    def isCollection(self):
        return False

    def isCalendarCollection(self):
        return False

    def isPseudoCalendarCollection(self):
        return False

    def render(self, request):
        output = """<html>
<head>
<title>Server To Server Inbox Resource</title>
</head>
<body>
<h1>Server To Server Inbox Resource.</h1>
</body
</html>"""

        response = Response(200, {}, output)
        response.headers.setHeader("content-type", MimeType("text", "html"))
        return response

    @inlineCallbacks
    def http_POST(self, request):
        """
        The server-to-server POST method.
        """

        # Check authentication and access controls
        yield self.authorize(request, (caldavxml.Schedule(),))

        # This is a server-to-server scheduling operation.
        scheduler = IScheduleScheduler(request, self)

        # Do the POST processing treating this as a non-local schedule
        result = (yield scheduler.doSchedulingViaPOST(use_request_headers=True))
        returnValue(result.response())
