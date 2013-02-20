# -*- test-case-name: twext.who.test.test_aggregate -*-
##
# Copyright (c) 2006-2013 Apple Inc. All rights reserved.
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
Directory service which aggregates multiple directory services.
"""

__all__ = [
    "DirectoryService",
    "DirectoryRecord",
]

from itertools import chain

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.defer import gatherResults

from twext.who.idirectory import IDirectoryService
from twext.who.index import DirectoryService as BaseDirectoryService
from twext.who.index import DirectoryRecord


class DirectoryService(BaseDirectoryService):
    """
    Aggregate directory service.
    """

    def __init__(self, realmName, services):
        for service in services:
            if not IDirectoryService.implementedBy(service):
                raise ValueError("Not a directory service: %s" % (service,))

        BaseDirectoryService.__init__(self, realmName)

        self._services = tuple(services)


    @property
    def services(self):
        return self._services


    @inlineCallbacks
    def recordTypes(self):
        if not hasattr(self, "_recordTypes"):
            recordTypes = set()
            for service in self._services:
                for recordType in (yield service.recordTypes()):
                    recordTypes.add(recordType)

            self._recordTypes = recordTypes

        returnValue(self._recordTypes)


    def recordsFromExpression(self, expression, records=None):
        ds = []
        for service in self.services:
            d = service.recordsFromExpression(expression, records)
            ds.append(d)

        return gatherResults(ds).addCallback(chain)
