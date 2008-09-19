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

from twistedcaldav.config import config
from twistedcaldav.log import Logger
from twistedcaldav.py.plistlib import readPlist

log = Logger()

class ClusterManager(object):
    
    def __init__(self):
        self.updateConfig()

    def updateConfig(self):

        # Map of nodes to hosts/ips
        self.node_host_map = config.Clustering["NodeIDHostnameMap"]

        # Get this nodes' host id
        self.node_id = config.Clustering["NodeID"]

        # Read in the hosted users
        plist = readPlist(config.Clustering["UserNodeMapFile"])

        self.hosted_guids = plist()['guids']

    def isHostedGUID(self, guid):
        try:
            return self.hosted_guids[guid] == self.node_id
        except KeyError:
            return False 

    def getHostForGUID(self, guid):
        try:
            return self.node_host_map[self.hosted_guids[guid]]
        except KeyError:
            return None 
