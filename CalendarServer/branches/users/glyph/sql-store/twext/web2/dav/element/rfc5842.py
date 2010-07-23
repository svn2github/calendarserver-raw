##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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
# DRI: Cyrus Daboo, cdaboo@apple.com
##

from twext.web2.dav.element.base import WebDAVTextElement, WebDAVElement,\
    dav_namespace

"""
RFC 5842 (Binding Extensions to WebDAV) XML Elements

This module provides XML element definitions for use with WebDAV.

See RFC 5842: http://www.ietf.org/rfc/rfc5842.txt
"""

class ResourceID (WebDAVTextElement):
    """
    Unique identifier for a resource
    """
    name = "resource-id"
    hidden = True
    protected = True

    allowed_children = { (dav_namespace, "href"): (0, 1) }

class ParentSet (WebDAVElement):
    """
    Identifies other bindings to a resource
    """
    name = "parent-set"
    hidden = True
    protected = True

    allowed_children = { (dav_namespace, "parent"): (0, 1) }

class Parent (WebDAVElement):

    name = "parent"

    allowed_children = {
        (dav_namespace, "href")    : (1, 1),
        (dav_namespace, "segment") : (1, 1),
    }

class Segment (WebDAVTextElement):

    name = "segment"

# Problem: DAV:bind is also defined in RFC3744 but with our XML element parsing/mapping behavior
# we are not allowed to have two class with the same qname(). So we are stuck.

#class BindResponse (WebDAVElement):
#    """
#    Response body for a BIND request
#    """
#
#    name = "bind-response"
#
#    allowed_children = {
#        # ANY
#    }
#
#class UnbindRequest (WebDAVElement):
#    """
#    Request body for a UNBIND request
#    """
#
#    name = "unbind"
#
#    allowed_children = {
#        (dav_namespace, "segment") : (1, 1),
#    }
#
#class Unbind (WebDAVElement):
#    """
#    Response body for a UNBIND request
#    """
#
#    name = "unbind-response"
#
#    allowed_children = {
#        # ANY
#    }
#
#class RebindRequest (WebDAVElement):
#    """
#    Request body for a REBIND request
#    """
#
#    name = "rebind"
#
#    allowed_children = {
#        (dav_namespace, "segment") : (1, 1),
#        (dav_namespace, "href")    : (1, 1),
#    }
#
#class Rebind (WebDAVElement):
#    """
#    Response body for a UNBIND request
#    """
#
#    name = "rebind-response"
#
#    allowed_children = {
#        # ANY
#    }
