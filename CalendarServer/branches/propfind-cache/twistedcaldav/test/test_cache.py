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

from new import instancemethod

from twisted.trial.unittest import TestCase

from twistedcaldav.cache import CacheChangeNotifier, CacheTokensProperty

from twistedcaldav.test.util import InMemoryPropertyStore


def _newCacheTokens(prefix):
    def _(self):
        called = getattr(self, '_%scalled' % (prefix,), 0)

        token = '%sToken%d' % (prefix, called)
        setattr(self, '_%scalled' % (prefix,), called + 1)
        return token

    return _


class CacheChangeNotifierTests(TestCase):
    def setUp(self):
        self.props = InMemoryPropertyStore()
        self.ccn = CacheChangeNotifier(self.props)
        self.ccn._newPropertyToken = instancemethod(_newCacheTokens('prop'),
                                                    self.ccn,
                                                    CacheChangeNotifier)
        self.ccn._newDataToken = instancemethod(_newCacheTokens('data'),
                                                self.ccn,
                                                CacheChangeNotifier)

    def test_unsetPropertiesAreProvisioned(self):
        self.ccn.propertiesChanged()
        tokens = self.props._properties[CacheTokensProperty.qname()
                                        ].children[0].data
        self.assertEquals(
            tokens,
            'propToken0:dataToken0')

    def test_changedPropertiesChangesPropToken(self):
        self.ccn.propertiesChanged()
        self.ccn.propertiesChanged()
        tokens = self.props._properties[CacheTokensProperty.qname()
                                        ].children[0].data
        self.assertEquals(
            tokens,
            'propToken1:dataToken0')


    def test_changedDataChangesDataToken(self):
        self.ccn.dataChanged()
        self.ccn.dataChanged()
        tokens = self.props._properties[CacheTokensProperty.qname()
                                        ].children[0].data
        self.assertEquals(
            tokens,
            'propToken0:dataToken1')
