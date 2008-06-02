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

from twisted.internet.defer import Deferred
from twisted.internet.protocol import ReconnectingClientFactory

from twistedcaldav.memcache import MemCacheProtocol


class PooledMemCacheProtocol(MemCacheProtocol):
    """
    A MemCacheProtocol that will notify a connectionPool that it is ready
    to accept requests.

    @ivar factory: A L{MemCacheClientFactory} instance.
    @ivar connectionPool: A managing connection pool that we notify of events.
    """
    factory = None
    connectionPool = None

    def connectionMade(self):
        """
        Notify our connectionPool that we're ready to accept connections.
        """
        MemCacheProtocol.connectionMade(self)
        self.connectionPool.clientFree(self)

        if self.factory.deferred is not None:
            self.factory.deferred.callback(self)
            self.factory.deferred = None



class MemCacheClientFactory(ReconnectingClientFactory):
    """
    A client factory for MemCache that reconnects and notifies a pool of it's
    state.

    @ivar connectionPool: A managing connection pool that we notify of events.
    @ivar deferred: A L{Deferred} that represents the initial connection.
    @ivar _protocolInstance: The current instance of our protocol that we pass
        to our connectionPool.
    """
    protocol = PooledMemCacheProtocol
    connectionPool = None
    _protocolInstance = None


    def __init__(self):
        self.deferred = Deferred()


    def clientConnectionLost(self, connector, reason):
        """
        Notify the connectionPool that we've lost our connection.
        """
        if self._protocolInstance is not None:
            self.connectionPool.clientBusy(self._protocolInstance)

        ReconnectingClientFactory.clientConnectionLost(
            self,
            connector,
            reason)


    def clientConnectionFailed(self, connector, reason):
        """
        Notify the connectionPool that we're unable to connect
        """
        if self._protocolInstance is not None:
            self.connectionPool.clientBusy(self._protocolInstance)

        ReconnectingClientFactory.clientConnectionFailed(
            self,
            connector,
            reason)


    def buildProtocol(self, addr):
        """
        Attach the C{self.connectionPool} to the protocol so it can tell it,
        when we've connected.
        """
        self._protocolInstance = self.protocol()
        self._protocolInstance.connectionPool = self.connectionPool
        self._protocolInstance.factory = self
        return self._protocolInstance



class MemCachePool(object):
    """
    A connection pool for MemCacheProtocol instances.

    @ivar clientFactory: The L{ClientFactory} implementation that will be used
        for each protocol.

    @ivar _maxClients: A C{int} indicating the maximum number of clients.
    @ivar _serverAddress: An L{IAddress} provider indicating the server to
        connect to.  (Only L{IPv4Address} currently supported.)
    @ivar _reactor: The L{IReactorTCP} provider used to initiate new
        connections.

    @ivar _busyClients: A C{set} that contains all currently busy clients.
    @ivar _freeClients: A C{set} that contains all currently free clients.
    """
    clientFactory = MemCacheClientFactory

    def __init__(self, serverAddress, maxClients=5, reactor=None):
        """
        @param serverAddress: An L{IPv4Address} indicating the server to
            connect to.
        @param maxClients: A C{int} indicating the maximum number of clients.
        @param reactor: An L{IReactorTCP{ provider used to initiate new
            connections.
        """
        self._serverAddress = serverAddress
        self._maxClients = maxClients

        if reactor is None:
            from twisted.internet import reactor

        self._reactor = reactor

        self._busyClients = set([])
        self._freeClients = set([])


    def _newClientConnection(self):
        """
        Create a new client connection.

        @return: A L{Deferred} that fires with the L{IProtocol} instance.
        """
        factory = self.clientFactory()

        factory.connectionPool = self

        self._reactor.connectTCP(self._serverAddress.host,
                                 self._serverAddress.port,
                                 factory)
        return factory.deferred


    def performRequest(self, command, *args, **kwargs):
        """
        Select an available client and perform the given request on it.

        @param command: A C{str} representing an attribute of
            L{MemCacheProtocol}.
        @parma args: Any positional arguments that should be passed to
            C{command}.
        @param kwargs: Any keyword arguments that should be passed to
            C{command}.

        @return: A L{Deferred} that fires with the result of the given command.
        """
        d = self._newClientConnection()
        return d

    def clientBusy(self, client):
        """
        Notify that the given client is being used to complete a request.

        @param client: An instance of C{self.clientFactory}
        """
        if client in self._freeClients:
            self._freeClients.remove(client)

        self._busyClients.add(client)


    def clientFree(self, client):
        """
        Notify that the given client is free to handle more requests.

        @param client: An instance of C{self.clientFactory}
        """
        if client in self._busyClients:
            self._busyClients.remove(client)

        self._freeClients.add(client)
