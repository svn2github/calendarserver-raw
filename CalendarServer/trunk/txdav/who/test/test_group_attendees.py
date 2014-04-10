##
# Copyright (c) 2014 Apple Inc. All rights reserved.
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
    group attendee tests
"""

from twext.who.test.test_xml import xmlService
from twisted.internet.defer import inlineCallbacks
from twisted.trial import unittest
from twistedcaldav.config import config
from twistedcaldav.ical import Component, normalize_iCalStr
from txdav.caldav.datastore.test.util import buildCalendarStore, populateCalendarsFrom, CommonCommonTests
from txdav.who.util import directoryFromConfig
import os

class GroupAttendeeReconciliation(CommonCommonTests, unittest.TestCase):
    """
    GroupAttendeeReconciliation tests
    """

    @inlineCallbacks
    def setUp(self):
        self.patch(config.Scheduling.Options, "AllowGroupAsAttendee", "True")

        yield super(GroupAttendeeReconciliation, self).setUp()
        self.xmlService = xmlService(self.mktemp(), xmlData=None)

        self.patch(config.DirectoryService.params, "xmlFile",
            os.path.join(
                os.path.dirname(__file__), "accounts", "groupAttendeeAccounts.xml"
            )
        )
        self.patch(config.ResourceService.params, "xmlFile",
            os.path.join(
                os.path.dirname(__file__), "accounts", "resources.xml"
            )
        )
        self._sqlCalendarStore = yield buildCalendarStore(self, self.notifierFactory, directoryFromConfig(config))
        yield self.populate()

        self.paths = {}


    def storeUnderTest(self):
        """
        Create and return a L{CalendarStore} for testing.
        """
        return self._sqlCalendarStore


    @inlineCallbacks
    def populate(self):
        yield populateCalendarsFrom(self.requirements, self.storeUnderTest())
        self.notifierFactory.reset()

    requirements = {
        "10000000-0000-0000-0000-000000000001" : {
            "calendar" : {}
        },
    }

    @inlineCallbacks
    def test_simplePUT(self):
        """
        Test that group attendee is expanded on PUT
        """
        calendar = yield self.calendarUnderTest(name="calendar", home="10000000-0000-0000-0000-000000000001")

        data_put_1 = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
VERSION:2.0
BEGIN:VEVENT
DTSTAMP:20051222T205953Z
CREATED:20060101T150000Z
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
SUMMARY:event 1
UID:event1@ninevah.local
ORGANIZER:MAILTO:user01@example.com
ATTENDEE:mailto:user01@example.com
ATTENDEE:MAILTO:group02@example.com
END:VEVENT
END:VCALENDAR"""

        data_get_1 = """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
BEGIN:VEVENT
UID:event1@ninevah.local
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
ATTENDEE;CN=User 01;EMAIL=user01@example.com;RSVP=TRUE:urn:uuid:10000000-0000-0000-0000-000000000001
ATTENDEE;CN=Group 02;CUTYPE=GROUP;EMAIL=group02@example.com;RSVP=TRUE;SCHEDULE-STATUS=3.7:urn:uuid:20000000-0000-0000-0000-000000000002
ATTENDEE;CN=User 06;EMAIL=user06@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000002";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000006
ATTENDEE;CN=User 07;EMAIL=user07@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000002";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000007
CREATED:20060101T150000Z
ORGANIZER;CN=User 01;EMAIL=user01@example.com:urn:uuid:10000000-0000-0000-0000-000000000001
SUMMARY:event 1
END:VEVENT
END:VCALENDAR
"""

        vcalendar1 = Component.fromString(data_put_1)
        yield calendar.createCalendarObjectWithName("data1.ics", vcalendar1)
        yield self.commit()

        cobj1 = yield self.calendarObjectUnderTest(name="data1.ics", calendar_name="calendar", home="10000000-0000-0000-0000-000000000001")
        vcalendar2 = yield cobj1.component()
        self.assertEqual(normalize_iCalStr(vcalendar2), normalize_iCalStr(data_get_1))


    @inlineCallbacks
    def test_unknownPUT(self):
        """
        Test unknown group with CUTYPE=GROUP handled
        """
        calendar = yield self.calendarUnderTest(name="calendar", home="10000000-0000-0000-0000-000000000001")

        data_put_1 = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
VERSION:2.0
BEGIN:VEVENT
DTSTAMP:20051222T205953Z
CREATED:20060101T150000Z
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
SUMMARY:event 1
UID:event1@ninevah.local
ORGANIZER:MAILTO:user01@example.com
ATTENDEE:mailto:user01@example.com
ATTENDEE;CUTYPE=GROUP:urn:uuid:FFFFFFFF-EEEE-DDDD-CCCC-BBBBBBBBBBBB
END:VEVENT
END:VCALENDAR"""

        data_get_1 = """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
BEGIN:VEVENT
UID:event1@ninevah.local
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
ATTENDEE;CN=User 01;EMAIL=user01@example.com;RSVP=TRUE:urn:uuid:10000000-0000-0000-0000-000000000001
ATTENDEE;CUTYPE=GROUP;RSVP=TRUE;SCHEDULE-STATUS=3.7:urn:uuid:FFFFFFFF-EEEE-DDDD-CCCC-BBBBBBBBBBBB
CREATED:20060101T150000Z
ORGANIZER;CN=User 01;EMAIL=user01@example.com:urn:uuid:10000000-0000-0000-0000-000000000001
SUMMARY:event 1
END:VEVENT
END:VCALENDAR
"""

        vcalendar1 = Component.fromString(data_put_1)
        yield calendar.createCalendarObjectWithName("data1.ics", vcalendar1)
        yield self.commit()

        cobj1 = yield self.calendarObjectUnderTest(name="data1.ics", calendar_name="calendar", home="10000000-0000-0000-0000-000000000001")
        vcalendar2 = yield cobj1.component()
        self.assertEqual(normalize_iCalStr(vcalendar2), normalize_iCalStr(data_get_1))


    @inlineCallbacks
    def test_primaryAttendeeInGroupPUT(self):
        """
        Test that primary attendee also in group remains primary
        """
        calendar = yield self.calendarUnderTest(name="calendar", home="10000000-0000-0000-0000-000000000001")

        data_put_1 = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
VERSION:2.0
BEGIN:VEVENT
DTSTAMP:20051222T205953Z
CREATED:20060101T150000Z
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
SUMMARY:event 1
UID:event1@ninevah.local
ORGANIZER:MAILTO:user01@example.com
ATTENDEE:mailto:user01@example.com
ATTENDEE:mailto:user02@example.com
ATTENDEE:MAILTO:group01@example.com
END:VEVENT
END:VCALENDAR"""

        data_get_1 = """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
BEGIN:VEVENT
UID:event1@ninevah.local
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
ATTENDEE;CN=User 01;EMAIL=user01@example.com;RSVP=TRUE:urn:uuid:10000000-0000-0000-0000-000000000001
ATTENDEE;CN=User 02;EMAIL=user02@example.com;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000002
ATTENDEE;CN=Group 01;CUTYPE=GROUP;EMAIL=group01@example.com;RSVP=TRUE;SCHEDULE-STATUS=3.7:urn:uuid:20000000-0000-0000-0000-000000000001
CREATED:20060101T150000Z
ORGANIZER;CN=User 01;EMAIL=user01@example.com:urn:uuid:10000000-0000-0000-0000-000000000001
SUMMARY:event 1
END:VEVENT
END:VCALENDAR
"""
        vcalendar1 = Component.fromString(data_put_1)
        yield calendar.createCalendarObjectWithName("data1.ics", vcalendar1)
        yield self.commit()

        cobj1 = yield self.calendarObjectUnderTest(name="data1.ics", calendar_name="calendar", home="10000000-0000-0000-0000-000000000001")
        vcalendar1 = yield cobj1.component()
        self.assertEqual(normalize_iCalStr(vcalendar1), normalize_iCalStr(data_get_1))


    @inlineCallbacks
    def test_nestedPUT(self):
        """
        Test that nested groups are expanded expanded on PUT
        """
        calendar = yield self.calendarUnderTest(name="calendar", home="10000000-0000-0000-0000-000000000001")

        data_put_1 = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
VERSION:2.0
BEGIN:VEVENT
DTSTAMP:20051222T205953Z
CREATED:20060101T150000Z
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
SUMMARY:event 1
UID:event1@ninevah.local
ORGANIZER:MAILTO:user01@example.com
ATTENDEE:mailto:user01@example.com
ATTENDEE:urn:uuid:20000000-0000-0000-0000-000000000004
END:VEVENT
END:VCALENDAR"""

        data_get_1 = """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
BEGIN:VEVENT
UID:event1@ninevah.local
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
ATTENDEE;CN=User 01;EMAIL=user01@example.com;RSVP=TRUE:urn:uuid:10000000-0000-0000-0000-000000000001
ATTENDEE;CN=Group 04;CUTYPE=GROUP;RSVP=TRUE;SCHEDULE-STATUS=3.7:urn:uuid:20000000-0000-0000-0000-000000000004
ATTENDEE;CN=User 06;EMAIL=user06@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000006
ATTENDEE;CN=User 07;EMAIL=user07@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000007
ATTENDEE;CN=User 08;EMAIL=user08@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000008
ATTENDEE;CN=User 09;EMAIL=user09@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000009
ATTENDEE;CN=User 10;EMAIL=user10@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000010
CREATED:20060101T150000Z
ORGANIZER;CN=User 01;EMAIL=user01@example.com:urn:uuid:10000000-0000-0000-0000-000000000001
SUMMARY:event 1
END:VEVENT
END:VCALENDAR
"""

        vcalendar1 = Component.fromString(data_put_1)
        yield calendar.createCalendarObjectWithName("data1.ics", vcalendar1)
        yield self.commit()

        cobj1 = yield self.calendarObjectUnderTest(name="data1.ics", calendar_name="calendar", home="10000000-0000-0000-0000-000000000001")
        vcalendar2 = yield cobj1.component()
        self.assertEqual(normalize_iCalStr(vcalendar2), normalize_iCalStr(data_get_1))


    @inlineCallbacks
    def test_twoGroupPUT(self):
        """
        Test that expanded users in two primary groups have groups in MEMBERS param
        """
        calendar = yield self.calendarUnderTest(name="calendar", home="10000000-0000-0000-0000-000000000001")

        data_put_1 = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
VERSION:2.0
BEGIN:VEVENT
DTSTAMP:20051222T205953Z
CREATED:20060101T150000Z
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
SUMMARY:event 1
UID:event1@ninevah.local
ORGANIZER:MAILTO:user01@example.com
ATTENDEE:mailto:user01@example.com
ATTENDEE:MAILTO:group02@example.com
ATTENDEE:urn:uuid:20000000-0000-0000-0000-000000000004
END:VEVENT
END:VCALENDAR"""

        data_get_1 = """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
BEGIN:VEVENT
UID:event1@ninevah.local
DTSTART;TZID=US/Eastern:20140101T100000
DURATION:PT1H
ATTENDEE;CN=User 01;EMAIL=user01@example.com;RSVP=TRUE:urn:uuid:10000000-0000-0000-0000-000000000001
ATTENDEE;CN=Group 02;CUTYPE=GROUP;EMAIL=group02@example.com;RSVP=TRUE;SCHEDULE-STATUS=3.7:urn:uuid:20000000-0000-0000-0000-000000000002
ATTENDEE;CN=Group 04;CUTYPE=GROUP;RSVP=TRUE;SCHEDULE-STATUS=3.7:urn:uuid:20000000-0000-0000-0000-000000000004
ATTENDEE;CN=User 06;EMAIL=user06@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004","urn:uuid:20000000-0000-0000-0000-000000000002";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000006
ATTENDEE;CN=User 07;EMAIL=user07@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004","urn:uuid:20000000-0000-0000-0000-000000000002";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000007
ATTENDEE;CN=User 08;EMAIL=user08@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000008
ATTENDEE;CN=User 09;EMAIL=user09@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000009
ATTENDEE;CN=User 10;EMAIL=user10@example.com;MEMBER="urn:uuid:20000000-0000-0000-0000-000000000004";PARTSTAT=NEEDS-ACTION;RSVP=TRUE;SCHEDULE-STATUS=1.2:urn:uuid:10000000-0000-0000-0000-000000000010
CREATED:20060101T150000Z
ORGANIZER;CN=User 01;EMAIL=user01@example.com:urn:uuid:10000000-0000-0000-0000-000000000001
SUMMARY:event 1
END:VEVENT
END:VCALENDAR
"""

        vcalendar1 = Component.fromString(data_put_1)
        yield calendar.createCalendarObjectWithName("data1.ics", vcalendar1)
        yield self.commit()

        cobj1 = yield self.calendarObjectUnderTest(name="data1.ics", calendar_name="calendar", home="10000000-0000-0000-0000-000000000001")
        vcalendar2 = yield cobj1.component()
        self.assertEqual(normalize_iCalStr(vcalendar2), normalize_iCalStr(data_get_1))
