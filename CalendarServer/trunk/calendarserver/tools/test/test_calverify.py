##
# Copyright (c) 2012 Apple Inc. All rights reserved.
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
Tests for calendarserver.tools.calverify
"""

from StringIO import StringIO
from calendarserver.tap.util import getRootResource
from calendarserver.tools.calverify import CalVerifyService
from pycalendar.datetime import PyCalendarDateTime
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.trial import unittest
from twistedcaldav import caldavxml
from twistedcaldav.config import config
from txdav.base.propertystore.base import PropertyName
from txdav.caldav.datastore import util
from txdav.common.datastore.test.util import buildStore, populateCalendarsFrom, CommonCommonTests
from txdav.xml import element as davxml
import os


OK_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:OK
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

# Missing DTSTAMP
BAD1_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD1
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

# Bad recurrence
BAD2_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD2
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
RRULE:FREQ=DAILY;COUNT=3
SEQUENCE:2
END:VEVENT
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD2
RECURRENCE-ID:20000307T120000Z
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

# Bad recurrence
BAD3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD2
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
RRULE:FREQ=DAILY;COUNT=3
SEQUENCE:2
END:VEVENT
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD2
RECURRENCE-ID:20000307T120000Z
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

# Missing Organizer
BAD3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD3
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
RRULE:FREQ=DAILY;COUNT=3
SEQUENCE:2
END:VEVENT
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD3
RECURRENCE-ID:20000307T111500Z
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:mailto:example2@example.com
ATTENDEE:mailto:example1@example.com
ATTENDEE:mailto:example2@example.com
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

# https Organizer
BAD4_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD4
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
ORGANIZER:http://demo.com:8008/principals/__uids__/D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:mailto:example1@example.com
ATTENDEE:mailto:example2@example.com
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")


# https Attendee
BAD5_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD5
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
ORGANIZER:mailto:example1@example.com
ATTENDEE:http://demo.com:8008/principals/__uids__/D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:mailto:example2@example.com
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")


# https Organizer and Attendee
BAD6_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD6
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
ORGANIZER:http://demo.com:8008/principals/__uids__/D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:http://demo.com:8008/principals/__uids__/D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:mailto:example2@example.com
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")


# Non-base64 Organizer and Attendee parameter
BAD7_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:BAD7
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
ORGANIZER;CALENDARSERVER-OLD-CUA="http://demo.com:8008/principals/__uids__/
 D46F3D71-04B7-43C2-A7B6-6F92F92E61D0":urn:uuid:D46F3D71-04B7-43C2-A7B6-6F9
 2F92E61D0
ATTENDEE;CALENDARSERVER-OLD-CUA="http://demo.com:8008/principals/__uids__/D
 46F3D71-04B7-43C2-A7B6-6F92F92E61D0":urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92
 F92E61D0
ATTENDEE:mailto:example2@example.com
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")


# Base64 Organizer and Attendee parameter
OK8_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:OK8
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
ORGANIZER;CALENDARSERVER-OLD-CUA="base64-aHR0cDovL2RlbW8uY29tOjgwMDgvcHJpbm
 NpcGFscy9fX3VpZHNfXy9ENDZGM0Q3MS0wNEI3LTQzQzItQTdCNi02RjkyRjkyRTYxRDA=":
 urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;CALENDARSERVER-OLD-CUA="base64-aHR0cDovL2RlbW8uY29tOjgwMDgvcHJpbmN
 pcGFscy9fX3VpZHNfXy9ENDZGM0Q3MS0wNEI3LTQzQzItQTdCNi02RjkyRjkyRTYxRDA=":u
 rn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:mailto:example2@example.com
SEQUENCE:2
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

BAD9_ICS =                 """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VTIMEZONE
TZID:US/Pacific
BEGIN:STANDARD
DTSTART:19621028T020000
RRULE:FREQ=YEARLY;UNTIL=20061029T090000Z;BYDAY=-1SU;BYMONTH=10
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19870405T020000
RRULE:FREQ=YEARLY;UNTIL=20060402T100000Z;BYDAY=1SU;BYMONTH=4
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:BAD9
DTSTART;TZID=US/Pacific:20111103T150000
DTEND;TZID=US/Pacific:20111103T160000
ATTENDEE;CALENDARSERVER-OLD-CUA="//example.com\\:8443/principals/users/cyrus
 /;CN=\\"Cyrus Daboo\\";CUTYPE=INDIVIDUAL;EMAIL=\\"cyrus@example.com\\";PARTSTAT=ACC
 EPTED:urn:uuid:7B2636C7-07F6-4475-924B-2854107F7A22";CN=Cyrus Daboo;EMAIL=c
 yrus@example.com;RSVP=TRUE:urn:uuid:7B2636C7-07F6-4475-924B-2854107F7A22
ATTENDEE;CN=John Smith;CUTYPE=INDIVIDUAL;EMAIL=smith@example.com;PARTSTAT=AC
 CEPTED;ROLE=REQ-PARTICIPANT:urn:uuid:E975EB3D-C412-411B-A655-C3BE4949788C
CREATED:20090730T214912Z
DTSTAMP:20120421T182823Z
ORGANIZER;CALENDARSERVER-OLD-CUA="//example.com\\:8443/principals/users/cyru
 s/;CN=\\"Cyrus Daboo\\";EMAIL=\\"cyrus@example.com\\":urn:uuid:7B2636C7-07F6-4475-9
 24B-2854107F7A22";CN=Cyrus Daboo;EMAIL=cyrus@example.com:urn:uuid:7B2636C7-
 07F6-4475-924B-2854107F7A22
RRULE:FREQ=WEEKLY;COUNT=400
SEQUENCE:18
SUMMARY:1-on-1
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")


class CalVerifyDataTests(CommonCommonTests, unittest.TestCase):
    """
    Tests calverify for iCalendar data problems.
    """

    metadata = {
        "accessMode": "PUBLIC",
        "isScheduleObject": True,
        "scheduleTag": "abc",
        "scheduleEtags": (),
        "hasPrivateComment": False,
    }

    requirements = {
        "home1" : {
            "calendar1" : {
                "ok.ics"   : (OK_ICS, metadata,),
                "bad1.ics" : (BAD1_ICS, metadata,),
                "bad2.ics" : (BAD2_ICS, metadata,),
                "bad3.ics" : (BAD3_ICS, metadata,),
                "bad4.ics" : (BAD4_ICS, metadata,),
                "bad5.ics" : (BAD5_ICS, metadata,),
                "bad6.ics" : (BAD6_ICS, metadata,),
                "bad7.ics" : (BAD7_ICS, metadata,),
                "ok8.ics"  : (OK8_ICS, metadata,),
                "bad9.ics" : (BAD9_ICS, metadata,),
            }
        },
    }

    @inlineCallbacks
    def setUp(self):
        yield super(CalVerifyDataTests, self).setUp()
        self._sqlCalendarStore = yield buildStore(self, self.notifierFactory)
        yield self.populate()

        self.patch(config.DirectoryService.params, "xmlFile",
            os.path.join(
                os.path.dirname(__file__), "calverify", "accounts.xml"
            )
        )
        self.patch(config.ResourceService.params, "xmlFile",
            os.path.join(
                os.path.dirname(__file__), "calverify", "resources.xml"
            )
        )
        self.rootResource = getRootResource(config, self._sqlCalendarStore)
        self.directory = self.rootResource.getDirectory()


    @inlineCallbacks
    def populate(self):
        
        # Need to bypass normal validation inside the store
        util.validationBypass = True
        yield populateCalendarsFrom(self.requirements, self.storeUnderTest(), migrating=True)
        util.validationBypass = False
        self.notifierFactory.reset()


    def storeUnderTest(self):
        """
        Create and return a L{CalendarStore} for testing.
        """
        return self._sqlCalendarStore


    @inlineCallbacks
    def homeUnderTest(self, txn=None):
        """
        Get the calendar home detailed by C{requirements['home1']}.
        """
        if txn is None:
            txn = self.transactionUnderTest()
        returnValue((yield txn.calendarHomeWithUID("home1")))


    @inlineCallbacks
    def calendarUnderTest(self, txn=None):
        """
        Get the calendar detailed by C{requirements['home1']['calendar1']}.
        """
        returnValue((yield
            (yield self.homeUnderTest(txn)).calendarWithName("calendar1"))
        )


    def verifyResultsByUID(self, results, expected):
        reported = set([(home, uid) for home, uid, _ignore_resid, _ignore_reason in results])
        self.assertEqual(reported, expected)


    @inlineCallbacks
    def test_scanBadData(self):
        """
        CalVerifyService.doScan without fix. Make sure it detects common errors.
        Make sure sync-token is not changed.
        """

        sync_token_old = (yield (yield self.calendarUnderTest()).syncToken())
        self.commit()

        options = {
            "ical":True,
            "nobase64":False,
            "verbose":False,
            "uid":"",
            "uuid":"",
        }
        output = StringIO()
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(True, False, False)

        self.assertEqual(calverify.results["Number of events to process"], 10)
        self.verifyResultsByUID(calverify.results["Bad iCalendar data"], set((
            ("home1", "BAD1",),
            ("home1", "BAD2",),
            ("home1", "BAD3",),
            ("home1", "BAD4",),
            ("home1", "BAD5",),
            ("home1", "BAD6",),
            ("home1", "BAD7",),
            ("home1", "BAD9",),
        )))

        sync_token_new = (yield (yield self.calendarUnderTest()).syncToken())
        self.assertEqual(sync_token_old, sync_token_new)


    @inlineCallbacks
    def test_fixBadData(self):
        """
        CalVerifyService.doScan with fix. Make sure it detects and fixes as much as it can.
        Make sure sync-token is changed.
        """

        sync_token_old = (yield (yield self.calendarUnderTest()).syncToken())
        self.commit()

        options = {
            "ical":True,
            "nobase64":False,
            "verbose":False,
            "uid":"",
            "uuid":"",
        }
        output = StringIO()
        
        # Do fix
        self.patch(config.Scheduling.Options, "PrincipalHostAliases", "demo.com")
        self.patch(config, "HTTPPort", 8008)
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(True, False, True)

        self.assertEqual(calverify.results["Number of events to process"], 10)
        self.verifyResultsByUID(calverify.results["Bad iCalendar data"], set((
            ("home1", "BAD1",),
            ("home1", "BAD2",),
            ("home1", "BAD3",),
            ("home1", "BAD4",),
            ("home1", "BAD5",),
            ("home1", "BAD6",),
            ("home1", "BAD7",),
            ("home1", "BAD9",),
        )))

        # Do scan
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(True, False, False)

        self.assertEqual(calverify.results["Number of events to process"], 10)
        self.verifyResultsByUID(calverify.results["Bad iCalendar data"], set((
            ("home1", "BAD1",),
        )))

        sync_token_new = (yield (yield self.calendarUnderTest()).syncToken())
        self.assertNotEqual(sync_token_old, sync_token_new)

    @inlineCallbacks
    def test_scanBadCuaOnly(self):
        """
        CalVerifyService.doScan without fix for CALENDARSERVER-OLD-CUA only. Make sure it detects
        and fixes as much as it can. Make sure sync-token is not changed.
        """

        sync_token_old = (yield (yield self.calendarUnderTest()).syncToken())
        self.commit()

        options = {
            "ical":False,
            "badcua":True,
            "nobase64":False,
            "verbose":False,
            "uid":"",
            "uuid":"",
        }
        output = StringIO()
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(True, False, False)

        self.assertEqual(calverify.results["Number of events to process"], 10)
        self.verifyResultsByUID(calverify.results["Bad iCalendar data"], set((
            ("home1", "BAD4",),
            ("home1", "BAD5",),
            ("home1", "BAD6",),
            ("home1", "BAD7",),
            ("home1", "BAD9",),
        )))

        sync_token_new = (yield (yield self.calendarUnderTest()).syncToken())
        self.assertEqual(sync_token_old, sync_token_new)

    @inlineCallbacks
    def test_fixBadCuaOnly(self):
        """
        CalVerifyService.doScan with fix for CALENDARSERVER-OLD-CUA only. Make sure it detects
        and fixes as much as it can. Make sure sync-token is changed.
        """

        sync_token_old = (yield (yield self.calendarUnderTest()).syncToken())
        self.commit()

        options = {
            "ical":False,
            "badcua":True,
            "nobase64":False,
            "verbose":False,
            "uid":"",
            "uuid":"",
        }
        output = StringIO()
        
        # Do fix
        self.patch(config.Scheduling.Options, "PrincipalHostAliases", "demo.com")
        self.patch(config, "HTTPPort", 8008)
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(True, False, True)

        self.assertEqual(calverify.results["Number of events to process"], 10)
        self.verifyResultsByUID(calverify.results["Bad iCalendar data"], set((
            ("home1", "BAD4",),
            ("home1", "BAD5",),
            ("home1", "BAD6",),
            ("home1", "BAD7",),
            ("home1", "BAD9",),
        )))

        # Do scan
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(True, False, False)

        self.assertEqual(calverify.results["Number of events to process"], 10)
        self.verifyResultsByUID(calverify.results["Bad iCalendar data"], set((
        )))

        sync_token_new = (yield (yield self.calendarUnderTest()).syncToken())
        self.assertNotEqual(sync_token_old, sync_token_new)

    def test_fixBadCuaLines(self):
        """
        CalVerifyService.fixBadOldCuaLines. Make sure it applies correct fix.
        """

        data = (
            (
                """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:REQUEST
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VTIMEZONE
TZID:US/Pacific
BEGIN:STANDARD
DTSTART:19621028T020000
RRULE:FREQ=YEARLY;UNTIL=20061029T090000Z;BYDAY=-1SU;BYMONTH=10
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19870405T020000
RRULE:FREQ=YEARLY;UNTIL=20060402T100000Z;BYDAY=1SU;BYMONTH=4
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:32956D5C-579F-46FD-BAE3-4A6C354B8CA3
DTSTART;TZID=US/Pacific:20111103T150000
DTEND;TZID=US/Pacific:20111103T160000
ATTENDEE;CALENDARSERVER-OLD-CUA="//example.com\\:8443/principals/users/cyrus
 /;CN="Cyrus Daboo";CUTYPE=INDIVIDUAL;EMAIL="cyrus@example.com";PARTSTAT=ACC
 EPTED:urn:uuid:7B2636C7-07F6-4475-924B-2854107F7A22";CN=Cyrus Daboo;EMAIL=c
 yrus@example.com;RSVP=TRUE:urn:uuid:7B2636C7-07F6-4475-924B-2854107F7A22
ATTENDEE;CN=John Smith;CUTYPE=INDIVIDUAL;EMAIL=smith@example.com;PARTSTAT=AC
 CEPTED;ROLE=REQ-PARTICIPANT:urn:uuid:E975EB3D-C412-411B-A655-C3BE4949788C
CREATED:20090730T214912Z
DTSTAMP:20120421T182823Z
ORGANIZER;CALENDARSERVER-OLD-CUA="//example.com\\:8443/principals/users/cyru
 s/;CN="Cyrus Daboo";EMAIL="cyrus@example.com":urn:uuid:7B2636C7-07F6-4475-9
 24B-2854107F7A22";CN=Cyrus Daboo;EMAIL=cyrus@example.com:urn:uuid:7B2636C7-
 07F6-4475-924B-2854107F7A22
RRULE:FREQ=WEEKLY;COUNT=400
SEQUENCE:18
SUMMARY:1-on-1
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n"),
                """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:REQUEST
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VTIMEZONE
TZID:US/Pacific
BEGIN:STANDARD
DTSTART:19621028T020000
RRULE:FREQ=YEARLY;UNTIL=20061029T090000Z;BYDAY=-1SU;BYMONTH=10
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19870405T020000
RRULE:FREQ=YEARLY;UNTIL=20060402T100000Z;BYDAY=1SU;BYMONTH=4
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:32956D5C-579F-46FD-BAE3-4A6C354B8CA3
DTSTART;TZID=US/Pacific:20111103T150000
DTEND;TZID=US/Pacific:20111103T160000
ATTENDEE;CALENDARSERVER-OLD-CUA="https://example.com:8443/principals/users/c
 yrus/";CN=Cyrus Daboo;EMAIL=cyrus@example.com;RSVP=TRUE:urn:uuid:7B2636C7-0
 7F6-4475-924B-2854107F7A22
ATTENDEE;CN=John Smith;CUTYPE=INDIVIDUAL;EMAIL=smith@example.com;PARTSTAT=AC
 CEPTED;ROLE=REQ-PARTICIPANT:urn:uuid:E975EB3D-C412-411B-A655-C3BE4949788C
CREATED:20090730T214912Z
DTSTAMP:20120421T182823Z
ORGANIZER;CALENDARSERVER-OLD-CUA="https://example.com:8443/principals/users/
 cyrus/";CN=Cyrus Daboo;EMAIL=cyrus@example.com:urn:uuid:7B2636C7-07F6-4475-
 924B-2854107F7A22
RRULE:FREQ=WEEKLY;COUNT=400
SEQUENCE:18
SUMMARY:1-on-1
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n"),
                """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:REQUEST
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VTIMEZONE
TZID:US/Pacific
BEGIN:STANDARD
DTSTART:19621028T020000
RRULE:FREQ=YEARLY;UNTIL=20061029T090000Z;BYDAY=-1SU;BYMONTH=10
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19870405T020000
RRULE:FREQ=YEARLY;UNTIL=20060402T100000Z;BYDAY=1SU;BYMONTH=4
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:32956D5C-579F-46FD-BAE3-4A6C354B8CA3
DTSTART;TZID=US/Pacific:20111103T150000
DTEND;TZID=US/Pacific:20111103T160000
ATTENDEE;CALENDARSERVER-OLD-CUA=base64-aHR0cHM6Ly9leGFtcGxlLmNvbTo4NDQzL3Bya
 W5jaXBhbHMvdXNlcnMvY3lydXMv;CN=Cyrus Daboo;EMAIL=cyrus@example.com;RSVP=TRU
 E:urn:uuid:7B2636C7-07F6-4475-924B-2854107F7A22
ATTENDEE;CN=John Smith;CUTYPE=INDIVIDUAL;EMAIL=smith@example.com;PARTSTAT=AC
 CEPTED;ROLE=REQ-PARTICIPANT:urn:uuid:E975EB3D-C412-411B-A655-C3BE4949788C
CREATED:20090730T214912Z
DTSTAMP:20120421T182823Z
ORGANIZER;CALENDARSERVER-OLD-CUA=base64-aHR0cHM6Ly9leGFtcGxlLmNvbTo4NDQzL3By
 aW5jaXBhbHMvdXNlcnMvY3lydXMv;CN=Cyrus Daboo;EMAIL=cyrus@example.com:urn:uui
 d:7B2636C7-07F6-4475-924B-2854107F7A22
RRULE:FREQ=WEEKLY;COUNT=400
SEQUENCE:18
SUMMARY:1-on-1
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n"),
            ),
        )
        
        optionsNo64 = {
            "ical":True,
            "nobase64":True,
            "verbose":False,
            "uid":"",
            "uuid":"",
        }
        calverifyNo64 = CalVerifyService(self._sqlCalendarStore, optionsNo64, StringIO(), reactor, config)

        options64 = {
            "ical":True,
            "nobase64":False,
            "verbose":False,
            "uid":"",
            "uuid":"",
        }
        calverify64 = CalVerifyService(self._sqlCalendarStore, options64, StringIO(), reactor, config)

        for bad, oknobase64, okbase64 in data:
            bad = bad.replace("\r\n ", "")
            oknobase64 = oknobase64.replace("\r\n ", "")
            okbase64 = okbase64.replace("\r\n ", "")
            self.assertEqual(calverifyNo64.fixBadOldCuaLines(bad), oknobase64)
            self.assertEqual(calverify64.fixBadOldCuaLines(bad), okbase64)


class CalVerifyMismatchTestsBase(CommonCommonTests, unittest.TestCase):
    """
    Tests calverify for iCalendar mismatch problems.
    """

    metadata = {
        "accessMode": "PUBLIC",
        "isScheduleObject": True,
        "scheduleTag": "abc",
        "scheduleEtags": (),
        "hasPrivateComment": False,
    }

    uuid1 = "D46F3D71-04B7-43C2-A7B6-6F92F92E61D0"
    uuid2 = "47B16BB4-DB5F-4BF6-85FE-A7DA54230F92"
    uuid3 = "AC478592-7783-44D1-B2AE-52359B4E8415"

    @inlineCallbacks
    def setUp(self):
        yield super(CalVerifyMismatchTestsBase, self).setUp()
        self._sqlCalendarStore = yield buildStore(self, self.notifierFactory)
        yield self.populate()

        inbox = (yield self.calendarUnderTest(self.uuid3, "inbox"))
        inbox.properties()[PropertyName.fromElement(caldavxml.ScheduleDefaultCalendarURL)] = caldavxml.ScheduleDefaultCalendarURL(
            davxml.HRef.fromString("/calendars/__uids__/%s/calendar2/" % (self.uuid3,))
        )
        yield self.commit()

        self.patch(config.DirectoryService.params, "xmlFile",
            os.path.join(
                os.path.dirname(__file__), "calverify", "accounts.xml"
            )
        )
        self.patch(config.ResourceService.params, "xmlFile",
            os.path.join(
                os.path.dirname(__file__), "calverify", "resources.xml"
            )
        )
        self.rootResource = getRootResource(config, self._sqlCalendarStore)
        self.directory = self.rootResource.getDirectory()


    @inlineCallbacks
    def populate(self):
        
        # Need to bypass normal validation inside the store
        util.validationBypass = True
        yield populateCalendarsFrom(self.requirements, self.storeUnderTest(), migrating=True)
        util.validationBypass = False
        self.notifierFactory.reset()


    def storeUnderTest(self):
        """
        Create and return a L{CalendarStore} for testing.
        """
        return self._sqlCalendarStore


    @inlineCallbacks
    def homeUnderTest(self, name=None, txn=None):
        """
        Get the calendar home detailed by C{requirements[name]}.
        """
        if txn is None:
            txn = self.transactionUnderTest()
        returnValue((yield txn.calendarHomeWithUID(name)))


    @inlineCallbacks
    def calendarUnderTest(self, home_name, name="calendar", txn=None):
        """
        Get the calendar detailed by C{requirements[home_name][name]}.
        """
        returnValue((yield
            (yield self.homeUnderTest(home_name, txn)).calendarWithName(name))
        )


    @inlineCallbacks
    def calendarObjectUnderTest(self, home_name, calendar_name, name, txn=None):
        """
        Get the calendar object detailed by C{requirements[home_name][calendar_name][name]}.
        """
        returnValue((yield
            (yield self.calendarUnderTest(home_name, calendar_name, txn)).calendarObjectWithName(name))
        )


class CalVerifyMismatchTestsNonRecurring(CalVerifyMismatchTestsBase):
    """
    Tests calverify for iCalendar mismatch problems for non-recurring events.
    """

    # Organizer has event, attendees do not
    MISSING_ATTENDEE_1_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISSING_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    # Attendees have event, organizer does not
    MISSING_ORGANIZER_2_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISSING_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISSING_ORGANIZER_3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISSING_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    # Attendee partstat mismatch
    MISMATCH_ATTENDEE_1_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=DECLINED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH_ATTENDEE_2_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH_ATTENDEE_3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    # Attendee events outside time range
    MISMATCH2_ATTENDEE_1_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH2_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=DECLINED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH2_ATTENDEE_2_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH2_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:19990307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH2_ATTENDEE_3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH2_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:19990307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    # Organizer event outside time range
    MISMATCH_ORGANIZER_1_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:19990307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=DECLINED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH_ORGANIZER_2_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    # Attendee uuid3 has event with different organizer
    MISMATCH3_ATTENDEE_1_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH3_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH3_ATTENDEE_2_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH3_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH3_ATTENDEE_3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH3_ATTENDEE_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH_ORGANIZER_3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=NEEDS-ACTION:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    # Attendee uuid3 has event they are not invited to
    MISMATCH2_ORGANIZER_1_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH2_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=DECLINED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH2_ORGANIZER_2_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH2_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=DECLINED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

    MISMATCH2_ORGANIZER_3_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.1//EN
CALSCALE:GREGORIAN
BEGIN:VEVENT
CREATED:20100303T181216Z
UID:MISMATCH2_ORGANIZER_ICS
DTEND:20000307T151500Z
TRANSP:OPAQUE
SUMMARY:Ancient event
DTSTART:20000307T111500Z
DTSTAMP:20100303T181220Z
SEQUENCE:2
ORGANIZER:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:D46F3D71-04B7-43C2-A7B6-6F92F92E61D0
ATTENDEE;PARTSTAT=DECLINED:urn:uuid:47B16BB4-DB5F-4BF6-85FE-A7DA54230F92
ATTENDEE;PARTSTAT=ACCEPTED:urn:uuid:AC478592-7783-44D1-B2AE-52359B4E8415
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")


    requirements = {
        CalVerifyMismatchTestsBase.uuid1 : {
            "calendar" : {
                 "missing_attendee.ics"      : (MISSING_ATTENDEE_1_ICS, CalVerifyMismatchTestsBase.metadata,),
                 "mismatched_attendee.ics"   : (MISMATCH_ATTENDEE_1_ICS, CalVerifyMismatchTestsBase.metadata,),
                 "mismatched2_attendee.ics"  : (MISMATCH2_ATTENDEE_1_ICS, CalVerifyMismatchTestsBase.metadata,),
                 "mismatched3_attendee.ics"  : (MISMATCH3_ATTENDEE_1_ICS, CalVerifyMismatchTestsBase.metadata,),
                 "mismatched_organizer.ics"  : (MISMATCH_ORGANIZER_1_ICS, CalVerifyMismatchTestsBase.metadata,),
                 "mismatched2_organizer.ics" : (MISMATCH2_ORGANIZER_1_ICS, CalVerifyMismatchTestsBase.metadata,),
           },
           "inbox" : {},
        },
        CalVerifyMismatchTestsBase.uuid2 : {
            "calendar" : {
                "mismatched_attendee.ics"   : (MISMATCH_ATTENDEE_2_ICS, CalVerifyMismatchTestsBase.metadata,),
                "mismatched2_attendee.ics"  : (MISMATCH2_ATTENDEE_2_ICS, CalVerifyMismatchTestsBase.metadata,),
                "mismatched3_attendee.ics"  : (MISMATCH3_ATTENDEE_2_ICS, CalVerifyMismatchTestsBase.metadata,),
                "missing_organizer.ics"     : (MISSING_ORGANIZER_2_ICS, CalVerifyMismatchTestsBase.metadata,),
                "mismatched_organizer.ics"  : (MISMATCH_ORGANIZER_2_ICS, CalVerifyMismatchTestsBase.metadata,),
                "mismatched2_organizer.ics" : (MISMATCH2_ORGANIZER_2_ICS, CalVerifyMismatchTestsBase.metadata,),
            },
           "inbox" : {},
        },
        CalVerifyMismatchTestsBase.uuid3 : {
            "calendar" : {
                "mismatched_attendee.ics"   : (MISMATCH_ATTENDEE_3_ICS, CalVerifyMismatchTestsBase.metadata,),
                "mismatched3_attendee.ics"  : (MISMATCH3_ATTENDEE_3_ICS, CalVerifyMismatchTestsBase.metadata,),
                "missing_organizer.ics"     : (MISSING_ORGANIZER_3_ICS, CalVerifyMismatchTestsBase.metadata,),
                "mismatched2_organizer.ics" : (MISMATCH2_ORGANIZER_3_ICS, CalVerifyMismatchTestsBase.metadata,),
            },
            "calendar2" : {
                "mismatched_organizer.ics" : (MISMATCH_ORGANIZER_3_ICS, CalVerifyMismatchTestsBase.metadata,),
                "mismatched2_attendee.ics" : (MISMATCH2_ATTENDEE_3_ICS, CalVerifyMismatchTestsBase.metadata,),
            },
           "inbox" : {},
        },
    }

    @inlineCallbacks
    def test_scanMismatchOnly(self):
        """
        CalVerifyService.doScan without fix for mismatches. Make sure it detects
        as much as it can. Make sure sync-token is not changed.
        """

        sync_token_old1 = (yield (yield self.calendarUnderTest(self.uuid1)).syncToken())
        sync_token_old2 = (yield (yield self.calendarUnderTest(self.uuid2)).syncToken())
        sync_token_old3 = (yield (yield self.calendarUnderTest(self.uuid3)).syncToken())
        self.commit()

        options = {
            "ical":False,
            "badcua":False,
            "mismatch":True,
            "nobase64":False,
            "verbose":False,
            "details":False,
            "uid":"",
            "uuid":"",
        }
        output = StringIO()
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(False, True, False, start=PyCalendarDateTime(2000, 1, 1, 0, 0, 0))

        self.assertEqual(calverify.results["Number of events to process"], 15)
        self.assertEqual(calverify.results["Missing Attendee"], set((
            ("MISSING_ATTENDEE_ICS", self.uuid1, self.uuid2,),
            ("MISSING_ATTENDEE_ICS", self.uuid1, self.uuid3,),
        )))
        self.assertEqual(calverify.results["Mismatch Attendee"], set((
            ("MISMATCH_ATTENDEE_ICS", self.uuid1, self.uuid2,),
            ("MISMATCH_ATTENDEE_ICS", self.uuid1, self.uuid3,),
            ("MISMATCH2_ATTENDEE_ICS", self.uuid1, self.uuid2,),
            ("MISMATCH2_ATTENDEE_ICS", self.uuid1, self.uuid3,),
            ("MISMATCH3_ATTENDEE_ICS", self.uuid1, self.uuid3,),
        )))
        self.assertEqual(calverify.results["Missing Organizer"], set((
            ("MISSING_ORGANIZER_ICS", self.uuid2, self.uuid1,),
            ("MISSING_ORGANIZER_ICS", self.uuid3, self.uuid1,),
        )))
        self.assertEqual(calverify.results["Mismatch Organizer"], set((
            ("MISMATCH_ORGANIZER_ICS", self.uuid2, self.uuid1,),
            ("MISMATCH_ORGANIZER_ICS", self.uuid3, self.uuid1,),
            ("MISMATCH2_ORGANIZER_ICS", self.uuid3, self.uuid1,),
        )))

        self.assertTrue("Fix change event" not in calverify.results)
        self.assertTrue("Fix add event" not in calverify.results)
        self.assertTrue("Fix add inbox" not in calverify.results)
        self.assertTrue("Fix remove" not in calverify.results)

        sync_token_new1 = (yield (yield self.calendarUnderTest(self.uuid1)).syncToken())
        sync_token_new2 = (yield (yield self.calendarUnderTest(self.uuid2)).syncToken())
        sync_token_new3 = (yield (yield self.calendarUnderTest(self.uuid3)).syncToken())
        self.assertEqual(sync_token_old1, sync_token_new1)
        self.assertEqual(sync_token_old2, sync_token_new2)
        self.assertEqual(sync_token_old3, sync_token_new3)


    @inlineCallbacks
    def test_fixMismatch(self):
        """
        CalVerifyService.doScan with fix for mismatches. Make sure it detects
        and fixes as much as it can. Make sure sync-token is not changed.
        """

        sync_token_old1 = (yield (yield self.calendarUnderTest(self.uuid1)).syncToken())
        sync_token_old2 = (yield (yield self.calendarUnderTest(self.uuid2)).syncToken())
        sync_token_old3 = (yield (yield self.calendarUnderTest(self.uuid3)).syncToken())
        self.commit()

        options = {
            "ical":False,
            "badcua":False,
            "mismatch":True,
            "nobase64":False,
            "verbose":False,
            "details":False,
            "uid":"",
            "uuid":"",
        }
        output = StringIO()
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(False, True, True, start=PyCalendarDateTime(2000, 1, 1, 0, 0, 0))

        self.assertEqual(calverify.results["Number of events to process"], 15)
        self.assertEqual(calverify.results["Missing Attendee"], set((
            ("MISSING_ATTENDEE_ICS", self.uuid1, self.uuid2,),
            ("MISSING_ATTENDEE_ICS", self.uuid1, self.uuid3,),
        )))
        self.assertEqual(calverify.results["Mismatch Attendee"], set((
            ("MISMATCH_ATTENDEE_ICS", self.uuid1, self.uuid2,),
            ("MISMATCH_ATTENDEE_ICS", self.uuid1, self.uuid3,),
            ("MISMATCH2_ATTENDEE_ICS", self.uuid1, self.uuid2,),
            ("MISMATCH2_ATTENDEE_ICS", self.uuid1, self.uuid3,),
            ("MISMATCH3_ATTENDEE_ICS", self.uuid1, self.uuid3,),
        )))
        self.assertEqual(calverify.results["Missing Organizer"], set((
            ("MISSING_ORGANIZER_ICS", self.uuid2, self.uuid1,),
            ("MISSING_ORGANIZER_ICS", self.uuid3, self.uuid1,),
        )))
        self.assertEqual(calverify.results["Mismatch Organizer"], set((
            ("MISMATCH_ORGANIZER_ICS", self.uuid2, self.uuid1,),
            ("MISMATCH_ORGANIZER_ICS", self.uuid3, self.uuid1,),
            ("MISMATCH2_ORGANIZER_ICS", self.uuid3, self.uuid1,),
        )))
        
        self.assertEqual(calverify.results["Fix change event"], set((
            (self.uuid2, "calendar", "MISMATCH_ATTENDEE_ICS",),
            (self.uuid3, "calendar", "MISMATCH_ATTENDEE_ICS",),
            (self.uuid2, "calendar", "MISMATCH2_ATTENDEE_ICS",),
            (self.uuid3, "calendar2", "MISMATCH2_ATTENDEE_ICS",),
            (self.uuid3, "calendar", "MISMATCH3_ATTENDEE_ICS",),
            (self.uuid2, "calendar", "MISMATCH_ORGANIZER_ICS",),
            (self.uuid3, "calendar2", "MISMATCH_ORGANIZER_ICS",),
        )))
        
        self.assertEqual(calverify.results["Fix add event"], set((
            (self.uuid2, "calendar", "MISSING_ATTENDEE_ICS",),
            (self.uuid3, "calendar2", "MISSING_ATTENDEE_ICS",),
        )))
        
        self.assertEqual(calverify.results["Fix add inbox"], set((
            (self.uuid2, "MISSING_ATTENDEE_ICS",),
            (self.uuid3, "MISSING_ATTENDEE_ICS",),
            (self.uuid2, "MISMATCH_ATTENDEE_ICS",),
            (self.uuid3, "MISMATCH_ATTENDEE_ICS",),
            (self.uuid2, "MISMATCH2_ATTENDEE_ICS",),
            (self.uuid3, "MISMATCH2_ATTENDEE_ICS",),
            (self.uuid3, "MISMATCH3_ATTENDEE_ICS",),
            (self.uuid2, "MISMATCH_ORGANIZER_ICS",),
            (self.uuid3, "MISMATCH_ORGANIZER_ICS",),
        )))
        
        self.assertEqual(calverify.results["Fix remove"], set((
            (self.uuid2, "calendar", "missing_organizer.ics",),
            (self.uuid3, "calendar", "missing_organizer.ics",),
            (self.uuid3, "calendar", "mismatched2_organizer.ics",),
        )))
        obj = yield self.calendarObjectUnderTest(self.uuid2, "calendar", "missing_organizer.ics")
        self.assertEqual(obj, None)
        obj = yield self.calendarObjectUnderTest(self.uuid3, "calendar", "missing_organizer.ics")
        self.assertEqual(obj, None)
        obj = yield self.calendarObjectUnderTest(self.uuid3, "calendar", "mismatched2_organizer.ics")
        self.assertEqual(obj, None)

        sync_token_new1 = (yield (yield self.calendarUnderTest(self.uuid1)).syncToken())
        sync_token_new2 = (yield (yield self.calendarUnderTest(self.uuid2)).syncToken())
        sync_token_new3 = (yield (yield self.calendarUnderTest(self.uuid3)).syncToken())
        self.assertEqual(sync_token_old1, sync_token_new1)
        self.assertNotEqual(sync_token_old2, sync_token_new2)
        self.assertNotEqual(sync_token_old3, sync_token_new3)

        # Re-scan after changes to make sure there are no errors
        self.commit()
        calverify = CalVerifyService(self._sqlCalendarStore, options, output, reactor, config)
        yield calverify.doScan(False, True, False, start=PyCalendarDateTime(2000, 1, 1, 0, 0, 0))

        self.assertEqual(calverify.results["Number of events to process"], 14)
        self.assertTrue("Missing Attendee" not in calverify.results)
        self.assertTrue("Mismatch Attendee" not in calverify.results)
        self.assertTrue("Missing Organizer" not in calverify.results)
        self.assertTrue("Mismatch Organizer" not in calverify.results)
        self.assertTrue("Fix add event" not in calverify.results)
        self.assertTrue("Fix add inbox" not in calverify.results)
        self.assertTrue("Fix remove" not in calverify.results)
