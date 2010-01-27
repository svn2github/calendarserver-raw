##
# Copyright (c) 2010 Apple Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
##

"""
Test memcacheprops.
"""

import os

from hashlib import md5

from twisted.python.reflect import namedClass
from twisted.web2.http_headers import generateContentType
from twisted.web2.dav.resource import TwistedGETContentMD5
from twisted.web2.dav.davxml import GETContentType

from twistedcaldav.config import config
from twistedcaldav.static import CalDAVFile, CalendarHomeProvisioningFile
#from twistedcaldav.fileprops import PropertyCollection

#from twistedcaldav.test.test_memcacheprops import StubCollection, StubResource, StubFP
from twistedcaldav.test import test_memcacheprops
from twistedcaldav.test.util import TestCase

#class PropertyCollectionTestCase(test_memcacheprops.MemcachePropertyCollectionTestCase):
class PropertyCollectionTestCase(TestCase):
    """
    Test PropertyCollection
    """

    def setUp(self):
        super(PropertyCollectionTestCase, self).setUp()

        #
        # Set up provisioning resources.  Mostly, we want the calendar
        # home and calendar.
        #

        #
        # We need a config to get a directory set up
        #
        srcroot = os.path.join(os.path.dirname(__file__), "..", "..")
        confdir = os.path.join(srcroot, "conf")
        configFileName = os.path.join(confdir, "caldavd-test.plist")

        #
        # We need a directory for provisioning to work
        #
        loadConfig(configFileName)
        config.DirectoryService.params.xmlFile = os.path.join(confdir, "accounts-test.xml")

        self.directoryService = getDirectory()

        #
        # OK, here's the provisioning resource tree
        #
        cpf = CalendarHomeProvisioningFile(
            os.path.join(self.docroot, "calendars"),
            self.directoryService,
            "/calendars/"
        )

        self.calendarHome = cpf.homeForDirectoryRecord(self.directoryService.recordWithGUID("user01"))
        self.calendar = self.calendarHome.getChild("calendar")

        #
        # Now, let's populate the calendar with some events
        #
        self.calendarObjectNames = (
            "%s.ics" % (name,)
            for name in (
                "Mercury",
                "Venus",
                "Earth",
                "Mars",
                "Saturn",
                "Jupiter",
                "Neptune",
                "Uranus",
                "Pluto", # Dwarf, but hey, why not?
            )
        )

        for objectName in self.calendarObjectNames:
            data = (
                """BEGIN:VCALENDAR"""
                """VERSION:2.0"""
                """PRODID:-//Apple Inc.//test//EN"""
                """CALSCALE:GREGORIAN"""
                """BEGIN:VEVENT"""
                """CREATED:20090721T211645Z"""
                """DTSTAMP:20090722T145407Z"""
                """UID:%(uid)s"""
                """SUMMARY:%(name)s"""
                """DTSTART;VALUE=DATE:20090907"""
                """DTEND;VALUE=DATE:20090908"""
                """END:VEVENT"""
                """END:VCALENDAR"""
                % {
                    "uid" : objectName,
                    "name": objectName,
                }
            )

            childFP = self.calendar.fp.child(objectName)
            fh = childFP.open("w")
            try:
                fh.write(data)
            finally:
                fh.close()

            #
            # Instantiate CalDAVFile directory, not through the
            # calendar home, because we want to use the old-style
            # (xattr) property store during setup here so that we can
            # verify that the upgrade to the new-style (file) property
            # store works.
            #
            child = CalDAVFile(childFP)
            child.writeDeadProperty(TwistedGETContentMD5.fromString(md5(data).hexdigest()))
            child.writeDeadProperty(GETContentType.fromString("text/calendar"))

        #
        # Remove the index, because we monkeyed with files directly.
        #
        self.calendar.fp.child(".db.sqlite").remove()

    def tearDown(self):
        raise NotImplementedError()

    def test_upgrade(self):
        #print "*"*80
        #print self.calendar
        #print self.calendar.listChildren()
        #print "*"*80

        calendar = self.calendar
        for childName in calendar.listChildren():
            #print childName
            child = calendar.getChild(childName)

            # Ensure that our initial dead properties are still there
            # and correct via the API
            self.failUnless(child.hasDeadProperty(TwistedGETContentMD5))
            self.failUnless(child.hasDeadProperty(GETContentType))
            self.assertEquals(len(str(child.readDeadProperty(TwistedGETContentMD5))), 32)
            self.assertEquals(str(child.readDeadProperty(GETContentType)), "text/calendar")

            # Old dead properties should be gone now
            oldSchool = CalDAVFile(child.fp.path)
            oldProperties = [x for x in oldSchool.deadProperties().list()]
            self.assertEquals(len(oldProperties), 0, oldProperties)

#
# Utilities
#

def loadConfig(configFileName):
    if configFileName is None:
        configFileName = defaultConfigFile

    if not os.path.isfile(configFileName):
        import sys
        sys.stderr.write("No config file: %s\n" % (configFileName,))
        sys.exit(1)

    config.loadConfig(configFileName)

    return config

def getDirectory():
    BaseDirectoryService = namedClass(config.DirectoryService["type"])

    class MyDirectoryService (BaseDirectoryService):
        def getPrincipalCollection(self):
            if not hasattr(self, "_principalCollection"):
                #
                # Instantiating a CalendarHomeProvisioningResource with a directory
                # will register it with the directory (still smells like a hack).
                #
                # We need that in order to locate calendar homes via the directory.
                #
                from twistedcaldav.static import CalendarHomeProvisioningFile
                CalendarHomeProvisioningFile(os.path.join(config.DocumentRoot, "calendars"), self, "/calendars/")

                from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
                self._principalCollection = DirectoryPrincipalProvisioningResource("/principals/", self)

            return self._principalCollection

        def setPrincipalCollection(self, coll):
            # See principal.py line 237:  self.directory.principalCollection = self
            pass

        principalCollection = property(getPrincipalCollection, setPrincipalCollection)

        def calendarHomeForRecord(self, record):
            principal = self.principalCollection.principalForRecord(record)
            if principal:
                try:
                    return principal._calendarHome()
                except AttributeError:
                    pass
            return None

        def calendarHomeForShortName(self, recordType, shortName):
            principal = self.principalCollection.principalForShortName(recordType, shortName)
            if principal:
                try:
                    return principal._calendarHome()
                except AttributeError:
                    pass
            return None

        def principalForCalendarUserAddress(self, cua):
            return self.principalCollection.principalForCalendarUserAddress(cua)

    return MyDirectoryService(**config.DirectoryService["params"])
