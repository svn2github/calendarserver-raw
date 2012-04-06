# -*- test-case-name: txdav.caldav.datastore.test.test_sql,txdav.carddav.datastore.test.test_sql -*-
##
# Copyright (c) 2010-2012 Apple Inc. All rights reserved.
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


from zope.interface import implements, directlyProvides

from twext.python.log import Logger, LoggingMixIn
from txdav.xml.rfc2518 import ResourceType
from txdav.xml.parser import WebDAVDocument
from twext.web2.http_headers import MimeType

from twisted.python import hashlib
from twisted.python.modules import getModule
from twisted.python.util import FancyEqMixin
from twisted.python.failure import Failure

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, succeed

from twisted.application.service import Service

from twext.internet.decorate import memoizedKey

from txdav.common.datastore.sql_legacy import PostgresLegacyNotificationsEmulator
from txdav.caldav.icalendarstore import ICalendarTransaction, ICalendarStore

from txdav.carddav.iaddressbookstore import IAddressBookTransaction

from txdav.common.datastore.sql_tables import schema
from txdav.common.datastore.sql_tables import _BIND_MODE_OWN, \
    _BIND_STATUS_ACCEPTED, NOTIFICATION_OBJECT_REVISIONS_TABLE
from txdav.common.icommondatastore import HomeChildNameNotAllowedError, \
    HomeChildNameAlreadyExistsError, NoSuchHomeChildError, \
    ObjectResourceNameNotAllowedError, ObjectResourceNameAlreadyExistsError, \
    NoSuchObjectResourceError, AllRetriesFailed, InvalidSubscriptionValues
from txdav.common.inotifications import INotificationCollection, \
    INotificationObject

from twext.python.clsprop import classproperty
from twext.enterprise.ienterprise import AlreadyFinishedError
from twext.enterprise.dal.parseschema import significant
from twext.enterprise.dal.syntax import Delete, utcNowSQL, Union
from twext.enterprise.dal.syntax import Insert
from twext.enterprise.dal.syntax import Len
from twext.enterprise.dal.syntax import Max
from twext.enterprise.dal.syntax import Parameter
from twext.enterprise.dal.syntax import SavepointAction
from twext.enterprise.dal.syntax import Select
from twext.enterprise.dal.syntax import Update

from txdav.base.propertystore.base import PropertyName
from txdav.base.propertystore.none import PropertyStore as NonePropertyStore
from txdav.base.propertystore.sql import PropertyStore

from txdav.common.icommondatastore import ConcurrentModification
from twistedcaldav.customxml import NotificationType
from twistedcaldav.dateops import datetimeMktime, parseSQLTimestamp,\
    pyCalendarTodatetime

from cStringIO import StringIO
from sqlparse import parse
import collections
import sys
import time

current_sql_schema = getModule(__name__).filePath.sibling("sql_schema").child("current.sql").getContent()

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
    """
    Shared logic for SQL-based data stores, between calendar and addressbook
    storage.

    @ivar sqlTxnFactory: A 0-arg factory callable that produces an
        L{IAsyncTransaction}.

    @ivar notifierFactory: a L{twistedcaldav.notify.NotifierFactory} (or
        similar) that produces new notifiers for homes and collections.

    @ivar attachmentsPath: a L{FilePath} indicating a directory where
        attachments may be stored.

    @ivar enableCalendars: a boolean, C{True} if this data store should provide
        L{ICalendarStore}, C{False} if not.

    @ivar enableAddressBooks: a boolean, C{True} if this data store should
        provide L{IAddressbookStore}, C{False} if not.

    @ivar label: A string, used for tagging debug messages in the case where
        there is more than one store.  (Useful mostly for unit tests.)

    @ivar quota: the amount of space granted to each calendar home (in bytes)
        for storing attachments, or C{None} if quota should not be enforced.

    @type quota: C{int} or C{NoneType}
    """

    implements(ICalendarStore)

    def __init__(self, sqlTxnFactory, notifierFactory, attachmentsPath,
                 enableCalendars=True, enableAddressBooks=True,
                 label="unlabeled", quota=(2 ** 20),
                 logLabels=False, logStats=False, logSQL=False,
                 logTransactionWaits=0, timeoutTransactions=0):
        assert enableCalendars or enableAddressBooks

        self.sqlTxnFactory = sqlTxnFactory
        self.notifierFactory = notifierFactory
        self.attachmentsPath = attachmentsPath
        self.enableCalendars = enableCalendars
        self.enableAddressBooks = enableAddressBooks
        self.label = label
        self.quota = quota
        self.logLabels = logLabels
        self.logStats = logStats
        self.logSQL = logSQL
        self.logTransactionWaits = logTransactionWaits
        self.timeoutTransactions = timeoutTransactions
        self._migrating = False
        self._enableNotifications = True


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



    def newTransaction(self, label="unlabeled"):
        """
        @see L{IDataStore.newTransaction}
        """
        txn = CommonStoreTransaction(
            self,
            self.sqlTxnFactory(),
            self.enableCalendars,
            self.enableAddressBooks,
            self.notifierFactory if self._enableNotifications else None,
            label,
            self._migrating,
        )
        
        if self.logTransactionWaits or self.timeoutTransactions:
            CommonStoreTransactionMonitor(txn, self.logTransactionWaits, self.timeoutTransactions)
        return txn

    def setMigrating(self, state):
        """
        Set the "migrating" state
        """
        self._migrating = state
        self._enableNotifications = not state

    def setUpgrading(self, state):
        """
        Set the "upgrading" state
        """
        self._enableNotifications = not state


class TransactionStatsCollector(object):
    
    def __init__(self):
        self.count = collections.defaultdict(int)
        self.times = collections.defaultdict(float)
    
    def startStatement(self, sql):
        self.count[sql] += 1
        return sql, time.time()

    def endStatement(self, context):
        sql, tstamp = context
        self.times[sql] += time.time() - tstamp
        
    def printReport(self, toFile=sys.stdout):
        
        toFile.write("*** SQL Stats ***\n")
        toFile.write("\n")
        toFile.write("Unique statements: %d\n" % (len(self.count,),))
        toFile.write("Total statements: %d\n" % (sum(self.count.values()),))
        toFile.write("Total time (ms): %.3f\n" % (sum(self.times.values()) * 1000.0,))
        toFile.write("\n")
        for k, v in self.count.items():
            toFile.write("%s\n" % (k,))
            toFile.write("Count: %s\n" % (v,))
            toFile.write("Total Time (ms): %.3f\n" % (self.times[k] * 1000.0,))
            if v > 1:
                toFile.write("Average Time (ms): %.3f\n" % (self.times[k] * 1000.0 / v,))
            toFile.write("\n")
        toFile.write("***\n")

class CommonStoreTransactionMonitor(object):
    """
    Object that monitors the state of a transaction over time and logs or times out
    the transaction.
    """
    
    callLater = reactor.callLater

    def __init__(self, txn, logTimerSeconds, timeoutSeconds):
        self.txn = txn
        self.delayedLog = None
        self.delayedTimeout = None
        self.logTimerSeconds = logTimerSeconds
        self.timeoutSeconds = timeoutSeconds
        
        self.txn.postCommit(self._cleanTxn)
        self.txn.postAbort(self._cleanTxn)
        
        self._installLogTimer()
        self._installTimeout()
    
    def _cleanTxn(self):
        self.txn = None
        if self.delayedLog:
            self.delayedLog.cancel()
            self.delayedLog = None
        if self.delayedTimeout:
            self.delayedTimeout.cancel()
            self.delayedTimeout = None

    def _installLogTimer(self):
        def _logTransactionWait():
            if self.txn is not None:
                log.error("Transaction wait: %r, Statements: %d, IUDs: %d, Statement: %s" % (self.txn, self.txn.statementCount, self.txn.iudCount, self.txn.currentStatement if self.txn.currentStatement else "None",))
                self.delayedLog = self.callLater(self.logTimerSeconds, _logTransactionWait)

        if self.logTimerSeconds:
            self.delayedLog = self.callLater(self.logTimerSeconds, _logTransactionWait)
    
    def _installTimeout(self):
        def _forceAbort():
            if self.txn is not None:
                log.error("Transaction abort too long: %r, Statements: %d, IUDs: %d, Statement: %s" % (self.txn, self.txn.statementCount, self.txn.iudCount, self.txn.currentStatement if self.txn.currentStatement else "None",))
                self.delayedTimeout = None
                if self.delayedLog:
                    self.delayedLog.cancel()
                    self.delayedLog = None
                self.txn.abort()

        if self.timeoutSeconds:
            self.delayedTimeout = self.callLater(self.timeoutSeconds, _forceAbort)

class CommonStoreTransaction(object):
    """
    Transaction implementation for SQL database.
    """
    _homeClass = {}

    id = 0

    def __init__(self, store, sqlTxn,
                 enableCalendars, enableAddressBooks,
                 notifierFactory, label, migrating=False):
        self._store = store
        self._calendarHomes = {}
        self._addressbookHomes = {}
        self._notificationHomes = {}
        self._postCommitOperations = []
        self._postAbortOperations = []
        self._notifierFactory = notifierFactory
        self._notifiedAlready = set()
        self._label = label
        self._migrating = migrating
        self._primaryHomeType = None

        CommonStoreTransaction.id += 1
        self._txid = CommonStoreTransaction.id

        extraInterfaces = []
        if enableCalendars:
            extraInterfaces.append(ICalendarTransaction)
            self._primaryHomeType = ECALENDARTYPE
        if enableAddressBooks:
            extraInterfaces.append(IAddressBookTransaction)
            if self._primaryHomeType is None:
                self._primaryHomeType = EADDRESSBOOKTYPE
        directlyProvides(self, *extraInterfaces)

        from txdav.caldav.datastore.sql import CalendarHome
        from txdav.carddav.datastore.sql import AddressBookHome
        CommonStoreTransaction._homeClass[ECALENDARTYPE] = CalendarHome
        CommonStoreTransaction._homeClass[EADDRESSBOOKTYPE] = AddressBookHome
        self._sqlTxn = sqlTxn
        self.paramstyle = sqlTxn.paramstyle
        self.dialect = sqlTxn.dialect
        
        self._stats = TransactionStatsCollector() if self._store.logStats else None
        self.statementCount = 0
        self.iudCount = 0
        self.currentStatement = None


    def store(self):
        return self._store


    def __repr__(self):
        return 'PG-TXN<%s>' % (self._label,)


    @classproperty
    def _calendarserver(cls): #@NoSelf
        cs = schema.CALENDARSERVER
        return Select(
            [cs.VALUE,],
            From=cs,
            Where=cs.NAME == Parameter('name'),
        )

    @inlineCallbacks
    def calendarserverValue(self, key):
        result = yield self._calendarserver.on(self, name=key)
        if result and len(result) == 1:
            returnValue(result[0][0])
        raise RuntimeError("Database key %s cannot be determined." % (key,))
        

    @memoizedKey('uid', '_calendarHomes')
    def calendarHomeWithUID(self, uid, create=False):
        return self.homeWithUID(ECALENDARTYPE, uid, create=create)


    @memoizedKey("uid", "_addressbookHomes")
    def addressbookHomeWithUID(self, uid, create=False):
        return self.homeWithUID(EADDRESSBOOKTYPE, uid, create=create)


    def homeWithUID(self, storeType, uid, create=False):
        if storeType not in (ECALENDARTYPE, EADDRESSBOOKTYPE):
            raise RuntimeError("Unknown home type.")

        return self._homeClass[storeType].homeWithUID(self, uid, create)

    @inlineCallbacks
    def calendarHomeWithResourceID(self, rid):
        uid = (yield self._homeClass[ECALENDARTYPE].homeUIDWithResourceID(self, rid))
        if uid:
            result = (yield self.calendarHomeWithUID(uid))
        else:
            result = None
        returnValue(result)

    @inlineCallbacks
    def addressbookHomeWithResourceID(self, rid):
        uid = (yield self._homeClass[EADDRESSBOOKTYPE].homeUIDWithResourceID(self, rid))
        if uid:
            result = (yield self.addressbookHomeWithUID(uid))
        else:
            result = None
        returnValue(result)

    @memoizedKey("uid", "_notificationHomes")
    def notificationsWithUID(self, uid):
        """
        Implement notificationsWithUID.
        """
        return NotificationCollection.notificationsWithUID(self, uid)


    @classproperty
    def _insertAPNSubscriptionQuery(cls): #@NoSelf
        apn = schema.APN_SUBSCRIPTIONS
        return Insert({apn.TOKEN: Parameter("token"),
                       apn.RESOURCE_KEY: Parameter("resourceKey"),
                       apn.MODIFIED: Parameter("modified"),
                       apn.SUBSCRIBER_GUID: Parameter("subscriber")})


    @classproperty
    def _updateAPNSubscriptionQuery(cls): #@NoSelf
        apn = schema.APN_SUBSCRIPTIONS
        return Update({apn.MODIFIED: Parameter("modified"),
                       apn.SUBSCRIBER_GUID: Parameter("subscriber")},
                      Where=(apn.TOKEN == Parameter("token")).And(
                             apn.RESOURCE_KEY == Parameter("resourceKey")))


    @classproperty
    def _selectAPNSubscriptionQuery(cls): #@NoSelf
        apn = schema.APN_SUBSCRIPTIONS
        return Select([apn.MODIFIED, apn.SUBSCRIBER_GUID], From=apn,
                Where=(
                    apn.TOKEN == Parameter("token")).And(
                    apn.RESOURCE_KEY == Parameter("resourceKey")
                )
            )


    @inlineCallbacks
    def addAPNSubscription(self, token, key, timestamp, subscriber):
        if not (token and key and timestamp and subscriber):
            raise InvalidSubscriptionValues()

        row = yield self._selectAPNSubscriptionQuery.on(self,
            token=token, resourceKey=key)
        if not row: # Subscription does not yet exist
            try:
                yield self._insertAPNSubscriptionQuery.on(self,
                    token=token, resourceKey=key, modified=timestamp,
                    subscriber=subscriber)
            except Exception:
                # Subscription may have been added by someone else, which is fine
                pass

        else: # Subscription exists, so update with new timestamp and subscriber
            try:
                yield self._updateAPNSubscriptionQuery.on(self,
                    token=token, resourceKey=key, modified=timestamp,
                    subscriber=subscriber)
            except Exception:
                # Subscription may have been added by someone else, which is fine
                pass


    @classproperty
    def _removeAPNSubscriptionQuery(cls): #@NoSelf
        apn = schema.APN_SUBSCRIPTIONS
        return Delete(From=apn,
                      Where=(apn.TOKEN == Parameter("token")).And(
                          apn.RESOURCE_KEY == Parameter("resourceKey")))


    def removeAPNSubscription(self, token, key):
        return self._removeAPNSubscriptionQuery.on(self,
            token=token, resourceKey=key)


    @classproperty
    def _apnSubscriptionsByTokenQuery(cls): #@NoSelf
        apn = schema.APN_SUBSCRIPTIONS
        return Select([apn.RESOURCE_KEY, apn.MODIFIED, apn.SUBSCRIBER_GUID],
                      From=apn, Where=apn.TOKEN == Parameter("token"))


    def apnSubscriptionsByToken(self, token):
        return self._apnSubscriptionsByTokenQuery.on(self, token=token)


    @classproperty
    def _apnSubscriptionsByKeyQuery(cls): #@NoSelf
        apn = schema.APN_SUBSCRIPTIONS
        return Select([apn.TOKEN, apn.SUBSCRIBER_GUID],
                      From=apn, Where=apn.RESOURCE_KEY == Parameter("resourceKey"))


    def apnSubscriptionsByKey(self, key):
        return self._apnSubscriptionsByKeyQuery.on(self, resourceKey=key)


    @classproperty
    def _apnSubscriptionsBySubscriberQuery(cls): #@NoSelf
        apn = schema.APN_SUBSCRIPTIONS
        return Select([apn.TOKEN, apn.RESOURCE_KEY, apn.MODIFIED],
                      From=apn, Where=apn.SUBSCRIBER_GUID == Parameter("subscriberGUID"))


    def apnSubscriptionsBySubscriber(self, guid):
        return self._apnSubscriptionsBySubscriberQuery.on(self, subscriberGUID=guid)


    def postCommit(self, operation):
        """
        Run things after C{commit}.
        """
        self._postCommitOperations.append(operation)


    def postAbort(self, operation):
        """
        Run things after C{abort}.
        """
        self._postAbortOperations.append(operation)


    def isNotifiedAlready(self, obj):
        return obj in self._notifiedAlready
    
    def notificationAddedForObject(self, obj):
        self._notifiedAlready.add(obj)

    _savepointCounter = 0

    def _savepoint(self):
        """
        Generate a new SavepointAction whose name is unique in this transaction.
        """
        self._savepointCounter += 1
        return SavepointAction('sp%d' % (self._savepointCounter,))


    @inlineCallbacks
    def subtransaction(self, thunk, retries=1, failureOK=False):
        """
        Create a limited transaction object, which provides only SQL execution,
        and run a function in a sub-transaction (savepoint) context, with that
        object to execute SQL on.

        @param thunk: a 1-argument callable which returns a Deferred when it is
            done.  If this Deferred fails, 

        @param retries: the number of times to re-try C{thunk} before deciding
            that it's legitimately failed.

        @param failureOK: it is OK if this subtransaction fails so do not log.

        @return: a L{Deferred} which fires or fails according to the logic in
            C{thunk}.  If it succeeds, it will return the value that C{thunk}
            returned.
        """
        # Right now this code is covered mostly by the automated property store
        # tests.  It should have more direct test coverage.

        # TODO: we should really have a list of acceptable exceptions for
        # failure and not blanket catch, but that involves more knowledge of the
        # database driver in use than we currently possess at this layer.
        block = self._sqlTxn.commandBlock()
        sp = self._savepoint()
        failuresToMaybeLog = []
        triesLeft = retries
        try:
            while True:
                yield sp.acquire(block)
                try:
                    result = yield thunk(block)
                except:
                    if not failureOK:
                        failuresToMaybeLog.append(Failure())
                    yield sp.rollback(block)
                    if triesLeft:
                        triesLeft -= 1
                        # Important to get the new block before the old one has
                        # been completed; since we almost certainly have some
                        # writes to do, the caller of commit() will expect that
                        # they actually get done, even if they didn't actually
                        # block or yield to wait for them!  (c.f. property
                        # store writes.)
                        newBlock = self._sqlTxn.commandBlock()
                        block.end()
                        block = newBlock
                        sp = self._savepoint()
                    else:
                        block.end()
                        for f in failuresToMaybeLog:
                            # TODO: direct tests, to make sure error logging
                            # happens correctly in all cases.
                            log.err(f)
                        raise AllRetriesFailed()
                else:
                    yield sp.release(block)
                    block.end()
                    returnValue(result)
        except AlreadyFinishedError:
            # Interfering agents may disrupt our plans by calling abort()
            # halfway through trying to do this subtransaction.  In that case -
            # and only that case - acquire() or release() or commandBlock() may
            # raise an AlreadyFinishedError (either synchronously, or in the
            # case of the first two, possibly asynchronously as well).  We can
            # safely ignore this, because it can't have any real effect; our
            # caller shouldn't be paying attention anyway.
            block.end()

    @inlineCallbacks
    def execSQL(self, *a, **kw):
        """
        Execute some SQL (delegate to L{IAsyncTransaction}).
        """
        if self._stats:        
            statsContext = self._stats.startStatement(a[0])
        self.currentStatement = a[0]
        if self._store.logTransactionWaits and a[0].split(" ", 1)[0].lower() in ("insert", "update", "delete",):
            self.iudCount += 1
        self.statementCount += 1
        if self._store.logLabels:
            a = ("-- Label: %s\n" % (self._label.replace("%", "%%"),) + a[0],) + a[1:]
        if self._store.logSQL:
            log.error("SQL: %r %r" % (a, kw,))
        try:
            results = (yield self._sqlTxn.execSQL(*a, **kw))
        finally:
            self.currentStatement = None
            if self._stats:        
                self._stats.endStatement(statsContext)
        returnValue(results)

    @inlineCallbacks
    def execSQLBlock(self, sql):
        """
        Execute a block of SQL by parsing it out into individual statements and execute
        each of those.
        
        FIXME: temporary measure for handling large schema upgrades. This should NOT be used
        for regular SQL operations - only upgrades. 
        """
        parsed = parse(sql)
        for stmt in parsed:
            while stmt.tokens and not significant(stmt.tokens[0]):
                stmt.tokens.pop(0)
            if not stmt.tokens:
                continue
            stmt = str(stmt).rstrip(";")
            yield self.execSQL(stmt)

    def commit(self):
        """
        Commit the transaction and execute any post-commit hooks.
        """
        def postCommit(ignored):
            for operation in self._postCommitOperations:
                operation()
            return ignored

        if self._stats:
            s = StringIO()
            self._stats.printReport(s)
            log.error(s.getvalue())

        return self._sqlTxn.commit().addCallback(postCommit)


    def abort(self):
        """
        Abort the transaction.
        """
        def postAbort(ignored):
            for operation in self._postAbortOperations:
                operation()
            return ignored
        return self._sqlTxn.abort().addCallback(postAbort)


    def _oldEventsBase(limited): #@NoSelf
        ch = schema.CALENDAR_HOME
        co = schema.CALENDAR_OBJECT
        cb = schema.CALENDAR_BIND
        tr = schema.TIME_RANGE
        kwds = { }
        if limited:
            kwds["Limit"] = Parameter("batchSize")
        return Select(
            [
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_NAME,
                Max(tr.END_DATE)
            ],
            From=ch.join(co).join(cb).join(tr),
            Where=(
                ch.RESOURCE_ID == cb.CALENDAR_HOME_RESOURCE_ID     ).And(
                tr.CALENDAR_OBJECT_RESOURCE_ID == co.RESOURCE_ID   ).And(
                cb.CALENDAR_RESOURCE_ID == tr.CALENDAR_RESOURCE_ID ).And(
                cb.BIND_MODE == _BIND_MODE_OWN
            ),
            GroupBy=(
                ch.OWNER_UID,
                cb.CALENDAR_RESOURCE_NAME,
                co.RESOURCE_NAME
            ),
            Having=Max(tr.END_DATE) < Parameter("CutOff"),
            OrderBy=Max(tr.END_DATE),
            **kwds
        )

    _oldEventsLimited = _oldEventsBase(True)
    _oldEventsUnlimited = _oldEventsBase(False)
    del _oldEventsBase


    def eventsOlderThan(self, cutoff, batchSize=None):
        """
        Return up to the oldest batchSize events which exist completely earlier
        than "cutoff" (PyCalendarDateTime)

        Returns a deferred to a list of (uid, calendarName, eventName, maxDate)
        tuples.
        """
        kwds = { "CutOff" : pyCalendarTodatetime(cutoff) }
        if batchSize is not None:
            kwds["batchSize"] = batchSize
            query = self._oldEventsLimited
        else:
            query = self._oldEventsUnlimited
        return query.on(self, **kwds)


    @inlineCallbacks
    def removeOldEvents(self, cutoff, batchSize=None):
        """
        Remove up to batchSize events older than "cutoff" and return how
        many were removed.
        """

        results = (yield self.eventsOlderThan(cutoff, batchSize=batchSize))
        count = 0
        for uid, calendarName, eventName, _ignore_maxDate in results:
            home = (yield self.calendarHomeWithUID(uid))
            calendar = (yield home.childWithName(calendarName))
            (yield calendar.removeObjectResourceWithName(eventName))
            count += 1
        returnValue(count)


    def _orphanedBase(limited): #@NoSelf
        at = schema.ATTACHMENT
        co = schema.CALENDAR_OBJECT
        kwds = {}
        if limited:
            kwds["Limit"] = Parameter('batchSize')
        return Select(
            [at.DROPBOX_ID, at.PATH],
            From=at.join(co, at.DROPBOX_ID == co.DROPBOX_ID, "left outer"),
            Where=co.DROPBOX_ID == None,
            **kwds
        )

    _orphanedLimited = _orphanedBase(True)
    _orphanedUnlimited = _orphanedBase(False)
    del _orphanedBase


    def orphanedAttachments(self, batchSize=None):
        """
        Find attachments no longer referenced by any events.

        Returns a deferred to a list of (dropbox_id, path) tuples.
        """
        if batchSize is not None:
            kwds = {'batchSize': batchSize}
            query = self._orphanedLimited
        else:
            kwds = {}
            query = self._orphanedUnlimited
        return query.on(self, **kwds)


    @inlineCallbacks
    def removeOrphanedAttachments(self, batchSize=None):
        """
        Remove attachments that no longer have any references to them
        """

        # TODO: see if there is a better way to import Attachment
        from txdav.caldav.datastore.sql import Attachment

        results = (yield self.orphanedAttachments(batchSize=batchSize))
        count = 0
        for dropboxID, path in results:
            attachment = Attachment(self, dropboxID, path)
            (yield attachment.remove( ))
            count += 1
        returnValue(count)


class CommonHome(LoggingMixIn):

    # All these need to be initialized by derived classes for each store type
    _homeTable = None
    _homeMetaDataTable = None
    _childClass = None
    _childTable = None
    _bindTable = None
    _objectBindTable = None
    _notifierPrefix = None
    _revisionsTable = None
    _notificationRevisionsTable = NOTIFICATION_OBJECT_REVISIONS_TABLE
    
    _dataVersionKey = None
    _dataVersionValue = None
    
    _cacher = None  # Initialize in derived classes

    def __init__(self, transaction, ownerUID, notifiers):
        self._txn = transaction
        self._ownerUID = ownerUID
        self._resourceID = None
        self._shares = None
        self._childrenLoaded = False
        self._children = {}
        self._sharedChildren = {}
        self._notifiers = notifiers
        self._quotaUsedBytes = None
        self._created = None
        self._modified = None

        # Needed for REVISION/BIND table join
        self._revisionBindJoinTable = {}
        for key, value in self._revisionsTable.iteritems():
            self._revisionBindJoinTable["REV:%s" % (key,)] = value
        for key, value in self._bindTable.iteritems():
            self._revisionBindJoinTable["BIND:%s" % (key,)] = value


    def quotaAllowedBytes(self):
        return self._txn.store().quota


    @classproperty
    def _resourceIDFromOwnerQuery(cls): #@NoSelf
        home = cls._homeSchema
        return Select([home.RESOURCE_ID],
                      From=home, Where=home.OWNER_UID == Parameter("ownerUID"))

    @classproperty
    def _ownerFromFromResourceID(cls): #@NoSelf
        home = cls._homeSchema
        return Select([home.OWNER_UID],
                      From=home,
                      Where=home.RESOURCE_ID == Parameter("resourceID"))

    @classproperty
    def _metaDataQuery(cls): #@NoSelf
        metadata = cls._homeMetaDataSchema
        return Select([metadata.CREATED, metadata.MODIFIED],
                      From=metadata,
                      Where=metadata.RESOURCE_ID == Parameter("resourceID"))

    @inlineCallbacks
    def initFromStore(self, no_cache=False):
        """
        Initialize this object from the store. We read in and cache all the
        extra meta-data from the DB to avoid having to do DB queries for those
        individually later.
        """
        result = yield self._cacher.get(self._ownerUID)
        if result is None:
            result = yield self._resourceIDFromOwnerQuery.on(
                self._txn, ownerUID=self._ownerUID)
            if result and not no_cache:
                yield self._cacher.set(self._ownerUID, result)

        if result:
            self._resourceID = result[0][0]
            self._created, self._modified = (yield self._metaDataQuery.on(
                self._txn, resourceID=self._resourceID))[0]
            yield self._loadPropertyStore()
            returnValue(self)
        else:
            returnValue(None)


    @classmethod
    @inlineCallbacks
    def homeWithUID(cls, txn, uid, create=False):
        if txn._notifierFactory:
            notifiers = (txn._notifierFactory.newNotifier(
                id=uid, prefix=cls._notifierPrefix
            ),)
        else:
            notifiers = None
        homeObject = cls(txn, uid, notifiers)
        homeObject = (yield homeObject.initFromStore())
        if homeObject is not None:
            returnValue(homeObject)
        else:
            if not create:
                returnValue(None)

            # Use savepoint so we can do a partial rollback if there is a race condition
            # where this row has already been inserted
            savepoint = SavepointAction("homeWithUID")
            yield savepoint.acquire(txn)

            if cls._dataVersionValue is None:
                cls._dataVersionValue = yield txn.calendarserverValue(cls._dataVersionKey)
            try:
                resourceid = (yield Insert(
                    {
                        cls._homeSchema.OWNER_UID: uid,
                        cls._homeSchema.DATAVERSION: cls._dataVersionValue,
                    },
                    Return=cls._homeSchema.RESOURCE_ID).on(txn))[0][0]
                yield Insert(
                    {cls._homeMetaDataSchema.RESOURCE_ID: resourceid}).on(txn)
            except Exception: # FIXME: Really want to trap the pg.DatabaseError but in a non-DB specific manner
                yield savepoint.rollback(txn)

                # Retry the query - row may exist now, if not re-raise
                homeObject = cls(txn, uid, notifiers)
                homeObject = (yield homeObject.initFromStore())
                if homeObject:
                    returnValue(homeObject)
                else:
                    raise
            else:
                yield savepoint.release(txn)

                # Note that we must not cache the owner_uid->resource_id
                # mapping in _cacher when creating as we don't want that to appear
                # until AFTER the commit
                home = cls(txn, uid, notifiers)
                home = (yield home.initFromStore(no_cache=True))
                yield home.createdHome()
                returnValue(home)


    @classmethod
    @inlineCallbacks
    def homeUIDWithResourceID(cls, txn, rid):
        rows = (yield cls._ownerFromFromResourceID.on(txn, resourceID=rid))
        if rows:
            returnValue(rows[0][0])
        else:
            returnValue(None)


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
        Implement L{IDataStoreObject.name} to return the uid.
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


    @inlineCallbacks
    def loadChildren(self):
        """
        Load and cache all children - Depth:1 optimization
        """
        results1 = (yield self._childClass.loadAllObjects(self, owned=True))
        for result in results1:
            self._children[result.name()] = result
        results2 = (yield self._childClass.loadAllObjects(self, owned=False))
        for result in results2:
            self._sharedChildren[result.name()] = result
        self._childrenLoaded = True
        returnValue(results1 + results2)


    def listChildren(self):
        """
        Retrieve the names of the children in this home.

        @return: an iterable of C{str}s.
        """
        
        if self._childrenLoaded:
            return succeed(self._children.keys())
        else:
            return self._childClass.listObjects(self, owned=True)


    def listSharedChildren(self):
        """
        Retrieve the names of the children in this home.

        @return: an iterable of C{str}s.
        """
        if self._childrenLoaded:
            return succeed(self._sharedChildren.keys())
        else:
            return self._childClass.listObjects(self, owned=False)


    @memoizedKey("name", "_children")
    def childWithName(self, name):
        """
        Retrieve the child with the given C{name} contained in this
        home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such child exists.
        """
        return self._childClass.objectWithName(self, name, owned=True)

    @memoizedKey("resourceID", "_children")
    def childWithID(self, resourceID):
        """
        Retrieve the child with the given C{resourceID} contained in this
        home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such child exists.
        """
        return self._childClass.objectWithID(self, resourceID)

    @memoizedKey("name", "_sharedChildren")
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
        return self._childClass.objectWithName(self, name, owned=False)


    @inlineCallbacks
    def createChildWithName(self, name):
        if name.startswith("."):
            raise HomeChildNameNotAllowedError(name)

        yield self._childClass.create(self, name)
        child = (yield self.childWithName(name))
        returnValue(child)

    @inlineCallbacks
    def removeChildWithName(self, name):
        child = yield self.childWithName(name)
        if child is None:
            raise NoSuchHomeChildError()

        try:
            yield child.remove()
        finally:
            self._children.pop(name, None)


    @classproperty
    def _syncTokenQuery(cls): #@NoSelf
        """
        DAL Select statement to find the sync token.
        """
        rev = cls._revisionsSchema
        bind = cls._bindSchema
        return Select(
            [Max(rev.REVISION)],
            From=Select(
                [rev.REVISION],
                From=rev,
                Where=(
                    rev.RESOURCE_ID.In(
                        Select(
                            [bind.RESOURCE_ID],
                            From=bind,
                            Where=bind.HOME_RESOURCE_ID == Parameter("resourceID"),
                        )
                    )
                ),
                SetExpression=Union(
                    Select(
                        [rev.REVISION],
                        From=rev,
                        Where=(rev.HOME_RESOURCE_ID == Parameter("resourceID")).And(rev.RESOURCE_ID == None),
                    ),
                    optype=Union.OPTYPE_ALL,
                )
            ),
        )


    @inlineCallbacks
    def syncToken(self):
        revision = (yield self._syncTokenQuery.on(
            self._txn, resourceID=self._resourceID))[0][0]
        returnValue("%s_%s" % (self._resourceID, revision))


    @classproperty
    def _changesQuery(cls): #@NoSelf
        bind = cls._bindSchema
        rev = cls._revisionsSchema
        return Select([bind.RESOURCE_NAME, rev.COLLECTION_NAME,
                       rev.RESOURCE_NAME, rev.DELETED],
                      From=rev.join(
                          bind,
                          (bind.HOME_RESOURCE_ID ==
                           Parameter("resourceID")).And(
                               rev.RESOURCE_ID ==
                               bind.RESOURCE_ID),
                          'left outer'),
                      Where=(rev.REVISION > Parameter("token")).And(
                          rev.HOME_RESOURCE_ID ==
                          Parameter("resourceID")))


    @inlineCallbacks
    def resourceNamesSinceToken(self, token, depth):

        results = [
            (
                path if path else (collection if collection else ""),
                name if name else "",
                wasdeleted
            )
            for path, collection, name, wasdeleted in
            (yield self._changesQuery.on(self._txn,
                                         resourceID=self._resourceID,
                                         token=token))
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
        bind = self._bindSchema
        rev = self._revisionsSchema
        shares = yield self.listSharedChildren()
        for sharename in shares:
            sharetoken = 0 if sharename in changed_collections else token
            shareID = (yield Select(
                [bind.RESOURCE_ID], From=bind,
                Where=(bind.RESOURCE_NAME == sharename).And(
                    bind.HOME_RESOURCE_ID == self._resourceID).And(
                        bind.BIND_MODE != _BIND_MODE_OWN)
            ).on(self._txn))[0][0]
            results = [
                (
                    sharename,
                    name if name else "",
                    wasdeleted
                )
                for name, wasdeleted in
                (yield Select([rev.RESOURCE_NAME, rev.DELETED],
                                 From=rev,
                                Where=(rev.REVISION > sharetoken).And(
                                rev.RESOURCE_ID == shareID)).on(self._txn))
                if name
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


    # IDataStoreObject
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
        return datetimeMktime(parseSQLTimestamp(self._created)) if self._created else None


    def modified(self):
        return datetimeMktime(parseSQLTimestamp(self._modified)) if self._modified else None


    @classmethod
    def _objectResourceQuery(cls, checkBindMode):
        obj = cls._objectSchema
        bind = cls._bindSchema
        where = ((obj.UID == Parameter("uid"))
                 .And(bind.HOME_RESOURCE_ID == Parameter("resourceID")))
        if checkBindMode:
            where = where.And(bind.BIND_MODE == Parameter("bindMode"))
        return Select(
            [obj.PARENT_RESOURCE_ID, obj.RESOURCE_ID],
            From=obj.join(bind, obj.PARENT_RESOURCE_ID == bind.RESOURCE_ID),
            Where=where
        )


    @classproperty
    def _resourceByUIDQuery(cls): #@NoSelf
        return cls._objectResourceQuery(checkBindMode=False)


    @classproperty
    def _resourceByUIDBindQuery(cls): #@NoSelf
        return cls._objectResourceQuery(checkBindMode=True)


    @inlineCallbacks
    def objectResourcesWithUID(self, uid, ignore_children=[], allowShared=True):
        """
        Return all child object resources with the specified UID, ignoring any
        in the named child collections.
        """
        results = []
        if allowShared:
            rows = (yield self._resourceByUIDQuery.on(
                self._txn, uid=uid, resourceID=self._resourceID
            ))
        else:
            rows = (yield self._resourceByUIDBindQuery.on(
                self._txn, uid=uid, resourceID=self._resourceID,
                bindMode=_BIND_MODE_OWN
            ))
        if rows:
            for childID, objectID in rows:
                child = (yield self.childWithID(childID))
                if child and child.name() not in ignore_children:
                    objectResource = (
                        yield child.objectResourceWithID(objectID)
                    )
                    results.append(objectResource)

        returnValue(results)


    @classproperty
    def _quotaQuery(cls): #@NoSelf
        meta = cls._homeMetaDataSchema
        return Select(
            [meta.QUOTA_USED_BYTES], From=meta,
            Where=meta.RESOURCE_ID == Parameter("resourceID")
        )


    @inlineCallbacks
    def quotaUsedBytes(self):
        if self._quotaUsedBytes is None:
            self._quotaUsedBytes = (yield self._quotaQuery.on(
                self._txn, resourceID=self._resourceID))[0][0]
        returnValue(self._quotaUsedBytes)


    @classproperty
    def _preLockResourceIDQuery(cls): #@NoSelf
        meta = cls._homeMetaDataSchema
        return Select(From=meta,
                      Where=meta.RESOURCE_ID==Parameter("resourceID"),
                      ForUpdate=True)


    @classproperty
    def _increaseQuotaQuery(cls): #@NoSelf
        meta = cls._homeMetaDataSchema
        return Update({meta.QUOTA_USED_BYTES: meta.QUOTA_USED_BYTES +
                       Parameter("delta")},
                      Where=meta.RESOURCE_ID == Parameter("resourceID"),
                      Return=meta.QUOTA_USED_BYTES)


    @classproperty
    def _resetQuotaQuery(cls): #@NoSelf
        meta = cls._homeMetaDataSchema
        return Update({meta.QUOTA_USED_BYTES: 0},
                      Where=meta.RESOURCE_ID == Parameter("resourceID"))


    @inlineCallbacks
    def adjustQuotaUsedBytes(self, delta):
        """
        Adjust quota used. We need to get a lock on the row first so that the
        adjustment is done atomically. It is import to do the 'select ... for
        update' because a race also exists in the 'update ... x = x + 1' case as
        seen via unit tests.
        """
        yield self._preLockResourceIDQuery.on(self._txn,
                                              resourceID=self._resourceID)

        self._quotaUsedBytes = (yield self._increaseQuotaQuery.on(
            self._txn, delta=delta, resourceID=self._resourceID))[0][0]

        # Double check integrity
        if self._quotaUsedBytes < 0:
            log.error(
                "Fixing quota adjusted below zero to %s by change amount %s" %
                (self._quotaUsedBytes, delta,))
            yield self._resetQuotaQuery.on(self._txn,
                                           resourceID=self._resourceID)
            self._quotaUsedBytes = 0

    
    def addNotifier(self, notifier):
        if self._notifiers is None:
            self._notifiers = ()
        self._notifiers += (notifier,)


    def notifierID(self, label="default"):
        if self._notifiers:
            return self._notifiers[0].getID(label)
        else:
            return None


    @inlineCallbacks
    def nodeName(self, label="default"):
        if self._notifiers:
            for notifier in self._notifiers:
                name = (yield notifier.nodeName(label=label))
                if name is not None:
                    returnValue(name)
        else:
            returnValue(None)

    @classproperty
    def _lockLastModifiedQuery(cls): #@NoSelf
        meta = cls._homeMetaDataSchema
        return Select(
            From=meta,
            Where=meta.RESOURCE_ID == Parameter("resourceID"),
            ForUpdate=True,
            NoWait=True
        )

    @classproperty
    def _changeLastModifiedQuery(cls): #@NoSelf
        meta = cls._homeMetaDataSchema
        return Update({meta.MODIFIED: utcNowSQL},
                      Where=meta.RESOURCE_ID == Parameter("resourceID"),
                      Return=meta.MODIFIED)

    @inlineCallbacks
    def bumpModified(self):
        """
        Bump the MODIFIED value. A possible deadlock could happen here if two or more
        simultaneous changes are happening. In that case it is OK for the MODIFIED change
        to fail so long as at least one works. We will use SAVEPOINT logic to handle
        ignoring the deadlock error. We use SELECT FOR UPDATE NOWAIT to ensure we do not
        delay the transaction whilst waiting for deadlock detection to kick in.
        """

        @inlineCallbacks
        def _bumpModified(subtxn):
            yield self._lockLastModifiedQuery.on(subtxn, resourceID=self._resourceID)
            result = (yield self._changeLastModifiedQuery.on(subtxn, resourceID=self._resourceID))
            returnValue(result)
            
        try:
            self._modified = (yield self._txn.subtransaction(_bumpModified, retries=0, failureOK=True))[0][0]
        except AllRetriesFailed:
            log.debug("CommonHome.bumpModified failed")
        
    @inlineCallbacks
    def notifyChanged(self):
        """
        Trigger a notification of a change
        """
        
        # Update modified if object still exists
        if self._resourceID:
            yield self.bumpModified()

        # Only send one set of change notifications per transaction
        if self._notifiers and not self._txn.isNotifiedAlready(self):
            for notifier in self._notifiers:
                self._txn.postCommit(notifier.notify)
            self._txn.notificationAddedForObject(self)



class _SharedSyncLogic(object):
    """
    Logic for maintaining sync-token shared between notification collections and
    shared collections.
    """

    @classproperty
    def _childSyncTokenQuery(cls): #@NoSelf
        """
        DAL query for retrieving the sync token of a L{CommonHomeChild} based on
        its resource ID.
        """
        rev = cls._revisionsSchema
        return Select([Max(rev.REVISION)], From=rev,
                      Where=rev.RESOURCE_ID == Parameter("resourceID"))


    def revisionFromToken(self, token):
        _ignore_uuid, revision = token.split("_", 1)
        return int(revision)

    @inlineCallbacks
    def syncToken(self):
        if self._syncTokenRevision is None:
            self._syncTokenRevision = (yield self._childSyncTokenQuery.on(
                self._txn, resourceID=self._resourceID))[0][0]
        returnValue(("%s_%s" % (self._resourceID, self._syncTokenRevision,)))


    def objectResourcesSinceToken(self, token):
        raise NotImplementedError()


    @classproperty
    def _objectNamesSinceRevisionQuery(cls): #@NoSelf
        """
        DAL query for (resource, deleted-flag)
        """
        rev = cls._revisionsSchema
        return Select([rev.RESOURCE_NAME, rev.DELETED],
                      From=rev,
                      Where=(rev.REVISION > Parameter("revision")).And(
                          rev.RESOURCE_ID == Parameter("resourceID")))


    @inlineCallbacks
    def resourceNamesSinceToken(self, token):
        
        if token is None:
            token = 0
        elif isinstance(token, str):
            token = self.revisionFromToken(token)

        results = [
            (name if name else "", deleted)
            for name, deleted in
            (yield self._objectNamesSinceRevisionQuery.on(
                self._txn, revision=token, resourceID=self._resourceID))
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


    @classproperty
    def _removeDeletedRevision(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Delete(From=rev,
                      Where=(rev.HOME_RESOURCE_ID == Parameter("homeID")).And(
                          rev.COLLECTION_NAME == Parameter("collectionName")))


    @classproperty
    def _addNewRevision(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Insert({rev.HOME_RESOURCE_ID: Parameter("homeID"),
                       rev.RESOURCE_ID: Parameter("resourceID"),
                       rev.COLLECTION_NAME: Parameter("collectionName"),
                       rev.RESOURCE_NAME: None,
                       # Always starts false; may be updated to be a tombstone
                       # later.
                       rev.DELETED: False},
                     Return=[rev.REVISION])


    @inlineCallbacks
    def _initSyncToken(self):
        yield self._removeDeletedRevision.on(
            self._txn, homeID=self._home._resourceID, collectionName=self._name
        )
        self._syncTokenRevision = (yield (
            self._addNewRevision.on(self._txn, homeID=self._home._resourceID,
                                    resourceID=self._resourceID,
                                    collectionName=self._name)))[0][0]


    @classproperty
    def _renameSyncTokenQuery(cls): #@NoSelf
        """
        DAL query to change sync token for a rename (increment and adjust
        resource name).
        """
        rev = cls._revisionsSchema
        return Update({
            rev.REVISION: schema.REVISION_SEQ,
            rev.COLLECTION_NAME: Parameter("name")},
            Where=(rev.RESOURCE_ID == Parameter("resourceID")
                  ).And(rev.RESOURCE_NAME == None),
            Return=rev.REVISION
        )


    @inlineCallbacks
    def _renameSyncToken(self):
        self._syncTokenRevision = (yield self._renameSyncTokenQuery.on(
            self._txn, name=self._name, resourceID=self._resourceID))[0][0]


    @classproperty
    def _deleteSyncTokenQuery(cls): #@NoSelf
        """
        DAL query to update a sync revision to be a tombstone instead.
        """
        rev = cls._revisionsSchema
        return Delete(From=rev, Where=(
            rev.HOME_RESOURCE_ID == Parameter("homeID")).And(
                rev.RESOURCE_ID == Parameter("resourceID")).And(
                rev.COLLECTION_NAME == None))


    @classproperty
    def _sharedRemovalQuery(cls): #@NoSelf
        """
        DAL query to update the sync token for a shared collection.
        """
        rev = cls._revisionsSchema
        return Update({rev.RESOURCE_ID: None,
                       rev.REVISION: schema.REVISION_SEQ,
                       rev.DELETED: True},
                      Where=(rev.HOME_RESOURCE_ID == Parameter("homeID")).And(
                          rev.RESOURCE_ID == Parameter("resourceID")).And(
                              rev.RESOURCE_NAME == None)
                     )


    @classproperty
    def _unsharedRemovalQuery(cls): #@NoSelf
        """
        DAL query to update the sync token for an owned collection.
        """
        rev = cls._revisionsSchema
        return Update({rev.RESOURCE_ID: None,
                       rev.REVISION: schema.REVISION_SEQ,
                       rev.DELETED: True},
                      Where=(rev.RESOURCE_ID == Parameter("resourceID")).And(
                          rev.RESOURCE_NAME == None),
                     )


    @inlineCallbacks
    def _deletedSyncToken(self, sharedRemoval=False):
        # Remove all child entries
        yield self._deleteSyncTokenQuery.on(self._txn,
                                            homeID=self._home._resourceID,
                                            resourceID=self._resourceID)

        # If this is a share being removed then we only mark this one specific
        # home/resource-id as being deleted.  On the other hand, if it is a
        # non-shared collection, then we need to mark all collections
        # with the resource-id as being deleted to account for direct shares.
        if sharedRemoval:
            yield self._sharedRemovalQuery.on(self._txn,
                                              homeID=self._home._resourceID,
                                              resourceID=self._resourceID)
        else:
            yield self._unsharedRemovalQuery.on(self._txn,
                                                resourceID=self._resourceID)
        self._syncTokenRevision = None


    def _insertRevision(self, name):
        return self._changeRevision("insert", name)


    def _updateRevision(self, name):
        return self._changeRevision("update", name)


    def _deleteRevision(self, name):
        return self._changeRevision("delete", name)


    @classproperty
    def _deleteBumpTokenQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Update({rev.REVISION: schema.REVISION_SEQ,
                       rev.DELETED: True},
                      Where=(rev.RESOURCE_ID == Parameter("resourceID")).And(
                           rev.RESOURCE_NAME == Parameter("name")),
                      Return=rev.REVISION)


    @classproperty
    def _updateBumpTokenQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Update({rev.REVISION: schema.REVISION_SEQ},
                      Where=(rev.RESOURCE_ID == Parameter("resourceID")).And(
                           rev.RESOURCE_NAME == Parameter("name")),
                      Return=rev.REVISION)


    @classproperty
    def _insertFindPreviouslyNamedQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Select([rev.RESOURCE_ID], From=rev,
                      Where=(rev.RESOURCE_ID == Parameter("resourceID")).And(
                           rev.RESOURCE_NAME == Parameter("name")))


    @classproperty
    def _updatePreviouslyNamedQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Update({rev.REVISION: schema.REVISION_SEQ,
                       rev.DELETED: False},
                      Where=(rev.RESOURCE_ID == Parameter("resourceID")).And(
                           rev.RESOURCE_NAME == Parameter("name")),
                      Return=rev.REVISION)


    @classproperty
    def _completelyNewRevisionQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Insert({rev.HOME_RESOURCE_ID: Parameter("homeID"),
                       rev.RESOURCE_ID: Parameter("resourceID"),
                       rev.RESOURCE_NAME: Parameter("name"),
                       rev.REVISION: schema.REVISION_SEQ,
                       rev.DELETED: False},
                      Return=rev.REVISION)


    @inlineCallbacks
    def _changeRevision(self, action, name):

        # Need to handle the case where for some reason the revision entry is
        # actually missing. For a "delete" we don't care, for an "update" we
        # will turn it into an "insert".
        if action == "delete":
            rows = (
                yield self._deleteBumpTokenQuery.on(
                    self._txn, resourceID=self._resourceID, name=name))
            if rows:
                self._syncTokenRevision = rows[0][0]
        elif action == "update":
            rows = (
                yield self._updateBumpTokenQuery.on(
                    self._txn, resourceID=self._resourceID, name=name))
            if rows:
                self._syncTokenRevision = rows[0][0]
            else:
                action = "insert"
        
        if action == "insert":
            # Note that an "insert" may happen for a resource that previously
            # existed and then was deleted. In that case an entry in the
            # REVISIONS table still exists so we have to detect that and do db
            # INSERT or UPDATE as appropriate

            found = bool( (
                yield self._insertFindPreviouslyNamedQuery.on(
                    self._txn, resourceID=self._resourceID, name=name)) )
            if found:
                self._syncTokenRevision = (
                    yield self._updatePreviouslyNamedQuery.on(
                        self._txn, resourceID=self._resourceID, name=name)
                )[0][0]
            else:
                self._syncTokenRevision = (
                    yield self._completelyNewRevisionQuery.on(
                        self._txn, homeID=self._home._resourceID,
                        resourceID=self._resourceID, name=name)
                )[0][0]
        self._maybeNotify()


    def _maybeNotify(self):
        """
        Maybe notify changed.  (Overridden in NotificationCollection.)
        """



class CommonHomeChild(LoggingMixIn, FancyEqMixin, _SharedSyncLogic):
    """
    Common ancestor class of AddressBooks and Calendars.

    @ivar _owned: Is this calendar or addressbook referencing its sharer (owner)
        home? (i.e. C{True} if L{ownerCalendarHome} will actually return the
        sharer, C{False} or if it will return a sharee.)
    """

    compareAttributes = (
        "_name",
        "_home",
        "_resourceID",
    )

    _objectResourceClass = None

    _bindSchema              = None
    _homeChildSchema         = None
    _homeChildMetaDataSchema = None
    _revisionsSchema         = None
    _objectSchema            = None

    _bindTable           = None
    _homeChildTable      = None
    _homeChildBindTable  = None
    _revisionsTable      = None
    _revisionsBindTable  = None
    _objectTable         = None


    def __init__(self, home, name, resourceID, owned):

        if home._notifiers:
            childID = "%s/%s" % (home.uid(), name)
            notifiers = [notifier.clone(label="collection", id=childID)
                         for notifier in home._notifiers]
        else:
            notifiers = None

        self._home              = home
        self._name              = name
        self._resourceID        = resourceID
        self._owned             = owned
        self._created           = None
        self._modified          = None
        self._objects           = {}
        self._objectNames       = None
        self._syncTokenRevision = None
        self._notifiers         = notifiers
        self._index             = None  # Derived classes need to set this
        self._invites           = None  # Derived classes need to set this


    @classproperty
    def _ownedChildListQuery(cls): #@NoSelf
        bind = cls._bindSchema
        return Select([bind.RESOURCE_NAME], From=bind,
                      Where=(bind.HOME_RESOURCE_ID ==
                             Parameter("resourceID")).And(
                                 bind.BIND_MODE == _BIND_MODE_OWN))


    @classmethod
    def metadataColumns(cls):
        """
        Return a list of column name for retrieval of metadata. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """
        
        # Common behavior is to have created and modified
        
        return (
            cls._homeChildMetaDataSchema.CREATED,
            cls._homeChildMetaDataSchema.MODIFIED,
        )
        
    @classmethod
    def metadataAttributes(cls):
        """
        Return a list of attribute names for retrieval of metadata. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """
        
        # Common behavior is to have created and modified
        
        return (
            "_created",
            "_modified",
        )
        
    @classproperty
    def _sharedChildListQuery(cls): #@NoSelf
        bind = cls._bindSchema
        return Select([bind.RESOURCE_NAME], From=bind,
                      Where=(bind.HOME_RESOURCE_ID ==
                             Parameter("resourceID")).And(
                                 bind.BIND_MODE != _BIND_MODE_OWN).And(
                                 bind.BIND_STATUS == _BIND_STATUS_ACCEPTED))

    @classmethod
    @inlineCallbacks
    def listObjects(cls, home, owned):
        """
        Retrieve the names of the children that exist in the given home.

        @return: an iterable of C{str}s.
        """
        # FIXME: tests don't cover this as directly as they should.
        if owned:
            rows = yield cls._ownedChildListQuery.on(
                home._txn, resourceID=home._resourceID)
        else:
            rows = yield cls._sharedChildListQuery.on(
                home._txn, resourceID=home._resourceID)
        names = [row[0] for row in rows]
        returnValue(names)


    @classmethod
    def _allHomeChildrenQuery(cls, owned):
        bind = cls._bindSchema
        child = cls._homeChildSchema
        childMetaData = cls._homeChildMetaDataSchema
        if owned:
            ownedPiece = bind.BIND_MODE == _BIND_MODE_OWN
        else:
            ownedPiece = (bind.BIND_MODE != _BIND_MODE_OWN).And(
                bind.BIND_STATUS == _BIND_STATUS_ACCEPTED)
        
        columns = [child.RESOURCE_ID, bind.RESOURCE_NAME,]
        columns.extend(cls.metadataColumns())
        return Select(columns,
                     From=child.join(
                         bind, child.RESOURCE_ID == bind.RESOURCE_ID,
                         'left outer').join(
                         childMetaData, childMetaData.RESOURCE_ID == bind.RESOURCE_ID,
                         'left outer'),
                     Where=(bind.HOME_RESOURCE_ID == Parameter("resourceID")
                           ).And(ownedPiece))


    @classproperty
    def _ownedHomeChildrenQuery(cls): #@NoSelf
        return cls._allHomeChildrenQuery(True)


    @classproperty
    def _sharedHomeChildrenQuery(cls): #@NoSelf
        return cls._allHomeChildrenQuery(False)


    @classmethod
    @inlineCallbacks
    def loadAllObjects(cls, home, owned):
        """
        Load all child objects and return a list of them. This must create the
        child classes and initialize them using "batched" SQL operations to keep
        this constant wrt the number of children. This is an optimization for
        Depth:1 operations on the home.
        """
        results = []

        # Load from the main table first
        if owned:
            query = cls._ownedHomeChildrenQuery
        else:
            query = cls._sharedHomeChildrenQuery
        dataRows = (yield query.on(home._txn, resourceID=home._resourceID))

        if dataRows:
            # Get property stores for all these child resources (if any found)
            propertyStores = (yield PropertyStore.forMultipleResources(
                home.uid(), home._txn,
                cls._bindSchema.RESOURCE_ID, cls._bindSchema.HOME_RESOURCE_ID,
                home._resourceID
            ))

            bind = cls._bindSchema
            rev = cls._revisionsSchema
            if owned:
                ownedCond = bind.BIND_MODE == _BIND_MODE_OWN
            else:
                ownedCond = bind.BIND_MODE != _BIND_MODE_OWN
            revisions = (yield Select(
                [rev.RESOURCE_ID, Max(rev.REVISION)],
                From=rev.join(bind, rev.RESOURCE_ID == bind.RESOURCE_ID, 'left'),
                Where=(bind.HOME_RESOURCE_ID == home._resourceID).
                    And(ownedCond).
                    And((rev.RESOURCE_NAME != None).Or(rev.DELETED == False)),
                GroupBy=rev.RESOURCE_ID
            ).on(home._txn))
            revisions = dict(revisions)

        # Create the actual objects merging in properties
        for items in dataRows:
            resourceID, resource_name = items[:2]
            metadata = items[2:]
            child = cls(home, resource_name, resourceID, owned)
            for attr, value in zip(cls.metadataAttributes(), metadata):
                setattr(child, attr, value)
            child._syncTokenRevision = revisions[resourceID]
            propstore = propertyStores.get(resourceID, None)
            yield child._loadPropertyStore(propstore)
            results.append(child)
        returnValue(results)


    @classmethod
    def _homeChildLookup(cls, ownedPart):
        """
        Common portions of C{_ownedResourceIDByName}
        C{_resourceIDSharedToHomeByName}, except for the 'owned' fragment of the
        Where clause, supplied as an argument.
        """
        bind = cls._bindSchema
        return Select(
            [bind.RESOURCE_ID],
            From=bind,
            Where=(bind.RESOURCE_NAME == Parameter('objectName')).And(
                   bind.HOME_RESOURCE_ID == Parameter('homeID')).And(
                    ownedPart))


    @classproperty
    def _resourceIDOwnedByHomeByName(cls): #@NoSelf
        """
        DAL query to look up an object resource ID owned by a home, given a
        resource name (C{objectName}), and a home resource ID
        (C{homeID}).
        """
        return cls._homeChildLookup(
            cls._bindSchema.BIND_MODE == _BIND_MODE_OWN)


    @classproperty
    def _resourceIDSharedToHomeByName(cls): #@NoSelf
        """
        DAL query to look up an object resource ID shared to a home, given a
        resource name (C{objectName}), and a home resource ID
        (C{homeID}).
        """
        return cls._homeChildLookup(
            (cls._bindSchema.BIND_MODE != _BIND_MODE_OWN).And(
                cls._bindSchema.BIND_STATUS == _BIND_STATUS_ACCEPTED))


    @classmethod
    @inlineCallbacks
    def objectWithName(cls, home, name, owned):
        """
        Retrieve the child with the given C{name} contained in the given
        C{home}.

        @param home: a L{CommonHome}.

        @param name: a string; the name of the L{CommonHomeChild} to retrieve.

        @param owned: a boolean - whether or not to get a shared child
        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        if owned:
            query = cls._resourceIDOwnedByHomeByName
        else:
            query = cls._resourceIDSharedToHomeByName
        data = yield query.on(home._txn,
                              objectName=name, homeID=home._resourceID)
        if not data:
            returnValue(None)
        resourceID = data[0][0]
        child = cls(home, name, resourceID, owned)
        yield child.initFromStore()
        returnValue(child)


    @classproperty
    def _homeChildByIDQuery(cls): #@NoSelf
        """
        DAL query that looks up home child names / bind modes by home child
        resouce ID and home resource ID.
        """
        bind = cls._bindSchema
        return Select([bind.RESOURCE_NAME, bind.BIND_MODE],
                      From=bind,
                      Where=(bind.RESOURCE_ID == Parameter("resourceID")
                            ).And(bind.HOME_RESOURCE_ID == Parameter("homeID")))


    @classmethod
    @inlineCallbacks
    def objectWithID(cls, home, resourceID):
        """
        Retrieve the child with the given C{resourceID} contained in the given
        C{home}.

        @param home: a L{CommonHome}.
        @param resourceID: a string.
        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        data = yield cls._homeChildByIDQuery.on(
            home._txn, resourceID=resourceID, homeID=home._resourceID)
        if not data:
            returnValue(None)
        name, mode = data[0]
        child = cls(home, name, resourceID, mode == _BIND_MODE_OWN)
        yield child.initFromStore()
        returnValue(child)


    @classproperty
    def _insertHomeChild(cls): #@NoSelf
        """
        DAL statement to create a home child with all default values.
        """
        child = cls._homeChildSchema
        return Insert({child.RESOURCE_ID: schema.RESOURCE_ID_SEQ},
                      Return=(child.RESOURCE_ID))


    @classproperty
    def _insertHomeChildMetaData(cls): #@NoSelf
        """
        DAL statement to create a home child with all default values.
        """
        child = cls._homeChildMetaDataSchema
        return Insert({child.RESOURCE_ID: Parameter("resourceID")},
                      Return=(child.CREATED, child.MODIFIED))


    @classproperty
    def _initialOwnerBind(cls): #@NoSelf
        """
        DAL statement to create a bind entry for a particular home value.
        """
        bind = cls._bindSchema
        return Insert({bind.HOME_RESOURCE_ID: Parameter("homeID"),
                       bind.RESOURCE_ID: Parameter("resourceID"),
                       bind.RESOURCE_NAME: Parameter("name"),
                       bind.BIND_MODE: _BIND_MODE_OWN,
                       bind.SEEN_BY_OWNER: True,
                       bind.SEEN_BY_SHAREE: True,
                       bind.BIND_STATUS: _BIND_STATUS_ACCEPTED})


    @classmethod
    @inlineCallbacks
    def create(cls, home, name):
        child = (yield cls.objectWithName(home, name, owned=True))
        if child is not None:
            raise HomeChildNameAlreadyExistsError(name)

        if name.startswith("."):
            raise HomeChildNameNotAllowedError(name)

        # Create this object
        resourceID = (
            yield cls._insertHomeChild.on(home._txn))[0][0]

        # Initialize this object
        _created, _modified = (
            yield cls._insertHomeChildMetaData.on(home._txn,
                                                  resourceID=resourceID))[0]

        # Bind table needs entry
        yield cls._initialOwnerBind.on(home._txn, homeID=home._resourceID,
                                       resourceID=resourceID, name=name)

        # Initialize other state
        child = cls(home, name, resourceID, True)
        child._created = _created
        child._modified = _modified
        yield child._loadPropertyStore()

        child.properties()[
            PropertyName.fromElement(ResourceType)
        ] = child.resourceType()
        yield child._initSyncToken()

        # Change notification for a create is on the home collection
        yield home.notifyChanged()
        returnValue(child)


    @classproperty
    def _metadataByIDQuery(cls): #@NoSelf
        """
        DAL query to retrieve created/modified dates based on a resource ID.
        """
        child = cls._homeChildMetaDataSchema
        return Select(cls.metadataColumns(),
                      From=child,
                      Where=child.RESOURCE_ID == Parameter("resourceID"))


    @inlineCallbacks
    def initFromStore(self):
        """
        Initialise this object from the store, based on its already-populated
        resource ID. We read in and cache all the extra metadata from the DB to
        avoid having to do DB queries for those individually later.
        """
        dataRows = (
            yield self._metadataByIDQuery.on(self._txn,
                                          resourceID=self._resourceID))[0]
        for attr, value in zip(self.metadataAttributes(), dataRows):
            setattr(self, attr, value)
        yield self._loadPropertyStore()


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


    def exists(self):
        """
        An empty resource-id means this object does not yet exist in the DB.
        """
        return self._resourceID is not None


    def name(self):
        return self._name


    @classproperty
    def _renameQuery(cls): #@NoSelf
        """
        DAL statement to rename a L{CommonHomeChild}
        """
        bind = cls._bindSchema
        return Update({bind.RESOURCE_NAME: Parameter("name")},
                      Where=(bind.RESOURCE_ID == Parameter("resourceID")).And(
                          bind.HOME_RESOURCE_ID == Parameter("homeID")))


    @inlineCallbacks
    def rename(self, name):
        """
        Change the name of this L{CommonHomeChild} and update its sync token to
        reflect that change.

        @return: a L{Deferred} which fires when the modification is complete.
        """
        oldName = self._name
        yield self._renameQuery.on(self._txn, name=name,
                                   resourceID=self._resourceID,
                                   homeID=self._home._resourceID)
        self._name = name
        # update memos
        del self._home._children[oldName]
        self._home._children[name] = self
        yield self._renameSyncToken()

        yield self.notifyChanged()

        # Make sure home collection modified is changed - not that we do not use _home.notifiedChanged() here
        # since we are sending the notification on the existing child collection object
        yield self._home.bumpModified()


    @classproperty
    def _deleteQuery(cls): #@NoSelf
        """
        DAL statement to delete a L{CommonHomeChild} by its resource ID.
        """
        child = cls._homeChildSchema
        return Delete(child, Where=child.RESOURCE_ID == Parameter("resourceID"))


    @inlineCallbacks
    def remove(self):
        yield self._deletedSyncToken()
        yield self._deleteQuery.on(self._txn, NoSuchHomeChildError,
                                   resourceID=self._resourceID)
        self.properties()._removeResource()

        # Set to non-existent state
        self._resourceID = None
        self._created    = None
        self._modified   = None
        self._objects    = {}

        yield self.notifyChanged()

        # Make sure home collection modified is changed - not that we do not use _home.notifiedChanged() here
        # since we are sending the notification on the previously existing child collection object
        yield self._home.bumpModified()


    def ownerHome(self):
        return self._home


    @classproperty
    def _ownerHomeFromResourceQuery(cls): #@NoSelf
        """
        DAL query to retrieve the home resource ID of the owner from the bound
        home-child ID.
        """
        bind = cls._bindSchema
        return Select([bind.HOME_RESOURCE_ID],
                     From=bind,
                     Where=(bind.RESOURCE_ID ==
                            Parameter("resourceID")).And(
                                bind.BIND_MODE == _BIND_MODE_OWN))


    @inlineCallbacks
    def sharerHomeID(self):
        if self._owned:
            # If this was loaded by its owner then we can skip the query, since
            # we already know who the owner is.
            returnValue(self._home._resourceID)
        else:
            rid = (yield self._ownerHomeFromResourceQuery.on(
                self._txn, resourceID=self._resourceID))[0][0]
            returnValue(rid)


    def setSharingUID(self, uid):
        self.properties()._setPerUserUID(uid)


    @inlineCallbacks
    def objectResources(self):
        """
        Load and cache all children - Depth:1 optimization
        """
        results = (yield self._objectResourceClass.loadAllObjects(self))
        for result in results:
            self._objects[result.name()] = result
            self._objects[result.uid()] = result
        self._objectNames = sorted([result.name() for result in results])
        returnValue(results)


    @classproperty
    def _objectResourceNamesQuery(cls): #@NoSelf
        """
        DAL query to load all object resource names for a home child.
        """
        obj = cls._objectSchema
        return Select([obj.RESOURCE_NAME], From=obj,
                      Where=obj.PARENT_RESOURCE_ID == Parameter('resourceID'))


    @inlineCallbacks
    def listObjectResources(self):
        if self._objectNames is None:
            rows = yield self._objectResourceNamesQuery.on(
                self._txn, resourceID=self._resourceID)
            self._objectNames = sorted([row[0] for row in rows])
        returnValue(self._objectNames)


    def objectResourceWithName(self, name):
        if name in self._objects:
            return succeed(self._objects[name])
        else:
            return self._makeObjectResource(name=name)


    def objectResourceWithUID(self, uid):
        if uid in self._objects:
            return succeed(self._objects[uid])
        else:
            return self._makeObjectResource(uid=uid)


    def objectResourceWithID(self, resourceID):
        if resourceID in self._objects:
            return succeed(self._objects[resourceID])
        else:
            return self._makeObjectResource(resourceID=resourceID)


    @inlineCallbacks
    def _makeObjectResource(self, name=None, uid=None, resourceID=None):
        """
        We create the empty object first then have it initialize itself from the
        store.
        """
        if resourceID:
            objectResource = (
                yield self._objectResourceClass.objectWithID(self, resourceID)
            )
        else:
            objectResource = (
                yield self._objectResourceClass.objectWithName(self, name, uid)
            )
        if objectResource:
            self._objects[objectResource.name()] = objectResource
            self._objects[objectResource.uid()] = objectResource
            self._objects[objectResource._resourceID] = objectResource
        else:
            if resourceID:
                self._objects[resourceID] = None
            else:
                self._objects[name if name else uid] = None
        returnValue(objectResource)


    @classproperty
    def _resourceNameForUIDQuery(cls): #@NoSelf
        """
        DAL query to retrieve the resource name for an object resource based on
        its UID column.
        """
        obj = cls._objectSchema
        return Select(
            [obj.RESOURCE_NAME], From=obj,
            Where=(obj.UID == Parameter("uid")
                  ).And(obj.PARENT_RESOURCE_ID == Parameter("resourceID")))


    @inlineCallbacks
    def resourceNameForUID(self, uid):
        try:
            resource = self._objects[uid]
            returnValue(resource.name() if resource else None)
        except KeyError:
            pass
        rows = yield self._resourceNameForUIDQuery.on(
            self._txn, uid=uid, resourceID=self._resourceID)
        if rows:
            returnValue(rows[0][0])
        else:
            self._objects[uid] = None
            returnValue(None)


    @classproperty
    def _resourceUIDForNameQuery(cls): #@NoSelf
        """
        DAL query to retrieve the UID for an object resource based on its
        resource name column.
        """
        obj = cls._objectSchema
        return Select(
            [obj.UID], From=obj,
            Where=(obj.UID == Parameter("name")
                  ).And(obj.PARENT_RESOURCE_ID == Parameter("resourceID")))


    @inlineCallbacks
    def resourceUIDForName(self, name):
        try:
            resource = self._objects[name]
            returnValue(resource.uid() if resource else None)
        except KeyError:
            pass
        rows = yield self._resourceUIDForNameQuery.on(
            self._txn, name=name, resourceID=self._resourceID)
        if rows:
            returnValue(rows[0][0])
        else:
            self._objects[name] = None
            returnValue(None)


    @inlineCallbacks
    def createObjectResourceWithName(self, name, component, metadata=None):
        """
        Create a new resource with component data and optional metadata. We
        create the python object using the metadata then create the actual store
        object with setComponent.
        """
        if name in self._objects:
            if self._objects[name]:
                raise ObjectResourceNameAlreadyExistsError()

        objectResource = (
            yield self._objectResourceClass.create(self, name, component,
                                                   metadata)
        )
        self._objects[objectResource.name()] = objectResource
        self._objects[objectResource.uid()] = objectResource

        # Note: create triggers a notification when the component is set, so we
        # don't need to call notify() here like we do for object removal.
        returnValue(objectResource)


    @inlineCallbacks
    def removeObjectResourceWithName(self, name):
        
        child = (yield self.objectResourceWithName(name))
        if child is None:
            raise NoSuchObjectResourceError
        yield self._removeObjectResource(child)


    @inlineCallbacks
    def removeObjectResourceWithUID(self, uid):
        
        child = (yield self.objectResourceWithUID(uid))
        if child is None:
            raise NoSuchObjectResourceError
        yield self._removeObjectResource(child)

    @inlineCallbacks
    def _removeObjectResource(self, child):
        name = child.name()
        uid = child.uid()
        try:
            yield child.remove()
        finally:        
            self._objects.pop(name, None)
            self._objects.pop(uid, None)
            yield self._deleteRevision(name)
            yield self.notifyChanged()

    @classproperty
    def _moveParentUpdateQuery(cls): #@NoSelf
        """
        DAL query to update a child to be in a new parent.
        """
        obj = cls._objectSchema
        return Update(
            {obj.PARENT_RESOURCE_ID: Parameter("newParentID")},
            Where=obj.RESOURCE_ID == Parameter("resourceID")
        )

    def _movedObjectResource(self, child, newparent):
        """
        Method that subclasses can override to do an extra DB adjustments when a resource
        is moved.
        """
        return succeed(True)

    @inlineCallbacks
    def moveObjectResource(self, child, newparent):
        """
        Move a child of this collection into another collection without actually removing/re-inserting the data.
        Make sure sync and cache details for both collections are updated.
        
        TODO: check that the resource name does not exist in the new parent, or that the UID
        does not exist there too.

        @param child: the child resource to move
        @type child: L{CommonObjectResource}
        @param newparent: the parent to move to
        @type newparent: L{CommonHomeChild}
        """

        name = child.name()
        uid = child.uid()

        # Clean this collections cache and signal sync change
        self._objects.pop(name, None)
        self._objects.pop(uid, None)
        self._objects.pop(child._resourceID, None)
        yield self._deleteRevision(name)
        yield self.notifyChanged()
        
        # Adjust the child to be a child of the new parent and update ancillary tables
        yield self._moveParentUpdateQuery.on(
            self._txn,
            newParentID=newparent._resourceID,
            resourceID=child._resourceID
        )
        yield self._movedObjectResource(child, newparent)
        child._parentCollection = newparent

        # Signal sync change on new collection
        yield newparent._insertRevision(name)
        yield newparent.notifyChanged()

    def objectResourcesHaveProperties(self):
        return False


    @inlineCallbacks
    def _loadPropertyStore(self, props=None):
        if props is None:
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


    # IDataStoreObject
    def contentType(self):
        raise NotImplementedError()


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return datetimeMktime(parseSQLTimestamp(self._created)) if self._created else None


    def modified(self):
        return datetimeMktime(parseSQLTimestamp(self._modified)) if self._modified else None


    def addNotifier(self, notifier):
        if self._notifiers is None:
            self._notifiers = ()
        self._notifiers += (notifier,)
 
    def notifierID(self, label="default"):
        if self._notifiers:
            return self._notifiers[0].getID(label)
        else:
            return None

    @inlineCallbacks
    def nodeName(self, label="default"):
        if self._notifiers:
            for notifier in self._notifiers:
                name = (yield notifier.nodeName(label=label))
                if name is not None:
                    returnValue(name)
        else:
            returnValue(None)

    @classproperty
    def _lockLastModifiedQuery(cls): #@NoSelf
        schema = cls._homeChildMetaDataSchema
        return Select(
            From=schema,
            Where=schema.RESOURCE_ID == Parameter("resourceID"),
            ForUpdate=True,
            NoWait=True
        )

    @classproperty
    def _changeLastModifiedQuery(cls): #@NoSelf
        schema = cls._homeChildMetaDataSchema
        return Update({schema.MODIFIED: utcNowSQL},
                      Where=schema.RESOURCE_ID == Parameter("resourceID"),
                      Return=schema.MODIFIED)

    @inlineCallbacks
    def bumpModified(self):
        """
        Bump the MODIFIED value. A possible deadlock could happen here if two or more
        simultaneous changes are happening. In that case it is OK for the MODIFIED change
        to fail so long as at least one works. We will use SAVEPOINT logic to handle
        ignoring the deadlock error. We use SELECT FOR UPDATE NOWAIT to ensure we do not
        delay the transaction whilst waiting for deadlock detection to kick in.
        """

        @inlineCallbacks
        def _bumpModified(subtxn):
            yield self._lockLastModifiedQuery.on(subtxn, resourceID=self._resourceID)
            result = (yield self._changeLastModifiedQuery.on(subtxn, resourceID=self._resourceID))
            returnValue(result)
            
        try:
            self._modified = (yield self._txn.subtransaction(_bumpModified, retries=0, failureOK=True))[0][0]
        except AllRetriesFailed:
            log.debug("CommonHomeChild.bumpModified failed")
        
    @inlineCallbacks
    def notifyChanged(self):
        """
        Trigger a notification of a change
        """
        
        # Update modified if object still exists
        if self._resourceID:
            yield self.bumpModified()

        # Only send one set of change notifications per transaction
        if self._notifiers and not self._txn.isNotifiedAlready(self):
            for notifier in self._notifiers:
                self._txn.postCommit(notifier.notify)
            self._txn.notificationAddedForObject(self)



class CommonObjectResource(LoggingMixIn, FancyEqMixin):
    """
    Base class for object resources.
    """

    compareAttributes = (
        "_name",
        "_parentCollection",
    )

    _objectTable = None

    _objectSchema = None

    def __init__(self, parent, name, uid, resourceID=None, metadata=None):
        self._parentCollection = parent
        self._resourceID = resourceID
        self._name = name
        self._uid = uid
        self._md5 = None
        self._size = None
        self._created = None
        self._modified = None
        self._objectText = None
        
        self._locked = False


    @classproperty
    def _allColumnsWithParent(cls): #@NoSelf
        obj = cls._objectSchema
        return Select(cls._allColumns, From=obj,
                      Where=obj.PARENT_RESOURCE_ID == Parameter("parentID"))


    @classmethod
    @inlineCallbacks
    def loadAllObjects(cls, parent):
        """
        Load all child objects and return a list of them. This must create the
        child classes and initialize them using "batched" SQL operations to keep
        this constant wrt the number of children. This is an optimization for
        Depth:1 operations on the collection.
        """

        results = []

        # Load from the main table first
        dataRows = yield cls._allColumnsWithParent.on(
            parent._txn, parentID=parent._resourceID)

        if dataRows:
            # Get property stores for all these child resources (if any found)
            if parent.objectResourcesHaveProperties():
                propertyStores =(yield PropertyStore.forMultipleResources(
                    parent._home.uid(),
                    parent._txn,
                    cls._objectSchema.RESOURCE_ID,
                    cls._objectSchema.PARENT_RESOURCE_ID,
                    parent._resourceID
                ))
            else:
                propertyStores = {}

        # Create the actual objects merging in properties
        for row in dataRows:
            child = cls(parent, "", None)
            child._initFromRow(tuple(row))
            yield child._loadPropertyStore(
                props=propertyStores.get(child._resourceID, None)
            )
            results.append(child)

        returnValue(results)


    @classmethod
    def objectWithName(cls, parent, name, uid):
        objectResource = cls(parent, name, uid, None)
        return objectResource.initFromStore()

    @classmethod
    def objectWithID(cls, parent, resourceID):
        objectResource = cls(parent, None, None, resourceID)
        return objectResource.initFromStore()

    @classmethod
    @inlineCallbacks
    def create(cls, parent, name, component, metadata):

        child = (yield cls.objectWithName(parent, name, None))
        if child:
            raise ObjectResourceNameAlreadyExistsError(name)

        if name.startswith("."):
            raise ObjectResourceNameNotAllowedError(name)
        
        objectResource = cls(parent, name, None, None, metadata=metadata)
        yield objectResource.setComponent(component, inserting=True)
        yield objectResource._loadPropertyStore(created=True)

        # Note: setComponent triggers a notification, so we don't need to
        # call notify( ) here like we do for object removal.
        
        returnValue(objectResource)


    @classmethod
    def _allWithParentAnd(cls, column, paramName):
        """
        DAL query for all columns where PARENT_RESOURCE_ID matches a parentID
        parameter and a given instance column matches a given parameter name.
        """
        return Select(
            cls._allColumns, From=cls._objectSchema,
            Where=(column == Parameter(paramName)).And(
                cls._objectSchema.PARENT_RESOURCE_ID == Parameter("parentID"))
        )


    @classproperty
    def _allWithParentAndName(cls): #@NoSelf
        return cls._allWithParentAnd(cls._objectSchema.RESOURCE_NAME, "name")


    @classproperty
    def _allWithParentAndUID(cls): #@NoSelf
        return cls._allWithParentAnd(cls._objectSchema.UID, "uid")


    @classproperty
    def _allWithParentAndID(cls): #@NoSelf
        return cls._allWithParentAnd(cls._objectSchema.RESOURCE_ID,
                                     "resourceID")


    @inlineCallbacks
    def initFromStore(self):
        """
        Initialise this object from the store. We read in and cache all the
        extra metadata from the DB to avoid having to do DB queries for those
        individually later. Either the name or uid is present, so we have to
        tweak the query accordingly.

        @return: L{self} if object exists in the DB, else C{None}
        """

        if self._name:
            rows = yield self._allWithParentAndName.on(
                self._txn, name=self._name,
                parentID=self._parentCollection._resourceID)
        elif self._uid:
            rows = yield self._allWithParentAndUID.on(
                self._txn, uid=self._uid,
                parentID=self._parentCollection._resourceID)
        elif self._resourceID:
            rows = yield self._allWithParentAndID.on(
                self._txn, resourceID=self._resourceID,
                parentID=self._parentCollection._resourceID)
        if rows:
            self._initFromRow(tuple(rows[0]))
            yield self._loadPropertyStore()
            returnValue(self)
        else:
            returnValue(None)


    @classproperty
    def _allColumns(cls): #@NoSelf
        """
        Full set of columns in the object table that need to be loaded to
        initialize the object resource state.
        """
        obj = cls._objectSchema
        return [
            obj.RESOURCE_ID,
            obj.RESOURCE_NAME,
            obj.UID,
            obj.MD5,
            Len(obj.TEXT),
            obj.CREATED,
            obj.MODIFIED
        ]


    def _initFromRow(self, row):
        """
        Given a select result using the columns from L{_allColumns}, initialize
        the object resource state.
        """
        (self._resourceID,
         self._name,
         self._uid,
         self._md5,
         self._size,
         self._created,
         self._modified,) = tuple(row)


    @inlineCallbacks
    def _loadPropertyStore(self, props=None, created=False):
        if props is None:
            if self._parentCollection.objectResourcesHaveProperties():
                props = yield PropertyStore.load(
                    self._parentCollection.ownerHome().uid(),
                    self._txn,
                    self._resourceID,
                    created=created
                )
            else:
                props = NonePropertyStore(self._parentCollection.ownerHome().uid())
        self.initPropertyStore(props)
        self._propertyStore = props

    
    def properties(self):
        return self._propertyStore


    def initPropertyStore(self, props):
        """
        A hook for subclasses to override in order to set up their property
        store after it's been created.

        @param props: the L{PropertyStore} from C{properties()}.
        """

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._resourceID)


    @property
    def _txn(self):
        return self._parentCollection._txn


    def transaction(self):
        return self._parentCollection._txn


    @classmethod
    def _selectForUpdateQuery(cls, nowait): #@NoSelf
        """
        DAL statement to lock a L{CommonObjectResource} by its resource ID.
        """
        return Select(From=cls._objectSchema, ForUpdate=True, NoWait=nowait, Where=cls._objectSchema.RESOURCE_ID == Parameter("resourceID"))


    @inlineCallbacks
    def lock(self, wait=True, txn=None):
        """
        Attempt to obtain a row lock on the object resource. 'wait' determines whether the DB call will
        block on any existing lock held by someone else. Lock will remain until
        transaction is complete, or fail if resource is missing, or it is already locked
        and wait=False is used. Occasionally we need to lock via a separate transaction so we
        pass that in too.

        @param wait: whether or not to wait on someone else's lock
        @type wait: C{bool}
        @param txn: alternative transaction to use
        @type txn: L{CommonStoreTransaction}
        
        @raise: L{NoSuchObjectResourceError} if resource does not exist, other L{Exception}
                if already locked and NOWAIT is used.
        """
        
        txn = txn if txn is not None else self._txn
        yield self._selectForUpdateQuery(not wait).on(txn, NoSuchObjectResourceError, resourceID=self._resourceID)
        self._locked = True


    def setComponent(self, component, inserting=False):
        raise NotImplementedError


    def component(self):
        raise NotImplementedError


    @inlineCallbacks
    def componentType(self):
        returnValue((yield self.component()).mainType())

    @classproperty
    def _deleteQuery(cls): #@NoSelf
        """
        DAL statement to delete a L{CommonObjectResource} by its resource ID.
        """
        return Delete(cls._objectSchema, Where=cls._objectSchema.RESOURCE_ID == Parameter("resourceID"))


    @inlineCallbacks
    def remove(self):
        yield self._deleteQuery.on(self._txn, NoSuchObjectResourceError,
                                   resourceID=self._resourceID)
        self.properties()._removeResource()

        # Set to non-existent state
        self._resourceID = None
        self._name = None
        self._uid = None
        self._md5 = None
        self._size = None
        self._created = None
        self._modified = None
        self._objectText = None

    def uid(self):
        return self._uid


    def name(self):
        return self._name



    # IDataStoreObject
    def contentType(self):
        raise NotImplementedError()


    def md5(self):
        return self._md5


    def size(self):
        return self._size


    def created(self):
        return datetimeMktime(parseSQLTimestamp(self._created))


    def modified(self):
        return datetimeMktime(parseSQLTimestamp(self._modified))


    @classproperty
    def _textByIDQuery(cls): #@NoSelf
        """
        DAL query to load iCalendar/vCard text via an object's resource ID.
        """
        obj = cls._objectSchema
        return Select([obj.TEXT], From=obj,
                      Where=obj.RESOURCE_ID == Parameter("resourceID"))


    @inlineCallbacks
    def _text(self):
        if self._objectText is None:
            texts = (
                yield self._textByIDQuery.on(self._txn,
                                             resourceID=self._resourceID)
            )
            if texts:
                text = texts[0][0]
                self._objectText = text
                returnValue(text)
            else:
                raise ConcurrentModification()
        else:
            returnValue(self._objectText)



class NotificationCollection(LoggingMixIn, FancyEqMixin, _SharedSyncLogic):

    implements(INotificationCollection)

    compareAttributes = (
        "_uid",
        "_resourceID",
    )

    _revisionsTable = NOTIFICATION_OBJECT_REVISIONS_TABLE
    _revisionsSchema = schema.NOTIFICATION_OBJECT_REVISIONS
    _homeSchema = schema.NOTIFICATION_HOME


    def __init__(self, txn, uid, resourceID):

        self._txn = txn
        self._uid = uid
        self._resourceID = resourceID
        self._notifications = {}
        self._notificationNames = None
        self._syncTokenRevision = None

        # Make sure we have push notifications setup to push on this collection
        # as well as the home it is in
        if txn._notifierFactory:
            childID = "%s/%s" % (uid, "notification")
            notifier = txn._notifierFactory.newNotifier(
                label="collection",
                id=childID,
                prefix=txn._homeClass[txn._primaryHomeType]._notifierPrefix
            )
            notifier.addID(id=uid)
            notifiers = (notifier,)
        else:
            notifiers = None
        self._notifiers = notifiers


    _resourceIDFromUIDQuery = Select(
        [_homeSchema.RESOURCE_ID], From=_homeSchema,
        Where=_homeSchema.OWNER_UID == Parameter("uid"))


    _provisionNewNotificationsQuery = Insert(
        {_homeSchema.OWNER_UID: Parameter("uid")},
        Return=_homeSchema.RESOURCE_ID
    )

    @property
    def _home(self):
        """
        L{NotificationCollection} serves as its own C{_home} for the purposes of
        working with L{_SharedSyncLogic}.
        """
        return self


    @classmethod
    @inlineCallbacks
    def notificationsWithUID(cls, txn, uid):
        rows = yield cls._resourceIDFromUIDQuery.on(txn, uid=uid)

        if rows:
            resourceID = rows[0][0]
            created = False
        else:
            # Use savepoint so we can do a partial rollback if there is a race condition
            # where this row has already been inserted
            savepoint = SavepointAction("notificationsWithUID")
            yield savepoint.acquire(txn)

            try:
                resourceID = str((
                    yield cls._provisionNewNotificationsQuery.on(txn, uid=uid)
                )[0][0])
            except Exception: # FIXME: Really want to trap the pg.DatabaseError but in a non-DB specific manner
                yield savepoint.rollback(txn)
                
                # Retry the query - row may exist now, if not re-raise
                rows = yield cls._resourceIDFromUIDQuery.on(txn, uid=uid)
                if rows:
                    resourceID = rows[0][0]
                    created = False
                else:
                    raise
            else:
                created = True
                yield savepoint.release(txn)
                
        collection = cls(txn, uid, resourceID)
        yield collection._loadPropertyStore()
        if created:
            yield collection._initSyncToken()
        returnValue(collection)


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
        return "notification"


    def uid(self):
        return self._uid


    @inlineCallbacks
    def notificationObjects(self):
        results = (yield NotificationObject.loadAllObjects(self))
        for result in results:
            self._notifications[result.uid()] = result
        self._notificationNames = sorted([result.name() for result in results])
        returnValue(results)


    _notificationUIDsForHomeQuery = Select(
        [schema.NOTIFICATION.NOTIFICATION_UID], From=schema.NOTIFICATION,
        Where=schema.NOTIFICATION.NOTIFICATION_HOME_RESOURCE_ID ==
        Parameter("resourceID"))


    @inlineCallbacks
    def listNotificationObjects(self):
        if self._notificationNames is None:
            rows = yield self._notificationUIDsForHomeQuery.on(
                self._txn, resourceID=self._resourceID)
            self._notificationNames = sorted([row[0] for row in rows])
        returnValue(self._notificationNames)


    def _nameToUID(self, name):
        """
        Based on the file-backed implementation, the 'name' is just uid +
        ".xml".
        """
        return name.rsplit(".", 1)[0]


    def notificationObjectWithName(self, name):
        return self.notificationObjectWithUID(self._nameToUID(name))


    @memoizedKey("uid", "_notifications")
    @inlineCallbacks
    def notificationObjectWithUID(self, uid):
        """
        Create an empty notification object first then have it initialize itself
        from the store.
        """
        no = NotificationObject(self, uid)
        no = (yield no.initFromStore())
        returnValue(no)


    @inlineCallbacks
    def writeNotificationObject(self, uid, xmltype, xmldata):

        inserting = False
        notificationObject = yield self.notificationObjectWithUID(uid)
        if notificationObject is None:
            notificationObject = NotificationObject(self, uid)
            inserting = True
        yield notificationObject.setData(uid, xmltype, xmldata, inserting=inserting)
        if inserting:
            yield self._insertRevision("%s.xml" % (uid,))
        else:
            yield self._updateRevision("%s.xml" % (uid,))


    def removeNotificationObjectWithName(self, name):
        return self.removeNotificationObjectWithUID(self._nameToUID(name))


    _removeByUIDQuery = Delete(
        From=schema.NOTIFICATION,
        Where=(schema.NOTIFICATION.NOTIFICATION_UID == Parameter("uid")).And(
            schema.NOTIFICATION.NOTIFICATION_HOME_RESOURCE_ID
            == Parameter("resourceID")))


    @inlineCallbacks
    def removeNotificationObjectWithUID(self, uid):
        yield self._removeByUIDQuery.on(
            self._txn, uid=uid, resourceID=self._resourceID)
        self._notifications.pop(uid, None)
        yield self._deleteRevision("%s.xml" % (uid,))


    _initSyncTokenQuery = Insert(
        {
            _revisionsSchema.HOME_RESOURCE_ID : Parameter("resourceID"),
            _revisionsSchema.RESOURCE_NAME    : None,
            _revisionsSchema.REVISION         : schema.REVISION_SEQ,
            _revisionsSchema.DELETED          : False
        }, Return=_revisionsSchema.REVISION
    )


    @inlineCallbacks
    def _initSyncToken(self):
        self._syncTokenRevision = (yield self._initSyncTokenQuery.on(
            self._txn, resourceID=self._resourceID))[0][0]


    _syncTokenQuery = Select(
        [Max(_revisionsSchema.REVISION)], From=_revisionsSchema,
        Where=_revisionsSchema.HOME_RESOURCE_ID == Parameter("resourceID")
    )


    @inlineCallbacks
    def syncToken(self):
        if self._syncTokenRevision is None:
            self._syncTokenRevision = (
                yield self._syncTokenQuery.on(
                    self._txn, resourceID=self._resourceID)
            )[0][0]
        returnValue("%s_%s" % (self._resourceID, self._syncTokenRevision))


    def properties(self):
        return self._propertyStore


    def addNotifier(self, notifier):
        if self._notifiers is None:
            self._notifiers = ()
        self._notifiers += (notifier,)
 
    def notifierID(self, label="default"):
        if self._notifiers:
            return self._notifiers[0].getID(label)
        else:
            return None


    @inlineCallbacks
    def nodeName(self, label="default"):
        if self._notifiers:
            for notifier in self._notifiers:
                name = (yield notifier.nodeName(label=label))
                if name is not None:
                    returnValue(name)
        else:
            returnValue(None)


    def notifyChanged(self):
        """
        Trigger a notification of a change
        """

        # Only send one set of change notifications per transaction
        if self._notifiers and not self._txn.isNotifiedAlready(self):
            for notifier in self._notifiers:
                self._txn.postCommit(notifier.notify)
            self._txn.notificationAddedForObject(self)


    @classproperty
    def _completelyNewRevisionQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Insert({rev.HOME_RESOURCE_ID: Parameter("homeID"),
                       # rev.RESOURCE_ID: Parameter("resourceID"),
                       rev.RESOURCE_NAME: Parameter("name"),
                       rev.REVISION: schema.REVISION_SEQ,
                       rev.DELETED: False},
                      Return=rev.REVISION)


    def _maybeNotify(self):
        """
        Emit a push notification after C{_changeRevision}.
        """
        self.notifyChanged()



class NotificationObject(LoggingMixIn, FancyEqMixin):

    implements(INotificationObject)

    compareAttributes = (
        "_resourceID",
        "_home",
    )

    _objectSchema = schema.NOTIFICATION

    def __init__(self, home, uid):
        self._home = home
        self._resourceID = None
        self._uid = uid
        self._md5 = None
        self._size = None
        self._created = None
        self._modified = None
        self._xmlType = None
        self._objectText = None


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._resourceID)


    @classproperty
    def _allColumnsByHomeIDQuery(cls): #@NoSelf
        """
        DAL query to load all columns by home ID.
        """
        obj = cls._objectSchema
        return Select([obj.RESOURCE_ID, obj.NOTIFICATION_UID, obj.MD5,
                       Len(obj.XML_DATA), obj.XML_TYPE, obj.CREATED,
                       obj.MODIFIED],
                      From=obj,
                      Where=(obj.NOTIFICATION_HOME_RESOURCE_ID == Parameter(
                          "homeID")))


    @classmethod
    @inlineCallbacks
    def loadAllObjects(cls, parent):
        """
        Load all child objects and return a list of them. This must create the
        child classes and initialize them using "batched" SQL operations to keep
        this constant wrt the number of children. This is an optimization for
        Depth:1 operations on the collection.
        """

        results = []

        # Load from the main table first
        dataRows = (
            yield cls._allColumnsByHomeIDQuery.on(parent._txn,
                                                  homeID=parent._resourceID))

        if dataRows:
            # Get property stores for all these child resources (if any found)
            propertyStores =(yield PropertyStore.forMultipleResources(
                parent.uid(),
                parent._txn,
                schema.NOTIFICATION.RESOURCE_ID,
                schema.NOTIFICATION.NOTIFICATION_HOME_RESOURCE_ID,
                parent._resourceID,
            ))

        # Create the actual objects merging in properties
        for row in dataRows:
            child = cls(parent, None)
            (child._resourceID,
             child._uid,
             child._md5,
             child._size,
             child._xmlType,
             child._created,
             child._modified,) = tuple(row)
            child._loadPropertyStore(
                props=propertyStores.get(child._resourceID, None)
            )
            results.append(child)

        returnValue(results)


    @classproperty
    def _oneNotificationQuery(cls): #@NoSelf
        no = cls._objectSchema
        return Select(
            [
                no.RESOURCE_ID,
                no.MD5,
                Len(no.XML_DATA),
                no.XML_TYPE,
                no.CREATED,
                no.MODIFIED
            ],
            From=no,
            Where=(no.NOTIFICATION_UID ==
                   Parameter("uid")).And(no.NOTIFICATION_HOME_RESOURCE_ID ==
                                         Parameter("homeID")))


    @inlineCallbacks
    def initFromStore(self):
        """
        Initialise this object from the store, based on its UID and home
        resource ID. We read in and cache all the extra metadata from the DB to
        avoid having to do DB queries for those individually later.

        @return: L{self} if object exists in the DB, else C{None}
        """
        rows = (yield self._oneNotificationQuery.on(
            self._txn, uid=self._uid, homeID=self._home._resourceID))
        if rows:
            (self._resourceID,
             self._md5,
             self._size,
             self._xmlType,
             self._created,
             self._modified,) = tuple(rows[0])
            self._loadPropertyStore()
            returnValue(self)
        else:
            returnValue(None)


    def _loadPropertyStore(self, props=None, created=False):
        if props is None:
            props = NonePropertyStore(self._home.uid())
        self._propertyStore = props


    def properties(self):
        return self._propertyStore


    @property
    def _txn(self):
        return self._home._txn


    def notificationCollection(self):
        return self._home


    def uid(self):
        return self._uid


    def name(self):
        return self.uid() + ".xml"


    @classproperty
    def _newNotificationQuery(cls): #@NoSelf
        no = cls._objectSchema
        return Insert(
            {
                no.NOTIFICATION_HOME_RESOURCE_ID: Parameter("homeID"),
                no.NOTIFICATION_UID: Parameter("uid"),
                no.XML_TYPE: Parameter("xmlType"),
                no.XML_DATA: Parameter("xmlData"),
                no.MD5: Parameter("md5"),
            },
            Return=[no.RESOURCE_ID, no.CREATED, no.MODIFIED]
        )


    @classproperty
    def _updateNotificationQuery(cls): #@NoSelf
        no = cls._objectSchema
        return Update(
            {
                no.XML_TYPE: Parameter("xmlType"),
                no.XML_DATA: Parameter("xmlData"),
                no.MD5: Parameter("md5"),
            },
            Where=(no.NOTIFICATION_HOME_RESOURCE_ID == Parameter("homeID")).And(
                no.NOTIFICATION_UID == Parameter("uid")),
            Return=no.MODIFIED
        )


    @inlineCallbacks
    def setData(self, uid, xmltype, xmldata, inserting=False):
        """
        Set the object resource data and update and cached metadata.
        """

        self._xmlType = NotificationType(xmltype)
        self._md5 = hashlib.md5(xmldata).hexdigest()
        self._size = len(xmldata)
        if inserting:
            rows = yield self._newNotificationQuery.on(
                self._txn, homeID=self._home._resourceID, uid=uid,
                xmlType=self._xmlType.toxml(), xmlData=xmldata, md5=self._md5
            )
            self._resourceID, self._created, self._modified = rows[0]
            self._loadPropertyStore()
        else:
            rows = yield self._updateNotificationQuery.on(
                self._txn, homeID=self._home._resourceID, uid=uid,
                xmlType=self._xmlType.toxml(), xmlData=xmldata, md5=self._md5
            )
            self._modified = rows[0][0]
        self._objectText = xmldata


    _xmlDataFromID = Select(
        [_objectSchema.XML_DATA], From=_objectSchema,
        Where=_objectSchema.RESOURCE_ID == Parameter("resourceID"))


    @inlineCallbacks
    def xmldata(self):
        if self._objectText is None:
            self._objectText = (
                yield self._xmlDataFromID.on(
                    self._txn, resourceID=self._resourceID))[0][0]
        returnValue(self._objectText)


    def contentType(self):
        """
        The content type of NotificationObjects is text/xml.
        """
        return MimeType.fromString("text/xml")


    def md5(self):
        return self._md5


    def size(self):
        return self._size

    def xmlType(self):
        # NB This is the NotificationType property element
        if isinstance(self._xmlType, str):
            # Convert into NotificationType property element
            self._xmlType = WebDAVDocument.fromString(self._xmlType).root_element

        return self._xmlType

    def created(self):
        return datetimeMktime(parseSQLTimestamp(self._created))


    def modified(self):
        return datetimeMktime(parseSQLTimestamp(self._modified))



