##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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
CardDAV Index.

This API is considered private to static.py and is therefore subject to
change.
"""

__all__ = [
    "AddressBookIndex",
]

import datetime
import os
import time
import hashlib

try:
    import sqlite3 as sqlite
except ImportError:
    from pysqlite2 import dbapi2 as sqlite

from twisted.internet.defer import maybeDeferred

from twistedcaldav import carddavxml
from twistedcaldav.query import addressbookquery
from twistedcaldav.sql import AbstractSQLDatabase
from twistedcaldav.sql import db_prefix
from twistedcaldav.vcard import Component

from twext.python.log import Logger, LoggingMixIn
from twistedcaldav.config import config
from twistedcaldav.memcachepool import CachePoolUserMixIn

log = Logger()

db_basename = db_prefix + "sqlite"
schema_version = "1"

class ReservationError(LookupError):
    """
    Attempt to reserve a UID which is already reserved or to unreverse a UID
    which is not reserved.
    """

def wrapInDeferred(f):
    def _(*args, **kwargs):
        return maybeDeferred(f, *args, **kwargs)

    return _


class MemcachedUIDReserver(CachePoolUserMixIn, LoggingMixIn):
    def __init__(self, index, cachePool=None):
        self.index = index
        self._cachePool = cachePool

    def _key(self, uid):
        return 'reservation:%s' % (
            hashlib.md5('%s:%s' % (uid,
                                   self.index.resource.fp.path)).hexdigest())

    def reserveUID(self, uid):
        uid = uid.encode('utf-8')
        self.log_debug("Reserving UID %r @ %r" % (
                uid,
                self.index.resource.fp.path))

        def _handleFalse(result):
            if result is False:
                raise ReservationError(
                    "UID %s already reserved for address book collection %s."
                    % (uid, self.index.resource)
                    )

        d = self.getCachePool().add(self._key(uid),
                                    'reserved',
                                    expireTime=config.UIDReservationTimeOut)
        d.addCallback(_handleFalse)
        return d


    def unreserveUID(self, uid):
        uid = uid.encode('utf-8')
        self.log_debug("Unreserving UID %r @ %r" % (
                uid,
                self.index.resource.fp.path))

        def _handleFalse(result):
            if result is False:
                raise ReservationError(
                    "UID %s is not reserved for address book collection %s."
                    % (uid, self.index.resource)
                    )

        d =self.getCachePool().delete(self._key(uid))
        d.addCallback(_handleFalse)
        return d


    def isReservedUID(self, uid):
        uid = uid.encode('utf-8')
        self.log_debug("Is reserved UID %r @ %r" % (
                uid,
                self.index.resource.fp.path))

        def _checkValue((flags, value)):
            if value is None:
                return False
            else:
                return True

        d = self.getCachePool().get(self._key(uid))
        d.addCallback(_checkValue)
        return d



class SQLUIDReserver(object):
    def __init__(self, index):
        self.index = index

    @wrapInDeferred
    def reserveUID(self, uid):
        """
        Reserve a UID for this index's resource.
        @param uid: the UID to reserve
        @raise ReservationError: if C{uid} is already reserved
        """

        try:
            self.index._db_execute("insert into RESERVED (UID, TIME) values (:1, :2)", uid, datetime.datetime.now())
            self.index._db_commit()
        except sqlite.IntegrityError:
            self.index._db_rollback()
            raise ReservationError(
                "UID %s already reserved for address book collection %s."
                % (uid, self.index.resource)
            )
        except sqlite.Error, e:
            log.err("Unable to reserve UID: %s", (e,))
            self.index._db_rollback()
            raise

    def unreserveUID(self, uid):
        """
        Unreserve a UID for this index's resource.
        @param uid: the UID to reserve
        @raise ReservationError: if C{uid} is not reserved
        """

        def _cb(result):
            if result == False:
                raise ReservationError(
                    "UID %s is not reserved for address book collection %s."
                    % (uid, self.index.resource)
                    )
            else:
                try:
                    self.index._db_execute(
                        "delete from RESERVED where UID = :1", uid)
                    self.index._db_commit()
                except sqlite.Error, e:
                    log.err("Unable to unreserve UID: %s", (e,))
                    self.index._db_rollback()
                    raise

        d = self.isReservedUID(uid)
        d.addCallback(_cb)
        return d


    @wrapInDeferred
    def isReservedUID(self, uid):
        """
        Check to see whether a UID is reserved.
        @param uid: the UID to check
        @return: True if C{uid} is reserved, False otherwise.
        """

        rowiter = self.index._db_execute("select UID, TIME from RESERVED where UID = :1", uid)
        for uid, attime in rowiter:
            # Double check that the time is within a reasonable period of now
            # otherwise we probably have a stale reservation
            tm = time.strptime(attime[:19], "%Y-%m-%d %H:%M:%S")
            dt = datetime.datetime(year=tm.tm_year, month=tm.tm_mon, day=tm.tm_mday, hour=tm.tm_hour, minute=tm.tm_min, second = tm.tm_sec)
            if datetime.datetime.now() - dt > datetime.timedelta(seconds=config.UIDReservationTimeOut):
                try:
                    self.index._db_execute("delete from RESERVED where UID = :1", uid)
                    self.index._db_commit()
                except sqlite.Error, e:
                    log.err("Unable to unreserve UID: %s", (e,))
                    self.index._db_rollback()
                    raise
                return False
            else:
                return True

        return False

class AddressBookIndex(AbstractSQLDatabase):
    """
    AddressBook collection index abstract base class that defines the apis for the index.
    """

    def __init__(self, resource):
        """
        @param resource: the L{twistedcaldav.static.CalDAVFile} resource to
            index. C{resource} must be an addressbook collection (ie.
            C{resource.isAddressBookCollection()} returns C{True}.)
        """
        assert resource.isAddressBookCollection(), "non-addressbook collection resource %s has no index." % (resource,)
        self.resource = resource
        db_filename = os.path.join(self.resource.fp.path, db_basename)
        super(AddressBookIndex, self).__init__(db_filename, False)

        if config.Memcached.Pools.Default.ClientEnabled:
            self.reserver = MemcachedUIDReserver(self)
        else:
            self.reserver = SQLUIDReserver(self)

    def create(self):
        """
        Create the index and initialize it.
        """
        self._db()

    def recreate(self):
        """
        Delete the database and re-create it
        """
        try:
            os.remove(self.dbpath)
        except OSError:
            pass
        self.create()

    #
    # A dict of sets. The dict keys are address book collection paths,
    # and the sets contains reserved UIDs for each path.
    #
    
    def reserveUID(self, uid):
        return self.reserver.reserveUID(uid)
    
    def unreserveUID(self, uid):
        return self.reserver.unreserveUID(uid)
    
    def isReservedUID(self, uid):
        return self.reserver.isReservedUID(uid)
        
    def isAllowedUID(self, uid, *names):
        """
        Checks to see whether to allow an operation which would add the
        specified UID to the index.  Specifically, the operation may not
        violate the constraint that UIDs must be unique.
        @param uid: the UID to check
        @param names: the names of resources being replaced or deleted by the
            operation; UIDs associated with these resources are not checked.
        @return: True if the UID is not in the index and is not reserved,
            False otherwise.
        """
        rname = self.resourceNameForUID(uid)
        return (rname is None or rname in names)
 
    def resourceNamesForUID(self, uid):
        """
        Looks up the names of the resources with the given UID.
        @param uid: the UID of the resources to look up.
        @return: a list of resource names
        """
        names = self._db_values_for_sql("select NAME from RESOURCE where UID = :1", uid)

        #
        # Check that each name exists as a child of self.resource.  If not, the
        # resource record is stale.
        #
        resources = []
        for name in names:
            name_utf8 = name.encode("utf-8")
            if name is not None and self.resource.getChild(name_utf8) is None:
                # Clean up
                log.err("Stale resource record found for child %s with UID %s in %s" % (name, uid, self.resource))
                self._delete_from_db(name, uid)
                self._db_commit()
            else:
                resources.append(name)

        return resources

    def resourceNameForUID(self, uid):
        """
        Looks up the name of the resource with the given UID.
        @param uid: the UID of the resource to look up.
        @return: If the resource is found, its name; C{None} otherwise.
        """
        result = None

        for name in self.resourceNamesForUID(uid):
            assert result is None, "More than one resource with UID %s in address book collection %r" % (uid, self)
            result = name
            
        return result

    def resourceUIDForName(self, name):
        """
        Looks up the UID of the resource with the given name.
        @param name: the name of the resource to look up.
        @return: If the resource is found, the UID of the resource; C{None}
            otherwise.
        """
        uid = self._db_value_for_sql("select UID from RESOURCE where NAME = :1", name)

        return uid

    def addResource(self, name, vcard, fast=False):
        """
        Adding or updating an existing resource.
        To check for an update we attempt to get an existing UID
        for the resource name. If present, then the index entries for
        that UID are removed. After that the new index entries are added.
        @param name: the name of the resource to add.
        @param vCard: a L{Component} object representing the resource
            contents.
        @param fast: if C{True} do not do commit, otherwise do commit.
        """
        oldUID = self.resourceUIDForName(name)
        if oldUID is not None:
            self._delete_from_db(name, oldUID)
        self._add_to_db(name, vcard)
        if not fast:
            self._db_commit()

    def deleteResource(self, name):
        """
        Remove this resource from the index.
        @param name: the name of the resource to add.
        @param uid: the UID of the vcard component in the resource.
        """
        uid = self.resourceUIDForName(name)
        if uid is not None:
            self._delete_from_db(name, uid)
            self._db_commit()
    
    def resourceExists(self, name):
        """
        Determines whether the specified resource name exists in the index.
        @param name: the name of the resource to test
        @return: True if the resource exists, False if not
        """
        uid = self._db_value_for_sql("select UID from RESOURCE where NAME = :1", name)
        return uid is not None
    
    def resourcesExist(self, names):
        """
        Determines whether the specified resource name exists in the index.
        @param names: a C{list} containing the names of the resources to test
        @return: a C{list} of all names that exist
        """
        statement = "select NAME from RESOURCE where NAME in ("
        for ctr, ignore_name in enumerate(names):
            if ctr != 0:
                statement += ", "
            statement += ":%s" % (ctr,)
        statement += ")"
        results = self._db_values_for_sql(statement, *names)
        return results
    
    def searchValid(self, filter):
        if isinstance(filter, carddavxml.Filter):
            qualifiers = addressbookquery.sqladdressbookquery(filter)
        else:
            qualifiers = None
            
        return qualifiers is not None

    def search(self, filter):
        """
        Finds resources matching the given qualifiers.
        @param filter: the L{Filter} for the addressbook-query to execute.
        @return: an interable iterable of tuples for each resource matching the
            given C{qualifiers}. The tuples are C{(name, uid, type)}, where
            C{name} is the resource name, C{uid} is the resource UID, and
            C{type} is the resource iCalendar component type.x
        """
        # FIXME: Don't forget to use maximum_future_expansion_duration when we
        # start caching...
        
        # Make sure we have a proper Filter element and get the partial SQL statement to use.
        if isinstance(filter, carddavxml.Filter):
            qualifiers = addressbookquery.sqladdressbookquery(filter)
        else:
            qualifiers = None
        if qualifiers is not None:
            rowiter = self._db_execute("select DISTINCT RESOURCE.NAME, RESOURCE.UID" + qualifiers[0], *qualifiers[1])
        else:
            rowiter = self._db_execute("select NAME, UID from RESOURCE")
            
        for row in rowiter:
            name = row[0]
            if self.resource.getChild(name.encode("utf-8")):
                yield row
            else:
                log.err("vCard resource %s is missing from %s. Removing from index."
                        % (name, self.resource))
                self.deleteResource(name)

    def _db_version(self):
        """
        @return: the schema version assigned to this index.
        """
        return schema_version
        
    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return "AddressBook"
        
    def _db_init_data_tables(self, q):
        """
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """
        
        # Create database where the RESOURCE table has unique UID column.
        self._db_init_data_tables_base(q, True)

    def _db_init_data_tables_base(self, q, uidunique):
        """
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """
        #
        # RESOURCE table is the primary index table
        #   NAME: Last URI component (eg. <uid>.ics, RESOURCE primary key)
        #   UID: iCalendar UID (may or may not be unique)
        #
        q.execute(
            """
            create table RESOURCE (
                NAME           text unique,
                UID            text unique
            )
            """
        )

        #
        # RESERVED table tracks reserved UIDs
        #   UID: The UID being reserved
        #   TIME: When the reservation was made
        #
        q.execute(
            """
            create table RESERVED (
                UID  text unique,
                TIME date
            )
            """
        )

    def _db_recreate(self, do_commit=True):
        """
        Re-create the database tables from existing address book data.
        """
        
        #
        # Populate the DB with data from already existing resources.
        # This allows for index recovery if the DB file gets
        # deleted.
        #
        fp = self.resource.fp
        for name in fp.listdir():
            if name.startswith("."):
                continue

            try:
                stream = fp.child(name).open()
            except (IOError, OSError), e:
                log.err("Unable to open resource %s: %s" % (name, e))
                continue

            try:
                # FIXME: This is blocking I/O
                try:
                    vcard = Component.fromStream(stream)
                    vcard.validForCardDAV()
                except ValueError:
                    log.err("Non-addressbook resource: %s" % (name,))
                else:
                    #log.msg("Indexing resource: %s" % (name,))
                    self.addResource(name, vcard, True)
            finally:
                stream.close()
        
        # Do commit outside of the loop for better performance
        if do_commit:
            self._db_commit()

    def _add_to_db(self, name, vcard, cursor = None):
        """
        Records the given address book resource in the index with the given name.
        Resource names and UIDs must both be unique; only one resource name may
        be associated with any given UID and vice versa.
        NB This method does not commit the changes to the db - the caller
        MUST take care of that
        @param name: the name of the resource to add.
        @param vcard: a L{AddressBook} object representing the resource
            contents.
        """
        uid = vcard.resourceUID()

        self._db_execute(
            """
            insert into RESOURCE (NAME, UID)
            values (:1, :2)
            """, name, uid,
        )
    
    def _delete_from_db(self, name, uid):
        """
        Deletes the specified entry from all dbs.
        @param name: the name of the resource to delete.
        @param uid: the uid of the resource to delete.
        """
        self._db_execute("delete from RESOURCE where NAME = :1", name)
