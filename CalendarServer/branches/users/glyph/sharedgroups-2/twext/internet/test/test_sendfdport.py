# -*- test-case-name: twext.internet.test.test_sendfdport -*-
##
# Copyright (c) 2010-2013 Apple Inc. All rights reserved.
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
Tests for L{twext.internet.sendfdport}.
"""

import os
import fcntl

from twext.internet.sendfdport import InheritedSocketDispatcher,\
    _SubprocessSocket
from twext.web2.metafd import ConnectionLimiter
from twisted.internet.interfaces import IReactorFDSet
from twisted.trial.unittest import TestCase
from zope.interface.declarations import implements


class ReaderAdder(object):
    implements(IReactorFDSet)

    def __init__(self):
        self.readers = []

    def addReader(self, reader):
        self.readers.append(reader)

    def getReaders(self):
        return self.readers[:]



def isNonBlocking(skt):
    """
    Determine if the given socket is blocking or not.

    @param skt: a socket.
    @type skt: L{socket.socket}

    @return: L{True} if the socket is non-blocking, L{False} if the socket is
        blocking.
    @rtype: L{bool}
    """
    return bool(fcntl.fcntl(skt.fileno(), fcntl.F_GETFL) & os.O_NONBLOCK)



class InheritedSocketDispatcherTests(TestCase):
    """
    Inherited socket dispatcher tests.
    """

    def test_nonBlocking(self):
        """
        Creating a L{_SubprocessSocket} via
        L{InheritedSocketDispatcher.addSocket} results in a non-blocking
        L{socket.socket} object being assigned to its C{skt} attribute, as well
        as a non-blocking L{socket.socket} object being returned.
        """
        dispatcher = InheritedSocketDispatcher(None)
        dispatcher.startDispatching()
        reactor = ReaderAdder()
        dispatcher.reactor = reactor
        inputSocket = dispatcher.addSocket()
        outputSocket = reactor.readers[-1]
        self.assertTrue(isNonBlocking(inputSocket), "Input is blocking.")
        self.assertTrue(isNonBlocking(outputSocket), "Output is blocking.")


    def test_addAfterStart(self):
        """
        Adding a socket to an L{InheritedSocketDispatcher} after it has already
        been started results in it immediately starting reading.
        """
        reactor = ReaderAdder()
        dispatcher = InheritedSocketDispatcher(None)
        dispatcher.reactor = reactor
        dispatcher.startDispatching()
        dispatcher.addSocket()
        self.assertEquals(reactor.getReaders(), dispatcher._subprocessSockets)


    def test_sendFileDescriptorSorting(self):
        """
        Make sure InheritedSocketDispatcher.sendFileDescriptor sorts sockets with status None
        higher than those with int status values.
        """

        self.patch(_SubprocessSocket, 'sendSocketToPeer', lambda x, y, z:None)
        dispatcher = InheritedSocketDispatcher(ConnectionLimiter(2, 20))
        dispatcher.addSocket()
        dispatcher.addSocket()
        dispatcher.addSocket()

        sockets = dispatcher._subprocessSockets[:]

        # Check that 0 is preferred over None
        sockets[0].status = 0
        sockets[1].status = 1
        sockets[2].status = None

        dispatcher.sendFileDescriptor(None, "")

        self.assertEqual(sockets[0].status, 1)
        self.assertEqual(sockets[1].status, 1)
        self.assertEqual(sockets[2].status, None)

        dispatcher.sendFileDescriptor(None, "")

        self.assertEqual(sockets[0].status, 1)
        self.assertEqual(sockets[1].status, 1)
        self.assertEqual(sockets[2].status, 1)

        # Check that after going to 1 and back to 0 that is still preferred over None
        sockets[0].status = 0
        sockets[1].status = 1
        sockets[2].status = None

        dispatcher.sendFileDescriptor(None, "")

        self.assertEqual(sockets[0].status, 1)
        self.assertEqual(sockets[1].status, 1)
        self.assertEqual(sockets[2].status, None)

        sockets[1].status = 0

        dispatcher.sendFileDescriptor(None, "")

        self.assertEqual(sockets[0].status, 1)
        self.assertEqual(sockets[1].status, 1)
        self.assertEqual(sockets[2].status, None)
