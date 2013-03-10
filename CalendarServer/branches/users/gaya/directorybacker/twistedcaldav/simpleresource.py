##
# Copyright (c) 2009-2013 Apple Inc. All rights reserved.
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
Implements a simple non-file resource.
"""

__all__ = [
    "SimpleResource",
    "SimpleCalDAVResource",
    "SimpleRedirectResource",
    "SimpleDataResource",
]

from twext.web2 import http
from twext.web2.dav.noneprops import NonePropertyStore
from twext.web2.http import Response

from twisted.internet.defer import succeed

from twistedcaldav.config import config
from twistedcaldav.resource import CalDAVResource

from txdav.xml import element as davxml

class SimpleResource (
    CalDAVResource,
):

    allReadACL = davxml.ACL(
        # Read access for all users.
        davxml.ACE(
            davxml.Principal(davxml.All()),
            davxml.Grant(davxml.Privilege(davxml.Read())),
            davxml.Protected(),
        ),
    )
    authReadACL = davxml.ACL(
        # Read access for authenticated users.
        davxml.ACE(
            davxml.Principal(davxml.Authenticated()),
            davxml.Grant(davxml.Privilege(davxml.Read())),
            davxml.Protected(),
        ),
    )

    def __init__(self, principalCollections, isdir=False, defaultACL=authReadACL):
        """
        Make sure it is a collection.
        """
        CalDAVResource.__init__(self, principalCollections=principalCollections)
        self._isDir = isdir
        self.defaultACL = defaultACL


    def isCollection(self):
        return self._isDir


    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties


    def etag(self):
        return succeed(None)


    def accessControlList(self, request, inheritance=True, expanding=False, inherited_aces=None):
        return succeed(self.defaultACL)

SimpleCalDAVResource = SimpleResource



class SimpleRedirectResource(SimpleResource):
    """
    A L{SimpleResource} which always performs a redirect.
    """

    def __init__(self, principalCollections, isdir=False, defaultACL=SimpleResource.authReadACL, **kwargs):
        """
        Parameters are URL components and are the same as those for
        L{urlparse.urlunparse}.  URL components which are not specified will
        default to the corresponding component of the URL of the request being
        redirected.
        """
        SimpleResource.__init__(self, principalCollections=principalCollections, isdir=isdir, defaultACL=defaultACL)
        self._kwargs = kwargs


    def renderHTTP(self, request):
        return http.RedirectResponse(request.unparseURL(host=config.ServerHostName, **self._kwargs))



class SimpleDataResource(SimpleResource):
    """
    A L{SimpleResource} which returns fixed content.
    """

    def __init__(self, principalCollections, content_type, data, defaultACL=SimpleResource.authReadACL):
        """
        @param content_type: the mime content-type of the data
        @type content_type: L{MimeType}
        @param data: the data
        @type data: C{str}
        """
        SimpleResource.__init__(self, principalCollections=principalCollections, isdir=False, defaultACL=defaultACL)
        self.content_type = content_type
        self.data = data


    def contentType(self):
        return self.content_type


    def render(self, request):
        response = Response(200, {}, self.data)
        response.headers.setHeader("content-type", self.content_type)
        return response
