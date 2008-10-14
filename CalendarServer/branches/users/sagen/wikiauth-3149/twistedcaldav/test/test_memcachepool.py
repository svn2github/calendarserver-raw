##
# Copyright (c) 2008 Apple Inc. All rights reserved.
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

from zope.interface import implements

from twisted.trial.unittest import TestCase

from twisted.internet.interfaces import IConnector, IReactorTCP
from twisted.internet.address import IPv4Address

from twistedcaldav.test.util import InMemoryMemcacheProtocol
from twistedcaldav.memcachepool import PooledMemCacheProtocol
from twistedcaldav.memcachepool import MemCacheClientFactory
from twistedcaldav.memcachepool import MemCachePool


MC_ADDRESS = IPv4Address('TCP', '127.0.0.1', 11211)


class StubConnectionPool(object):
    """
    A stub client connection pool that records it's calls in the form of a list
    of (status, client) tuples where status is C{'free'} or C{'busy'}

    @ivar calls: A C{list} of C{tuple}s of the form C{(status, client)} where
        status is C{'free'}, C{'busy'} or C{'gone'} and client is the protocol
        instance that made the call.
    """
    def __init__(self):
        self.calls = []


    def clientFree(self, client):
        """
        Record a C{'free'} call for C{client}.
        """
        self.calls.append(('free', client))


    def clientBusy(self, client):
        """
        Record a C{'busy'} call for C{client}.
        """
        self.calls.append(('busy', client))


    def clientGone(self, client):
        """
        Record a C{'gone'} call for C{client}
        """
        self.calls.append(('gone', client))



class StubConnector(object):
    """
    A stub L{IConnector} that can be used for testing.
    """
    implements(IConnector)

    def connect(self):
        """
        A L{IConnector.connect} implementation that doesn't do anything.
        """


    def stopConnecting(self):
        """
        A L{IConnector.stopConnecting} that doesn't do anything.
        """



class StubReactor(object):
    """
    A stub L{IReactorTCP} that records the calls to connectTCP.

    @ivar calls: A C{list} of tuples (args, kwargs) sent to connectTCP.
    """
    implements(IReactorTCP)

    def __init__(self):
        self.calls = []


    def connectTCP(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return StubConnector()



class PooledMemCacheProtocolTests(TestCase):
    """
    Tests for the L{PooledMemCacheProtocol}
    """
    def test_connectionMadeFiresDeferred(self):
        """
        Test that L{PooledMemCacheProtocol.connectionMade} fires the factory's
        deferred.
        """
        p = PooledMemCacheProtocol()
        p.factory = MemCacheClientFactory()
        p.connectionPool = StubConnectionPool()
        d = p.factory.deferred
        d.addCallback(self.assertEquals, p)

        p.connectionMade()
        return d


class MemCacheClientFactoryTests(TestCase):
    """
    Tests for the L{MemCacheClientFactory}

    @ivar factory: A L{MemCacheClientFactory} instance with a
        L{StubConnectionPool}.
    @ivar protocol: A L{PooledMemCacheProtocol} that was built by
        L{MemCacheClientFactory.buildProtocol}.
    @ivar pool: The L{StubConnectionPool} attached to C{self.factory} and
        C{self.protocol}.
    """
    def setUp(self):
        """
        Create a L{MemCacheClientFactory} instance and and give it a
        L{StubConnectionPool} instance.
        """
        self.pool = StubConnectionPool()
        self.factory = MemCacheClientFactory()
        self.factory.connectionPool = self.pool
        self.protocol = self.factory.buildProtocol(None)


    def test_clientConnectionFailedNotifiesPool(self):
        """
        Test that L{MemCacheClientFactory.clientConnectionFailed} notifies
        the it's connectionPool that it is busy.
        """
        self.factory.clientConnectionFailed(StubConnector(), None)
        self.assertEquals(self.factory.connectionPool.calls,
                          [('busy', self.protocol)])


    def test_clientConnectionLostNotifiesPool(self):
        """
        Test that L{MemCacheClientFactory.clientConnectionLost} notifies
        the it's connectionPool that it is busy.
        """
        self.factory.clientConnectionLost(StubConnector(), None)
        self.assertEquals(self.factory.connectionPool.calls,
                          [('busy', self.protocol)])


    def test_buildProtocolRemovesExistingClient(self):
        """
        Test that L{MemCacheClientFactory.buildProtocol} notifies
        the connectionPool when an old protocol instance is going away.

        This will happen when we get reconnected.  We'll remove the old protocol
        and add a new one.
        """
        protocol = self.factory.buildProtocol(None)
        self.assertEquals(self.factory.connectionPool.calls,
                          [('gone', self.protocol)])


    def tearDown(self):
        """
        Make sure the L{MemCacheClientFactory} isn't trying to reconnect
        anymore.
        """
        self.factory.stopTrying()



class MemCachePoolTests(TestCase):
    """
    Tests for L{MemCachePool}.

    @ivar reactor: A L{StubReactor} instance.
    @ivar pool: A L{MemCachePool} for testing.
    """
    def setUp(self):
        """
        Create a L{MemCachePool}.
        """
        self.reactor = StubReactor()
        self.pool = MemCachePool(MC_ADDRESS,
                                 maxClients=5,
                                 reactor=self.reactor)


    def test_clientFreeAddsNewClient(self):
        """
        Test that a client not in the busy set gets added to the free set.
        """
        p = MemCacheClientFactory().buildProtocol(None)
        self.pool.clientFree(p)

        self.assertEquals(self.pool._freeClients, set([p]))


    def test_clientFreeAddsBusyClient(self):
        """
        Test that a client in the busy set gets moved to the free set.
        """
        p = MemCacheClientFactory().buildProtocol(None)

        self.pool.clientBusy(p)
        self.pool.clientFree(p)

        self.assertEquals(self.pool._freeClients, set([p]))
        self.assertEquals(self.pool._busyClients, set([]))


    def test_clientBusyAddsNewClient(self):
        """
        Test that a client not in the free set gets added to the busy set.
        """
        p = MemCacheClientFactory().buildProtocol(None)
        self.pool.clientBusy(p)

        self.assertEquals(self.pool._busyClients, set([p]))


    def test_clientBusyAddsFreeClient(self):
        """
        Test that a client in the free set gets moved to the busy set.
        """
        p = MemCacheClientFactory().buildProtocol(None)

        self.pool.clientFree(p)
        self.pool.clientBusy(p)

        self.assertEquals(self.pool._busyClients, set([p]))
        self.assertEquals(self.pool._freeClients, set([]))


    def test_clientGoneRemovesFreeClient(self):
        """
        Test that a client in the free set gets removed when
        L{MemCachePool.clientGone} is called.
        """
        p = MemCacheClientFactory().buildProtocol(None)
        self.pool.clientFree(p)
        self.assertEquals(self.pool._freeClients, set([p]))
        self.assertEquals(self.pool._busyClients, set([]))

        self.pool.clientGone(p)
        self.assertEquals(self.pool._freeClients, set([]))


    def test_clientGoneRemovesBusyClient(self):
        """
        Test that a client in the busy set gets removed when
        L{MemCachePool.clientGone} is called.
        """
        p = MemCacheClientFactory().buildProtocol(None)
        self.pool.clientBusy(p)
        self.assertEquals(self.pool._busyClients, set([p]))
        self.assertEquals(self.pool._freeClients, set([]))

        self.pool.clientGone(p)
        self.assertEquals(self.pool._busyClients, set([]))


    def test_performRequestCreatesConnection(self):
        """
        Test that L{MemCachePool.performRequest} on a fresh instance causes
        a new connection to be created.
        """
        def _checkResult(result):
            self.assertEquals(result, (0, 'bar'))


        p = InMemoryMemcacheProtocol()
        p.set('foo', 'bar')

        d = self.pool.performRequest('get', 'foo')
        d.addCallback(_checkResult)

        args, kwargs = self.reactor.calls.pop()

        self.assertEquals(args[:2], (MC_ADDRESS.host, MC_ADDRESS.port))
        self.failUnless(isinstance(args[2], MemCacheClientFactory))
        self.assertEquals(kwargs, {})

        args[2].deferred.callback(p)

        return d


    def test_performRequestUsesFreeConnection(self):
        """
        Test that L{MemCachePool.performRequest} doesn't create a new connection
        to be created if there is a free connection.
        """
        def _checkResult(result):
            self.assertEquals(result, (0, 'bar'))
            self.assertEquals(self.reactor.calls, [])

        p = InMemoryMemcacheProtocol()
        p.set('foo', 'bar')

        self.pool.clientFree(p)

        d = self.pool.performRequest('get', 'foo')
        d.addCallback(_checkResult)

        return d


    def test_performRequestMaxBusyQueuesRequest(self):
        """
        Test that L{MemCachePool.performRequest} queues the request if
        all clients are busy.
        """
        def _checkResult(result):
            self.assertEquals(result, (0, 'bar'))
            self.assertEquals(self.reactor.calls, [])

        p = InMemoryMemcacheProtocol()
        p.set('foo', 'bar')

        p1 = InMemoryMemcacheProtocol()
        p1.set('foo', 'baz')

        self.pool.suggestMaxClients(2)

        self.pool.clientBusy(p)
        self.pool.clientBusy(p1)

        d = self.pool.performRequest('get', 'foo')
        d.addCallback(_checkResult)

        self.pool.clientFree(p)

        return d


    def test_performRequestCreatesConnectionsUntilMaxBusy(self):
        """
        Test that L{MemCachePool.performRequest} will create new connections
        until it reaches the maximum number of busy clients.
        """
        def _checkResult(result):
            self.assertEquals(result, (0, 'baz'))

        self.pool.suggestMaxClients(2)

        p = InMemoryMemcacheProtocol()
        p.set('foo', 'bar')

        p1 = InMemoryMemcacheProtocol()
        p1.set('foo', 'baz')


        self.pool.clientBusy(p)

        d = self.pool.performRequest('get', 'foo')

        args, kwargs = self.reactor.calls.pop()

        self.assertEquals(args[:2], (MC_ADDRESS.host, MC_ADDRESS.port))
        self.failUnless(isinstance(args[2], MemCacheClientFactory))
        self.assertEquals(kwargs, {})

        args[2].deferred.callback(p1)

        return d


    def test_pendingConnectionsCountAgainstMaxClients(self):
        """
        Test that L{MemCachePool.performRequest} will not initiate a new
        connection if there are pending connections that count towards max
        clients.
        """
        self.pool.suggestMaxClients(1)

        d = self.pool.performRequest('get', 'foo')

        args, kwargs = self.reactor.calls.pop()

        self.assertEquals(args[:2], (MC_ADDRESS.host, MC_ADDRESS.port))
        self.failUnless(isinstance(args[2], MemCacheClientFactory))
        self.assertEquals(kwargs, {})

        d1 = self.pool.performRequest('get', 'bar')
        self.assertEquals(self.reactor.calls, [])

        args[2].deferred.callback(InMemoryMemcacheProtocol())

        return d
