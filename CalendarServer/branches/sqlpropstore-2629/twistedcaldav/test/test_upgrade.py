##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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

from twisted.web2.dav import davxml
from twisted.web2.dav.resource import TwistedGETContentMD5
from twistedcaldav import caldavxml, customxml
from twistedcaldav.config import config
from twistedcaldav.directory.calendaruserproxy import CalendarUserProxyDatabase
from twistedcaldav.sqlprops import SQLPropertiesDatabase
from twistedcaldav.static import CalDAVFile, CalendarHomeFile
from twistedcaldav.upgrade import UpgradeXattrsToSqlite, UpgradePrincipalCollectionInMemory, UpgradeError
import os
import twistedcaldav.test.util
import xattr

class ProxyDBUpgradeTests(twistedcaldav.test.util.TestCase):
    
    def setUpInitialStates(self):
        
        self.setUpOldDocRoot()
        self.setUpOldDocRootWithoutDB()
        self.setUpNewDocRoot()
        
        self.setUpNewDataRoot()
        self.setUpDataRootWithProxyDB()

    def setUpOldDocRoot(self):
        
        # Set up doc root
        self.olddocroot = self.mktemp()
        os.mkdir(self.olddocroot)

        principals = os.path.join(self.olddocroot, "principals")
        os.mkdir(principals)
        os.mkdir(os.path.join(principals, "__uids__"))
        os.mkdir(os.path.join(principals, "users"))
        os.mkdir(os.path.join(principals, "groups"))
        os.mkdir(os.path.join(principals, "locations"))
        os.mkdir(os.path.join(principals, "resources"))
        os.mkdir(os.path.join(principals, "sudoers"))
        os.mkdir(os.path.join(self.olddocroot, "calendars"))

        proxyDB = CalendarUserProxyDatabase(principals)
        proxyDB._db()
        os.rename(
            os.path.join(principals, CalendarUserProxyDatabase.dbFilename),
            os.path.join(principals, CalendarUserProxyDatabase.dbOldFilename),
        )

    def setUpOldDocRootWithoutDB(self):
        
        # Set up doc root
        self.olddocrootnodb = self.mktemp()
        os.mkdir(self.olddocrootnodb)

        principals = os.path.join(self.olddocrootnodb, "principals")
        os.mkdir(principals)
        os.mkdir(os.path.join(principals, "__uids__"))
        os.mkdir(os.path.join(principals, "users"))
        os.mkdir(os.path.join(principals, "groups"))
        os.mkdir(os.path.join(principals, "locations"))
        os.mkdir(os.path.join(principals, "resources"))
        os.mkdir(os.path.join(principals, "sudoers"))
        os.mkdir(os.path.join(self.olddocrootnodb, "calendars"))

    def setUpNewDocRoot(self):
        
        # Set up doc root
        self.newdocroot = self.mktemp()
        os.mkdir(self.newdocroot)

        os.mkdir(os.path.join(self.newdocroot, "calendars"))

    def setUpNewDataRoot(self):
        
        # Set up data root
        self.newdataroot = self.mktemp()
        os.mkdir(self.newdataroot)

    def setUpDataRootWithProxyDB(self):
        
        # Set up data root
        self.existingdataroot = self.mktemp()
        os.mkdir(self.existingdataroot)

        proxyDB = CalendarUserProxyDatabase(self.existingdataroot)
        proxyDB._db()

    def test_normalUpgrade(self):
        """
        Test the behavior of normal upgrade from old server to new.
        """

        self.setUpInitialStates()

        config.DocumentRoot = self.olddocroot
        config.DataRoot = self.newdataroot
        
        # Check pre-conditions
        self.assertTrue(os.path.exists(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.isdir(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.exists(os.path.join(config.DocumentRoot, "principals", CalendarUserProxyDatabase.dbOldFilename)))
        self.assertFalse(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

        UpgradePrincipalCollectionInMemory.doUpgrade()
        
        # Check post-conditions
        self.assertFalse(os.path.exists(os.path.join(config.DocumentRoot, "principals",)))
        self.assertTrue(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

    def test_partialUpgrade(self):
        """
        Test the behavior of a partial upgrade (one where /principals exists but the proxy db does not) from old server to new.
        """

        self.setUpInitialStates()

        config.DocumentRoot = self.olddocrootnodb
        config.DataRoot = self.newdataroot
        
        # Check pre-conditions
        self.assertTrue(os.path.exists(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.isdir(os.path.join(config.DocumentRoot, "principals")))
        self.assertFalse(os.path.exists(os.path.join(config.DocumentRoot, "principals", CalendarUserProxyDatabase.dbOldFilename)))
        self.assertFalse(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

        UpgradePrincipalCollectionInMemory.doUpgrade()
        
        # Check post-conditions
        self.assertFalse(os.path.exists(os.path.join(config.DocumentRoot, "principals",)))
        self.assertFalse(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

    def test_noUpgrade(self):
        """
        Test the behavior of running on a new server (i.e. no upgrade needed).
        """

        self.setUpInitialStates()

        config.DocumentRoot = self.newdocroot
        config.DataRoot = self.existingdataroot
        
        # Check pre-conditions
        self.assertFalse(os.path.exists(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

        UpgradePrincipalCollectionInMemory.doUpgrade()
        
        # Check post-conditions
        self.assertFalse(os.path.exists(os.path.join(config.DocumentRoot, "principals",)))
        self.assertTrue(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

    def test_failedUpgrade(self):
        """
        Test the behavior of failed upgrade from old server to new where proxy DB exists in two locations.
        """

        self.setUpInitialStates()

        config.DocumentRoot = self.olddocroot
        config.DataRoot = self.existingdataroot
        
        # Check pre-conditions
        self.assertTrue(os.path.exists(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.isdir(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.exists(os.path.join(config.DocumentRoot, "principals", CalendarUserProxyDatabase.dbOldFilename)))
        self.assertTrue(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

        self.assertRaises(UpgradeError, UpgradePrincipalCollectionInMemory.doUpgrade)
        
        # Check post-conditions
        self.assertTrue(os.path.exists(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.isdir(os.path.join(config.DocumentRoot, "principals")))
        self.assertTrue(os.path.exists(os.path.join(config.DocumentRoot, "principals", CalendarUserProxyDatabase.dbOldFilename)))
        self.assertTrue(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))

class UpgradeSQLProps (twistedcaldav.test.util.TestCase):
    """
    SQL properties tests
    """
    data_dir = os.path.join(os.path.dirname(__file__), "data")

    props = (
        davxml.DisplayName.fromString("My Name"),
        davxml.ACL(
            davxml.ACE(
                davxml.Principal(davxml.Authenticated()),
                davxml.Grant(davxml.Privilege(davxml.Read())),
                davxml.Protected(),
            ),
        ),
        caldavxml.CalendarDescription.fromString("My Calendar"),
    )
    
    def setUp(self):
        
        super(UpgradeSQLProps, self).setUp()

        # First setup an xattr document root
        self._setUpTestDocuments()

        # Set to use sqlite prop store
        config.PropertyStore = "SQL"
        config.updatePropertyStore()
        
        # Do the upgrade
        UpgradeXattrsToSqlite.doUpgrade()
        
    def _setUpTestDocuments(self):

        config.PropertyStore = "xattr"
        config.updatePropertyStore()

        self.xattr_root, _ignore_url = self.mkdtemp("docs")
        config.DocumentRoot = self.xattr_root
        os.mkdir(os.path.join(self.xattr_root, "calendars"))
        os.mkdir(os.path.join(self.xattr_root, "calendars", "__uids__"))
        
        os.mkdir(os.path.join(self.xattr_root, "calendars", "__uids__", "12"))
        os.mkdir(os.path.join(self.xattr_root, "calendars", "__uids__", "12", "34"))
        self.user_home = os.path.join(self.xattr_root, "calendars", "__uids__", "12", "34", "12345-6789-10")
        os.mkdir(self.user_home)
        
        self.user_calendar = os.path.join(self.user_home, "calendar")
        os.mkdir(self.user_calendar)

        self.user_event = os.path.join(self.user_calendar, "12345.ics")
        f = open(self.user_event, "w")
        f.close()
        
        # xattrs on user home
        resource = CalDAVFile(self.user_home)
        resource.deadProperties().set(davxml.QuotaAvailableBytes.fromString("1000"))
        resource.deadProperties().set(davxml.QuotaUsedBytes.fromString("500"))
        
        # xattrs on user calendar
        resource = CalDAVFile(self.user_calendar)
        resource.deadProperties().set(davxml.ResourceType.calendar) #@UndefinedVariable
        resource.deadProperties().set(davxml.DisplayName.fromString("A Calendar"))
        resource.deadProperties().set(caldavxml.CalendarDescription.fromString("A Calendar"))
        resource.deadProperties().set(customxml.GETCTag.fromString("2008-06-30T13:00:00-123456"))
        
        # xattr on calendar resource
        resource = CalDAVFile(self.user_event)
        resource.deadProperties().set(davxml.GETContentType.fromString("text/calendar"))
        resource.deadProperties().set(TwistedGETContentMD5.fromString("ABCDEF-12345"))
        
    def _testProperty(self, path, prop, description = ""):

        if path == self.user_home:
            class DummyCalendarHomeFile(CalendarHomeFile):
                
                def __init__(self, path):
                    CalDAVFile.__init__(self, path)

            resource = DummyCalendarHomeFile(path)
        else:
            resource = CalDAVFile(path)

        self.assertTrue(resource.deadProperties().contains(prop.qname()),
                        msg="Could not find property %s %r." % (description, prop,))
        self.assertTrue(resource.deadProperties().get(prop.qname()) == prop,
                        msg="Could not get property %s %r." % (description, prop,))
    
    def _testNoXattrs(self, path):
        
        x = xattr.xattr(path)
        self.assertTrue(len(x) == 0)

    def test_root(self):
        self.assertFalse(os.path.exists(os.path.join(self.xattr_root, SQLPropertiesDatabase.dbFilename)))
        self._testNoXattrs(self.xattr_root)

    def test_calendars(self):
        self.assertFalse(os.path.exists(os.path.join(self.xattr_root, "calendars", SQLPropertiesDatabase.dbFilename)))
        self._testNoXattrs(os.path.join(self.xattr_root, "calendars"))

    def test_uids(self):
        self.assertFalse(os.path.exists(os.path.join(self.xattr_root, "calendars", "__uids__", SQLPropertiesDatabase.dbFilename)))
        self._testNoXattrs(os.path.join(self.xattr_root, "calendars", "__uids__"))

    def test_hashed(self):
        self.assertFalse(os.path.exists(os.path.join(self.xattr_root, "calendars", "__uids__", "12", SQLPropertiesDatabase.dbFilename)))
        self._testNoXattrs(os.path.join(self.xattr_root, "calendars", "__uids__", "12"))
        self.assertFalse(os.path.exists(os.path.join(self.xattr_root, "calendars", "__uids__", "12", "34", SQLPropertiesDatabase.dbFilename)))
        self._testNoXattrs(os.path.join(self.xattr_root, "calendars", "__uids__", "12", "34"))

    def test_user_home(self):
        self.assertTrue(os.path.exists(os.path.join(self.user_home, SQLPropertiesDatabase.dbFilename)))
        self._testProperty(self.user_home, davxml.QuotaAvailableBytes.fromString("1000"), "on user home") #@UndefinedVariable
        self._testProperty(self.user_home, davxml.QuotaUsedBytes.fromString("500"), "on user home")
        self._testNoXattrs(self.user_home)

    def test_user_calendar(self):
        self.assertTrue(os.path.exists(os.path.join(self.user_home, SQLPropertiesDatabase.dbFilename)))
        self.assertTrue(os.path.exists(os.path.join(self.user_calendar, SQLPropertiesDatabase.dbFilename)))
        self._testProperty(self.user_calendar, davxml.ResourceType.calendar, "on user calendar") #@UndefinedVariable
        self._testProperty(self.user_calendar, davxml.DisplayName.fromString("A Calendar"), "on user calendar")
        self._testProperty(self.user_calendar, caldavxml.CalendarDescription.fromString("A Calendar"), "on user calendar")
        self._testProperty(self.user_calendar, customxml.GETCTag.fromString("2008-06-30T13:00:00-123456"), "on user calendar")
        self._testNoXattrs(self.user_calendar)

    def test_user_event(self):
        self.assertTrue(os.path.exists(os.path.join(self.user_calendar, SQLPropertiesDatabase.dbFilename)))
        self._testProperty(self.user_event, davxml.GETContentType.fromString("text/calendar"), "on user event")
        self._testProperty(self.user_event, TwistedGETContentMD5.fromString("ABCDEF-12345"), "on user event")
        self._testNoXattrs(self.user_event)
