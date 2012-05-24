##
# Copyright (c) 2009-2010 Apple Inc. All rights reserved.
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

__all__ = [
    "installPools",
    "installPool",
    "getHTTPClientPool",
]

import OpenSSL
import urlparse

from twext.python.log import LoggingMixIn
from twext.internet.gaiendpoint import GAIEndpoint
from twext.internet.adaptendpoint import connect

from twext.internet.ssl import ChainingOpenSSLContextFactory

from twisted.internet.defer import Deferred, inlineCallbacks, returnValue
from twisted.internet.error import ConnectionLost, ConnectionDone, ConnectError
from twisted.internet.protocol import ClientFactory

from twext.web2 import responsecode
from twext.web2.client.http import HTTPClientProtocol
from twext.web2.http import StatusResponse, HTTPError
from twext.web2.dav.util import allDataFromStream
from twext.web2.stream import MemoryStream

class PooledHTTPClientFactory(ClientFactory, LoggingMixIn):
    """
    A client factory for HTTPClient that notifies a pool of it's state. It the connection
    fails in the middle of a request it will retry the request.

    @ivar protocol: The current instance of our protocol that we pass
        to our connectionPool.
    @ivar connectionPool: A managing connection pool that we notify of events.
    """
    protocol = HTTPClientProtocol
    connectionPool = None

    def __init__(self, reactor):
        self.reactor = reactor
        self.instance = None
        self.onConnect = Deferred()
        self.afterConnect = Deferred()

    def clientConnectionLost(self, connector, reason):
        """
        Notify the connectionPool that we've lost our connection.
        """

        if hasattr(self, "afterConnect"):
            self.reactor.callLater(0, self.afterConnect.errback, reason)
            del self.afterConnect

        if self.connectionPool.shutdown_requested:
            # The reactor is stopping; don't reconnect
            return

    def clientConnectionFailed(self, connector, reason):
        """
        Notify the connectionPool that we're unable to connect
        """
        if hasattr(self, "onConnect"):
            self.reactor.callLater(0, self.onConnect.errback, reason)
            del self.onConnect
        elif hasattr(self, "afterConnect"):
            self.reactor.callLater(0, self.afterConnect.errback, reason)
            del self.afterConnect

    def buildProtocol(self, addr):
        self.instance = self.protocol()
        self.reactor.callLater(0, self.onConnect.callback, self.instance)
        del self.onConnect
        return self.instance



class HTTPClientPool(LoggingMixIn):
    """
    A connection pool for HTTPClientProtocol instances.

    @ivar clientFactory: The L{ClientFactory} implementation that will be used
        for each protocol.

    @ivar _maxClients: A C{int} indicating the maximum number of clients.

    @ivar _endpoint: An L{IStreamClientEndpoint} provider indicating the server
        to connect to.

    @ivar _reactor: The L{IReactorTCP} provider used to initiate new
        connections.

    @ivar _busyClients: A C{set} that contains all currently busy clients.

    @ivar _freeClients: A C{set} that contains all currently free clients.

    @ivar _pendingConnects: A C{int} indicating how many connections are in
        progress.
    """
    clientFactory = PooledHTTPClientFactory
    maxRetries = 2

    def __init__(self, name, scheme, endpoint, secureEndpoint,
                 maxClients=5, reactor=None):
        """
        @param endpoint: An L{IStreamClientEndpoint} indicating the server to
            connect to.

        @param maxClients: A C{int} indicating the maximum number of clients.

        @param reactor: An L{IReactorTCP} provider used to initiate new
            connections.
        """
        
        self._name = name
        self._scheme = scheme
        self._endpoint = endpoint
        self._secureEndpoint = secureEndpoint
        self._maxClients = maxClients

        if reactor is None:
            from twisted.internet import reactor
        self._reactor = reactor

        self.shutdown_deferred = None
        self.shutdown_requested = False
        reactor.addSystemEventTrigger('before', 'shutdown', self._shutdownCallback)

        self._busyClients = set([])
        self._freeClients = set([])
        self._pendingConnects = 0
        self._pendingRequests = []

    def _isIdle(self):
        return (
            len(self._busyClients) == 0 and
            len(self._pendingRequests) == 0 and
            self._pendingConnects == 0
        )

    def _shutdownCallback(self):
        self.shutdown_requested = True
        if self._isIdle():
            return None
        self.shutdown_deferred = Deferred()
        return self.shutdown_deferred

    def _newClientConnection(self):
        """
        Create a new client connection.

        @return: A L{Deferred} that fires with the L{IProtocol} instance.
        """
        self._pendingConnects += 1

        self.log_debug("Initating new client connection to: %r" % (
                self._endpoint,))
        self._logClientStats()

        factory = self.clientFactory(self._reactor)
        factory.connectionPool = self

        if self._scheme == "https":
            connect(self._secureEndpoint, factory)
        elif self._scheme == "http":
            connect(self._endpoint, factory)
        else:
            raise ValueError("URL scheme for client pool not supported")

        def _doneOK(client):
            self._pendingConnects -= 1

            def _goneClientAfterError(f, client):
                f.trap(ConnectionLost, ConnectionDone, ConnectError)
                self.clientGone(client)

            d2 = factory.afterConnect
            d2.addErrback(_goneClientAfterError, client)
            return client

        def _doneError(result):
            self._pendingConnects -= 1
            return result

        d = factory.onConnect
        d.addCallbacks(_doneOK, _doneError)
        
        return d

    def _performRequestOnClient(self, client, request, *args, **kwargs):
        """
        Perform the given request on the given client.

        @param client: A L{PooledMemCacheProtocol} that will be used to perform
            the given request.

        @param command: A C{str} representing an attribute of
            L{MemCacheProtocol}.
        @parma args: Any positional arguments that should be passed to
            C{command}.
        @param kwargs: Any keyword arguments that should be passed to
            C{command}.

        @return: A L{Deferred} that fires with the result of the given command.
        """

        def _freeClientAfterRequest(result):
            self.clientFree(client)
            return result

        def _goneClientAfterError(result):
            self.clientGone(client)
            return result

        self.clientBusy(client)
        d = client.submitRequest(request, closeAfter=True)
        d.addCallbacks(_freeClientAfterRequest, _goneClientAfterError)
        return d

    @inlineCallbacks
    def submitRequest(self, request, *args, **kwargs):
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

        # Since we may need to replay the request we have to read the request.stream
        # into memory and reset request.stream to use a MemoryStream each time we repeat
        # the request
        data = (yield allDataFromStream(request.stream))

        # Try this maxRetries times
        for ctr in xrange(self.maxRetries + 1):
            try:
                request.stream = MemoryStream(data if data is not None else "")
                request.stream.doStartReading = None

                response = (yield self._submitRequest(request, args, kwargs))

            except (ConnectionLost, ConnectionDone, ConnectError), e:
                self.log_error("HTTP pooled client connection error (attempt: %d) - retrying: %s" % (ctr+1, e,))
                continue
            
            # TODO: find the proper cause of these assertions and fix
            except (AssertionError,), e:
                self.log_error("HTTP pooled client connection assertion error (attempt: %d) - retrying: %s" % (ctr+1, e,))
                continue

            else:
                returnValue(response)
        else:
            self.log_error("HTTP pooled client connection error - exhausted retry attempts.")
            raise HTTPError(StatusResponse(responsecode.BAD_GATEWAY, "Could not connect to HTTP pooled client host."))

    def _submitRequest(self, request, *args, **kwargs):
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

        if len(self._freeClients) > 0:
            d = self._performRequestOnClient(self._freeClients.pop(), request, *args, **kwargs)

        elif len(self._busyClients) + self._pendingConnects >= self._maxClients:
            d = Deferred()
            self._pendingRequests.append((d, request, args, kwargs))
            self.log_debug("Request queued: %s, %r, %r" % (request, args, kwargs))
            self._logClientStats()

        else:
            d = self._newClientConnection()
            d.addCallback(self._performRequestOnClient, request, *args, **kwargs)

        return d

    def _logClientStats(self):
        self.log_debug("Clients #free: %d, #busy: %d, "
                       "#pending: %d, #queued: %d" % (
                len(self._freeClients),
                len(self._busyClients),
                self._pendingConnects,
                len(self._pendingRequests)))

    def clientGone(self, client):
        """
        Notify that the given client is to be removed from the pool completely.

        @param client: An instance of L{PooledMemCacheProtocol}.
        """
        if client in self._busyClients:
            self._busyClients.remove(client)

        elif client in self._freeClients:
            self._freeClients.remove(client)

        self.log_debug("Removed client: %r" % (client,))
        self._logClientStats()

        self._processPending()

    def clientBusy(self, client):
        """
        Notify that the given client is being used to complete a request.

        @param client: An instance of C{self.clientFactory}
        """

        if client in self._freeClients:
            self._freeClients.remove(client)

        self._busyClients.add(client)

        self.log_debug("Busied client: %r" % (client,))
        self._logClientStats()

    def clientFree(self, client):
        """
        Notify that the given client is free to handle more requests.

        @param client: An instance of C{self.clientFactory}
        """
        if client in self._busyClients:
            self._busyClients.remove(client)

        self._freeClients.add(client)

        if self.shutdown_deferred and self._isIdle():
            self.shutdown_deferred.callback(None)

        self.log_debug("Freed client: %r" % (client,))
        self._logClientStats()

        self._processPending()

    def _processPending(self):
        if len(self._pendingRequests) > 0:
            d, request, args, kwargs = self._pendingRequests.pop(0)

            self.log_debug("Performing Queued Request: %s, %r, %r" % (
                    request, args, kwargs))
            self._logClientStats()

            _ign_d = self._submitRequest(request, *args, **kwargs)

            _ign_d.addCallbacks(d.callback, d.errback)

    def suggestMaxClients(self, maxClients):
        """
        Suggest the maximum number of concurrently connected clients.

        @param maxClients: A C{int} indicating how many client connections we
            should keep open.
        """
        self._maxClients = maxClients

_clientPools = {}     # Maps a host:port to a pool object

def installPools(hosts, maxClients=5, reactor=None):
    
    for name, url in hosts:
        installPool(
            name,
            url,
            maxClients,
            reactor,
        )



def _configuredClientContextFactory():
    """
    Get a client context factory from the configuration.
    """
    from twistedcaldav.config import config
    return ChainingOpenSSLContextFactory(
        config.SSLPrivateKey, config.SSLCertificate,
        certificateChainFile=config.SSLAuthorityChain,
        sslmethod=getattr(OpenSSL.SSL, config.SSLMethod)
    )



def installPool(name, url, maxClients=5, reactor=None):

    if reactor is None:
        from twisted.internet import reactor
    parsedURL = urlparse.urlparse(url)
    ctxf = _configuredClientContextFactory()
    pool = HTTPClientPool(
        name,
        parsedURL.scheme,
        GAIEndpoint(reactor, parsedURL.hostname, parsedURL.port),
        GAIEndpoint(reactor, parsedURL.hostname, parsedURL.port, ctxf),
        maxClients,
        reactor,
    )
    _clientPools[name] = pool



def getHTTPClientPool(name):
    return _clientPools[name]
