# -*- test-case-name: twext.who.test.test_xml -*-
##
# Copyright (c) 2006-2013 Apple Inc. All rights reserved.
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

from __future__ import absolute_import

"""
XML directory service implementation.
"""

__all__ = [
    "ParseError",
    "DirectoryService",
    "DirectoryRecord",
]

from time import time
from uuid import UUID

from xml.etree.ElementTree import parse as parseXML
from xml.etree.ElementTree import ParseError as XMLParseError
from xml.etree.ElementTree import tostring as etreeToString
from xml.etree.ElementTree import Element as XMLElement

from twisted.python.constants import Values, ValueConstant
from twisted.internet.defer import fail

from twext.who.idirectory import DirectoryServiceError
from twext.who.idirectory import NoSuchRecordError, UnknownRecordTypeError
from twext.who.idirectory import RecordType, FieldName as BaseFieldName
from twext.who.index import DirectoryService as BaseDirectoryService
from twext.who.index import DirectoryRecord
from twext.who.index import FieldName as IndexFieldName



##
# Exceptions
##

class ParseError(DirectoryServiceError):
    """
    Parse error.
    """



##
# XML constants
##

class Element(Values):
    directory = ValueConstant(u"directory")
    record    = ValueConstant(u"record")

    #
    # Field names
    #
    uid = ValueConstant(u"uid")
    uid.fieldName = BaseFieldName.uid

    guid = ValueConstant(u"guid")
    guid.fieldName = BaseFieldName.guid

    shortName = ValueConstant(u"short-name")
    shortName.fieldName = BaseFieldName.shortNames

    fullName = ValueConstant(u"full-name")
    fullName.fieldName = BaseFieldName.fullNames

    emailAddress = ValueConstant(u"email")
    emailAddress.fieldName = BaseFieldName.emailAddresses

    password = ValueConstant(u"password")
    password.fieldName = BaseFieldName.password

    memberUID = ValueConstant(u"member-uid")
    memberUID.fieldName = IndexFieldName.memberUIDs



class Attribute(Values):
    realm      = ValueConstant(u"realm")
    recordType = ValueConstant(u"type")



class Value(Values):
    #
    # Booleans
    #
    true  = ValueConstant(u"true")
    false = ValueConstant(u"false")

    #
    # Record types
    #
    user = ValueConstant(u"user")
    user.recordType = RecordType.user

    group = ValueConstant(u"group")
    group.recordType = RecordType.group



##
# Directory Service
##

class DirectoryService(BaseDirectoryService):
    """
    XML directory service.
    """

    element   = Element
    attribute = Attribute
    value     = Value

    refreshInterval = 4


    def __init__(self, filePath):
        BaseDirectoryService.__init__(self, realmName=noRealmName)

        self.filePath = filePath


    def __repr__(self):
        realmName = self._realmName
        if realmName is None:
            realmName = "(not loaded)"
        else:
            realmName = repr(realmName)

        return (
            "<{self.__class__.__name__} {realmName}>".format(
                self=self,
                realmName=realmName,
            )
        )


    @property
    def realmName(self):
        self.loadRecords()
        return self._realmName


    @realmName.setter
    def realmName(self, value):
        if value is not noRealmName:
            raise AssertionError("realmName may not be set directly")


    @property
    def unknownRecordTypes(self):
        self.loadRecords()
        return self._unknownRecordTypes


    @property
    def unknownFieldElements(self):
        self.loadRecords()
        return self._unknownFieldElements


    def loadRecords(self, loadNow=False, stat=True):
        """
        Load records from L{self.filePath}.

        Does nothing if a successful refresh has happened within the
        last L{self.refreshInterval} seconds.

        @param loadNow: If true, load now (ignoring
            L{self.refreshInterval})
        @type loadNow: L{type}

        @param stat: If true, check file metadata and don't reload if
            unchanged.
        @type loadNow: L{type}
        """
        #
        # Punt if we've read the file recently
        #
        now = time()
        if not loadNow and now - self._lastRefresh <= self.refreshInterval:
            return

        #
        # Punt if we've read the file and it's still the same.
        #
        if stat:
            self.filePath.restat()
            cacheTag = (
                self.filePath.getModificationTime(),
                self.filePath.getsize()
            )
            if cacheTag == self._cacheTag:
                return
        else:
            cacheTag = None

        #
        # Open and parse the file
        #
        try:
            fh = self.filePath.open()

            try:
                etree = parseXML(fh)
            except XMLParseError as e:
                raise ParseError(e)
        finally:
            fh.close()

        #
        # Pull data from DOM
        #
        directoryNode = etree.getroot()
        if directoryNode.tag != self.element.directory.value:
            raise ParseError(
                "Incorrect root element: {0}".format(directoryNode.tag)
            )

        realmName = unicode(directoryNode.get(
            self.attribute.realm.value, u""
        ))

        if not realmName:
            raise ParseError("No realm name.")

        unknownRecordTypes   = set()
        unknownFieldElements = set()

        records = set()

        for recordNode in directoryNode:
            try:
                records.add(
                    self.parseRecordNode(recordNode, unknownFieldElements)
                )
            except UnknownRecordTypeError as e:
                unknownRecordTypes.add(e.token)

        #
        # Store results
        #

        index = {}

        for fieldName in self.indexedFields:
            index[fieldName] = {}

        for record in records:
            for fieldName in self.indexedFields:
                values = record.fields.get(fieldName, None)

                if values is not None:
                    if not BaseFieldName.isMultiValue(fieldName):
                        values = (values,)

                    for value in values:
                        index[fieldName].setdefault(value, set()).add(record)

        self._realmName = realmName

        self._unknownRecordTypes   = unknownRecordTypes
        self._unknownFieldElements = unknownFieldElements

        self._cacheTag = cacheTag
        self._lastRefresh = now

        self.index = index

        return etree


    def parseRecordNode(self, recordNode, unknownFieldElements=None):
        recordTypeAttribute = recordNode.get(
            self.attribute.recordType.value, u""
        )
        if recordTypeAttribute:
            try:
                recordType = (
                    self.value.lookupByValue(recordTypeAttribute).recordType
                )
            except (ValueError, AttributeError):
                raise UnknownRecordTypeError(recordTypeAttribute)
        else:
            recordType = self.recordType.user

        fields = {}
        fields[self.fieldName.recordType] = recordType

        for fieldNode in recordNode:
            try:
                fieldElement = self.element.lookupByValue(fieldNode.tag)
            except ValueError:
                if unknownFieldElements is not None:
                    unknownFieldElements.add(fieldNode.tag)

            try:
                fieldName = fieldElement.fieldName
            except AttributeError:
                if unknownFieldElements is not None:
                    unknownFieldElements.add(fieldNode.tag)

            vType = BaseFieldName.valueType(fieldName)

            if vType in (unicode, UUID):
                value = vType(fieldNode.text)
            else:
                raise AssertionError(
                    "Unknown value type {0} for field {1}",
                    vType, fieldName
                )

            if BaseFieldName.isMultiValue(fieldName):
                values = fields.setdefault(fieldName, [])
                values.append(value)
            else:
                fields[fieldName] = value

        return DirectoryRecord(self, fields)


    def _uidForRecordNode(self, recordNode):
        uidNode = recordNode.find(self.element.uid.value)
        if uidNode is None:
            raise NotImplementedError("No UID node")

        return uidNode.text


    def flush(self):
        BaseDirectoryService.flush(self)

        self._realmName            = None
        self._unknownRecordTypes   = None
        self._unknownFieldElements = None
        self._cacheTag             = None
        self._lastRefresh          = 0


    def updateRecords(self, records, create=False):
        # Index the records to update by UID
        recordsByUID = dict(((record.uid, record) for record in records))

        # Index the record type -> attribute mappings.
        recordTypes = {}
        for valueName in self.value.iterconstants():
            recordType = getattr(valueName, "recordType", None)
            if recordType is not None:
                recordTypes[recordType] = valueName.value
        del valueName

        # Index the field name -> element mappings.
        fieldNames = {}
        for elementName in self.element.iterconstants():
            fieldName = getattr(elementName, "fieldName", None)
            if fieldName is not None:
                fieldNames[fieldName] = elementName.value
        del elementName

        directoryNode = self._directoryNodeForEditing()

        def fillRecordNode(recordNode, record):
            for (name, value) in record.fields.items():
                if name == self.fieldName.recordType:
                    if value in recordTypes:
                        recordNode.set(
                            self.attribute.recordType.value,
                            recordTypes[value]
                        )
                    else:
                        raise AssertionError(
                            "Unknown record type: {0}".format(value)
                        )

                else:
                    if name in fieldNames:
                        tag = fieldNames[name]

                        if BaseFieldName.isMultiValue(name):
                            values = value
                        else:
                            values = (value,)

                        for value in values:
                            subNode = XMLElement(tag)
                            subNode.text = value
                            recordNode.append(subNode)

                    else:
                        raise AssertionError(
                            "Unknown field name: {0!r}".format(name)
                        )

        # Walk through the record nodes in the XML tree and apply
        # updates.
        for recordNode in directoryNode:
            uid = self._uidForRecordNode(recordNode)

            record = recordsByUID.get(uid, None)

            if record:
                recordNode.clear()
                fillRecordNode(recordNode, record)
                del recordsByUID[uid]

        if recordsByUID:
            if not create:
                return fail(NoSuchRecordError(recordsByUID.keys()))

            for uid, record in recordsByUID.items():
                recordNode = XMLElement(self.element.record.value)
                fillRecordNode(recordNode, record)
                directoryNode.append(recordNode)

        self._writeDirectoryNode(directoryNode)


    def removeRecords(self, uids):
        directoryNode = self._directoryNodeForEditing()

        #
        # Walk through the record nodes in the XML tree and start
        # zapping.
        #
        for recordNode in directoryNode:
            uid = self._uidForRecordNode(recordNode)

            if uid in uids:
                directoryNode.remove(recordNode)

        self._writeDirectoryNode(directoryNode)


    def _directoryNodeForEditing(self):
        """
        Drop cached data and load the XML DOM.
        """
        self.flush()
        etree = self.loadRecords(loadNow=True)
        return etree.getroot()


    def _writeDirectoryNode(self, directoryNode):
        self.filePath.setContent(etreeToString(directoryNode))
        self.flush()



noRealmName = object()
