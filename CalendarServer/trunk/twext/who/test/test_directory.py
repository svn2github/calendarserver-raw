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
Generic directory service base implementation tests.
"""

from zope.interface.verify import verifyObject, BrokenMethodImplementation

from twisted.trial import unittest
from twisted.trial.unittest import SkipTest
from twisted.internet.defer import inlineCallbacks

from twext.who.idirectory import QueryNotSupportedError, NotAllowedError
from twext.who.idirectory import RecordType, FieldName
from twext.who.idirectory import IDirectoryService, IDirectoryRecord
from twext.who.directory import DirectoryService, DirectoryRecord



class ServiceMixIn(object):
    realmName = u"xyzzy"


    def service(self):
        if not hasattr(self, "_service"):
            self._service = DirectoryService(self.realmName)
        return self._service



class BaseDirectoryServiceTest(ServiceMixIn):
    def test_interface(self):
        service = self.service()
        try:
            verifyObject(IDirectoryService, service)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def test_init(self):
        service = self.service()
        self.assertEquals(service.realmName, self.realmName)


    def test_repr(self):
        service = self.service()
        self.assertEquals(repr(service), "<DirectoryService u'xyzzy'>")


    def test_recordTypes(self):
        service = self.service()
        self.assertEquals(
            set(service.recordTypes()),
            set(service.recordType.iterconstants())
        )


    @inlineCallbacks
    def test_recordsFromQueryNone(self):
        service = self.service()
        records = (yield service.recordsFromQuery(()))
        for record in records:
            self.failTest("No records expected")


    def test_recordsFromQueryBogus(self):
        service = self.service()
        self.assertFailure(
            service.recordsFromQuery((object(),)),
            QueryNotSupportedError
        )


    def test_recordWithUID(self):
        raise SkipTest("Subclasses should implement this test.")


    def test_recordWithGUID(self):
        raise SkipTest("Subclasses should implement this test.")


    def test_recordsWithRecordType(self):
        raise SkipTest("Subclasses should implement this test.")


    def test_recordWithShortName(self):
        raise SkipTest("Subclasses should implement this test.")


    def test_recordsWithEmailAddress(self):
        raise SkipTest("Subclasses should implement this test.")



class DirectoryServiceTest(unittest.TestCase, BaseDirectoryServiceTest):
    def test_recordsFromExpression(self):
        service = self.service()
        result = yield(service.recordsFromExpression(None))
        self.assertFailure(result, QueryNotSupportedError)


    def test_recordWithUID(self):
        service = self.service()
        self.assertFailure(
            service.recordWithUID(None),
            QueryNotSupportedError
        )


    def test_recordWithGUID(self):
        service = self.service()
        self.assertFailure(
            service.recordWithGUID(None),
            QueryNotSupportedError
        )


    def test_recordsWithRecordType(self):
        service = self.service()
        self.assertFailure(
            service.recordsWithRecordType(None),
            QueryNotSupportedError
        )


    def test_recordWithShortName(self):
        service = self.service()
        self.assertFailure(
            service.recordWithShortName(None, None),
            QueryNotSupportedError
        )


    def test_recordsWithEmailAddress(self):
        service = self.service()
        self.assertFailure(
            service.recordsWithEmailAddress(None),
            QueryNotSupportedError
        )



class BaseDirectoryServiceImmutableTest(ServiceMixIn):
    def test_updateRecordsNotAllowed(self):
        service = self.service()

        newRecord = DirectoryRecord(
            service,
            fields={
                service.fieldName.uid:        u"__plugh__",
                service.fieldName.recordType: service.recordType.user,
                service.fieldName.shortNames: (u"plugh",),
            }
        )

        self.assertFailure(
            service.updateRecords((newRecord,), create=True),
            NotAllowedError,
        )

        self.assertFailure(
            service.updateRecords((newRecord,), create=False),
            NotAllowedError,
        )


    def test_removeRecordsNotAllowed(self):
        service = self.service()

        service.removeRecords(())
        self.assertFailure(
            service.removeRecords((u"foo",)),
            NotAllowedError,
        )



class DirectoryServiceImmutableTest(
    unittest.TestCase,
    BaseDirectoryServiceImmutableTest,
):
    pass



class BaseDirectoryRecordTest(ServiceMixIn):
    fields_wsanchez = {
        FieldName.uid: u"UID:wsanchez",
        FieldName.recordType: RecordType.user,
        FieldName.shortNames: (u"wsanchez", u"wilfredo_sanchez"),
        FieldName.fullNames: (
            u"Wilfredo Sanchez",
            u"Wilfredo Sanchez Vega",
        ),
        FieldName.emailAddresses: (
            u"wsanchez@calendarserver.org",
            u"wsanchez@example.com",
        )
    }

    fields_glyph = {
        FieldName.uid: u"UID:glyph",
        FieldName.recordType: RecordType.user,
        FieldName.shortNames: (u"glyph",),
        FieldName.fullNames: (u"Glyph Lefkowitz",),
        FieldName.emailAddresses: (u"glyph@calendarserver.org",)
    }

    fields_sagen = {
        FieldName.uid: u"UID:sagen",
        FieldName.recordType: RecordType.user,
        FieldName.shortNames: (u"sagen",),
        FieldName.fullNames: (u"Morgen Sagen",),
        FieldName.emailAddresses: (u"sagen@CalendarServer.org",)
    }

    fields_staff = {
        FieldName.uid: u"UID:staff",
        FieldName.recordType: RecordType.group,
        FieldName.shortNames: (u"staff",),
        FieldName.fullNames: (u"Staff",),
        FieldName.emailAddresses: (u"staff@CalendarServer.org",)
    }


    def makeRecord(self, fields=None, service=None):
        if fields is None:
            fields = self.fields_wsanchez
        if service is None:
            service = self.service()
        return DirectoryRecord(service, fields)


    def test_interface(self):
        record = self.makeRecord()
        try:
            verifyObject(IDirectoryRecord, record)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def test_init(self):
        service  = self.service()
        wsanchez = self.makeRecord(self.fields_wsanchez, service=service)

        self.assertEquals(wsanchez.service, service)
        self.assertEquals(wsanchez.fields, self.fields_wsanchez)


    def test_initWithNoUID(self):
        fields = self.fields_wsanchez.copy()
        del fields[FieldName.uid]
        self.assertRaises(ValueError, self.makeRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.uid] = u""
        self.assertRaises(ValueError, self.makeRecord, fields)


    def test_initWithNoRecordType(self):
        fields = self.fields_wsanchez.copy()
        del fields[FieldName.recordType]
        self.assertRaises(ValueError, self.makeRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.recordType] = None
        self.assertRaises(ValueError, self.makeRecord, fields)


    def test_initWithNoShortNames(self):
        fields = self.fields_wsanchez.copy()
        del fields[FieldName.shortNames]
        self.assertRaises(ValueError, self.makeRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.shortNames] = ()
        self.assertRaises(ValueError, self.makeRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.shortNames] = (u"",)
        self.assertRaises(ValueError, self.makeRecord, fields)

        fields = self.fields_wsanchez.copy()
        fields[FieldName.shortNames] = (u"wsanchez", u"")
        self.assertRaises(ValueError, self.makeRecord, fields)


    def test_initWithBogusRecordType(self):
        fields = self.fields_wsanchez.copy()
        fields[FieldName.recordType] = object()
        self.assertRaises(ValueError, self.makeRecord, fields)


    def test_initNormalize(self):
        sagen = self.makeRecord(self.fields_sagen)

        self.assertEquals(
            sagen.fields[FieldName.emailAddresses],
            (u"sagen@calendarserver.org",)
        )


    def test_compare(self):
        fields_glyphmod = self.fields_glyph.copy()
        del fields_glyphmod[FieldName.emailAddresses]

        plugh = DirectoryService(u"plugh")

        wsanchez    = self.makeRecord(self.fields_wsanchez)
        wsanchezmod = self.makeRecord(self.fields_wsanchez, plugh)
        glyph       = self.makeRecord(self.fields_glyph)
        glyphmod    = self.makeRecord(fields_glyphmod)

        self.assertEquals(wsanchez, wsanchez)
        self.assertNotEqual(wsanchez, glyph)
        self.assertNotEqual(glyph, glyphmod)  # UID matches, other fields don't
        self.assertNotEqual(glyphmod, wsanchez)
        self.assertNotEqual(wsanchez, wsanchezmod)  # Different service


    def test_attributeAccess(self):
        wsanchez = self.makeRecord(self.fields_wsanchez)

        self.assertEquals(
            wsanchez.recordType,
            wsanchez.fields[FieldName.recordType]
        )
        self.assertEquals(
            wsanchez.uid,
            wsanchez.fields[FieldName.uid]
        )
        self.assertEquals(
            wsanchez.shortNames,
            wsanchez.fields[FieldName.shortNames]
        )
        self.assertEquals(
            wsanchez.emailAddresses,
            wsanchez.fields[FieldName.emailAddresses]
        )


    @inlineCallbacks
    def test_members(self):
        wsanchez = self.makeRecord(self.fields_wsanchez)
        self.assertEquals(
            set((yield wsanchez.members())),
            set()
        )

        raise SkipTest("Subclasses should implement this test.")


    def test_groups(self):
        raise SkipTest("Subclasses should implement this test.")



class DirectoryRecordTest(unittest.TestCase, BaseDirectoryRecordTest):
    def test_members(self):
        wsanchez = self.makeRecord(self.fields_wsanchez)
        self.assertEquals(
            set((yield wsanchez.members())),
            set()
        )

        staff = self.makeRecord(self.fields_staff)
        self.assertFailure(staff.members(), NotImplementedError)


    def test_groups(self):
        wsanchez = self.makeRecord(self.fields_wsanchez)
        self.assertFailure(wsanchez.groups(), NotImplementedError)
