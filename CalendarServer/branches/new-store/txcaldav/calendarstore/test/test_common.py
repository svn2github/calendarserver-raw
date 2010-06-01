# -*- test-case-name: txcaldav.calendarstore.test.test_file -*-
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
Tests for common calendar store API functions.
"""

from txcaldav.icalendarstore import ICalendarStore, ICalendarStoreTransaction, \
    ICalendarObject, ICalendarHome, ICalendar, InvalidCalendarComponentError
from twext.python.filepath import CachingFilePath as FilePath
from zope.interface.verify import verifyObject
from zope.interface.exceptions import BrokenMethodImplementation
from txdav.propertystore.base import PropertyName
from twext.web2.dav import davxml
from twext.python.vcomponent import VComponent


storePath = FilePath(__file__).parent().child("calendar_store")

homeRoot = storePath.child("ho").child("me").child("home1")

cal1Root = homeRoot.child("calendar_1")

calendar1_objectNames = (
    "1.ics",
    "2.ics",
    "3.ics",
)


event4_text = (
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
        "UID:uid4\r\n"
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
)

class CommonTests(object):
    """
    Tests for common functionality of interfaces defined in
    L{txcaldav.icalendarstore}.
    """

    requirements = {
        "home1": {
            "calendar_1": {
                "1.ics": cal1Root.child("1.ics").getContent(),
                "2.ics": cal1Root.child("2.ics").getContent(),
                "3.ics": cal1Root.child("3.ics").getContent()
            },
            "calendar_empty": {},
            "not_a_calendar": None
        },
        "not_a_home": None
    }

    def storeUnderTest(self):
        """
        Subclasses must override this to return an L{ICalendarStore} provider
        which adheres to the structure detailed by L{CommonTests.requirements}.
        This attribute is a dict of dict of dicts; the outermost layer
        representing UIDs mapping to calendar homes, then calendar names mapping
        to calendar collections, and finally calendar object names mapping to
        calendar object text.
        """
        raise NotImplementedError()


    lastTransaction = None
    savedStore = None

    def transactionUnderTest(self):
        """
        Create a transaction from C{storeUnderTest} and save it as
        C[lastTransaction}.  Also makes sure to use the same store, saving the
        value from C{storeUnderTest}.
        """
        if self.lastTransaction is not None:
            return self.lastTransaction
        if self.savedStore is None:
            self.savedStore = self.storeUnderTest()
        txn = self.lastTransaction = self.savedStore.newTransaction()
        return txn


    def commit(self):
        """
        Commit the last transaction created from C{transactionUnderTest}, and
        clear it.
        """
        self.lastTransaction.commit()
        self.lastTransaction = None


    def abort(self):
        """
        Abort the last transaction created from C[transactionUnderTest}, and
        clear it.
        """
        self.lastTransaction.abort()
        self.lastTransaction = None


    def homeUnderTest(self):
        """
        Get the calendar home detailed by C{requirements['home1']}.
        """
        return self.transactionUnderTest().calendarHomeWithUID(
            "home1")


    def calendarUnderTest(self):
        """
        Get the calendar detailed by C{requirements['home1']['calendar_1']}.
        """
        return self.homeUnderTest().calendarWithName("calendar_1")


    def calendarObjectUnderTest(self):
        """
        Get the calendar detailed by
        C{requirements['home1']['calendar_1']['1.ics']}.
        """
        return self.calendarUnderTest().calendarObjectWithName("1.ics")


    def assertProvides(self, interface, provider):
        """
        Verify that C{provider} properly provides C{interface}

        @type interface: L{zope.interface.Interface}
        @type provider: C{provider}
        """
        try:
            verifyObject(interface, provider)
        except BrokenMethodImplementation, e:
            self.fail(e)


    def test_calendarStoreProvides(self):
        """
        The calendar store provides L{ICalendarStore} and its required
        attributes.
        """
        calendarStore = self.storeUnderTest()
        self.assertProvides(ICalendarStore, calendarStore)


    def test_transactionProvides(self):
        """
        The transactions generated by the calendar store provide
        L{ICalendarStoreTransaction} and its required attributes.
        """
        self.assertProvides(ICalendarStoreTransaction,
                            self.storeUnderTest().newTransaction())


    def test_homeProvides(self):
        """
        The calendar homes generated by the calendar store provide
        L{ICalendarHome} and its required attributes.
        """
        self.assertProvides(ICalendarHome, self.homeUnderTest())


    def test_calendarProvides(self):
        """
        The calendars generated by the calendar store provide L{ICalendar} and
        its required attributes.
        """
        self.assertProvides(ICalendar, self.calendarUnderTest())


    def test_calendarObjectProvides(self):
        """
        The calendar objects generated by the calendar store provide
        L{ICalendarObject} and its required attributes.
        """
        self.assertProvides(ICalendarObject, self.calendarObjectUnderTest())


    def test_calendarHomeWithUID_exists(self):
        """
        Finding an existing calendar home by UID results in an object that
        provides L{ICalendarHome} and has a C{uid()} method that returns the
        same value that was passed in.
        """
        calendarHome = (self.storeUnderTest().newTransaction()
                        .calendarHomeWithUID("home1"))

        self.assertEquals(calendarHome.uid(), "home1")
        self.assertProvides(ICalendarHome, calendarHome)


    def test_calendarHomeWithUID_absent(self):
        """
        L{ICalendarStoreTransaction.calendarHomeWithUID} should return C{None}
        when asked for a non-existent calendar home.
        """
        self.assertEquals(
            self.storeUnderTest().newTransaction()
            .calendarHomeWithUID("xyzzy"),
            None
        )


    def test_createCalendarWithName_absent(self):
        """
        L{ICalendarHome.createCalendarWithName} creates a new L{ICalendar} that
        can be retrieved with L{ICalendarHome.calendarWithName}.
        """
        home = self.homeUnderTest()
        name = "new"
        self.assertIdentical(home.calendarWithName(name), None)
        home.createCalendarWithName(name)
        self.assertNotIdentical(home.calendarWithName(name), None)
        def checkProperties():
            calendarProperties = home.calendarWithName(name).properties()
            self.assertEquals(
                calendarProperties[
                    PropertyName.fromString(davxml.ResourceType.sname())
                ],
                davxml.ResourceType.calendar) #@UndefinedVariable
        checkProperties()
        self.commit()

        # Make sure it's available in a new transaction; i.e. test the commit.
        home = self.homeUnderTest()
        self.assertNotIdentical(home.calendarWithName(name), None)
        home = self.calendarStore.newTransaction().calendarHomeWithUID(
            "home1")
        # Sanity check: are the properties actually persisted?
        # FIXME: no independent testing of this right now
        checkProperties()


    def test_calendarObjects(self):
        """
        L{ICalendar.calendarObjects} will enumerate the calendar objects present
        in the filesystem, in name order, but skip those with hidden names.
        """
        calendar1 = self.calendarUnderTest()
        calendarObjects = tuple(calendar1.calendarObjects())

        for calendarObject in calendarObjects:
            self.assertProvides(ICalendarObject, calendarObject)

        self.assertEquals(
            tuple(o.name() for o in calendarObjects),
            calendar1_objectNames
        )


    def test_calendarObjectsWithRemovedObject(self):
        """
        L{ICalendar.calendarObjects} will skip those objects which have been
        removed by L{Calendar.removeCalendarObjectWithName} in the same
        transaction, even if it has not yet been committed.
        """
        calendar1 = self.calendarUnderTest()
        calendar1.removeCalendarObjectWithName("2.ics")
        calendarObjects = list(calendar1.calendarObjects())
        self.assertEquals(set(o.name() for o in calendarObjects),
                          set(calendar1_objectNames) - set(["2.ics"]))


    def test_ownerCalendarHome(self):
        """
        L{ICalendar.ownerCalendarHome} should match the home UID.
        """
        self.assertEquals(
            self.calendarUnderTest().ownerCalendarHome().uid(),
            self.homeUnderTest().uid()
        )


    def test_calendarObjectWithName_absent(self):
        """
        L{ICalendar.calendarObjectWithName} returns C{None} for calendars which
        don't exist.
        """
        calendar1 = self.calendarUnderTest()
        self.assertEquals(calendar1.calendarObjectWithName("xyzzy"), None)


    def test_name(self):
        """
        L{Calendar.name} reflects the name of the calendar.
        """
        self.assertEquals(self.calendarUnderTest().name(), "calendar_1")


    def test_calendarObjectWithUID_absent(self):
        """
        L{ICalendar.calendarObjectWithUID} returns C{None} for calendars which
        don't exist.
        """
        calendar1 = self.calendarUnderTest()
        self.assertEquals(calendar1.calendarObjectWithUID("xyzzy"), None)


    def test_createCalendarObjectWithName_absent(self):
        """
        L{ICalendar.createCalendarObjectWithName} will create a new
        L{ICalendarObject}.
        """
        calendar1 = self.calendarUnderTest()
        name = "4.ics"
        self.assertIdentical(calendar1.calendarObjectWithName(name), None)
        component = VComponent.fromString(event4_text)
        calendar1.createCalendarObjectWithName(name, component)

        calendarObject = calendar1.calendarObjectWithName(name)
        self.assertEquals(calendarObject.component(), component)


    def test_setComponent_uidchanged(self):
        """
        L{ICalendarObject.setComponent} raises L{InvalidCalendarComponentError}
        when given a L{VComponent} whose UID does not match its existing UID.
        """
        calendar1 = self.calendarUnderTest()
        component = VComponent.fromString(event4_text)
        calendarObject = calendar1.calendarObjectWithName("1.ics")
        self.assertRaises(
            InvalidCalendarComponentError,
            calendarObject.setComponent, component
        )


