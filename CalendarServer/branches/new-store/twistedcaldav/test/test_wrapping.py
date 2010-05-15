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
Tests for the interaction between model-level and protocol-level logic.
"""

from twisted.internet.defer import inlineCallbacks, returnValue

from twistedcaldav.directory.calendar import uidsResourceName
from twistedcaldav.ical import Component as VComponent

from twistedcaldav.storebridge import ProtoCalendarCollectionFile

from twistedcaldav.test.util import TestCase

from txcaldav.calendarstore.file import CalendarStore, CalendarHome
from txcaldav.calendarstore.test.test_file import event4_text



class WrappingTests(TestCase):
    """
    Tests for L{twistedcaldav.static.CalDAVFile} creating the appropriate type
    of txcaldav.calendarstore.file underlying object when it can determine what
    type it really represents.
    """

    def setUp(self):
        # Mostly copied from
        # twistedcaldav.directory.test.test_calendar.ProvisionedCalendars; this
        # should probably be refactored, perhaps even to call (some part of) the
        # actual root-resource construction logic?
        super(WrappingTests, self).setUp()

        # Setup the initial directory
        self.createStockDirectoryService()
        self.setupCalendars()


    def populateOneObject(self, objectName, objectText):
        """
        Populate one calendar object in the test user's calendar.

        @param objectName: The name of a calendar object.
        @type objectName: str
        @param objectText: Some iCalendar text to populate it with.
        @type objectText: str 
        """
        record = self.directoryService.recordWithShortName("users", "wsanchez")
        uid = record.uid
        # XXX there should be a more test-friendly way to ensure the directory
        # actually exists
        try:
            self.calendarCollection._newStore._path.createDirectory()
        except:
            pass
        txn = self.calendarCollection._newStore.newTransaction()
        home = txn.calendarHomeWithUID(uid, True)
        cal = home.calendarWithName("calendar")
        if cal is None:
            home.createCalendarWithName("calendar")
            cal = home.calendarWithName("calendar")
        cal.createCalendarObjectWithName(objectName, VComponent.fromString(objectText))
        txn.commit()


    @inlineCallbacks
    def getResource(self, path):
        """
        Retrieve a resource from the site.

        @param path: the path from the root of the site (not starting with a
            slash)

        @type path: C{str}
        """
        segments = path.split("/")
        resource = self.site.resource
        while segments:
            resource, segments = yield resource.locateChild(None, segments)
        returnValue(resource)


    def test_createStore(self):
        """
        Creating a CalendarHomeProvisioningFile will create a paired
        CalendarStore.
        """
        self.assertIsInstance(self.calendarCollection._newStore, CalendarStore)
        self.assertEquals(self.calendarCollection._newStore._path,
                          self.calendarCollection.fp.child(uidsResourceName))


    @inlineCallbacks
    def test_lookupCalendarHome(self):
        """
        When a L{CalDAVFile} representing an existing calendar home is looked up
        in a CalendarHomeFile, it will create a corresponding L{CalendarHome}
        via C{newTransaction().calendarHomeWithUID}.
        """
        calDavFile = yield self.getResource("calendars/users/wsanchez/")
        self.assertEquals(calDavFile.fp, calDavFile._newStoreCalendarHome._path)
        self.assertIsInstance(calDavFile._newStoreCalendarHome, CalendarHome)


    @inlineCallbacks
    def test_lookupExistingCalendar(self):
        """
        When a L{CalDAVFile} representing an existing calendar collection is
        looked up in a L{CalendarHomeFile} representing a calendar home, it will
        create a corresponding L{Calendar} via C{CalendarHome.calendarWithName}.
        """
        calDavFile = yield self.getResource("calendars/users/wsanchez/calendar")
        self.assertEquals(calDavFile.fp, calDavFile._newStoreCalendar._path)


    @inlineCallbacks
    def test_lookupNewCalendar(self):
        """
        When a L{CalDAVFile} which represents a not-yet-created calendar
        collection is looked up in a L{CalendarHomeFile} representing a calendar
        home, it will initially have a new storage backend set to C{None}, but
        when the calendar is created via a protocol action, the backend will be
        initialized to match.
        """
        calDavFile = yield self.getResource("calendars/users/wsanchez/frobozz")
        self.assertIsInstance(calDavFile, ProtoCalendarCollectionFile)
        calDavFile.createCalendarCollection()
        self.assertEquals(calDavFile.fp, calDavFile._newStoreCalendar._path)


    @inlineCallbacks
    def test_lookupSpecial(self):
        """
        When a L{CalDAVFile} I{not} representing a calendar collection - one of
        the special collections, like the dropbox or freebusy URLs - is looked
        up in a L{CalendarHomeFile} representing a calendar home, it will I{not}
        create a corresponding L{Calendar} via C{CalendarHome.calendarWithName}.
        """
        for specialName in ['dropbox', 'freebusy', 'notifications']:
            calDavFile = yield self.getResource(
                "calendars/users/wsanchez/%s" % (specialName,)
            )
            self.assertIdentical(
                getattr(calDavFile, "_newStoreCalendar", None), None
            )


    @inlineCallbacks
    def test_lookupCalendarObject(self):
        """
        When a L{CalDAVFile} representing an existing calendar object is looked
        up on a L{CalDAVFile} representing a calendar collection, a parallel
        L{CalendarObject} will be created (with a matching FilePath).
        """
        self.populateOneObject("1.ics", event4_text)
        calDavFileCalendar = yield self.getResource(
            "calendars/users/wsanchez/calendar/1.ics"
        )
        self.assertEquals(calDavFileCalendar._newStoreObject._path, 
                          calDavFileCalendar.fp)
