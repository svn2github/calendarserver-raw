##
# Copyright (c) 2005-2006 Apple Computer, Inc. All rights reserved.
#
# This file contains Original Code and/or Modifications of Original Code
# as defined in and that are subject to the Apple Public Source License
# Version 2.0 (the 'License'). You may not use this file except in
# compliance with the License. Please obtain a copy of the License at
# http://www.opensource.apple.com/apsl/ and read it before using this
# file.
# 
# The Original Code and all software distributed under the License are
# distributed on an 'AS IS' basis, WITHOUT WARRANTY OF ANY KIND, EITHER
# EXPRESS OR IMPLIED, AND APPLE HEREBY DISCLAIMS ALL SUCH WARRANTIES,
# INCLUDING WITHOUT LIMITATION, ANY WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE, QUIET ENJOYMENT OR NON-INFRINGEMENT.
# Please see the License for the specific language governing rights and
# limitations under the License.
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

"""
WebDAV support for Twisted Web2.

See draft spec: http://ietf.webdav.org/caldav/draft-dusseault-caldav.txt
"""

from twisted.web2.static import File, loadMimeTypes

__all__ = [
    "caldavxml",
    "customxml",
    "dateops",
    "db",
    "directory",
    "ical",
    "index",
    "instance",
    "principalindex",
    "resource",
    "static",
]

try:
    from twistedcaldav.version import version as __version__
except ImportError:
    __version__ = None

# Load in suitable file extension/content-type map from OS X
File.contentTypes = loadMimeTypes(["/etc/httpd/mime.types"])

import twisted.web2.dav.davxml
import twistedcaldav.caldavxml

twisted.web2.dav.davxml.registerElements(twistedcaldav.caldavxml)
