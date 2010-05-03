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

from twext.python.filepath import CachingFilePath as FilePath
from twext.web2.dav import davxml
from twisted.internet.defer import inlineCallbacks, returnValue
from twistedcaldav.config import config
from twistedcaldav.directory import augment
from twistedcaldav.directory.calendar import uidsResourceName
from twistedcaldav.directory.principal import \
    DirectoryPrincipalProvisioningResource
from twistedcaldav.directory.test.test_xmlfile import augmentsFile, xmlFile
from twistedcaldav.directory.xmlfile import XMLDirectoryService
from twistedcaldav.static import CalendarHomeProvisioningFile
from twistedcaldav.test.util import TestCase
from txcaldav.calendarstore.file import CalendarStore, CalendarHome



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
        self.xmlFile = FilePath(config.DataRoot).child("accounts.xml")
        self.xmlFile.setContent(xmlFile.getContent())
        self.directoryService = XMLDirectoryService({'xmlFile' :
                                                     "accounts.xml"})
        augment.AugmentService = augment.AugmentXMLDB(
            xmlFiles=(augmentsFile.path,))
        # Set up a principals hierarchy for each service we're testing with
        provisioningResource = DirectoryPrincipalProvisioningResource(
            "/principals/", self.directoryService
        )
        self.site.resource.putChild("principals", provisioningResource)
        calendarsPath = FilePath(self.docroot).child("calendars")
        calendarsPath.makedirs()
        self.calendarCollection = CalendarHomeProvisioningFile(
            calendarsPath, self.directoryService, "/calendars/"
        )
        self.site.resource.putChild("calendars", self.calendarCollection)
        self.site.resource.setAccessControlList(davxml.ACL())


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
    def test_lookupCalendar(self):
        """
        When a L{CalDAVFile} representing a calendar collection is looked up in
        a L{CalendarHomeFile} representing a calendar home, it will create a
        corresponding L{Calendar} via C{CalendarHome.calendarWithName}.
        """
        calDavFile = yield self.getResource("calendars/users/wsanchez/calendar")
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
