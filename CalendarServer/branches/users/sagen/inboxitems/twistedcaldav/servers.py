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

from twext.python.log import Logger

from twistedcaldav.client.pool import installPool
from twistedcaldav.config import config, fullServerPath
from twistedcaldav.xmlutil import readXML
import urlparse
import socket

"""
XML based server configuration file handling.

This is used in an environment where more than one server is being used within a single domain. i.e., all
the principals across the whole domain need to be able to directly schedule each other and know of each others
existence. A common scenario would be a production server and a development/test server.

Each server is identified by an id and url. The id is used when assigning principals to a specific server. Each
server can also support multiple partitions, and each of those is identified by an id and url, with the id also
being used to assign principals to a specific partition.
"""

__all__ = [
    "Servers",
]

log = Logger()

class ServersDB(object):
    """
    Represents the set of servers within the same domain.
    """
    
    def __init__(self):
        
        self._servers = {}
        self._xmlFile = None
        self._thisServer = None

    def load(self, xmlFile=None):
        if self._xmlFile is None or xmlFile is not None:
            self._servers = {}
            if xmlFile:
                self._xmlFile = xmlFile
            else:
                self._xmlFile = fullServerPath(
                    config.ConfigRoot,
                    config.Servers.ConfigFile
                )
        self._servers = ServersParser.parse(self._xmlFile)
        for server in self._servers.values():
            if server.thisServer:
                self._thisServer = server
                break
        else:
            raise ValueError("No server in %s matches this server." % (self._xmlFile,))
    
    def clear(self):
        self._servers = {}
        self._xmlFile = None
        self._thisServer = None

    def getServerById(self, id):
        return self._servers.get(id)
        
    def getServerURIById(self, id):
        try:
            return self._servers[id].uri
        except KeyError:
            return None
    
    def getThisServer(self):
        return self._thisServer

Servers = ServersDB()   # Global server DB

class Server(object):
    """
    Represents a server which may itself be partitioned.
    """
    
    def __init__(self):
        self.id = None
        self.uri = None
        self.thisServer = False
        self.ips = set()
        self.partitions = {}
        self.partitions_ips = set()
        self.isImplicit = True
    
    def check(self):
        # Check whether this matches the current server
        parsed_uri = urlparse.urlparse(self.uri)
        if parsed_uri.hostname == config.ServerHostName:
            if parsed_uri.scheme == "http":
                if config.HTTPPort:
                    self.thisServer = parsed_uri.port in (config.HTTPPort,) + tuple(config.BindHTTPPorts)
            elif parsed_uri.scheme == "https":
                if config.SSLPort:
                    self.thisServer = parsed_uri.port in (config.SSLPort,) + tuple(config.BindSSLPorts)
        
        # Need to cache IP addresses
        try:
            _ignore_host, _ignore_aliases, ips = socket.gethostbyname_ex(parsed_uri.hostname)
        except socket.gaierror, e:
            log.error("Unable to lookup ip-addr for server '%s': %s" % (parsed_uri.hostname, str(e)))
            ips = ()
        self.ips = set(ips)

        for uri in self.partitions.values():
            parsed_uri = urlparse.urlparse(uri)
            try:
                _ignore_host, _ignore_aliases, ips = socket.gethostbyname_ex(parsed_uri.hostname)
            except socket.gaierror, e:
                log.error("Unable to lookup ip-addr for partition '%s': %s" % (parsed_uri.hostname, str(e)))
                ips = ()
            self.partitions_ips.update(ips)
    
    def checkThisIP(self, ip):
        """
        Check that the passed in IP address corresponds to this server or one of its partitions.
        """
        return (ip in self.ips) or (ip in self.partitions_ips)

    def addPartition(self, id, uri):
        self.partitions[id] = uri
    
    def getPartitionURIForId(self, id):
        return self.partitions.get(id)
    
    def installReverseProxies(self, ownUID, maxClients):
        
        for partition, url in self.partitions.iteritems():
            if partition != ownUID:
                installPool(
                    partition,
                    url,
                    maxClients,
                )
    
        
        
ELEMENT_SERVERS                 = "servers"
ELEMENT_SERVER                  = "server"
ELEMENT_ID                      = "id"
ELEMENT_URI                     = "uri"
ELEMENT_PARTITIONS              = "partitions"
ELEMENT_PARTITION               = "partition"
ATTR_IMPLICIT                   = "implicit"
ATTR_VALUE_YES                  = "yes"
ATTR_VALUE_NO                   = "no"

class ServersParser(object):
    """
    Servers configuration file parser.
    """
    @staticmethod
    def parse(xmlFile):

        results = {}

        # Read in XML
        try:
            _ignore_tree, servers_node = readXML(xmlFile, ELEMENT_SERVERS)
        except ValueError, e:
            log.error("XML parse error for '%s' because: %s" % (xmlFile, e,), raiseException=RuntimeError)

        for child in servers_node.getchildren():
            
            if child.tag != ELEMENT_SERVER:
                log.error("Unknown server type: '%s' in servers file: '%s'" % (child.tag, xmlFile,), raiseException=RuntimeError)

            server = Server()
            server.isImplicit = child.get(ATTR_IMPLICIT, ATTR_VALUE_YES) == ATTR_VALUE_YES

            for node in child.getchildren():
                if node.tag == ELEMENT_ID:
                    server.id = node.text
                elif node.tag == ELEMENT_URI:
                    server.uri = node.text
                elif node.tag == ELEMENT_PARTITIONS:
                    ServersParser._parsePartition(xmlFile, node, server)
                else:
                    log.error("Invalid element '%s' in servers file: '%s'" % (node.tag, xmlFile,), raiseException=RuntimeError)

            if server.id is None or server.uri is None:
                log.error("Invalid partition '%s' in servers file: '%s'" % (child.tag, xmlFile,), raiseException=RuntimeError)

            server.check()
            results[server.id] = server

        return results

    @staticmethod
    def _parsePartition(xmlFile, partitions, server):

        for child in partitions.getchildren():
            
            if child.tag != ELEMENT_PARTITION:
                log.error("Unknown partition type: '%s' in servers file: '%s'" % (child.tag, xmlFile,), raiseException=RuntimeError)

            id = None
            uri = None
            for node in child.getchildren():
                if node.tag == ELEMENT_ID:
                    id = node.text
                elif node.tag == ELEMENT_URI:
                    uri = node.text
                else:
                    log.error("Invalid element '%s' in augment file: '%s'" % (node.tag, xmlFile,), raiseException=RuntimeError)
        
            if id is None or uri is None:
                log.error("Invalid partition '%s' in servers file: '%s'" % (child.tag, xmlFile,), raiseException=RuntimeError)
            
            server.addPartition(id, uri)
