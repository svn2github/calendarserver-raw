##
# Copyright (c) 2006-2009 Apple Inc. All rights reserved.
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
XML based user/group/resource directory service implementation.
"""

__all__ = [
    "XMLDirectoryService",
]

from twisted.cred.credentials import UsernamePassword
from twisted.web2.auth.digest import DigestedCredentials
from twisted.python.filepath import FilePath

from twistedcaldav.directory import augment
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.xmlaccountsparser import XMLAccountsParser

class XMLDirectoryService(DirectoryService):
    """
    XML based implementation of L{IDirectoryService}.
    """
    baseGUID = "9CA8DEC5-5A17-43A9-84A8-BE77C1FB9172"

    realmName = None

    def __repr__(self):
        return "<%s %r: %r>" % (self.__class__.__name__, self.realmName, self.xmlFile)

    def __init__(self, xmlFile, **kwds):
        super(XMLDirectoryService, self).__init__()

        if type(xmlFile) is str:
            xmlFile = FilePath(xmlFile)

        self.xmlFile = xmlFile
        self._fileInfo = None
        self._accounts()

    def recordTypes(self):
        recordTypes = (
            DirectoryService.recordType_users,
            DirectoryService.recordType_groups,
            DirectoryService.recordType_locations,
            DirectoryService.recordType_resources
        )
        return recordTypes

    def listRecords(self, recordType):
        for entryShortName, xmlPrincipal in self._entriesForRecordType(recordType):
            record = XMLDirectoryRecord(
                service       = self,
                recordType    = recordType,
                shortName     = entryShortName,
                xmlPrincipal  = xmlPrincipal,
            )

            # Look up augment information
            # TODO: this needs to be deferred but for now we hard code the deferred result because
            # we know it is completing immediately.
            d = augment.AugmentService.getAugmentRecord(record.guid, recordType)
            d.addCallback(lambda x:record.addAugmentInformation(x))

            yield record

    def recordWithShortName(self, recordType, shortName):
        for entryShortName, xmlprincipal in self._entriesForRecordType(recordType):
            if entryShortName == shortName:
                record = XMLDirectoryRecord(
                    service       = self,
                    recordType    = recordType,
                    shortName     = entryShortName,
                    xmlPrincipal  = xmlprincipal,
                )

                # Look up augment information
                # TODO: this needs to be deferred but for now we hard code the deferred result because
                # we know it is completing immediately.
                d = augment.AugmentService.getAugmentRecord(record.guid, recordType)
                d.addCallback(lambda x:record.addAugmentInformation(x))

                return record

        return None

    def _entriesForRecordType(self, recordType):
        try:
            for entry in self._accounts()[recordType].itervalues():
                yield entry.shortName, entry
        except KeyError:
            return

    def _accounts(self):
        self.xmlFile.restat()
        fileInfo = (self.xmlFile.getmtime(), self.xmlFile.getsize())
        if fileInfo != self._fileInfo:
            parser = XMLAccountsParser(self.xmlFile)
            self._parsedAccounts = parser.items
            self.realmName = parser.realm
            self._fileInfo = fileInfo
        return self._parsedAccounts

class XMLDirectoryRecord(DirectoryRecord):
    """
    XML based implementation implementation of L{IDirectoryRecord}.
    """
    def __init__(self, service, recordType, shortName, xmlPrincipal):
        super(XMLDirectoryRecord, self).__init__(
            service               = service,
            recordType            = recordType,
            guid                  = xmlPrincipal.guid,
            shortName             = shortName,
            fullName              = xmlPrincipal.name,
            emailAddresses        = xmlPrincipal.emailAddresses,
        )

        self.password          = xmlPrincipal.password
        self._members          = xmlPrincipal.members
        self._groups           = xmlPrincipal.groups

    def members(self):
        for recordType, shortName in self._members:
            yield self.service.recordWithShortName(recordType, shortName)

    def groups(self):
        for shortName in self._groups:
            yield self.service.recordWithShortName(DirectoryService.recordType_groups, shortName)

    def verifyCredentials(self, credentials):
        if self.enabled:
            if isinstance(credentials, UsernamePassword):
                return credentials.password == self.password
            if isinstance(credentials, DigestedCredentials):
                return credentials.checkPassword(self.password)

        return super(XMLDirectoryRecord, self).verifyCredentials(credentials)
