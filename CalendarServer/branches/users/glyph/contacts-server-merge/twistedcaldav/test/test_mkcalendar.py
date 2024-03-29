##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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
from twisted.internet.defer import inlineCallbacks
from twistedcaldav import index

import os

from twisted.web2 import responsecode
from twisted.web2.iweb import IResponse
from twisted.web2.stream import MemoryStream
from twisted.web2.dav import davxml
from twisted.web2.dav.fileop import rmdir
from twisted.web2.test.test_server import SimpleRequest

import twistedcaldav.test.util
from twistedcaldav import caldavxml
from twistedcaldav.static import CalDAVFile

class MKCALENDAR (twistedcaldav.test.util.TestCase):
    """
    MKCALENDAR request
    """
    # FIXME:
    # Try nesting calendars (should fail)
    # HEAD request on calendar: resourcetype = (collection, calendar)

    def test_make_calendar(self):
        """
        Make calendar
        """
        uri  = "/calendar_make"
        path = os.path.join(self.docroot, uri[1:])

        if os.path.exists(path):
            rmdir(path)

        def do_test(response):
            response = IResponse(response)

            if not os.path.isdir(path):
                self.fail("MKCALENDAR made no calendar")

            resource = CalDAVFile(path)
            if not resource.isCalendarCollection():
                self.fail("MKCALENDAR made non-calendar collection")

            if response.code != responsecode.CREATED:
                self.fail("Incorrect response to successful MKCALENDAR: %s"
                          % (response.code,))

            if not os.path.exists(os.path.join(path, index.db_basename)):
                self.fail("Did not create index file when creating a calendar")

        request = SimpleRequest(self.site, "MKCALENDAR", uri)
        return self.send(request, do_test)

    def test_make_calendar_with_props(self):
        """
        Make calendar with properties (CalDAV-access-09, section 5.3.1.2)
        """
        uri  = "/calendar_prop"
        path = os.path.join(self.docroot, uri[1:])

        if os.path.exists(path):
            rmdir(path)

        @inlineCallbacks
        def do_test(response):
            response = IResponse(response)

            if response.code != responsecode.CREATED:
                self.fail("MKCALENDAR failed: %s" % (response.code,))

            resource = CalDAVFile(path)
            if not resource.isCalendarCollection():
                self.fail("MKCALENDAR made non-calendar collection")

            for qname, value in (
                (davxml.DisplayName.qname(), "Lisa's Events"),
                (caldavxml.CalendarDescription.qname(), "Calendar restricted to events."),
            ):
                stored = yield resource.readProperty(qname, None)
                stored = str(stored)
                if stored != value:
                    self.fail("MKCALENDAR failed to set property %s: %s != %s"
                              % (qname, stored, value))

            supported_components = yield resource.readProperty(caldavxml.SupportedCalendarComponentSet, None)
            supported_components = supported_components.children
            if len(supported_components) != 1:
                self.fail("MKCALENDAR failed to set property %s: len(%s) != 1"
                          % (caldavxml.SupportedCalendarComponentSet.qname(), supported_components))
            if supported_components[0] != caldavxml.CalendarComponent(name="VEVENT"):
                self.fail("MKCALENDAR failed to set property %s: %s != %s"
                          % (caldavxml.SupportedCalendarComponentSet.qname(),
                             supported_components[0].toxml(), caldavxml.CalendarComponent(name="VEVENT").toxml()))

            tz = yield resource.readProperty(caldavxml.CalendarTimeZone, None)
            tz = tz.calendar()
            self.failUnless(tz.resourceType() == "VTIMEZONE")
            self.failUnless(tuple(tz.subcomponents())[0].propertyValue("TZID") == "US-Eastern")
        
        mk = caldavxml.MakeCalendar(
            davxml.Set(
                davxml.PropertyContainer(
                    davxml.DisplayName("Lisa's Events"),
                    caldavxml.CalendarDescription("Calendar restricted to events."), # FIXME: lang=en
                    caldavxml.SupportedCalendarComponentSet(caldavxml.CalendarComponent(name="VEVENT")),
                    caldavxml.CalendarTimeZone(
"""BEGIN:VCALENDAR
PRODID:-//Example Corp.//CalDAV Client//EN
VERSION:2.0
BEGIN:VTIMEZONE
TZID:US-Eastern
LAST-MODIFIED:19870101T000000Z
BEGIN:STANDARD
DTSTART:19671029T020000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
TZNAME:Eastern Standard Time (US & Canada)
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19870405T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=4
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
TZNAME:Eastern Daylight Time (US & Canada)
END:DAYLIGHT
END:VTIMEZONE
END:VCALENDAR
"""
                    )
                )
            )
        )

        request = SimpleRequest(self.site, "MKCALENDAR", uri)
        request.stream = MemoryStream(mk.toxml())
        return self.send(request, do_test)

    def test_make_calendar_no_parent(self):
        """
        Make calendar with no parent
        """
        uri  = "/no/parent/for/calendar"

        def do_test(response):
            response = IResponse(response)

            if response.code != responsecode.CONFLICT:
                self.fail("Incorrect response to MKCALENDAR with no parent: %s" % (response.code,))

            # FIXME: Check for CalDAV:calendar-collection-location-ok element

        request = SimpleRequest(self.site, "MKCALENDAR", uri)
        return self.send(request, do_test)

    def test_make_calendar_on_resource(self):
        """
        Make calendar on existing resource
        """
        uri  = "/calendar_on_resource"
        path = os.path.join(self.docroot, uri[1:])

        if not os.path.exists(path):
            f = open(path, "w")
            f.close()

        def do_test(response):
            response = IResponse(response)

            if response.code != responsecode.FORBIDDEN:
                self.fail("Incorrect response to MKCALENDAR on existing resource: %s" % (response.code,))

            # FIXME: Check for DAV:resource-must-be-null element

        request = SimpleRequest(self.site, "MKCALENDAR", uri)
        return self.send(request, do_test)

    def test_make_calendar_in_calendar(self):
        """
        Make calendar in calendar
        """
        first_uri  = "/calendar_in_calendar"
        first_path = os.path.join(self.docroot, first_uri[1:])

        if os.path.exists(first_path): rmdir(first_path)

        def next(response):
            response = IResponse(response)

            if response.code != responsecode.CREATED:
                self.fail("MKCALENDAR failed: %s" % (response.code,))

            def do_test(response):
                response = IResponse(response)

                if response.code != responsecode.FORBIDDEN:
                    self.fail("Incorrect response to nested MKCALENDAR: %s" % (response.code,))

            nested_uri  = os.path.join(first_uri, "nested")

            request = SimpleRequest(self.site, "MKCALENDAR", nested_uri)
            self.send(request, do_test)

        request = SimpleRequest(self.site, "MKCALENDAR", first_uri)
        return self.send(request, next)
