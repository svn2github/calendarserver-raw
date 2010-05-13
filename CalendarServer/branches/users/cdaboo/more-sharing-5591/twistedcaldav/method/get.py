##
# Copyright (c) 2005-2008 Apple Inc. All rights reserved.
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
CalDAV GET method.
"""

__all__ = ["http_GET"]

from twisted.internet.defer import inlineCallbacks, returnValue
from twext.web2.dav import davxml
from twext.web2.dav.util import parentForURL
from twext.web2.http import HTTPError
from twext.web2.http import Response
from twext.web2.http_headers import MimeType
from twext.web2.stream import MemoryStream

from twistedcaldav.caldavxml import ScheduleTag
from twistedcaldav.customxml import TwistedCalendarAccessProperty
from twistedcaldav.datafilters.privateevents import PrivateEventFilter
from twistedcaldav.resource import isPseudoCalendarCollectionResource

@inlineCallbacks
def http_GET(self, request):

    # Look for calendar access restriction on existing resource.
    if self.exists():
        parentURL = parentForURL(request.uri)
        parent = (yield request.locateResource(parentURL))
        if isPseudoCalendarCollectionResource(parent):
    
            # Check authorization first
            yield self.authorize(request, (davxml.Read(),))

            caldata = (yield self.iCalendarForUser(request))

            try:
                access = self.readDeadProperty(TwistedCalendarAccessProperty)
            except HTTPError:
                access = None
                
            if access:
        
                # Non DAV:owner's have limited access to the data
                isowner = (yield self.isOwner(request, adminprincipals=True, readprincipals=True))
                
                # Now "filter" the resource calendar data
                caldata = PrivateEventFilter(access, isowner).filter(caldata)
    
            response = Response()
            response.stream = MemoryStream(str(caldata))
            response.headers.setHeader("content-type", MimeType.fromString("text/calendar; charset=utf-8"))
    
            # Add Schedule-Tag header if property is present
            if self.hasDeadProperty(ScheduleTag):
                scheduletag = self.readDeadProperty(ScheduleTag)
                if scheduletag:
                    response.headers.setHeader("Schedule-Tag", str(scheduletag))
        
            returnValue(response)

    # Do normal GET behavior
    response = (yield super(CalDAVFile, self).http_GET(request))
    returnValue(response)
