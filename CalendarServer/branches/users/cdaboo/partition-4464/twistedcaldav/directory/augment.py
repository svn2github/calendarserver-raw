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

from twistedcaldav.database import AbstractADBAPIDatabase
from twistedcaldav.directory.xmlaccountsparser import XMLAccountsParser,\
    RECORD_TYPES

from twisted.internet.defer import inlineCallbacks, returnValue, succeed

class AugmentRecord(object):
    """
    Augmented directory record information
    """

    def __init__(
        self,
        guid,
        enabled,
        hostedAt,
        enabledForCalendaring,
        autoSchedule,
        calendarUserAddresses,  
    ):
        self.guid = guid
        self.enabled = enabled
        self.hostedAt = hostedAt
        self.enabledForCalendaring = enabledForCalendaring
        self.autoSchedule = autoSchedule
        self.calendarUserAddresses = calendarUserAddresses

class AugmentDB(object):
    """
    Abstract base class for an augment record database.
    """
    
    def __init__(self):
        pass
    
    def getAugmentRecord(self, guid):
        """
        Get an AugmentRecord for the specified GUID.

        @param guid: directory GUID to lookup
        @type guid: C{str}
        
        @return: L{Deferred}
        """
        
        raise NotImplementedError("Child class must define this.")

class AugmentXMLDB(AugmentDB):
    """
    XMLFile based augment database implementation.
    """
    
    def __init__(self, xmlfilepath):
        
        self.xmlfilepath = xmlfilepath
        self.db = {}
        
        self._parseXML()

    def getAugmentRecord(self, guid):
        """
        Get an AugmentRecord for the specified GUID.

        @param guid: directory GUID to lookup
        @type guid: C{str}
        
        @return: L{Deferred}
        """
        
        return succeed(self.db.get(guid))

    def _parseXML(self):
        
        # We will re-use the XML account format and associated classes
        
        # Create a parser and do the parse
        parser = XMLAccountsParser(self.xmlfilepath, externalUpdate=False)
        
        # Add all records to our DB
        for recordType in RECORD_TYPES.values():
            for record in parser.items[recordType].itervalues():
                
                # Create augment record from XML account record
                augment = AugmentRecord(
                    guid=record.guid,
                    enabled=record.enabled,
                    hostedAt=record.hostedAt,
                    enabledForCalendaring=record.enabledForCalendaring,
                    autoSchedule=record.autoSchedule,
                    calendarUserAddresses=record.calendarUserAddresses,
                )
                
                self.db[augment.guid] = augment


class AugmentADAPI(AugmentDB, AbstractADBAPIDatabase):
    """
    DBAPI based augment database implementation.
    """

    schema_version = "1"
    schema_type    = "AugmentDB"
    
    def __init__(self, dbID, dbapiName, dbapiArgs, **kwargs):
        
        self.cachedPartitions = {}
        self.cachedHostedAt = {}
        
        AbstractADBAPIDatabase.__init__(self, dbID, dbapiName, dbapiArgs, True, **kwargs)
        
    @inlineCallbacks
    def getAugmentRecord(self, guid):
        """
        Get an AugmentRecord for the specified GUID.

        @param guid: directory GUID to lookup
        @type guid: C{str}

        @return: L{Deferred}
        """
        
        # Query for the record information
        results = (yield self.query("select GUID, ENABLED, PARTITIONID, CALENDARING, AUTOSCHEDULE, CUADDRS from AUGMENTS where GUID = :1", (guid,)))
        if not results:
            returnValue(None)
        else:
            guid, enabled, partitionid, enabdledForCalendaring, autoSchedule, cuaddrs = results[0]
            
            record = AugmentRecord(
                guid = guid,
                enabled = enabled == "T",
                hostedAt = (yield self._getPartition(partitionid)),
                enabledForCalendaring = enabdledForCalendaring == "T",
                autoSchedule = autoSchedule == "T",
                calendarUserAddresses = set(cuaddrs.split("\t")) if cuaddrs else set(),
            )
            
            returnValue(record)

    @inlineCallbacks
    def addAugmentRecord(self, record):

        partitionid = (yield self._getPartitionID(record.hostedAt))
        cuaddrs = "\t".join(record.calendarUserAddresses)
        
        yield self.execute(
            """insert into AUGMENTS
            (GUID, ENABLED, PARTITIONID, CALENDARING, AUTOSCHEDULE, CUADDRS)
            values (:1, :2, :3, :4, :5, :6)""",
            (
                record.guid,
                "T" if record.enabled else "F",
                partitionid,
                "T" if record.enabledForCalendaring else "F",
                "T" if record.autoSchedule else "F",
                cuaddrs,)
        )

    @inlineCallbacks
    def _getPartitionID(self, hostedat, createIfMissing=True):
        
        # We will use a cache for these as we do not expect changes whilst running
        try:
            returnValue(self.cachedHostedAt[hostedat])
        except KeyError:
            pass

        partitionid = (yield self.queryOne("select PARTITIONID from PARTITIONS where HOSTEDAT = :1", (hostedat,)))
        if partitionid == None:
            yield self.execute("insert into PARTITIONS (HOSTEDAT) values (:1)", (hostedat,))
            partitionid = (yield self.queryOne("select PARTITIONID from PARTITIONS where HOSTEDAT = :1", (hostedat,)))
        self.cachedHostedAt[hostedat] = partitionid
        returnValue(partitionid)

    @inlineCallbacks
    def _getPartition(self, partitionid):
        
        # We will use a cache for these as we do not expect changes whilst running
        try:
            returnValue(self.cachedPartitions[partitionid])
        except KeyError:
            pass

        partition = (yield self.queryOne("select HOSTEDAT from PARTITIONS where PARTITIONID = :1", (partitionid,)))
        self.cachedPartitions[partitionid] = partition
        returnValue(partition)

    @inlineCallbacks
    def _getCUAddrs(self, augmentid):
        
        return self.queryList("select CUADDR from CUADDRS where AUGMENTID = :1", (augmentid,))

    def _db_version(self):
        """
        @return: the schema version assigned to this index.
        """
        return AugmentADAPI.schema_version
        
    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return AugmentADAPI.schema_type
    
    @inlineCallbacks
    def _db_init_data_tables(self):
        """
        Initialise the underlying database tables.
        """

        #
        # TESTTYPE table
        #
        yield self._db_execute(
            """
            create table AUGMENTS (
                GUID         text unique,
                ENABLED      text(1),
                PARTITIONID  text,
                CALENDARING  text(1),
                AUTOSCHEDULE text(1),
                CUADDRS      text
            )
            """
        )
        yield self._db_execute(
            """
            create table PARTITIONS (
                PARTITIONID  integer primary key autoincrement,
                HOSTEDAT     text
            )
            """
        )

    @inlineCallbacks
    def _db_remove_data_tables(self):
        yield self._db_execute("drop table if exists AUGMENTS")
        yield self._db_execute("drop table if exists PARTITIONS")

class AugmentSqliteDB(AugmentADAPI):
    """
    Sqlite based augment database implementation.
    """

    def __init__(self, dbpath):
        
        super(AugmentSqliteDB, self).__init__("Augments", "sqlite3", (dbpath,))
