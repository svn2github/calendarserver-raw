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
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
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
import os

import opendirectory
import dsattributes
import dsquery

from twisted.internet.reactor import callLater
from twisted.internet.threads import deferToThread
from twisted.cred.credentials import UsernamePassword
from twisted.web2.auth.digest import DigestedCredentials

from twistedcaldav import logging
from twistedcaldav.config import config
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.directory import DirectoryError, UnknownRecordTypeError

from plistlib import readPlistFromString, readPlist

_serverPreferences = '/Library/Preferences/com.apple.servermgr_info.plist'
_saclGroup = 'com.apple.access_calendar'

recordListCacheTimeout = 60 * 30 # 30 minutes

class OpenDirectoryService(DirectoryService):
    """
    Open Directory implementation of L{IDirectoryService}.
    """
    baseGUID = "891F8321-ED02-424C-BA72-89C32F215C1E"

    def __repr__(self):
        return "<%s %r: %r>" % (self.__class__.__name__, self.realmName, self.node)

    def __init__(self, node="/Search", requireComputerRecord=True, dosetup=True):
        """
        @param node: an OpenDirectory node name to bind to.
        @param requireComputerRecord: C{True} if the directory schema is to be used to determine
            which calendar users are enabled.
        @param dosetup: if C{True} then the directory records are initialized,
                        if C{False} they are not.
                        This should only be set to C{False} when doing unit tests.
        """
        try:
            directory = opendirectory.odInit(node)
        except opendirectory.ODError, e:
            logging.err("Open Directory (node=%s) Initialization error: %s" % (node, e), system="OpenDirectoryService")
            raise

        self.realmName = node
        self.directory = directory
        self.node = node
        self.requireComputerRecord = requireComputerRecord
        self.computerRecords = {}
        self.servicetags = set()
        self._records = {}
        self._delayedCalls = set()

        self.isWorkgroupServer = False

        if dosetup:
            if self.requireComputerRecord:
                try:
                    self._lookupVHostRecord()
                except Exception, e:
                    logging.err("Unable to locate virtual host record: %s" % (e,), system="OpenDirectoryService")
                    raise

                if os.path.exists(_serverPreferences):
                    serverInfo = readPlist(_serverPreferences)

                    self.isWorkgroupServer = serverInfo.get(
                        'ServiceConfig', {}).get(
                        'IsWorkgroupServer', False)

                    if self.isWorkgroupServer:
                        logging.info("Enabling Workgroup Server compatibility mode", system="OpenDirectoryService")

            for recordType in self.recordTypes():
                self.recordsForType(recordType)

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

            result = opendirectory.queryRecordsWithAttribute_list(
                self.directory,
                dsattributes.kDS1AttrGeneratedUID,
                groupGUID,
                dsattributes.eDSExact,
                False,
                dsattributes.kDSStdRecordTypeGroups,
                [dsattributes.kDSNAttrGroupMembers,
                 dsattributes.kDSNAttrNestedGroups])

            if not result:
                logging.err("Couldn't find group %s when trying to expand nested groups." % (groupGUID,), system="OpenDirectoryService")
                continue

            group = result[0][1]

            processedGUIDs.add(groupGUID)

            for GUID in self._expandGroupMembership(
                group.get(dsattributes.kDSNAttrGroupMembers, []),
                group.get(dsattributes.kDSNAttrNestedGroups, []),
                processedGUIDs):
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

    def _lookupVHostRecord(self):
        """
        Get the OD service record for this host.
        """

        # The server must have been configured with a virtual hostname.
        vhostname = config.ServerHostName
        if not vhostname:
            raise OpenDirectoryInitError(
                "There is no virtual hostname configured for the server for use with Open Directory (node=%s)"
                % (self.realmName,)
            )
         
        # Find a record in /Computers with an apple-serviceinfo attribute value equal to the virtual hostname
        # and return some useful attributes.
        attrs = [
            dsattributes.kDS1AttrGeneratedUID,
            dsattributes.kDSNAttrRecordName,
            dsattributes.kDSNAttrMetaNodeLocation,
            'dsAttrTypeNative:apple-serviceinfo',
        ]

        records = opendirectory.queryRecordsWithAttributes_list(
            self.directory,
            dsquery.match(
                'dsAttrTypeNative:apple-serviceinfo',
                vhostname,
                dsattributes.eDSContains,
            ).generate(),
            True,    # case insentive for hostnames
            dsattributes.kDSStdRecordTypeComputers,
            attrs
        )
        self._parseComputersRecords(records, vhostname)

    def _parseComputersRecords(self, records, vhostname):
        # Must have some results
        if len(records) == 0:
            raise OpenDirectoryInitError(
                "Open Directory (node=%s) has no /Computers records with a virtual hostname: %s"
                % (self.realmName, vhostname,)
            )

        # Now find all appropriate records and determine the enabled (only) service tags for each.
        for recordname, record in records:
            self._parseServiceInfo(vhostname, recordname, record)

        # Log all the matching records
        for key, value in self.computerRecords.iteritems():
            _ignore_recordname, enabled, servicetag = value
            logging.info("Matched Directory record: %s with ServicesLocator: %s, state: %s" % (
                key,
                servicetag,
                {True:"enabled", False:"disabled"}[enabled]
            ), system="OpenDirectoryService")

        # Log all the enabled service tags - or generate an error if there are none
        if self.servicetags:
            for tag in self.servicetags:
                logging.info("Enabled ServicesLocator: %s" % (tag,), system="OpenDirectoryService")
        else:
            raise OpenDirectoryInitError(
                "Open Directory (node=%s) no /Computers records with an enabled and valid "
                "calendar service were found matching virtual hostname: %s"
                % (self.realmName, vhostname,)
            )

    def _parseServiceInfo(self, vhostname, recordname, record):

        # Extract some useful attributes
        recordguid = record[dsattributes.kDS1AttrGeneratedUID]
        recordlocation = "%s/Computers/%s" % (record[dsattributes.kDSNAttrMetaNodeLocation], recordname,)

        # First check for apple-serviceinfo attribute
        plist = record.get('dsAttrTypeNative:apple-serviceinfo', None)
        if not plist:
            return False

        # Parse the plist and look for our special entry
        plist = readPlistFromString(plist)
        vhosts = plist.get("com.apple.macosxserver.virtualhosts", None)
        if not vhosts:
            logging.err(
                "Open Directory (node=%s) %s record does not have a "
                "com.apple.macosxserver.virtualhosts in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation,), system="OpenDirectoryService"
            )
            return False
        
        # Iterate over each vhost and find one that is a calendar service
        hostguid = None
        for key, value in vhosts.iteritems():
            serviceTypes = value.get("serviceType", None)
            if serviceTypes:
                for type in serviceTypes:
                    if type == "calendar":
                        hostguid = key
                        break
                    
        if not hostguid:
            # We can get false positives from the query - we ignore those.
            return False
            
        # Get host name
        hostname = vhosts[hostguid].get("hostname", None)
        if not hostname:
            logging.err(
                "Open Directory (node=%s) %s record does not have "
                "any host name in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation, ), system="OpenDirectoryService"
            )
            return False
        if hostname != vhostname:
            # We can get false positives from the query - we ignore those.
            return False
        
        # Get host details. At this point we only check that it is present. We actually
        # ignore the details themselves (scheme/port) as we use our own config for that.
        hostdetails = vhosts[hostguid].get("hostDetails", None)
        if not hostdetails:
            logging.err(
                "Open Directory (node=%s) %s record does not have "
                "any host details in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation, ), system="OpenDirectoryService"
            )
            return False
        
        # Look at the service data
        serviceInfos = vhosts[hostguid].get("serviceInfo", None)
        if not serviceInfos or not serviceInfos.has_key("calendar"):
            logging.err(
                "Open Directory (node=%s) %s record does not have a "
                "calendar service in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation), system="OpenDirectoryService"
            )
            return False
        serviceInfo = serviceInfos["calendar"]
        
        # Check that this service is enabled
        enabled = serviceInfo.get("enabled", True)

        # Create the string we will use to match users with accounts on this server
        servicetag = "%s:%s:calendar" % (recordguid, hostguid)
        
        self.computerRecords[recordlocation] = (recordname, enabled, servicetag,)
        
        if enabled:
            self.servicetags.add(servicetag)
        
        return True
    
    def _getCalendarUserAddresses(self, recordType, recordName, record):
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

    def _parseResourceInfo(self, plist):
        """
        Parse OD ResourceInfo attribute and extract information that the server needs.

        @param plist: the plist that is the attribute value.
        @type plist: str
        @return: a C{tuple} of C{bool} for auto-accept and C{str} for proxy GUID.
        """
        plist = readPlistFromString(plist)
        wpframework = plist.get("com.apple.WhitePagesFramework", {})
        autoaccept = wpframework.get("AutoAcceptsInvitation", False)
        proxy= wpframework.get("CalendaringDelegate")
        
        return (autoaccept, proxy,)

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
                    logging.err("Unable to load records of type %s from OpenDirectory due to unexpected error: %s"
                            % (recordType, f), system="OpenDirectoryService")

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
            # Cache miss; try looking the record up, in case it is new
            # FIXME: This is a blocking call (hopefully it's a fast one)
            self.reloadCache(recordType, shortName)
            return self.recordsForType(recordType).get(shortName, None)

    def recordWithGUID(self, guid):
        # Override super's implementation with something faster.
        for recordType in self.recordTypes():
            record = self._storage(recordType)["guids"].get(guid, None)
            if record:
                return record
        else:
            return None

    def reloadCache(self, recordType, shortName=None):
        
        if shortName:
            logging.info("Trying to add record %s to %s record cache" % (shortName, recordType,), system="OpenDirectoryService")
        else:
            logging.info("Reloading %s record cache" % (recordType,), system="OpenDirectoryService")

        results = self._queryDirectory(recordType, shortName)
        
        records  = {}
        guids    = {}
        disabled_names = set()
        disabled_guids = set()

        for (key, value) in results:
            enabledForCalendaring = True

            if self.requireComputerRecord:
                if not value.get(dsattributes.kDSNAttrServicesLocator):
                    if recordType == DirectoryService.recordType_groups:
                        enabledForCalendaring = False
                        logging.debug("Group %s is not enabled for calendaring but may be used in ACLs" % (key,), system="OpenDirectoryService")
                    else:
                        logging.err("Directory (incorrectly) returned a record with no ServicesLocator attribute: %s" % (key,), system="OpenDirectoryService")
                        continue

            # Now get useful record info.
            recordShortName = key
            guid = value.get(dsattributes.kDS1AttrGeneratedUID)
            if not guid:
                continue
            realName = value.get(dsattributes.kDS1AttrDistinguishedName)
            nodename = value.get(dsattributes.kDSNAttrMetaNodeLocation)

            # Get calendar user addresses from directory record.
            if enabledForCalendaring:
                calendarUserAddresses = self._getCalendarUserAddresses(recordType, key, value)
            else:
                calendarUserAddresses = ()

            # Special case for groups.
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
            if recordType in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
                resourceInfo = value.get(dsattributes.kDSNAttrResourceInfo)
                if resourceInfo is not None:
                    autoSchedule, proxy = self._parseResourceInfo(resourceInfo)
                    if proxy:
                        proxyGUIDs = (proxy,)

            record = OpenDirectoryRecord(
                service               = self,
                recordType            = recordType,
                guid                  = guid,
                nodename              = nodename,
                shortName             = recordShortName,
                fullName              = realName,
                calendarUserAddresses = calendarUserAddresses,
                autoSchedule          = autoSchedule,
                enabledForCalendaring = enabledForCalendaring,
                memberGUIDs           = memberGUIDs,
                proxyGUIDs            = proxyGUIDs,
            )
            
            # Check for disabled items
            if recordShortName in disabled_names or guid in disabled_guids:
                disabled_names.add(recordShortName)
                disabled_guids.add(guid)
                logging.warn("Record disabled: %s, with GUID: %s" % (recordShortName, guid,), system="OpenDirectoryService")
            else:
                # Check for duplicate items and disable all names/guids for mismatched duplicates.
                ok_to_add = True
                if recordShortName in records:
                    existing_record = records[recordShortName]
                    if existing_record.guid != guid:
                        disabled_names.add(recordShortName)
                        disabled_guids.add(guid)
                        disabled_guids.add(existing_record.guid)
                        del records[existing_record.shortName]
                        del guids[existing_record.guid]
                        logging.warn("Record disabled: %s, with GUID: %s" % (existing_record.shortName, existing_record.guid,), system="OpenDirectoryService")
                        logging.warn("Record disabled: %s, with GUID: %s" % (recordShortName, guid,), system="OpenDirectoryService")
                        ok_to_add = False
                elif guid in guids:
                    existing_record = guids[guid]
                    if existing_record.shortName != recordShortName:
                        disabled_names.add(recordShortName)
                        disabled_guids.add(guid)
                        disabled_names.add(existing_record.shortName)
                        del records[existing_record.shortName]
                        del guids[existing_record.guid]
                        logging.warn("Record disabled: %s, with GUID: %s" % (existing_record.shortName, existing_record.guid,), system="OpenDirectoryService")
                        logging.warn("Record disabled: %s, with GUID: %s" % (recordShortName, guid,), system="OpenDirectoryService")
                        ok_to_add = False
                
                if ok_to_add:
                    records[recordShortName] = guids[guid] = record
                    logging.debug("Populated record: %s, with GUID: %s" % (records[recordShortName], guid,), system="OpenDirectoryService")

        if shortName is None:
            #
            # Replace the entire cache
            #
            storage = {
                "status"        : "new",
                "records"       : records,
                "guids"         : guids,
                "disabled_names": disabled_names,
                "disabled_guids": disabled_guids,
            }

            def rot():
                storage["status"] = "stale"
                removals = set()
                for call in self._delayedCalls:
                    if not call.active():
                        removals.add(call)
                for item in removals:
                    self._delayedCalls.remove(item)

            self._delayedCalls.add(callLater(recordListCacheTimeout, rot))

            self._records[recordType] = storage

        elif records:
            #
            # Update one record, if found
            #
            assert len(records) == 1, "shortName = %r, records = %r" % (shortName, len(records))
            
            # Need to check that this record has not been disabled
            storage = self._records[recordType]
            if shortName in storage["disabled_names"] or record.guid in storage["disabled_guids"]:
                storage["disabled_guids"].add(record.guid)
                storage["disabled_names"].add(shortName)
                logging.warn("Record disabled: %s, with GUID: %s" % (shortName, record.guid,), system="OpenDirectoryService")
                records = {}
            elif record.guid in storage["guids"]:
                # Got a duplicate GUID - for now we disable the record  being looked up. On the next cache refresh
                # the existing record will also get disabled.
                storage["disabled_guids"].add(record.guid)
                storage["disabled_names"].add(shortName)
                logging.warn("Record disabled: %s, with GUID: %s" % (shortName, record.guid,), system="OpenDirectoryService")
                records = {}
            else:
                storage["records"][shortName] = record
                storage["guids"][record.guid] = record

        if shortName:
            logging.info("Added %d records for %s in %s record cache" % (len(records), shortName, recordType,), system="OpenDirectoryService")
        else:
            logging.info("Found %d records for %s record cache" % (len(self._records[recordType]["guids"]), recordType,), system="OpenDirectoryService")

    def _queryDirectory(self, recordType, shortName=None):

        attrs = [
            dsattributes.kDS1AttrGeneratedUID,
            dsattributes.kDS1AttrDistinguishedName,
            dsattributes.kDSNAttrEMailAddress,
            dsattributes.kDSNAttrServicesLocator,
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
            raise UnknownRecordTypeError("Unknown Open Directory record type: %s"
                                         % (recordType,))

        if self.requireComputerRecord:
            if self.isWorkgroupServer and recordType == DirectoryService.recordType_users:
                if shortName is None:
                    results = opendirectory.queryRecordsWithAttribute_list(
                        self.directory,
                        dsattributes.kDSNAttrRecordName,
                        _saclGroup,
                        dsattributes.eDSExact,
                        False,
                        dsattributes.kDSStdRecordTypeGroups,
                        [dsattributes.kDSNAttrGroupMembers,
                         dsattributes.kDSNAttrNestedGroups])

                    if len(results) == 1:
                        members = results[0][1].get(
                            dsattributes.kDSNAttrGroupMembers, [])
    
                        nestedGroups = results[0][1].get(
                            dsattributes.kDSNAttrNestedGroups, [])
                    else:
                        members = []
                        nestedGroups = []

                    guidQueries = []

                    for GUID in self._expandGroupMembership(members,
                                                            nestedGroups):
                        guidQueries.append(
                            dsquery.match(dsattributes.kDS1AttrGeneratedUID,
                                          GUID, dsattributes.eDSExact))

                    if not guidQueries:
                        logging.warn("No SACL enabled users found.", system="OpenDirectoryService")
                        return ()

                    query = dsquery.expression(dsquery.expression.OR,
                                               guidQueries)

            #
            # For groups, we'll load all entries, even if they don't
            # have a services locator for this server.
            #
            elif recordType != DirectoryService.recordType_groups:
                tag_queries = []
                for tag in self.servicetags:
                    tag_queries.append(dsquery.match(dsattributes.kDSNAttrServicesLocator, tag, dsattributes.eDSExact))
                if len(tag_queries) == 1:
                    subquery = tag_queries[0]
                else:
                    subquery = dsquery.expression(dsquery.expression.OR, tag_queries)
                if query is None:
                    query = subquery
                else:
                    query = dsquery.expression(dsquery.expression.AND, (subquery, query))

        if shortName is not None:
            subquery = dsquery.match(dsattributes.kDSNAttrRecordName, shortName, dsattributes.eDSExact)
            if query is None:
                query = subquery
            else:
                query = dsquery.expression(dsquery.expression.AND, (subquery, query))


        try:
            if query:
                if isinstance(query, dsquery.match):
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
                    results = opendirectory.queryRecordsWithAttributes_list(
                        self.directory,
                        query.generate(),
                        False,
                        listRecordType,
                        attrs,
                    )
            else:
                results = opendirectory.listAllRecordsWithAttributes_list(
                    self.directory,
                    listRecordType,
                    attrs,
                )
        except opendirectory.ODError, ex:
            logging.err("Open Directory (node=%s) error: %s" % (self.realmName, str(ex)), system="OpenDirectoryService")
            raise

        return results

class OpenDirectoryRecord(DirectoryRecord):
    """
    Open Directory implementation of L{IDirectoryRecord}.
    """
    def __init__(
        self, service, recordType, guid, nodename, shortName, fullName,
        calendarUserAddresses, autoSchedule, enabledForCalendaring,
        memberGUIDs, proxyGUIDs,
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
        self._nodename = nodename
        self._memberGUIDs = tuple(memberGUIDs)
        self._proxyGUIDs = tuple(proxyGUIDs)

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
                logging.err("No record for proxy in %s with GUID %s" % (self.shortName, guid), system="OpenDirectoryService")
            else:
                yield proxyRecord

    def proxyFor(self):
        for proxyRecord in itertools.chain(
                                  self.service.recordsForType(DirectoryService.recordType_resources).itervalues(),
                                  self.service.recordsForType(DirectoryService.recordType_locations).itervalues()
                              ):
            if self.guid in proxyRecord._proxyGUIDs:
                yield proxyRecord

    def verifyCredentials(self, credentials):
        if isinstance(credentials, UsernamePassword):
            try:
                return opendirectory.authenticateUserBasic(self.service.directory, self._nodename, self.shortName, credentials.password)
            except opendirectory.ODError, e:
                logging.err("Open Directory (node=%s) error while performing basic authentication for user %s: %s"
                        % (self.service.realmName, self.shortName, e), system="OpenDirectoryService")
                return False
        elif isinstance(credentials, DigestedCredentials):
            try:
                # We need a special format for the "challenge" and "response" strings passed into open directory, as it is
                # picky about exactly what it receives.
                
                try:
                    challenge = 'Digest realm="%(realm)s", nonce="%(nonce)s", algorithm=%(algorithm)s' % credentials.fields
                    response = ('Digest username="%(username)s", '
                                'realm="%(realm)s", '
                                'nonce="%(nonce)s", '
                                'uri="%(uri)s", '
                                'response="%(response)s",'
                                'algorithm=%(algorithm)s') % credentials.fields
                except KeyError, e:
                    logging.err("Open Directory (node=%s) error while performing digest authentication for user %s: missing digest response field: %s in: %s"
                            % (self.service.realmName, self.shortName, e, credentials.fields), system="OpenDirectoryService")
                    return False

                return opendirectory.authenticateUserDigest(
                    self.service.directory,
                    self._nodename,
                    self.shortName,
                    challenge,
                    response,
                    credentials.method
                )
            except opendirectory.ODError, e:
                logging.err("Open Directory (node=%s) error while performing digest authentication for user %s: %s"
                        % (self.service.realmName, self.shortName, e), system="OpenDirectoryService")
                return False

        return super(OpenDirectoryRecord, self).verifyCredentials(credentials)

class OpenDirectoryInitError(DirectoryError):
    """
    OpenDirectory initialization error.
    """
