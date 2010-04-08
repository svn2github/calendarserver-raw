##
# Copyright (c) 2005-2010 Apple Inc. All rights reserved.
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
CalDAV Index.

This API is considered private to static.py and is therefore subject to
change.
"""

__all__ = [
    "db_basename",
    "ReservationError",
    "MemcachedUIDReserver",
    "Index",
    "IndexSchedule",
    "IndexedSearchException",
]

import datetime
import os
import time
import hashlib

try:
    import sqlite3 as sqlite
except ImportError:
    from pysqlite2 import dbapi2 as sqlite

from vobject.icalendar import utc

from twisted.internet.defer import maybeDeferred, succeed

from twext.python.log import Logger, LoggingMixIn

from twistedcaldav.ical import Component
from twistedcaldav.query import calendarquery, queryfilter
from twistedcaldav.sql import AbstractSQLDatabase
from twistedcaldav.sql import db_prefix
from twistedcaldav.instance import InvalidOverriddenInstanceError
from twistedcaldav.config import config
from twistedcaldav.memcachepool import CachePoolUserMixIn

log = Logger()

db_basename = db_prefix + "sqlite"
schema_version = "10"
collection_types = {"Calendar": "Regular Calendar Collection", "iTIP": "iTIP Calendar Collection"}

icalfbtype_to_indexfbtype = {
    "FREE"            : 'F',
    "BUSY"            : 'B',
    "BUSY-UNAVAILABLE": 'U',
    "BUSY-TENTATIVE"  : 'T',
}
indexfbtype_to_icalfbtype = dict([(v, k) for k,v in icalfbtype_to_indexfbtype.iteritems()])

#
# Duration into the future through which recurrences are expanded in the index
# by default.  This is a caching parameter which affects the size of the index;
# it does not affect search results beyond this period, but it may affect
# performance of such a search.
#
default_future_expansion_duration = datetime.timedelta(days=365*1)

#
# Maximum duration into the future through which recurrences are expanded in the
# index.  This is a caching parameter which affects the size of the index; it
# does not affect search results beyond this period, but it may affect
# performance of such a search.
#
# When a search is performed on a time span that goes beyond that which is
# expanded in the index, we have to open each resource which may have data in
# that time period.  In order to avoid doing that multiple times, we want to
# cache those results.  However, we don't necessarily want to cache all
# occurrences into some obscenely far-in-the-future date, so we cap the caching
# period.  Searches beyond this period will always be relatively expensive for
# resources with occurrences beyond this period.
#
maximum_future_expansion_duration = datetime.timedelta(days=365*5)

class ReservationError(LookupError):
    """
    Attempt to reserve a UID which is already reserved or to unreserve a UID
    which is not reserved.
    """

class IndexedSearchException(ValueError):
    pass

class SyncTokenValidException(ValueError):
    pass

class AbstractCalendarIndex(AbstractSQLDatabase, LoggingMixIn):
    """
    Calendar collection index abstract base class that defines the apis for the index.
    This will be subclassed for the two types of index behaviour we need: one for
    regular calendar collections, one for schedule calendar collections.
    """

    def __init__(self, resource):
        """
        @param resource: the L{twistedcaldav.static.CalDAVFile} resource to
            index. C{resource} must be a calendar collection (ie.
            C{resource.isPseudoCalendarCollection()} returns C{True}.)
        """
        self.resource = resource
        db_filename = os.path.join(self.resource.fp.path, db_basename)
        super(AbstractCalendarIndex, self).__init__(db_filename, False)

    def create(self):
        """
        Create the index and initialize it.
        """
        self._db()

    def reserveUID(self, uid):
        """
        Reserve a UID for this index's resource.
        @param uid: the UID to reserve
        @raise ReservationError: if C{uid} is already reserved
        """
        raise NotImplementedError

    def unreserveUID(self, uid):
        """
        Unreserve a UID for this index's resource.
        @param uid: the UID to reserve
        @raise ReservationError: if C{uid} is not reserved
        """
        raise NotImplementedError

    def isReservedUID(self, uid):
        """
        Check to see whether a UID is reserved.
        @param uid: the UID to check
        @return: True if C{uid} is reserved, False otherwise.
        """
        raise NotImplementedError

    def isAllowedUID(self, uid, *names):
        """
        Checks to see whether to allow an operation with adds the the specified
        UID is allowed to the index.  Specifically, the operation may not
        violate the constraint that UIDs must be unique, and the UID must not
        be reserved.
        @param uid: the UID to check
        @param names: the names of resources being replaced or deleted by the
            operation; UIDs associated with these resources are not checked.
        @return: True if the UID is not in the index and is not reserved,
            False otherwise.
        """
        raise NotImplementedError

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
                self._delete_from_db(name, uid, None)
                self._db_commit()
            else:
                resources.append(name_utf8)

        return resources

    def resourceNameForUID(self, uid):
        """
        Looks up the name of the resource with the given UID.
        @param uid: the UID of the resource to look up.
        @return: If the resource is found, its name; C{None} otherwise.
        """
        result = None

        for name in self.resourceNamesForUID(uid):
            assert result is None, "More than one resource with UID %s in calendar collection %r" % (uid, self)
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

    def addResource(self, name, calendar, revision, fast=False, reCreate=False):
        """
        Adding or updating an existing resource.
        To check for an update we attempt to get an existing UID
        for the resource name. If present, then the index entries for
        that UID are removed. After that the new index entries are added.
        @param name: the name of the resource to add.
        @param calendar: a L{Calendar} object representing the resource
            contents.
        @param fast: if C{True} do not do commit, otherwise do commit.
        """
        oldUID = self.resourceUIDForName(name)
        if oldUID is not None:
            self._delete_from_db(name, oldUID, None)
        self._add_to_db(name, calendar, revision, reCreate=reCreate)
        if not fast:
            self._db_commit()

    def deleteResource(self, name, revision):
        """
        Remove this resource from the index.
        @param name: the name of the resource to add.
        @param uid: the UID of the calendar component in the resource.
        """
        uid = self.resourceUIDForName(name)
        if uid is not None:
            self._delete_from_db(name, uid, revision)
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


    def testAndUpdateIndex(self, minDate):
        # Find out if the index is expanded far enough
        names = self.notExpandedBeyond(minDate)
        # Actually expand recurrence max
        for name in names:
            self.log_info("Search falls outside range of index for %s %s" % (name, minDate))
            self.reExpandResource(name, minDate)

    def whatchanged(self, revision):

        results = [(name.encode("utf-8"), created, wasdeleted) for name, created, wasdeleted in self._db_execute("select NAME, CREATEDREVISION, WASDELETED from REVISIONS where REVISION > :1", revision)]
        results.sort(key=lambda x:x[1])
        
        changed = []
        deleted = []
        for name, created, wasdeleted in results:
            if name:
                if wasdeleted == 'Y':
                    # Don't report items that were created/deleted since the requested revision
                    if created <= revision:
                        deleted.append(name)
                else:
                    changed.append(name)
            else:
                raise SyncTokenValidException
        
        return changed, deleted,

    def indexedSearch(self, filter, useruid="", fbtype=False):
        """
        Finds resources matching the given qualifiers.
        @param filter: the L{Filter} for the calendar-query to execute.
        @return: an iterable of tuples for each resource matching the
            given C{qualifiers}. The tuples are C{(name, uid, type)}, where
            C{name} is the resource name, C{uid} is the resource UID, and
            C{type} is the resource iCalendar component type.x
        """

        # Make sure we have a proper Filter element and get the partial SQL
        # statement to use.
        if isinstance(filter, queryfilter.Filter):
            qualifiers = calendarquery.sqlcalendarquery(filter)
            if qualifiers is not None:
                # Determine how far we need to extend the current expansion of
                # events. If we have an open-ended time-range we will expand one
                # year past the start. That should catch bounded recurrences - unbounded
                # will have been indexed with an "infinite" value always included.
                maxDate, isStartDate = filter.getmaxtimerange()
                if maxDate:
                    maxDate = maxDate.date()
                    if isStartDate:
                        maxDate += datetime.timedelta(days=365)
                    self.testAndUpdateIndex(maxDate)
            else:
                # We cannot handler this filter in an indexed search
                raise IndexedSearchException()

        else:
            qualifiers = None

        # Perform the search
        if qualifiers is None:
            rowiter = self._db_execute("select NAME, UID, TYPE from RESOURCE")
        else:
            if fbtype:
                # Lookup the useruid - try the empty (default) one if needed
                dbuseruid = self._db_value_for_sql(
                    "select PERUSERID from PERUSER where USERUID == :1",
                    useruid,
                )

                # For a free-busy time-range query we return all instances
                rowiter = self._db_execute(
                    "select DISTINCT RESOURCE.NAME, RESOURCE.UID, RESOURCE.TYPE, RESOURCE.ORGANIZER, TIMESPAN.FLOAT, TIMESPAN.START, TIMESPAN.END, TIMESPAN.FBTYPE, TIMESPAN.TRANSPARENT, TRANSPARENCY.TRANSPARENT" + 
                    qualifiers[0] % (dbuseruid if dbuseruid else "",),
                    *qualifiers[1]
                )
            else:
                rowiter = self._db_execute("select DISTINCT RESOURCE.NAME, RESOURCE.UID, RESOURCE.TYPE" + qualifiers[0], *qualifiers[1])

        # Check result for missing resources

        for row in rowiter:
            name = row[0]
            if self.resource.getChild(name.encode("utf-8")):
                if fbtype:
                    row = list(row)
                    if row[9]:
                        row[8] = row[9]
                    del row[9]
                yield row
            else:
                log.err("Calendar resource %s is missing from %s. Removing from index."
                        % (name, self.resource))
                self.deleteResource(name)

    def bruteForceSearch(self):
        """
        List the whole index and tests for existence, updating the index
        @return: all resources in the index
        """
        # List all resources
        rowiter = self._db_execute("select NAME, UID, TYPE from RESOURCE")

        # Check result for missing resources:

        for row in rowiter:
            name = row[0]
            if self.resource.getChild(name.encode("utf-8")):
                yield row
            else:
                log.err("Calendar resource %s is missing from %s. Removing from index."
                        % (name, self.resource))
                self.deleteResource(name)


    def _db_version(self):
        """
        @return: the schema version assigned to this index.
        """
        return schema_version

    def _add_to_db(self, name, calendar, revision, cursor=None, expand_until=None, reCreate=False):
        """
        Records the given calendar resource in the index with the given name.
        Resource names and UIDs must both be unique; only one resource name may
        be associated with any given UID and vice versa.
        NB This method does not commit the changes to the db - the caller
        MUST take care of that
        @param name: the name of the resource to add.
        @param calendar: a L{Calendar} object representing the resource
            contents.
        """
        raise NotImplementedError

    def _delete_from_db(self, name, uid, revision):
        """
        Deletes the specified entry from all dbs.
        @param name: the name of the resource to delete.
        @param uid: the uid of the resource to delete.
        """
        raise NotImplementedError

class CalendarIndex (AbstractCalendarIndex):
    """
    Calendar index - abstract class for indexer that indexes calendar objects in a collection.
    """

    def __init__(self, resource):
        """
        @param resource: the L{twistedcaldav.static.CalDAVFile} resource to
            index.
        """
        super(CalendarIndex, self).__init__(resource)

    def _db_init_data_tables_base(self, q, uidunique):
        """
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """
        #
        # RESOURCE table is the primary index table
        #   NAME: Last URI component (eg. <uid>.ics, RESOURCE primary key)
        #   UID: iCalendar UID (may or may not be unique)
        #   TYPE: iCalendar component type
        #   RECURRANCE_MAX: Highest date of recurrence expansion
        #   ORGANIZER: cu-address of the Organizer of the event
        #
        q.execute(
            """
            create table RESOURCE (
                RESOURCEID     integer primary key autoincrement,
                NAME           text unique,
                UID            text%s,
                TYPE           text,
                RECURRANCE_MAX date,
                ORGANIZER      text
            )
            """ % (" unique" if uidunique else "",)
        )

        #
        # TIMESPAN table tracks (expanded) time spans for resources
        #   NAME: Related resource (RESOURCE foreign key)
        #   FLOAT: 'Y' if start/end are floating, 'N' otherwise
        #   START: Start date
        #   END: End date
        #   FBTYPE: FBTYPE value:
        #     '?' - unknown
        #     'F' - free
        #     'B' - busy
        #     'U' - busy-unavailable
        #     'T' - busy-tentative
        #   TRANSPARENT: Y if transparent, N if opaque (default non-per-user value)
        #
        q.execute(
            """
            create table TIMESPAN (
                INSTANCEID   integer primary key autoincrement,
                RESOURCEID   integer,
                FLOAT        text(1),
                START        date,
                END          date,
                FBTYPE       text(1),
                TRANSPARENT  text(1)
            )
            """
        )
        q.execute(
            """
            create index STARTENDFLOAT on TIMESPAN (START, END, FLOAT)
            """
        )

        #
        # PERUSER table tracks per-user ids
        #   PERUSERID: autoincrement primary key
        #   UID: User ID used in calendar data
        #
        q.execute(
            """
            create table PERUSER (
                PERUSERID       integer primary key autoincrement,
                USERUID         text
            )
            """
        )
        q.execute(
            """
            create index PERUSER_UID on PERUSER (USERUID)
            """
        )

        #
        # TRANSPARENCY table tracks per-user per-instance transparency
        #   PERUSERID: user id key
        #   INSTANCEID: instance id key
        #   TRANSPARENT: Y if transparent, N if opaque
        #
        q.execute(
            """
            create table TRANSPARENCY (
                PERUSERID       integer,
                INSTANCEID      integer,
                TRANSPARENT     text(1)
            )
            """
        )

        #
        # REVISIONS table tracks changes
        #   NAME: Last URI component (eg. <uid>.ics, RESOURCE primary key)
        #   REVISION: revision number
        #   WASDELETED: Y if revision deleted, N if added or changed
        #
        q.execute(
            """
            create table REVISIONS (
                NAME            text unique,
                REVISION        integer,
                CREATEDREVISION integer,
                WASDELETED      text(1)
            )
            """
        )
        q.execute(
            """
            create index REVISION on REVISIONS (REVISION)
            """
        )

        if uidunique:
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

        # Cascading triggers to help on delete
        q.execute(
            """
            create trigger resourceDelete after delete on RESOURCE
            for each row
            begin
                delete from TIMESPAN where TIMESPAN.RESOURCEID = OLD.RESOURCEID;
            end
            """
        )
        q.execute(
            """
            create trigger timespanDelete after delete on TIMESPAN
            for each row
            begin
                delete from TRANSPARENCY where INSTANCEID = OLD.INSTANCEID;
            end
            """
        )
        
    def _db_can_upgrade(self, old_version):
        """
        Can we do an in-place upgrade
        """
        
        # v10 is a big change - no upgrade possible
        return False

    def _db_upgrade_data_tables(self, q, old_version):
        """
        Upgrade the data from an older version of the DB.
        """

        # v10 is a big change - no upgrade possible
        pass

    def notExpandedBeyond(self, minDate):
        """
        Gives all resources which have not been expanded beyond a given date
        in the index
        """
        return self._db_values_for_sql("select NAME from RESOURCE where RECURRANCE_MAX < :1", minDate)

    def reExpandResource(self, name, expand_until):
        """
        Given a resource name, remove it from the database and re-add it
        with a longer expansion.
        """
        calendar = self.resource.getChild(name).iCalendar()
        self._add_to_db(name, calendar, None, expand_until=expand_until, reCreate=True)
        self._db_commit()

    def _add_to_db(self, name, calendar, revision, cursor = None, expand_until=None, reCreate=False):
        """
        Records the given calendar resource in the index with the given name.
        Resource names and UIDs must both be unique; only one resource name may
        be associated with any given UID and vice versa.
        NB This method does not commit the changes to the db - the caller
        MUST take care of that
        @param name: the name of the resource to add.
        @param calendar: a L{Calendar} object representing the resource
            contents.
        """
        uid = calendar.resourceUID()
        organizer = calendar.getOrganizer()
        if not organizer:
            organizer = ""

        # Decide how far to expand based on the component
        master = calendar.masterComponent()
        if master is None or not calendar.isRecurring() and not calendar.isRecurringUnbounded():
            # When there is no master we have a set of overridden components - index them all.
            # When there is one instance - index it.
            # When bounded - index all.
            expand = datetime.datetime(2100, 1, 1, 0, 0, 0, tzinfo=utc)
        else:
            if expand_until:
                expand = expand_until
            else:
                expand = datetime.date.today() + default_future_expansion_duration
    
            if expand > (datetime.date.today() + maximum_future_expansion_duration):
                raise IndexedSearchException

        try:
            instances = calendar.expandTimeRanges(expand, ignoreInvalidInstances=reCreate)
        except InvalidOverriddenInstanceError, e:
            log.err("Invalid instance %s when indexing %s in %s" % (e.rid, name, self.resource,))
            raise

        self._delete_from_db(name, uid, None)

        # Add RESOURCE item
        self._db_execute(
            """
            insert into RESOURCE (NAME, UID, TYPE, RECURRANCE_MAX, ORGANIZER)
            values (:1, :2, :3, :4, :5)
            """, name, uid, calendar.resourceType(), instances.limit, organizer
        )
        resourceid = self.lastrowid

        # Get a set of all referenced per-user UIDs and map those to entries already
        # in the DB and add new ones as needed
        useruids = calendar.allPerUserUIDs()
        useruids.add("")
        useruidmap = {}
        for useruid in useruids:
            peruserid = self._db_value_for_sql(
                "select PERUSERID from PERUSER where USERUID = :1",
                useruid
            )
            if peruserid is None:
                self._db_execute(
                    """
                    insert into PERUSER (USERUID)
                    values (:1)
                    """, useruid
                )
                peruserid = self.lastrowid
            useruidmap[useruid] = peruserid
            
        for key in instances:
            instance = instances[key]
            start = instance.start.replace(tzinfo=utc)
            end = instance.end.replace(tzinfo=utc)
            float = 'Y' if instance.start.tzinfo is None else 'N'
            transp = 'T' if instance.component.propertyValue("TRANSP") == "TRANSPARENT" else 'F'
            self._db_execute(
                """
                insert into TIMESPAN (RESOURCEID, FLOAT, START, END, FBTYPE, TRANSPARENT)
                values (:1, :2, :3, :4, :5, :6)
                """,
                resourceid,
                float,
                start,
                end,
                icalfbtype_to_indexfbtype.get(instance.component.getFBType(), 'F'),
                transp
            )
            instanceid = self.lastrowid
            peruserdata = calendar.perUserTransparency(instance.rid)
            for useruid, transp in peruserdata:
                peruserid = useruidmap[useruid]
                self._db_execute(
                    """
                    insert into TRANSPARENCY (PERUSERID, INSTANCEID, TRANSPARENT)
                    values (:1, :2, :3)
                    """, peruserid, instanceid, 'T' if transp else 'F'
                )
                    

        # Special - for unbounded recurrence we insert a value for "infinity"
        # that will allow an open-ended time-range to always match it.
        if calendar.isRecurringUnbounded():
            start = datetime.datetime(2100, 1, 1, 0, 0, 0, tzinfo=utc)
            end = datetime.datetime(2100, 1, 1, 1, 0, 0, tzinfo=utc)
            float = 'N'
            self._db_execute(
                """
                insert into TIMESPAN (RESOURCEID, FLOAT, START, END, FBTYPE, TRANSPARENT)
                values (:1, :2, :3, :4, :5, :6)
                """, resourceid, float, start, end, '?', '?'
            )
            instanceid = self.lastrowid
            peruserdata = calendar.perUserTransparency(None)
            for useruid, transp in peruserdata:
                peruserid = useruidmap[useruid]
                self._db_execute(
                    """
                    insert into TRANSPARENCY (PERUSERID, INSTANCEID, TRANSPARENT)
                    values (:1, :2, :3)
                    """, peruserid, instanceid, 'T' if transp else 'F'
                )
            
        if revision is not None:
            created = self._db_value_for_sql("select CREATEDREVISION from REVISIONS where NAME = :1", name)
            if created is None:
                created = revision
            self._db_execute(
                """
                insert or replace into REVISIONS (NAME, REVISION, CREATEDREVISION, WASDELETED)
                values (:1, :2, :3, :4)
                """, name, revision, revision, 'N',
            )

    def _delete_from_db(self, name, uid, revision):
        """
        Deletes the specified entry from all dbs.
        @param name: the name of the resource to delete.
        @param uid: the uid of the resource to delete.
        """
        self._db_execute("delete from RESOURCE where NAME = :1", name)
        if revision is not None:
            self._db_execute(
                """
                update REVISIONS SET REVISION = :1, WASDELETED = :2
                where NAME = :3
                """, revision, 'Y', name
            )


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
                    "UID %s already reserved for calendar collection %s."
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
                    "UID %s is not reserved for calendar collection %s."
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
                "UID %s already reserved for calendar collection %s."
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
                    "UID %s is not reserved for calendar collection %s."
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



class Index (CalendarIndex):
    """
    Calendar collection index - regular collection that enforces CalDAV UID uniqueness requirement.
    """

    def __init__(self, resource):
        """
        @param resource: the L{twistedcaldav.static.CalDAVFile} resource to
            index. C{resource} must be a calendar collection (i.e.
            C{resource.isPseudoCalendarCollection()} returns C{True}.)
        """
        assert resource.isCalendarCollection(), "non-calendar collection resource %s has no index." % (resource,)
        super(Index, self).__init__(resource)

        if (
            hasattr(config, "Memcached") and
            config.Memcached.Pools.Default.ClientEnabled
        ):
            self.reserver = MemcachedUIDReserver(self)
        else:
            self.reserver = SQLUIDReserver(self)

    #
    # A dict of sets. The dict keys are calendar collection paths,
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

    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return collection_types["Calendar"]

    def _db_init_data_tables(self, q):
        """
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """

        # Create database where the RESOURCE table has unique UID column.
        self._db_init_data_tables_base(q, True)

    def _db_recreate(self, do_commit=True):
        """
        Re-create the database tables from existing calendar data.
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

            # FIXME: This is blocking I/O
            try:
                calendar = Component.fromStream(stream)
                calendar.validateForCalDAV()
            except ValueError:
                log.err("Non-calendar resource: %s" % (name,))
            else:
                #log.msg("Indexing resource: %s" % (name,))
                self.addResource(name, calendar, 0, True, reCreate=True)
            finally:
                stream.close()

        # Do commit outside of the loop for better performance
        if do_commit:
            self._db_commit()
            
        # This is a deferred but we can't defer at this point...
        self.resource.bumpSyncToken(True)

class IndexSchedule (CalendarIndex):
    """
    Schedule collection index - does not require UID uniqueness.
    """
    def __init__(self, resource):
        """
        @param resource: the L{twistedcaldav.static.CalDAVFile} resource to
            index. C{resource} must be a calendar collection (i.e.
            C{resource.isPseudoCalendarCollection()} returns C{True}.)
        """
        assert resource.isPseudoCalendarCollection() and not resource.isCalendarCollection(), "non-calendar collection resource %s has no index." % (resource,)
        super(IndexSchedule, self).__init__(resource)

    def reserveUID(self, uid): #@UnusedVariable
        """
        Reserve a UID for this index's resource.
        @param uid: the UID to reserve
        @raise ReservationError: if C{uid} is already reserved
        """

        # iTIP does not require unique UIDs
        return succeed(None)

    def unreserveUID(self, uid): #@UnusedVariable
        """
        Unreserve a UID for this index's resource.
        @param uid: the UID to reserve
        @raise ReservationError: if C{uid} is not reserved
        """

        # iTIP does not require unique UIDs
        return succeed(None)

    def isReservedUID(self, uid): #@UnusedVariable
        """
        Check to see whether a UID is reserved.
        @param uid: the UID to check
        @return: True if C{uid} is reserved, False otherwise.
        """

        # iTIP does not require unique UIDs
        return succeed(False)

    def isAllowedUID(self, uid, *names): #@UnusedVariable
        """
        Checks to see whether to allow an operation with adds the the specified
        UID is allowed to the index.  Specifically, the operation may not
        violate the constraint that UIDs must be unique, and the UID must not
        be reserved.
        @param uid: the UID to check
        @param names: the names of resources being replaced or deleted by the
            operation; UIDs associated with these resources are not checked.
        @return: True if the UID is not in the index and is not reserved,
            False otherwise.
        """

        # iTIP does not require unique UIDs
        return True

    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return collection_types["iTIP"]

    def _db_init_data_tables(self, q):
        """
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """

        # Create database where the RESOURCE table has a UID column that is not unique.
        self._db_init_data_tables_base(q, False)

    def _db_recreate(self, do_commit=True):
        """
        Re-create the database tables from existing calendar data.
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

            # FIXME: This is blocking I/O
            try:
                calendar = Component.fromStream(stream)
                calendar.validCalendarForCalDAV()
                calendar.validateComponentsForCalDAV(True)
            except ValueError:
                log.err("Non-calendar resource: %s" % (name,))
            else:
                #log.msg("Indexing resource: %s" % (name,))
                self.addResource(name, calendar, 0, True, reCreate=True)
            finally:
                stream.close()

        # Do commit outside of the loop for better performance
        if do_commit:
            self._db_commit()
            
        # This is a deferred but we can't defer at this point...
        self.resource.bumpSyncToken(True)
