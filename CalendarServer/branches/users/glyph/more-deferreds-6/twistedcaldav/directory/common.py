# -*- test-case-name: twistedcaldav.test.test_wrapping,twistedcaldav.directory.test.test_calendar -*-
##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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

from twisted.internet.defer import inlineCallbacks, returnValue
from twext.web2.http import HTTPError
from twext.web2 import responsecode
from twext.web2.dav.util import joinURL
from twistedcaldav.directory.util import transactionFromRequest
from twistedcaldav.directory.resource import DirectoryReverseProxyResource

__all__ = [
    'uidsResourceName',
    'CommonUIDProvisioningResource'
]

# Use __underbars__ convention to avoid conflicts with directory resource
# types.

uidsResourceName = "__uids__"

class CommonUIDProvisioningResource(object):
    """
    Common ancestor for addressbook/calendar UID provisioning resources.

    Must be mixed in to the hierarchy I{before} the appropriate resource type.
    
    @ivar homeResourceTypeName: The name of the home resource type ('calendars'
        or 'addressbooks').

    @ivar enabledAttribute: The name of the attribute of the directory record
        which determines whether this should be enabled or not.
    """

    def __init__(self, parent):
        """
        @param parent: the parent of this resource
        """

        super(CommonUIDProvisioningResource, self).__init__()

        self.directory = parent.directory
        self.parent = parent


    @inlineCallbacks
    def homeResourceForRecord(self, record, request):

        transaction = transactionFromRequest(request, self.parent._newStore)
        name = record.uid

        if record is None:
            self.log_msg("No directory record with GUID %r" % (name,))
            returnValue(None)

        if not getattr(record, self.enabledAttribute):
            self.log_msg("Directory record %r is not enabled for %s" % (
                record, self.homeResourceTypeName))
            returnValue(None)

        assert len(name) > 4, "Directory record has an invalid GUID: %r" % (
            name,)
        
        if record.locallyHosted():
            child = yield self.homeResourceCreator(record, transaction)
        else:
            child = DirectoryReverseProxyResource(self, record)

        returnValue(child)


    @inlineCallbacks
    def locateChild(self, request, segments):

        name = segments[0]
        if name == "":
            returnValue((self, ()))

        record = self.directory.recordWithUID(name)
        if record:
            child = yield self.homeResourceForRecord(record, request)
            returnValue((child, segments[1:]))
        else:
            returnValue((None, ()))


    def listChildren(self):
        # Not a listable collection
        raise HTTPError(responsecode.FORBIDDEN)

    ##
    # ACL
    ##

    def principalCollections(self):
        return self.parent.principalCollections()

    def principalForRecord(self, record):
        return self.parent.principalForRecord(record)
    ##
    # DAV
    ##
    
    def isCollection(self):
        return True


    def getChild(self, name, record=None):
        raise NotImplementedError(self.__class__.__name__ +
                                  ".getChild no longer exists.")

    def displayName(self):
        return uidsResourceName

    def url(self):
        return joinURL(self.parent.url(), uidsResourceName)



class CommonHomeTypeProvisioningResource(object):

    @inlineCallbacks
    def locateChild(self, request, segments):
        name = segments[0]
        if name == "":
            returnValue((self, segments[1:]))

        record = self.directory.recordWithShortName(self.recordType, name)
        if record is None:
            returnValue((None, []))

        child = yield self._parent.homeForDirectoryRecord(record, request)
        returnValue((child, segments[1:]))

