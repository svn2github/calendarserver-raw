##
# Copyright (c) 2013 Apple Inc. All rights reserved.
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
txdav.who.util tests
"""

import os

from txdav.who.util import directoryFromConfig
from twisted.trial.unittest import TestCase
from twistedcaldav.config import ConfigDict
from twisted.python.filepath import FilePath
from txdav.who.augment import AugmentedDirectoryService
from twext.who.aggregate import DirectoryService as AggregateDirectoryService


class StubStore(object):
    pass



class UtilTest(TestCase):

    def setUp(self):
        sourceDir = FilePath(__file__).parent().child("accounts")
        self.serverRoot = os.path.abspath(self.mktemp())
        os.mkdir(self.serverRoot)
        self.dataRoot = os.path.join(self.serverRoot, "data")
        if not os.path.exists(self.dataRoot):
            os.makedirs(self.dataRoot)
        destDir = FilePath(self.dataRoot)

        accounts = destDir.child("accounts.xml")
        sourceAccounts = sourceDir.child("accounts.xml")
        accounts.setContent(sourceAccounts.getContent())

        resources = destDir.child("resources.xml")
        sourceResources = sourceDir.child("resources.xml")
        resources.setContent(sourceResources.getContent())

        augments = destDir.child("augments.xml")
        sourceAugments = sourceDir.child("augments.xml")
        augments.setContent(sourceAugments.getContent())


    def test_directoryFromConfig(self):

        config = ConfigDict(
            {
                "DataRoot": self.dataRoot,
                "DirectoryService": {
                    "Enabled": True,
                    "type": "XML",
                    "params": {
                        "xmlFile": "accounts.xml",
                        "recordTypes": ["users", "groups"],
                    },
                },
                "ResourceService": {
                    "Enabled": True,
                    "type": "XML",
                    "params": {
                        "xmlFile": "resources.xml",
                        "recordTypes": ["locations", "resources"],
                    },
                },
                "AugmentService": {
                    "Enabled": True,
                    # FIXME: This still uses an actual class name:
                    "type": "twistedcaldav.directory.augment.AugmentXMLDB",
                    "params": {
                        "xmlFiles": ["augments.xml"],
                    },
                },
            }
        )

        store = StubStore()
        service = directoryFromConfig(config, store=store)
        self.assertTrue(isinstance(service, AugmentedDirectoryService))
        self.assertTrue(isinstance(service._directory, AggregateDirectoryService))
        self.assertEquals(len(service._directory.services), 3)
