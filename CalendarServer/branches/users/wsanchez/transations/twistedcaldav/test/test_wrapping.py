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
from twistedcaldav.test.util import TestCase
from twistedcaldav.config import config
from twext.python.filepath import CachingFilePath as FilePath
from twistedcaldav.directory.test.test_xmlfile import xmlFile
from twistedcaldav.directory.xmlfile import XMLDirectoryService
from twistedcaldav.directory import augment
from twistedcaldav.directory.test.test_xmlfile import augmentsFile
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twext.web2.dav import davxml
from twistedcaldav.static import CalendarHomeProvisioningFile
from txcaldav.calendarstore.file import CalendarStore
from twisted.internet.defer import inlineCallbacks
from twistedcaldav.directory.calendar import uidsResourceName


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


    def test_createStore(self):
        """
        Creating a CalendarHomeProvisioningFile will create a paired
        CalendarStore.
        """
        self.assertIsInstance(self.calendarCollection._newStore, CalendarStore)
        self.assertEqual(self.calendarCollection._newStore._path,
                         self.calendarCollection.fp.child(uidsResourceName))


    @inlineCallbacks
    def test_lookupCalendar(self):
        """
        When a L{CalDAVFile} representing an existing calendar collection is
        looked up in a CalendarHomeProvisioningFile, it will create a
        corresponding L{txcaldav.calendarstore.file.Calendar} via
        calendarHomeWithUID.
        """
        segments = ["users", "wsanchez", ""]
        resource = self.calendarCollection
        while segments:
            resource, segments = yield resource.locateChild(None, segments)
        self.assertEqual(resource.fp, resource._newStoreCalendar._path)



