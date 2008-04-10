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

"""
Unittests for CalDAV-aware static resources found in twistedcaldav.static
"""
from new import instancemethod

from twisted.trial.unittest import TestCase
from twistedcaldav.test.test_resource import StubLocatingRequest
from twistedcaldav.test.test_resource import StubParentResource

from twistedcaldav.static import CalendarHomeFile
from twistedcaldav.static import CacheTokensProperty
from twisted.web2.http import HTTPError, StatusResponse

class InMemoryPropertyStore(object):
    def __init__(self, resource):
        self.resource = resource
        self._properties = {}

    def get(self, qname):
        data = self._properties.get(qname)
        if data is None:
            raise HTTPError(StatusResponse(404, "No such property"))
        return data

    def set(self, property):
        self._properties[property.qname()] = property


def _newCacheTokenStub(self, property=False, data=False):
    if not hasattr(self, '_called'):
        self._called = [0,0]

    if property:
        token = 'propertyToken%d' % (self._called[0],)
        self._called[0] += 1
    elif data:
        token = 'dataToken%d' % (self._called[1],)
        self._called[1] += 1

    return token


class CalendarHomeChangedTests(TestCase):
    def setUp(self):
        self.parent = StubParentResource()
        self.parent.principalCollections = (lambda: [])
        self.request = StubLocatingRequest({'/calendars/users/': self.parent})

        self.myCalendarHome = CalendarHomeFile(self.mktemp(),
                                               self.parent,
                                               object())
        self.properties = InMemoryPropertyStore(self.myCalendarHome)
        self.myCalendarHome._dead_properties = self.properties
        self.properties._properties[CacheTokensProperty.qname()] = (
            CacheTokensProperty.fromString('propertyToken0:dataToken0'))

        self.myCalendarHome._newCacheToken = instancemethod(
            _newCacheTokenStub,
            self.myCalendarHome,
            CalendarHomeFile)

        self.myCalendarHome._newCacheToken(property=True)
        self.myCalendarHome._newCacheToken(data=True)


    def test_doesntPropogateToParent(self):
        self.myCalendarHome.changed(self.request, '/calendars/users/dreid')
        self.assertEquals(self.parent.changedArgs, None)
        self.assertEquals(self.parent.changedKwArgs, None)


    def _test(self, properties=False, data=False, expectedTokens=None):
        self.myCalendarHome.changed(self.request, '/calendars/users/dreid',
                                    properties=properties, data=data)

        tokens = self.properties._properties[CacheTokensProperty.qname()]
        self.assertEquals(tokens.children[0].data, expectedTokens)


    def test_changesPropertyToken(self):
        self._test(properties=True, expectedTokens='propertyToken1:dataToken0')


    def test_changesDataToken(self):
        self._test(data=True, expectedTokens='propertyToken0:dataToken1')


    def test_changesBothTokens(self):
        self._test(properties=True, data=True,
                   expectedTokens='propertyToken1:dataToken1')


    def test_initializesNonExistantProperty(self):
        self.properties._properties = {}
        self.myCalendarHome._called = [0,0]
        self._test(properties=False, data=False,
                   expectedTokens='propertyToken0:dataToken0')
