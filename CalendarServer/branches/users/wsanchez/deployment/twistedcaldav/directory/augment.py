##
# Copyright (c) 2009-2011 Apple Inc. All rights reserved.
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


from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twistedcaldav.database import AbstractADBAPIDatabase, ADBAPISqliteMixin,\
    ADBAPIPostgreSQLMixin
from twistedcaldav.directory.xmlaugmentsparser import XMLAugmentsParser
import copy
import time

from twistedcaldav.log import Logger

log = Logger()

class AugmentRecord(object):
    """
    Augmented directory record information
    """

    def __init__(
        self,
        uid,
        enabled=False,
        serverID="",
        partitionID="",
        enabledForCalendaring=False,
        autoSchedule=False,
    ):
        self.uid = uid
        self.enabled = enabled
        self.serverID = serverID
        self.partitionID = partitionID
        self.enabledForCalendaring = enabledForCalendaring
        self.autoSchedule = autoSchedule

recordTypesMap = {
    "users" : "User",
    "groups" : "Group",
    "locations" : "Location",
    "resources" : "Resource",
}

class AugmentDB(object):
    """
    Abstract base class for an augment record database.
    """
    
    def __init__(self):
        
        self.cachedRecords = {}
    
    @inlineCallbacks
    def getAugmentRecord(self, uid, recordType):
        """
        Get an AugmentRecord for the specified UID or the default.

        @param uid: directory uid to lookup
        @type uid: C{str}
        
        @return: L{Deferred}
        """
        recordType = recordTypesMap[recordType]

        result = (yield self._lookupAugmentRecord(uid))
        if result is not None:
            returnValue(result)
        
        # Try wildcard/default matches next
        for lookup in (
            "%s-%s*" % (recordType, uid[0:2]),
            "%s-%s*" % (recordType, uid[0]),
            "%s*" % (uid[0:2],),
            "%s*" % (uid[0],),
            "%s-Default" % (recordType,),
            "Default",
        ):
            result = (yield self._cachedAugmentRecord(lookup))
            if result is not None:
                result = copy.deepcopy(result)
                result.uid = uid
                returnValue(result)

        returnValue(None)

    @inlineCallbacks
    def getAllUIDs(self):
        """
        Get all AugmentRecord UIDs.

        @return: L{Deferred}
        """
        
        raise NotImplementedError("Child class must define this.")

    def _lookupAugmentRecord(self, uid):
        """
        Get an AugmentRecord for the specified uid.

        @param uid: directory uid to lookup
        @type uid: C{str}
        
        @return: L{Deferred}
        """
        
        raise NotImplementedError("Child class must define this.")

    @inlineCallbacks
    def _cachedAugmentRecord(self, uid):
        """
        Get an AugmentRecord for the specified uid from the cache.

        @param uid: directory uid to lookup
        @type uid: C{str}
        
        @return: L{Deferred}
        """
        
        if not uid in self.cachedRecords:
            result = (yield self._lookupAugmentRecord(uid))
            self.cachedRecords[uid] = result
        returnValue(self.cachedRecords[uid])

    def refresh(self):
        """
        Refresh any cached data.
        """
        
        self.cachedRecords.clear()
        
AugmentService = AugmentDB()   # Global augment service


class AugmentXMLDB(AugmentDB):
    """
    XMLFile based augment database implementation.
    """
    
    def __init__(self, xmlFiles, cacheTimeout=30):
        
        super(AugmentXMLDB, self).__init__()
        self.xmlFiles = xmlFiles
        self.cacheTimeout = cacheTimeout * 60 # Value is mins we want secs
        self.lastCached = 0
        self.db = {}
        
        try:
            self.db = self._parseXML()
        except RuntimeError:
            log.error("Failed to parse XML augments file - fatal error on startup")
            raise
            
        self.lastCached = time.time()

    @inlineCallbacks
    def getAllUIDs(self):
        """
        Get all AugmentRecord UIDs.

        @return: L{Deferred}
        """
        
        return succeed(self.db.keys())

    def _lookupAugmentRecord(self, uid):
        """
        Get an AugmentRecord for the specified uid.

        @param uid: directory uid to lookup
        @type uid: C{str}
        
        @return: L{Deferred}
        """
        
        # May need to re-cache
        if self.lastCached + self.cacheTimeout <= time.time():
            self.refresh()
            
        return succeed(self.db.get(uid))

    def refresh(self):
        """
        Refresh any cached data.
        """
        super(AugmentXMLDB, self).refresh()
        try:
            self.db = self._parseXML()
        except RuntimeError:
            log.error("Failed to parse XML augments file during cache refresh - ignoring")
        self.lastCached = time.time()

    def _parseXML(self):
        
        # Do each file
        results = {}
        for xmlFile in self.xmlFiles:
            
            # Creating a parser does the parse
            XMLAugmentsParser(xmlFile, results)
        
        return results

class AugmentADAPI(AugmentDB, AbstractADBAPIDatabase):
    """
    DBAPI based augment database implementation.
    """

    schema_version = "1"
    schema_type    = "AugmentDB"
    
    def __init__(self, dbID, dbapiName, dbapiArgs, **kwargs):
        
        AugmentDB.__init__(self)
        AbstractADBAPIDatabase.__init__(self, dbID, dbapiName, dbapiArgs, True, **kwargs)
        
    @inlineCallbacks
    def getAllUIDs(self):
        """
        Get all AugmentRecord UIDs.

        @return: L{Deferred}
        """
        
        # Query for the record information
        results = (yield self.queryList("select UID from AUGMENTS", ()))
        returnValue(results)

    @inlineCallbacks
    def _lookupAugmentRecord(self, uid):
        """
        Get an AugmentRecord for the specified UID.

        @param uid: directory UID to lookup
        @type uid: C{str}

        @return: L{Deferred}
        """
        
        # Query for the record information
        results = (yield self.query("select UID, ENABLED, SERVERID, PARTITIONID, CALENDARING, AUTOSCHEDULE from AUGMENTS where UID = :1", (uid,)))
        if not results:
            returnValue(None)
        else:
            uid, enabled, serverid, partitionid, enabdledForCalendaring, autoSchedule = results[0]
            
            record = AugmentRecord(
                uid = uid,
                enabled = enabled == "T",
                serverID = serverid,
                partitionID = partitionid,
                enabledForCalendaring = enabdledForCalendaring == "T",
                autoSchedule = autoSchedule == "T",
            )
            
            returnValue(record)

    @inlineCallbacks
    def addAugmentRecord(self, record, update=False):

        if update:
            yield self.execute(
                """update AUGMENTS set
                   (UID, ENABLED, SERVERID, PARTITIONID, CALENDARING, AUTOSCHEDULE) =
                   (:1, :2, :3, :4, :5 :6) where UID = :7""",
                (
                    record.uid,
                    "T" if record.enabled else "F",
                    record.serverID,
                    record.partitionID,
                    "T" if record.enabledForCalendaring else "F",
                    "T" if record.autoSchedule else "F",
                    record.uid,
                )
            )
        else:
            yield self.execute(
                """insert into AUGMENTS
                   (UID, ENABLED, SERVERID, PARTITIONID, CALENDARING, AUTOSCHEDULE)
                   values (:1, :2, :3, :4, :5, :6)""",
                (
                    record.uid,
                    "T" if record.enabled else "F",
                    record.serverID,
                    record.partitionID,
                    "T" if record.enabledForCalendaring else "F",
                    "T" if record.autoSchedule else "F",
                )
            )

    def removeAugmentRecord(self, uid):

        return self.query("delete from AUGMENTS where UID = :1", (uid,))

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
        Initialize the underlying database tables.
        """

        #
        # TESTTYPE table
        #
        yield self._create_table("AUGMENTS", (
            ("UID",          "text unique"),
            ("ENABLED",      "text(1)"),
            ("SERVERID",     "text"),
            ("PARTITIONID",  "text"),
            ("CALENDARING",  "text(1)"),
            ("AUTOSCHEDULE", "text(1)"),
        ))

    @inlineCallbacks
    def _db_empty_data_tables(self):
        yield self._db_execute("delete from AUGMENTS")

class AugmentSqliteDB(ADBAPISqliteMixin, AugmentADAPI):
    """
    Sqlite based augment database implementation.
    """

    def __init__(self, dbpath):
        
        ADBAPISqliteMixin.__init__(self)
        AugmentADAPI.__init__(self, "Augments", "sqlite3", (dbpath,))

class AugmentPostgreSQLDB(ADBAPIPostgreSQLMixin, AugmentADAPI):
    """
    PostgreSQL based augment database implementation.
    """

    def __init__(self, host, database, user=None, password=None):
        
        ADBAPIPostgreSQLMixin.__init__(self)
        AugmentADAPI.__init__(self, "Augments", "pgdb", (), host=host, database=database, user=user, password=password,)

