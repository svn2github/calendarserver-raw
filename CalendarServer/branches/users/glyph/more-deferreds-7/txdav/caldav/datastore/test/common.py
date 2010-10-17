# -*- test-case-name: txdav.caldav.datastore -*-
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

from twisted.internet.defer import Deferred, inlineCallbacks, returnValue,\
    maybeDeferred
from twisted.internet.protocol import Protocol

from txdav.idav import IPropertyStore, IDataStore, AlreadyFinishedError
from txdav.base.propertystore.base import PropertyName

from txdav.common.icommondatastore import HomeChildNameAlreadyExistsError, \
    ICommonTransaction
from txdav.common.icommondatastore import InvalidObjectResourceError
from txdav.common.icommondatastore import NoSuchHomeChildError
from txdav.common.icommondatastore import NoSuchObjectResourceError
from txdav.common.icommondatastore import ObjectResourceNameAlreadyExistsError
from txdav.common.inotifications import INotificationObject
from txdav.common.datastore.test.util import CommonCommonTests

from txdav.caldav.icalendarstore import (
    ICalendarObject, ICalendarHome,
    ICalendar, IAttachment, ICalendarTransaction)

from twext.python.filepath import CachingFilePath as FilePath
from twext.web2.dav import davxml
from twext.web2.http_headers import MimeType
from twext.web2.dav.element.base import WebDAVUnknownElement
from twext.python.vcomponent import VComponent

from twistedcaldav.customxml import InviteNotification, InviteSummary

storePath = FilePath(__file__).parent().child("calendar_store")

homeRoot = storePath.child("ho").child("me").child("home1")

cal1Root = homeRoot.child("calendar_1")

calendar1_objectNames = [
    "1.ics",
    "2.ics",
    "3.ics",
]


home1_calendarNames = [
    "calendar_1",
    "calendar_2",
    "calendar_empty",
]


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



event1modified_text = event4_text.replace(
    "\r\nUID:uid4\r\n",
    "\r\nUID:uid1\r\n"
)



class CommonTests(CommonCommonTests):
    """
    Tests for common functionality of interfaces defined in
    L{txdav.caldav.icalendarstore}.
    """

    requirements = {
        "home1": {
            "calendar_1": {
                "1.ics": cal1Root.child("1.ics").getContent(),
                "2.ics": cal1Root.child("2.ics").getContent(),
                "3.ics": cal1Root.child("3.ics").getContent()
            },
            "calendar_2": {},
            "calendar_empty": {},
            "not_a_calendar": None
        },
        "not_a_home": None
    }

    def storeUnderTest(self):
        """
        Subclasses must override this to return an L{ICommonDataStore} provider
        which adheres to the structure detailed by L{CommonTests.requirements}.
        This attribute is a dict of dict of dicts; the outermost layer
        representing UIDs mapping to calendar homes, then calendar names mapping
        to calendar collections, and finally calendar object names mapping to
        calendar object text.
        """
        raise NotImplementedError()


    @inlineCallbacks
    def homeUnderTest(self):
        """
        Get the calendar home detailed by C{requirements['home1']}.
        """
        returnValue(
            (yield self.transactionUnderTest().calendarHomeWithUID("home1"))
        )


    @inlineCallbacks
    def calendarUnderTest(self):
        """
        Get the calendar detailed by C{requirements['home1']['calendar_1']}.
        """
        returnValue((yield
            (yield self.homeUnderTest()).calendarWithName("calendar_1"))
        )


    @inlineCallbacks
    def calendarObjectUnderTest(self):
        """
        Get the calendar detailed by
        C{requirements['home1']['calendar_1']['1.ics']}.
        """
        returnValue(
            (yield (yield self.calendarUnderTest())
                .calendarObjectWithName("1.ics")))


    def test_calendarStoreProvides(self):
        """
        The calendar store provides L{IDataStore} and its required attributes.
        """
        calendarStore = self.storeUnderTest()
        self.assertProvides(IDataStore, calendarStore)


    def test_transactionProvides(self):
        """
        The transactions generated by the calendar store provide
        L{ICommonStoreTransaction}, L{ICalendarTransaction}, and their
        respectively required attributes.
        """
        txn = self.transactionUnderTest()
        self.assertProvides(ICommonTransaction, txn)
        self.assertProvides(ICalendarTransaction, txn)


    @inlineCallbacks
    def test_homeProvides(self):
        """
        The calendar homes generated by the calendar store provide
        L{ICalendarHome} and its required attributes.
        """
        self.assertProvides(ICalendarHome, (yield self.homeUnderTest()))


    @inlineCallbacks
    def test_calendarProvides(self):
        """
        The calendars generated by the calendar store provide L{ICalendar} and
        its required attributes.
        """
        self.assertProvides(ICalendar, (yield self.calendarUnderTest()))


    @inlineCallbacks
    def test_calendarObjectProvides(self):
        """
        The calendar objects generated by the calendar store provide
        L{ICalendarObject} and its required attributes.
        """
        self.assertProvides(
            ICalendarObject, (yield self.calendarObjectUnderTest())
        )


    @inlineCallbacks
    def notificationUnderTest(self):
        txn = self.transactionUnderTest()
        notifications = yield txn.notificationsWithUID("home1")
        inviteNotification = InviteNotification()
        yield notifications.writeNotificationObject("abc", inviteNotification,
            inviteNotification.toxml())
        notificationObject = yield notifications.notificationObjectWithUID("abc")
        returnValue(notificationObject)


    @inlineCallbacks
    def test_notificationObjectProvides(self):
        """
        The objects retrieved from the notification home (the object returned
        from L{notificationsWithUID}) provide L{INotificationObject}.
        """
        notificationObject = yield self.notificationUnderTest()
        self.assertProvides(INotificationObject, notificationObject)


    @inlineCallbacks
    def test_replaceNotification(self):
        """
        L{INotificationCollection.writeNotificationObject} will silently
        overwrite the notification object.
        """
        notifications = yield self.transactionUnderTest().notificationsWithUID(
            "home1"
        )
        inviteNotification = InviteNotification()
        yield notifications.writeNotificationObject("abc", inviteNotification,
            inviteNotification.toxml())
        inviteNotification2 = InviteNotification(InviteSummary("a summary"))
        yield notifications.writeNotificationObject(
            "abc", inviteNotification, inviteNotification2.toxml())
        abc = yield notifications.notificationObjectWithUID("abc")
        self.assertEquals((yield abc.xmldata()), inviteNotification2.toxml())


    @inlineCallbacks
    def test_notificationObjectModified(self):
        """
        The objects retrieved from the notification home have a C{modified}
        method which returns the timestamp of their last modification.
        """
        notification = yield self.notificationUnderTest()
        self.assertIsInstance((yield notification.modified()), int)


    @inlineCallbacks
    def test_notificationObjectParent(self):
        """
        L{INotificationObject.notificationCollection} returns the
        L{INotificationCollection} that the object was retrieved from.
        """
        txn = self.transactionUnderTest()
        collection = yield txn.notificationsWithUID("home1")
        notification = yield self.notificationUnderTest()
        self.assertIdentical(collection, notification.notificationCollection())


    @inlineCallbacks
    def test_notifierID(self):
        home = yield self.homeUnderTest()
        self.assertEquals(home.notifierID(), "CalDAV|home1")
        calendar = yield home.calendarWithName("calendar_1")
        self.assertEquals(calendar.notifierID(), "CalDAV|home1")
        self.assertEquals(calendar.notifierID(label="collection"), "CalDAV|home1/calendar_1")


    @inlineCallbacks
    def test_calendarHomeWithUID_exists(self):
        """
        Finding an existing calendar home by UID results in an object that
        provides L{ICalendarHome} and has a C{uid()} method that returns the
        same value that was passed in.
        """
        calendarHome = (yield self.transactionUnderTest()
                        .calendarHomeWithUID("home1"))
        self.assertEquals(calendarHome.uid(), "home1")
        self.assertProvides(ICalendarHome, calendarHome)


    @inlineCallbacks
    def test_calendarHomeWithUID_absent(self):
        """
        L{ICommonStoreTransaction.calendarHomeWithUID} should return C{None}
        when asked for a non-existent calendar home.
        """
        txn = self.transactionUnderTest()
        self.assertEquals((yield txn.calendarHomeWithUID("xyzzy")), None)


    @inlineCallbacks
    def test_calendarWithName_exists(self):
        """
        L{ICalendarHome.calendarWithName} returns an L{ICalendar} provider,
        whose name matches the one passed in.
        """
        home = yield self.homeUnderTest()
        for name in home1_calendarNames:
            calendar = yield home.calendarWithName(name)
            if calendar is None:
                self.fail("calendar %r didn't exist" % (name,))
            self.assertProvides(ICalendar, calendar)
            self.assertEquals(calendar.name(), name)


    @inlineCallbacks
    def test_calendarRename(self):
        """
        L{ICalendar.rename} changes the name of the L{ICalendar}.
        """
        home = yield self.homeUnderTest()
        calendar = yield home.calendarWithName("calendar_1")
        yield calendar.rename("some_other_name")
        @inlineCallbacks
        def positiveAssertions():
            self.assertEquals(calendar.name(), "some_other_name")
            self.assertEquals(
                calendar, (yield home.calendarWithName("some_other_name")))
            self.assertEquals(
                None, (yield home.calendarWithName("calendar_1")))
        yield positiveAssertions()
        yield self.commit()
        home = yield self.homeUnderTest()
        calendar = yield home.calendarWithName("some_other_name")
        yield positiveAssertions()
        # FIXME: revert
        # FIXME: test for multiple renames
        # FIXME: test for conflicting renames (a->b, c->a in the same txn)


    @inlineCallbacks
    def test_calendarWithName_absent(self):
        """
        L{ICalendarHome.calendarWithName} returns C{None} for calendars which
        do not exist.
        """
        home = yield self.homeUnderTest()
        calendar = yield home.calendarWithName("xyzzy")
        self.assertEquals(calendar, None)


    @inlineCallbacks
    def test_createCalendarWithName_absent(self):
        """
        L{ICalendarHome.createCalendarWithName} creates a new L{ICalendar} that
        can be retrieved with L{ICalendarHome.calendarWithName}.
        """
        home = yield self.homeUnderTest()
        name = "new"
        self.assertIdentical((yield home.calendarWithName(name)), None)
        yield home.createCalendarWithName(name)
        self.assertNotIdentical((yield home.calendarWithName(name)), None)
        @inlineCallbacks
        def checkProperties():
            calendarProperties = (
                yield home.calendarWithName(name)).properties()
            self.assertEquals(
                calendarProperties[
                    PropertyName.fromString(davxml.ResourceType.sname())
                ],
                davxml.ResourceType.calendar #@UndefinedVariable
            )
        yield checkProperties()

        yield self.commit()

        # Make sure notification fired after commit
        self.assertEquals(self.notifierFactory.history,
            [("update", "CalDAV|home1")])

        # Make sure it's available in a new transaction; i.e. test the commit.
        home = yield self.homeUnderTest()
        self.assertNotIdentical((yield home.calendarWithName(name)), None)

        # Sanity check: are the properties actually persisted?  Check in
        # subsequent transaction.
        yield checkProperties()

        # FIXME: no independent testing of the property store's persistence
        # right now


    @inlineCallbacks
    def test_createCalendarWithName_exists(self):
        """
        L{ICalendarHome.createCalendarWithName} raises
        L{CalendarAlreadyExistsError} when the name conflicts with an already-
        existing 
        """
        home = yield self.homeUnderTest()
        for name in home1_calendarNames:
            yield self.failUnlessFailure(
                maybeDeferred(home.createCalendarWithName, name),
                HomeChildNameAlreadyExistsError
            )


    @inlineCallbacks
    def test_removeCalendarWithName_exists(self):
        """
        L{ICalendarHome.removeCalendarWithName} removes a calendar that already
        exists.
        """
        home = yield self.homeUnderTest()

        # FIXME: test transactions
        for name in home1_calendarNames:
            self.assertNotIdentical((yield home.calendarWithName(name)), None)
            yield home.removeCalendarWithName(name)
            self.assertEquals((yield home.calendarWithName(name)), None)

        yield self.commit()

        # Make sure notification fired after commit
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_1"),
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_2"),
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_empty"),
            ]
        )


    @inlineCallbacks
    def test_removeCalendarWithName_absent(self):
        """
        Attempt to remove an non-existing calendar should raise.
        """
        home = yield self.homeUnderTest()
        yield self.failUnlessFailure(
            maybeDeferred(home.removeCalendarWithName, "xyzzy"),
            NoSuchHomeChildError
        )


    @inlineCallbacks
    def test_calendarObjects(self):
        """
        L{ICalendar.calendarObjects} will enumerate the calendar objects present
        in the filesystem, in name order, but skip those with hidden names.
        """
        calendar1 = yield self.calendarUnderTest()
        calendarObjects = list((yield calendar1.calendarObjects()))

        for calendarObject in calendarObjects:
            self.assertProvides(ICalendarObject, calendarObject)
            self.assertEquals(
                (yield calendar1.calendarObjectWithName(calendarObject.name())),
                calendarObject
            )

        self.assertEquals(
            set(list(o.name() for o in calendarObjects)),
            set(calendar1_objectNames)
        )


    @inlineCallbacks
    def test_calendarObjectsWithRemovedObject(self):
        """
        L{ICalendar.calendarObjects} skips those objects which have been
        removed by L{Calendar.removeCalendarObjectWithName} in the same
        transaction, even if it has not yet been committed.
        """
        calendar1 = yield self.calendarUnderTest()
        calendar1.removeCalendarObjectWithName("2.ics")
        calendarObjects = list((yield calendar1.calendarObjects()))
        self.assertEquals(set(o.name() for o in calendarObjects),
                          set(calendar1_objectNames) - set(["2.ics"]))


    @inlineCallbacks
    def test_ownerCalendarHome(self):
        """
        L{ICalendar.ownerCalendarHome} should match the home UID.
        """
        self.assertEquals(
            (yield self.calendarUnderTest()).ownerCalendarHome().uid(),
            (yield self.homeUnderTest()).uid()
        )


    @inlineCallbacks
    def test_calendarObjectWithName_exists(self):
        """
        L{ICalendar.calendarObjectWithName} returns an L{ICalendarObject}
        provider for calendars which already exist.
        """
        calendar1 = yield self.calendarUnderTest()
        for name in calendar1_objectNames:
            calendarObject = yield calendar1.calendarObjectWithName(name)
            self.assertProvides(ICalendarObject, calendarObject)
            self.assertEquals(calendarObject.name(), name)
            # FIXME: add more tests based on CommonTests.requirements


    @inlineCallbacks
    def test_calendarObjectWithName_absent(self):
        """
        L{ICalendar.calendarObjectWithName} returns C{None} for calendars which
        don't exist.
        """
        calendar1 = yield self.calendarUnderTest()
        self.assertEquals((yield calendar1.calendarObjectWithName("xyzzy")), None)


    @inlineCallbacks
    def test_removeCalendarObjectWithUID_exists(self):
        """
        Remove an existing calendar object.
        """
        calendar = yield self.calendarUnderTest()
        for name in calendar1_objectNames:
            uid = (u'uid' + name.rstrip(".ics"))
            self.assertNotIdentical((yield calendar.calendarObjectWithUID(uid)),
                                    None)
            yield calendar.removeCalendarObjectWithUID(uid)
            self.assertEquals(
                (yield calendar.calendarObjectWithUID(uid)),
                None
            )
            self.assertEquals(
                (yield calendar.calendarObjectWithName(name)),
                None
            )

        # Make sure notifications are fired after commit
        yield self.commit()
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_1"),
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_1"),
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_1"),
            ]
        )

    @inlineCallbacks
    def test_removeCalendarObjectWithName_exists(self):
        """
        Remove an existing calendar object.
        """
        calendar = yield self.calendarUnderTest()
        for name in calendar1_objectNames:
            self.assertNotIdentical(
                (yield calendar.calendarObjectWithName(name)), None
            )
            yield calendar.removeCalendarObjectWithName(name)
            self.assertIdentical(
                (yield calendar.calendarObjectWithName(name)), None
            )


    @inlineCallbacks
    def test_removeCalendarObjectWithName_absent(self):
        """
        Attempt to remove an non-existing calendar object should raise.
        """
        calendar = yield self.calendarUnderTest()
        yield self.failUnlessFailure(
            maybeDeferred(calendar.removeCalendarObjectWithName, "xyzzy"),
            NoSuchObjectResourceError
        )


    @inlineCallbacks
    def test_calendarName(self):
        """
        L{Calendar.name} reflects the name of the calendar.
        """
        self.assertEquals((yield self.calendarUnderTest()).name(), "calendar_1")


    @inlineCallbacks
    def test_calendarObjectName(self):
        """
        L{ICalendarObject.name} reflects the name of the calendar object.
        """
        self.assertEquals(
            (yield self.calendarObjectUnderTest()).name(),
            "1.ics"
        )


    @inlineCallbacks
    def test_component(self):
        """
        L{ICalendarObject.component} returns a L{VComponent} describing the
        calendar data underlying that calendar object.
        """
        component = yield (yield self.calendarObjectUnderTest()).component()

        self.failUnless(
            isinstance(component, VComponent),
            component
        )

        self.assertEquals(component.name(), "VCALENDAR")
        self.assertEquals(component.mainType(), "VEVENT")
        self.assertEquals(component.resourceUID(), "uid1")


    @inlineCallbacks
    def test_iCalendarText(self):
        """
        L{ICalendarObject.iCalendarText} returns a C{str} describing the same
        data provided by L{ICalendarObject.component}.
        """
        text = yield (yield self.calendarObjectUnderTest()).iCalendarText()
        self.assertIsInstance(text, str)
        self.failUnless(text.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertIn("\r\nUID:uid1\r\n", text)
        self.failUnless(text.endswith("\r\nEND:VCALENDAR\r\n"))


    @inlineCallbacks
    def test_calendarObjectUID(self):
        """
        L{ICalendarObject.uid} returns a C{str} describing the C{UID} property
        of the calendar object's component.
        """
        self.assertEquals(
            (yield self.calendarObjectUnderTest()).uid(), "uid1"
        )


    def test_organizer(self):
        """
        L{ICalendarObject.organizer} returns a C{str} describing the calendar
        user address of the C{ORGANIZER} property of the calendar object's
        component.
        """
        self.assertEquals(
            (yield self.calendarObjectUnderTest()).organizer(),
            "mailto:wsanchez@apple.com"
        )


    @inlineCallbacks
    def test_calendarObjectWithUID_absent(self):
        """
        L{ICalendar.calendarObjectWithUID} returns C{None} for calendars which
        don't exist.
        """
        calendar1 = yield self.calendarUnderTest()
        self.assertEquals((yield calendar1.calendarObjectWithUID("xyzzy")),
                          None)


    @inlineCallbacks
    def test_calendars(self):
        """
        L{ICalendarHome.calendars} returns an iterable of L{ICalendar}
        providers, which are consistent with the results from
        L{ICalendar.calendarWithName}.
        """
        # Add a dot directory to make sure we don't find it
        # self.home1._path.child(".foo").createDirectory()
        home = yield self.homeUnderTest()
        calendars = list((yield home.calendars()))

        for calendar in calendars:
            self.assertProvides(ICalendar, calendar)
            self.assertEquals(calendar,
                              (yield home.calendarWithName(calendar.name())))

        self.assertEquals(
            set(c.name() for c in calendars),
            set(home1_calendarNames)
        )


    @inlineCallbacks
    def test_calendarsAfterAddCalendar(self):
        """
        L{ICalendarHome.calendars} includes calendars recently added with
        L{ICalendarHome.createCalendarWithName}.
        """
        home = yield self.homeUnderTest()
        allCalendars = yield home.calendars()
        before = set(x.name() for x in allCalendars)
        yield home.createCalendarWithName("new-name")
        allCalendars = yield home.calendars()
        after = set(x.name() for x in allCalendars)
        self.assertEquals(before | set(['new-name']), after)


    @inlineCallbacks
    def test_createCalendarObjectWithName_absent(self):
        """
        L{ICalendar.createCalendarObjectWithName} creates a new
        L{ICalendarObject}.
        """
        calendar1 = yield self.calendarUnderTest()
        name = "4.ics"
        self.assertIdentical(
            (yield calendar1.calendarObjectWithName(name)), None
        )
        component = VComponent.fromString(event4_text)
        yield calendar1.createCalendarObjectWithName(name, component)

        calendarObject = yield calendar1.calendarObjectWithName(name)
        self.assertEquals((yield calendarObject.component()), component)

        yield self.commit()

        # Make sure notifications fire after commit
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_1"),
            ]
        )


    @inlineCallbacks
    def test_createCalendarObjectWithName_exists(self):
        """
        L{ICalendar.createCalendarObjectWithName} raises
        L{CalendarObjectNameAlreadyExistsError} if a calendar object with the
        given name already exists in that calendar.
        """
        cal = yield self.calendarUnderTest()
        comp = VComponent.fromString(event4_text)
        yield self.failUnlessFailure(
            maybeDeferred(cal.createCalendarObjectWithName, "1.ics", comp),
            ObjectResourceNameAlreadyExistsError,
        )


    @inlineCallbacks
    def test_createCalendarObjectWithName_invalid(self):
        """
        L{ICalendar.createCalendarObjectWithName} raises
        L{InvalidCalendarComponentError} if presented with invalid iCalendar
        text.
        """
        yield self.failUnlessFailure(
            maybeDeferred((yield self.calendarUnderTest()).createCalendarObjectWithName,
            "new", VComponent.fromString(event4notCalDAV_text)),
            InvalidObjectResourceError,
        )

    @inlineCallbacks
    def test_setComponent_invalid(self):
        """
        L{ICalendarObject.setComponent} raises L{InvalidICalendarDataError} if
        presented with invalid iCalendar text.
        """
        calendarObject = yield self.calendarObjectUnderTest()
        yield self.failUnlessFailure(
            maybeDeferred(calendarObject.setComponent,
                          VComponent.fromString(event4notCalDAV_text)),
            InvalidObjectResourceError,
        )


    @inlineCallbacks
    def test_setComponent_uidchanged(self):
        """
        L{ICalendarObject.setComponent} raises L{InvalidCalendarComponentError}
        when given a L{VComponent} whose UID does not match its existing UID.
        """
        calendar1 = yield self.calendarUnderTest()
        component = VComponent.fromString(event4_text)
        calendarObject = yield calendar1.calendarObjectWithName("1.ics")
        yield self.failUnlessFailure(
            maybeDeferred(calendarObject.setComponent, component),
            InvalidObjectResourceError,
        )


    @inlineCallbacks
    def test_calendarHomeWithUID_create(self):
        """
        L{ICommonStoreTransaction.calendarHomeWithUID} with C{create=True}
        will create a calendar home that doesn't exist yet.
        """
        txn = self.transactionUnderTest()
        noHomeUID = "xyzzy"
        calendarHome = yield txn.calendarHomeWithUID(
            noHomeUID,
            create=True
        )
        @inlineCallbacks
        def readOtherTxn():
            otherTxn = self.savedStore.newTransaction(self.id() + "other txn")
            self.addCleanup(otherTxn.commit)
            returnValue((yield otherTxn.calendarHomeWithUID(noHomeUID)))
        self.assertProvides(ICalendarHome, calendarHome)
        # Default calendar should be automatically created.
        self.assertProvides(ICalendar,
                            (yield calendarHome.calendarWithName("calendar")))
        # A concurrent transaction shouldn't be able to read it yet:
        self.assertIdentical((yield readOtherTxn()), None)
        yield self.commit()
        # But once it's committed, other transactions should see it.
        self.assertProvides(ICalendarHome, (yield readOtherTxn()))


    @inlineCallbacks
    def test_setComponent(self):
        """
        L{CalendarObject.setComponent} changes the result of
        L{CalendarObject.component} within the same transaction.
        """
        component = VComponent.fromString(event1modified_text)

        calendar1 = yield self.calendarUnderTest()
        calendarObject = yield calendar1.calendarObjectWithName("1.ics")
        oldComponent = yield calendarObject.component()
        self.assertNotEqual(component, oldComponent)
        yield calendarObject.setComponent(component)
        self.assertEquals((yield calendarObject.component()), component)

        # Also check a new instance
        calendarObject = yield calendar1.calendarObjectWithName("1.ics")
        self.assertEquals((yield calendarObject.component()), component)

        yield self.commit()

        # Make sure notification fired after commit
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CalDAV|home1"),
                ("update", "CalDAV|home1/calendar_1"),
            ]
        )


    def checkPropertiesMethod(self, thunk):
        """
        Verify that the given object has a properties method that returns an
        L{IPropertyStore}.
        """
        properties = thunk.properties()
        self.assertProvides(IPropertyStore, properties)


    @inlineCallbacks
    def test_homeProperties(self):
        """
        L{ICalendarHome.properties} returns a property store.
        """
        self.checkPropertiesMethod((yield self.homeUnderTest()))


    @inlineCallbacks
    def test_calendarProperties(self):
        """
        L{ICalendar.properties} returns a property store.
        """
        self.checkPropertiesMethod((yield self.calendarUnderTest()))


    @inlineCallbacks
    def test_calendarObjectProperties(self):
        """
        L{ICalendarObject.properties} returns a property store.
        """
        self.checkPropertiesMethod((yield self.calendarObjectUnderTest()))


    @inlineCallbacks
    def test_newCalendarObjectProperties(self):
        """
        L{ICalendarObject.properties} returns an empty property store for a
        calendar object which has been created but not committed.
        """
        calendar = yield self.calendarUnderTest()
        yield calendar.createCalendarObjectWithName(
            "4.ics", VComponent.fromString(event4_text)
        )
        newEvent = yield calendar.calendarObjectWithName("4.ics")
        self.assertEquals(newEvent.properties().items(), [])


    @inlineCallbacks
    def test_setComponentPreservesProperties(self):
        """
        L{ICalendarObject.setComponent} preserves properties.

        (Some implementations must go to extra trouble to provide this
        behavior; for example, file storage must copy extended attributes from
        the existing file to the temporary file replacing it.)
        """
        propertyName = PropertyName("http://example.com/ns", "example")
        propertyContent = WebDAVUnknownElement("sample content")
        propertyContent.name = propertyName.name
        propertyContent.namespace = propertyName.namespace

        (yield self.calendarObjectUnderTest()).properties()[
            propertyName] = propertyContent
        yield self.commit()
        # Sanity check; are properties even readable in a separate transaction?
        # Should probably be a separate test.
        self.assertEquals(
            (yield self.calendarObjectUnderTest()).properties()[propertyName],
            propertyContent)
        obj = yield self.calendarObjectUnderTest()
        event1_text = yield obj.iCalendarText()
        event1_text_withDifferentSubject = event1_text.replace(
            "SUMMARY:CalDAV protocol updates",
            "SUMMARY:Changed"
        )
        # Sanity check; make sure the test has the right idea of the subject.
        self.assertNotEquals(event1_text, event1_text_withDifferentSubject)
        newComponent = VComponent.fromString(event1_text_withDifferentSubject)
        obj.setComponent(newComponent)

        # Putting everything into a separate transaction to account for any
        # caching that may take place.
        yield self.commit()
        self.assertEquals(
            (yield self.calendarObjectUnderTest()).properties()[propertyName],
            propertyContent
        )


    eventWithDropbox = "\r\n".join("""
BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//Example Inc.//Example Calendar//EN
VERSION:2.0
BEGIN:VTIMEZONE
LAST-MODIFIED:20040110T032845Z
TZID:US/Eastern
BEGIN:DAYLIGHT
DTSTART:20000404T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=4
TZNAME:EDT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20001026T020000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
TZNAME:EST
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTAMP:20051222T205953Z
CREATED:20060101T150000Z
DTSTART;TZID=US/Eastern:20060101T100000
DURATION:PT1H
SUMMARY:event 1
UID:event1@ninevah.local
ORGANIZER:user01
ATTENDEE;PARTSTAT=ACCEPTED:user01
ATTACH;VALUE=URI:/calendars/users/home1/some-dropbox-id/some-dropbox-id/caldavd.plist
X-APPLE-DROPBOX:/calendars/users/home1/dropbox/some-dropbox-id
END:VEVENT
END:VCALENDAR
    """.strip().split("\n"))


    @inlineCallbacks
    def test_dropboxID(self):
        """
        L{ICalendarObject.dropboxID} should synthesize its dropbox from the X
        -APPLE-DROPBOX property, if available.
        """
        cal = yield self.calendarUnderTest()
        yield cal.createCalendarObjectWithName("drop.ics", VComponent.fromString(
                self.eventWithDropbox
            )
        )
        obj = yield cal.calendarObjectWithName("drop.ics")
        self.assertEquals((yield obj.dropboxID()), "some-dropbox-id")


    @inlineCallbacks
    def test_indexByDropboxProperty(self):
        """
        L{ICalendarHome.calendarObjectWithDropboxID} will return a calendar
        object in the calendar home with the given final segment in its C{X
        -APPLE-DROPBOX} property URI.
        """
        objName = "with-dropbox.ics"
        cal = yield self.calendarUnderTest()
        cal.createCalendarObjectWithName(
            objName, VComponent.fromString(
                self.eventWithDropbox
            )
        )
        yield self.commit()
        home = yield self.homeUnderTest()
        cal = yield self.calendarUnderTest()
        fromName = yield cal.calendarObjectWithName(objName)
        fromDropbox = yield home.calendarObjectWithDropboxID("some-dropbox-id")
        self.assertEquals(fromName, fromDropbox)


    @inlineCallbacks
    def createAttachmentTest(self, refresh):
        """
        Common logic for attachment-creation tests.
        """
        obj = yield self.calendarObjectUnderTest()
        t = yield obj.createAttachmentWithName(
            "new.attachment", MimeType("text", "x-fixture")
        )
        t.write("new attachment")
        t.write(" text")
        t.loseConnection()
        obj = yield refresh(obj)
        class CaptureProtocol(Protocol):
            buf = ''
            def dataReceived(self, data):
                self.buf += data
            def connectionLost(self, reason):
                self.deferred.callback(self.buf)
        capture = CaptureProtocol()
        capture.deferred = Deferred()
        attachment = yield obj.attachmentWithName("new.attachment")
        self.assertProvides(IAttachment, attachment)
        attachment.retrieve(capture)
        data = yield capture.deferred
        self.assertEquals(data, "new attachment text")
        contentType = attachment.contentType()
        self.assertIsInstance(contentType, MimeType)
        self.assertEquals(contentType, MimeType("text", "x-fixture"))
        self.assertEquals(attachment.md5(), '50a9f27aeed9247a0833f30a631f1858')
        self.assertEquals(
            [attachment.name() for attachment in (yield obj.attachments())],
            ['new.attachment']
        )


    def test_createAttachment(self):
        """
        L{ICalendarObject.createAttachmentWithName} will store an
        L{IAttachment} object that can be retrieved by
        L{ICalendarObject.attachmentWithName}.
        """
        return self.createAttachmentTest(lambda x: x)


    def test_createAttachmentCommit(self):
        """
        L{ICalendarObject.createAttachmentWithName} will store an
        L{IAttachment} object that can be retrieved by
        L{ICalendarObject.attachmentWithName} in subsequent transactions.
        """
        @inlineCallbacks
        def refresh(obj):
            yield self.commit()
            result = yield self.calendarObjectUnderTest()
            returnValue(result)
        return self.createAttachmentTest(refresh)


    def test_removeAttachmentWithName(self, refresh=lambda x:x):
        """
        L{ICalendarObject.removeAttachmentWithName} will remove the calendar
        object with the given name.
        """
        @inlineCallbacks
        def deleteIt(ignored):
            obj = yield self.calendarObjectUnderTest()
            obj.removeAttachmentWithName("new.attachment")
            obj = yield refresh(obj)
            self.assertIdentical(
                None, (yield obj.attachmentWithName("new.attachment"))
            )
            self.assertEquals(list((yield obj.attachments())), [])
        return self.test_createAttachmentCommit().addCallback(deleteIt)


    def test_removeAttachmentWithNameCommit(self):
        """
        L{ICalendarObject.removeAttachmentWithName} will remove the calendar
        object with the given name.  (After commit, it will still be gone.)
        """
        @inlineCallbacks
        def refresh(obj):
            yield self.commit()
            result = yield self.calendarObjectUnderTest()
            returnValue(result)
        return self.test_removeAttachmentWithName(refresh)


    @inlineCallbacks
    def test_noDropboxCalendar(self):
        """
        L{ICalendarObject.createAttachmentWithName} may create a directory
        named 'dropbox', but this should not be seen as a calendar by
        L{ICalendarHome.calendarWithName} or L{ICalendarHome.calendars}.
        """
        obj = yield self.calendarObjectUnderTest()
        t = yield obj.createAttachmentWithName(
            "new.attachment", MimeType("text", "plain")
        )
        t.write("new attachment text")
        t.loseConnection()
        yield self.commit()
        home = (yield self.homeUnderTest())
        calendars = (yield home.calendars())
        self.assertEquals((yield home.calendarWithName("dropbox")), None)
        self.assertEquals(
            set([n.name() for n in calendars]),
            set(home1_calendarNames))


    @inlineCallbacks
    def test_finishedOnCommit(self):
        """ 
        Calling L{ITransaction.abort} or L{ITransaction.commit} after
        L{ITransaction.commit} has already been called raises an
        L{AlreadyFinishedError}.
        """
        yield self.calendarObjectUnderTest()
        txn = self.lastTransaction
        yield self.commit()
        
        yield self.failUnlessFailure(
            maybeDeferred(txn.commit),
            AlreadyFinishedError
        )
        yield self.failUnlessFailure(
            maybeDeferred(txn.abort),
            AlreadyFinishedError
        )


    @inlineCallbacks
    def test_dontLeakCalendars(self):
        """
        Calendars in one user's calendar home should not show up in another
        user's calendar home.
        """
        home2 = yield self.transactionUnderTest().calendarHomeWithUID(
            "home2", create=True)
        self.assertIdentical(
            (yield home2.calendarWithName("calendar_1")), None)


    @inlineCallbacks
    def test_dontLeakObjects(self):
        """
        Calendar objects in one user's calendar should not show up in another
        user's via uid or name queries.
        """
        home1 = yield self.homeUnderTest()
        home2 = yield self.transactionUnderTest().calendarHomeWithUID(
            "home2", create=True)
        calendar1 = yield home1.calendarWithName("calendar_1")
        calendar2 = yield home2.calendarWithName("calendar")
        objects = list(
            (yield (yield home2.calendarWithName("calendar")).calendarObjects()))
        self.assertEquals(objects, [])
        for resourceName in self.requirements['home1']['calendar_1'].keys():
            obj = yield calendar1.calendarObjectWithName(resourceName)
            self.assertIdentical(
                (yield calendar2.calendarObjectWithName(resourceName)), None)
            self.assertIdentical(
                (yield calendar2.calendarObjectWithUID(obj.uid())), None)


    @inlineCallbacks
    def test_eachCalendarHome(self):
        """
        L{ICalendarTransaction.eachCalendarHome} returns an iterator that
        yields 2-tuples of (transaction, home).
        """
        # create some additional calendar homes
        additionalUIDs = set('alpha-uid home2 home3 beta-uid'.split())
        txn = self.transactionUnderTest()
        for name in additionalUIDs:
            # maybe it's not actually necessary to yield (i.e. wait) for each
            # one?  commit() should wait for all of them.
            yield txn.calendarHomeWithUID(name, create=True)
        yield self.commit()
        foundUIDs = set([])
        lastTxn = None
        for txn, home in (yield self.storeUnderTest().eachCalendarHome()):
            self.addCleanup(txn.commit)
            foundUIDs.add(home.uid())
            self.assertNotIdentical(lastTxn, txn)
            lastTxn = txn
        requiredUIDs = set([
            uid for uid in self.requirements
            if self.requirements[uid] is not None
        ])
        expectedUIDs = additionalUIDs.union(requiredUIDs)
        self.assertEquals(foundUIDs, expectedUIDs)



