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

from twisted.trial.unittest import TestCase
from twisted.internet.task import Clock
from twistedcaldav.notify import *



class NotificationClientUserTests(TestCase):

    class NotificationClientUser(NotificationClientUserMixIn):
        pass

    def test_installNoficationClient(self):
        self.assertEquals(getNotificationClient(), None)
        self.clock = Clock()
        installNotificationClient(self.clock, None, None,
            klass=StubNotificationClient)
        notificationClient = getNotificationClient()
        self.assertNotEquals(notificationClient, None)

        clientUser = self.NotificationClientUser()
        clientUser.sendNotification("a")
        self.assertEquals(notificationClient.lines, ["a"])


class NotificationClientFactoryTests(TestCase):

    def setUp(self):
        self.client = StubNotificationClient(None, None, None)
        self.factory = NotificationClientFactory(self.client)
        self.factory.protocol = StubNotificationClientProtocol

    def test_connect(self):
        self.assertEquals(self.factory.isReady(), False)
        protocol = self.factory.buildProtocol(None)
        protocol.connectionMade()
        self.assertEquals(self.client.observers, set([protocol]))
        self.assertEquals(self.factory.isReady(), True)

        protocol.connectionLost(None)
        self.assertEquals(self.client.observers, set())
        self.assertEquals(self.factory.isReady(), False)


class StubNotificationClient(object):

    def __init__(self, reactor, host, port):
        self.lines = []
        self.observers = set()

    def send(self, uri):
        self.lines.append(uri)

    def addObserver(self, observer):
        self.observers.add(observer)

    def removeObserver(self, observer):
        self.observers.remove(observer)

    def connectionMade(self):
        pass

class StubNotificationClientProtocol(object):

    def __init__(self):
        self.lines = []

    def sendLine(self, line):
        self.lines.append(line)

    def connectionMade(self):
        self.client.addObserver(self)
        self.factory.connectionMade()

    def connectionLost(self, reason):
        self.client.removeObserver(self)
        self.factory.connected = False


class NotificationClientTests(TestCase):

    def setUp(self):
        self.client = NotificationClient(Clock(), None, None)
        self.client.factory = StubNotificationClientFactory()

    def test_sendWhileNotConnected(self):
        self.client.send("a")
        self.assertEquals(self.client.queued, set(["a"]))

    def test_sendWhileConnected(self):
        protocol = StubNotificationClientProtocol()
        self.client.addObserver(protocol)
        self.client.factory.connected = True
        self.client.send("a")
        self.assertEquals(self.client.queued, set())
        self.assertEquals(protocol.lines, ["a"])

    def test_sendQueue(self):
        self.client.send("a")
        self.assertEquals(self.client.queued, set(["a"]))
        protocol = StubNotificationClientProtocol()
        self.client.addObserver(protocol)
        self.client.factory.connected = True
        self.client.connectionMade()
        self.assertEquals(protocol.lines, ["a"])
        self.assertEquals(self.client.queued, set())


class StubNotificationClientFactory(object):

    def __init__(self):
        self.connected = False

    def isReady(self):
        return self.connected


class CoalescerTests(TestCase):

    def setUp(self):
        self.clock = Clock()
        self.notifier = StubNotifier()
        self.coalescer = Coalescer([self.notifier], reactor=self.clock)

    def test_delayedNotifications(self):
        self.coalescer.add("A")
        self.assertEquals(self.notifier.notifications, [])
        self.clock.advance(5)
        self.assertEquals(self.notifier.notifications, ["A"])

    def test_removeDuplicates(self):
        self.coalescer.add("A")
        self.coalescer.add("A")
        self.clock.advance(5)
        self.assertEquals(self.notifier.notifications, ["A"])


class StubNotifier(object):

    def __init__(self):
        self.notifications = []
        self.observers = set()
        self.playbackHistory = []

    def enqueue(self, uri):
        self.notifications.append(uri)

    def playback(self, protocol, old_seq):
        self.playbackHistory.append((protocol, old_seq))

    def addObserver(self, observer):
        self.observers.add(observer)

    def removeObserver(self, observer):
        self.observers.remove(observer)


class SimpleLineNotifierTests(TestCase):

    def setUp(self):
        self.clock = Clock()
        self.notifier = SimpleLineNotifier(None)
        self.coalescer = Coalescer([self.notifier], reactor=self.clock)

    def test_initialConnection(self):
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.connectionMade(protocol)
        self.assertEquals(protocol.lines, ["0"])

    def test_subsequentConnection(self):
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.connectionMade(protocol)
        protocol.lines = []
        self.notifier.connectionMade(protocol)
        self.assertEquals(protocol.lines, [])

    def test_send(self):
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.enqueue("A")
        self.assertEquals(protocol.lines, ["1 A"])

    def test_incrementSequence(self):
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.enqueue("A")
        self.notifier.enqueue("B")
        self.assertEquals(protocol.lines, ["1 A", "2 B"])

    def test_addObserver(self):
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.enqueue("A")
        self.assertEquals(protocol.lines, ["1 A"])

    def test_removeObserver(self):
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.removeObserver(protocol)
        self.notifier.enqueue("A")
        self.assertEquals(protocol.lines, [])

    def test_multipleObservers(self):
        protocol1 = StubProtocol()
        protocol2 = StubProtocol()
        self.notifier.addObserver(protocol1)
        self.notifier.addObserver(protocol2)
        self.notifier.enqueue("A")
        self.assertEquals(protocol1.lines, ["1 A"])
        self.assertEquals(protocol2.lines, ["1 A"])

    def test_duplicateObservers(self):
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.addObserver(protocol)
        self.notifier.enqueue("A")
        self.assertEquals(protocol.lines, ["1 A"])

    def test_playback(self):
        self.notifier.enqueue("A")
        self.notifier.enqueue("B")
        self.notifier.enqueue("C")
        protocol = StubProtocol()
        self.notifier.addObserver(protocol)
        self.notifier.playback(protocol, 1)
        self.assertEquals(protocol.lines, ["2 B", "3 C"])

    def test_reset(self):
        self.notifier.enqueue("A")
        self.assertEquals(self.notifier.history, {"A" : 1})
        self.assertEquals(self.notifier.latestSeq, 1)
        self.notifier.reset()
        self.assertEquals(self.notifier.history, {})
        self.assertEquals(self.notifier.latestSeq, 0)
        

class SimpleLineNotificationFactoryTests(TestCase):

    def test_buildProtocol(self):
        notifier = StubNotifier()
        factory = SimpleLineNotificationFactory(notifier)
        protocol = factory.buildProtocol(None)
        self.assertEquals(protocol.notifier, notifier)
        self.assertIn(protocol, notifier.observers)


class SimpleLineNotificationProtocolTests(TestCase):

    def setUp(self):
        self.notifier = StubNotifier()
        self.protocol = SimpleLineNotificationProtocol()
        self.protocol.notifier = self.notifier
        self.protocol.transport = StubTransport()
        self.notifier.addObserver(self.protocol)

    def test_connectionLost(self):
        self.protocol.connectionLost(None)
        self.assertNotIn(self.protocol, self.notifier.observers)

    def test_lineReceived(self):
        self.protocol.lineReceived("2")
        self.assertEquals(self.notifier.playbackHistory, [(self.protocol, 2)])

    def test_lineReceivedInvalid(self):
        self.protocol.lineReceived("bogus")
        self.assertEquals(self.notifier.playbackHistory, [])



class StubProtocol(object):

    def __init__(self):
        self.lines = []

    def sendLine(self, line):
        self.lines.append(line)


class StubTransport(object):

    def getPeer(self):
        return "peer"






class StubXmlStream(object):

    def __init__(self):
        self.elements = []

    def send(self, element):
        self.elements.append(element)

    def addOnetimeObserver(*args, **kwds):
        pass

    def addObserver(*args, **kwds):
        pass


class XMPPNotifierTests(TestCase):

    def test_sendWhileConnected(self):
        xmlStream = StubXmlStream()
        settings = { 'ServiceAddress' : 'pubsub.example.com' }
        notifier = XMPPNotifier(settings, reactor=Clock())
        notifier.streamOpened(xmlStream)
        notifier.enqueue("A")

        iq = xmlStream.elements[0]
        self.assertEquals(iq.name, "iq")

        pubsub = list(iq.elements())[0]
        self.assertEquals(pubsub.name, "pubsub")
        self.assertEquals(pubsub.uri, 'http://jabber.org/protocol/pubsub')

        publish = list(pubsub.elements())[0]
        self.assertEquals(publish.name, "publish")
        self.assertEquals(publish.uri, 'http://jabber.org/protocol/pubsub')
        self.assertEquals(publish['node'], "A")

    def test_sendWhileNotConnected(self):
        xmlStream = StubXmlStream()
        settings = { 'ServiceAddress' : 'pubsub.example.com' }
        notifier = XMPPNotifier(settings, Clock())
        notifier.enqueue("A")
        self.assertEquals(xmlStream.elements, [])


class XMPPNotificationFactoryTests(TestCase):

    def test_sendPresence(self):
        clock = Clock()
        xmlStream = StubXmlStream()
        settings = { 'ServiceAddress' : 'pubsub.example.com', 'JID' : 'jid',
            'Password' : 'password', 'KeepAliveSeconds' : 5 }
        notifier = XMPPNotifier(settings, reactor=clock)
        factory = XMPPNotificationFactory(notifier, settings, reactor=clock)
        factory.connected(xmlStream)
        factory.authenticated(xmlStream)

        self.assertEquals(len(xmlStream.elements), 1)
        presence = xmlStream.elements[0]
        self.assertEquals(presence.name, 'presence')

        clock.advance(5)

        self.assertEquals(len(xmlStream.elements), 2)
        presence = xmlStream.elements[1]
        self.assertEquals(presence.name, 'presence')

        factory.disconnected(xmlStream)
        clock.advance(5)
        self.assertEquals(len(xmlStream.elements), 2)
