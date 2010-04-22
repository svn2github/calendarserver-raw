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

from twisted.application import internet
from twisted.internet import tcp, ssl

class MaxAcceptPortMixin(object):
    """
    Mixin for resetting maxAccepts.
    """
    def doRead(self):
        self.numberAccepts = min(
            self.factory.maxRequests - self.factory.outstandingRequests,
            self.factory.maxAccepts
        )
        tcp.Port.doRead(self)



class MaxAcceptTCPPort(MaxAcceptPortMixin, tcp.Port):
    """
    Use for non-inheriting tcp ports.
    """



class MaxAcceptSSLPort(MaxAcceptPortMixin, ssl.Port):
    """
    Use for non-inheriting SSL ports.
    """



class MaxAcceptTCPServer(internet.TCPServer):
    """
    TCP server which will uses MaxAcceptTCPPorts.

    @ivar myPort: When running, this is set to the L{IListeningPort} being
        managed by this service.
    """

    def __init__(self, *args, **kwargs):
        internet.TCPServer.__init__(self, *args, **kwargs)
        self.args[1].myServer = self
        self.backlog = self.kwargs.get("backlog", None)
        self.interface = self.kwargs.get("interface", None)


    def _getPort(self):
        from twisted.internet import reactor

        port = MaxAcceptTCPPort(self.args[0], self.args[1], self.backlog, self.interface, reactor)
        port.startListening()
        self.myPort = port
        return port


class MaxAcceptSSLServer(internet.SSLServer):
    """
    SSL server which will uses MaxAcceptSSLPorts.
    """

    def __init__(self, *args, **kwargs):
        internet.SSLServer.__init__(self, *args, **kwargs)
        self.args[1].myServer = self
        self.backlog = self.kwargs.get("backlog", None)
        self.interface = self.kwargs.get("interface", None)

    def _getPort(self):
        port = MaxAcceptSSLPort(self.args[0], self.args[1], self.args[2], self.backlog, self.interface, self.reactor)
        port.startListening()
        self.myPort = port
        return port
