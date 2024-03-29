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

from twisted.internet.defer import inlineCallbacks

from twext.web2.test.test_server import SimpleRequest

from twext.web2.dav.davxml import Principal
from twext.web2.dav.davxml import Unauthenticated
from twext.web2.dav.element.rfc2518 import HRef

from twistedcaldav.resource import (
    CalDAVResource, CommonHomeResource, CalendarHomeResource,
    AddressBookHomeResource)

from twistedcaldav.test.util import InMemoryPropertyStore
from twistedcaldav.test.util import TestCase
from twistedcaldav.config import config

from twistedcaldav.test.util import patchConfig


class StubProperty(object):
    def qname(self):
        return "StubQnamespace", "StubQname"

class StubHome(object):
    def properties(self):
        return []
    
    def addNotifier(self, notifier):
        pass

    def nodeName(self):
        return "xyzzy" if self.pushWorking else None

    def notifierID(self):
        return "xyzzy"

    def setPushWorking(self, status):
        self.pushWorking = status

class CalDAVResourceTests(TestCase):
    def setUp(self):
        TestCase.setUp(self)
        self.resource = CalDAVResource()
        self.resource._dead_properties = InMemoryPropertyStore()

    def test_writeDeadPropertyWritesProperty(self):
        prop = StubProperty()
        self.resource.writeDeadProperty(prop)
        self.assertEquals(self.resource._dead_properties.get(("StubQnamespace", "StubQname")),
                          prop)

class CommonHomeResourceTests(TestCase):

    def test_commonHomeliveProperties(self):
        resource = CommonHomeResource(None, None, None, StubHome())
        self.assertTrue(('http://calendarserver.org/ns/', 'push-transports') in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'pushkey') in resource.liveProperties())


    def test_calendarHomeliveProperties(self):
        resource = CalendarHomeResource(None, None, None, StubHome())
        self.assertTrue(('http://calendarserver.org/ns/', 'push-transports') in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'pushkey') in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'xmpp-uri') in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'xmpp-heartbeat-uri') in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'xmpp-server') in resource.liveProperties())


    def test_addressBookHomeliveProperties(self):
        resource = AddressBookHomeResource(None, None, None, StubHome())
        self.assertTrue(('http://calendarserver.org/ns/', 'push-transports') in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'pushkey') in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'xmpp-uri') not in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'xmpp-heartbeat-uri') not in resource.liveProperties())
        self.assertTrue(('http://calendarserver.org/ns/', 'xmpp-server') not in resource.liveProperties())


    @inlineCallbacks
    def test_push404(self):
        """
        If push is configured, yet we can't communicate with the XMPP server
        for whatever reason, readProperty on the various push-related properties
        should return None
        """
        resource = CalendarHomeResource(None, None, None, StubHome())
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'push-transports'), None)), None)

        self.patch(config, "ServerHostName", "cal.example.com")
        self.patch(config, "SSLPort", 8443)
        self.patch(config.Notifications, "Enabled", True)
        self.patch(config.Notifications.Services, "XMPPNotifier", 
            {
                "Enabled" : True,
                "Host" : "xmpp.example.com",
                "Port" : 5218,
                "ServiceAddress" : "pubsub.xmpp.example.com",
                "Service" : "twistedcaldav.notify.XMPPNotifierService",
                "HeartbeatMinutes" : 30,
            }
        )

        # Verify that when push is "working" we get a value
        resource._newStoreHome.setPushWorking(True)
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'push-transports'), None)).toxml(), "<?xml version='1.0' encoding='UTF-8'?>\n<push-transports xmlns='http://calendarserver.org/ns/'>\r\n  <transport type='XMPP'>\r\n    <xmpp-server>xmpp.example.com:5218</xmpp-server>\r\n    <xmpp-uri>xmpp:pubsub.xmpp.example.com?pubsub;node=/cal.example.com/xyzzy/</xmpp-uri>\r\n  </transport>\r\n</push-transports>")
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'pushkey'), None)).toxml(), "<?xml version='1.0' encoding='UTF-8'?>\n<pushkey xmlns='http://calendarserver.org/ns/'>xyzzy</pushkey>")
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'xmpp-uri'), None)).toxml(), "<?xml version='1.0' encoding='UTF-8'?>\n<xmpp-uri xmlns='http://calendarserver.org/ns/'>xmpp:pubsub.xmpp.example.com?pubsub;node=/cal.example.com/xyzzy/</xmpp-uri>")
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'xmpp-heartbeat-uri'), None)).toxml(), "<?xml version='1.0' encoding='UTF-8'?>\n<xmpp-heartbeat xmlns='http://calendarserver.org/ns/'>\r\n  <xmpp-heartbeat-uri>xmpp:pubsub.xmpp.example.com?pubsub;node=/cal.example.com/</xmpp-heartbeat-uri>\r\n  <xmpp-heartbeat-minutes>30</xmpp-heartbeat-minutes>\r\n</xmpp-heartbeat>")
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'xmpp-server'), None)).toxml(), "<?xml version='1.0' encoding='UTF-8'?>\n<xmpp-server xmlns='http://calendarserver.org/ns/'>xmpp.example.com:5218</xmpp-server>")

        # Verify that when push is "not working" we get None
        resource._newStoreHome.setPushWorking(False)
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'push-transports'), None)), None)
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'pushkey'), None)), None)
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'xmpp-uri'), None)), None)
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'xmpp-heartbeat-uri'), None)), None)
        self.assertEqual((yield resource.readProperty(('http://calendarserver.org/ns/', 'xmpp-server'), None)), None)



class OwnershipTests(TestCase):
    """
    L{CalDAVResource.isOwner} determines if the authenticated principal of the
    given request is the owner of that resource.
    """

    @inlineCallbacks
    def test_isOwnerUnauthenticated(self):
        """
        L{CalDAVResource.isOwner} returns C{False} for unauthenticated requests.
        """
        site = None
        request = SimpleRequest(site, "GET", "/not/a/real/url/")
        request.authzUser = request.authnUser = Principal(Unauthenticated())
        rsrc = CalDAVResource()
        rsrc.owner = lambda igreq: HRef("/somebody/")
        self.assertEquals((yield rsrc.isOwner(request)), False)


    @inlineCallbacks
    def test_isOwnerNo(self):
        """
        L{CalDAVResource.isOwner} returns C{True} for authenticated requests
        with a principal that matches the resource's owner.
        """
        site = None
        request = SimpleRequest(site, "GET", "/not/a/real/url/")
        theOwner = Principal(HRef("/yes-i-am-the-owner/"))
        request.authzUser = request.authnUser = theOwner
        rsrc = CalDAVResource()
        rsrc.owner = lambda igreq: HRef("/no-i-am-not-the-owner/")
        self.assertEquals((yield rsrc.isOwner(request)), False)


    @inlineCallbacks
    def test_isOwnerYes(self):
        """
        L{CalDAVResource.isOwner} returns C{True} for authenticated requests
        with a principal that matches the resource's owner.
        """
        site = None
        request = SimpleRequest(site, "GET", "/not/a/real/url/")
        theOwner = Principal(HRef("/yes-i-am-the-owner/"))
        request.authzUser = request.authnUser = theOwner
        rsrc = CalDAVResource()
        rsrc.owner = lambda igreq: HRef("/yes-i-am-the-owner/")
        self.assertEquals((yield rsrc.isOwner(request)), True)


    @inlineCallbacks
    def test_isOwnerAdmin(self):
        """
        L{CalDAVResource.isOwner} returns C{True} for authenticated requests
        with a principal that matches any principal configured in the
        L{AdminPrincipals} list.
        """
        theAdmin = "/read-write-admin/"
        patchConfig(self, AdminPrincipals=[theAdmin])
        site = None
        request = SimpleRequest(site, "GET", "/not/a/real/url/")
        request.authzUser = request.authnUser = Principal(HRef(theAdmin))
        rsrc = CalDAVResource()
        rsrc.owner = lambda igreq: HRef("/some-other-user/")
        self.assertEquals((yield rsrc.isOwner(request)), True)


    @inlineCallbacks
    def test_isOwnerReadPrincipal(self):
        """
        L{CalDAVResource.isOwner} returns C{True} for authenticated requests
        with a principal that matches any principal configured in the
        L{AdminPrincipals} list.
        """
        theAdmin = "/read-only-admin/"
        patchConfig(self, ReadPrincipals=[theAdmin])
        site = None
        request = SimpleRequest(site, "GET", "/not/a/real/url/")
        request.authzUser = request.authnUser = Principal(HRef(theAdmin))
        rsrc = CalDAVResource()
        rsrc.owner = lambda igreq: HRef("/some-other-user/")
        self.assertEquals((yield rsrc.isOwner(request)), True)

