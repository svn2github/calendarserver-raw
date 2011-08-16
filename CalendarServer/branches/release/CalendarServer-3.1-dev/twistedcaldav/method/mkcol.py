# -*- test-case-name: twistedcaldav.test.test_DAV.MKCOL -*-
##
# Copyright (c) 2005-2010 Apple Inc. All rights reserved.
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
Extended MKCOL method.
"""

__all__ = ["http_MKCOL"]

from twisted.python.failure import Failure
from twisted.internet.defer import inlineCallbacks, returnValue

from twext.python.log import Logger
from twext.web2 import responsecode
from twext.web2.dav import davxml
from twext.web2.http import Response
from twext.web2.dav.http import ErrorResponse, PropertyStatusResponseQueue
from twext.web2.dav.util import davXMLFromStream
from twext.web2.dav.util import parentForURL
from twext.web2.http import HTTPError
from twext.web2.http import StatusResponse

from twistedcaldav import caldavxml, carddavxml, mkcolxml
from twistedcaldav.config import config
from twistedcaldav.resource import isAddressBookCollectionResource,\
    CalDAVResource
from twistedcaldav.resource import isPseudoCalendarCollectionResource

log = Logger()


@inlineCallbacks
def http_MKCOL(self, request):

    #
    # Check authentication and access controls
    #
    parent = (yield request.locateResource(parentForURL(request.uri)))

    yield parent.authorize(request, (davxml.Bind(),))

    if self.exists():
        log.err("Attempt to create collection where resource exists: %s"
                % (self,))
        raise HTTPError(ErrorResponse(
            responsecode.FORBIDDEN,
            (davxml.dav_namespace, "resource-must-be-null"),
            "Resource already exists",
        ))

    if not parent.isCollection():
        log.err("Attempt to create collection with non-collection parent: %s"
                % (self,))
        raise HTTPError(ErrorResponse(
            responsecode.CONFLICT,
            (davxml.dav_namespace, "collection-location-ok"),
            "Cannot create calendar inside another calendar",
        ))

    #
    # Don't allow DAV collections in a calendar or address book collection
    #

    if config.EnableCalDAV:
        parent = (yield self._checkParents(request, isPseudoCalendarCollectionResource))
    
        if parent is not None:
            raise HTTPError(StatusResponse(
                responsecode.FORBIDDEN,
                "Cannot create collection within calendar collection %s" % (parent,)
            ))

    if config.EnableCardDAV:
        parent = (yield self._checkParents(request, isAddressBookCollectionResource))
    
        if parent is not None:
            raise HTTPError(StatusResponse(
                responsecode.FORBIDDEN,
                "Cannot create collection within address book collection %s" % (parent,)
            ))

    #
    # Read request body
    #
    try:
        doc = (yield davXMLFromStream(request.stream))
    except ValueError, e:
        log.err("Error while handling MKCOL: %s" % (e,))
        # TODO: twext.web2.dav 'MKCOL' tests demand this particular response
        # code, but should we really be looking at the XML content or the
        # content-type header?  It seems to me like this ought to be considered
        # a BAD_REQUEST if it claims to be XML but isn't, but an
        # UNSUPPORTED_MEDIA_TYPE if it claims to be something else. -glyph
        raise HTTPError(
            StatusResponse(responsecode.UNSUPPORTED_MEDIA_TYPE, str(e))
        )

    if doc is not None:

        # Parse response body
        mkcol = doc.root_element
        if not isinstance(mkcol, mkcolxml.MakeCollection):
            error = ("Non-%s element in MKCOL request body: %s"
                     % (mkcolxml.MakeCollection.name, mkcol))
            log.err(error)
            raise HTTPError(StatusResponse(responsecode.UNSUPPORTED_MEDIA_TYPE, error))

        errors = PropertyStatusResponseQueue("PROPPATCH", request.uri, responsecode.NO_CONTENT)
        got_an_error = False
    
        if mkcol.children:
            # mkcol -> set -> prop -> property*
            properties = mkcol.children[0].children[0].children

            # First determine the resource type
            rtype = None
            for property in properties:
                if isinstance(property, davxml.ResourceType):
                    if rtype:
                        error = "Multiple {DAV:}resourcetype properties in MKCOL request body: %s" % (mkcol,)
                        log.err(error)
                        raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, error))
                    else:
                        if property.childrenOfType(davxml.Collection):
                            if property.childrenOfType(caldavxml.Calendar):
                                rtype = "calendar"
                            elif property.childrenOfType(carddavxml.AddressBook):
                                rtype = "addressbook"
            if not rtype:
                error = "No {DAV:}resourcetype property in MKCOL request body: %s" % (mkcol,)
                log.err(error)
                raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, error))
            elif rtype not in ("calendar", "addressbook"):
                error = "{DAV:}resourcetype property in MKCOL request body not supported: %s" % (mkcol,)
                log.err(error)
                raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, error))
                
            # Make sure feature is enabled
            if (rtype == "calendar" and not config.EnableCalDAV or
                rtype == "addressbook" and not config.EnableCardDAV):
                error = "{DAV:}resourcetype property in MKCOL request body not supported: %s" % (mkcol,)
                log.err(error)
                raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, error))
            
            # Now create the special collection
            if rtype == "calendar":
                yield self.createCalendar(request)
            elif rtype == "addressbook":
                yield self.createAddressBook(request)

            # Now handle other properties
            for property in mkcol.children[0].children[0].children:
                try:
                    if rtype == "calendar" and property.qname() == (caldavxml.caldav_namespace, "supported-calendar-component-set"):
                        self.writeDeadProperty(property)
                    else:
                        yield self.writeProperty(property, request)
                except HTTPError:
                    errors.add(Failure(), property)
                    got_an_error = True
                else:
                    errors.add(responsecode.OK, property)
    
        if got_an_error:
            # Clean up
            self.transactionError()
            errors.error()
            raise HTTPError(Response(
                    code=responsecode.BAD_REQUEST,
                    stream=mkcolxml.MakeCollectionResponse(errors.response()).toxml()
            ))

        yield returnValue(responsecode.CREATED)
    
    else:
        # No request body so it is a standard MKCOL
        result = yield super(CalDAVResource, self).http_MKCOL(request)
        returnValue(result)

