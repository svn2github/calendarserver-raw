##
# Copyright (c) 2008 Apple Inc. All rights reserved.
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
Admin page resource.
"""

__all__ = [
    "AdminServiceResource",
]

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.web2.dav import davxml
from twisted.web2.http import Response
from twisted.web2.http_headers import MimeType

from twistedcaldav.config import config
from twistedcaldav.directory.directory import DirectoryService
from twistedcaldav.resource import CalDAVResource

class AdminServiceResource (CalDAVResource):
    """
    Timezone Service resource.

    Extends L{DAVResource} to provide timezone service functionality.
    """

    def __init__(self, parent):
        """
        @param parent: the parent resource of this one.
        """
        assert parent is not None

        CalDAVResource.__init__(self, principalCollections=parent.principalCollections())

        self.parent = parent
        self.cache = {}

    def defaultAccessControlList(self):
        return config.AdminOnlyResourceACL

    def resourceType(self):
        return davxml.ResourceType.admin

    def isCollection(self):
        return False

    def isCalendarCollection(self):
        return False

    def isPseudoCalendarCollection(self):
        return False

    def render(self, request):
        output = """<html>
<head>
<title>Admin Resource</title>
</head>
<body>
<h1>Calendar Server Administration Interface.</h1>
<table>

<tr>
<td><b>Accounts</b></td>
<td>%(accounts)s</td>
</tr>

<tr>
<td><b>Calendars</b></td>
<td>%(calendars)s</td>
</tr>

<tr>
<td><b>Groups</b></td>
<td>%(groups)s</td>
</tr>

<tr>
<td><b>Locations</b></td>
<td>%(locations)s</td>
</tr>

<tr>
<td><b>Resources</b></td>
<td>%(resources)s</td>
</tr>

<tr>
<td><b>Events</b></td>
<td>%(events)s</td>
</tr>

<tr>
<td><b>Todos</b></td>
<td>%(todos)s</td>
</tr>

</table>
</body
</html>""" % self.stats

        response = Response(200, {}, output)
        response.headers.setHeader("content-type", MimeType("text", "html"))
        return response

    @inlineCallbacks
    def http_GET(self, request):
        """
        The admin service GET method.
        """
        
        # Check authentication and access controls
        yield self.authorize(request, (davxml.Read(),))
        
        # Collect the stats
        self.stats = {}
        
        # Accounts - number of individuals principals
        self.stats["accounts"] = 0

        # Calendars - number of calendars of principals
        self.stats["calendars"] = 0

        # Groups - number of group principals
        self.stats["groups"] = 0

        # Resources - number of resource principals
        self.stats["resources"] = 0

        # Locations - number of location principals
        self.stats["locations"] = 0

        # Events - number of events
        self.stats["events"] = 0

        # Todos - number of todos principals
        self.stats["todos"] = 0

        # Collect principal collection details
        for collection in self.principalCollections():
            for child in collection.listChildren():
                child = collection.getChild(child)
                if child.recordType == DirectoryService.recordType_users:
                    self.stats["accounts"] += len(tuple(child.directory.listRecords(child.recordType)))
                elif child.recordType == DirectoryService.recordType_groups:
                    self.stats["groups"] += len(tuple(child.directory.listRecords(child.recordType)))
                elif child.recordType == DirectoryService.recordType_resources:
                    self.stats["resources"] += len(tuple(child.directory.listRecords(child.recordType)))
                elif child.recordType == DirectoryService.recordType_locations:
                    self.stats["locations"] += len(tuple(child.directory.listRecords(child.recordType)))

        # Do normal GET behavior
        response = yield self.render(request)
        returnValue(response)
