##
# Copyright (c) 2012 Apple Inc. All rights reserved.
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
Tests for L{twext.enterprise.queue}.
"""

from twext.enterprise.dal.syntax import SchemaSyntax
from twext.enterprise.dal.record import fromTable
from twext.enterprise.dal.test.test_parseschema import SchemaTestHelper

from twext.enterprise.queue import inTransaction, PeerConnectionPool, WorkItem

from twisted.trial.unittest import TestCase
from twisted.internet.defer import Deferred, inlineCallbacks, gatherResults

from twisted.application.service import Service, MultiService


from txdav.common.datastore.test.util import buildStore

class UtilityTests(TestCase):
    """
    Tests for supporting utilities.
    """

    def test_inTransactionSuccess(self):
        """
        L{inTransaction} invokes its C{transactionCreator} argument, and then
        returns a L{Deferred} which fires with the result of its C{operation}
        argument when it succeeds.
        """
        class faketxn(object):
            def __init__(self):
                self.commits = []
                self.aborts = []
            def commit(self):
                self.commits.append(Deferred())
                return self.commits[-1]
            def abort(self):
                self.aborts.append(Deferred())
                return self.aborts[-1]

        createdTxns = []
        def createTxn():
            createdTxns.append(faketxn())
            return createdTxns[-1]
        dfrs = []
        def operation(t):
            self.assertIdentical(t, createdTxns[-1])
            dfrs.append(Deferred())
            return dfrs[-1]
        d = inTransaction(createTxn, operation)
        x = []
        d.addCallback(x.append)
        self.assertEquals(x, [])
        self.assertEquals(len(dfrs), 1)
        dfrs[0].callback(35)
        # Commit in progress, so still no result...
        self.assertEquals(x, [])
        createdTxns[0].commits[0].callback(42)
        # Committed, everything's done.
        self.assertEquals(x, [35])



class SimpleSchemaHelper(SchemaTestHelper):
    def id(self):
        return 'worker'

schemaText = """
    create table DUMMY_WORK_ITEM (WORK_ID integer, CREATED timestamp);
"""
schema = SchemaSyntax(SimpleSchemaHelper().schemaFromString(schemaText))


class DummyWorkItem(WorkItem, fromTable(schema.DUMMY_WORK_ITEM)):
    pass



class PeerConnectionPoolIntegrationTests(TestCase):
    """
    L{PeerConnectionPool} is the service responsible for coordinating
    eventually-consistent task queuing within a cluster.
    """

    @inlineCallbacks
    def setUp(self):
        """
        L{PeerConnectionPool} requires access to a database and the reactor.
        """
        self.store = yield buildStore(self, None)
        def doit(txn):
            return txn.execSQL(schemaText)
        yield inTransaction(lambda: self.store.newTransaction("bonus schema"),
                            doit)
        def deschema():
            def deletestuff(txn):
                return txn.execSQL("drop table DUMMY_WORK_ITEM")
            return inTransaction(self.store.newTransaction, deletestuff)
        self.addCleanup(deschema)

        from twisted.internet import reactor
        self.node1 = PeerConnectionPool(
            reactor, self.store.newTransaction, 0, schema)
        self.node2 = PeerConnectionPool(
            reactor, self.store.newTransaction, 0, schema)

        class FireMeService(Service, object):
            def __init__(self, d):
                super(FireMeService, self).__init__()
                self.d = d
            def startService(self):
                self.d.callback(None)
        d1 = Deferred()
        d2 = Deferred()
        FireMeService(d1).setServiceParent(self.node1)
        FireMeService(d2).setServiceParent(self.node2)
        ms = MultiService()
        self.node1.setServiceParent(ms)
        self.node2.setServiceParent(ms)
        ms.startService()
        self.addCleanup(ms.stopService)
        yield gatherResults([d1, d2])


    def test_currentNodeInfo(self):
        """
        There will be two C{NODE_INFO} rows in the database, retrievable as two
        L{NodeInfo} objects, once both nodes have started up.
        """
        @inlineCallbacks
        def check(txn):
            self.assertEquals(len((yield self.node1.activeNodes(txn))), 2)
            self.assertEquals(len((yield self.node2.activeNodes(txn))), 2)
        return inTransaction(self.store.newTransaction, check)



