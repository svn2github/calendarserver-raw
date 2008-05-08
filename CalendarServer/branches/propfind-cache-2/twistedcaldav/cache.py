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

from zope.interface import implements

from twisted.python.filepath import FilePath
from twisted.python import log

from twisted.web2.iweb import IResource
from twisted.web2.dav import davxml
from twisted.web2.dav.util import allDataFromStream
from twisted.web2.http import HTTPError, Response
from twisted.web2.stream import MemoryStream

from twisted.web2.dav.xattrprops import xattrPropertyStore

class CacheTokensProperty(davxml.WebDAVTextElement):
    namespace = davxml.twisted_private_namespace
    name = "cacheTokens"



class CacheChangeNotifier(object):
    def __init__(self, propertyStore):
        self._propertyStore = propertyStore
        self._token = None


    def _newCacheToken(self):
        return uuid.uuid4()


    def changed(self):
        property = CacheTokensProperty.fromString(self._newCacheToken())
        self._propertyStore.set(property)




class ResponseCache(object):
    """
    An object that caches responses to given requests.

    @ivar CACHE_TIMEOUT: The number of seconds that a cache entry is valid,
        (default 3600 seconds or 1 hour).

    @ivar _docroot: An L{FilePath} that points to the document root.
    @ivar _responses: A C{dict} with (request-method, request-uri,
         principal-uri) keys and (principal-token, uri-token, cache-time,
         response) values.
    """

    CACHE_TIMEOUT = 60*60 #1hr
    propertyStoreFactory = xattrPropertyStore

    def __init__(self, docroot, cacheTimeout=None):
        self._docroot = docroot
        self._responses = {}
        if cacheTimeout:
            self.CACHE_TIMEOUT = cacheTimeout


    def _tokenForURI(self, uri):
        """
        Get a property store for the given C{uri}.

        @param uri: The URI we'd like the token for.
        @return: A C{str} representing the token for the URI.
        """

        class __FauxStaticResource(object):
            def __init__(self, fp):
                self.fp = fp


        fp = self._docroot
        for childPath in uri.split('/')[:4]:
            fp = fp.child(childPath)

        props = self.propertyStoreFactory(__FauxStaticResource(fp))

        try:
            tokenElement = props.get(CacheTokensProperty.qname())
            return tokenElement.children[0].data

        except HTTPError, err:
            pass


    def _principalURI(self, principal):
        return str(principal.children[0])


    def _time(self):
        """
        Return the current time in seconds since the epoch
        """
        return time.time()


    def getResponseForRequest(self, request):
        """
        Retrieve a cached response to the given C{request} otherwise return
        C{None}

        @param request: An L{IRequest} provider that will be used to locate
            a cached L{IResponse}.

        @return: An L{IResponse} or C{None} if the response has not been cached.
        """
        def _returnRequest(requestBody):

            if requestBody is not None:
                request.stream = MemoryStream(requestBody)
                request.stream.doStartReading = None

            principalURI = self._principalURI(request.authnUser)

            key = (request.method,
                   request.uri,
                   principalURI,
                   request.headers.getHeader('depth'),
                   hash(requestBody))

#             print "Looking in cache for:"
#             import pprint; pprint.pprint(key)

            request.cacheKey = key

            if key not in self._responses:
#                 pprint.pprint(self._responses.keys())
                return None

            principalToken, uriToken, cacheTime, response = self._responses[key]

            if self._tokenForURI(principalURI) != principalToken:
                return None

            elif self._tokenForURI(request.uri) != uriToken:
                return None

            elif self._time() >= cacheTime + self.CACHE_TIMEOUT:
                return None

            response[1].removeHeader('date')

            return Response(response[0],
                            headers=response[1],
                            stream=MemoryStream(response[2]))

        d = allDataFromStream(request.stream)
        d.addCallback(_returnRequest)
        return d


    def cacheResponseForRequest(self, request, response):
        """
        Cache the given C{response} for the given C{request}.

        @param request: An L{IRequest} provider that will be keyed to the
            given C{response}.

        @param response: An L{IResponse} provider that will be returned on
            subsequent checks for the given L{IRequest}

        @return: A deferred that fires when the response has been added
            to the cache.
        """
        def _getRequestBody(responseBody):
            d1 = allDataFromStream(request.stream)
            d1.addCallback(lambda requestBody: (requestBody, responseBody))
            return d1

        def _cacheResponse((requestBody, responseBody)):
            if requestBody is not None:
                request.stream = MemoryStream(requestBody)
                request.stream.doStartReading = None

            principalURI = self._principalURI(request.authnUser)

            if hasattr(request, 'cacheKey'):
                key = request.cacheKey
            else:
                key = (request.method,
                       request.uri,
                       principalURI,
                       request.headers.getHeader('depth'),
                       hash(requestBody))

            self._responses[key] = (self._tokenForURI(principalURI),
                                    self._tokenForURI(request.uri),
                                    self._time(), (response.code,
                                                   response.headers,
                                                   responseBody))

#             print self._responses.keys()

            response.stream = MemoryStream(responseBody)
            return response


        d = allDataFromStream(response.stream)
        d.addCallback(_getRequestBody)
        d.addCallback(_cacheResponse)
        return d



class _CachedResponseResource(object):
    implements(IResource)

    def __init__(self, response):
        self._response = response

    def renderHTTP(self, request):
        return self._response

    def locateChild(self, request, segments):
        return self, []


class PropfindCacheMixin(object):
    def http_PROPFIND(self, request):
        def _cacheResponse(responseCache, response):
            return responseCache.cacheResponseForRequest(request, response)

        def _getResponseCache(response):
            print "Caching response: %r, %r" % (request.stream, response)
            d1 = request.locateResource("/")
            d1.addCallback(lambda resource: resource.responseCache)
            d1.addCallback(_cacheResponse, response)
            return d1

        d = super(PropfindCacheMixin, self).http_PROPFIND(request)
        d.addCallback(_getResponseCache)
        return d
