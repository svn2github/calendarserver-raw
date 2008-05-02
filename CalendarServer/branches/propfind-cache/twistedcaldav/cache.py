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

    def __init__(self, docroot):
        self._docroot = docroot
        self._responses = {}


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
        key = (request.method, request.uri, request.authnUser)
        if key not in self._responses:
            return None

        principalToken, uriToken, cacheTime, response = self._responses[key]

        if self._tokenForURI(request.authnUser) != principalToken:
            return None

        elif self._tokenForURI(request.uri) != uriToken:
            return None

        elif self._time() >= cacheTime + self.CACHE_TIMEOUT:
            return None

        return response


    def cacheResponseForRequest(self, request, response):
        """
        Cache the given C{response} for the given C{request}.

        @param request: An L{IRequest} provider that will be keyed to the
            given C{response}.

        @param response: An L{IResponse} provider that will be returned on
            subsequent checks for the given L{IRequest}
        """
        key = (request.method, request.uri, request.authnUser)
        self._responses[key] = (self._tokenForURI(request.authnUser),
                                self._tokenForURI(request.uri),
                                self._time(), response)
