##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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
CalDAV PUT method.
"""

__all__ = ["http_PUT"]

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.web2 import responsecode
from twext.web2.dav.davxml import ErrorResponse
from twisted.web2.dav.util import allDataFromStream, parentForURL
from twisted.web2.http import HTTPError, StatusResponse

from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.method.put_common import StoreCalendarObjectResource
from twistedcaldav.resource import isPseudoCalendarCollectionResource
from twistedcaldav.log import Logger

log = Logger()

@inlineCallbacks
def http_PUT(self, request):

    parentURL = parentForURL(request.uri)
    parent = (yield request.locateResource(parentURL))

    if isPseudoCalendarCollectionResource(parent):
        self.fp.restat(False)

        # Content-type check
        content_type = request.headers.getHeader("content-type")
        if content_type is not None and (content_type.mediaType, content_type.mediaSubtype) != ("text", "calendar"):
            log.err("MIME type %s not allowed in calendar collection" % (content_type,))
            raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "supported-calendar-data")))
            
        # Read the calendar component from the stream
        try:
            calendardata = (yield allDataFromStream(request.stream))

            # We must have some data at this point
            if calendardata is None:
                # Use correct DAV:error response
                raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-calendar-data"), description="No calendar data"))

            storer = StoreCalendarObjectResource(
                request = request,
                destination = self,
                destination_uri = request.uri,
                destinationcal = True,
                destinationparent = parent,
                calendar = calendardata,
            )
            result = (yield storer.run())
            returnValue(result)

        except ValueError, e:
            log.err("Error while handling (calendar) PUT: %s" % (e,))
            raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, str(e)))

    else:
        result = (yield super(CalDAVFile, self).http_PUT(request))
        returnValue(result)
