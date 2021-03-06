##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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
Extensions to twisted.web2.dav.davxml
"""

__all__ = [
    "sname2qname",
    "qname2sname",
    "ErrorResponse",
]

from twisted.web2.http import Response
from twisted.web2.dav.http import ErrorResponse as SuperErrorResponse
from twisted.web2.dav.davxml import twisted_dav_namespace, WebDAVTextElement
from twisted.web2.dav.davxml import WebDAVUnknownElement, Error
from twisted.web2.http_headers import MimeType


def sname2qname(sname):
    """
    Convert an sname into a qname.

    That is, parse a property name string (eg: C{"{DAV:}displayname"})
    into a tuple (eg: C{("DAV:", "displayname")}).

    @raise ValueError is input is not valid. Note, however, that this
    function does not attempt to fully validate C{sname}.
    """
    def raiseIf(condition):
        if condition:
            raise ValueError("Invalid sname: %s" % (sname,))

    raiseIf(not sname.startswith("{"))

    try:
        i = sname.index("}")
    except ValueError:
        raiseIf(True)

    namespace = sname[1:i]
    name = sname [i+1:]

    raiseIf("{" in namespace or not name)

    return namespace, name

def qname2sname(qname):
    """
    Convert a qname into an sname.
    """
    try:
        return "{%s}%s" % qname
    except TypeError:
        raise ValueError("Invalid qname: %r" % (qname,))





class ErrorDescription(WebDAVTextElement):
    """
    The human-readable description of a failed precondition
    """
    namespace = twisted_dav_namespace
    name = "error-description"
    protected = True


class ErrorResponse(SuperErrorResponse):
    """
    A L{Response} object which contains a status code and a L{davxml.Error}
    element.
    Renders itself as a DAV:error XML document.
    """
    error = None

    def __init__(self, code, error, description=None):
        """
        @param code: a response code.
        @param error: an L{davxml.WebDAVElement} identifying the error, or a
            tuple C{(namespace, name)} with which to create an empty element
            denoting the error.  (The latter is useful in the case of
            preconditions ans postconditions, not all of which have defined
            XML element classes.)
        @param description: an optional string that, if present, will get
            wrapped in a (twisted_dav_namespace, error-description) element.
        """
        if type(error) is tuple:
            xml_namespace, xml_name = error
            error = WebDAVUnknownElement()
            error.namespace = xml_namespace
            error.name = xml_name

        if description:
            output = Error(error, ErrorDescription(description)).toxml()
        else:
            output = Error(error).toxml()

        Response.__init__(self, code=code, stream=output)

        self.headers.setHeader("content-type", MimeType("text", "xml"))

        self.error = error

    def __repr__(self):
        return "<%s %s %s>" % (self.__class__.__name__, self.code, self.error.sname())

