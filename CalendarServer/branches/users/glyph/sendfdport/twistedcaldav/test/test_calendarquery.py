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

import os
import shutil

from twisted.trial.unittest import SkipTest
from twext.web2 import responsecode
from twext.web2.iweb import IResponse
from twext.web2.stream import MemoryStream
from twext.web2.dav import davxml
from twext.web2.dav.fileop import rmdir
from twext.web2.dav.util import davXMLFromStream
from twext.web2.test.test_server import SimpleRequest

import twistedcaldav.test.util
from twistedcaldav import caldavxml
from twistedcaldav import ical
from twistedcaldav.index import db_basename

class CalendarQuery (twistedcaldav.test.util.TestCase):
    """
    calendar-query REPORT
    """
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    holidays_dir = os.path.join(data_dir, "Holidays")

    def test_calendar_query_time_range(self):
        """
        Partial retrieval of events by time range.
        (CalDAV-access-09, section 7.6.1)
        """
        calendar_properties = (
            davxml.GETETag(),
            caldavxml.CalendarData(
                caldavxml.CalendarComponent(
                    caldavxml.AllProperties(),
                    caldavxml.CalendarComponent(
                        caldavxml.Property(name="X-ABC-GUID"),
                        caldavxml.Property(name="UID"),
                        caldavxml.Property(name="DTSTART"),
                        caldavxml.Property(name="DTEND"),
                        caldavxml.Property(name="DURATION"),
                        caldavxml.Property(name="EXDATE"),
                        caldavxml.Property(name="EXRULE"),
                        caldavxml.Property(name="RDATE"),
                        caldavxml.Property(name="RRULE"),
                        caldavxml.Property(name="LOCATION"),
                        caldavxml.Property(name="SUMMARY"),
                        name="VEVENT",
                    ),
                    caldavxml.CalendarComponent(
                        caldavxml.AllProperties(),
                        caldavxml.AllComponents(),
                        name="VTIMEZONE",
                    ),
                    name="VCALENDAR",
                ),
            ),
        )

        query_timerange = caldavxml.TimeRange(
            start="20021001T000000Z",
            end="20021101T000000Z",
        )

        query = caldavxml.CalendarQuery(
            davxml.PropertyContainer(*calendar_properties),
            caldavxml.Filter(
                caldavxml.ComponentFilter(
                    caldavxml.ComponentFilter(
                        query_timerange,
                        name="VEVENT",
                    ),
                    name="VCALENDAR",
                ),
            ),
        )

        def got_xml(doc):
            if not isinstance(doc.root_element, davxml.MultiStatus):
                self.fail("REPORT response XML root element is not multistatus: %r" % (doc.root_element,))

            for response in doc.root_element.childrenOfType(davxml.PropertyStatusResponse):
                properties_to_find = [p.qname() for p in calendar_properties]

                for propstat in response.childrenOfType(davxml.PropertyStatus):
                    status = propstat.childOfType(davxml.Status)
                    properties = propstat.childOfType(davxml.PropertyContainer).children

                    if status.code != responsecode.OK:
                        self.fail("REPORT failed (status %s) to locate properties: %r"
                                  % (status.code, properties))

                    for property in properties:
                        qname = property.qname()
                        if qname in properties_to_find:
                            properties_to_find.remove(qname)
                        else:
                            self.fail("REPORT found property we didn't ask for: %r" % (property,))

                        if isinstance(property, caldavxml.CalendarData):
                            cal = property.calendar()
                            instances = cal.expandTimeRanges(query_timerange.end)
                            vevents = [x for x in cal.subcomponents() if x.name() == "VEVENT"]
                            if not query_timerange.matchinstance(vevents[0], instances):
                                self.fail("REPORT property %r returned calendar %s outside of request time range %r"
                                          % (property, property.calendar, query_timerange))

        return self.calendar_query("/calendar_query_time_range/", query, got_xml)

    def test_calendar_query_partial_recurring(self):
        """
        Partial retrieval of recurring events.
        (CalDAV-access-09, section 7.6.2)
        """
        raise SkipTest("test unimplemented")

    def test_calendar_query_expanded_recurring(self):
        """
        Expanded retrieval of recurring events.
        (CalDAV-access-09, section 7.6.3)
        """
        raise SkipTest("test unimplemented")

    def test_calendar_query_partial_freebusy(self):
        """
        Partial retrieval of stored free busy components.
        (CalDAV-access-09, section 7.6.4)
        """
        raise SkipTest("test unimplemented")

    def test_calendar_query_todo_alarm(self):
        """
        Retrieval of to-dos by alarm time range.
        (CalDAV-access-09, section 7.6.5)
        """
        raise SkipTest("test unimplemented")

    def test_calendar_query_by_uid(self):
        """
        Event by UID.
        (CalDAV-access-09, section 7.6.6)
        """
        uid = "C3189A88-1ED0-11D9-A5E0-000A958A3252"

        return self.simple_event_query(
            "/calendar_query_uid/",
            caldavxml.PropertyFilter(
                caldavxml.TextMatch.fromString(uid, False),
                name="UID",
            ),
            [uid]
        )

    def test_calendar_query_partstat(self):
        """
        Retrieval of events by participation status.
        (CalDAV-access-09, section 7.6.7)
        """
        raise SkipTest("test unimplemented")

    def test_calendar_query_all_events(self):
        """
        All events.
        (CalDAV-access-09, section 7.6.8)
        """
        uids = [r[0] for r in (os.path.splitext(f) for f in os.listdir(self.holidays_dir)) if r[1] == ".ics"]

        return self.simple_event_query("/calendar_query_events/", None, uids)

    def simple_event_query(self, cal_uri, event_filter, uids):
        query = caldavxml.CalendarQuery(
            davxml.PropertyContainer(
                davxml.GETETag(),
                caldavxml.CalendarData(),
            ),
            caldavxml.Filter(
                caldavxml.ComponentFilter(
                    caldavxml.ComponentFilter(
                        event_filter,
                        name="VEVENT",
                    ),
                    name="VCALENDAR",
                ),
            ),
        )

        def got_xml(doc):
            if not isinstance(doc.root_element, davxml.MultiStatus):
                self.fail("REPORT response XML root element is not multistatus: %r" % (doc.root_element,))

            for response in doc.root_element.childrenOfType(davxml.PropertyStatusResponse):
                for propstat in response.childrenOfType(davxml.PropertyStatus):
                    status = propstat.childOfType(davxml.Status)

                    if status.code != responsecode.OK:
                        self.fail("REPORT failed (status %s) to locate properties: %r"
                                  % (status.code, propstat))

                    properties = propstat.childOfType(davxml.PropertyContainer).children

                    for property in properties:
                        qname = property.qname()
                        if qname == (davxml.dav_namespace, "getetag"): continue
                        if qname != (caldavxml.caldav_namespace, "calendar-data"):
                            self.fail("Response included unexpected property %r" % (property,))

                        result_calendar = property.calendar()

                        if result_calendar is None:
                            self.fail("Invalid response CalDAV:calendar-data: %r" % (property,))

                        uid = result_calendar.resourceUID()

                        if uid in uids:
                            uids.remove(uid)
                        else:
                            self.fail("Got calendar for unexpected UID %r" % (uid,))

                        original_filename = file(os.path.join(self.holidays_dir, uid + ".ics"))
                        original_calendar = ical.Component.fromStream(original_filename)

                        self.assertEqual(result_calendar, original_calendar)

        return self.calendar_query(cal_uri, query, got_xml)

    def calendar_query(self, calendar_uri, query, got_xml):
        calendar_path = os.path.join(self.docroot, calendar_uri[1:])

        if os.path.exists(calendar_path): rmdir(calendar_path)

        def do_report(response):
            response = IResponse(response)

            if response.code != responsecode.CREATED:
                self.fail("MKCALENDAR failed: %s" % (response.code,))

            # Add holiday events to calendar
            # We're cheating by simply copying the files in
            for filename in os.listdir(self.holidays_dir):
                if os.path.splitext(filename)[1] != ".ics": continue
                path = os.path.join(self.holidays_dir, filename)
                shutil.copy(path, calendar_path)

            # Delete the index because we cheated
            index_path = os.path.join(calendar_path, db_basename)
            if os.path.isfile(index_path): os.remove(index_path)

            request = SimpleRequest(self.site, "REPORT", calendar_uri)
            request.stream = MemoryStream(query.toxml())

            def do_test(response):
                response = IResponse(response)

                if response.code != responsecode.MULTI_STATUS:
                    self.fail("REPORT failed: %s" % (response.code,))

                return davXMLFromStream(response.stream).addCallback(got_xml)

            return self.send(request, do_test)

        request = SimpleRequest(self.site, "MKCALENDAR", calendar_uri)

        return self.send(request, do_report)
