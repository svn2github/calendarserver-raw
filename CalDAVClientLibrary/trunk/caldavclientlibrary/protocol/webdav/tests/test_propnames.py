##
# Copyright (c) 2007-2013 Apple Inc. All rights reserved.
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

from caldavclientlibrary.protocol.webdav.session import Session
from caldavclientlibrary.protocol.webdav.propnames import PropNames
from StringIO import StringIO
from caldavclientlibrary.protocol.webdav.definitions import headers
import unittest

class TestPropNames(unittest.TestCase):

    def test_Method(self):

        server = Session("www.example.com")
        request = PropNames(server, "/", headers.Depth0)
        self.assertEqual(request.getMethod(), "PROPFIND")



class TestRequestHeaders(unittest.TestCase):

    def test_Depth0Headers(self):

        server = Session("www.example.com")
        request = PropNames(server, "/", headers.Depth0)
        hdrs = request.generateRequestHeader()
        self.assertTrue("Depth: 0" in hdrs)
        self.assertFalse("Depth: 1" in hdrs)
        self.assertFalse("Depth: infinity" in hdrs)


    def test_Depth1Headers(self):

        server = Session("www.example.com")
        request = PropNames(server, "/", headers.Depth1)
        hdrs = request.generateRequestHeader()
        self.assertFalse("Depth: 0" in hdrs)
        self.assertTrue("Depth: 1" in hdrs)
        self.assertFalse("Depth: infinity" in hdrs)


    def test_DepthInfinityHeaders(self):

        server = Session("www.example.com")
        request = PropNames(server, "/", headers.DepthInfinity)
        hdrs = request.generateRequestHeader()
        self.assertFalse("Depth: 0" in hdrs)
        self.assertFalse("Depth: 1" in hdrs)
        self.assertTrue("Depth: infinity" in hdrs)



class TestRequestBody(unittest.TestCase):

    def test_GenerateXML(self):

        server = Session("www.example.com")
        request = PropNames(server, "/", headers.Depth0)
        os = StringIO()
        request.generateXML(os)
        self.assertEqual(os.getvalue(), """<?xml version='1.0' encoding='utf-8'?>
<ns0:propfind xmlns:ns0="DAV:">
  <ns0:propname />
</ns0:propfind>
""".replace("\n", "\r\n")
)



class TestResponse(unittest.TestCase):
    pass



class TestResponseHeaders(unittest.TestCase):
    pass



class TestResponseBody(unittest.TestCase):
    pass
