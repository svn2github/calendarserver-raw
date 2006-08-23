##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
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
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##

"""
CalDAV DELETE method.
"""

__version__ = "0.0"

__all__ = ["http_DELETE"]

from twisted.internet.defer import maybeDeferred
from twisted.web2 import responsecode
from twisted.web2.iweb import IResponse

from twistedcaldav.resource import isPseudoCalendarCollectionResource

def http_DELETE(self, request):
    # Override base DELETE request handling to ensure that the calendar
    # index file has the entry for the deleted calendar component removed.

    # Do inherited default behaviour
    d = maybeDeferred(super(CalDAVFile, self).http_DELETE, request)
    
    def deleteFromIndex(response):
        response = IResponse(response)

        if response.code == responsecode.NO_CONTENT:
            # Remove index entry if we are a child of a calendar collection
            parent = self.locateParent(request, request.uri)
            if isPseudoCalendarCollectionResource(parent):
                index = parent.index()
                index.deleteResource(self.fp.basename())

        return response
        
    return d.addCallback(deleteFromIndex)
