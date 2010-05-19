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
File calendar store tests.
"""

from twext.python.filepath import CachingFilePath as FilePath
from twisted.trial import unittest

from twext.python.vcomponent import VComponent

from twext.web2.dav import davxml

from txdav.idav import IPropertyStore
from txdav.propertystore.base import PropertyName

from txcaldav.icalendarstore import CalendarNameNotAllowedError
from txcaldav.icalendarstore import CalendarObjectNameNotAllowedError
from txcaldav.icalendarstore import CalendarAlreadyExistsError
from txcaldav.icalendarstore import CalendarObjectNameAlreadyExistsError
from txcaldav.icalendarstore import CalendarObjectUIDAlreadyExistsError
from txcaldav.icalendarstore import NoSuchCalendarError
from txcaldav.icalendarstore import NoSuchCalendarObjectError
from txcaldav.icalendarstore import InvalidCalendarComponentError

from txcaldav.calendarstore.file import CalendarStore, CalendarHome
from txcaldav.calendarstore.file import Calendar, CalendarObject

from txcaldav.calendarstore.test.test_common import CommonTests

storePath = FilePath(__file__).parent().child("calendar_store")

home1_calendarNames = (
    "calendar_1",
    "calendar_2",
    "calendar_empty",
)

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

event1modified_text = event4_text.replace(
    "\r\nUID:uid4\r\n",
    "\r\nUID:uid1\r\n"
)

event4notCalDAV_text = (
    "BEGIN:VCALENDAR\r\n"
      "VERSION:2.0\r\n"
      "PRODID:-//Apple Inc.//iCal 4.0.1//EN\r\n"
      "CALSCALE:GREGORIAN\r\n"
      "BEGIN:VEVENT\r\n"
        "CREATED:20100203T013849Z\r\n"
        "UID:4\r\n"
        "DTEND;TZID=US/Pacific:20100207T173000\r\n" # TZID without VTIMEZONE
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



def _todo(f, why):
    f.todo = why
    return f



featureUnimplemented = lambda f: _todo(f, "Feature unimplemented")
testUnimplemented = lambda f: _todo(f, "Test unimplemented")
todo = lambda why: lambda f: _todo(f, why)


class PropertiesTestMixin(object):

    def test_properties(self):
        properties = self.home1.properties()

        self.failUnless(
            IPropertyStore.providedBy(properties),
            properties
        )



def setUpCalendarStore(test):
    test.root = FilePath(test.mktemp())
    test.root.createDirectory()

    calendarPath = test.root.child("store")
    storePath.copyTo(calendarPath)


    test.calendarStore = CalendarStore(calendarPath)
    test.txn = test.calendarStore.newTransaction()
    assert test.calendarStore is not None, "No calendar store?"



def setUpHome1(test):
    setUpCalendarStore(test)
    test.home1 = test.txn.calendarHomeWithUID("home1")
    assert test.home1 is not None, "No calendar home?"



def setUpCalendar1(test):
    setUpHome1(test)
    test.calendar1 = test.home1.calendarWithName("calendar_1")
    assert test.calendar1 is not None, "No calendar?"



class CalendarStoreTest(unittest.TestCase):
    """
    Test cases for L{CalendarStore}.
    """

    def setUp(self):
        setUpCalendarStore(self)


    def test_init(self):
        """
        Ivars are correctly initialized.
        """
        self.failUnless(
            isinstance(self.calendarStore._path, FilePath),
            self.calendarStore._path
        )


    def test_calendarHomeWithUID_exists(self):
        """
        Find an existing calendar home by UID.
        """
        calendarHome = self.calendarStore.newTransaction().calendarHomeWithUID("home1")

        self.failUnless(isinstance(calendarHome, CalendarHome))


    def test_calendarHomeWithUID_absent(self):
        """
        Missing calendar home.
        """
        self.assertEquals(
            self.calendarStore.newTransaction().calendarHomeWithUID("xyzzy"),
            None
        )


    def test_calendarHomeWithUID_create(self):
        """
        Create missing calendar home.
        """
        txn = self.calendarStore.newTransaction()
        calendarHome = txn.calendarHomeWithUID(
            "xyzzy",
            create=True
        )

        self.failUnless(isinstance(calendarHome, CalendarHome))
        self.failIf(calendarHome._path.isdir())
        txn.commit()
        self.failUnless(calendarHome._path.isdir())


    def test_calendarHomeWithUID_create_exists(self):
        """
        Create missing calendar home.
        """
        calendarHome = self.calendarStore.newTransaction().calendarHomeWithUID("home1")

        self.failUnless(isinstance(calendarHome, CalendarHome))


    def test_calendarHomeWithUID_dot(self):
        """
        Filenames starting with "." are reserved by this
        implementation, so no UIDs may start with ".".
        """
        self.assertEquals(
            self.calendarStore.newTransaction().calendarHomeWithUID("xyzzy"),
            None
        )



class CalendarHomeTest(unittest.TestCase, PropertiesTestMixin):

    def setUp(self):
        setUpHome1(self)


    def test_init(self):
        """
        Ivars are correctly initialized.
        """
        self.failUnless(
            isinstance(self.home1._path, FilePath),
            self.home1._path
        )
        self.assertEquals(
            self.home1._calendarStore,
            self.calendarStore
        )


    def test_uid(self):
        """
        UID is correct.
        """
        self.assertEquals(self.home1.uid(), "home1")


    def test_calendars(self):
        """
        Find all of the calendars.
        """
        # Add a dot directory to make sure we don't find it
        self.home1._path.child(".foo").createDirectory()

        calendars = tuple(self.home1.calendars())

        for calendar in calendars:
            self.failUnless(isinstance(calendar, Calendar), calendar)

        self.assertEquals(
            tuple(c.name() for c in calendars),
            home1_calendarNames
        )


    def test_calendarWithName_exists(self):
        """
        Find existing calendar by name.
        """
        for name in home1_calendarNames:
            calendar = self.home1.calendarWithName(name)
            self.failUnless(isinstance(calendar, Calendar), calendar)
            self.assertEquals(calendar.name(), name)


    def test_calendarWithName_absent(self):
        """
        Missing calendar.
        """
        self.assertEquals(self.home1.calendarWithName("xyzzy"), None)


    def test_calendarWithName_dot(self):
        """
        Filenames starting with "." are reserved by this
        implementation, so no calendar names may start with ".".
        """
        name = ".foo"
        self.home1._path.child(name).createDirectory()
        self.assertEquals(self.home1.calendarWithName(name), None)


    def test_createCalendarWithName_absent(self):
        """
        Create a new calendar.
        """
        name = "new"
        assert self.home1.calendarWithName(name) is None
        self.home1.createCalendarWithName(name)
        self.failUnless(self.home1.calendarWithName(name) is not None)
        def checkProperties():
            calendarProperties = self.home1.calendarWithName(name).properties()
            self.assertEquals(
                calendarProperties[
                    PropertyName.fromString(davxml.ResourceType.sname())
                ],
                davxml.ResourceType.calendar) #@UndefinedVariable
        checkProperties()
        self.txn.commit()
        self.home1 = self.calendarStore.newTransaction().calendarHomeWithUID(
            "home1")
        # Sanity check: are the properties actually persisted?
        # FIXME: no independent testing of this right now
        checkProperties()


    def test_createCalendarWithName_exists(self):
        """
        Attempt to create an existing calendar should raise.
        """
        for name in home1_calendarNames:
            self.assertRaises(
                CalendarAlreadyExistsError,
                self.home1.createCalendarWithName, name
            )


    def test_createCalendarWithName_dot(self):
        """
        Filenames starting with "." are reserved by this
        implementation, so no calendar names may start with ".".
        """
        self.assertRaises(
            CalendarNameNotAllowedError,
            self.home1.createCalendarWithName, ".foo"
        )


    def test_removeCalendarWithName_exists(self):
        """
        Remove an existing calendar.
        """
        for name in home1_calendarNames:
            self.assertNotIdentical(self.home1.calendarWithName(name),
                                    None)
            self.home1.removeCalendarWithName(name)
            self.assertEquals(self.home1.calendarWithName(name), None)


    def test_removeCalendarWithName_absent(self):
        """
        Attempt to remove an non-existing calendar should raise.
        """
        self.assertRaises(
            NoSuchCalendarError,
            self.home1.removeCalendarWithName, "xyzzy"
        )


    def test_removeCalendarWithName_dot(self):
        """
        Filenames starting with "." are reserved by this
        implementation, so no calendar names may start with ".".
        """
        name = ".foo"
        self.home1._path.child(name).createDirectory()
        self.assertRaises(
            NoSuchCalendarError,
            self.home1.removeCalendarWithName, name
        )



class CalendarTest(unittest.TestCase, PropertiesTestMixin):

    def setUp(self):
        setUpCalendar1(self)


    def test_init(self):
        """
        L{Calendar.__init__} sets private attributes to reflect its constructor
        arguments.
        """
        self.failUnless(
            isinstance(self.calendar1._path, FilePath),
            self.calendar1
        )
        self.failUnless(
            isinstance(self.calendar1._calendarHome, CalendarHome),
            self.calendar1._calendarHome
        )


    def test_name(self):
        """
        L{Calendar.name} reflects the name of the calendar.
        """
        self.assertEquals(self.calendar1.name(), "calendar_1")


    def test_ownerCalendarHome(self):
        """
        Owner is correct.
        """
        # Note that here we know that home1 owns calendar1
        self.assertEquals(
            self.calendar1.ownerCalendarHome().uid(),
            self.home1.uid()
        )


    def test_calendarObjects(self):
        """
        L{Calendar.calendarObjects} will enumerate the calendar objects present
        in the filesystem, in name order, but skip those with hidden names.
        """
        # Add a dot file to make sure we don't find it
        self.home1._path.child(".foo").createDirectory()

        calendarObjects = tuple(self.calendar1.calendarObjects())

        for calendarObject in calendarObjects:
            self.failUnless(
                isinstance(calendarObject, CalendarObject),
                calendarObject
            )

        self.assertEquals(
            tuple(o.name() for o in calendarObjects),
            calendar1_objectNames
        )


    def test_calendarObjectsWithRemovedObject(self):
        """
        L{Calendar.calendarObjects will skip those objects which have been
        removed by L{Calendar.removeCalendarObjectWithName} in the same
        transaction, even if it has not yet been committed.
        """
        self.calendar1.removeCalendarObjectWithName("2.ics")
        calendarObjects = list(self.calendar1.calendarObjects())
        self.assertEquals(set(o.name() for o in calendarObjects),
                          set(calendar1_objectNames) - set(["2.ics"]))


    def test_calendarObjectWithName_exists(self):
        """
        Find existing calendar object by name.
        """
        for name in calendar1_objectNames:
            calendarObject = self.calendar1.calendarObjectWithName(name)
            self.failUnless(
                isinstance(calendarObject, CalendarObject),
                calendarObject
            )
            self.assertEquals(calendarObject.name(), name)


    def test_calendarObjectWithName_absent(self):
        """
        Missing calendar object.
        """
        self.assertEquals(self.calendar1.calendarObjectWithName("xyzzy"), None)


    def test_calendarObjectWithName_dot(self):
        """
        Filenames starting with "." are reserved by this
        implementation, so no calendar object names may start with
        ".".
        """
        name = ".foo.ics"
        self.home1._path.child(name).touch()
        self.assertEquals(self.calendar1.calendarObjectWithName(name), None)


    @featureUnimplemented
    def test_calendarObjectWithUID_exists(self):
        """
        Find existing calendar object by name.
        """
        calendarObject = self.calendar1.calendarObjectWithUID("1")
        self.failUnless(
            isinstance(calendarObject, CalendarObject),
            calendarObject
        )
        self.assertEquals(
            calendarObject.component(),
            self.calendar1.calendarObjectWithName("1.ics").component()
        )


    def test_calendarObjectWithUID_absent(self):
        """
        Missing calendar object.
        """
        self.assertEquals(self.calendar1.calendarObjectWithUID("xyzzy"), None)


    def test_createCalendarObjectWithName_absent(self):
        """
        Create a new calendar object.
        """
        name = "4.ics"
        self.assertIdentical(self.calendar1.calendarObjectWithName(name), None)
        component = VComponent.fromString(event4_text)
        self.calendar1.createCalendarObjectWithName(name, component)

        calendarObject = self.calendar1.calendarObjectWithName(name)
        self.assertEquals(calendarObject.component(), component)


    def test_createCalendarObjectWithName_exists(self):
        """
        Attempt to create an existing calendar object should raise.
        """
        self.assertRaises(
            CalendarObjectNameAlreadyExistsError,
            self.calendar1.createCalendarObjectWithName,
            "1.ics", VComponent.fromString(event4_text)
        )


    def test_createCalendarObjectWithName_dot(self):
        """
        Filenames starting with "." are reserved by this
        implementation, so no calendar object names may start with
        ".".
        """
        self.assertRaises(
            CalendarObjectNameNotAllowedError,
            self.calendar1.createCalendarObjectWithName,
            ".foo", VComponent.fromString(event4_text)
        )


    @featureUnimplemented
    def test_createCalendarObjectWithName_uidconflict(self):
        """
        Attempt to create a calendar object with a conflicting UID
        should raise.
        """
        name = "foo.ics"
        assert self.calendar1.calendarObjectWithName(name) is None
        component = VComponent.fromString(event1modified_text)
        self.assertRaises(
            CalendarObjectUIDAlreadyExistsError,
            self.calendar1.createCalendarObjectWithName,
            name, component
        )


    def test_createCalendarObjectWithName_invalid(self):
        """
        Attempt to create a calendar object with a invalid iCalendar text
        should raise.
        """
        self.assertRaises(
            InvalidCalendarComponentError,
            self.calendar1.createCalendarObjectWithName,
            "new", VComponent.fromString(event4notCalDAV_text)
        )


    def test_removeCalendarObjectWithName_exists(self):
        """
        Remove an existing calendar object.
        """
        for name in calendar1_objectNames:
            self.assertNotIdentical(
                self.calendar1.calendarObjectWithName(name), None
            )
            self.calendar1.removeCalendarObjectWithName(name)
            self.assertIdentical(
                self.calendar1.calendarObjectWithName(name), None
            )


    def test_removeCalendarObjectWithName_absent(self):
        """
        Attempt to remove an non-existing calendar object should raise.
        """
        self.assertRaises(
            NoSuchCalendarObjectError,
            self.calendar1.removeCalendarObjectWithName, "xyzzy"
        )


    def test_removeCalendarObject_delayedEffect(self):
        """
        Removing a calendar object should not immediately remove the underlying
        file; it should only be removed upon commit() of the transaction.
        """
        self.calendar1.removeCalendarObjectWithName("2.ics")
        self.failUnless(self.calendar1._path.child("2.ics").exists())
        self.txn.commit()
        self.failIf(self.calendar1._path.child("2.ics").exists())


    def test_removeCalendarObjectWithName_dot(self):
        """
        Filenames starting with "." are reserved by this
        implementation, so no calendar object names may start with
        ".".
        """
        name = ".foo"
        self.calendar1._path.child(name).touch()
        self.assertRaises(
            NoSuchCalendarObjectError,
            self.calendar1.removeCalendarObjectWithName, name
        )


    def test_removeCalendarObjectWithUID_exists(self):
        """
        Remove an existing calendar object.
        """
        for name in calendar1_objectNames:
            uid = (u'uid' + name.rstrip(".ics"))
            self.assertNotIdentical(self.calendar1.calendarObjectWithUID(uid),
                                    None)
            self.calendar1.removeCalendarObjectWithUID(uid)
            self.assertEquals(
                self.calendar1.calendarObjectWithUID(uid),
                None
            )
            self.assertEquals(
                self.calendar1.calendarObjectWithName(name),
                None
            )


    def _refresh(self):
        """
        Re-read the (committed) home1 and calendar1 objects in a new
        transaction.
        """
        self.txn = self.calendarStore.newTransaction()
        self.home1 = self.txn.calendarHomeWithUID("home1")
        self.calendar1 = self.home1.calendarWithName("calendar_1")


    def test_undoCreateCalendarObject(self):
        """
        If a calendar object is created as part of a transaction, it will be
        removed if that transaction has to be aborted.
        """
        # Make sure that the calendar home is actually committed; rolling back
        # calendar home creation will remove the whole directory.
        self.txn.commit()
        self._refresh()
        self.calendar1.createCalendarObjectWithName(
            "sample.ics",
            VComponent.fromString(event4_text)
        )
        self._refresh()
        self.assertIdentical(
            self.calendar1.calendarObjectWithName("sample.ics"),
            None
        )


    def doThenUndo(self):
        """
        Commit the current transaction, but add an operation that will cause it
        to fail at the end.  Finally, refresh all attributes with a new
        transaction so that further oparations can be performed in a valid
        context.
        """
        def fail():
            raise RuntimeError("oops")
        self.txn.addOperation(fail)
        self.assertRaises(RuntimeError, self.txn.commit)
        self._refresh()


    def test_undoModifyCalendarObject(self):
        """
        If an existing calendar object is modified as part of a transaction, it
        should be restored to its previous status if the transaction aborts.
        """
        originalComponent = self.calendar1.calendarObjectWithName(
            "1.ics").component()
        self.calendar1.calendarObjectWithName("1.ics").setComponent(
            VComponent.fromString(event1modified_text)
        )
        # Sanity check.
        self.assertEquals(
            self.calendar1.calendarObjectWithName("1.ics").component(),
            VComponent.fromString(event1modified_text)
        )
        self.doThenUndo()
        self.assertEquals(
            self.calendar1.calendarObjectWithName("1.ics").component(),
            originalComponent
        )


    def test_modifyCalendarObjectCaches(self):
        """
        Modifying a calendar object should cache the modified component in
        memory, to avoid unnecessary parsing round-trips.
        """
        modifiedComponent = VComponent.fromString(event1modified_text)
        self.calendar1.calendarObjectWithName("1.ics").setComponent(
            modifiedComponent
        )
        self.assertIdentical(
            modifiedComponent,
            self.calendar1.calendarObjectWithName("1.ics").component()
        )


    @featureUnimplemented
    def test_removeCalendarObjectWithUID_absent(self):
        """
        Attempt to remove an non-existing calendar object should raise.
        """
        self.assertRaises(
            NoSuchCalendarObjectError,
            self.calendar1.removeCalendarObjectWithUID, "xyzzy"
        )


    @testUnimplemented
    def test_syncToken(self):
        """
        Sync token is correct.
        """
        raise NotImplementedError()


    @testUnimplemented
    def test_calendarObjectsInTimeRange(self):
        """
        Find calendar objects occuring in a given time range.
        """
        raise NotImplementedError()


    @testUnimplemented
    def test_calendarObjectsSinceToken(self):
        """
        Find calendar objects that have been modified since a given
        sync token.
        """
        raise NotImplementedError()



class CalendarObjectTest(unittest.TestCase, PropertiesTestMixin):
    def setUp(self):
        setUpCalendar1(self)
        self.object1 = self.calendar1.calendarObjectWithName("1.ics")


    def test_init(self):
        """
        Ivars are correctly initialized.
        """
        self.failUnless(
            isinstance(self.object1._path, FilePath),
            self.object1._path
        )
        self.failUnless(
            isinstance(self.object1.calendar, Calendar),
            self.object1.calendar
        )


    def test_name(self):
        """
        Name is correct.
        """
        self.assertEquals(self.object1.name(), "1.ics")


    def test_setComponent(self):
        """
        L{CalendarObject.setComponent} changes the result of
        L{CalendarObject.component} within the same transaction.
        """
        component = VComponent.fromString(event1modified_text)

        calendarObject = self.calendar1.calendarObjectWithName("1.ics")
        oldComponent = calendarObject.component()
        self.assertNotEqual(component, oldComponent)
        calendarObject.setComponent(component)
        self.assertEquals(calendarObject.component(), component)

        # Also check a new instance
        calendarObject = self.calendar1.calendarObjectWithName("1.ics")
        self.assertEquals(calendarObject.component(), component)


    def test_setComponent_uidchanged(self):
        component = VComponent.fromString(event4_text)

        calendarObject = self.calendar1.calendarObjectWithName("1.ics")
        self.assertRaises(
            InvalidCalendarComponentError,
            calendarObject.setComponent, component
        )


    def test_setComponent_invalid(self):
        calendarObject = self.calendar1.calendarObjectWithName("1.ics")
        self.assertRaises(
            InvalidCalendarComponentError,
            calendarObject.setComponent,
            VComponent.fromString(event4notCalDAV_text)
        )


    def test_component(self):
        """
        Component is correct.
        """
        component = self.object1.component()

        self.failUnless(
            isinstance(component, VComponent),
            component
        )

        self.assertEquals(component.name(), "VCALENDAR")
        self.assertEquals(component.mainType(), "VEVENT")
        self.assertEquals(component.resourceUID(), "uid1")


    def text_iCalendarText(self):
        """
        iCalendar text is correct.
        """
        text = self.object1.iCalendarText()

        self.failUnless(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.failUnless("\r\nUID:uid-1\r\n" in text)
        self.failUnless(text.endswith("\r\nEND:VCALENDAR\r\n"))


    def test_uid(self):
        """
        UID is correct.
        """
        self.assertEquals(self.object1.uid(), "uid1")


    def test_componentType(self):
        """
        Component type is correct.
        """
        self.assertEquals(self.object1.componentType(), "VEVENT")


    def test_organizer(self):
        """
        Organizer is correct.
        """
        self.assertEquals(
            self.object1.organizer(),
            "mailto:wsanchez@apple.com"
        )



class FileStorageTests(unittest.TestCase, CommonTests):
    """
    File storage tests.
    """

    def storeUnderTest(self):
        """
        Create and return a L{CalendarStore} for testing.
        """
        setUpCalendarStore(self)
        return self.calendarStore