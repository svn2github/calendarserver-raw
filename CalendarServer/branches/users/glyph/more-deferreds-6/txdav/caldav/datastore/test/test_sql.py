##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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
Tests for txdav.caldav.datastore.postgres, mostly based on
L{txdav.caldav.datastore.test.common}.
"""

import time

from txdav.caldav.datastore.test.common import CommonTests as CalendarCommonTests

from txdav.common.datastore.sql import ECALENDARTYPE
from txdav.common.datastore.test.util import buildStore, populateCalendarsFrom

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread
from twext.python.vcomponent import VComponent
from twext.web2.dav.element.rfc2518 import GETContentLanguage, ResourceType

from txdav.caldav.datastore.test.test_file import setUpCalendarStore
from txdav.caldav.datastore.util import _migrateCalendar, migrateHome
from txdav.base.propertystore.base import PropertyName



class CalendarSQLStorageTests(CalendarCommonTests, unittest.TestCase):
    """
    Calendar SQL storage tests.
    """

    @inlineCallbacks
    def setUp(self):
        super(CalendarSQLStorageTests, self).setUp()
        self._sqlCalendarStore = yield buildStore(self, self.notifierFactory)
        yield self.populate()


    @inlineCallbacks
    def populate(self):
        yield populateCalendarsFrom(self.requirements, self.storeUnderTest())
        self.notifierFactory.reset()


    def storeUnderTest(self):
        """
        Create and return a L{CalendarStore} for testing.
        """
        return self._sqlCalendarStore


    def assertCalendarsSimilar(self, a, b, bCalendarFilter=None):
        """
        Assert that two calendars have a similar structure (contain the same
        events).
        """
        def namesAndComponents(x, filter=lambda x:x.component()):
            return dict([(fromObj.name(), filter(fromObj))
                         for fromObj in x.calendarObjects()])
        if bCalendarFilter is not None:
            extra = [bCalendarFilter]
        else:
            extra = []
        self.assertEquals(namesAndComponents(a), namesAndComponents(b, *extra))


    def assertPropertiesSimilar(self, a, b, disregard=[]):
        """
        Assert that two objects with C{properties} methods have similar
        properties.
        
        @param disregard: a list of L{PropertyName} keys to discard from both
            input and output.
        """
        def sanitize(x):
            result = dict(x.properties().items())
            for key in disregard:
                result.pop(key, None)
            return result
        self.assertEquals(sanitize(a), sanitize(b))


    def fileTransaction(self):
        """
        Create a file-backed calendar transaction, for migration testing.
        """
        setUpCalendarStore(self)
        fileStore = self.calendarStore
        txn = fileStore.newTransaction()
        self.addCleanup(txn.commit)
        return txn

    @inlineCallbacks
    def test_attachmentPath(self):
        """
        L{ICalendarObject.createAttachmentWithName} will store an
        L{IAttachment} object that can be retrieved by
        L{ICalendarObject.attachmentWithName}.
        """
        yield self.createAttachmentTest(lambda x: x)
        attachmentRoot = (yield self.calendarObjectUnderTest())._txn._store.attachmentsPath
        attachmentPath = attachmentRoot.child("ho").child("me").child("home1")
        attachmentPath = attachmentPath.child(
            (yield self.calendarObjectUnderTest()).uid()).child(
                "new.attachment")
        self.assertTrue(attachmentPath.isfile())


    @inlineCallbacks
    def test_migrateCalendarFromFile(self):
        """
        C{_migrateCalendar()} can migrate a file-backed calendar to a database-
        backed calendar.
        """
        fromCalendar = yield (yield self.fileTransaction().calendarHomeWithUID(
            "home1")).calendarWithName("calendar_1")
        toHome = yield self.transactionUnderTest().calendarHomeWithUID(
            "new-home", create=True)
        toCalendar = yield toHome.calendarWithName("calendar")
        _migrateCalendar(fromCalendar, toCalendar, lambda x: x.component())
        self.assertCalendarsSimilar(fromCalendar, toCalendar)


    @inlineCallbacks
    def test_migrateHomeFromFile(self):
        """
        L{migrateHome} will migrate an L{ICalendarHome} provider from one
        backend to another; in this specific case, from the file-based backend
        to the SQL-based backend.
        """
        fromHome = yield self.fileTransaction().calendarHomeWithUID("home1")

        builtinProperties = [PropertyName.fromElement(ResourceType)]

        # Populate an arbitrary / unused dead properties so there's something
        # to verify against.

        key = PropertyName.fromElement(GETContentLanguage)
        fromHome.properties()[key] = GETContentLanguage("C")
        (yield fromHome.calendarWithName("calendar_1")).properties()[key] = (
            GETContentLanguage("pig-latin")
        )
        toHome = yield self.transactionUnderTest().calendarHomeWithUID(
            "new-home", create=True
        )
        migrateHome(fromHome, toHome, lambda x: x.component())
        self.assertEquals(set([c.name() for c in toHome.calendars()]),
                          set([k for k in self.requirements['home1'].keys()
                               if self.requirements['home1'][k] is not None]))
        for c in fromHome.calendars():
            self.assertPropertiesSimilar(
                c, (yield toHome.calendarWithName(c.name())),
                builtinProperties
            )
        self.assertPropertiesSimilar(fromHome, toHome, builtinProperties)


    def test_eachCalendarHome(self):
        """
        L{ICalendarStore.eachCalendarHome} is currently stubbed out by
        L{txdav.common.datastore.sql.CommonDataStore}.
        """
        return super(CalendarSQLStorageTests, self).test_eachCalendarHome()


    test_eachCalendarHome.todo = (
        "stubbed out, as migration only needs to go from file->sql currently")




    @inlineCallbacks
    def test_homeProvisioningConcurrency(self):
        """
        Test that two concurrent attempts to provision a calendar home do not cause a race-condition
        whereby the second commit results in a second INSERT that violates a unique constraint. Also verify
        that, whilst the two provisioning attempts are happening and doing various lock operations, that we
        do not block other reads of the table.
        """

        calendarStore1 = yield buildStore(self, self.notifierFactory)
        calendarStore2 = yield buildStore(self, self.notifierFactory)
        calendarStore3 = yield buildStore(self, self.notifierFactory)

        txn1 = calendarStore1.newTransaction()
        txn2 = calendarStore2.newTransaction()
        txn3 = calendarStore3.newTransaction()
        
        # Provision one home now - we will use this to later verify we can do reads of
        # existing data in the table
        home_uid2 = txn3.homeWithUID(ECALENDARTYPE, "uid2", create=True)
        self.assertNotEqual(home_uid2, None)
        txn3.commit()

        home_uid1_1 = txn1.homeWithUID(ECALENDARTYPE, "uid1", create=True)
        
        def _defer_home_uid1_2():
            home_uid1_2 = txn2.homeWithUID(ECALENDARTYPE, "uid1", create=True)
            txn2.commit()
            return home_uid1_2
        d1 = deferToThread(_defer_home_uid1_2)
        
        def _pause_home_uid1_1():
            time.sleep(1)
            txn1.commit()
        d2 = deferToThread(_pause_home_uid1_1)
        
        # Verify that we can still get to the existing home - i.e. the lock
        # on the table allows concurrent reads
        txn4 = calendarStore3.newTransaction()
        home_uid2 = txn4.homeWithUID(ECALENDARTYPE, "uid2", create=True)
        self.assertNotEqual(home_uid2, None)
        txn4.commit()
        
        # Now do the concurrent provision attempt
        yield d2
        home_uid1_2 = yield d1
        
        self.assertNotEqual(home_uid1_1, None)
        self.assertNotEqual(home_uid1_2, None)


    @inlineCallbacks
    def test_putConcurrency(self):
        """
        Test that two concurrent attempts to PUT different address book object resources to the
        same address book home does not cause a deadlock.
        """

        calendarStore1 = yield buildStore(self, self.notifierFactory)
        calendarStore2 = yield buildStore(self, self.notifierFactory)

        # Provision the home now
        txn = calendarStore1.newTransaction()
        home = txn.homeWithUID(ECALENDARTYPE, "uid1", create=True)
        self.assertNotEqual(home, None)
        txn.commit()

        txn1 = calendarStore1.newTransaction()
        txn2 = calendarStore2.newTransaction()

        home1 = txn1.homeWithUID(ECALENDARTYPE, "uid1", create=True)
        home2 = txn2.homeWithUID(ECALENDARTYPE, "uid1", create=True)
        
        adbk1 = yield home1.calendarWithName("calendar")
        adbk2 = yield home2.calendarWithName("calendar")
        
        def _defer1():
            adbk1.createObjectResourceWithName("1.ics", VComponent.fromString(
    "BEGIN:VCALENDAR\r\n"
      "VERSION:2.0\r\n"
      "PRODID:-//Apple Inc.//iCal 4.0.1//EN\r\n"
      "CALSCALE:GREGORIAN\r\n"
      "BEGIN:VTIMEZONE\r\n"
        "TZID:US/Pacific\r\n"
        "BEGIN:DAYLIGHT\r\n"
          "TZOFFSETFROM:-0800\r\n"
          "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
          "DTSTART:20070311T020000\r\n"
          "TZNAME:PDT\r\n"
          "TZOFFSETTO:-0700\r\n"
        "END:DAYLIGHT\r\n"
        "BEGIN:STANDARD\r\n"
          "TZOFFSETFROM:-0700\r\n"
          "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
          "DTSTART:20071104T020000\r\n"
          "TZNAME:PST\r\n"
          "TZOFFSETTO:-0800\r\n"
        "END:STANDARD\r\n"
      "END:VTIMEZONE\r\n"
      "BEGIN:VEVENT\r\n"
        "CREATED:20100203T013849Z\r\n"
        "UID:uid1\r\n"
        "DTEND;TZID=US/Pacific:20100207T173000\r\n"
        "TRANSP:OPAQUE\r\n"
        "SUMMARY:New Event\r\n"
        "DTSTART;TZID=US/Pacific:20100207T170000\r\n"
        "DTSTAMP:20100203T013909Z\r\n"
        "SEQUENCE:3\r\n"
        "BEGIN:VALARM\r\n"
          "X-WR-ALARMUID:1377CCC7-F85C-4610-8583-9513D4B364E1\r\n"
          "TRIGGER:-PT20M\r\n"
          "ATTACH;VALUE=URI:Basso\r\n"
          "ACTION:AUDIO\r\n"
        "END:VALARM\r\n"
      "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
            ))
            txn1.commit()
        d1 = deferToThread(_defer1)
            
        def _defer2():
            adbk2.createObjectResourceWithName("2.ics", VComponent.fromString(
    "BEGIN:VCALENDAR\r\n"
      "VERSION:2.0\r\n"
      "PRODID:-//Apple Inc.//iCal 4.0.1//EN\r\n"
      "CALSCALE:GREGORIAN\r\n"
      "BEGIN:VTIMEZONE\r\n"
        "TZID:US/Pacific\r\n"
        "BEGIN:DAYLIGHT\r\n"
          "TZOFFSETFROM:-0800\r\n"
          "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\n"
          "DTSTART:20070311T020000\r\n"
          "TZNAME:PDT\r\n"
          "TZOFFSETTO:-0700\r\n"
        "END:DAYLIGHT\r\n"
        "BEGIN:STANDARD\r\n"
          "TZOFFSETFROM:-0700\r\n"
          "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\n"
          "DTSTART:20071104T020000\r\n"
          "TZNAME:PST\r\n"
          "TZOFFSETTO:-0800\r\n"
        "END:STANDARD\r\n"
      "END:VTIMEZONE\r\n"
      "BEGIN:VEVENT\r\n"
        "CREATED:20100203T013849Z\r\n"
        "UID:uid2\r\n"
        "DTEND;TZID=US/Pacific:20100207T173000\r\n"
        "TRANSP:OPAQUE\r\n"
        "SUMMARY:New Event\r\n"
        "DTSTART;TZID=US/Pacific:20100207T170000\r\n"
        "DTSTAMP:20100203T013909Z\r\n"
        "SEQUENCE:3\r\n"
        "BEGIN:VALARM\r\n"
          "X-WR-ALARMUID:1377CCC7-F85C-4610-8583-9513D4B364E1\r\n"
          "TRIGGER:-PT20M\r\n"
          "ATTACH;VALUE=URI:Basso\r\n"
          "ACTION:AUDIO\r\n"
        "END:VALARM\r\n"
      "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
            ))
            txn2.commit()
        d2 = deferToThread(_defer2)

        yield d1
        yield d2
