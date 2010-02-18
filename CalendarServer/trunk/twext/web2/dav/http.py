##
# Copyright (c) 2005 Apple Computer, Inc. All rights reserved.
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
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

"""
HTTP Utilities
"""

__all__ = [
    "ErrorResponse",
    "NeedPrivilegesResponse",
    "MultiStatusResponse",
    "ResponseQueue",
    "PropertyStatusResponseQueue",
    "statusForFailure",
    "errorForFailure",
    "messageForFailure",
]

import errno

from twisted.python import log
from twisted.python.failure import Failure
from twisted.python.filepath import InsecurePath
from twext.web2 import responsecode
from twext.web2.iweb import IResponse
from twext.web2.http import Response, HTTPError, StatusResponse
from twext.web2.http_headers import MimeType
from twext.web2.dav import davxml
from twext.web2.dav.util import joinURL

from twext.web2.dav._errorbase import ErrorResponse

class NeedPrivilegesResponse (ErrorResponse):
    def __init__(self, base_uri, errors):
        """
        An error response which is due to unsufficient privileges, as
        determined by L{DAVResource.checkPrivileges}.
        @param base_uri: the base URI for the resources with errors (the URI of
            the resource on which C{checkPrivileges} was called).
        @param errors: a sequence of tuples, as returned by
            C{checkPrivileges}.
        """
        denials = []

        for subpath, privileges in errors:
            if subpath is None:
                uri = base_uri
            else:
                uri = joinURL(base_uri, subpath)

            for p in privileges:
                denials.append(davxml.Resource(davxml.HRef(uri), 
                                               davxml.Privilege(p)))

        super(NeedPrivilegesResponse, self).__init__(responsecode.FORBIDDEN, davxml.NeedPrivileges(*denials))

class MultiStatusResponse (Response):
    """
    Multi-status L{Response} object.
    Renders itself as a DAV:multi-status XML document.
    """
    def __init__(self, xml_responses):
        """
        @param xml_responses: an interable of davxml.Response objects.
        """
        Response.__init__(self, code=responsecode.MULTI_STATUS,
                          stream=davxml.MultiStatus(*xml_responses).toxml())

        self.headers.setHeader("content-type", MimeType("text", "xml"))

class ResponseQueue (object):
    """
    Stores a list of (typically error) responses for use in a
    L{MultiStatusResponse}.
    """
    def __init__(self, path_basename, method, success_response):
        """
        @param path_basename: the base path for all responses to be added to the 
            queue.
            All paths for responses added to the queue must start with
            C{path_basename}, which will be stripped from the beginning of each
            path to determine the response's URI.
        @param method: the name of the method generating the queue.
        @param success_response: the response to return in lieu of a
            L{MultiStatusResponse} if no responses are added to this queue.
        """
        self.responses         = []
        self.path_basename     = path_basename
        self.path_basename_len = len(path_basename)
        self.method            = method
        self.success_response  = success_response

    def add(self, path, what):
        """
        Add a response.
        @param path: a path, which must be a subpath of C{path_basename} as
            provided to L{__init__}.
        @param what: a status code or a L{Failure} for the given path.
        """
        assert path.startswith(self.path_basename), "%s does not start with %s" % (path, self.path_basename)

        if type(what) is int:
            code    = what
            error   = None
            message = responsecode.RESPONSES[code]
        elif isinstance(what, Failure):
            code    = statusForFailure(what)
            error   = errorForFailure(what)
            message = messageForFailure(what)
        else:
            raise AssertionError("Unknown data type: %r" % (what,))

        if code > 400: # Error codes only
            log.err("Error during %s for %s: %s" % (self.method, path, message))

        uri = path[self.path_basename_len:]

        children = []
        children.append(davxml.HRef(uri))
        children.append(davxml.Status.fromResponseCode(code))
        if error is not None:
            children.append(error)
        if message is not None:
            children.append(davxml.ResponseDescription(message))
        self.responses.append(davxml.StatusResponse(*children))

    def response(self):
        """
        Generate a L{MultiStatusResponse} with the responses contained in the
        queue or, if no such responses, return the C{success_response} provided
        to L{__init__}.
        @return: the response.
        """
        if self.responses:
            return MultiStatusResponse(self.responses)
        else:
            return self.success_response

class PropertyStatusResponseQueue (object):
    """
    Stores a list of propstat elements for use in a L{Response}
    in a L{MultiStatusResponse}.
    """
    def __init__(self, method, uri, success_response):
        """
        @param method: the name of the method generating the queue.
        @param uri: the URI for the response.
        @param success_response: the status to return if no
            L{PropertyStatus} are added to this queue.
        """
        self.method            = method
        self.uri               = uri
        self.propstats         = []
        self.success_response  = success_response

    def add(self, what, property):
        """
        Add a response.
        @param what: a status code or a L{Failure} for the given path.
        @param property: the property whose status is being reported.
        """
        if type(what) is int:
            code    = what
            error   = None
            message = responsecode.RESPONSES[code]
        elif isinstance(what, Failure):
            code    = statusForFailure(what)
            error   = errorForFailure(what)
            message = messageForFailure(what)
        else:
            raise AssertionError("Unknown data type: %r" % (what,))

        if len(property.children) > 0:
            # Re-instantiate as empty element.
            property = property.emptyCopy()

        if code > 400: # Error codes only
            log.err("Error during %s for %s: %s" % (self.method, property, message))

        children = []
        children.append(davxml.PropertyContainer(property))
        children.append(davxml.Status.fromResponseCode(code))
        if error is not None:
            children.append(error)
        if message is not None:
            children.append(davxml.ResponseDescription(message))
        self.propstats.append(davxml.PropertyStatus(*children))

    def error(self):
        """
        Convert any 2xx codes in the propstat responses to 424 Failed Dependency.
        """
        for index, propstat in enumerate(self.propstats):
            # Check the status
            changed_status = False
            newchildren = []
            for child in propstat.children:
                if isinstance(child, davxml.Status) and (child.code / 100 == 2):
                    # Change the code
                    newchildren.append(davxml.Status.fromResponseCode(responsecode.FAILED_DEPENDENCY))
                    changed_status = True
                elif changed_status and isinstance(child, davxml.ResponseDescription):
                    newchildren.append(davxml.ResponseDescription(responsecode.RESPONSES[responsecode.FAILED_DEPENDENCY]))
                else:
                    newchildren.append(child)
            self.propstats[index] = davxml.PropertyStatus(*newchildren)

    def response(self):
        """
        Generate a response from the responses contained in the queue or, if
        there are no such responses, return the C{success_response} provided to
        L{__init__}.
        @return: a L{davxml.PropertyStatusResponse}.
        """
        if self.propstats:
            return davxml.PropertyStatusResponse(
                davxml.HRef(self.uri),
                *self.propstats
            )
        else:
            return davxml.StatusResponse(
                davxml.HRef(self.uri),
                davxml.Status.fromResponseCode(self.success_response)
            )

##
# Exceptions and response codes
##

def statusForFailure(failure, what=None):
    """
    @param failure: a L{Failure}.
    @param what: a decription of what was going on when the failure occurred.
        If what is not C{None}, emit a cooresponding message via L{log.err}.
    @return: a response code cooresponding to the given C{failure}.
    """
    def msg(err):
        if what is not None:
            log.msg("%s while %s" % (err, what))

    if failure.check(IOError, OSError):
        e = failure.value[0]
        if e == errno.EACCES or e == errno.EPERM:
            msg("Permission denied")
            return responsecode.FORBIDDEN
        elif e == errno.ENOSPC:
            msg("Out of storage space")
            return responsecode.INSUFFICIENT_STORAGE_SPACE
        elif e == errno.ENOENT:
            msg("Not found")
            return responsecode.NOT_FOUND
        else:
            failure.raiseException()
    elif failure.check(NotImplementedError):
        msg("Unimplemented error")
        return responsecode.NOT_IMPLEMENTED
    elif failure.check(InsecurePath):
        msg("Insecure path")
        return responsecode.FORBIDDEN
    elif failure.check(HTTPError):
        code = IResponse(failure.value.response).code
        msg("%d response" % (code,))
        return code
    else:
        failure.raiseException()

def errorForFailure(failure):
    if failure.check(HTTPError) and isinstance(failure.value.response, ErrorResponse):
        return davxml.Error(failure.value.response.error)
    else:
        return None

def messageForFailure(failure):
    if failure.check(HTTPError):
        if isinstance(failure.value.response, ErrorResponse):
            return None
        if isinstance(failure.value.response, StatusResponse):
            return failure.value.response.description
    return str(failure)
