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
WebDAV REPORT method
"""

__all__ = ["http_REPORT"]

import string

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, StatusResponse
from twisted.web2.dav import davxml
from twisted.web2.dav.element.parser import lookupElement
from twisted.web2.dav.util import davXMLFromStream

from twext.log import Logger
from twext.web2.dav.davxml import ErrorResponse

from twistedcaldav import caldavxml

log = Logger()

@inlineCallbacks
def http_REPORT(self, request):
    """
    Respond to a REPORT request. (RFC 3253, section 3.6)
    """
    if not self.fp.exists():
        log.err("File not found: %s" % (self.fp.path,))
        raise HTTPError(responsecode.NOT_FOUND)

    #
    # Read request body
    #
    try:
        doc = (yield davXMLFromStream(request.stream))
    except ValueError, e:
        log.err("Error while handling REPORT body: %s" % (e,))
        raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, str(e)))

    if doc is None:
        raise HTTPError(StatusResponse(
            responsecode.BAD_REQUEST,
            "REPORT request body may not be empty"
        ))

    #
    # Parse request
    #
    namespace = doc.root_element.namespace
    name = doc.root_element.name

    if namespace:
        if namespace == davxml.dav_namespace:
            request.submethod = "DAV:" + name
        elif namespace == caldavxml.caldav_namespace:
            request.submethod = "CalDAV:" + name
        else:
            request.submethod = "{%s}%s" % (namespace, name)
    else:
        request.submethod = name

    def to_method(namespace, name):
        if namespace:
            s = "_".join((namespace, name))
        else:
            s = name

        ok = string.ascii_letters + string.digits + "_"
        out = []
        for c in s:
            if c in ok:
                out.append(c)
            else:
                out.append("_")
        return "report_" + "".join(out)

    method_name = to_method(namespace, name)

    try:
        method = getattr(self, method_name)
        
        # Also double-check via supported-reports property
        reports = self.supportedReports()
        test = lookupElement((namespace, name))
        if not test:
            raise AttributeError()
        test = davxml.Report(test())
        if test not in reports:
            raise AttributeError()
    except AttributeError:
        #
        # Requested report is not supported.
        #
        log.err("Unsupported REPORT {%s}%s for resource %s (no method %s)"
                % (namespace, name, self, method_name))

        raise HTTPError(ErrorResponse(
            responsecode.FORBIDDEN,
            davxml.SupportedReport()
        ))

    #
    # Check authentication and access controls
    #
    privileges = (davxml.Read(),)
    if method_name == "report_urn_ietf_params_xml_ns_caldav_free_busy_query":
        privileges = (caldavxml.ReadFreeBusy(),)
    yield self.authorize(request, privileges)

    result = (yield method(request, doc.root_element))
    returnValue(result)
