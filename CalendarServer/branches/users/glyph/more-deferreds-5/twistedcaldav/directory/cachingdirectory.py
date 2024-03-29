# -*- test-case-name: twistedcaldav.directory.test.test_cachedirectory -*-
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

"""
Caching directory service implementation.
"""

__all__ = [
    "CachingDirectoryService",
    "CachingDirectoryRecord",
    "DictRecordTypeCache",
]


import time

import memcacheclient
import base64

from twistedcaldav.config import config
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord, DirectoryError, UnknownRecordTypeError
from twistedcaldav.log import LoggingMixIn
from twistedcaldav.scheduling.cuaddress import normalizeCUAddr
from twisted.internet.defer import inlineCallbacks, returnValue, succeed


class RecordTypeCache(object):
    """
    Abstract class for a record type cache. We will likely have dict and memcache implementations of this.
    """
    
    def __init__(self, directoryService, recordType):
        
        self.directoryService = directoryService
        self.recordType = recordType

    def addRecord(self, record, indexType, indexKey, useMemcache=True,
        neverExpire=False):
        raise NotImplementedError()
    
    def removeRecord(self, record):
        raise NotImplementedError()
        
    def findRecord(self, indexType, indexKey):
        raise NotImplementedError()
        
class DictRecordTypeCache(RecordTypeCache, LoggingMixIn):
    """
    Cache implementation using a dict. Does not share the cache with other instances.
    """
    
    def __init__(self, directoryService, recordType):
        
        super(DictRecordTypeCache, self).__init__(directoryService, recordType)
        self.records = set()
        self.recordsIndexedBy = {
            CachingDirectoryService.INDEX_TYPE_GUID     : {},
            CachingDirectoryService.INDEX_TYPE_SHORTNAME: {},
            CachingDirectoryService.INDEX_TYPE_CUA    : {},
            CachingDirectoryService.INDEX_TYPE_AUTHID   : {},
        }

    # MOR: Defer this
    @inlineCallbacks
    def addRecord(self, record, indexType, indexKey, useMemcache=True,
        neverExpire=False):

        useMemcache = useMemcache and config.Memcached.ClientEnabled
        if neverExpire:
            record.neverExpire()

        self.records.add(record)

        # Also index/cache on guid
        indexTypes = [(indexType, indexKey)]
        if indexType != CachingDirectoryService.INDEX_TYPE_GUID:
            indexTypes.append((CachingDirectoryService.INDEX_TYPE_GUID,
                record.guid))

        for indexType, indexKey in indexTypes:
            self.recordsIndexedBy[indexType][indexKey] = record
            if useMemcache:
                key = "dir|%s|%s" % (indexType, indexKey)
                self.log_debug("Memcache: storing %s" % (key,))
                try:
                    yield self.directoryService.memcacheSet(key, record)
                except DirectoryMemcacheError:
                    self.log_error("Memcache: failed to store %s" % (key,))
                    pass
                
        returnValue(None)


    # MOR: Defer this
    def removeRecord(self, record):
        if record in self.records:
            self.records.remove(record)
            for indexType in self.directoryService.indexTypes():
                try:
                    indexData = getattr(record, CachingDirectoryService.indexTypeToRecordAttribute[indexType])
                except AttributeError:
                    continue
                if isinstance(indexData, basestring):
                    indexData = [indexData]
                for item in indexData:
                    try:
                        del self.recordsIndexedBy[indexType][item]
                    except KeyError:
                        self.log_debug("Missing record index item; type: %s, item: %s" % (indexType, item))

        return succeed(None) # MOR: Remove this when really deferred
        
    def findRecord(self, indexType, indexKey):
        return self.recordsIndexedBy[indexType].get(indexKey)

class CachingDirectoryService(DirectoryService):
    """
    Caching Directory implementation of L{IDirectoryService}.
    
    This is class must be overridden to provide a concrete implementation.
    """

    INDEX_TYPE_GUID      = "guid"
    INDEX_TYPE_SHORTNAME = "shortname"
    INDEX_TYPE_CUA       = "cua"
    INDEX_TYPE_AUTHID    = "authid"

    indexTypeToRecordAttribute = {
        "guid"     : "guid",
        "shortname": "shortNames",
        "cua"      : "calendarUserAddresses",
        "authid"   : "authIDs",
    }

    def __init__(
        self,
        cacheTimeout=30,
        cacheClass=DictRecordTypeCache,
    ):
        """
        @param cacheTimeout: C{int} number of minutes before cache is invalidated.
        """
        
        self.cacheTimeout = cacheTimeout * 60

        self._initCaches(cacheClass)

        super(CachingDirectoryService, self).__init__()

    def _getMemcacheClient(self, refresh=False):
        if refresh or not hasattr(self, "memcacheClient"):
            self.memcacheClient = memcacheclient.ClientFactory.getClient(['%s:%s' %
                (config.Memcached.BindAddress, config.Memcached.Port)],
                debug=0, pickleProtocol=2)
        return self.memcacheClient


    @inlineCallbacks
    def memcacheSet(self, key, record):

        hideService = isinstance(record, DirectoryRecord)

        try:
            if hideService:
                record.service = None # so we don't pickle service

            key = base64.b64encode(key)
            if not (yield self._getMemcacheClient().set(key, record, time=self.cacheTimeout)):
                self.log_error("Could not write to memcache, retrying")
                if not (yield self._getMemcacheClient(refresh=True).set(
                    key, record,
                    time=self.cacheTimeout
                )):
                    self.log_error("Could not write to memcache again, giving up")
                    del self.memcacheClient
                    raise DirectoryMemcacheError("Failed to write to memcache")
        finally:
            if hideService:
                record.service = self

    @inlineCallbacks
    def memcacheGet(self, key):

        key = base64.b64encode(key)
        try:
            record = (yield self._getMemcacheClient().get(key))
            if record is not None and isinstance(record, DirectoryRecord):
                record.service = self
        except memcacheclient.MemcacheError:
            self.log_error("Could not read from memcache, retrying")
            try:
                record = (yield self._getMemcacheClient(refresh=True).get(key))
            except memcacheclient.MemcacheError:
                self.log_error("Could not read from memcache again, giving up")
                del self.memcacheClient
                raise DirectoryMemcacheError("Failed to read from memcache")
        returnValue(record)

    def _initCaches(self, cacheClass):
        self._recordCaches = dict([
            (recordType, cacheClass(self, recordType))
            for recordType in self.recordTypes()
        ])
            
        self._disabledKeys = dict([(indexType, dict()) for indexType in self.indexTypes()])

    def indexTypes(self):
        
        return (
            CachingDirectoryService.INDEX_TYPE_GUID,
            CachingDirectoryService.INDEX_TYPE_SHORTNAME,
            CachingDirectoryService.INDEX_TYPE_CUA,
            CachingDirectoryService.INDEX_TYPE_AUTHID,
        )

    def recordCacheForType(self, recordType):
        try:
            return self._recordCaches[recordType]
        except KeyError:
            raise UnknownRecordTypeError(recordType)

    def listRecords(self, recordType):
        return succeed(self.recordCacheForType(recordType).records)

    def recordWithShortName(self, recordType, shortName):
        return self._lookupRecord((recordType,), CachingDirectoryService.INDEX_TYPE_SHORTNAME, shortName)

    @inlineCallbacks
    def recordWithCalendarUserAddress(self, address):
        address = normalizeCUAddr(address)
        record = None
        if address.startswith("urn:uuid:"):
            guid = address[9:]
            record = (yield self.recordWithGUID(guid))
        elif address.startswith("mailto:"):
            record = (yield self._lookupRecord(None, CachingDirectoryService.INDEX_TYPE_CUA, address))

        returnValue(record if record and record.enabledForCalendaring else None)

    def recordWithAuthID(self, authID):
        return self._lookupRecord(None, CachingDirectoryService.INDEX_TYPE_AUTHID, authID)

    def recordWithGUID(self, guid):
        return self._lookupRecord(None, CachingDirectoryService.INDEX_TYPE_GUID, guid)

    recordWithUID = recordWithGUID

    @inlineCallbacks
    def _lookupRecord(self, recordTypes, indexType, indexKey, cacheOnMiss=True):

        if recordTypes is None:
            recordTypes = self.recordTypes()
        else:
            # Only use recordTypes this service supports:
            supportedRecordTypes = self.recordTypes()
            typesToQuery = []
            for recordType in recordTypes:
                if recordType in supportedRecordTypes:
                    typesToQuery.append(recordType)
            if not typesToQuery:
                returnValue(None)
            recordTypes = typesToQuery

        @inlineCallbacks
        def lookup():
            for recordType in recordTypes:
                record = self.recordCacheForType(recordType).findRecord(indexType, indexKey)

                if record:
                    if (
                        record.cachedTime != 0 and
                        time.time() - record.cachedTime > self.cacheTimeout
                    ):
                        yield self.recordCacheForType(recordType).removeRecord(record)
                        returnValue(None)
                    else:
                        returnValue(record)
            else:
                returnValue(None)

        record = (yield lookup())
        if record:
            returnValue(record)

        if cacheOnMiss:
            # Check negative cache (take cache entry timeout into account)
            try:
                disabledTime = self._disabledKeys[indexType][indexKey]
                if time.time() - disabledTime < self.cacheTimeout:
                    returnValue(None)
            except KeyError:
                pass
            
            # Check memcache
            if config.Memcached.ClientEnabled:
                key = "dir|%s|%s" % (indexType, indexKey)
                record = (yield self.memcacheGet(key))
                self.log_debug("Memcache: checking %s" % (key,))

                try:
                    record = self.memcacheGet(key)
                except DirectoryMemcacheError:
                    self.log_error("Memcache: failed to get %s" % (key,))
                    record = None

                if record is None:
                    self.log_debug("Memcache: miss %s" % (key,))
                else:
                    self.log_debug("Memcache: hit %s" % (key,))
                    yield self.recordCacheForType(record.recordType).addRecord(record, indexType, indexKey, useMemcache=False)
                    returnValue(record)

                # Check negative memcache
                try:
                    val = yield self.memcacheGet("-%s" % (key,))
                except DirectoryMemcacheError:
                    self.log_error("Memcache: failed to get -%s" % (key,))
                    val = None
                if val == 1:
                    self.log_debug("Memcache: negative %s" % (key,))
                    self._disabledKeys[indexType][indexKey] = time.time()
                    returnValue(None)

            # Try query
            self.log_debug("Faulting record for attribute '%s' with value '%s'" % (indexType, indexKey,))
            yield self.queryDirectory(recordTypes, indexType, indexKey)
            
            # Now try again from cache
            record = (yield lookup())
            if record:
                self.log_debug("Found record for attribute '%s' with value '%s'" % (indexType, indexKey,))
                returnValue(record)


            # Add to negative cache with timestamp
            self.log_debug("Failed to fault record for attribute '%s' with value '%s'" % (indexType, indexKey,))
            self._disabledKeys[indexType][indexKey] = time.time()

            if config.Memcached.ClientEnabled:
                self.log_debug("Memcache: storing (negative) %s" % (key,))
                try:
                    yield self.memcacheSet("-%s" % (key,), 1)
                except DirectoryMemcacheError:
                    self.log_error("Memcache: failed to set -%s" % (key,))
                    pass

        returnValue(None)


    def queryDirectory(self, recordTypes, indexType, indexKey):
        raise NotImplementedError()

class CachingDirectoryRecord(DirectoryRecord):

    def __init__(
        self, service, recordType, guid, shortNames=(), authIDs=set(),
        fullName=None, firstName=None, lastName=None, emailAddresses=set(),
        enabledForCalendaring=None, uid=None,
    ):
        super(CachingDirectoryRecord, self).__init__(
            service               = service,
            recordType            = recordType,
            guid                  = guid,
            shortNames            = shortNames,
            authIDs               = authIDs,
            fullName              = fullName,
            firstName             = firstName,
            lastName              = lastName,
            emailAddresses        = emailAddresses,
            enabledForCalendaring = enabledForCalendaring,
            uid                   = uid,
        )
        
        self.cachedTime = time.time()

    def neverExpire(self):
        self.cachedTime = 0

class DirectoryMemcacheError(DirectoryError):
    """
    Error communicating with memcached.
    """

