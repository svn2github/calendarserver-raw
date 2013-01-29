##
# Copyright (c) 2013 Apple Inc. All rights reserved.
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
Generic directory service base implementation tests
"""

from zope.interface.verify import verifyObject, BrokenMethodImplementation

from twisted.trial import unittest

from twext.who.idirectory import IDirectoryService, IDirectoryRecord
from twext.who.idirectory import RecordType, FieldName
from twext.who.directory import DirectoryService, DirectoryRecord



class DirectoryServiceTest(unittest.TestCase):
    realmName = "xyzzy"

    def _testService(self):
        return DirectoryService(self.realmName)

    def test_interface(self):
        service = self._testService()
        try:
            verifyObject(IDirectoryService, service)
        except BrokenMethodImplementation, e:
            self.fail(e)

    def test_init(self):
        service = self._testService()
        self.assertEquals(service.realmName, self.realmName)

    def test_recordTypes(self):
        service = self._testService()
        self.assertEquals(
            set(service.recordTypes()),
            set(service.RecordTypeClass.iterconstants())
        )

    def test_recordFromQuery(self):
        raise NotImplementedError()

    test_recordFromQuery.todo = "Not implemented."



class DirectoryRecordTest(unittest.TestCase):
    realmName = "xyzzy"

    fields_wsanchez = {
        FieldName.recordType:     RecordType.user,
        FieldName.shortNames:     ("wsanchez",),
        FieldName.uid:            "wsanchez",
        FieldName.emailAddresses: ("wsanchez@calendarserver.org", "wsanchez@example.com")
    }

    fields_glyph = {
        FieldName.recordType:     RecordType.user,
        FieldName.shortNames:     ("glyph",),
        FieldName.uid:            "glyph",
        FieldName.emailAddresses: ("glyph@calendarserver.org", "glyph@example.com")
    }

    def _testService(self):
        if not hasattr(self, "_service"):
            self._service = DirectoryService(self.realmName)
        return self._service

    def _testRecord(self, fields=None, service=None):
        if fields is None:
            fields = self.fields_wsanchez
        if service is None:
            service = self._testService()
        return DirectoryRecord(service, fields)

    def test_interface(self):
        record = self._testRecord()
        try:
            verifyObject(IDirectoryRecord, record)
        except BrokenMethodImplementation, e:
            self.fail(e)

    def test_init(self):
        service  = self._testService()
        wsanchez = self._testRecord(self.fields_wsanchez)

        self.assertEquals(wsanchez.service, service)
        self.assertEquals(wsanchez.fields , self.fields_wsanchez)

    def test_initWithNoUID(self):
        fields = self.fields_wsanchez.copy()
        del fields[FieldName.uid]
        self.assertRaises(ValueError, self._testRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.uid] = ""
        self.assertRaises(ValueError, self._testRecord, fields)

    def test_initWithNoRecordType(self):
        fields = self.fields_wsanchez.copy()
        del fields[FieldName.recordType]
        self.assertRaises(ValueError, self._testRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.recordType] = ""
        self.assertRaises(ValueError, self._testRecord, fields)

    def test_initWithNoShortNames(self):
        fields = self.fields_wsanchez.copy()
        del fields[FieldName.shortNames]
        self.assertRaises(ValueError, self._testRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.shortNames] = ()
        self.assertRaises(ValueError, self._testRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.shortNames] = ("",)
        self.assertRaises(ValueError, self._testRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.shortNames] = ("wsanchez", "")
        self.assertRaises(ValueError, self._testRecord, fields)

    def test_compare(self):
        fields_glyphmod = self.fields_glyph.copy()
        del fields_glyphmod[FieldName.emailAddresses]

        wsanchez    = self._testRecord(self.fields_wsanchez)
        wsanchezmod = self._testRecord(self.fields_wsanchez, DirectoryService("plugh"))
        glyph       = self._testRecord(self.fields_glyph)
        glyphmod    = self._testRecord(fields_glyphmod)

        self.assertEquals(wsanchez, wsanchez)
        self.assertNotEqual(wsanchez, glyph)
        self.assertEquals(glyph, glyphmod) # UID matches
        self.assertNotEqual(glyphmod, wsanchez)
        self.assertNotEqual(wsanchez, wsanchezmod) # Different service

    def test_attributeAccess(self):
        wsanchez = self._testRecord(self.fields_wsanchez)

        self.assertEquals(wsanchez.recordType    , wsanchez.fields[FieldName.recordType    ])
        self.assertEquals(wsanchez.uid           , wsanchez.fields[FieldName.uid           ])
        self.assertEquals(wsanchez.shortNames    , wsanchez.fields[FieldName.shortNames    ])
        self.assertEquals(wsanchez.emailAddresses, wsanchez.fields[FieldName.emailAddresses])
