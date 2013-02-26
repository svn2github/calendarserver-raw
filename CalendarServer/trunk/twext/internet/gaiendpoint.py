# -*- test-case-name: twext.internet.test.test_gaiendpoint -*-
##
# Copyright (c) 2012-2013 Apple Inc. All rights reserved.
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
from __future__ import print_function

"""
L{getaddrinfo}()-based endpoint
"""

from socket import getaddrinfo, AF_UNSPEC, AF_INET, AF_INET6, SOCK_STREAM
from twisted.internet.endpoints import TCP4ClientEndpoint, SSL4ClientEndpoint
from twisted.internet.defer import Deferred
from twisted.internet.threads import deferToThread
from twisted.internet.task import LoopingCall

class MultiFailure(Exception):

    def __init__(self, failures):
        super(MultiFailure, self).__init__("Failure with multiple causes.")
        self.failures = failures



class GAIEndpoint(object):
    """
    Client endpoint that will call L{getaddrinfo} in a thread and then attempt
    to connect to each endpoint (almost) in parallel.

    @ivar reactor: The reactor to attempt the connection with.
    @type reactor: provider of L{IReactorTCP} and L{IReactorTime}

    @ivar host: The host to resolve.
    @type host: L{str}

    @ivar port: The port number to resolve.
    @type port: L{int}

    @ivar deferToThread: A function like L{deferToThread}, used to invoke
        getaddrinfo.  (Replaceable mainly for testing purposes.)
    """

    deferToThread = staticmethod(deferToThread)

    def subEndpoint(self, reactor, host, port, contextFactory):
        """
        Create an endpoint to connect to based on a single address result from
        L{getaddrinfo}.

        @param reactor: the reactor to connect to
        @type reactor: L{IReactorTCP}

        @param host: The IP address of the host to connect to, in presentation
            format.
        @type host: L{str}

        @param port: The numeric port number to connect to.
        @type port: L{int}

        @param contextFactory: If not L{None}, the OpenSSL context factory to
            use to produce client connections.

        @return: a stream client endpoint that will connect to the given host
            and port via the given reactor.
        @rtype: L{IStreamClientEndpoint}
        """
        if contextFactory is None:
            return TCP4ClientEndpoint(reactor, host, port)
        else:
            return SSL4ClientEndpoint(reactor, host, port, contextFactory)


    def __init__(self, reactor, host, port, contextFactory=None):
        self.reactor = reactor
        self.host = host
        self.port = port
        self.contextFactory = contextFactory


    def connect(self, factory):
        dgai = self.deferToThread(getaddrinfo, self.host, self.port,
                                  AF_UNSPEC, SOCK_STREAM)
        @dgai.addCallback
        def gaiToEndpoints(gairesult):
            for family, socktype, proto, canonname, sockaddr in gairesult:
                if family in [AF_INET6, AF_INET]:
                    yield self.subEndpoint(self.reactor, sockaddr[0],
                                           sockaddr[1], self.contextFactory)

        @gaiToEndpoints.addCallback
        def connectTheEndpoints(endpoints):
            doneTrying = []
            outstanding = []
            errors = []
            succeeded = []
            actuallyDidIt = Deferred()
            def removeMe(result, attempt):
                outstanding.remove(attempt)
                return result
            def connectingDone(result):
                if lc.running:
                    lc.stop()
                succeeded.append(True)
                for o in outstanding[::]:
                    o.cancel()
                actuallyDidIt.callback(result)
                return None
            def lastChance():
                if doneTrying and not outstanding and not succeeded:
                    # We've issued our last attempts. There are no remaining
                    # outstanding attempts; they've all failed. We haven't
                    # succeeded.  Time... to die.
                    actuallyDidIt.errback(MultiFailure(errors))
            def connectingFailed(why):
                errors.append(why)
                lastChance()
                return None
            def nextOne():
                try:
                    endpoint = endpoints.next()
                except StopIteration:
                    # Out of endpoints to try!  Now it's time to wait for all of
                    # the outstanding attempts to complete, and, if none of them
                    # have been successful, then to give up with a relevant
                    # error.  They'll all be dealt with by connectingDone or
                    # connectingFailed.
                    doneTrying.append(True)
                    lc.stop()
                    lastChance()
                else:
                    attempt = endpoint.connect(factory)
                    attempt.addBoth(removeMe, attempt)
                    attempt.addCallbacks(connectingDone, connectingFailed)
                    outstanding.append(attempt)
            lc = LoopingCall(nextOne)
            lc.clock = self.reactor
            lc.start(0.0)
            return actuallyDidIt
        return dgai



if __name__ == '__main__':
    from twisted.internet import reactor
    import sys
    if sys.argv[1:]:
        host = sys.argv[1]
        port = int(sys.argv[2])
    else:
        host = "localhost"
        port = 22
    gaie = GAIEndpoint(reactor, host, port)
    from twisted.internet.protocol import Factory, Protocol
    class HelloGoobye(Protocol, object):
        def connectionMade(self):
            print('Hello!')
            self.transport.loseConnection()

        def connectionLost(self, reason):
            print('Goodbye')

    class MyFactory(Factory, object):
        def buildProtocol(self, addr):
            print('Building protocol for:', addr)
            return HelloGoobye()
    def bye(what):
        print('bye', what)
        reactor.stop()
    gaie.connect(MyFactory()).addBoth(bye)
    reactor.run()
