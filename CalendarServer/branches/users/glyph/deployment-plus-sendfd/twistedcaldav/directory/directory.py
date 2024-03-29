##
# Copyright (c) 2006-2007 Apple Inc. All rights reserved.
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
Generic directory service classes.
"""

__all__ = [
    "DirectoryService",
    "DirectoryRecord",
    "DirectoryError",
    "UnknownRecordError",
    "UnknownRecordTypeError",
]

import sys

from zope.interface import implements

from twisted.cred.error import UnauthorizedLogin
from twisted.cred.checkers import ICredentialsChecker
from twisted.web2.dav.auth import IPrincipalCredentials

from twistedcaldav.log import LoggingMixIn
from twistedcaldav.directory.idirectory import IDirectoryService, IDirectoryRecord
from twistedcaldav.directory.util import uuidFromName

class DirectoryService(LoggingMixIn):
    implements(IDirectoryService, ICredentialsChecker)

    ##
    # IDirectoryService
    ##

    realmName = None

    recordType_users = "users"
    recordType_groups = "groups"
    recordType_locations = "locations"
    recordType_resources = "resources"
    
    def _generatedGUID(self):
        if not hasattr(self, "_guid"):
            realmName = self.realmName

            assert self.baseGUID, "Class %s must provide a baseGUID attribute" % (self.__class__.__name__,)

            if realmName is None:
                self.log_error("Directory service %s has no realm name or GUID; generated service GUID will not be unique." % (self,))
                realmName = ""
            else:
                self.log_info("Directory service %s has no GUID; generating service GUID from realm name." % (self,))

            self._guid = uuidFromName(self.baseGUID, realmName)

        return self._guid

    baseGUID = None
    guid = property(_generatedGUID)

    ##
    # ICredentialsChecker
    ##

    # For ICredentialsChecker
    credentialInterfaces = (IPrincipalCredentials,)

    def requestAvatarId(self, credentials):
        credentials = IPrincipalCredentials(credentials)

        # FIXME: ?
        # We were checking if principal is enabled; seems unnecessary in current
        # implementation because you shouldn't have a principal object for a
        # disabled directory principal.

        user = self.recordWithShortName(DirectoryService.recordType_users, credentials.credentials.username)
        if user is None:
            raise UnauthorizedLogin("No such user: %s" % (user,))

        # Handle Kerberos as a separate behavior
        try:
            from twistedcaldav.authkerb import NegotiateCredentials
        except ImportError:
            NegotiateCredentials=None
        
        if NegotiateCredentials and isinstance(credentials.credentials, 
                                               NegotiateCredentials):
            # If we get here with Kerberos, then authentication has already succeeded
            return (
                credentials.authnPrincipal.principalURL(),
                credentials.authzPrincipal.principalURL(),
            )
        else:
            if user.verifyCredentials(credentials.credentials):
                return (
                    credentials.authnPrincipal.principalURL(),
                    credentials.authzPrincipal.principalURL(),
                )
            else:
                raise UnauthorizedLogin("Incorrect credentials for %s" % (user,)) 

    def recordTypes(self):
        raise NotImplementedError("Subclass must implement recordTypes()")

    def listRecords(self, recordType):
        raise NotImplementedError("Subclass must implement listRecords()")

    def recordWithShortName(self, recordType, shortName):
        raise NotImplementedError("Subclass must implement recordWithShortName()")

    def recordWithGUID(self, guid):
        for record in self.allRecords():
            if record.guid == guid:
                return record
        return None

    def recordWithCalendarUserAddress(self, address):
        address = address.lower()
        for record in self.allRecords():
            if address in record.calendarUserAddresses:
                return record
        return None

    def allRecords(self):
        for recordType in self.recordTypes():
            for record in self.listRecords(recordType):
                yield record

    def refresh(self, master=False):
        """
        This gets called in the master process to give the directory service
        a chance to refresh a cache of directory information
        """
        pass


class DirectoryRecord(LoggingMixIn):
    implements(IDirectoryRecord)

    def __repr__(self):
        return "<%s[%s@%s(%s)] %s(%s) %r>" % (
            self.__class__.__name__,
            self.recordType,
            self.service.guid,
            self.service.realmName,
            self.guid,
            self.shortName,
            self.fullName
        )

    def __init__(
        self, service, recordType, guid, shortName, fullName,
        calendarUserAddresses, autoSchedule, enabledForCalendaring=True,
    ):
        assert service.realmName is not None
        assert recordType
        assert shortName

        if not guid:
            guid = uuidFromName(service.guid, "%s:%s" % (recordType, shortName))

        if enabledForCalendaring:
            calendarUserAddresses.add("urn:uuid:%s" % (guid,))
        else:
            assert len(calendarUserAddresses) == 0

        self.service               = service
        self.recordType            = recordType
        self.guid                  = guid
        self.shortName             = shortName
        self.fullName              = fullName
        self.enabledForCalendaring = enabledForCalendaring
        self.calendarUserAddresses = calendarUserAddresses
        self.autoSchedule          = autoSchedule

    def __cmp__(self, other):
        if not isinstance(other, DirectoryRecord):
            return NotImplemented

        for attr in ("service", "recordType", "shortName", "guid"):
            diff = cmp(getattr(self, attr), getattr(other, attr))
            if diff != 0:
                return diff
        return 0

    def __hash__(self):
        h = hash(self.__class__)
        for attr in ("service", "recordType", "shortName", "guid",
                     "enabledForCalendaring"):
            h = (h + hash(getattr(self, attr))) & sys.maxint

        return h

    def members(self):
        return ()

    def groups(self):
        return ()

    def proxies(self):
        return ()

    def proxyFor(self):
        return ()

    def readOnlyProxies(self):
        return ()

    def readOnlyProxyFor(self):
        return ()

    def hasEditableProxyMembership(self):
        return self.recordType in (DirectoryService.recordType_users, DirectoryService.recordType_groups)

    def verifyCredentials(self, credentials):
        return False

class DirectoryError(RuntimeError):
    """
    Generic directory error.
    """

class UnknownRecordTypeError(DirectoryError):
    """
    Unknown directory record type.
    """
