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
from twistedcaldav.cache import ResponseCache

from twistedcaldav.test.util import InMemoryPropertyStore


def _newCacheTokens(prefix):
    def _(self):
        called = getattr(self, '_%scalled' % (prefix,), 0)

        token = '%sToken%d' % (prefix, called)
        setattr(self, '_%scalled' % (prefix,), called + 1)
        return token

    return _



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


    def test_cacheTokenPropertyIsProvisioned(self):
        self.ccn.changed()
        token = self.props._properties[CacheTokensProperty.qname()
                                        ].children[0].data
        self.assertEquals(tokens, 'token0')


    def test_changedChangesToken(self):
        self.ccn.propertiesChanged()
        self.ccn.propertiesChanged()
        tokens = self.props._properties[CacheTokensProperty.qname()
                                        ].children[0].data
        self.assertEquals(token, 'token1')



class ResponseCacheTests(TestCase):
    def setUp(self):
        self.tokens = {
                '/calendars/users/cdaboo/': 'uriToken0',
                '/principals/users/cdaboo/': 'principalToken0',
                '/principals/users/dreid/': 'principalTokenX'}

        self.rc = ResponseCache(None)
        self.rc._tokenForURI = self.tokens.get

        self.rc._time = (lambda:0)

        self.expected_response = object()

        self.rc._responses[(
                'PROPFIND',
                '/calendars/users/cdaboo/',
                '/principals/users/cdaboo/')] = (
            'principalToken0', 'uriToken0', 0, self.expected_response)


    def test_getResponseForRequestNotInCache(self):
        response = self.rc.getResponseForRequest(StubRequest(
                'PROPFIND',
                '/calendars/users/dreid/',
                '/principals/users/dreid/'))

        self.assertEquals(response, None)


    def test_getResponseForRequestInCache(self):
        response = self.rc.getResponseForRequest(StubRequest(
                'PROPFIND',
                '/calendars/users/cdaboo/',
                '/principals/users/cdaboo/'))

        self.assertEquals(self.expected_response, response)


    def test_getResponseForRequestPrincipalTokenChanged(self):
        self.tokens['/principals/users/cdaboo/'] = 'principalToken1'

        response = self.rc.getResponseForRequest(StubRequest(
                'PROPFIND',
                '/calendars/users/cdaboo/',
                '/principals/users/cdaboo/'))

        self.assertEquals(response, None)


    def test_getResponseForRequestUriTokenChanged(self):
        self.tokens['/calendars/users/cdaboo/'] = 'uriToken1'

        response = self.rc.getResponseForRequest(StubRequest(
                'PROPFIND',
                '/calendars/users/cdaboo/',
                '/principals/users/cdaboo/'))

        self.assertEquals(response, None)


    def test_getResponseForRequestCacheTimeoutLapsed(self):
        self.rc._time = (lambda: 50000)

        response = self.rc.getResponseForRequest(StubRequest(
                'PROPFIND',
                '/calendars/users/cdaboo/',
                '/principals/users/cdaboo/'))

        self.assertEquals(response, None)


    def test_cacheResponseForRequest(self):
        expected_response = object()
        self.rc.cacheResponseForRequest(StubRequest('PROPFIND',
                                                    '/principals/users/dreid/',
                                                    '/principals/users/dreid/'),
                                        expected_response)

        response = self.rc.getResponseForRequest(StubRequest(
                'PROPFIND',
                '/principals/users/dreid/',
                '/principals/users/dreid/'))

        self.assertEquals(response, expected_response)


    def test__tokenForURI(self):
        docroot = FilePath(self.mktemp())
        principal = docroot.child('principals').child('users').child('wsanchez')

        expected_token = "wsanchezToken0"

        props = InMemoryPropertyStore()
        props._properties[CacheTokensProperty.qname()
                          ] = CacheTokensProperty.fromString(expected_token)

        stores = {principal.path: props}

        rc = ResponseCache(docroot)

        rc.propertyStoreFactory = (lambda rsrc: stores[rsrc.fp.path])

        token = rc._tokenForURI('/principals/users/wsanchez')
        self.assertEquals(token, expected_token)
