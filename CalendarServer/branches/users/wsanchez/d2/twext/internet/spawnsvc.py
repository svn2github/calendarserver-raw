##
# Copyright (c) 2011 Apple Inc. All rights reserved.
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
Utility service that can spawn subprocesses.
"""

import os
import sys

from twisted.python import log

from twisted.python.reflect import namedAny
from twisted.internet.stdio import StandardIO
from twisted.internet.error import ReactorNotRunning

if __name__ == '__main__':

    sys.stdout = sys.stderr
    there = sys.argv[1]
    protocolClass = namedAny(there)
    proto = protocolClass()
    origLost = proto.connectionLost
    def goodbye(reason):
        """
        Stop the process if stdin is closed.
        """
        try:
            reactor.stop()
        except ReactorNotRunning:
            pass
        return origLost(reason)
    proto.connectionLost = goodbye
    StandardIO(proto)
    from twisted.internet import reactor
    reactor.run()
    os._exit(0)


import sys

from zope.interface import implements

from twisted.internet.interfaces import ITransport, IPushProducer, IConsumer

from twisted.application.service import Service
from twisted.python.reflect import qual
from twisted.internet.protocol import ProcessProtocol
from twisted.internet.defer import Deferred, succeed


class BridgeTransport(object):
    """
    ITransport implementation for the protocol in the parent process running a
    L{SpawnerService}.
    """

    implements(ITransport, IPushProducer, IConsumer)

    def __init__(self, processTransport):
        """
        Create this bridge transport connected to an L{IProcessTransport}.
        """
        self.transport = processTransport


    def __getattr__(self, name):
        """
        Delegate all attribute accesses to the process traansport.
        """
        return getattr(self.transport, name)


    def getPeer(self):
        """
        Get a fake peer address indicating the subprocess's pid.
        """
        return "Peer:PID:" + str(self.transport.pid)


    def getHost(self):
        """
        Get a fake host address indicating the subprocess's pid.
        """
        return "Host:PID:" + str(self.transport.pid)



class BridgeProtocol(ProcessProtocol, object):
    """
    Process protocol implementation that delivers data to the C{hereProto}
    associated with an invocation of L{SpawnerService.spawn}.

    @ivar service: a L{SpawnerService} that created this L{BridgeProtocol}

    @ivar protocol: a reference to the L{IProtocol}.

    @ivar killTimeout: number of seconds after sending SIGINT that this process
        will send SIGKILL.
    """

    def __init__(self, service, protocol, killTimeout=15.0):
        self.service = service
        self.protocol = protocol
        self.killTimeout = killTimeout
        self.service.addBridge(self)


    def connectionMade(self):
        """
        The subprocess was started.
        """
        self.protocol.makeConnection(BridgeTransport(self.transport))


    def outReceived(self, data):
        """
        Some data was received to standard output; relay it to the protocol.
        """
        self.protocol.dataReceived(data)


    def errReceived(self, data):
        """
        Some standard error was received from the subprocess.
        """
        log.msg("Error output from process: " + data,
                isError=True)


    _killTimeout = None
    def eventuallyStop(self):
        """
        Eventually stop this subprocess.  Send it a SIGTERM, and if it hasn't
        stopped by C{self.killTimeout} seconds, send it a SIGKILL.
        """
        self.transport.signalProcess('TERM')
        def reallyStop():
            self.transport.signalProcess("KILL")
            self._killTimeout = None
        self._killTimeout = (
            self.service.reactor.callLater(self.killTimeout, reallyStop)
        )


    def processEnded(self, reason):
        """
        The process has ended; notify the L{SpawnerService} that this bridge
        has stopped.
        """
        if self._killTimeout is not None:
            self._killTimeout.cancel()
        self.protocol.connectionLost(reason)
        self.service.removeBridge(self)



class SpawnerService(Service, object):
    """
    Process to spawn services and then shut them down.

    @ivar reactor: an L{IReactorProcess}/L{IReactorTime}

    @ivar pendingSpawns: a C{list} of 2-C{tuple}s of hereProto, thereProto.

    @ivar bridges: a C{list} of L{BridgeProtocol} instances.
    """

    def __init__(self, reactor=None):
        if reactor is None:
            from twisted.internet import reactor
        self.reactor = reactor
        self.pendingSpawns = []
        self.bridges = []
        self._stopAllDeferred = None


    def spawn(self, hereProto, thereProto, childFDs=None):
        """
        Spawn a subprocess with a connected pair of protocol objects, one in
        the current process, one in the subprocess.

        @param hereProto: a L{Protocol} instance to listen in this process.

        @param thereProto: a top-level class or function that will be imported
            and called in the spawned subprocess.

        @param childFDs: File descriptors to share with the subprocess; same
            format as L{IReactorProcess.spawnProcess}.

        @return: a L{Deferred} that fires when C{hereProto} is ready.
        """
        if not self.running:
            self.pendingSpawns.append((hereProto, thereProto))
            return
        name = qual(thereProto)
        argv = [sys.executable, '-u', '-m', __name__, name]
        self.reactor.spawnProcess(
            BridgeProtocol(self, hereProto), sys.executable,
            argv, os.environ, childFDs=childFDs
        )
        return succeed(hereProto)


    def startService(self):
        """
        Start the service; spawn any processes previously started with spawn().
        """
        super(SpawnerService, self).startService()
        for spawn in self.pendingSpawns:
            self.spawn(*spawn)
        self.pendingSpawns = []


    def addBridge(self, bridge):
        """
        Add a L{BridgeProtocol} to the list to be tracked.
        """
        self.bridges.append(bridge)


    def removeBridge(self, bridge):
        """
        The process controlled by a L{BridgeProtocol} has terminated; remove it
        from the active list, and fire any outstanding Deferred.

        @param bridge: the protocol which has ended.
        """
        self.bridges.remove(bridge)
        if self._stopAllDeferred is not None:
            if len(self.bridges) == 0:
                self._stopAllDeferred.callback(None)
                self._stopAllDeferred = None


    def stopService(self):
        """
        Stop the service.
        """
        super(SpawnerService, self).stopService()
        if self.bridges:
            self._stopAllDeferred = Deferred()
            for bridge in self.bridges:
                bridge.eventuallyStop()
            return self._stopAllDeferred
        return succeed(None)



