# -*- test-case-name: txcaldav.calendarstore.test.test_postgres -*-
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
from twext.python.log import Logger
from txdav.datastore.sql import memoized
from txcaldav.calendarstore.postgres import PostgresCalendarHome,\
    PostgresAddressBookHome, PostgresNotificationCollection

"""
SQL data store.
"""

__all__ = [
    "CommonDataStore",
    "CommonStoreTransaction",
]

from twistedcaldav.sharing import SharedCollectionRecord #@UnusedImport

from zope.interface.declarations import implements, directlyProvides

from twisted.application.service import Service

from txdav.idav import IDataStore, AlreadyFinishedError

from txcaldav.icalendarstore import ICalendarTransaction
from txcarddav.iaddressbookstore import IAddressBookTransaction


log = Logger()

ECALENDARTYPE = 0
EADDRESSBOOKTYPE = 1

class CommonDataStore(Service, object):

    implements(IDataStore)

    def __init__(self, connectionFactory, notifierFactory, attachmentsPath,
                 enableCalendars=True, enableAddressBooks=True):
        assert enableCalendars or enableAddressBooks

        self.connectionFactory = connectionFactory
        self.notifierFactory = notifierFactory
        self.attachmentsPath = attachmentsPath
        self.enableCalendars = enableCalendars
        self.enableAddressBooks = enableAddressBooks


    def newTransaction(self, label="unlabeled"):
        return CommonStoreTransaction(
            self,
            self.connectionFactory(),
            self.enableCalendars,
            self.enableAddressBooks, 
            self.notifierFactory,
            label
        )

class CommonStoreTransaction(object):
    """
    Transaction implementation for SQL database.
    """

    _homeClass = {}

    def __init__(self, store, connection, enableCalendars, enableAddressBooks, notifierFactory, label):

        self._store = store
        self._connection = connection
        self._cursor = connection.cursor()
        self._completed = False
        self._calendarHomes = {}
        self._addressbookHomes = {}
        self._notificationHomes = {}
        self._postCommitOperations = []
        self._notifierFactory = notifierFactory
        self._label = label

        extraInterfaces = []
        if enableCalendars:
            extraInterfaces.append(ICalendarTransaction)
        if enableAddressBooks:
            extraInterfaces.append(IAddressBookTransaction)
        directlyProvides(self, *extraInterfaces)

        CommonStoreTransaction._homeClass[ECALENDARTYPE] = PostgresCalendarHome
        CommonStoreTransaction._homeClass[EADDRESSBOOKTYPE] = PostgresAddressBookHome

    def store(self):
        return self._store


    def __repr__(self):
        return 'PG-TXN<%s>' % (self._label,)


    def execSQL(self, sql, args=[]):
        # print 'EXECUTE %s: %s' % (self._label, sql)
        self._cursor.execute(sql, args)
        if self._cursor.description:
            return self._cursor.fetchall()
        else:
            return None


    def __del__(self):
        if not self._completed:
            self._connection.rollback()
            self._connection.close()


    @memoized('uid', '_calendarHomes')
    def calendarHomeWithUID(self, uid, create=False):
        return self.homeWithUID(ECALENDARTYPE, uid, create=create)

    @memoized('uid', '_addressbookHomes')
    def addressbookHomeWithUID(self, uid, create=False):
        return self.homeWithUID(EADDRESSBOOKTYPE, uid, create=create)

    def homeWithUID(self, storeType, uid, create=False):
        
        if storeType == ECALENDARTYPE:
            homeTable = "CALENDAR_HOME"
        elif storeType == EADDRESSBOOKTYPE:
            homeTable = "ADDRESSBOOK_HOME"

        data = self.execSQL(
            "select RESOURCE_ID from %s where OWNER_UID = %%s" % (homeTable,),
            [uid]
        )
        if not data:
            if not create:
                return None
            self.execSQL(
                "insert into %s (OWNER_UID) values (%%s)" % (homeTable,),
                [uid]
            )
            home = self.homeWithUID(storeType, uid)
            home.createdHome()
            return home
        resid = data[0][0]

        if self._notifierFactory:
            notifier = self._notifierFactory.newNotifier(id=uid)
        else:
            notifier = None

        return self._homeClass[storeType](self, uid, resid, notifier)


    @memoized('uid', '_notificationHomes')
    def notificationsWithUID(self, uid):
        """
        Implement notificationsWithUID.
        """
        rows = self.execSQL(
            """
            select RESOURCE_ID from NOTIFICATION_HOME where
            OWNER_UID = %s
            """, [uid])
        if rows:
            [[resourceID]] = rows
        else:
            [[resourceID]] = self.execSQL("select nextval('RESOURCE_ID_SEQ')")
            resourceID = str(resourceID)
            self.execSQL(
                "insert into NOTIFICATION_HOME (RESOURCE_ID, OWNER_UID) "
                "values (%s, %s)", [resourceID, uid])
        return PostgresNotificationCollection(self, uid, resourceID)


    def abort(self):
        if not self._completed:
            # print 'ABORTING', self._label
            self._completed = True
            self._connection.rollback()
            self._connection.close()
        else:
            raise AlreadyFinishedError()


    def commit(self):
        if not self._completed:
            # print 'COMPLETING', self._label
            self._completed = True
            self._connection.commit()
            self._connection.close()
            for operation in self._postCommitOperations:
                operation()
        else:
            raise AlreadyFinishedError()


    def postCommit(self, operation):
        """
        Run things after 'commit.'
        """
        self._postCommitOperations.append(operation)
        # FIXME: implement.

