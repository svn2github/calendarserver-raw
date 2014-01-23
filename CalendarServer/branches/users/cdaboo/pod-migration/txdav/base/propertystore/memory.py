# -*- test-case-name: txdav.base.propertystore.test.test_xattr -*-
##
# Copyright (c) 2010-2014 Apple Inc. All rights reserved.
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
from twisted.internet.defer import succeed

"""
Property store using a dict - read-only (except for initial insert). This is used
to mirror properties on a resource on another pod.
"""

from txdav.base.propertystore.base import AbstractPropertyStore, validKey, \
    PropertyName
from txdav.idav import PropertyChangeNotAllowedError
from txdav.xml.parser import WebDAVDocument


__all__ = [
    "PropertyStore",
]



class PropertyStore(AbstractPropertyStore):
    """
    Property store using a C{dict}. C{dict} keys are a C{tuple} of the property name and user
    id (both as C{str}. Property values are the C{str} representation of the XML value.
    """

    def __init__(self, defaultuser, properties):
        """
        Initialize a L{PropertyStore}.
        """
        super(PropertyStore, self).__init__(defaultuser)

        self.properties = properties


    def __str__(self):
        return "<%s>" % (self.__class__.__name__)


    #
    # Required implementations
    #

    def _getitem_uid(self, key, uid):
        validKey(key)
        try:
            value = self.properties[(key.toString(), uid)]
        except KeyError:
            raise KeyError(key)

        return WebDAVDocument.fromString(value).root_element


    def _setitem_uid(self, key, value, uid):
        validKey(key)
        raise PropertyChangeNotAllowedError("Property store is read-only.", (key,))


    def _delitem_uid(self, key, uid):
        validKey(key)
        raise PropertyChangeNotAllowedError("Property store is read-only.", (key,))


    def _keys_uid(self, uid):
        for cachedKey, cachedUID in self.properties.keys():
            if cachedUID == uid:
                yield PropertyName.fromString(cachedKey)


    def _removeResource(self):
        pass


    def flush(self):
        return None


    def abort(self):
        return None


    def serialize(self):
        """
        The dict used in this class is already the serialized format.
        """
        return succeed(self.properties)


    def deserialize(self, props):
        """
        The dict being passed in is already the format we need.
        """
        self.properties = props
        return succeed(None)
