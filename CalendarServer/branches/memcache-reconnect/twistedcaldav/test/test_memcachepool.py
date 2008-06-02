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

from twistedcaldav.memcachepool import PooledMemCacheProtocol
from twistedcaldav.memcachepool import MemCacheClientFactory
from twistedcaldav.memcachepool import MemCachePool


MC_ADDRESS = IPv4Address('TCP', '127.0.0.1', 11211)


class StubConnectionPool(object):
    """
    A stub client connection pool that records it's calls in the form of a list
    of (status, client) tuples where status is C{'free'} or C{'busy'}

    @ivar calls: A C{list} of C{tuple}s of the form C{(status, client)} where
        status is C{'free'} or C{'busy'} and client is the protocol instance
        that made the call.
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
    def test_connectionMadeNotifiesPool(self):
        """
        Test that L{PooledMemCacheProtocol.connectionMade} notifies the
        connectionPool that it is free.
        """
        p = PooledMemCacheProtocol()
        p.factory = MemCacheClientFactory()
        p.connectionPool = StubConnectionPool()
        p.connectionMade()

        self.assertEquals(p.connectionPool.calls, [('free', p)])

        return p.factory.deferred


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


    def test_buildProtocolGivesProtocolConnectionPool(self):
        """
        Test that L{MemCacheClientFactory.buildProtocol} gives it's
        protocol instance it's connectionPool.
        """
        self.assertEquals(self.factory.connectionPool,
                          self.protocol.connectionPool)


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


    def test_performRequestCreatesConnection(self):
        """
        Test that L{MemCachePool.performRequest} on a fresh instance causes
        a new connection to be created.
        """
        d = self.pool.performRequest('get', 'foo')

        args, kwargs = self.reactor.calls.pop()

        self.assertEquals(args[:2], (MC_ADDRESS.host, MC_ADDRESS.port))
        self.failUnless(isinstance(args[2], MemCacheClientFactory))
        self.assertEquals(kwargs, {})

        factory = args[2]
        protocol = factory.buildProtocol(None)
        protocol.connectionMade()

        return d
