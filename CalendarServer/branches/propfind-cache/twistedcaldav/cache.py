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

import uuid
import time
import os

from twisted.python.filepath import FilePath

from twisted.web2.dav import davxml
from twisted.web2.http import HTTPError
from twisted.web2.dav.xattrprops import xattrPropertyStore

class CacheTokensProperty(davxml.WebDAVTextElement):
    namespace = davxml.twisted_private_namespace
    name = "cacheTokens"


class CacheChangeLoaderMixin(object):
    _propToken = None
    _dataToken = None

    def _loadTokens(self):
        try:
            tokens = self._propertyStore.get(CacheTokensProperty.qname())
            self._propToken, self._dataToken = (
                tokens.children[0].data.split(':'))

        except HTTPError, e:
            pass



class CacheChangeNotifier(CacheChangeLoaderMixin):
    def __init__(self, propertyStore):
        self._propertyStore = propertyStore


    def _newDataToken(self):
        return uuid.uuid4()

    _newPropertyToken = _newDataToken


    def _writeTokens(self):
        if self._propToken is None:
            self._propToken = self._newPropertyToken()

        if self._dataToken is None:
            self._dataToken = self._newDataToken()

        property = CacheTokensProperty.fromString('%s:%s' % (self._propToken,
                                                             self._dataToken))
        self._propertyStore.set(property)


    def propertiesChanged(self):
        self._loadTokens()
        self._propToken = self._newPropertyToken()
        self._writeTokens()


    def dataChanged(self):
        self._loadTokens()
        self._dataToken = self._newDataToken()
        self._writeTokens()



class CacheChangeObserver(CacheChangeLoaderMixin):
    def __init__(self, propertyStore):
        self._propertyStore = propertyStore
        self._oldPropToken = None
        self._oldDataToken = None


    def propertiesHaveChanged(self):
        self._loadTokens()

        if self._propToken != self._oldPropToken:
            self._oldPropToken = self._propToken
            return True

        return False


    def dataHasChanged(self):
        self._loadTokens()

        if self._dataToken != self._oldDataToken:
            self._oldDataToken = self._dataToken
            return True

        return False



class PropfindCachingResource(object):
    CACHE_TIMEOUT = 60*60 # 1 hour

    propertyStoreFactory = xattrPropertyStore
    observerFactory = CacheChangeObserver

    def __init__(self, docroot, timeFunc=time.time):
        self._docroot = docroot
        self._responses = {}
        self._observers = {}
        self._timeFunc = timeFunc


    def _tokenPathForURI(self, uri):
        tokenPath = self._docroot

        for childName in uri.split('/')[:4]:
            tokenPath = tokenPath.child(childName)

        return tokenPath


    def _observerForURI(self, uri):
        class FauxStaticResource(object):
            def __init__(self, fp):
                self.fp = fp

        propertyStore = self.propertyStoreFactory(
                FauxStaticResource(self._tokenPathForURI(uri)))

        return self.observerFactory(propertyStore)


    def _cacheResponse(self, response, request):
        if getattr(request, 'cacheRequest', False):
            if request.uri not in self._observers:
                self._observers[request.uri] = self._observerForURI(request.uri)

            self._responses[(request.method,
                             request.uri,
                             request.authnUser)] = (self._timeFunc(),
                                                    response)

        return response


    def renderHTTP(self, request):
        key = (request.method, request.uri, request.authnUser)

        if key in self._responses:
            cacheTime, cachedResponse = self._responses[key]
            if cacheTime + CACHE_TIMEOUT <= self._timeFunc():
                if (request.uri in self._observers and
                    self._observers[request.uri]()):

                    return cachedResponse

        def _renderResource(resource, request):
            request.addResponseFilter(self._cacheResponse)
            return resource.renderHTTP(request)

        request.notInCache = True
        d = request.locateResource(request.uri)
        d.addCallback(_renderResource, request)
        return d


    def locateChild(self, request, segments):
        return self, []
