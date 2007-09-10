##
# Copyright (c) 2006-2007 Apple Inc. All rights reserved.
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
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##


"""
XML based server-to-server configuration file handling.
"""

__all__ = [
    "ServerToServerParser",
    "ServerToServerRecord",
]

import xml.dom.minidom

ELEMENT_SERVERS                 = "servers"
ELEMENT_SERVER                  = "server"
ELEMENT_URI                     = "uri"
ELEMENT_ALLOW_REQUESTS_FROM     = "allow-requests-from"
ELEMENT_ALLOW_REQUESTS_TO       = "allow-requests-to"
ELEMENT_DOMAINS                 = "domains"
ELEMENT_DOMAIN                  = "domain"

class ServerToServerParser(object):
    """
    Server-to-server configuration file parser.
    """
    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.xmlFile)

    def __init__(self, xmlFile):

        self.servers = []
        
        # Read in XML
        fd = open(xmlFile.path, "r")
        doc = xml.dom.minidom.parse(fd)
        fd.close()

        # Verify that top-level element is correct
        servers_node = doc._get_documentElement()
        if servers_node._get_localName() != ELEMENT_SERVERS:
            self.log("Ignoring file %r because it is not a server-to-server config file" % (self.xmlFile,))
            return
        self._parseXML(servers_node)
        
    def _parseXML(self, node):
        """
        Parse the XML root node from the server-to-server configuration document.
        @param node: the L{Node} to parse.
        """

        for child in node._get_childNodes():
            child_name = child._get_localName()
            if child_name is None:
                continue
            elif child_name == ELEMENT_SERVER:
                self.servers.append(ServerToServerRecord())
                self.servers[-1].parseXML(child)
                
class ServerToServerRecord (object):
    """
    Contains server-to-server details.
    """
    def __init__(self):
        """
        @param recordType: record type for directory entry.
        """
        self.uri = ""
        self.allow_from = False
        self.allow_to = True
        self.domains = []

    def parseXML(self, node):
        for child in node._get_childNodes():
            child_name = child._get_localName()
            if child_name is None:
                continue
            elif child_name == ELEMENT_URI:
                if child.firstChild is not None:
                    self.uri = child.firstChild.data.encode("utf-8")
            elif child_name == ELEMENT_ALLOW_REQUESTS_FROM:
                self.allow_from = True
            elif child_name == ELEMENT_ALLOW_REQUESTS_TO:
                self.allow_to = True
            elif child_name == ELEMENT_DOMAINS:
                self._parseDomains(child)
            else:
                raise RuntimeError("[%s] Unknown attribute: %s" % (self.__class__, child_name,))
        
        self._parseDetails()

    def _parseDomains(self, node):
        for child in node._get_childNodes():
            if child._get_localName() == ELEMENT_DOMAIN:
                if child.firstChild is not None:
                    self.domains.append(child.firstChild.data.encode("utf-8"))

    def _parseDetails(self):
        # Extract scheme, host, port and path
        if self.uri.startswith("http://"):
            self.ssl = False
            rest = self.uri[7:]
        elif self.uri.startswith("https://"):
            self.ssl = True
            rest = self.uri[8:]
        
        splits = rest.split("/", 1)
        hostport = splits[0].split(":")
        self.host = hostport[0]
        if len(hostport) > 1:
            self.port = int(hostport[1])
        else:
            self.port = {False:80, True:443}[self.ssl]
        self.path = "/"
        if len(splits) > 1:
            self.path += splits[1]
