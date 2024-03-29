##
# Copyright (c) 2009-2010 Apple Inc. All rights reserved.
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

from twext.web2.dav import davxml
from twext.web2.dav.noneprops import NonePropertyStore
from twisted.internet.defer import succeed
from twistedcaldav.directory.util import NotFilePath
from twistedcaldav.extensions import DAVFile
from twistedcaldav.resource import CalDAVResource
from twistedcaldav.static import CalDAVFile

"""
Implements a simple non-file resource.
"""

__all__ = [
    "SimpleResource",
    "SimpleCalDAVResource",
]

class SimpleResource (
    CalDAVResource,
    DAVFile,
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
        DAVFile.__init__(self, NotFilePath(isfile=not isdir,isdir=isdir), principalCollections=principalCollections)
        self.defaultACL = defaultACL

    def locateChild(self, req, segments):
        child = self.getChild(segments[0])
        if child is not None:
            return (child, segments[1:])
        return (None, ())

    def getChild(self, name):
        if name == "":
            return self
        else:
            return self.putChildren.get(name, None)

    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties

    def etag(self):
        return None

    def accessControlList(self, request, inheritance=True, expanding=False, inherited_aces=None):
        return succeed(self.defaultACL)

class SimpleCalDAVResource (
    CalDAVFile,
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
        DAVFile.__init__(self, NotFilePath(isfile=not isdir,isdir=isdir), principalCollections=principalCollections)
        self.defaultACL = defaultACL

    def locateChild(self, req, segments):
        child = self.getChild(segments[0])
        if child is not None:
            return (child, segments[1:])
        return (None, ())

    def getChild(self, name):
        if name == "":
            return self
        else:
            return self.putChildren.get(name, None)

    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties

    def etag(self):
        return None

    def accessControlList(self, request, inheritance=True, expanding=False, inherited_aces=None):
        return succeed(self.defaultACL)
