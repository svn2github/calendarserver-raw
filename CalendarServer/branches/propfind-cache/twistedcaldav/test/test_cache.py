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

from twisted.python.filepath import FilePath

from twistedcaldav.cache import CacheChangeNotifier
from twistedcaldav.cache import CacheTokensProperty
from twistedcaldav.cache import CacheChangeObserver
from twistedcaldav.cache import PropfindCachingResource

from twistedcaldav.test.util import InMemoryPropertyStore


def _newCacheTokens(prefix):
    def _(self):
        called = getattr(self, '_%scalled' % (prefix,), 0)

        token = '%sToken%d' % (prefix, called)
        setattr(self, '_%scalled' % (prefix,), called + 1)
        return token

    return _



class StubCacheChangeObserver(CacheChangeObserver):
    def __init__(self, ps):
        self._ps = ps
        self.fp = self._ps.resource.fp

        self._oldPropToken = None
        self._oldDataToken = None
        self.curPropToken = None
        self.curDataToken = None

    def _loadTokens(self):
        self._dataToken = self.curDataToken
        self._propToken = self.curPropToken



class StubRequest(object):
    def __init__(self, method, uri, authnUser):
        self.method = method
        self.uri = uri
        self.authnUser = authnUser

        self.cacheRequest = False



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



class CacheChangeObserverTests(TestCase):
    def setUp(self):
        self.props = InMemoryPropertyStore()
        self.props._properties[CacheTokensProperty.qname()
                               ] = CacheTokensProperty.fromString(
            'propToken0:dataToken0')
        self.observer = CacheChangeObserver(self.props)


    def test_propertiesHaveChangedNewObserver(self):
        self.assertEquals(self.observer.propertiesHaveChanged(), True)


    def test_propertiesHaveChanged(self):
        self.assertEquals(self.observer.propertiesHaveChanged(), True)

        self.props._properties[CacheTokensProperty.qname()
                               ] = CacheTokensProperty.fromString(
            'propToken1:dataToken0')

        self.assertEquals(self.observer.propertiesHaveChanged(), True)


    def test_propertiesHaveNotChanged(self):
        self.assertEquals(self.observer.propertiesHaveChanged(), True)
        self.assertEquals(self.observer.propertiesHaveChanged(), False)


    def test_propertiesDoNotChangeData(self):
        self.assertEquals(self.observer.propertiesHaveChanged(), True)
        self.assertEquals(self.observer.dataHasChanged(), True)

        self.props._properties[CacheTokensProperty.qname()
                               ] = CacheTokensProperty.fromString(
            'propToken1:dataToken0')

        self.assertEquals(self.observer.propertiesHaveChanged(), True)
        self.assertEquals(self.observer.dataHasChanged(), False)


    def test_dataHasChanged(self):
        self.assertEquals(self.observer.dataHasChanged(), True)

        self.props._properties[CacheTokensProperty.qname()
                               ] = CacheTokensProperty.fromString(
            'propToken0:dataToken1')

        self.assertEquals(self.observer.dataHasChanged(), True)


    def test_dataHasChangedNewObserver(self):
        self.assertEquals(self.observer.dataHasChanged(), True)


    def test_dataHasNotChanged(self):
        self.assertEquals(self.observer.dataHasChanged(), True)
        self.assertEquals(self.observer.dataHasChanged(), False)


    def test_dataDoesNotChangeProperties(self):
        self.assertEquals(self.observer.dataHasChanged(), True)
        self.assertEquals(self.observer.propertiesHaveChanged(), True)

        self.props._properties[CacheTokensProperty.qname()
                               ] = CacheTokensProperty.fromString(
            'propToken0:dataToken1')

        self.assertEquals(self.observer.dataHasChanged(), True)
        self.assertEquals(self.observer.propertiesHaveChanged(), False)



class PropfindCachingResourceTests(TestCase):
    def setUp(self):
        self.pcr = PropfindCachingResource(FilePath('/root'),
                                                timeFunc=lambda:0)

    def test_tokenPathForURI(self):
        paths = [
            ('/principals/__uids__/557C330A-06E2-403B-BC24-CE3A253CDB5B/',
             '/root/principals/__uids__/557C330A-06E2-403B-BC24-CE3A253CDB5B'),
            ('/calendars/users/dreid/', '/root/calendars/users/dreid'),
            ('/calendars/users/dreid/calendar', '/root/calendars/users/dreid')]

        for inPath, outPath in paths:
            self.assertEquals(self.pcr._tokenPathForURI(inPath).path, outPath)


    def test_observerForURI(self):
        self.pcr.observerFactory = StubCacheChangeObserver

        paths = [
            ('/principals/__uids__/557C330A-06E2-403B-BC24-CE3A253CDB5B/',
             '/root/principals/__uids__/557C330A-06E2-403B-BC24-CE3A253CDB5B'),
            ('/calendars/users/dreid/', '/root/calendars/users/dreid'),
            ('/calendars/users/dreid/calendar', '/root/calendars/users/dreid')]

        for inPath, outPath in paths:
            self.assertEquals(self.pcr._observerForURI(inPath).fp.path,
                              outPath)


    def test_cacheResponseTaggedRequestTrue(self):
        response = object()
        request = StubRequest('GET', '/root/calendars/users/dreid', 'dreid')

        request.cacheRequest = True

        r = self.pcr._cacheResponse(request, response)
        self.assertEquals(r, response)

        self.assertEquals(self.pcr._responses,
                          {('GET',
                            '/root/calendars/users/dreid',
                            'dreid'): (0, response)})


    def test_cacheResponseTaggedRequestFalse(self):
        response = object()
        request = StubRequest('GET', '/root/calendars/users/dreid', 'dreid')

        r = self.pcr._cacheResponse(request, response)
        self.assertEquals(r, response)

        self.assertEquals(self.pcr._responses, {})


    def test_cacheResposneUntaggedRequest(self):
        response = object()
        request = StubRequest('GET', '/root/calendars/users/dreid', 'dreid')
        del request.cacheRequest

        r = self.pcr._cacheResponse(request, response)
        self.assertEquals(r, response)

        self.assertEquals(self.pcr._responses, {})
