##
# Copyright (c) 2006-2010 Apple Inc. All rights reserved.
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
CalDAV DELETE behaviors.
"""

__all__ = ["DeleteResource"]

from twisted.internet.defer import inlineCallbacks, returnValue
from twext.web2 import responsecode
from twext.web2.dav.fileop import delete
from twext.web2.dav.http import ResponseQueue, MultiStatusResponse
from twext.web2.dav.util import joinURL
from twext.web2.http import HTTPError, StatusResponse

from twext.python.log import Logger

from twistedcaldav.memcachelock import MemcacheLockTimeoutError
from twistedcaldav.method.report_common import applyToAddressBookCollections
from twistedcaldav.resource import isAddressBookCollectionResource

log = Logger()

class DeleteResource(object):

    def __init__(self, request, resource, resource_uri, parent, depth,
        internal_request=False, allowImplicitSchedule=True):

        self.request = request
        self.resource = resource
        self.resource_uri = resource_uri
        self.parent = parent
        self.depth = depth
        self.internal_request = internal_request
        self.allowImplicitSchedule = allowImplicitSchedule


    @inlineCallbacks
    def deleteAddressBookResource(self, delresource, deluri, parent):
        """
        Delete a single addressbook resource and do implicit scheduling actions if required.

        @param delresource:
        @type delresource:
        @param deluri:
        @type deluri:
        @param parent:
        @type parent:
        """

        # TODO: need to use transaction based delete on live scheduling object resources
        # as the iTIP operation may fail and may need to prevent the delete from happening.

        # Do quota checks before we start deleting things
        myquota = (yield delresource.quota(self.request))
        if myquota is not None:
            old_size = (yield delresource.quotaSize(self.request))
        else:
            old_size = 0

        try:

            # Do delete
            response = (yield delete(deluri, delresource.fp, self.depth))

            # Adjust quota
            if myquota is not None:
                yield delresource.quotaSizeAdjust(self.request, -old_size)

            if response == responsecode.NO_CONTENT:
                newrevision = (yield parent.bumpSyncToken())
                index = parent.index()
                index.deleteResource(delresource.fp.basename(), newrevision)

        except MemcacheLockTimeoutError:
            raise HTTPError(StatusResponse(responsecode.CONFLICT, "Resource: %s currently in use on the server." % (deluri,)))


        returnValue(response)

    @inlineCallbacks
    def deleteAddressBook(self, delresource, deluri, parent):
        """
        Delete an entire addressbook collection by deleting each child resource in turn to
        ensure that proper implicit scheduling actions occur.
        
        This has to emulate the behavior in fileop.delete in that any errors need to be
        reported back in a multistatus response.
        """


        if self.depth != "infinity":
            msg = "Client sent illegal depth header value for DELETE: %s" % (self.depth,)
            log.err(msg)
            raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, msg))

        # Check virtual share first
        isVirtual = yield delresource.isVirtualShare(self.request)
        if isVirtual:
            log.debug("Removing shared address book %s" % (delresource,))
            yield delresource.removeVirtualShare(self.request)
            returnValue(responsecode.NO_CONTENT)

        log.debug("Deleting addressbook %s" % (delresource.fp.path,))

        errors = ResponseQueue(deluri, "DELETE", responsecode.NO_CONTENT)

        for childname in delresource.listChildren():

            childurl = joinURL(deluri, childname)
            child = (yield self.request.locateChildResource(delresource, childname))

            try:
                yield self.deleteAddressBookResource(child, childurl, delresource)
            except:
                errors.add(childurl, responsecode.BAD_REQUEST)

        # Now do normal delete

        # Handle sharing
        wasShared = (yield delresource.isShared(self.request))
        if wasShared:
            yield delresource.downgradeFromShare(self.request)

        yield delresource.bumpSyncToken()
        more_responses = (yield self.deleteResource(delresource, deluri, parent))

        if isinstance(more_responses, MultiStatusResponse):
            # Merge errors
            errors.responses.update(more_responses.children)

        response = errors.response()

        returnValue(response)

    @inlineCallbacks
    def deleteCollectionAB(self):
        # XXX CSCS-MERGE this needs to be merged into deleteCollection
        """
        Delete a regular collection with special processing for any addressbook collections
        contained within it.
        """
        if self.depth != "infinity":
            msg = "Client sent illegal depth header value for DELETE: %s" % (self.depth,)
            log.err(msg)
            raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, msg))

        log.debug("Deleting collection %s" % (self.resource.fp.path,))

        errors = ResponseQueue(self.resource_uri, "DELETE", responsecode.NO_CONTENT)

        @inlineCallbacks
        def doDeleteAddressBook(delresource, deluri):

            delparent = (yield delresource.locateParent(self.request, deluri))

            response = (yield self.deleteAddressBook(delresource, deluri, delparent))

            if isinstance(response, MultiStatusResponse):
                # Merge errors
                errors.responses.update(response.children)

            returnValue(True)

        yield applyToAddressBookCollections(self.resource, self.request, self.resource_uri, self.depth, doDeleteAddressBook, None)

        # Now do normal delete
        more_responses = (yield self.deleteResource(self.resource, self.resource_uri, self.parent))

        if isinstance(more_responses, MultiStatusResponse):
            # Merge errors
            errors.responses.update(more_responses.children)

        response = errors.response()

        returnValue(response)

    @inlineCallbacks
    def run(self):
        if isAddressBookCollectionResource(self.parent):
            response = (yield self.deleteAddressBookResource(self.resource, self.resource_uri, self.parent))
        elif isAddressBookCollectionResource(self.resource):
            response = (yield self.deleteAddressBook(self.resource, self.resource_uri, self.parent))
        else:
            # FIXME: this code-path shouldn't actually be used, as the things
            # with storeRemove on them also have their own http_DELETEs.
            response = (
                yield self.resource.storeRemove(
                    self.request, 
                    not self.internal_request and self.allowImplicitSchedule,
                    self.resource_uri
                )
            )

        returnValue(response)
