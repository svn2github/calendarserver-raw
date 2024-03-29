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

from twisted.web2.test.test_server import SimpleRequest
from twistedcaldav.config import config
from twistedcaldav.servers import Servers, SERVER_SECRET_HEADER
from twistedcaldav.test.test_tap import CleanupHelper
from twistedcaldav.test.util import TestCase
import StringIO as StringIO

class ServerTests(CleanupHelper, TestCase):

    data1 = """<?xml version="1.0" encoding="utf-8"?>
<servers>
  <server>
    <id>00001</id>
    <uri>http://caldav1.example.com:8008</uri>
    <allowed-from>127.0.0.1</allowed-from>
    <shared-secret>foobar</shared-secret>
  </server>
  <server>
    <id>00002</id>
    <uri>https://caldav2.example.com:8843</uri>
    <partitions>
        <partition>
            <id>A</id>
            <uri>https://machine1.example.com:8443</uri>
        </partition>
        <partition>
            <id>B</id>
            <uri>https://machine2.example.com:8443</uri>
        </partition>
    </partitions>
  </server>
</servers>
"""

    data2 = """<?xml version="1.0" encoding="utf-8"?>
<servers>
  <server>
    <id>00001</id>
    <uri>http://caldav1.example.com:8008</uri>
    <allowed-from>localhost</allowed-from>
    <shared-secret>foobar</shared-secret>
  </server>
  <server>
    <id>00002</id>
    <uri>https://caldav2.example.com:8843</uri>
    <partitions>
        <partition>
            <id>A</id>
            <uri>https://machine1.example.com:8443</uri>
        </partition>
        <partition>
            <id>B</id>
            <uri>https://machine2.example.com:8443</uri>
        </partition>
    </partitions>
  </server>
</servers>
"""

    def patch(self, obj, attribute, value):
        """
        Monkey patch an object for the duration of the test.

        The monkey patch will be reverted at the end of the test using the
        L{addCleanup} mechanism.

        The L{MonkeyPatcher} is returned so that users can restore and
        re-apply the monkey patch within their tests.

        @param obj: The object to monkey patch.
        @param attribute: The name of the attribute to change.
        @param value: The value to set the attribute to.
        @return: A L{monkey.MonkeyPatcher} object.
        """
        old_value = getattr(obj, attribute)
        setattr(obj, attribute, value)
        self.addCleanup(lambda:setattr(obj, attribute, old_value))


    def _setupServers(self, data=data1):
        self.patch(config, "ServerHostName", "caldav1.example.com")
        self.patch(config, "HTTPPort", 8008)

        xmlFile = StringIO.StringIO(data)
        servers = Servers
        servers.load(xmlFile, ignoreIPLookupFailures=True)

        return servers

    def test_read_ok(self):
        
        servers = self._setupServers()

        self.assertTrue(servers.getServerById("00001") is not None)
        self.assertTrue(servers.getServerById("00002") is not None)

        self.assertEqual(servers.getServerById("00001").uri, "http://caldav1.example.com:8008")
        self.assertEqual(servers.getServerById("00002").uri, "https://caldav2.example.com:8843")

        self.assertEqual(servers.getServerById("00001").allowed_from_ips, set(("127.0.0.1",)))
        self.assertEqual(servers.getServerById("00002").allowed_from_ips, set())

        self.assertEqual(servers.getServerById("00001").shared_secret, "foobar")
        self.assertEqual(servers.getServerById("00002").shared_secret, None)

        self.assertEqual(len(servers.getServerById("00001").partitions), 0)
        self.assertEqual(len(servers.getServerById("00002").partitions), 2)

        self.assertEqual(servers.getServerById("00002").getPartitionURIForId("A"), "https://machine1.example.com:8443")
        self.assertEqual(servers.getServerById("00002").getPartitionURIForId("B"), "https://machine2.example.com:8443")

    def test_this_server(self):
        
        servers = self._setupServers()

        self.assertTrue(servers.getServerById("00001").thisServer)
        self.assertFalse(servers.getServerById("00002").thisServer)
        
        self.patch(config, "ServerHostName", "caldav2.example.com")
        self.patch(config, "SSLPort", 8443)
        self.patch(config, "BindSSLPorts", [8843])
        
        xmlFile = StringIO.StringIO(ServerTests.data1)
        servers = Servers
        servers.load(xmlFile, ignoreIPLookupFailures=True)

        self.assertFalse(servers.getServerById("00001").thisServer)
        self.assertTrue(servers.getServerById("00002").thisServer)

    def test_check_this_ip(self):

        servers = self._setupServers()
        servers.getServerById("00001").ips = set(("127.0.0.2",))
        servers.getServerById("00002").ips = set(("127.0.0.3",))
        
        self.assertTrue(servers.getServerById("00001").checkThisIP("127.0.0.2"))
        self.assertFalse(servers.getServerById("00001").checkThisIP("127.0.0.3"))

    def test_check_allowed_from(self):

        for servers in (self._setupServers(), self._setupServers(data=self.data2),):
            self.assertTrue(servers.getServerById("00001").hasAllowedFromIP())
            self.assertFalse(servers.getServerById("00002").hasAllowedFromIP())

            self.assertTrue(servers.getServerById("00001").checkAllowedFromIP("127.0.0.1"))
            self.assertFalse(servers.getServerById("00001").checkAllowedFromIP("127.0.0.2"))
            self.assertFalse(servers.getServerById("00001").checkAllowedFromIP("127.0.0.3"))
            self.assertFalse(servers.getServerById("00002").checkAllowedFromIP("127.0.0.1"))
            self.assertFalse(servers.getServerById("00002").checkAllowedFromIP("127.0.0.2"))
            self.assertFalse(servers.getServerById("00002").checkAllowedFromIP("127.0.0.3"))

    def test_check_shared_secret(self):

        servers = self._setupServers()
        
        request = SimpleRequest(None, "POST", "/ischedule")
        request.headers.addRawHeader(SERVER_SECRET_HEADER, "foobar")
        self.assertTrue(servers.getServerById("00001").checkSharedSecret(request))
        
        request = SimpleRequest(None, "POST", "/ischedule")
        request.headers.addRawHeader(SERVER_SECRET_HEADER, "foobar1")
        self.assertFalse(servers.getServerById("00001").checkSharedSecret(request))
        
        request = SimpleRequest(None, "POST", "/ischedule")
        self.assertFalse(servers.getServerById("00001").checkSharedSecret(request))
        
        request = SimpleRequest(None, "POST", "/ischedule")
        request.headers.addRawHeader(SERVER_SECRET_HEADER, "foobar")
        self.assertFalse(servers.getServerById("00002").checkSharedSecret(request))
        
        request = SimpleRequest(None, "POST", "/ischedule")
        request.headers.addRawHeader(SERVER_SECRET_HEADER, "foobar1")
        self.assertFalse(servers.getServerById("00002").checkSharedSecret(request))
        
        request = SimpleRequest(None, "POST", "/ischedule")
        self.assertTrue(servers.getServerById("00002").checkSharedSecret(request))
        