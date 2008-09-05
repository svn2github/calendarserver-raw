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
Apple Open Directory directory service implementation.
"""

__all__ = [
    "OpenDirectoryService",
    "OpenDirectoryInitError",
]

import itertools
import sys
from random import random

import opendirectory
import dsattributes
import dsquery

from twisted.internet.reactor import callLater
from twisted.internet.threads import deferToThread
from twisted.cred.credentials import UsernamePassword
from twisted.web2.auth.digest import DigestedCredentials

from twistedcaldav.config import config
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.directory import DirectoryError, UnknownRecordTypeError

from plistlib import readPlistFromString

serverPreferences = '/Library/Preferences/com.apple.servermgr_info.plist'
saclGroup = 'com.apple.access_calendar'

class OpenDirectoryService(DirectoryService):
    """
    Open Directory implementation of L{IDirectoryService}.
    """
    baseGUID = "891F8321-ED02-424C-BA72-89C32F215C1E"

    def __repr__(self):
        return "<%s %r: %r>" % (self.__class__.__name__, self.realmName, self.node)

    def __init__(
        self,
        node="/Search",
        restrictEnabledRecords=False,
        restrictToGroupGUID="",
        restrictToGroupName="",
        dosetup=True,
        cacheTimeout=30
    ):
        """
        @param node: an OpenDirectory node name to bind to.
        @param restrictEnabledRecords: C{True} if a group in the directory is to be used to determine
            which calendar users are enabled.
        @param restrictToGroupGUID: C{str} guid of group used to restrict enabled users.
        @param restrictToGroupName: C{str} name of group used to restrict enabled users, not
            used if restrictToGroupGUID is set.
        @param dosetup: if C{True} then the directory records are initialized,
                        if C{False} they are not.
                        This should only be set to C{False} when doing unit tests.
        @param cacheTimeout: C{int} number of minutes before cache is invalidated.
        """
        try:
            directory = opendirectory.odInit(node)
        except opendirectory.ODError, e:
            self.log_error("Open Directory (node=%s) Initialization error: %s" % (node, e))
            raise

        self.realmName = node
        self.directory = directory
        self.node = node
        self.restrictEnabledRecords = restrictEnabledRecords
        self.restrictToGroupGUID = restrictToGroupGUID
        self.restrictToGroupName = restrictToGroupName
        self.restrictedGUIDs = None
        self.cacheTimeout = cacheTimeout
        self._records = {}
        self._delayedCalls = set()

        self.doSACLs = config.EnableSACLs
        self.SACLwasEnabled = False

        if dosetup:
            for recordType in self.recordTypes():
                self.recordsForType(recordType)

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

    def _calendarUserAddresses(self, recordType, recordName, record):
        """
        Extract specific attributes from the directory record for use as calendar user address.
        
        @param recordName: a C{str} containing the record name being operated on.
        @param record: a C{dict} containing the attributes retrieved from the directory.
        @return: a C{set} of C{str} for each expanded calendar user address.
        """
        # Now get the addresses
        result = set()
        
        # Add each email address as a mailto URI
        emails = record.get(dsattributes.kDSNAttrEMailAddress)
        if emails is not None:
            if isinstance(emails, str):
                emails = [emails]
            for email in emails:
                result.add("mailto:%s" % (email,))
                
        return result

    def _parseResourceInfo(self, plist, guid, shortname):
        """
        Parse OD ResourceInfo attribute and extract information that the server needs.

        @param plist: the plist that is the attribute value.
        @type plist: str
        @param guid: the directory GUID of the record being parsed.
        @type guid: str
        @param shortname: the record shortname of the record being parsed.
        @type shortname: str
        @return: a C{tuple} of C{bool} for auto-accept, C{str} for proxy GUID, C{str} for read-only proxy GUID.
        """
        try:
            plist = readPlistFromString(plist)
            wpframework = plist.get("com.apple.WhitePagesFramework", {})
            autoaccept = wpframework.get("AutoAcceptsInvitation", False)
            proxy = wpframework.get("CalendaringDelegate", None)
            read_only_proxy = wpframework.get("ReadOnlyCalendaringDelegate", None)
        except AttributeError:
            self.log_error(
                "Failed to parse ResourceInfo attribute of record %s (%s): %s" %
                (shortname, guid, plist,)
            )
            autoaccept = False
            proxy = None
            read_only_proxy = None

        return (autoaccept, proxy, read_only_proxy,)

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

                # Reload the restricted access group details if reloading user records
                if recordType == DirectoryService.recordType_users:
                    self.restrictedGUIDs = None

                d = deferToThread(self.reloadCache, recordType)
                d.addErrback(onError)

        return storage

    def recordsForType(self, recordType):
        """
        @param recordType: a record type
        @return: a dictionary containing all records for the given record
        type.  Keys are short names and values are the corresponding
        OpenDirectoryRecord for the given record type.
        """
        return self._storage(recordType)["records"]

    def listRecords(self, recordType):
        return self.recordsForType(recordType).itervalues()

    def recordWithShortName(self, recordType, shortName):
        try:
            return self.recordsForType(recordType)[shortName]
        except KeyError:
            # Cache miss; try looking the record up, in case it is new
            # FIXME: This is a blocking call (hopefully it's a fast one)
            self.reloadCache(recordType, shortName=shortName)
            return self.recordsForType(recordType).get(shortName, None)

    def recordWithGUID(self, guid):
        def lookup():
            for recordType in self.recordTypes():
                record = self._storage(recordType)["guids"].get(guid, None)
                if record:
                    return record
            else:
                return None

        record = lookup()

        if record is None:
            # Cache miss; try looking the record up, in case it is new
            for recordType in self.recordTypes():
                self.reloadCache(recordType, guid=guid)
                record = lookup()
                if record is not None:
                    self.log_info("Faulted record with GUID %s into %s record cache"
                                  % (guid, recordType))
                    break
            else:
                self.log_info("Unable to find any record with GUID %s" % (guid,))

        return record

    def reloadCache(self, recordType, shortName=None, guid=None):
        if shortName:
            self.log_info("Faulting record %s into %s record cache" % (shortName, recordType))
        elif guid is None:
            self.log_info("Reloading %s record cache" % (recordType,))

        results = self._queryDirectory(recordType, shortName=shortName, guid=guid)
        
        if shortName is None and guid is None:
            records = {}
            guids   = {}

            disabledNames = set()
            disabledGUIDs = set()
        else:
            storage = self._records[recordType]

            records = storage["records"]
            guids   = storage["guids"]

            disabledNames = storage["disabled names"]
            disabledGUIDs = storage["disabled guids"]

        enabled_count = 0
        for (recordShortName, value) in results:

            # Now get useful record info.
            recordGUID     = value.get(dsattributes.kDS1AttrGeneratedUID)
            recordFullName = value.get(dsattributes.kDS1AttrDistinguishedName)
            recordNodeName = value.get(dsattributes.kDSNAttrMetaNodeLocation)

            if not recordGUID:
                self.log_debug("Record (%s)%s in node %s has no GUID; ignoring."
                               % (recordType, recordShortName, recordNodeName))
                continue

            # Determine enabled state
            enabledForCalendaring = True

            if self.doSACLs and self.SACLwasEnabled and recordType == DirectoryService.recordType_users:
                # We have already filtered based on allowed GUIDs
                enabledForCalendaring = True

            elif not self.restrictEnabledRecords:
                # Enable everything
                enabledForCalendaring = True
            
            elif self.restrictedGUIDs is not None:
                enabledForCalendaring = recordGUID in self.restrictedGUIDs

            if not enabledForCalendaring:
                # Some records we want to keep even though they are not enabled for calendaring.
                # Others we discard.
                if recordType in (
                    DirectoryService.recordType_users,
                    DirectoryService.recordType_groups,
                ):
                    self.log_debug(
                        "Record (%s) %s is not enabled for calendaring but may be used in ACLs"
                        % (recordType, recordShortName)
                    )
                else:
                    self.log_debug(
                        "Record (%s) %s is not enabled for calendaring"
                        % (recordType, recordShortName)
                    )
                    continue
            else:
                enabled_count += 1

            # Get calendar user addresses from directory record.
            if enabledForCalendaring:
                calendarUserAddresses = self._calendarUserAddresses(recordType, recordShortName, value)
            else:
                calendarUserAddresses = ()

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

            # Special case for resources and locations
            autoSchedule = False
            proxyGUIDs = ()
            readOnlyProxyGUIDs = ()
            if recordType in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
                resourceInfo = value.get(dsattributes.kDSNAttrResourceInfo)
                if resourceInfo is not None:
                    autoSchedule, proxy, read_only_proxy = self._parseResourceInfo(resourceInfo, recordGUID, recordShortName)
                    if proxy:
                        proxyGUIDs = (proxy,)
                    if read_only_proxy:
                        readOnlyProxyGUIDs = (read_only_proxy,)

            record = OpenDirectoryRecord(
                service               = self,
                recordType            = recordType,
                guid                  = recordGUID,
                nodeName              = recordNodeName,
                shortName             = recordShortName,
                fullName              = recordFullName,
                calendarUserAddresses = calendarUserAddresses,
                autoSchedule          = autoSchedule,
                enabledForCalendaring = enabledForCalendaring,
                memberGUIDs           = memberGUIDs,
                proxyGUIDs            = proxyGUIDs,
                readOnlyProxyGUIDs    = readOnlyProxyGUIDs,
            )

            def disableRecord(record):
                self.log_warn("Record disabled due to conflict (record name and GUID must match): %s" % (record,))

                shortName = record.shortName
                guid      = record.guid

                disabledNames.add(shortName)
                disabledGUIDs.add(guid)

                if shortName in records:
                    del records[shortName]
                if guid in guids:
                    del guids[guid]

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
                        
                        if existing_record.enabledForCalendaring:
                            enabled_count -= 1

                if record.shortName not in disabledNames:
                    records[record.shortName] = guids[record.guid] = record
                    self.log_debug("Added record %s to OD record cache" % (record,))

        if shortName is None and guid is None:
            #
            # Replace the entire cache
            #
            storage = {
                "status"        : "new",
                "records"       : records,
                "guids"         : guids,
                "disabled names": disabledNames,
                "disabled guids": disabledGUIDs,
            }

            def rot():
                storage["status"] = "stale"
                removals = set()
                for call in self._delayedCalls:
                    if not call.active():
                        removals.add(call)
                for item in removals:
                    self._delayedCalls.remove(item)

            #
            # Add jitter/fuzz factor to avoid stampede for large OD query
            # Max out the jitter at 60 minutes
            #
            cacheTimeout = min(self.cacheTimeout, 60) * 60
            cacheTimeout = (cacheTimeout * random()) - (cacheTimeout / 2)
            cacheTimeout += self.cacheTimeout * 60
            self._delayedCalls.add(callLater(cacheTimeout, rot))

            self._records[recordType] = storage

            self.log_info(
                "Added %d (%d enabled) records to %s OD record cache; expires in %d seconds"
                % (len(self._records[recordType]["guids"]), enabled_count, recordType, cacheTimeout)
            )

    def _queryDirectory(self, recordType, shortName=None, guid=None):
        attrs = [
            dsattributes.kDS1AttrGeneratedUID,
            dsattributes.kDS1AttrDistinguishedName,
            dsattributes.kDSNAttrEMailAddress,
            dsattributes.kDSNAttrMetaNodeLocation,
        ]

        query = None
        if recordType == DirectoryService.recordType_users:
            listRecordType = dsattributes.kDSStdRecordTypeUsers

        elif recordType == DirectoryService.recordType_groups:
            listRecordType = dsattributes.kDSStdRecordTypeGroups
            attrs.append(dsattributes.kDSNAttrGroupMembers)
            attrs.append(dsattributes.kDSNAttrNestedGroups)

        elif recordType == DirectoryService.recordType_locations:
            listRecordType = dsattributes.kDSStdRecordTypePlaces
            attrs.append(dsattributes.kDSNAttrResourceInfo)
        
        elif recordType == DirectoryService.recordType_resources:
            listRecordType = dsattributes.kDSStdRecordTypeResources
            attrs.append(dsattributes.kDSNAttrResourceInfo)
        
        else:
            raise UnknownRecordTypeError("Unknown Open Directory record type: %s" % (recordType))

        # Query policy:
        #
        # For Users - always check for SACL and use that to determine enabled users
        #             if no SACL then do same processing as for other types
        #
        # Other types - load all records. If restricted access is in place, load the
        #               group membership for the restricted group and enable those users
        #               in the group

        processed = False
        if self.doSACLs and recordType == DirectoryService.recordType_users:
            if shortName is None and guid is None:
                self.log_debug("Doing SACL membership check")
                self.log_debug("opendirectory.queryRecordsWithAttribute_list(%r,%r,%r,%r,%r,%r,%r)" % (
                    self.directory,
                    dsattributes.kDSNAttrRecordName,
                    saclGroup,
                    dsattributes.eDSExact,
                    False,
                    dsattributes.kDSStdRecordTypeGroups,
                    [dsattributes.kDSNAttrGroupMembers, dsattributes.kDSNAttrNestedGroups],
                ))
                results = opendirectory.queryRecordsWithAttribute_list(
                    self.directory,
                    dsattributes.kDSNAttrRecordName,
                    saclGroup,
                    dsattributes.eDSExact,
                    False,
                    dsattributes.kDSStdRecordTypeGroups,
                    [dsattributes.kDSNAttrGroupMembers, dsattributes.kDSNAttrNestedGroups]
                )

                if len(results) == 1:
                    self.SACLwasEnabled = True
                    members      = results[0][1].get(dsattributes.kDSNAttrGroupMembers, [])
                    nestedGroups = results[0][1].get(dsattributes.kDSNAttrNestedGroups, [])
    
                    guidQueries = []
    
                    for GUID in self._expandGroupMembership(members, nestedGroups):
                        guidQueries.append(
                            dsquery.match(dsattributes.kDS1AttrGeneratedUID, GUID, dsattributes.eDSExact)
                        )
    
                    if not guidQueries:
                        self.log_warn("No SACL enabled users found.")
                        return ()
    
                    query = dsquery.expression(dsquery.expression.OR, guidQueries)
                    self.log_debug("Got %d SACL members" % (len(guidQueries),))
                    processed = True
                else:
                    self.SACLwasEnabled = False
                    self.log_debug("SACL not enabled for calendar service")
        
        if not processed and self.restrictEnabledRecords and self.restrictedGUIDs is None:

            attributeToMatch = dsattributes.kDS1AttrGeneratedUID if self.restrictToGroupGUID else dsattributes.kDSNAttrRecordName 
            valueToMatch = self.restrictToGroupGUID if self.restrictToGroupGUID else self.restrictToGroupName

            self.log_debug("Doing restricted group membership check")
            self.log_debug("opendirectory.queryRecordsWithAttribute_list(%r,%r,%r,%r,%r,%r,%r)" % (
                self.directory,
                attributeToMatch,
                valueToMatch,
                dsattributes.eDSExact,
                False,
                dsattributes.kDSStdRecordTypeGroups,
                [dsattributes.kDSNAttrGroupMembers, dsattributes.kDSNAttrNestedGroups,],
            ))
            results = opendirectory.queryRecordsWithAttribute_list(
                self.directory,
                attributeToMatch,
                valueToMatch,
                dsattributes.eDSExact,
                False,
                dsattributes.kDSStdRecordTypeGroups,
                [dsattributes.kDSNAttrGroupMembers, dsattributes.kDSNAttrNestedGroups,],
            )
    
            if len(results) == 1:
                members      = results[0][1].get(dsattributes.kDSNAttrGroupMembers, [])
                nestedGroups = results[0][1].get(dsattributes.kDSNAttrNestedGroups, [])
            else:
                members = []
                nestedGroups = []

            self.restrictedGUIDs = set()
            for expanded_guid in self._expandGroupMembership(members, nestedGroups):
                self.restrictedGUIDs.add(expanded_guid)
            self.log_debug("Got %d restricted group members" % (len(self.restrictedGUIDs),))

        if shortName is not None:
            subquery = dsquery.match(dsattributes.kDSNAttrRecordName, shortName, dsattributes.eDSExact)
        elif guid is not None:
            subquery = dsquery.match(dsattributes.kDS1AttrGeneratedUID, guid, dsattributes.eDSExact)
        else:
            subquery = None

        if subquery is not None:
            if query is None:
                query = subquery
            else:
                query = dsquery.expression(dsquery.expression.AND, (subquery, query))

        try:
            if query:
                if isinstance(query, dsquery.match):
                    self.log_debug("opendirectory.queryRecordsWithAttribute_list(%r,%r,%r,%r,%r,%r,%r)" % (
                        self.directory,
                        query.attribute,
                        query.value,
                        query.matchType,
                        False,
                        listRecordType,
                        attrs,
                    ))
                    results = opendirectory.queryRecordsWithAttribute_list(
                        self.directory,
                        query.attribute,
                        query.value,
                        query.matchType,
                        False,
                        listRecordType,
                        attrs,
                    )
                else:
                    self.log_debug("opendirectory.queryRecordsWithAttribute_list(%r,%r,%r,%r,%r)" % (
                        self.directory,
                        query.generate(),
                        False,
                        listRecordType,
                        attrs,
                    ))
                    results = opendirectory.queryRecordsWithAttributes_list(
                        self.directory,
                        query.generate(),
                        False,
                        listRecordType,
                        attrs,
                    )
            else:
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
        calendarUserAddresses, autoSchedule, enabledForCalendaring,
        memberGUIDs, proxyGUIDs, readOnlyProxyGUIDs,
    ):
        super(OpenDirectoryRecord, self).__init__(
            service               = service,
            recordType            = recordType,
            guid                  = guid,
            shortName             = shortName,
            fullName              = fullName,
            calendarUserAddresses = calendarUserAddresses,
            autoSchedule          = autoSchedule,
            enabledForCalendaring = enabledForCalendaring,
        )
        self.nodeName = nodeName
        self._memberGUIDs = tuple(memberGUIDs)
        self._proxyGUIDs = tuple(proxyGUIDs)
        self._readOnlyProxyGUIDs = tuple(readOnlyProxyGUIDs)

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
        for groupRecord in self.service.recordsForType(DirectoryService.recordType_groups).itervalues():
            if self.guid in groupRecord._memberGUIDs:
                yield groupRecord

    def proxies(self):
        if self.recordType not in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
            return

        for guid in self._proxyGUIDs:
            proxyRecord = self.service.recordWithGUID(guid)
            if proxyRecord is None:
                self.log_error("No record for proxy in %s with GUID %s" % (self.shortName, guid))
            else:
                yield proxyRecord

    def proxyFor(self):
        for proxyRecord in itertools.chain(
            self.service.recordsForType(DirectoryService.recordType_resources).itervalues(),
            self.service.recordsForType(DirectoryService.recordType_locations).itervalues(),
        ):
            if self.guid in proxyRecord._proxyGUIDs:
                yield proxyRecord

    def readOnlyProxies(self):
        if self.recordType not in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
            return

        for guid in self._readOnlyProxyGUIDs:
            proxyRecord = self.service.recordWithGUID(guid)
            if proxyRecord is None:
                self.log_error("No record for proxy in %s with GUID %s" % (self.shortName, guid))
            else:
                yield proxyRecord

    def readOnlyProxyFor(self):
        for proxyRecord in itertools.chain(
            self.service.recordsForType(DirectoryService.recordType_resources).itervalues(),
            self.service.recordsForType(DirectoryService.recordType_locations).itervalues(),
        ):
            if self.guid in proxyRecord._readOnlyProxyGUIDs:
                yield proxyRecord

    def verifyCredentials(self, credentials):
        if isinstance(credentials, UsernamePassword):
            # Check cached password
            try:
                if credentials.password == self.password:
                    return True
            except AttributeError:
                pass

            # Check with directory services
            try:
                if opendirectory.authenticateUserBasic(self.service.directory, self.nodeName, self.shortName, credentials.password):
                    # Cache the password to avoid future DS queries
                    self.password = credentials.password
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
