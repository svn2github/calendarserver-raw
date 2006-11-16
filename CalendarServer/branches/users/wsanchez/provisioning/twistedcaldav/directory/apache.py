##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
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
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

"""
Apache UserFile/GroupFile compatible directory service implementation.
"""

__all__ = [
    "FileDirectoryService",
    "FileDirectoryRecord",
]

from crypt import crypt

from twisted.python.filepath import FilePath
from twisted.cred.credentials import UsernamePassword

from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.directory import UnknownRecordTypeError

class FileDirectoryService(DirectoryService):
    """
    Apache UserFile/GroupFile implementation of L{IDirectoryService}.
    """
    def __repr__(self):
        return "<%s %r %r>" % (self.__class__.__name__, self.userFile, self.groupFile)

    def __init__(self, userFile, groupFile=None):
        if type(userFile) is str:
            userFile = FilePath(userFile)
        if type(groupFile) is str:
            groupFile = FilePath(groupFile)

        self.userFile = userFile
        self.groupFile = groupFile

    def recordTypes(self):
        recordTypes = ("user",)
        if self.groupFile is not None:
            recordTypes += ("group",)
        return recordTypes

    def listRecords(self, recordType):
        for entryShortName, entryData in self._entriesForRecordType(recordType):
            yield entryShortName

    def recordWithShortName(self, recordType, shortName):
        for entryShortName, entryData in self._entriesForRecordType(recordType):
            if entryShortName == shortName:
                if recordType == "user":
                    return FileDirectoryRecord(
                        service       = self,
                        recordType    = recordType,
                        shortName     = entryShortName,
                        cryptPassword = entryData,
                    )
                elif recordType == "group":
                    return FileDirectoryRecord(
                        service    = self,
                        recordType = recordType,
                        shortName  = entryShortName,
                        members    = entryData,
                    )
                else:
                    raise AssertionError("We shouldn't be here.")

        return None

    def recordWithGUID(self, guid):
        raise NotImplementedError()

    def _entriesForRecordType(self, recordType):
        if recordType == "user":
            recordFile = self.userFile
        elif recordType == "group":
            recordFile = self.groupFile
        else:
            raise UnknownRecordTypeError("Unknown record type: %s" % (recordType,))

        if recordFile is None:
            return

        for entry in recordFile.open():
            if entry and entry[0] != "#":
                shortName, rest = entry.rstrip("\n").split(":", 1)
                yield shortName, rest

class FileDirectoryRecord(DirectoryRecord):
    """
    Apache UserFile/GroupFile implementation of L{IDirectoryRecord}.
    """
    def __init__(self, service, recordType, shortName, cryptPassword=None, members=()):
        if type(members) is str:
            members = tuple(m.strip() for m in members.split(","))

        assert recordType == "group" or not members, "Only group records may have members."

        self.service        = service
        self.recordType     = recordType
        self.guid           = None
        self.shortName      = shortName
        self.fullName       = None
        self._cryptPassword = cryptPassword
        self._members       = members

    def members(self):
        for shortName in self._members:
            yield self.service.recordWithShortName("user", shortName)

    def groups(self):
        for groupName in self.service.listRecords("group"):
            group = self.service.recordWithShortName("group", groupName)
            for member in group.members():
                if member == self:
                    yield group
                    continue

    def verifyCredentials(self, credentials):
        if self._cryptPassword in ("", "*", "x"):
            return False

        if isinstance(credentials, UsernamePassword):
            return crypt(credentials.password, self._cryptPassword) == self._cryptPassword

        return super(FileDirectoryRecord, self).verifyCredentials(credentials)
