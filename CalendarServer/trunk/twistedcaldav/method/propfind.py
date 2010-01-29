# -*- test-case-name: twisted.web2.dav.test.test_prop.PROP.test_PROPFIND -*-
##
# Copyright (c) 2005-2008 Apple Computer, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
##

"""
WebDAV PROPFIND method
"""

__all__ = ["http_PROPFIND"]

from twisted.python.failure import Failure
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.web2.http import HTTPError
from twisted.web2 import responsecode
from twisted.web2.http import StatusResponse
from twisted.web2.dav import davxml
from twisted.web2.dav.http import MultiStatusResponse, statusForFailure
from twisted.web2.dav.util import normalizeURL, davXMLFromStream

from twext.log import Logger

log = Logger()

"""
This is a direct copy of the twisted implementation of PROPFIND except that it uses the
findChildrenFaster method to optimize child privilege checking.
"""

@inlineCallbacks
def http_PROPFIND(self, request):
    """
    Respond to a PROPFIND request. (RFC 2518, section 8.1)
    """
    if not self.exists():
        log.err("File not found: %s" % (self.fp.path,))
        raise HTTPError(responsecode.NOT_FOUND)

    #
    # Check authentication and access controls
    #
    yield self.authorize(request, (davxml.Read(),))

    #
    # Read request body
    #
    try:
        doc = (yield davXMLFromStream(request.stream))
    except ValueError, e:
        log.err("Error while handling PROPFIND body: %s" % (e,))
        raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, str(e)))

    if doc is None:
        # No request body means get all properties.
        search_properties = "all"
    else:
        #
        # Parse request
        #
        find = doc.root_element
        if not isinstance(find, davxml.PropertyFind):
            error = ("Non-%s element in PROPFIND request body: %s"
                     % (davxml.PropertyFind.sname(), find))
            log.err(error)
            raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, error))

        container = find.children[0]

        if isinstance(container, davxml.AllProperties):
            # Get all properties
            search_properties = "all"
        elif isinstance(container, davxml.PropertyName):
            # Get names only
            search_properties = "names"
        elif isinstance(container, davxml.PropertyContainer):
            properties = container.children
            search_properties = [(p.namespace, p.name) for p in properties]
        else:
            raise AssertionError("Unexpected element type in %s: %s"
                                 % (davxml.PropertyFind.sname(), container))

    #
    # Generate XML output stream
    #
    request_uri = request.uri
    depth = request.headers.getHeader("depth", "infinity")

    xml_responses = []

    # FIXME: take advantage of the new generative properties of findChildren

    my_url = normalizeURL(request_uri)
    if self.isCollection() and not my_url.endswith("/"):
        my_url += "/"

    # Do some optimization of access control calculation by determining any inherited ACLs outside of
    # the child resource loop and supply those to the checkPrivileges on each child.
    filtered_aces = (yield self.inheritedACEsforChildren(request))

    resources = [(True, self, my_url)]

    yield self.findChildrenFaster(
        depth,
        request,
        lambda x, y: resources.append((True, x, y)),
        lambda x, y: resources.append((False, x, y)),
        None,
        (davxml.Read(),),
        inherited_aces=filtered_aces,
    )

    for readable, resource, uri in resources:
        if readable:
            if search_properties is "names":
                try:
                    resource_properties = (yield resource.listProperties(request))
                except:
                    log.err("Unable to get properties for resource %r" % (resource,))
                    raise
    
                properties_by_status = {
                    responsecode.OK: [propertyName(p) for p in resource_properties]
                }
            else:
                properties_by_status = {
                    responsecode.OK        : [],
                    responsecode.NOT_FOUND : [],
                }
    
                if search_properties is "all":
                    properties_to_enumerate = (yield resource.listAllprop(request))
                else:
                    properties_to_enumerate = search_properties
    
                for property in properties_to_enumerate:
                    has = (yield resource.hasProperty(property, request))
                    if has:
                        try:
                            resource_property = (yield resource.readProperty(property, request))
                        except:
                            f = Failure()
    
                            status = statusForFailure(f, "getting property: %s" % (property,))
                            if status not in properties_by_status:
                                properties_by_status[status] = []
                            properties_by_status[status].append(propertyName(property))
                        else:
                            properties_by_status[responsecode.OK].append(resource_property)
                    else:
                        properties_by_status[responsecode.NOT_FOUND].append(propertyName(property))

            propstats = []
    
            for status in properties_by_status:
                properties = properties_by_status[status]
                if not properties: continue
    
                xml_status    = davxml.Status.fromResponseCode(status)
                xml_container = davxml.PropertyContainer(*properties)
                xml_propstat  = davxml.PropertyStatus(xml_container, xml_status)
    
                propstats.append(xml_propstat)
    
            xml_response = davxml.PropertyStatusResponse(davxml.HRef(uri), *propstats)
        else:
            xml_response = davxml.StatusResponse(davxml.HRef(uri), davxml.Status.fromResponseCode(responsecode.FORBIDDEN))
    
        xml_responses.append(xml_response)
            

    if not hasattr(request, "extendedLogItems"):
        request.extendedLogItems = {}
    request.extendedLogItems["responses"] = len(xml_responses)

    #
    # Return response
    #
    returnValue(MultiStatusResponse(xml_responses))


##
# Utilities
##

def propertyName(name):
    property_namespace, property_name = name
    pname = davxml.WebDAVUnknownElement()
    pname.namespace = property_namespace
    pname.name = property_name
    return pname
