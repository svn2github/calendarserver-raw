##
# Copyright (c) 2009 Apple Inc. All rights reserved.
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
from twistedcaldav.directory.augment import AugmentXMLDB, AugmentSqliteDB
from twisted.python.filepath import FilePath
from twisted.internet.defer import inlineCallbacks
import os

xmlFile = FilePath(os.path.join(os.path.dirname(__file__), "augments.xml"))

testRecords = (
    {"guid":"D11F03A0-97EA-48AF-9A6C-FAC7F3975766", "enabled":True,  "hostedAt":"", "enabledForCalendaring":False, "autoSchedule":False, "calendarUserAddresses":set()},
    {"guid":"6423F94A-6B76-4A3A-815B-D52CFD77935D", "enabled":True,  "hostedAt":"", "enabledForCalendaring":True, "autoSchedule":False, "calendarUserAddresses":set(("mailto:wsanchez@example.com",))},
    {"guid":"5A985493-EE2C-4665-94CF-4DFEA3A89500", "enabled":False, "hostedAt":"", "enabledForCalendaring":False, "autoSchedule":False, "calendarUserAddresses":set()},
    {"guid":"8B4288F6-CC82-491D-8EF9-642EF4F3E7D0", "enabled":True,  "hostedAt":"", "enabledForCalendaring":False, "autoSchedule":False, "calendarUserAddresses":set()},
    {"guid":"5FF60DAD-0BDE-4508-8C77-15F0CA5C8DD1", "enabled":True,  "hostedAt":"00001", "enabledForCalendaring":False, "autoSchedule":False, "calendarUserAddresses":set()},
    {"guid":"543D28BA-F74F-4D5F-9243-B3E3A61171E5", "enabled":True,  "hostedAt":"00002", "enabledForCalendaring":False, "autoSchedule":False, "calendarUserAddresses":set()},
    {"guid":"6A73326A-F781-47E7-A9F8-AF47364D4152", "enabled":True,  "hostedAt":"00002", "enabledForCalendaring":True, "autoSchedule":True, "calendarUserAddresses":set(("mailto:usera@example.com", "mailto:user.a@example.com", "mailto:user_a@example.com",))},
)

class AugmentTests(TestCase):

    @inlineCallbacks
    def _checkRecord(self, db, items):
        
        record = (yield db.getAugmentRecord(items["guid"]))
        self.assertTrue(record is not None)
        
        for k,v in items.iteritems():
            self.assertEqual(getattr(record, k), v)

    @inlineCallbacks
    def _checkNoRecord(self, db, guid):
        
        record = (yield db.getAugmentRecord(guid))
        self.assertTrue(record is None)

class AugmentXMLTests(AugmentTests):

    @inlineCallbacks
    def test_read(self):
        
        db = AugmentXMLDB(xmlFile)

        for item in testRecords:
            yield self._checkRecord(db, item)

        yield self._checkNoRecord(db, "D11F03A0-97EA-48AF-9A6C-FAC7F3975767")

class AugmentSqliteTests(AugmentTests):

    @inlineCallbacks
    def test_read(self):
        
        db = AugmentSqliteDB(self.mktemp())

        dbxml = AugmentXMLDB(xmlFile)
        for record in dbxml.db.values():
            yield db.addAugmentRecord(record)

        for item in testRecords:
            yield self._checkRecord(db, item)

        yield self._checkNoRecord(db, "D11F03A0-97EA-48AF-9A6C-FAC7F3975767")
