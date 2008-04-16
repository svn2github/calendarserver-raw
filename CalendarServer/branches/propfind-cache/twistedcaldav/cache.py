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


from twisted.web2.dav import davxml
from twisted.web2.http import HTTPError


class CacheTokensProperty(davxml.WebDAVTextElement):
    namespace = davxml.twisted_private_namespace
    name = "cacheTokens"


class CacheChangeNotifier(object):
    def __init__(self, propertyStore):
        self._propertyStore = propertyStore
        self._propToken = None
        self._dataToken = None

    def _newDataToken(self):
        return uuid.uuid4()

    _newPropertyToken = _newDataToken

    def _loadTokens(self):
        try:
            tokens = self._propertyStore.get(CacheTokensProperty.qname())
            self._propToken, self._dataToken = (
                tokens.children[0].data.split(':'))

        except HTTPError, e:
            pass

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
