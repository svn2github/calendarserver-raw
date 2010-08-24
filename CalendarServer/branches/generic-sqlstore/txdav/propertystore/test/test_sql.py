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
Tests for txcaldav.calendarstore.postgres, mostly based on
L{txcaldav.calendarstore.test.common}.
"""


from twext.python.filepath import CachingFilePath

from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks, succeed
from twisted.internet.task import deferLater
from twisted.python import log

from txcaldav.calendarstore.postgres import v1_schema
from txcaldav.calendarstore.test.common import StubNotifierFactory

from txdav.common.datastore.sql import CommonDataStore
from txdav.datastore.subpostgres import PostgresService
from txdav.propertystore.base import PropertyName
from txdav.propertystore.test import base

try:
    from txdav.propertystore.sql import PropertyStore
except ImportError, e:
    PropertyStore = None
    importErrorMessage = str(e)



class StoreBuilder(object):
    """
    Test-fixture-builder which can construct a PostgresStore.
    """
    sharedService = None
    currentTestID = None

    SHARED_DB_PATH = "../_test_postgres_db"

    def buildStore(self, testCase, notifierFactory):
        """
        Do the necessary work to build a store for a particular test case.

        @return: a L{Deferred} which fires with an L{IDataStore}.
        """
        currentTestID = testCase.id()
        dbRoot = CachingFilePath(self.SHARED_DB_PATH)
        if self.sharedService is None:
            ready = Deferred()
            def getReady(connectionFactory):
                attachmentRoot = dbRoot.child("attachments")
                try:
                    attachmentRoot.createDirectory()
                except OSError:
                    pass
                try:
                    self.store = CommonDataStore(
                        lambda label=None: connectionFactory(
                            label or currentTestID
                        ),
                        notifierFactory,
                        attachmentRoot
                    )
                except:
                    ready.errback()
                    raise
                else:
                    self.cleanDatabase(testCase)
                    ready.callback(self.store)
                return self.store
            self.sharedService = PostgresService(
                dbRoot, getReady, v1_schema, "caldav", resetSchema=True,
                testMode=True
            )
            self.sharedService.startService()
            def startStopping():
                log.msg("Starting stopping.")
                self.sharedService.unpauseMonitor()
                return self.sharedService.stopService()
            reactor.addSystemEventTrigger(#@UndefinedVariable
                "before", "shutdown", startStopping)
            result = ready
        else:
            self.store.notifierFactory = notifierFactory
            self.cleanDatabase(testCase)
            result = succeed(self.store)

        def cleanUp():
            # FIXME: clean up any leaked connections and report them with an
            # immediate test failure.
            def stopit():
                self.sharedService.pauseMonitor()
            return deferLater(reactor, 0.1, stopit)
        testCase.addCleanup(cleanUp)
        return result


    def cleanDatabase(self, testCase):
        cleanupConn = self.store.connectionFactory(
            "%s schema-cleanup" % (testCase.id(),)
        )
        cursor = cleanupConn.cursor()
        tables = ['INVITE',
                  'RESOURCE_PROPERTY',
                  'ATTACHMENT',
                  'ADDRESSBOOK_OBJECT',
                  'CALENDAR_OBJECT',
                  'CALENDAR_BIND',
                  'ADDRESSBOOK_BIND',
                  'CALENDAR',
                  'ADDRESSBOOK',
                  'CALENDAR_HOME',
                  'ADDRESSBOOK_HOME',
                  'NOTIFICATION',
                  'NOTIFICATION_HOME']
        for table in tables:
            try:
                cursor.execute("delete from "+table)
            except:
                log.err()
        cleanupConn.commit()
        cleanupConn.close()



theStoreBuilder = StoreBuilder()
buildStore = theStoreBuilder.buildStore


class PropertyStoreTest(base.PropertyStoreTest):

    def _preTest(self):
        self._txn = self.store.newTransaction()
        self.propertyStore = self.propertyStore1 = PropertyStore(
            "user01", self._txn, 1
        )
        self.propertyStore2 = PropertyStore("user01", self._txn, 1)
        self.propertyStore2._setPerUserUID("user02")
        
        self.addCleanup(self._postTest)

    def _postTest(self):
        if hasattr(self, "_txn"):
            self._txn.commit()
            delattr(self, "_txn")
        self.propertyStore = self.propertyStore1 = self.propertyStore2 = None

    def _changed(self, store):
        if hasattr(self, "_txn"):
            self._txn.commit()
            delattr(self, "_txn")
        self._txn = self.store.newTransaction()
        self.propertyStore1._txn = self._txn
        self.propertyStore2._txn = self._txn

    def _abort(self, store):
        if hasattr(self, "_txn"):
            self._txn.abort()
            delattr(self, "_txn")

        self._txn = self.store.newTransaction()
        self.propertyStore1._txn = self._txn
        self.propertyStore2._txn = self._txn

    @inlineCallbacks
    def setUp(self):
        self.notifierFactory = StubNotifierFactory()
        self.store = yield buildStore(self, self.notifierFactory)

if PropertyStore is None:
    PropertyStoreTest.skip = importErrorMessage


def propertyName(name):
    return PropertyName("http://calendarserver.org/ns/test/", name)
