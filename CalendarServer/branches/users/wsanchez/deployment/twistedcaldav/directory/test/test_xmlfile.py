##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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

import os

from twisted.python.filepath import FilePath

from twistedcaldav.directory import augment
from twistedcaldav.directory.directory import DirectoryService
import twistedcaldav.directory.test.util
from twistedcaldav.directory.xmlfile import XMLDirectoryService

xmlFile = FilePath(os.path.join(os.path.dirname(__file__), "accounts.xml"))
augmentsFile = FilePath(os.path.join(os.path.dirname(__file__), "augments.xml"))
proxiesFile = FilePath(os.path.join(os.path.dirname(__file__), "proxies.xml"))

# FIXME: Add tests for GUID hooey, once we figure out what that means here

class XMLFileBase(object):
    recordTypes = set((
        DirectoryService.recordType_users,
        DirectoryService.recordType_groups,
        DirectoryService.recordType_locations,
        DirectoryService.recordType_resources
    ))

    users = {
        "admin"      : { "password": "nimda",      "guid": "D11F03A0-97EA-48AF-9A6C-FAC7F3975766", "addresses": () },
        "wsanchez"   : { "password": "zehcnasw",   "guid": "6423F94A-6B76-4A3A-815B-D52CFD77935D", "addresses": ("mailto:wsanchez@example.com",) },
        "cdaboo"     : { "password": "oobadc",     "guid": "5A985493-EE2C-4665-94CF-4DFEA3A89500", "addresses": ("mailto:cdaboo@example.com",)   },
        "lecroy"     : { "password": "yorcel",     "guid": "8B4288F6-CC82-491D-8EF9-642EF4F3E7D0", "addresses": ("mailto:lecroy@example.com",)   },
        "dreid"      : { "password": "dierd",      "guid": "5FF60DAD-0BDE-4508-8C77-15F0CA5C8DD1", "addresses": ("mailto:dreid@example.com",)    },
        "nocalendar" : { "password": "radnelacon", "guid": "543D28BA-F74F-4D5F-9243-B3E3A61171E5", "addresses": () },
        "user01"     : { "password": "01user",     "guid": None                                  , "addresses": () },
        "user02"     : { "password": "02user",     "guid": None                                  , "addresses": () },
    }

    groups = {
        "admin"      : { "password": "admin",       "guid": None, "addresses": (), "members": ((DirectoryService.recordType_groups, "managers"),)                                      },
        "managers"   : { "password": "managers",    "guid": None, "addresses": (), "members": ((DirectoryService.recordType_users , "lecroy"),)                                         },
        "grunts"     : { "password": "grunts",      "guid": None, "addresses": (), "members": ((DirectoryService.recordType_users , "wsanchez"),
                                                                                               (DirectoryService.recordType_users , "cdaboo"),
                                                                                               (DirectoryService.recordType_users , "dreid")) },
        "right_coast": { "password": "right_coast", "guid": None, "addresses": (), "members": ((DirectoryService.recordType_users , "cdaboo"),)                                         },
        "left_coast" : { "password": "left_coast",  "guid": None, "addresses": (), "members": ((DirectoryService.recordType_users , "wsanchez"),
                                                                                               (DirectoryService.recordType_users , "dreid"),
                                                                                               (DirectoryService.recordType_users , "lecroy")) },
        "both_coasts": { "password": "both_coasts", "guid": None, "addresses": (), "members": ((DirectoryService.recordType_groups, "right_coast"),
                                                                                               (DirectoryService.recordType_groups, "left_coast"))           },
        "recursive1_coasts":  { "password": "recursive1_coasts",  "guid": None, "addresses": (), "members": ((DirectoryService.recordType_groups, "recursive2_coasts"),
                                                                                               (DirectoryService.recordType_users, "wsanchez"))           },
        "recursive2_coasts":  { "password": "recursive2_coasts",  "guid": None, "addresses": (), "members": ((DirectoryService.recordType_groups, "recursive1_coasts"),
                                                                                               (DirectoryService.recordType_users, "cdaboo"))           },
        "non_calendar_group": { "password": "non_calendar_group", "guid": None, "addresses": (), "members": ((DirectoryService.recordType_users , "cdaboo"),
                                                                                               (DirectoryService.recordType_users , "lecroy"))           },
    }

    locations = {
        "mercury": { "password": "mercury", "guid": None, "addresses": ("mailto:mercury@example.com",) },
        "gemini" : { "password": "gemini",  "guid": None, "addresses": ("mailto:gemini@example.com",)  },
        "apollo" : { "password": "apollo",  "guid": None, "addresses": ("mailto:apollo@example.com",)  },
        "orion"  : { "password": "orion",   "guid": None, "addresses": ("mailto:orion@example.com",)  },
    }

    resources = {
        "transporter"        : { "password": "transporter",        "guid": None,                 "addresses": ("mailto:transporter@example.com",)        },
        "ftlcpu"             : { "password": "ftlcpu",             "guid": None,                 "addresses": ("mailto:ftlcpu@example.com",)             },
        "non_calendar_proxy" : { "password": "non_calendar_proxy", "guid": "non_calendar_proxy", "addresses": ("mailto:non_calendar_proxy@example.com",) },
    }

    def xmlFile(self):
        if not hasattr(self, "_xmlFile"):
            self._xmlFile = FilePath(self.mktemp())
            xmlFile.copyTo(self._xmlFile)
        return self._xmlFile

    def augmentsFile(self):
        if not hasattr(self, "_augmentsFile"):
            self._augmentsFile = FilePath(self.mktemp())
            augmentsFile.copyTo(self._augmentsFile)
        return self._augmentsFile

class XMLFile (
    XMLFileBase,
    twistedcaldav.directory.test.util.BasicTestCase,
    twistedcaldav.directory.test.util.DigestTestCase
):
    """
    Test XML file based directory implementation.
    """
    def service(self):
        directory = XMLDirectoryService(self.xmlFile())
        augment.AugmentService = augment.AugmentXMLDB(xmlFiles=(self.augmentsFile().path,))
        return directory

    def test_changedXML(self):
        service = self.service()

        self.xmlFile().open("w").write(
"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE accounts SYSTEM "accounts.dtd">
<accounts realm="Test Realm">
  <user>
    <uid>admin</uid>
    <guid>admin</guid>
    <password>nimda</password>
    <name>Super User</name>
  </user>
</accounts>
"""
        )
        for recordType, expectedRecords in (
            ( DirectoryService.recordType_users     , ("admin",) ),
            ( DirectoryService.recordType_groups    , ()         ),
            ( DirectoryService.recordType_locations , ()         ),
            ( DirectoryService.recordType_resources , ()         ),
        ):
            self.assertEquals(
                set(r.shortName for r in service.listRecords(recordType)),
                set(expectedRecords)
            )

    def test_okAutoSchedule(self):
        service = self.service()

        self.xmlFile().open("w").write(
"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE accounts SYSTEM "accounts.dtd">
<accounts realm="Test Realm">
  <location>
    <uid>my office</uid>
    <guid>myoffice</guid>
    <password>nimda</password>
    <name>Super User</name>
  </location>
</accounts>
"""
        )
        self.augmentsFile().open("w").write(
"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE accounts SYSTEM "accounts.dtd">
<augments>
  <record>
    <uid>myoffice</uid>
    <enable>true</enable>
    <enable-calendar>true</enable-calendar>
    <auto-schedule>true</auto-schedule>
  </record>
</augments>
"""
        )
        augment.AugmentService.refresh()

        for recordType, expectedRecords in (
            ( DirectoryService.recordType_users     , ()             ),
            ( DirectoryService.recordType_groups    , ()             ),
            ( DirectoryService.recordType_locations , ("my office",) ),
            ( DirectoryService.recordType_resources , ()             ),
        ):
            self.assertEquals(
                set(r.shortName for r in service.listRecords(recordType)),
                set(expectedRecords)
            )
        self.assertTrue(service.recordWithShortName(DirectoryService.recordType_locations, "my office").autoSchedule)


    def test_okDisableCalendar(self):
        service = self.service()

        self.xmlFile().open("w").write(
"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE accounts SYSTEM "accounts.dtd">
<accounts realm="Test Realm">
  <group>
    <uid>enabled</uid>
    <password>enabled</password>
    <name>Enabled</name>
  </group>
  <group>
    <uid>disabled</uid>
    <password>disabled</password>
    <name>Disabled</name>
  </group>
</accounts>
"""
        )
        
        for recordType, expectedRecords in (
            ( DirectoryService.recordType_users     , ()                       ),
            ( DirectoryService.recordType_groups    , ("enabled", "disabled")  ),
            ( DirectoryService.recordType_locations , ()                       ),
            ( DirectoryService.recordType_resources , ()                       ),
        ):
            self.assertEquals(
                set(r.shortName for r in service.listRecords(recordType)),
                set(expectedRecords)
            )
        self.assertFalse(service.recordWithShortName(DirectoryService.recordType_groups, "enabled").enabledForCalendaring)
        self.assertFalse(service.recordWithShortName(DirectoryService.recordType_groups, "disabled").enabledForCalendaring)
