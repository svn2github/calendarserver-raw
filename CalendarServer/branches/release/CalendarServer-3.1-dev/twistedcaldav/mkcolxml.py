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
CalDAV XML Support.

This module provides XML utilities for use with CalDAV.

This API is considered private to static.py and is therefore subject to
change.

See draft spec: http://ietf.webdav.org/caldav/draft-dusseault-caldav.txt
"""

from twext.web2.dav import davxml

##
# Extended MKCOL objects
##

mkcol_compliance = (
    "extended-mkcol",
)

class MakeCollection (davxml.WebDAVElement):
    """
    Top-level element for request body in MKCOL.
    (Extended-MKCOL, section 5.1)
    """
    name = "mkcol"

    allowed_children = { (davxml.dav_namespace, "set"): (0, 1) }

    child_types = { "WebDAVUnknownElement": (0, None) }

class MakeCollectionResponse (davxml.WebDAVElement):
    """
    Top-level element for response body in MKCOL.
    (Extended-MKCOL, section 5.2)
    """
    name = "mkcol-response"

    allowed_children = { davxml.WebDAVElement: (0, None) }
