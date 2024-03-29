##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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

from twistedcaldav.directory.apache import BasicDirectoryService
from twistedcaldav.directory.xmlfile import XMLDirectoryService
from twistedcaldav.directory.aggregate import AggregateDirectoryService

from twistedcaldav.directory.test.test_apache import digestRealm, basicUserFile, groupFile
from twistedcaldav.directory.test.test_xmlfile import xmlFile

import twistedcaldav.directory.test.util

apache_prefix = "apache:"
xml_prefix = "xml:"

testServices = (
    (apache_prefix, twistedcaldav.directory.test.test_apache.Apache  ),
    (xml_prefix   , twistedcaldav.directory.test.test_xmlfile.XMLFile),
)

class AggregatedDirectories (twistedcaldav.directory.test.util.DirectoryTestCase):
    def _recordTypes(self):
        recordTypes = set()
        for prefix, testClass in testServices:
            for recordType in testClass.recordTypes:
                recordTypes.add(prefix + recordType)
        return recordTypes

    def _records(key):
        def get(self):
            records = {}
            for prefix, testClass in testServices:
                for record, info in getattr(testClass, key).iteritems():
                    info = dict(info)
                    info["prefix"] = prefix
                    info["members"] = tuple(
                        (t, prefix + s) for t, s in info.get("members", {})
                    )
                    records[prefix + record] = info
            return records
        return get

    recordTypes = property(_recordTypes)
    users = property(_records("users"))
    groups = property(_records("groups"))
    locations = property(_records("locations"))
    resources = property(_records("resources"))

    recordTypePrefixes = tuple(s[0] for s in testServices)

    def service(self):
        """
        Returns an IDirectoryService.
        """
        apacheService = BasicDirectoryService(digestRealm, basicUserFile, groupFile)
        apacheService.recordTypePrefix = apache_prefix

        xmlService = XMLDirectoryService(xmlFile)
        xmlService.recordTypePrefix = xml_prefix

        return AggregateDirectoryService((apacheService, xmlService))
