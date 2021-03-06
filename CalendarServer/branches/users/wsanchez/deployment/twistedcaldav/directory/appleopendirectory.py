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

import os
import sys
import signal

from xml.parsers.expat import ExpatError

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

from plistlib import readPlistFromString, readPlist

serverPreferences = '/Library/Preferences/com.apple.servermgr_info.plist'
saclGroup = 'com.apple.access_calendar'

class OpenDirectoryService(DirectoryService):
    """
    Open Directory implementation of L{IDirectoryService}.
    """
    baseGUID = "891F8321-ED02-424C-BA72-89C32F215C1E"

    def __repr__(self):
        return "<%s %r: %r>" % (self.__class__.__name__, self.realmName, self.node)

    def __init__(self, node="/Search", requireComputerRecord=True, dosetup=True, doreload=True, cacheTimeout=30, signalIntervalSeconds=10, **kwds):
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
            self.log_error("Open Directory (node=%s) Initialization error: %s" % (node, e))
            raise

        self.realmName = node
        self.directory = directory
        self.node = node
        self.requireComputerRecord = requireComputerRecord
        self.computerRecords = {}
        self.servicetags = set()
        self.cacheTimeout = cacheTimeout
        self.signalIntervalSeconds = signalIntervalSeconds
        self._records = {}
        self._delayedCalls = set()
        self._refreshing = False

        self.isWorkgroupServer = False

        if dosetup:
            if self.requireComputerRecord:
                try:
                    self._lookupVHostRecord()
                except Exception, e:
                    self.log_error("Unable to locate virtual host record: %s" % (e,))
                    raise

                if os.path.exists(serverPreferences):
                    serverInfo = readPlist(serverPreferences)

                    self.isWorkgroupServer = serverInfo.get('ServiceConfig', {}).get('IsWorkgroupServer', False)

                    if self.isWorkgroupServer:
                        self.log_info("Enabling Workgroup Server compatibility mode")

            if doreload:
                for recordType in self.recordTypes():
                    self.recordsForType(recordType)

    def refresh(self, loop=True, master=False):
        """
        This service works by having the master process call this method
        which queries OD for all records, storing the pickled results into
        files, then sending SIGUSR1 to the children to tell them to refresh.
        The files are only written by this method if there are actually
        changes in the results.
        The reloadCache( ) method below used to talk to OD, but now it reads
        these files.
        """

        if self._refreshing:
            self.log_warn("Already refreshing directory cache")
            return

        self._refreshing = True

        def _refresh(self):
            try:
                dataRoot = FilePath(config.DataRoot)
                cacheDir = dataRoot.child("DirectoryCache")
                if not cacheDir.exists():
                    cacheDir.createDirectory()

                dataWritten = False
                for recordType in self.recordTypes():
                    self.log_warn("Master fetching %s for directory cache" % (recordType,))
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
                        self.log_warn("Saving directory cache file for %s (%d items)" % (recordType, numNewResults))
                        cacheFile.setContent(pickled)
                        dataWritten = True
                    else:
                        self.log_warn("%s info hasn't changed" % (recordType,))

                if dataWritten and hasattr(self, 'processMonitor'):
                    self.processMonitor.signalAll(signal.SIGUSR1, "caldav", seconds=self.signalIntervalSeconds)
            finally:
                self._refreshing = False

        def _refreshInThread(self):
            return deferToThread(_refresh, self)

        if master:
            _refresh(self)

            if loop:
                LoopingCall(_refreshInThread, self).start(self.cacheTimeout * 60)
        else:
            def _reloadCaches():
                try:
                    self.log_warn("Reading directory cache files")
                    for recordType in self.recordTypes():
                        self.reloadCache(recordType)
                    self.log_warn("Done reading directory cache files")
                finally:
                    self._refreshing = False
            deferToThread(_reloadCaches)



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
            "dsAttrTypeNative:apple-serviceinfo",
        ]

        self.log_debug("opendirectory.queryRecordsWithAttribute_list(%r,%r,%r,%r,%r)" % (
            self.directory,
            dsquery.match(
                "dsAttrTypeNative:apple-serviceinfo",
                vhostname,
                dsattributes.eDSContains,
            ).generate(),
            True,    # case insentive for hostnames
            dsattributes.kDSStdRecordTypeComputers,
            attrs
        ))
        records = opendirectory.queryRecordsWithAttributes_list(
            self.directory,
            dsquery.match(
                "dsAttrTypeNative:apple-serviceinfo",
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
                % (self.realmName, vhostname)
            )

        # Now find all appropriate records and determine the enabled (only) service tags for each.
        for recordname, record in records:
            self._parseServiceInfo(vhostname, recordname, record)

        # Log all the matching records
        for key, value in self.computerRecords.iteritems():
            _ignore_recordname, enabled, servicetag = value
            self.log_info("Matched Directory record: %s with ServicesLocator: %s, state: %s" % (
                key,
                servicetag,
                {True:"enabled", False:"disabled"}[enabled]
            ))

        # Log all the enabled service tags - or generate an error if there are none
        if self.servicetags:
            for tag in self.servicetags:
                self.log_info("Enabled ServicesLocator: %s" % (tag,))
        else:
            raise OpenDirectoryInitError(
                "Open Directory (node=%s) no /Computers records with an enabled and valid "
                "calendar service were found matching virtual hostname: %s"
                % (self.realmName, vhostname)
            )

    def _parseServiceInfo(self, vhostname, recordname, record):

        # Extract some useful attributes
        recordguid = record[dsattributes.kDS1AttrGeneratedUID]
        recordlocation = "%s/Computers/%s" % (record[dsattributes.kDSNAttrMetaNodeLocation], recordname)

        # First check for apple-serviceinfo attribute
        plist = record.get("dsAttrTypeNative:apple-serviceinfo", None)
        if not plist:
            return False

        # Parse the plist and look for our special entry
        plist = readPlistFromString(plist)
        vhosts = plist.get("com.apple.macosxserver.virtualhosts", None)
        if not vhosts:
            self.log_error(
                "Open Directory (node=%s) %s record does not have a "
                "com.apple.macosxserver.virtualhosts in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation)
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
            self.log_error(
                "Open Directory (node=%s) %s record does not have "
                "any host name in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation)
            )
            return False
        if hostname != vhostname:
            # We can get false positives from the query - we ignore those.
            return False
        
        # Get host details. At this point we only check that it is present. We actually
        # ignore the details themselves (scheme/port) as we use our own config for that.
        hostdetails = vhosts[hostguid].get("hostDetails", None)
        if not hostdetails:
            self.log_error(
                "Open Directory (node=%s) %s record does not have "
                "any host details in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation)
            )
            return False
        
        # Look at the service data
        serviceInfos = vhosts[hostguid].get("serviceInfo", None)
        if not serviceInfos or not serviceInfos.has_key("calendar"):
            self.log_error(
                "Open Directory (node=%s) %s record does not have a "
                "calendar service in its apple-serviceinfo attribute value"
                % (self.realmName, recordlocation)
            )
            return False
        serviceInfo = serviceInfos["calendar"]
        
        # Check that this service is enabled
        enabled = serviceInfo.get("enabled", True)

        # Create the string we will use to match users with accounts on this server
        servicetag = "%s:%s:calendar" % (recordguid, hostguid)
        
        self.computerRecords[recordlocation] = (recordname, enabled, servicetag)
        
        if enabled:
            self.servicetags.add(servicetag)
        
        return True
    
    def _parseResourceInfo(self, plist, guid, recordType, shortname):
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
        except (ExpatError, AttributeError), e:
            self.log_error(
                "Failed to parse ResourceInfo attribute of record (%s)%s (guid=%s): %s\n%s" %
                (recordType, shortname, guid, e, plist,)
            )
            raise ValueError("Invalid ResourceInfo")

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
                if storage["status"] == "loading":
                    self.log_warn("Directory cache file for %s already being reloaded" % (recordType,))
                    return
                if not forceUpdate and (lastModified <= storage["last modified"]):
                    self.log_debug("Directory cache file for %s unchanged" % (recordType,))
                    storage["status"] = "new" # mark this as not stale
                    return
                storage["status"] = "loading"
            except KeyError:
                # Haven't read the file before
                pass

            self.log_debug("Reloading %s record cache" % (recordType,))

            pickled = cacheFile.getContent()
            results = pickle.loads(pickled)

        records = {}
        guids   = {}
        cuaddrs = {}

        disabledNames = set()
        disabledGUIDs = set()
        
        if recordType == DirectoryService.recordType_groups:
            groupsForGUID = {}
        elif recordType in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
            proxiesForGUID = {}
            readOnlyProxiesForGUID = {}

        def allowForACLs():
            return recordType in (
                DirectoryService.recordType_users,
                DirectoryService.recordType_groups,
            )

        def _setFromAttribute(attribute, lower=False):
            if attribute:
                if isinstance(attribute, str):
                    return set((attribute.lower() if lower else attribute,))
                else:
                    return set([item.lower() if lower else item for item in attribute])
            else:
                return ()

        def invalidRecord(recordShortName):
            self.log_error(
                "Directory (incorrectly) returned a record with no applicable "
                "ServicesLocator attribute: (%s) %s"
                % (recordType, recordShortName)
            )

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

        for (recordShortName, value) in results:
            enabledForCalendaring = True

            if self.requireComputerRecord:
                servicesLocators = value.get(dsattributes.kDSNAttrServicesLocator)

                if servicesLocators:
                    if type(servicesLocators) is str:
                        servicesLocators = (servicesLocators,)

                    for locator in servicesLocators:
                        if locator in self.servicetags:
                            break
                    else:
                        if allowForACLs():
                            enabledForCalendaring = False
                        else:
                            invalidRecord(recordShortName)
                            continue
                else:
                    if allowForACLs():
                        enabledForCalendaring = False
                    else:
                        invalidRecord(recordShortName)
                        continue

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

            # Special case for resources and locations
            autoSchedule = False
            proxyGUIDs = ()
            readOnlyProxyGUIDs = ()
            if recordType in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
                resourceInfo = value.get(dsattributes.kDSNAttrResourceInfo)
                if resourceInfo is not None:
                    try:
                        autoSchedule, proxy, read_only_proxy = self._parseResourceInfo(resourceInfo, recordGUID, recordType, recordShortName)
                    except ValueError:
                        continue
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
                emailAddresses        = recordEmailAddresses,
                memberGUIDs           = memberGUIDs,
                proxyGUIDs            = proxyGUIDs,
                readOnlyProxyGUIDs    = readOnlyProxyGUIDs,
            )

            # Look up augment information
            # TODO: this needs to be deferred but for now we hard code the deferred result because
            # we know it is completing immediately.
            d = augment.AugmentService.getAugmentRecord(record.guid, recordType)
            d.addCallback(lambda x:record.addAugmentInformation(x))
            
            # Override based on ServicesLocator and ResourceInfo
            if not enabledForCalendaring:
                record.enabledForCalendaring = False
            if autoSchedule:
                record.autoSchedule = True

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

                    # Do proxy indexing if needed
                    elif recordType in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
                        self._indexGroup(record, record._proxyGUIDs, proxiesForGUID)
                        self._indexGroup(record, record._readOnlyProxyGUIDs, readOnlyProxiesForGUID)

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

        # Add proxy indexing if needed
        elif recordType in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
            storage["proxiesForGUID"] = proxiesForGUID
            storage["readOnlyProxiesForGUID"] = readOnlyProxiesForGUID

        self._records[recordType] = storage

        self.log_info(
            "Added %d records to %s OD record cache"
            % (len(self._records[recordType]["guids"]), recordType)
        )

    def _queryDirectory(self, recordType):
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
            raise UnknownRecordTypeError("Unknown Open Directory record type: %s" % (recordType))

        if self.requireComputerRecord:
            if self.isWorkgroupServer and recordType == DirectoryService.recordType_users:
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
                    members      = results[0][1].get(dsattributes.kDSNAttrGroupMembers, [])
                    nestedGroups = results[0][1].get(dsattributes.kDSNAttrNestedGroups, [])
                else:
                    members = []
                    nestedGroups = []

                guidQueries = []

                for GUID in self._expandGroupMembership(members, nestedGroups):
                    guidQueries.append(
                        dsquery.match(dsattributes.kDS1AttrGeneratedUID, GUID, dsattributes.eDSExact)
                    )

                if not guidQueries:
                    self.log_warn("No SACL enabled users found.")
                    return ()

                query = dsquery.expression(dsquery.expression.OR, guidQueries)

            #
            # For users and groups, we'll load all entries, even if
            # they don't have a services locator for this server.
            #
            elif (
                recordType != DirectoryService.recordType_users and
                recordType != DirectoryService.recordType_groups
            ):
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

        subquery = None

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
        emailAddresses, memberGUIDs, proxyGUIDs, readOnlyProxyGUIDs,
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
        return self.service.groupsForGUID(self.guid)

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
        result = set()
        result.update(self.service.proxiesForGUID(DirectoryService.recordType_resources, self.guid))
        result.update(self.service.proxiesForGUID(DirectoryService.recordType_locations, self.guid))
        return result

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
        result = set()
        result.update(self.service.readOnlyProxiesForGUID(DirectoryService.recordType_resources, self.guid))
        result.update(self.service.readOnlyProxiesForGUID(DirectoryService.recordType_locations, self.guid))
        return result


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
