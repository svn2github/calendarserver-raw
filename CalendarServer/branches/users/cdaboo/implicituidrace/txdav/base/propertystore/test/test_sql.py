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
Tests for txdav.caldav.datastore.postgres, mostly based on
L{txdav.caldav.datastore.test.common}.
"""

from twisted.internet.defer import inlineCallbacks, returnValue

from txdav.common.datastore.test.util import buildStore, StubNotifierFactory

from txdav.base.propertystore.test.base import (
    PropertyStoreTest, propertyName, propertyValue)

from twisted.internet.defer import gatherResults
from twext.enterprise.ienterprise import AlreadyFinishedError

try:
    from txdav.base.propertystore.sql import PropertyStore
except ImportError, e:
    # XXX: when could this ever fail?
    PropertyStore = None
    importErrorMessage = str(e)



class PropertyStoreTest(PropertyStoreTest):


    @inlineCallbacks
    def setUp(self):
        self.notifierFactory = StubNotifierFactory()
        self.store = yield buildStore(self, self.notifierFactory)
        self.addCleanup(self.maybeCommitLast)
        self._transaction = self.store.newTransaction()
        self.propertyStore = self.propertyStore1 = yield PropertyStore.load(
            "user01", self._transaction, 1
        )
        self.propertyStore2 = yield PropertyStore.load("user01", self._transaction, 1)
        self.propertyStore2._setPerUserUID("user02")


    @inlineCallbacks
    def maybeCommitLast(self):
        if hasattr(self, "_transaction"):
            result = yield self._transaction.commit()
            delattr(self, "_transaction")
        else:
            result = None
        self.propertyStore = self.propertyStore1 = self.propertyStore2 = None
        returnValue(result)


    @inlineCallbacks
    def _changed(self, store):
        if hasattr(self, "_transaction"):
            yield self._transaction.commit()
            delattr(self, "_transaction")
        self._transaction = self.store.newTransaction()

        store = self.propertyStore1
        self.propertyStore = self.propertyStore1 = yield PropertyStore.load(
            "user01", self._transaction, 1
        )
        self.propertyStore1._shadowableKeys = store._shadowableKeys
        self.propertyStore1._globalKeys = store._globalKeys

        store = self.propertyStore2
        self.propertyStore2 = yield PropertyStore.load("user01", self._transaction, 1)
        self.propertyStore2._setPerUserUID("user02")
        self.propertyStore2._shadowableKeys = store._shadowableKeys
        self.propertyStore2._globalKeys = store._globalKeys


    @inlineCallbacks
    def _abort(self, store):
        if hasattr(self, "_transaction"):
            yield self._transaction.abort()
            delattr(self, "_transaction")

        self._transaction = self.store.newTransaction()

        store = self.propertyStore1
        self.propertyStore = self.propertyStore1 = yield PropertyStore.load(
            "user01", self._transaction, 1
        )
        self.propertyStore1._shadowableKeys = store._shadowableKeys
        self.propertyStore1._globalKeys = store._globalKeys

        store = self.propertyStore2
        self.propertyStore2 = yield PropertyStore.load("user01", self._transaction, 1)
        self.propertyStore2._setPerUserUID("user02")
        self.propertyStore2._shadowableKeys = store._shadowableKeys
        self.propertyStore2._globalKeys = store._globalKeys


    @inlineCallbacks
    def test_concurrentInsertion(self):
        """
        When two property stores set the same value, both should succeed, and
        update the cache.  Whoever wins the race (i.e. updates last) will set
        the last property value.
        """
        pname = propertyName("concurrent")
        pval1 = propertyValue("alpha")
        pval2 = propertyValue("beta")
        concurrentTransaction = self.store.newTransaction()
        @inlineCallbacks
        def maybeAbortIt():
            try:
                yield concurrentTransaction.abort()
            except AlreadyFinishedError:
                pass
        self.addCleanup(maybeAbortIt)
        concurrentPropertyStore = yield PropertyStore.load(
            "user01", concurrentTransaction, 1
        )
        concurrentPropertyStore[pname] = pval1
        race = []
        def tiebreaker(label):
            # Let's not get into the business of figuring out who the database
            # concurrency rules are suppsoed to pick; it might differ.  We just
            # take the answer we're given for who gets to be the final writer,
            # and make sure that matches the property read in the next
            # transaction.
            def breaktie(result):
                race.append(label)
                return result
            return breaktie
        a = concurrentTransaction.commit().addCallback(tiebreaker('a'))
        self.propertyStore[pname] = pval2
        b = self._transaction.commit().addCallback(tiebreaker('b'))
        del self._transaction
        self.assertEquals((yield gatherResults([a, b])), [None, None])
        yield self._abort(self.propertyStore)
        winner = {'a': pval1,
                  'b': pval2}[race[-1]]
        self.assertEquals(self.propertyStore[pname], winner)



if PropertyStore is None:
    PropertyStoreTest.skip = importErrorMessage


