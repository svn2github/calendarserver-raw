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
Apple Open Directory directory service implementation.
"""

__all__ = [
    "OpenDirectoryService",
    "OpenDirectoryInitError",
]

import sys

import opendirectory
import dsattributes
import dsquery
import memcacheclient
import cPickle as pickle

try:
    from hashlib import md5
except ImportError:
    from md5 import new as md5


from twisted.internet.reactor import callLater
from twisted.internet.threads import deferToThread
from twisted.internet.task import LoopingCall
from twisted.cred.credentials import UsernamePassword
from twisted.web2.auth.digest import DigestedCredentials
from twisted.python.filepath import FilePath

from twistedcaldav.config import config
from twistedcaldav.directory import augment
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.directory import DirectoryError, UnknownRecordTypeError

serverPreferences = '/Library/Preferences/com.apple.servermgr_info.plist'
saclGroup = 'com.apple.access_calendar'

class OpenDirectoryService(DirectoryService):
    """
    Open Directory implementation of L{IDirectoryService}.
    """
    baseGUID = "891F8321-ED02-424C-BA72-89C32F215C1E"

    def __repr__(self):
        return "<%s %r: %r>" % (self.__class__.__name__, self.realmName, self.node)

    def __init__(self, node="/Search", dosetup=True, cacheTimeout=30, **kwds):
        """
        @param node: an OpenDirectory node name to bind to.
        @param dosetup: if C{True} then the directory records are initialized,
                        if C{False} they are not.
                        This should only be set to C{False} when doing unit tests.
        """
        try:
            directory = opendirectory.odInit(node)
        except opendirectory.ODError, e:
            self.log_error("Open Directory (node=%s) Initialization error: %s" % (node, e))
            raise

        self.realmName = node
        self.directory = directory
        self.node = node
        self.cacheTimeout = cacheTimeout
        self._records = {}
        self._delayedCalls = set()

        self.isWorkgroupServer = False

        if dosetup:
            for recordType in self.recordTypes():
                self.recordsForType(recordType)

    def refresh(self, loop=True):
        """
        This service works by having the master process call this method
        which queries OD for all records, storing the pickled results into
        files that the child processes stat/read every minute.
        The files are only written by this method if there are actually
        changes in the results.
        The reloadCache( ) method below used to talk to OD, but now it reads
        these files.
        """

        def _refresh(self):
            dataRoot = FilePath(config.DataRoot)
            cacheDir = dataRoot.child("DirectoryCache")
            if not cacheDir.exists():
                cacheDir.createDirectory()

            for recordType in self.recordTypes():
                self.log_debug("Master fetching %s from directory" % (recordType,))
                cacheFile = cacheDir.child(recordType)
                try:
                    results = self._queryDirectory(recordType)
                except Exception, e:
                    self.log_error("Master query for %s failed: %s" % (recordType, e))
                    continue

                results.sort()
                numNewResults = len(results)
                pickled = pickle.dumps(results)
                needsWrite = True
                if cacheFile.exists():
                    prevPickled = cacheFile.getContent()
                    if prevPickled == pickled:
                        needsWrite = False
                    else:
                        prevResults = pickle.loads(prevPickled)
                        numPrevResults = len(prevResults)
                        if numPrevResults == 0:
                            needsWrite = True
                        else:
                            if float(numNewResults) / numPrevResults < 0.5:
                                # New results is less than half of what it used
                                # to be -- this indicates we might not have
                                # gotten back enough records from OD.  Don't
                                # write out the file, but log an error.
                                self.log_error("OD results for %s substantially less than last time: was %d, now %d." % (recordType, numPrevResults, numNewResults))
                                needsWrite = False
                                continue

                if needsWrite:
                    self.log_info("Saving cache file for %s (%d items)" % (recordType, numNewResults))
                    cacheFile.setContent(pickled)
                else:
                    self.log_debug("%s info hasn't changed" % (recordType,))

        def _refreshInThread(self):
            return deferToThread(_refresh, self)

        _refresh(self)

        if loop:
            LoopingCall(_refreshInThread, self).start(self.cacheTimeout * 60)



    def _expandGroupMembership(self, members, nestedGroups, processedGUIDs=None):

        if processedGUIDs is None:
            processedGUIDs = set()

        if isinstance(members, str):
            members = [members]

        if isinstance(nestedGroups, str):
            nestedGroups = [nestedGroups]

        for memberGUID in members:
            if memberGUID not in processedGUIDs:
                processedGUIDs.add(memberGUID)
                yield memberGUID

        for groupGUID in nestedGroups:
            if groupGUID in processedGUIDs:
                continue

            self.log_debug("opendirectory.queryRecordsWithAttribute_list(%r,%r,%r,%r,%r,%r,%r)" % (
                self.directory,
                dsattributes.kDS1AttrGeneratedUID,
                groupGUID,
                dsattributes.eDSExact,
                False,
                dsattributes.kDSStdRecordTypeGroups,
                [dsattributes.kDSNAttrGroupMembers, dsattributes.kDSNAttrNestedGroups]
            ))
            result = opendirectory.queryRecordsWithAttribute_list(
                self.directory,
                dsattributes.kDS1AttrGeneratedUID,
                groupGUID,
                dsattributes.eDSExact,
                False,
                dsattributes.kDSStdRecordTypeGroups,
                [dsattributes.kDSNAttrGroupMembers, dsattributes.kDSNAttrNestedGroups]
            )

            if not result:
                self.log_error("Couldn't find group %s when trying to expand nested groups."
                             % (groupGUID,))
                continue

            group = result[0][1]

            processedGUIDs.add(groupGUID)

            for GUID in self._expandGroupMembership(
                group.get(dsattributes.kDSNAttrGroupMembers, []),
                group.get(dsattributes.kDSNAttrNestedGroups, []),
                processedGUIDs
            ):
                yield GUID

    def __cmp__(self, other):
        if not isinstance(other, DirectoryRecord):
            return super(DirectoryRecord, self).__eq__(other)

        for attr in ("directory", "node"):
            diff = cmp(getattr(self, attr), getattr(other, attr))
            if diff != 0:
                return diff
        return 0

    def __hash__(self):
        h = hash(self.__class__)
        for attr in ("directory", "node"):
            h = (h + hash(getattr(self, attr))) & sys.maxint
        return h

    def _getMemcacheClient(self, refresh=False):
        if refresh or not hasattr(self, "memcacheClient"):
            self.memcacheClient = memcacheclient.ClientFactory.getClient(['%s:%s' %
                (config.Memcached.Pools.Default.BindAddress, config.Memcached.Pools.Default.Port)],
                debug=0, pickleProtocol=2)
        return self.memcacheClient


    def recordTypes(self):
        return (
            DirectoryService.recordType_users,
            DirectoryService.recordType_groups,
            DirectoryService.recordType_locations,
            DirectoryService.recordType_resources,
        )

    def _storage(self, recordType):
        try:
            storage = self._records[recordType]
        except KeyError:
            self.reloadCache(recordType)
            storage = self._records[recordType]
        else:
            if storage["status"] == "stale":
                storage["status"] = "loading"

                def onError(f):
                    storage["status"] = "stale" # Keep trying
                    self.log_error(
                        "Unable to load records of type %s from OpenDirectory due to unexpected error: %s"
                        % (recordType, f)
                    )

                d = deferToThread(self.reloadCache, recordType)
                d.addErrback(onError)

        return storage

    def recordsForType(self, recordType):
        """
        @param recordType: a record type
        @return: a dictionary containing all records for the given record
        type.  Keys are short names and values are the cooresponding
        OpenDirectoryRecord for the given record type.
        """
        return self._storage(recordType)["records"]

    def listRecords(self, recordType):
        return self.recordsForType(recordType).itervalues()

    def recordWithShortName(self, recordType, shortName):
        try:
            return self.recordsForType(recordType)[shortName]
        except KeyError:
            return None

    def recordWithGUID(self, guid):
        for recordType in self.recordTypes():
            record = self._storage(recordType)["guids"].get(guid, None)
            if record:
                return record
        else:
            return None


    def recordWithCalendarUserAddress(self, address):
        address = address.lower()

        for recordType in self.recordTypes():
            record = self._storage(recordType)["cuaddrs"].get(address, None)
            if record:
                return record
        else:
            return None


    def groupsForGUID(self, guid):
        
        # Lookup in index
        try:
            return self._storage(DirectoryService.recordType_groups)["groupsForGUID"][guid]
        except KeyError:
            return ()

    def proxiesForGUID(self, recordType, guid):
        
        # Lookup in index
        try:
            return self._storage(recordType)["proxiesForGUID"][guid]
        except KeyError:
            return ()

    def readOnlyProxiesForGUID(self, recordType, guid):
        
        # Lookup in index
        try:
            return self._storage(recordType)["readOnlyProxiesForGUID"][guid]
        except KeyError:
            return ()

    def _indexGroup(self, group, guids, index):
        for guid in guids:
            index.setdefault(guid, set()).add(group)

    _ODFields = {
        'fullName' : dsattributes.kDS1AttrDistinguishedName,
        'firstName' : dsattributes.kDS1AttrFirstName,
        'lastName' : dsattributes.kDS1AttrLastName,
        'emailAddresses' : dsattributes.kDSNAttrEMailAddress,
    }

    _toODRecordTypes = {
        DirectoryService.recordType_users :
            dsattributes.kDSStdRecordTypeUsers,
        DirectoryService.recordType_locations :
            dsattributes.kDSStdRecordTypePlaces,
        DirectoryService.recordType_groups :
            dsattributes.kDSStdRecordTypeGroups,
        DirectoryService.recordType_resources :
            dsattributes.kDSStdRecordTypeResources,
    }

    _fromODRecordTypes = dict([(b, a) for a, b in _toODRecordTypes.iteritems()])

    def recordsMatchingFields(self, fields, operand="or", recordType=None):

        # Note that OD applies case-sensitivity globally across the entire
        # query, not per expression, so the current code uses whatever is
        # specified in the last field in the fields list

        operand = (dsquery.expression.OR if operand == "or"
            else dsquery.expression.AND)

        expressions = []
        for field, value, caseless, matchType in fields:
            if field in self._ODFields:
                ODField = self._ODFields[field]
                if matchType == "starts-with":
                    comparison = dsattributes.eDSStartsWith
                else:
                    comparison = dsattributes.eDSContains
                expressions.append(dsquery.match(ODField, value, comparison))


        if recordType is None:
            recordTypes = self._toODRecordTypes.values()
        else:
            recordTypes = (self._toODRecordTypes[recordType],)

        for recordType in recordTypes:

            try:
                self.log_info("Calling OD: Type %s, Operand %s, Caseless %s, %s" % (recordType, operand, caseless, fields))
                results = opendirectory.queryRecordsWithAttributes(
                    self.directory,
                    dsquery.expression(operand, expressions).generate(),
                    caseless,
                    recordType,
                    [ dsattributes.kDS1AttrGeneratedUID ]
                )
                self.log_info("Got back %d records from OD" % (len(results),))
                for key, val in results.iteritems():
                    self.log_debug("OD result: %s %s" % (key, val))
                    try:
                        guid = val[dsattributes.kDS1AttrGeneratedUID]
                        rec = self.recordWithGUID(guid)
                        if rec:
                            yield rec
                    except KeyError:
                        pass

            except Exception, e:
                self.log_error("OD search failed: %s" % (e,))
                raise

    def reloadCache(self, recordType, forceUpdate=False):

        def rot():
            storage["status"] = "stale"
            removals = set()
            for call in self._delayedCalls:
                if not call.active():
                    removals.add(call)
            for item in removals:
                self._delayedCalls.remove(item)

        cacheTimeout = 60 # child processes always check once per minute

        dataRoot = FilePath(config.DataRoot)
        cacheDir = dataRoot.child("DirectoryCache")
        if not cacheDir.exists():
            self.log_error("Directory cache directory for does not exist: %s" % (cacheDir.path,))
            return

        cacheFile = cacheDir.child(recordType)
        if not cacheFile.exists():
            self.log_debug("Directory cache file for %s does not exist: %s" % (recordType, cacheFile.path))
            results = []
            lastModified = 0
        else:
            lastModified = cacheFile.getModificationTime()
            try:
                storage = self._records[recordType]
                if not forceUpdate and (lastModified <= storage["last modified"]):
                    self.log_debug("Directory cache file for %s unchanged" % (recordType,))
                    storage["status"] = "new" # mark this as not stale
                    self._delayedCalls.add(callLater(cacheTimeout, rot))
                    return
            except KeyError:
                # Haven't read the file before
                pass

            self.log_debug("Reloading %s record cache" % (recordType,))

            pickled = cacheFile.getContent()
            results = pickle.loads(pickled)
            # results = self._queryDirectory(recordType)

        records = {}
        guids   = {}
        cuaddrs = {}

        disabledNames = set()
        disabledGUIDs = set()
        
        if recordType == DirectoryService.recordType_groups:
            groupsForGUID = {}

        def _setFromAttribute(attribute, lower=False):
            if attribute:
                if isinstance(attribute, str):
                    return set((attribute.lower() if lower else attribute,))
                else:
                    return set([item.lower() if lower else item for item in attribute])
            else:
                return ()

        for (recordShortName, value) in results:

            # Now get useful record info.
            recordGUID     = value.get(dsattributes.kDS1AttrGeneratedUID)
            recordFullName = value.get(dsattributes.kDS1AttrDistinguishedName)
            recordEmailAddresses = _setFromAttribute(value.get(dsattributes.kDSNAttrEMailAddress), lower=True)
            recordNodeName = value.get(dsattributes.kDSNAttrMetaNodeLocation)

            if not recordGUID:
                self.log_debug("Record (%s)%s in node %s has no GUID; ignoring."
                               % (recordType, recordShortName, recordNodeName))
                continue

            # Special case for groups, which have members.
            if recordType == DirectoryService.recordType_groups:
                memberGUIDs = value.get(dsattributes.kDSNAttrGroupMembers)
                if memberGUIDs is None:
                    memberGUIDs = ()
                elif type(memberGUIDs) is str:
                    memberGUIDs = (memberGUIDs,)
                nestedGUIDs = value.get(dsattributes.kDSNAttrNestedGroups)
                if nestedGUIDs:
                    if type(nestedGUIDs) is str:
                        nestedGUIDs = (nestedGUIDs,)
                    memberGUIDs += tuple(nestedGUIDs)
            else:
                memberGUIDs = ()

            record = OpenDirectoryRecord(
                service               = self,
                recordType            = recordType,
                guid                  = recordGUID,
                nodeName              = recordNodeName,
                shortName             = recordShortName,
                fullName              = recordFullName,
                emailAddresses        = recordEmailAddresses,
                memberGUIDs           = memberGUIDs,
            )

            # Look up augment information
            # TODO: this needs to be deferred but for now we hard code the deferred result because
            # we know it is completing immediately.
            d = augment.AugmentService.getAugmentRecord(record.guid)
            d.addCallback(lambda x:record.addAugmentInformation(x))

            def disableRecord(record):
                self.log_warn("Record disabled due to conflict (record name and GUID must match): %s" % (record,))

                shortName = record.shortName
                guid      = record.guid
                cuaddrset = record.calendarUserAddresses

                disabledNames.add(shortName)
                disabledGUIDs.add(guid)

                if shortName in records:
                    del records[shortName]
                if guid in guids:
                    del guids[guid]
                for cuaddr in cuaddrset:
                    if cuaddr in cuaddrs:
                        del cuaddrs[cuaddr]

            # Check for disabled items
            if record.shortName in disabledNames or record.guid in disabledGUIDs:
                disableRecord(record)
            else:
                # Check for duplicate items and disable all names/guids for mismatched duplicates.
                if record.shortName in records:
                    existing_record = records[record.shortName]
                elif record.guid in guids:
                    existing_record = guids[record.guid]
                else:
                    existing_record = None

                if existing_record is not None:
                    if record.guid != existing_record.guid or record.shortName != existing_record.shortName:
                        disableRecord(existing_record)
                        disableRecord(record)

                if record.shortName not in disabledNames:
                    records[record.shortName] = guids[record.guid] = record
                    for cuaddr in record.calendarUserAddresses:
                        cuaddrs[cuaddr] = record
                    self.log_debug("Added record %s to OD record cache" % (record,))

                    # Do group indexing if needed
                    if recordType == DirectoryService.recordType_groups:
                        self._indexGroup(record, record._memberGUIDs, groupsForGUID)

        #
        # Replace the entire cache
        #
        storage = {
            "status"        : "new",
            "records"       : records,
            "guids"         : guids,
            "cuaddrs"       : cuaddrs,
            "disabled names": disabledNames,
            "disabled guids": disabledGUIDs,
            "last modified" : lastModified,
        }

        # Add group indexing if needed
        if recordType == DirectoryService.recordType_groups:
            storage["groupsForGUID"] = groupsForGUID

        self._delayedCalls.add(callLater(cacheTimeout, rot))

        self._records[recordType] = storage

        self.log_info(
            "Added %d records to %s OD record cache; expires in %d seconds"
            % (len(self._records[recordType]["guids"]), recordType, cacheTimeout)
        )

    def _queryDirectory(self, recordType):
        attrs = [
            dsattributes.kDS1AttrGeneratedUID,
            dsattributes.kDS1AttrDistinguishedName,
            dsattributes.kDSNAttrEMailAddress,
            dsattributes.kDSNAttrServicesLocator,
            dsattributes.kDSNAttrMetaNodeLocation,
        ]

        if recordType == DirectoryService.recordType_users:
            listRecordType = dsattributes.kDSStdRecordTypeUsers

        elif recordType == DirectoryService.recordType_groups:
            listRecordType = dsattributes.kDSStdRecordTypeGroups
            attrs.append(dsattributes.kDSNAttrGroupMembers)
            attrs.append(dsattributes.kDSNAttrNestedGroups)

        elif recordType == DirectoryService.recordType_locations:
            listRecordType = dsattributes.kDSStdRecordTypePlaces
        
        elif recordType == DirectoryService.recordType_resources:
            listRecordType = dsattributes.kDSStdRecordTypeResources
        
        else:
            raise UnknownRecordTypeError("Unknown Open Directory record type: %s" % (recordType))

        try:
            self.log_debug("opendirectory.listAllRecordsWithAttributes_list(%r,%r,%r)" % (
                self.directory,
                listRecordType,
                attrs,
            ))
            results = opendirectory.listAllRecordsWithAttributes_list(
                self.directory,
                listRecordType,
                attrs,
            )
        except opendirectory.ODError, ex:
            self.log_error("Open Directory (node=%s) error: %s" % (self.realmName, str(ex)))
            raise

        return results

class OpenDirectoryRecord(DirectoryRecord):
    """
    Open Directory implementation of L{IDirectoryRecord}.
    """
    def __init__(
        self, service, recordType, guid, nodeName, shortName, fullName,
        emailAddresses, memberGUIDs,
    ):
        super(OpenDirectoryRecord, self).__init__(
            service               = service,
            recordType            = recordType,
            guid                  = guid,
            shortName             = shortName,
            fullName              = fullName,
            emailAddresses        = emailAddresses,
        )
        self.nodeName = nodeName
        self._memberGUIDs = tuple(memberGUIDs)

    def __repr__(self):
        if self.service.realmName == self.nodeName:
            location = self.nodeName
        else:
            location = "%s->%s" % (self.service.realmName, self.nodeName)

        return "<%s[%s@%s(%s)] %s(%s) %r>" % (
            self.__class__.__name__,
            self.recordType,
            self.service.guid,
            location,
            self.guid,
            self.shortName,
            self.fullName
        )

    def members(self):
        if self.recordType != DirectoryService.recordType_groups:
            return

        for guid in self._memberGUIDs:
            userRecord = self.service.recordWithGUID(guid)
            if userRecord is not None:
                yield userRecord

    def groups(self):
        return self.service.groupsForGUID(self.guid)

    def getMemcacheKey(self, shortName):
        key = "auth-%s" % (md5(shortName).hexdigest(),)
        return key

    def verifyCredentials(self, credentials):
        if isinstance(credentials, UsernamePassword):
            # Check cached password which is an md5 hexdigest

            credPassword = md5(credentials.password).hexdigest()
            try:
                if credPassword == self.password:
                    return True
            except AttributeError:
                # No locally stored password; check memcached
                key = self.getMemcacheKey(self.shortName)
                memcachePassword = self.service._getMemcacheClient().get(key)
                if memcachePassword is not None:
                    if credPassword == memcachePassword:
                        # Memcached version matches, store locally
                        self.password = credPassword
                        return True

            # No local version, *or* local version differs; check directory services
            try:
                if opendirectory.authenticateUserBasic(self.service.directory, self.nodeName, self.shortName, credentials.password):
                    # Cache the password to avoid future DS queries
                    self.password = credPassword
                    key = self.getMemcacheKey(self.shortName)
                    self.service._getMemcacheClient().set(key, self.password, time=self.service.cacheTimeout*60)
                    return True
            except opendirectory.ODError, e:
                self.log_error("Open Directory (node=%s) error while performing basic authentication for user %s: %s"
                            % (self.service.realmName, self.shortName, e))

            return False

        elif isinstance(credentials, DigestedCredentials):
            #
            # We need a special format for the "challenge" and "response" strings passed into open directory, as it is
            # picky about exactly what it receives.
            #
            try:
                challenge = 'Digest realm="%(realm)s", nonce="%(nonce)s", algorithm=%(algorithm)s' % credentials.fields
                response = (
                    'Digest username="%(username)s", '
                    'realm="%(realm)s", '
                    'nonce="%(nonce)s", '
                    'uri="%(uri)s", '
                    'response="%(response)s",'
                    'algorithm=%(algorithm)s'
                ) % credentials.fields
            except KeyError, e:
                self.log_error(
                    "Open Directory (node=%s) error while performing digest authentication for user %s: "
                    "missing digest response field: %s in: %s"
                    % (self.service.realmName, self.shortName, e, credentials.fields)
                )
                return False

            try:
                if self.digestcache[credentials.fields["uri"]] == response:
                    return True
            except (AttributeError, KeyError):
                pass

            try:
                if opendirectory.authenticateUserDigest(
                    self.service.directory,
                    self.nodeName,
                    self.shortName,
                    challenge,
                    response,
                    credentials.method
                ):
                    try:
                        cache = self.digestcache
                    except AttributeError:
                        cache = self.digestcache = {}

                    cache[credentials.fields["uri"]] = response

                    return True
                else:
                    self.log_debug(
"""Open Directory digest authentication failed with:
    Nodename:  %s
    Username:  %s
    Challenge: %s
    Response:  %s
    Method:    %s
""" % (self.nodeName, self.shortName, challenge, response, credentials.method))

            except opendirectory.ODError, e:
                self.log_error(
                    "Open Directory (node=%s) error while performing digest authentication for user %s: %s"
                    % (self.service.realmName, self.shortName, e)
                )
                return False

            return False

        return super(OpenDirectoryRecord, self).verifyCredentials(credentials)

class OpenDirectoryInitError(DirectoryError):
    """
    OpenDirectory initialization error.
    """
