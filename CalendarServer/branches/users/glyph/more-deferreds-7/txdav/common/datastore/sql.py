# -*- test-case-name: txdav.caldav.datastore.test.test_sql,txdav.carddav.datastore.test.test_sql -*-
##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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
SQL data store.
"""

__all__ = [
    "CommonDataStore",
    "CommonStoreTransaction",
    "CommonHome",
]

import sys

from Queue import Queue

from twext.python.log import Logger, LoggingMixIn
from twext.web2.dav.element.rfc2518 import ResourceType
from twext.web2.http_headers import MimeType

from twisted.application.service import Service
from twisted.python import hashlib
from twisted.python.modules import getModule
from twisted.python.util import FancyEqMixin

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, Deferred
from twisted.python.failure import Failure

from twistedcaldav.customxml import NotificationType
from twistedcaldav.dateops import datetimeMktime

from txdav.common.datastore.sql_legacy import PostgresLegacyNotificationsEmulator
from txdav.caldav.icalendarstore import ICalendarTransaction, ICalendarStore

from txdav.carddav.iaddressbookstore import IAddressBookTransaction

from txdav.common.datastore.sql_tables import CALENDAR_HOME_TABLE, \
    ADDRESSBOOK_HOME_TABLE, NOTIFICATION_HOME_TABLE, _BIND_MODE_OWN, \
    _BIND_STATUS_ACCEPTED, NOTIFICATION_OBJECT_REVISIONS_TABLE
from txdav.common.icommondatastore import HomeChildNameNotAllowedError, \
    HomeChildNameAlreadyExistsError, NoSuchHomeChildError, \
    ObjectResourceNameNotAllowedError, ObjectResourceNameAlreadyExistsError, \
    NoSuchObjectResourceError
from txdav.common.inotifications import INotificationCollection, \
    INotificationObject
from txdav.base.datastore.sql import memoized

from txdav.idav import AlreadyFinishedError
from txdav.base.propertystore.base import PropertyName
from txdav.base.propertystore.sql import PropertyStore

from zope.interface.declarations import implements, directlyProvides

import datetime

v1_schema = getModule(__name__).filePath.sibling(
    "sql_schema_v1.sql").getContent()

log = Logger()

ECALENDARTYPE = 0
EADDRESSBOOKTYPE = 1

# Labels used to identify the class of resource being modified, so that
# notification systems can target the correct application
NotifierPrefixes = {
    ECALENDARTYPE : "CalDAV",
    EADDRESSBOOKTYPE : "CardDAV",
}

class CommonDataStore(Service, object):

    implements(ICalendarStore)

    def __init__(self, connectionFactory, notifierFactory, attachmentsPath,
                 enableCalendars=True, enableAddressBooks=True):
        assert enableCalendars or enableAddressBooks

        self.connectionFactory = connectionFactory
        self.notifierFactory = notifierFactory
        self.attachmentsPath = attachmentsPath
        self.enableCalendars = enableCalendars
        self.enableAddressBooks = enableAddressBooks


    def eachCalendarHome(self):
        """
        @see L{ICalendarStore.eachCalendarHome}
        """
        return []


    def eachAddressbookHome(self):
        """
        @see L{IAddressbookStore.eachAddressbookHome}
        """
        return []


    def newTransaction(self, label="unlabeled", migrating=False):
        return CommonStoreTransaction(
            self,
            self.connectionFactory,
            self.enableCalendars,
            self.enableAddressBooks,
            self.notifierFactory,
            label,
            migrating,
        )


_DONE = object()

_STATE_STOPPED = 'STOPPED'
_STATE_RUNNING = 'RUNNING'
_STATE_STOPPING = 'STOPPING'

class ThreadHolder(object):
    """
    A queue which will hold a reactor threadpool thread open until all of the
    work in that queue is done.
    """

    def __init__(self, reactor):
        self._reactor = reactor
        self._state = _STATE_STOPPED
        self._stopper = None
        self._q = None


    def _run(self):
        """
        Worker function which runs in a non-reactor thread.
        """
        while True:
            work = self._q.get()
            if work is _DONE:
                def finishStopping():
                    self._state = _STATE_STOPPED
                    self._q = None
                    s = self._stopper
                    self._stopper = None
                    s.callback(None)
                self._reactor.callFromThread(finishStopping)
                return
            self._oneWorkUnit(*work)


    def _oneWorkUnit(self, deferred, instruction):
        try: 
            result = instruction()
        except:
            etype, evalue, etb = sys.exc_info()
            def relayFailure():
                f = Failure(evalue, etype, etb)
                deferred.errback(f)
            self._reactor.callFromThread(relayFailure)
        else:
            self._reactor.callFromThread(deferred.callback, result)


    def submit(self, work):
        """
        Submit some work to be run.

        @param work: a 0-argument callable, which will be run in a thread.

        @return: L{Deferred} that fires with the result of L{work}
        """
        d = Deferred()
        self._q.put((d, work))
        return d


    def start(self):
        """
        Start this thing, if it's stopped.
        """
        if self._state != _STATE_STOPPED:
            raise RuntimeError("Not stopped.")
        self._state = _STATE_RUNNING
        self._q = Queue(0)
        self._reactor.callInThread(self._run)


    def stop(self):
        """
        Stop this thing and release its thread, if it's running.
        """
        if self._state != _STATE_RUNNING:
            raise RuntimeError("Not running.")
        s = self._stopper = Deferred()
        self._state = _STATE_STOPPING
        self._q.put(_DONE)
        return s



class CommonStoreTransaction(object):
    """
    Transaction implementation for SQL database.
    """
    _homeClass = {}

    def __init__(self, store, connectionFactory,
                 enableCalendars, enableAddressBooks,
                 notifierFactory, label, migrating=False):
        self._store = store
        self._completed = False
        self._calendarHomes = {}
        self._addressbookHomes = {}
        self._notificationHomes = {}
        self._postCommitOperations = []
        self._notifierFactory = notifierFactory
        self._label = label
        self._migrating = migrating

        extraInterfaces = []
        if enableCalendars:
            extraInterfaces.append(ICalendarTransaction)
        if enableAddressBooks:
            extraInterfaces.append(IAddressBookTransaction)
        directlyProvides(self, *extraInterfaces)

        from txdav.caldav.datastore.sql import CalendarHome
        from txdav.carddav.datastore.sql import AddressBookHome
        CommonStoreTransaction._homeClass[ECALENDARTYPE] = CalendarHome
        CommonStoreTransaction._homeClass[EADDRESSBOOKTYPE] = AddressBookHome
        self._holder = ThreadHolder(reactor)
        self._holder.start()
        def initCursor():
            # support threadlevel=1; we can't necessarily cursor() in a
            # different thread than we do transactions in.

            # FIXME: may need to be pooling ThreadHolders along with
            # connections, if threadlevel=1 requires connect() be called in the
            # same thread as cursor() et. al.
            self._connection = connectionFactory()
            self._cursor = self._connection.cursor()
        self._holder.submit(initCursor)


    def store(self):
        return self._store


    def __repr__(self):
        return 'PG-TXN<%s>' % (self._label,)


    def _reallyExecSQL(self, sql, args=[], raiseOnZeroRowCount=None):
        self._cursor.execute(sql, args)
        if raiseOnZeroRowCount is not None and self._cursor.rowcount == 0:
            raise raiseOnZeroRowCount()
        if self._cursor.description:
            return self._cursor.fetchall()
        else:
            return None


    noisy = False

    def execSQL(self, *args, **kw):
        result = self._holder.submit(
            lambda : self._reallyExecSQL(*args, **kw)
        )
        if self.noisy:
            def reportResult(results):
                sys.stdout.write("\n".join([
                    "",
                    "SQL: %r %r" % (args, kw),
                    "Results: %r" % (results,),
                    "",
                    ]))
                return results
            result.addBoth(reportResult)
        return result


    def __del__(self):
        if not self._completed:
            print 'CommonStoreTransaction.__del__: OK'
            self.abort()


    @memoized('uid', '_calendarHomes')
    def calendarHomeWithUID(self, uid, create=False):
        return self.homeWithUID(ECALENDARTYPE, uid, create=create)


    @memoized('uid', '_addressbookHomes')
    def addressbookHomeWithUID(self, uid, create=False):
        return self.homeWithUID(EADDRESSBOOKTYPE, uid, create=create)


    @inlineCallbacks
    def homeWithUID(self, storeType, uid, create=False):
        if storeType == ECALENDARTYPE:
            homeTable = CALENDAR_HOME_TABLE
        elif storeType == EADDRESSBOOKTYPE:
            homeTable = ADDRESSBOOK_HOME_TABLE
        else:
            raise RuntimeError("Unknown home type.")

        data = yield self.execSQL(
            "select %(column_RESOURCE_ID)s from %(name)s"
            " where %(column_OWNER_UID)s = %%s" % homeTable,
            [uid]
        )
        if not data:
            if not create:
                returnValue(None)
            # Need to lock to prevent race condition
            # FIXME: this is an entire table lock - ideally we want a row lock
            # but the row does not exist yet. However, the "exclusive" mode
            # does allow concurrent reads so the only thing we block is other
            # attempts to provision a home, which is not too bad
            yield self.execSQL(
                "lock %(name)s in exclusive mode" % homeTable,
            )
            # Now test again
            data = yield self.execSQL(
                "select %(column_RESOURCE_ID)s from %(name)s"
                " where %(column_OWNER_UID)s = %%s" % homeTable,
                [uid]
            )
            if not data:
                yield self.execSQL(
                    "insert into %(name)s (%(column_OWNER_UID)s) values (%%s)" % homeTable,
                    [uid]
                )
                home = yield self.homeWithUID(storeType, uid)
                yield home.createdHome()
                returnValue(home)
        resid = data[0][0]
        if self._notifierFactory:
            notifier = self._notifierFactory.newNotifier(
                id=uid, prefix=NotifierPrefixes[storeType]
            )
        else:
            notifier = None
        homeObject = self._homeClass[storeType](self, uid, resid, notifier)
        yield homeObject._loadPropertyStore()
        returnValue(homeObject)


    @memoized('uid', '_notificationHomes')
    @inlineCallbacks
    def notificationsWithUID(self, uid):
        """
        Implement notificationsWithUID.
        """
        rows = yield self.execSQL(
            """
            select %(column_RESOURCE_ID)s from %(name)s where
            %(column_OWNER_UID)s = %%s
            """ % NOTIFICATION_HOME_TABLE, [uid]
        )
        if rows:
            resourceID = rows[0][0]
            created = False
        else:
            resourceID = str((yield self.execSQL(
                "insert into %(name)s (%(column_OWNER_UID)s) values (%%s) returning %(column_RESOURCE_ID)s" % NOTIFICATION_HOME_TABLE,
                [uid]
            ))[0][0])
            created = True
        collection = NotificationCollection(self, uid, resourceID)
        yield collection._loadPropertyStore()
        if created:
            collection._initSyncToken()
        returnValue(collection)


    def abort(self):
        if not self._completed:
            def reallyAbort():
                self._connection.rollback()
                self._connection.close()
            self._completed = True
            result = self._holder.submit(reallyAbort)
            self._holder.stop()
            return result
        else:
            raise AlreadyFinishedError()


    def commit(self):
        if not self._completed:
            self._completed = True
            def postCommit(ignored):
                for operation in self._postCommitOperations:
                    operation()
            def reallyCommit():
                self._connection.commit()
                self._connection.close()
            result = self._holder.submit(reallyCommit).addCallback(postCommit)
            self._holder.stop()
            return result
        else:
            raise AlreadyFinishedError()


    def postCommit(self, operation):
        """
        Run things after 'commit.'
        """
        self._postCommitOperations.append(operation)



class CommonHome(LoggingMixIn):

    _childClass = None
    _childTable = None
    _bindTable = None
    _revisionsTable = None
    _notificationRevisionsTable = NOTIFICATION_OBJECT_REVISIONS_TABLE

    def __init__(self, transaction, ownerUID, resourceID, notifier):
        self._txn = transaction
        self._ownerUID = ownerUID
        self._resourceID = resourceID
        self._shares = None
        self._children = {}
        self._sharedChildren = {}
        self._notifier = notifier

        # Needed for REVISION/BIND table join
        self._revisionBindJoinTable = {}
        for key, value in self._revisionsTable.iteritems():
            self._revisionBindJoinTable["REV:%s" % (key,)] = value 
        for key, value in self._bindTable.iteritems():
            self._revisionBindJoinTable["BIND:%s" % (key,)] = value 

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._resourceID)

    def uid(self):
        """
        Retrieve the unique identifier for this home.

        @return: a string.
        """
        return self._ownerUID


    def transaction(self):
        return self._txn


    def retrieveOldShares(self):
        return self._shares


    def name(self):
        """
        Implement L{IDataStoreResource.name} to return the uid.
        """
        return self.uid()


    @inlineCallbacks
    def children(self):
        """
        Retrieve children contained in this home.
        """
        x = []
        names = yield self.listChildren()
        for name in names:
            x.append((yield self.childWithName(name)))
        returnValue(x)


    def listChildren(self):
        """
        Retrieve the names of the children in this home.

        @return: an iterable of C{str}s.
        """
        return self._listChildren(owned=True)


    def listSharedChildren(self):
        """
        Retrieve the names of the children in this home.

        @return: an iterable of C{str}s.
        """
        return self._listChildren(owned=False)


    @inlineCallbacks
    def _listChildren(self, owned):
        """
        Retrieve the names of the children in this home.

        @return: an iterable of C{str}s.
        """
        # FIXME: not specified on the interface or exercised by the tests, but
        # required by clients of the implementation!
        if owned:
            rows = yield self._txn.execSQL("""
                select %(column_RESOURCE_NAME)s from %(name)s
                where
                  %(column_HOME_RESOURCE_ID)s = %%s and
                  %(column_BIND_MODE)s = %%s
                """ % self._bindTable,
                [self._resourceID, _BIND_MODE_OWN]
            )
        else:
            rows = yield self._txn.execSQL("""
                select %(column_RESOURCE_NAME)s from %(name)s
                where
                  %(column_HOME_RESOURCE_ID)s = %%s and
                  %(column_BIND_MODE)s != %%s and
                  %(column_RESOURCE_NAME)s is not null
                """ % self._bindTable,
                [self._resourceID, _BIND_MODE_OWN]
            )

        names = [row[0] for row in rows]
        returnValue(names)


    @memoized('name', '_children')
    def childWithName(self, name):
        """
        Retrieve the child with the given C{name} contained in this
        home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such child exists.
        """
        return self._childWithName(name, owned=True)


    @memoized('name', '_sharedChildren')
    def sharedChildWithName(self, name):
        """
        Retrieve the shared child with the given C{name} contained in this
        home. Return a child object with this home and the name.

        IMPORTANT: take care when using this. Shared calendars should normally
        be accessed through the owner home collection, not the sharee home collection.
        The only reason for access through sharee home is to do some housekeeping
        for maintaining the revisions database to show shared calendars appearing and
        disappearing in the sharee home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such child
            exists.
        """
        return self._childWithName(name, owned=False)


    @inlineCallbacks
    def _childWithName(self, name, owned):
        """
        Retrieve the child with the given C{name} contained in this
        home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such child
            exists.
        """
        
        if owned:
            data = yield self._txn.execSQL("""
                select %(column_RESOURCE_ID)s from %(name)s
                where
                  %(column_RESOURCE_NAME)s = %%s and
                  %(column_HOME_RESOURCE_ID)s = %%s and
                  %(column_BIND_MODE)s = %%s
                """ % self._bindTable,
                [
                    name,
                    self._resourceID,
                    _BIND_MODE_OWN
                ]
            )
        else:
            data = yield self._txn.execSQL("""
                select %(column_RESOURCE_ID)s from %(name)s
                where
                  %(column_RESOURCE_NAME)s = %%s and
                  %(column_HOME_RESOURCE_ID)s = %%s and
                  %(column_BIND_MODE)s != %%s
                """ % self._bindTable,
                [
                    name,
                    self._resourceID,
                    _BIND_MODE_OWN
                ]
            )

        if not data:
            returnValue(None)
        resourceID = data[0][0]
        if self._notifier:
            childID = "%s/%s" % (self.uid(), name)
            notifier = self._notifier.clone(label="collection", id=childID)
        else:
            notifier = None
        child = self._childClass(self, name, resourceID, notifier)
        yield child._loadPropertyStore()
        returnValue(child)


    @inlineCallbacks
    def createChildWithName(self, name):
        if name.startswith("."):
            raise HomeChildNameNotAllowedError(name)

        rows = yield self._txn.execSQL(
            "select %(column_RESOURCE_NAME)s from %(name)s where "
            "%(column_RESOURCE_NAME)s = %%s AND "
            "%(column_HOME_RESOURCE_ID)s = %%s" % self._bindTable,
            [name, self._resourceID]
        )
        if rows:
            raise HomeChildNameAlreadyExistsError(name)

        rows = yield self._txn.execSQL("select nextval('RESOURCE_ID_SEQ')")
        resourceID = rows[0][0]
        yield self._txn.execSQL(
            "insert into %(name)s (%(column_RESOURCE_ID)s) values "
            "(%%s)" % self._childTable,
            [resourceID])

        yield self._txn.execSQL("""
            insert into %(name)s (
                %(column_HOME_RESOURCE_ID)s,
                %(column_RESOURCE_ID)s, %(column_RESOURCE_NAME)s, %(column_BIND_MODE)s,
                %(column_SEEN_BY_OWNER)s, %(column_SEEN_BY_SHAREE)s, %(column_BIND_STATUS)s) values (
            %%s, %%s, %%s, %%s, %%s, %%s, %%s)
            """ % self._bindTable,
            [self._resourceID, resourceID, name, _BIND_MODE_OWN, True, True,
             _BIND_STATUS_ACCEPTED]
        )

        newChild = yield self.childWithName(name)
        newChild.properties()[
            PropertyName.fromElement(ResourceType)
        ] = newChild.resourceType()
        newChild._initSyncToken()
        self.createdChild(newChild)

        self.notifyChanged()


    def createdChild(self, child):
        pass


    @inlineCallbacks
    def removeChildWithName(self, name):
        child = yield self.childWithName(name)
        if not child:
            raise NoSuchHomeChildError()
        yield child._deletedSyncToken()

        yield self._txn.execSQL(
            "delete from %(name)s where %(column_RESOURCE_ID)s = %%s" % self._childTable,
            [child._resourceID]
        )
        self._children.pop(name, None)
        if self._txn._cursor.rowcount == 0:
            raise NoSuchHomeChildError()

        child.notifyChanged()


    @inlineCallbacks
    def syncToken(self):
        revision = (yield self._txn.execSQL(
            """
            select max(%(REV:column_REVISION)s) from %(REV:name)s
            where %(REV:column_RESOURCE_ID)s in (
              select %(BIND:column_RESOURCE_ID)s from %(BIND:name)s 
              where %(BIND:column_HOME_RESOURCE_ID)s = %%s
            ) or (
              %(REV:column_HOME_RESOURCE_ID)s = %%s and
              %(REV:column_RESOURCE_ID)s is null
            )
            """ % self._revisionBindJoinTable,
            [self._resourceID, self._resourceID,]
        ))[0][0]
        returnValue("%s#%s" % (self._resourceID, revision))


    @inlineCallbacks
    def resourceNamesSinceToken(self, token, depth):

        results = [
            (
                path if path else (collection if collection else ""),
                name if name else "",
                wasdeleted
            )
            for path, collection, name, wasdeleted in
            (yield self._txn.execSQL("""
                select %(BIND:column_RESOURCE_NAME)s, %(REV:column_COLLECTION_NAME)s, %(REV:column_RESOURCE_NAME)s, %(REV:column_DELETED)s
                from %(REV:name)s
                left outer join %(BIND:name)s on (
                  %(BIND:name)s.%(BIND:column_HOME_RESOURCE_ID)s = %%s and
                  %(REV:name)s.%(REV:column_RESOURCE_ID)s = %(BIND:name)s.%(BIND:column_RESOURCE_ID)s
                )
                where 
                  %(REV:column_REVISION)s > %%s and 
                  %(REV:name)s.%(REV:column_HOME_RESOURCE_ID)s = %%s
                """ % self._revisionBindJoinTable,
                [self._resourceID, token, self._resourceID],
            ))
        ]
        
        deleted = []
        deleted_collections = set()
        changed_collections = set()
        for path, name, wasdeleted in results:
            if wasdeleted:
                if token:
                    deleted.append("%s/%s" % (path, name,))
                if not name:
                    deleted_collections.add(path)
        
        changed = []
        for path, name, wasdeleted in results:
            if path not in deleted_collections:
                changed.append("%s/%s" % (path, name,))
                if not name:
                    changed_collections.add(path)
        
        # Now deal with shared collections
        shares = yield self.listSharedChildren()
        for sharename in shares:
            sharetoken = 0 if sharename in changed_collections else token
            shareID = (yield self._txn.execSQL("""
                select %(column_RESOURCE_ID)s from %(name)s
                where
                  %(column_RESOURCE_NAME)s = %%s and
                  %(column_HOME_RESOURCE_ID)s = %%s and
                  %(column_BIND_MODE)s != %%s
                """ % self._bindTable,
                [
                    sharename,
                    self._resourceID,
                    _BIND_MODE_OWN
                ]
            ))[0][0]
            results = [
                (
                    sharename,
                    name if name else "",
                    wasdeleted
                )
                for name, wasdeleted in
                (yield self._txn.execSQL("""
                    select %(column_RESOURCE_NAME)s, %(column_DELETED)s
                    from %(name)s
                    where %(column_REVISION)s > %%s and %(column_RESOURCE_ID)s = %%s
                    """ % self._revisionsTable,
                    [sharetoken, shareID],
                )) if name
            ]

            for path, name, wasdeleted in results:
                if wasdeleted:
                    if sharetoken:
                        deleted.append("%s/%s" % (path, name,))
            
            for path, name, wasdeleted in results:
                changed.append("%s/%s" % (path, name,))
        
        
        changed.sort()
        deleted.sort()
        returnValue((changed, deleted))


    @inlineCallbacks
    def _loadPropertyStore(self):
        props = yield PropertyStore.load(
            self.uid(),
            self._txn,
            self._resourceID
        )
        self._propertyStore = props


    def properties(self):
        return self._propertyStore

    
    # IDataStoreResource
    def contentType(self):
        """
        The content type of objects
        """
        return None


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None


    def notifierID(self, label="default"):
        if self._notifier:
            return self._notifier.getID(label)
        else:
            return None

    def notifyChanged(self):
        """
        Trigger a notification of a change
        """
        if self._notifier:
            self._txn.postCommit(self._notifier.notify)
        

class CommonHomeChild(LoggingMixIn, FancyEqMixin):
    """
    Common ancestor class of AddressBooks and Calendars.
    """

    compareAttributes = '_name _home _resourceID'.split()

    _objectResourceClass = None
    _bindTable = None
    _homeChildTable = None
    _revisionsTable = None
    _objectTable = None

    def __init__(self, home, name, resourceID, notifier):
        self._home = home
        self._name = name
        self._resourceID = resourceID
        self._objects = {}
        self._notifier = notifier

        self._index = None  # Derived classes need to set this
        self._invites = None # Derived classes need to set this


    @property
    def _txn(self):
        return self._home._txn


    def resourceType(self):
        return NotImplementedError


    def retrieveOldIndex(self):
        return self._index


    def retrieveOldInvites(self):
        return self._invites


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._resourceID)


    def name(self):
        return self._name


    @inlineCallbacks
    def rename(self, name):
        oldName = self._name
        yield self._txn.execSQL(
            "update %(name)s set %(column_RESOURCE_NAME)s = %%s "
            "where %(column_RESOURCE_ID)s = %%s AND "
            "%(column_HOME_RESOURCE_ID)s = %%s" % self._bindTable,
            [name, self._resourceID, self._home._resourceID]
        )
        self._name = name
        # update memos
        del self._home._children[oldName]
        self._home._children[name] = self
        yield self._renameSyncToken()

        self.notifyChanged()


    def ownerHome(self):
        return self._home


    def setSharingUID(self, uid):
        self.properties()._setPerUserUID(uid)


    @inlineCallbacks
    def objectResources(self):
        x = []
        r = x.append
        for name in (yield self.listObjectResources()):
            r((yield self.objectResourceWithName(name)))
        returnValue(x)


    @inlineCallbacks
    def listObjectResources(self):
        rows = yield self._txn.execSQL(
            "select %(column_RESOURCE_NAME)s from %(name)s "
            "where %(column_PARENT_RESOURCE_ID)s = %%s" % self._objectTable,
            [self._resourceID])
        returnValue(sorted([row[0] for row in rows]))


    @memoized('name', '_objects')
    @inlineCallbacks
    def objectResourceWithName(self, name):
        rows = yield self._txn.execSQL(
            "select %(column_RESOURCE_ID)s, %(column_UID)s from %(name)s "
            "where %(column_RESOURCE_NAME)s = %%s and %(column_PARENT_RESOURCE_ID)s = %%s" % self._objectTable,
            [name, self._resourceID]
        )
        if not rows:
            returnValue(None)
        [resid, uid] = rows[0]
        returnValue((yield self._makeObjectResource(name, resid, uid)))


    @inlineCallbacks
    def _makeObjectResource(self, name, resid, uid):
        """
        Create an instance of C{self._objectResourceClass}.
        """
        objectResource = yield self._objectResourceClass(
            name, self, resid, uid
        )
        yield objectResource._loadPropertyStore()
        returnValue(objectResource)


    @memoized('uid', '_objects')
    @inlineCallbacks
    def objectResourceWithUID(self, uid):
        rows = yield self._txn.execSQL(
            "select %(column_RESOURCE_ID)s, %(column_RESOURCE_NAME)s "
            "from %(name)s where %(column_UID)s = %%s "
            "and %(column_PARENT_RESOURCE_ID)s = %%s" % self._objectTable,
            [uid, self._resourceID]
        )
        if not rows:
            returnValue(None)
        resid = rows[0][0]
        name = rows[0][1]
        returnValue((yield self._makeObjectResource(name, resid, uid)))


    @inlineCallbacks
    def createObjectResourceWithName(self, name, component):
        if name.startswith("."):
            raise ObjectResourceNameNotAllowedError(name)

        rows = yield self._txn.execSQL(
            "select %(column_RESOURCE_ID)s from %(name)s "
            "where %(column_RESOURCE_NAME)s = %%s "
            "and %(column_PARENT_RESOURCE_ID)s = %%s" % self._objectTable,
            [name, self._resourceID]
        )
        if rows:
            raise ObjectResourceNameAlreadyExistsError()

        objectResource = (
            yield self._makeObjectResource(name, None, component.resourceUID())
        )
        yield objectResource.setComponent(component, inserting=True)

        # Note: setComponent triggers a notification, so we don't need to
        # call notify( ) here like we do for object removal.


    @inlineCallbacks
    def removeObjectResourceWithName(self, name):
        rows = yield self._txn.execSQL(
            "delete from %(name)s "
            "where %(column_RESOURCE_NAME)s = %%s and %(column_PARENT_RESOURCE_ID)s = %%s "
            "returning %(column_UID)s" % self._objectTable,
            [name, self._resourceID],
            raiseOnZeroRowCount=lambda:NoSuchObjectResourceError()
        )
        uid = rows[0][0]
        self._objects.pop(name, None)
        self._objects.pop(uid, None)
        yield self._deleteRevision(name)

        self.notifyChanged()


    @inlineCallbacks
    def removeObjectResourceWithUID(self, uid):
        rows = yield self._txn.execSQL(
            "delete from %(name)s "
            "where %(column_UID)s = %%s and %(column_PARENT_RESOURCE_ID)s = %%s "
            "returning %(column_RESOURCE_NAME)s" % self._objectTable,
            [uid, self._resourceID],
            raiseOnZeroRowCount=lambda:NoSuchObjectResourceError()
        )
        name = rows[0][0]
        self._objects.pop(name, None)
        self._objects.pop(uid, None)
        yield self._deleteRevision(name)

        self.notifyChanged()


    @inlineCallbacks
    def syncToken(self):
        revision = (yield self._txn.execSQL(
            """
            select max(%(column_REVISION)s) from %(name)s
            where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is not null
            """ % self._revisionsTable,
            [self._resourceID,]
        ))[0][0]
        if revision is None:
            revision = (yield self._txn.execSQL(
                """
                select %(column_REVISION)s from %(name)s
                where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is null
                """ % self._revisionsTable,
                [self._resourceID,]
            ))[0][0]
        returnValue(("%s#%s" % (self._resourceID, revision,)))


    def objectResourcesSinceToken(self, token):
        raise NotImplementedError()


    @inlineCallbacks
    def resourceNamesSinceToken(self, token):
        results = [
            (name if name else "", deleted)
            for name, deleted in
            (yield self._txn.execSQL("""
                select %(column_RESOURCE_NAME)s, %(column_DELETED)s from %(name)s
                where %(column_REVISION)s > %%s and %(column_RESOURCE_ID)s = %%s
                """ % self._revisionsTable,
                [token, self._resourceID],
            ))
        ]
        results.sort(key=lambda x:x[1])
        
        changed = []
        deleted = []
        for name, wasdeleted in results:
            if name:
                if wasdeleted:
                    if token:
                        deleted.append(name)
                else:
                    changed.append(name)
        
        returnValue((changed, deleted))


    @inlineCallbacks
    def _initSyncToken(self):

        # Remove any deleted revision entry that uses the same name
        yield self._txn.execSQL("""
            delete from %(name)s
            where %(column_HOME_RESOURCE_ID)s = %%s and %(column_COLLECTION_NAME)s = %%s
            """ % self._revisionsTable,
            [self._home._resourceID, self._name]
        )

        # Insert new entry
        yield self._txn.execSQL("""
            insert into %(name)s
            (%(column_HOME_RESOURCE_ID)s, %(column_RESOURCE_ID)s, %(column_COLLECTION_NAME)s, %(column_RESOURCE_NAME)s, %(column_REVISION)s, %(column_DELETED)s)
            values (%%s, %%s, %%s, null, nextval('%(sequence)s'), FALSE)
            """ % self._revisionsTable,
            [self._home._resourceID, self._resourceID, self._name]
        )


    @inlineCallbacks
    def _updateSyncToken(self):

        yield self._txn.execSQL("""
            update %(name)s
            set (%(column_REVISION)s) = (nextval('%(sequence)s'))
            where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is null
            """ % self._revisionsTable,
            [self._resourceID,]
        )


    @inlineCallbacks
    def _renameSyncToken(self):

        yield self._txn.execSQL("""
            update %(name)s
            set (%(column_REVISION)s, %(column_COLLECTION_NAME)s) = (nextval('%(sequence)s'), %%s)
            where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is null
            """ % self._revisionsTable,
            [self._name, self._resourceID,]
        )


    @inlineCallbacks
    def _deletedSyncToken(self):

        # Remove all child entries
        yield self._txn.execSQL("""
            delete from %(name)s
            where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_ID)s = %%s and %(column_COLLECTION_NAME)s is null
            """ % self._revisionsTable,
            [self._home._resourceID, self._resourceID,]
        )

        # Then adjust collection entry to deleted state (do this for all entries with this collection's
        # resource-id so that we deal with direct shares which are not normally removed thorugh an unshare
        yield self._txn.execSQL("""
            update %(name)s
            set (%(column_RESOURCE_ID)s, %(column_REVISION)s, %(column_DELETED)s)
             = (null, nextval('%(sequence)s'), TRUE)
            where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is null
            """ % self._revisionsTable,
            [self._resourceID,]
        )


    def _insertRevision(self, name):
        return self._changeRevision("insert", name)

    def _updateRevision(self, name):
        return self._changeRevision("update", name)

    def _deleteRevision(self, name):
        return self._changeRevision("delete", name)


    @inlineCallbacks
    def _changeRevision(self, action, name):

        nextrevision = yield self._txn.execSQL("""
            select nextval('%(sequence)s')
            """ % self._revisionsTable
        )

        if action == "delete":
            yield self._txn.execSQL("""
                update %(name)s
                set (%(column_REVISION)s, %(column_DELETED)s) = (%%s, TRUE)
                where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                """ % self._revisionsTable,
                [nextrevision, self._resourceID, name]
            )
        elif action == "update":
            yield self._txn.execSQL("""
                update %(name)s
                set (%(column_REVISION)s) = (%%s)
                where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                """ % self._revisionsTable,
                [nextrevision, self._resourceID, name]
            )
        elif action == "insert":
            # Note that an "insert" may happen for a resource that previously existed and then
            # was deleted. In that case an entry in the REVISIONS table still exists so we have to
            # detect that and do db INSERT or UPDATE as appropriate

            found = bool( (yield self._txn.execSQL("""
                select %(column_RESOURCE_ID)s from %(name)s
                where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                """ % self._revisionsTable,
                [self._resourceID, name, ]
            )) )
            if found:
                yield self._txn.execSQL("""
                    update %(name)s
                    set (%(column_REVISION)s, %(column_DELETED)s) = (%%s, FALSE)
                    where %(column_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                    """ % self._revisionsTable,
                    [nextrevision, self._resourceID, name]
                )
            else:
                yield self._txn.execSQL("""
                    insert into %(name)s
                    (%(column_HOME_RESOURCE_ID)s, %(column_RESOURCE_ID)s, %(column_RESOURCE_NAME)s, %(column_REVISION)s, %(column_DELETED)s)
                    values (%%s, %%s, %%s, %%s, FALSE)
                    """ % self._revisionsTable,
                    [self._home._resourceID, self._resourceID, name, nextrevision]
                )


    @inlineCallbacks
    def _loadPropertyStore(self):
        props = yield PropertyStore.load(
            self.ownerHome().uid(),
            self._txn,
            self._resourceID
        )
        self.initPropertyStore(props)
        self._properties = props


    def properties(self):
        return self._properties


    def initPropertyStore(self, props):
        """
        A hook for subclasses to override in order to set up their property
        store after it's been created.

        @param props: the L{PropertyStore} from C{properties()}.
        """


    def _doValidate(self, component):
        raise NotImplementedError


    # IDataStoreResource
    def contentType(self):
        raise NotImplementedError()


    def md5(self):
        return None


    def size(self):
        return 0


    @inlineCallbacks
    def created(self):
        created = (yield self._txn.execSQL(
            "select %(column_CREATED)s from %(name)s "
            "where %(column_RESOURCE_ID)s = %%s" % self._homeChildTable,
            [self._resourceID]
        ))[0][0]
        utc = datetime.datetime.strptime(created, "%Y-%m-%d %H:%M:%S.%f")
        returnValue(datetimeMktime(utc))


    @inlineCallbacks
    def modified(self):
        modified = (yield self._txn.execSQL(
            "select %(column_MODIFIED)s from %(name)s "
            "where %(column_RESOURCE_ID)s = %%s" % self._homeChildTable,
            [self._resourceID]
        ))[0][0]
        utc = datetime.datetime.strptime(modified, "%Y-%m-%d %H:%M:%S.%f")
        returnValue(datetimeMktime(utc))


    def notifierID(self, label="default"):
        if self._notifier:
            return self._notifier.getID(label)
        else:
            return None


    def notifyChanged(self):
        """
        Trigger a notification of a change
        """
        if self._notifier:
            self._txn.postCommit(self._notifier.notify)



class CommonObjectResource(LoggingMixIn, FancyEqMixin):
    """
    @ivar _path: The path of the file on disk

    @type _path: L{FilePath}
    """

    compareAttributes = '_name _parentCollection'.split()

    _objectTable = None

    def __init__(self, name, parent, resid, uid):
        self._name = name
        self._parentCollection = parent
        self._resourceID = resid
        self._objectText = None
        self._uid = uid


    @inlineCallbacks
    def _loadPropertyStore(self):
        props = yield PropertyStore.load(
            self.uid(),
            self._txn,
            self._resourceID
        )
        self.initPropertyStore(props)
        self._propertyStore = props


    def properties(self):
        return self._propertyStore


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._resourceID)


    @property
    def _txn(self):
        return self._parentCollection._txn


    def setComponent(self, component, inserting=False):
        raise NotImplementedError


    def component(self):
        raise NotImplementedError


    @inlineCallbacks
    def componentType(self):
        returnValue((yield self.component()).mainType())


    def uid(self):
        return self._uid


    def name(self):
        return self._name


    def initPropertyStore(self, props):
        """
        A hook for subclasses to override in order to set up their property
        store after it's been created.

        @param props: the L{PropertyStore} from C{properties()}.
        """


    # IDataStoreResource
    def contentType(self):
        raise NotImplementedError()


    def md5(self):
        return None


    @inlineCallbacks
    def size(self):
        size = (yield self._txn.execSQL(
            "select character_length(%(column_TEXT)s) from %(name)s "
            "where %(column_RESOURCE_ID)s = %%s" % self._objectTable,
            [self._resourceID]
        ))[0][0]
        returnValue(size)


    @inlineCallbacks
    def created(self):
        created = (yield self._txn.execSQL(
            "select %(column_CREATED)s from %(name)s "
            "where %(column_RESOURCE_ID)s = %%s" % self._objectTable,
            [self._resourceID]
        ))[0][0]
        utc = datetime.datetime.strptime(created, "%Y-%m-%d %H:%M:%S.%f")
        returnValue(datetimeMktime(utc))


    @inlineCallbacks
    def modified(self):
        modified = (yield self._txn.execSQL(
            "select %(column_MODIFIED)s from %(name)s "
            "where %(column_RESOURCE_ID)s = %%s" % self._objectTable,
            [self._resourceID]
        ))[0][0]
        utc = datetime.datetime.strptime(modified, "%Y-%m-%d %H:%M:%S.%f")
        returnValue(datetimeMktime(utc))


    @inlineCallbacks
    def text(self):
        if self._objectText is None:
            text = (yield self._txn.execSQL(
                "select %(column_TEXT)s from %(name)s where "
                "%(column_RESOURCE_ID)s = %%s" % self._objectTable,
                [self._resourceID]
            ))[0][0]
            self._objectText = text
            returnValue(text)
        else:
            returnValue(self._objectText)



class NotificationCollection(LoggingMixIn, FancyEqMixin):

    implements(INotificationCollection)

    compareAttributes = '_uid _resourceID'.split()

    _objectResourceClass = None
    _revisionsTable = NOTIFICATION_OBJECT_REVISIONS_TABLE

    def __init__(self, txn, uid, resourceID):

        self._txn = txn
        self._uid = uid
        self._resourceID = resourceID
        self._notifications = {}


    @inlineCallbacks
    def _loadPropertyStore(self):
        self._propertyStore = yield PropertyStore.load(
            self._uid,
            self._txn,
            self._resourceID
        )


    def resourceType(self):
        return ResourceType.notification #@UndefinedVariable

    def retrieveOldIndex(self):
        return PostgresLegacyNotificationsEmulator(self)

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._resourceID)

    def name(self):
        return 'notification'

    def uid(self):
        return self._uid


    @inlineCallbacks
    def notificationObjects(self):
        L = []
        for name in (yield self.listNotificationObjects()):
            L.append((yield self.notificationObjectWithName(name)))
        returnValue(L)


    @inlineCallbacks
    def listNotificationObjects(self):
        rows = yield self._txn.execSQL(
            "select (NOTIFICATION_UID) from NOTIFICATION "
            "where NOTIFICATION_HOME_RESOURCE_ID = %s",
            [self._resourceID])
        returnValue(sorted(["%s.xml" % row[0] for row in rows]))


    def _nameToUID(self, name):
        """
        Based on the file-backed implementation, the 'name' is just uid +
        ".xml".
        """
        return name.rsplit(".", 1)[0]


    def notificationObjectWithName(self, name):
        return self.notificationObjectWithUID(self._nameToUID(name))


    @memoized('uid', '_notifications')
    @inlineCallbacks
    def notificationObjectWithUID(self, uid):
        rows = (yield self._txn.execSQL(
            "select RESOURCE_ID from NOTIFICATION "
            "where NOTIFICATION_UID = %s and NOTIFICATION_HOME_RESOURCE_ID = %s",
            [uid, self._resourceID]))
        if rows:
            resourceID = rows[0][0]
            no = NotificationObject(self, uid, resourceID)
            yield no._loadPropertyStore()
            returnValue(no)
        else:
            returnValue(None)


    @inlineCallbacks
    def writeNotificationObject(self, uid, xmltype, xmldata):

        inserting = False
        notificationObject = yield self.notificationObjectWithUID(uid)
        if notificationObject is None:
            notificationObject = NotificationObject(self, uid, None)
            inserting = True
        yield notificationObject.setData(uid, xmltype, xmldata, inserting=inserting)
        if inserting:
            yield self._insertRevision("%s.xml" % (uid,))
        else:
            yield self._updateRevision("%s.xml" % (uid,))


    def removeNotificationObjectWithName(self, name):
        return self.removeNotificationObjectWithUID(self._nameToUID(name))


    @inlineCallbacks
    def removeNotificationObjectWithUID(self, uid):
        yield self._txn.execSQL(
            "delete from NOTIFICATION "
            "where NOTIFICATION_UID = %s and NOTIFICATION_HOME_RESOURCE_ID = %s",
            [uid, self._resourceID]
        )
        self._notifications.pop(uid, None)
        yield self._deleteRevision("%s.xml" % (uid,))


    def _initSyncToken(self):
        return self._txn.execSQL("""
            insert into %(name)s
            (%(column_HOME_RESOURCE_ID)s, %(column_RESOURCE_NAME)s, %(column_REVISION)s, %(column_DELETED)s)
            values (%%s, null, nextval('%(sequence)s'), FALSE)
            """ % self._revisionsTable,
            [self._resourceID,]
        )


    @inlineCallbacks
    def syncToken(self):
        revision = (yield self._txn.execSQL(
            """
            select max(%(column_REVISION)s) from %(name)s
            where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is not null
            """ % self._revisionsTable,
            [self._resourceID,]
        ))[0][0]

        if revision is None:
            revision = (yield self._txn.execSQL(
                """
                select %(column_REVISION)s from %(name)s
                where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is null
                """ % self._revisionsTable,
                [self._resourceID,]
            ))[0][0]
        returnValue("%s#%s" % (self._resourceID, revision,))


    def objectResourcesSinceToken(self, token):
        raise NotImplementedError()


    @inlineCallbacks
    def resourceNamesSinceToken(self, token):
        results = [
            (name if name else "", deleted)
            for name, deleted in
            (yield self._txn.execSQL("""
                select %(column_RESOURCE_NAME)s, %(column_DELETED)s from %(name)s
                where %(column_REVISION)s > %%s and %(column_HOME_RESOURCE_ID)s = %%s
                """ % self._revisionsTable,
                [token, self._resourceID],
            ))
        ]
        results.sort(key=lambda x:x[1])

        changed = []
        deleted = []
        for name, wasdeleted in results:
            if name:
                if wasdeleted:
                    if token:
                        deleted.append(name)
                else:
                    changed.append(name)

        returnValue((changed, deleted))


    def _updateSyncToken(self):
        return self._txn.execSQL("""
            update %(name)s
            set (%(column_REVISION)s) = (nextval('%(sequence)s'))
            where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s is null
            """ % self._revisionsTable,
            [self._resourceID,]
        )


    def _insertRevision(self, name):
        return self._changeRevision("insert", name)


    def _updateRevision(self, name):
        return self._changeRevision("update", name)


    def _deleteRevision(self, name):
        return self._changeRevision("delete", name)


    @inlineCallbacks
    def _changeRevision(self, action, name):

        nextrevision = yield self._txn.execSQL("""
            select nextval('%(sequence)s')
            """ % self._revisionsTable
        )

        if action == "delete":
            yield self._txn.execSQL("""
                update %(name)s
                set (%(column_REVISION)s, %(column_DELETED)s) = (%%s, TRUE)
                where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                """ % self._revisionsTable,
                [nextrevision, self._resourceID, name]
            )
        elif action == "update":
            yield self._txn.execSQL("""
                update %(name)s
                set (%(column_REVISION)s) = (%%s)
                where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                """ % self._revisionsTable,
                [nextrevision, self._resourceID, name]
            )
        elif action == "insert":
            # Note that an "insert" may happen for a resource that previously existed and then
            # was deleted. In that case an entry in the REVISIONS table still exists so we have to
            # detect that and do db INSERT or UPDATE as appropriate

            found = bool( (yield self._txn.execSQL("""
                select %(column_HOME_RESOURCE_ID)s from %(name)s
                where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                """ % self._revisionsTable,
                [self._resourceID, name, ]
            )))
            if found:
                yield self._txn.execSQL("""
                    update %(name)s
                    set (%(column_REVISION)s, %(column_DELETED)s) = (%%s, FALSE)
                    where %(column_HOME_RESOURCE_ID)s = %%s and %(column_RESOURCE_NAME)s = %%s
                    """ % self._revisionsTable,
                    [nextrevision, self._resourceID, name]
                )
            else:
                yield self._txn.execSQL("""
                    insert into %(name)s
                    (%(column_HOME_RESOURCE_ID)s, %(column_RESOURCE_NAME)s, %(column_REVISION)s, %(column_DELETED)s)
                    values (%%s, %%s, %%s, FALSE)
                    """ % self._revisionsTable,
                    [self._resourceID, name, nextrevision]
                )


    def properties(self):
        return self._propertyStore



class NotificationObject(LoggingMixIn, FancyEqMixin):

    implements(INotificationObject)

    compareAttributes = '_resourceID _home'.split()

    def __init__(self, home, uid, resourceID):
        self._home = home
        self._uid = uid
        self._resourceID = resourceID


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._resourceID)


    @property
    def _txn(self):
        return self._home._txn


    def notificationCollection(self):
        return self._home


    def uid(self):
        return self._uid


    def name(self):
        return self.uid() + ".xml"


    @inlineCallbacks
    def setData(self, uid, xmltype, xmldata, inserting=False):

        xmltypeString = xmltype.toxml()
        if inserting:
            rows = yield self._txn.execSQL(
                "insert into NOTIFICATION (NOTIFICATION_HOME_RESOURCE_ID, NOTIFICATION_UID, XML_TYPE, XML_DATA) "
                "values (%s, %s, %s, %s) returning RESOURCE_ID",
                [self._home._resourceID, uid, xmltypeString, xmldata]
            )
            self._resourceID = rows[0][0]
            yield self._loadPropertyStore()
        else:
            yield self._txn.execSQL(
                "update NOTIFICATION set XML_TYPE = %s, XML_DATA = %s "
                "where NOTIFICATION_HOME_RESOURCE_ID = %s and NOTIFICATION_UID = %s",
                [xmltypeString, xmldata, self._home._resourceID, uid])

        self.properties()[PropertyName.fromElement(NotificationType)] = NotificationType(xmltype)


    @inlineCallbacks
    def _fieldQuery(self, field):
        data = yield self._txn.execSQL(
            "select " + field + " from NOTIFICATION "
            "where RESOURCE_ID = %s",
            [self._resourceID]
        )
        returnValue(data[0][0])


    def xmldata(self):
        return self._fieldQuery("XML_DATA")


    def properties(self):
        return self._propertyStore


    @inlineCallbacks
    def _loadPropertyStore(self):
        self._propertyStore = yield PropertyStore.load(
            self._home.uid(),
            self._txn,
            self._resourceID
        )
        self.initPropertyStore(self._propertyStore)


    def initPropertyStore(self, props):
        # Setup peruser special properties
        props.setSpecialProperties(
            (
            ),
            (
                PropertyName.fromElement(NotificationType),
            ),
        )


    def contentType(self):
        """
        The content type of NotificationObjects is text/xml.
        """
        return MimeType.fromString("text/xml")


    @inlineCallbacks
    def md5(self):
        returnValue(hashlib.md5((yield self.xmldata())).hexdigest())


    @inlineCallbacks
    def size(self):
        size = (yield self._txn.execSQL(
            "select character_length(XML_DATA) from NOTIFICATION "
            "where RESOURCE_ID = %s",
            [self._resourceID]
        ))[0][0]
        returnValue(size)


    @inlineCallbacks
    def created(self):
        created = (yield self._txn.execSQL(
            "select CREATED from NOTIFICATION "
            "where RESOURCE_ID = %s",
            [self._resourceID]
        ))[0][0]
        utc = datetime.datetime.strptime(created, "%Y-%m-%d %H:%M:%S.%f")
        returnValue(datetimeMktime(utc))


    @inlineCallbacks
    def modified(self):
        modified = (yield self._txn.execSQL(
            "select MODIFIED from NOTIFICATION "
            "where RESOURCE_ID = %s", [self._resourceID]
        ))[0][0]
        utc = datetime.datetime.strptime(modified, "%Y-%m-%d %H:%M:%S.%f")
        returnValue(datetimeMktime(utc))



