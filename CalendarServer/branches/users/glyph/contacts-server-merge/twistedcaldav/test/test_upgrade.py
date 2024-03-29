##
# Copyright (c) 2008-2010 Apple Inc. All rights reserved.
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

from twistedcaldav.config import config
from twistedcaldav.directory.calendaruserproxy import CalendarUserProxyDatabase
from twistedcaldav.directory.xmlfile import XMLDirectoryService
from twistedcaldav.directory.resourceinfo import ResourceInfoDatabase
from twistedcaldav.mail import MailGatewayTokensDatabase
from twistedcaldav.upgrade import UpgradeError, upgradeData, updateFreeBusySet
from twistedcaldav.test.util import TestCase
from calendarserver.tools.util import getDirectory

import hashlib
import os, zlib, cPickle

freeBusyAttr = "WebDAV:{urn:ietf:params:xml:ns:caldav}calendar-free-busy-set"
cTagAttr = "WebDAV:{http:%2F%2Fcalendarserver.org%2Fns%2F}getctag"
md5Attr = "WebDAV:{http:%2F%2Ftwistedmatrix.com%2Fxml_namespace%2Fdav%2F}getcontentmd5"


class ProxyDBUpgradeTests(TestCase):
    todo = "upgrade.py needs to be fixed"
    
    def setUpXMLDirectory(self):
        xmlFile = os.path.join(os.path.dirname(os.path.dirname(__file__)),
            "directory", "test", "accounts.xml")
        config.DirectoryService.params.xmlFile = xmlFile

        xmlAugmentsFile = os.path.join(os.path.dirname(os.path.dirname(__file__)),
            "directory", "test", "augments.xml")
        config.AugmentService.params.xmlFiles = (xmlAugmentsFile,)


    def setUpInitialStates(self):
        self.setUpXMLDirectory()

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

        upgradeData(config)
        
        # Check post-conditions
        self.assertFalse(os.path.exists(os.path.join(config.DocumentRoot, "principals",)))
        self.assertTrue(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))


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

        upgradeData(config)
        
        # Check post-conditions
        self.assertFalse(os.path.exists(os.path.join(config.DocumentRoot, "principals",)))
        self.assertTrue(os.path.exists(os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)))


    def test_freeBusyUpgrade(self):
        """
        Test the updating of calendar-free-busy-set xattrs on inboxes
        """

        self.setUpInitialStates()
        directory = getDirectory()

        #
        # Verify these values require no updating:
        #

        # Uncompressed XML
        value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/BB05932F-DCE7-4195-9ED4-0896EAFF3B0B/calendar</href>\r\n</calendar-free-busy-set>\r\n"
        self.assertEquals(updateFreeBusySet(value, directory), None)

        # Zlib compressed XML
        value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/BB05932F-DCE7-4195-9ED4-0896EAFF3B0B/calendar</href>\r\n</calendar-free-busy-set>\r\n"
        value = zlib.compress(value)
        self.assertEquals(updateFreeBusySet(value, directory), None)

        # Pickled XML
        value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/BB05932F-DCE7-4195-9ED4-0896EAFF3B0B/calendar</href>\r\n</calendar-free-busy-set>\r\n"
        doc = davxml.WebDAVDocument.fromString(value)
        value = cPickle.dumps(doc.root_element)
        self.assertEquals(updateFreeBusySet(value, directory), None)


        #
        # Verify these values do require updating:
        #

        expected = "<?xml version='1.0' encoding='UTF-8'?><calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/6423F94A-6B76-4A3A-815B-D52CFD77935D/calendar/</href>\r\n</calendar-free-busy-set>"

        # Uncompressed XML
        value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/users/wsanchez/calendar</href>\r\n</calendar-free-busy-set>\r\n"
        newValue = updateFreeBusySet(value, directory)
        newValue = zlib.decompress(newValue)
        self.assertEquals(newValue, expected)

        # Zlib compressed XML
        value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/users/wsanchez/calendar</href>\r\n</calendar-free-busy-set>\r\n"
        value = zlib.compress(value)
        newValue = updateFreeBusySet(value, directory)
        newValue = zlib.decompress(newValue)
        self.assertEquals(newValue, expected)

        # Pickled XML
        value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/users/wsanchez/calendar</href>\r\n</calendar-free-busy-set>\r\n"
        doc = davxml.WebDAVDocument.fromString(value)
        value = cPickle.dumps(doc.root_element)
        newValue = updateFreeBusySet(value, directory)
        newValue = zlib.decompress(newValue)
        self.assertEquals(newValue, expected)


        #
        # Shortname not in directory, return empty string
        #

        expected = "<?xml version='1.0' encoding='UTF-8'?><calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'/>"
        value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/users/nonexistent/calendar</href>\r\n</calendar-free-busy-set>\r\n"
        newValue = updateFreeBusySet(value, directory)
        newValue = zlib.decompress(newValue)
        self.assertEquals(newValue, expected)


    def verifyDirectoryComparison(self, before, after, reverify=False):
        """
        Verify that the hierarchy described by "before", when upgraded, matches
        the hierarchy described by "after".

        @param before: a dictionary of the format accepted by L{TestCase.createHierarchy}

        @param after: a dictionary of the format accepted by L{TestCase.createHierarchy}

        @param reverify: if C{True}, re-verify the hierarchy by upgrading a
            second time and re-verifying the root again.

        @raise twisted.trial.unittest.FailTest: if the test fails.

        @return: C{None}
        """
        root = self.createHierarchy(before)

        config.DocumentRoot = root
        config.DataRoot = root

        upgradeData(config)
        self.assertTrue(self.verifyHierarchy(root, after))

        if reverify:
            # Ensure that repeating the process doesn't change anything
            upgradeData(config)
            self.assertTrue(self.verifyHierarchy(root, after))


    def test_removeNotificationDirectories(self):
        """
        The upgrade process should remove unused notification directories in
        users' calendar homes, as well as the XML files found therein.
        """
        self.setUpXMLDirectory()

        before = {
            "calendars": {
                "users": {
                    "wsanchez": {
                        "calendar" : {},
                        "notifications": {
                            "sample-notification.xml": {
                                "@contents":  "<?xml version='1.0'>\n<should-be-ignored />"
                            }
                        }
                    }
                }
            }
        }

        after = {
            "calendars" : {
                "__uids__" : {
                    "64" : {
                        "23" : {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935D" : {
                                "calendar": {},
                            }
                        }
                    }
                }
            },
            ".calendarserver_version" : {
                "@contents" : "1",
            },
            CalendarUserProxyDatabase.dbFilename : { "@contents" : None },
            MailGatewayTokensDatabase.dbFilename : { "@contents" : None },
            ResourceInfoDatabase.dbFilename : { "@contents" : None },
            "tasks" : {"incoming" : {}}
        }

        self.verifyDirectoryComparison(before, after)


    def test_calendarsUpgradeWithTypes(self):
        """
        Verify that calendar homes in the /calendars/<type>/<shortname>/ form
        are upgraded to /calendars/__uids__/XX/YY/<guid> form
        """

        self.setUpXMLDirectory()

        before = {
            "calendars" :
            {
                "users" :
                {
                    "wsanchez" :
                    {
                        "calendar" :
                        {
                            "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                            {
                                "@contents" : event01_before,
                                "@xattrs" :
                                {
                                    md5Attr : "12345",
                                },
                            },
                            "@xattrs" :
                            {
                                cTagAttr : "12345",
                            },
                        },
                        "inbox" :
                        {
                            "@xattrs" :
                            {
                                # Pickled XML Doc
                                freeBusyAttr : cPickle.dumps(davxml.WebDAVDocument.fromString("<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/users/wsanchez/calendar</href>\r\n</calendar-free-busy-set>\r\n").root_element),
                            },
                        },
                    },
                },
                "groups" :
                {
                    "managers" :
                    {
                        "calendar" :
                        {
                        },
                    },
                },
            },
            "principals" :
            {
                CalendarUserProxyDatabase.dbOldFilename :
                {
                    "@contents" : "",
                }
            }
        }

        after = {
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935D" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_after,
                                        "@xattrs" :
                                        {
                                            md5Attr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<getcontentmd5 xmlns='http://twistedmatrix.com/xml_namespace/dav/'>%s</getcontentmd5>\r\n" % (event01_after_md5,)),
                                        },
                                    },
                                    "@xattrs" :
                                    {
                                        cTagAttr : isValidCTag, # method below
                                    },
                                },
                                "inbox" :
                                {
                                    "@xattrs" :
                                    {
                                        freeBusyAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?><calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/6423F94A-6B76-4A3A-815B-D52CFD77935D/calendar/</href>\r\n</calendar-free-busy-set>"),
                                    },
                                },
                            },
                        },
                    },
                    "9F" :
                    {
                        "F6" :
                        {
                            "9FF60DAD-0BDE-4508-8C77-15F0CA5C8DD1" :
                            {
                                "calendar" :
                                {
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
#            ResourceInfoDatabase.dbFilename :
#            {
#                "@contents" : None,
#            }
        }

        self.verifyDirectoryComparison(before, after, reverify=True)


    def test_calendarsUpgradeWithOrphans(self):
        """
        Verify that calendar homes in the /calendars/<type>/<shortname>/ form
        whose records don't exist are moved into dataroot/archived/
        """

        self.setUpXMLDirectory()

        before = {
            "calendars" :
            {
                "users" :
                {
                    "unknownuser" :
                    {
                    },
                },
                "groups" :
                {
                    "unknowngroup" :
                    {
                    },
                },
            },
            "principals" :
            {
                CalendarUserProxyDatabase.dbOldFilename :
                {
                    "@contents" : "",
                }
            }
        }

        after = {
            "archived" :
            {
                "unknownuser" :
                {
                },
                "unknowngroup" :
                {
                },
            },
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
            "calendars" :
            {
                "__uids__" :
                {
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
            ResourceInfoDatabase.dbFilename :
            {
                "@contents" : None,
            }
        }

        self.verifyDirectoryComparison(before, after, reverify=True)


    def test_calendarsUpgradeWithDuplicateOrphans(self):
        """
        Verify that calendar homes in the /calendars/<type>/<shortname>/ form
        whose records don't exist are moved into dataroot/archived/
        """

        self.setUpXMLDirectory()

        before = {
            "archived" :
            {
                "unknownuser" :
                {
                },
                "unknowngroup" :
                {
                },
            },
            "calendars" :
            {
                "users" :
                {
                    "unknownuser" :
                    {
                    },
                },
                "groups" :
                {
                    "unknowngroup" :
                    {
                    },
                },
            },
            "principals" :
            {
                CalendarUserProxyDatabase.dbOldFilename :
                {
                    "@contents" : "",
                }
            }
        }

        after = {
            "archived" :
            {
                "unknownuser" :
                {
                },
                "unknowngroup" :
                {
                },
                "unknownuser.1" :
                {
                },
                "unknowngroup.1" :
                {
                },
            },
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
            "calendars" :
            {
                "__uids__" :
                {
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
            ResourceInfoDatabase.dbFilename :
            {
                "@contents" : None,
            }
        }

        self.verifyDirectoryComparison(before, after, reverify=True)


    def test_calendarsUpgradeWithUnknownFiles(self):
        """
        Unknown files, including .DS_Store files at any point in the hierarchy,
        as well as non-directory in a user's calendar home, will be ignored and not
        interrupt an upgrade.
        """

        self.setUpXMLDirectory()

        ignoredUIDContents = {
            "64" : {
                "23" : {
                    "6423F94A-6B76-4A3A-815B-D52CFD77935D" : {
                        "calendar" : {
                        },
                        "garbage.ics" : {
                            "@contents": "Oops, not actually an ICS file.",
                        },
                        "other-file.txt": {
                            "@contents": "Also not a calendar collection."
                        }
                    }
                }
            },
            ".DS_Store" : {
                "@contents" : "",
            }
        }

        before = {
            ".DS_Store" :
            {
                "@contents" : "",
            },
            "calendars" :
            {
                ".DS_Store" :
                {
                    "@contents" : "",
                },
                "__uids__" :ignoredUIDContents,
            },
            "principals" :
            {
                ".DS_Store" :
                {
                    "@contents" : "",
                },
                CalendarUserProxyDatabase.dbOldFilename :
                {
                    "@contents" : "",
                }
            }
        }

        after = {
            ".DS_Store" :
            {
                "@contents" : "",
            },
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
            "calendars" :
            {
                ".DS_Store" :
                {
                    "@contents" : "",
                },
                "__uids__" : ignoredUIDContents,
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
            ResourceInfoDatabase.dbFilename :
            {
                "@contents" : None,
            }
        }

        self.verifyDirectoryComparison(before, after, reverify=True)


    def test_calendarsUpgradeWithUIDs(self):
        """
        Verify that calendar homes in the /calendars/__uids__/<guid>/ form
        are upgraded to /calendars/__uids__/XX/YY/<guid>/ form
        """

        self.setUpXMLDirectory()

        before = {
            "calendars" :
            {
                "__uids__" :
                {
                    "6423F94A-6B76-4A3A-815B-D52CFD77935D" :
                    {
                        "calendar" :
                        {
                            "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                            {
                                "@contents" : event01_before,
                            },
                        },
                        "inbox" :
                        {
                            "@xattrs" :
                            {
                                # Plain XML
                                freeBusyAttr : "<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/users/wsanchez/calendar</href>\r\n</calendar-free-busy-set>\r\n",
                            },
                        },
                    },
                },
            },
            "principals" :
            {
                CalendarUserProxyDatabase.dbOldFilename :
                {
                    "@contents" : "",
                }
            }
        }

        after = {
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935D" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_after,
                                    },
                                    "@xattrs" :
                                    {
                                        cTagAttr : isValidCTag, # method below
                                    },
                                },
                                "inbox" :
                                {
                                    "@xattrs" :
                                    {
                                        freeBusyAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?><calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/6423F94A-6B76-4A3A-815B-D52CFD77935D/calendar/</href>\r\n</calendar-free-busy-set>"),
                                    },
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
#            ResourceInfoDatabase.dbFilename :
#            {
#                "@contents" : None,
#            }
        }

        self.verifyDirectoryComparison(before, after, reverify=True)


    def test_calendarsUpgradeWithUIDsMultilevel(self):
        """
        Verify that calendar homes in the /calendars/__uids__/XX/YY/<guid>/
        form are upgraded correctly in place
        """

        self.setUpXMLDirectory()

        before = {
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935D" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_before,
                                        "@xattrs" :
                                        {
                                            md5Attr : "12345",
                                        },
                                    },
                                    "@xattrs" :
                                    {
                                        "ignore" : "extra",
                                        cTagAttr : "12345",
                                    },
                                },
                                "inbox" :
                                {
                                    "@xattrs" :
                                    {
                                        # Zlib compressed XML
                                        freeBusyAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/users/wsanchez/calendar</href>\r\n</calendar-free-busy-set>\r\n"),
                                    },
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : "",
            }
        }

        after = {
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935D" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_after,
                                        "@xattrs" :
                                        {
                                            md5Attr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<getcontentmd5 xmlns='http://twistedmatrix.com/xml_namespace/dav/'>%s</getcontentmd5>\r\n" % (event01_after_md5,)),
                                        },
                                    },
                                    "@xattrs" :
                                    {
                                        "ignore" : "extra",
                                        cTagAttr : isValidCTag, # method below
                                    },
                                },
                                "inbox" :
                                {
                                    "@xattrs" :
                                    {
                                        freeBusyAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?><calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/6423F94A-6B76-4A3A-815B-D52CFD77935D/calendar/</href>\r\n</calendar-free-busy-set>"),
                                    },
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
#            ResourceInfoDatabase.dbFilename :
#            {
#                "@contents" : None,
#            }
        }

        self.verifyDirectoryComparison(before, after, reverify=True)

    def test_calendarsUpgradeWithNoChange(self):
        """
        Verify that calendar homes in the /calendars/__uids__/XX/YY/<guid>/
        form which require no changes are untouched
        """

        self.setUpXMLDirectory()

        before = {
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935D" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_after,
                                        "@xattrs" :
                                        {
                                            md5Attr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<getcontentmd5 xmlns='http://twistedmatrix.com/xml_namespace/dav/'>%s</getcontentmd5>\r\n" % (event01_after_md5,)),
                                        },
                                    },
                                    "@xattrs" :
                                    {
                                        "ignore" : "extra",
                                        cTagAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<getctag xmlns='http://calendarserver.org/ns/'>2009-02-25 14:34:34.703093</getctag>\r\n"),
                                    },
                                },
                                "inbox" :
                                {
                                    "@xattrs" :
                                    {
                                        # Zlib compressed XML
                                        freeBusyAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/6423F94A-6B76-4A3A-815B-D52CFD77935D/calendar/</href>\r\n</calendar-free-busy-set>\r\n"),
                                    },
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : "",
            }
        }

        after = {
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935D" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_after,
                                        "@xattrs" :
                                        {
                                            md5Attr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<getcontentmd5 xmlns='http://twistedmatrix.com/xml_namespace/dav/'>%s</getcontentmd5>\r\n" % (event01_after_md5,)),
                                        },
                                    },
                                    "@xattrs" :
                                    {
                                        "ignore" : "extra",
                                        cTagAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<getctag xmlns='http://calendarserver.org/ns/'>2009-02-25 14:34:34.703093</getctag>\r\n"),
                                    },
                                },
                                "inbox" :
                                {
                                    "@xattrs" :
                                    {
                                        freeBusyAttr : zlib.compress("<?xml version='1.0' encoding='UTF-8'?>\r\n<calendar-free-busy-set xmlns='urn:ietf:params:xml:ns:caldav'>\r\n  <href xmlns='DAV:'>/calendars/__uids__/6423F94A-6B76-4A3A-815B-D52CFD77935D/calendar/</href>\r\n</calendar-free-busy-set>\r\n"),
                                    },
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
#            ResourceInfoDatabase.dbFilename :
#            {
#                "@contents" : None,
#            }
        }

        self.verifyDirectoryComparison(before, after)


    def test_calendarsUpgradeWithError(self):
        """
        Verify that a problem with one resource doesn't stop the process, but
        also doesn't write the new version file
        """

        self.setUpXMLDirectory()

        before = {
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935E" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_before,
                                    },
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C73.ics" :
                                    {
                                        "@contents" : event02_broken,
                                    },
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : "",
            }
        }


        after = {
            "tasks" :
            {
                "incoming" :
                {
                },
            },
            "calendars" :
            {
                "__uids__" :
                {
                    "64" :
                    {
                        "23" :
                        {
                            "6423F94A-6B76-4A3A-815B-D52CFD77935E" :
                            {
                                "calendar" :
                                {
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C72.ics" :
                                    {
                                        "@contents" : event01_after,
                                    },
                                    "1E238CA1-3C95-4468-B8CD-C8A399F78C73.ics" :
                                    {
                                        "@contents" : event02_broken,
                                    },
                                },
                            },
                        },
                    },
                },
            },
            CalendarUserProxyDatabase.dbFilename :
            {
                "@contents" : None,
            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
#            ResourceInfoDatabase.dbFilename :
#            {
#                "@contents" : None,
#            }
        }


        root = self.createHierarchy(before)

        config.DocumentRoot = root
        config.DataRoot = root

        self.assertRaises(UpgradeError, upgradeData, config)
        self.assertTrue(self.verifyHierarchy(root, after))

    def test_migrateResourceInfo(self):
        # Fake getResourceInfo( )

        assignments = {
            'guid1' : (False, None, None),
            'guid2' : (True, 'guid1', None),
            'guid3' : (False, 'guid1', 'guid2'),
            'guid4' : (True, None, 'guid3'),
        }

        def _getResourceInfo(ignored):
            results = []
            for guid, info in assignments.iteritems():
                results.append( (guid, info[0], info[1], info[2]) )
            return results

        self.setUpInitialStates()
        # Override the normal getResourceInfo method with our own:
        XMLDirectoryService.getResourceInfo = _getResourceInfo

        before = { }
        after = {
            ".calendarserver_version" :
            {
                "@contents" : "1",
            },
#            CalendarUserProxyDatabase.dbFilename :
#            {
#                "@contents" : None,
#            },
            MailGatewayTokensDatabase.dbFilename :
            {
                "@contents" : None,
            },
#            ResourceInfoDatabase.dbFilename :
#            {
#                "@contents" : None,
#            }
        }
        root = self.createHierarchy(before)
        config.DocumentRoot = root
        config.DataRoot = root

        upgradeData(config)
        self.assertTrue(self.verifyHierarchy(root, after))

#        calendarUserProxyDatabase = CalendarUserProxyDatabase(root)
#        resourceInfoDatabase = ResourceInfoDatabase(root)
#
#        for guid, info in assignments.iteritems():
#
#            proxyGroup = "%s#calendar-proxy-write" % (guid,)
#            result = set([row[0] for row in calendarUserProxyDatabase._db_execute("select MEMBER from GROUPS where GROUPNAME = :1", proxyGroup)])
#            if info[1]:
#                self.assertTrue(info[1] in result)
#            else:
#                self.assertTrue(not result)
#
#            readOnlyProxyGroup = "%s#calendar-proxy-read" % (guid,)
#            result = set([row[0] for row in calendarUserProxyDatabase._db_execute("select MEMBER from GROUPS where GROUPNAME = :1", readOnlyProxyGroup)])
#            if info[2]:
#                self.assertTrue(info[2] in result)
#            else:
#                self.assertTrue(not result)
#
#            autoSchedule = resourceInfoDatabase._db_value_for_sql("select AUTOSCHEDULE from RESOURCEINFO where GUID = :1", guid)
#            autoSchedule = autoSchedule == 1
#            self.assertEquals(info[0], autoSchedule)



event01_before = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 3.0//EN
CALSCALE:GREGORIAN
BEGIN:VTIMEZONE
TZID:US/Pacific
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:PDT
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:PST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
SEQUENCE:2
TRANSP:OPAQUE
UID:1E238CA1-3C95-4468-B8CD-C8A399F78C71
DTSTART;TZID=US/Pacific:20090203T120000
ORGANIZER;CN="Cyrus":mailto:cdaboo@example.com
DTSTAMP:20090203T181924Z
SUMMARY:New Event
DESCRIPTION:This has \\" Bad Quotes \\" in it
ATTENDEE;CN="Wilfredo";CUTYPE=INDIVIDUAL;PARTSTAT=ACCEPTED:mailto:wsanchez
 @example.com
ATTENDEE;CN="Cyrus";CUTYPE=INDIVIDUAL;PARTSTAT=ACCEPTED;ROLE=REQ-PARTICI
 PANT:mailto:cdaboo@example.com
CREATED:20090203T181910Z
DTEND;TZID=US/Pacific:20090203T130000
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

event01_after = """BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//Apple Inc.//iCal 3.0//EN
BEGIN:VTIMEZONE
TZID:US/Pacific
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:PST
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:PDT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:1E238CA1-3C95-4468-B8CD-C8A399F78C71
DTSTART;TZID=US/Pacific:20090203T120000
DTEND;TZID=US/Pacific:20090203T130000
ATTENDEE;CN=Wilfredo Sanchez;CUTYPE=INDIVIDUAL;EMAIL=wsanchez@example.com;
 PARTSTAT=ACCEPTED:urn:uuid:6423F94A-6B76-4A3A-815B-D52CFD77935D
ATTENDEE;CN=Cyrus Daboo;CUTYPE=INDIVIDUAL;EMAIL=cdaboo@example.com;PARTSTA
 T=ACCEPTED;ROLE=REQ-PARTICIPANT:urn:uuid:5A985493-EE2C-4665-94CF-4DFEA3A89
 500
CREATED:20090203T181910Z
DESCRIPTION:This has " Bad Quotes " in it
DTSTAMP:20090203T181924Z
ORGANIZER;CN=Cyrus Daboo;EMAIL=cdaboo@example.com:urn:uuid:5A985493-EE2C-4
 665-94CF-4DFEA3A89500
SEQUENCE:2
SUMMARY:New Event
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")

event02_broken = "Invalid!"

event01_after_md5 = hashlib.md5(event01_after).hexdigest()

def isValidCTag(value):
    """
    Since ctag is generated from datetime.now(), let's make sure that at
    least the value is zlib compressed XML
    """
    try:
        value = zlib.decompress(value)
    except zlib.error:
        return False
    try:
        davxml.WebDAVDocument.fromString(value)
        return True
    except ValueError:
        return False
