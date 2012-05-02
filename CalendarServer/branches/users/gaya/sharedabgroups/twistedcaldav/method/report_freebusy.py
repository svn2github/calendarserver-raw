##
# Copyright (c) 2006-2008 Apple Inc. All rights reserved.
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
CalDAV freebusy report
"""

__all__ = ["report_urn_ietf_params_xml_ns_caldav_free_busy_query"]

from twext.python.log import Logger
from twext.web2.dav.http import ErrorResponse

from twisted.internet.defer import inlineCallbacks, returnValue
from twext.web2 import responsecode
from txdav.xml import element as davxml
from twext.web2.dav.method.report import NumberOfMatchesWithinLimits
from twext.web2.http import HTTPError, Response, StatusResponse
from twext.web2.http_headers import MimeType
from twext.web2.stream import MemoryStream

from twistedcaldav import caldavxml
from twistedcaldav.method import report_common

log = Logger()

@inlineCallbacks
def report_urn_ietf_params_xml_ns_caldav_free_busy_query(self, request, freebusy): #@UnusedVariable
    """
    Generate a free-busy REPORT.
    (CalDAV-access-09, section 7.8)
    """
    if not self.isCollection():
        log.err("freebusy report is only allowed on collection resources %s" % (self,))
        raise HTTPError(StatusResponse(responsecode.FORBIDDEN, "Not a calendar collection"))

    if freebusy.qname() != (caldavxml.caldav_namespace, "free-busy-query"):
        raise ValueError("{CalDAV:}free-busy-query expected as root element, not %s." % (freebusy.sname(),))

    timerange = freebusy.timerange
    if not timerange.valid():
        raise HTTPError(StatusResponse(responsecode.BAD_REQUEST, "Invalid time-range specified"))

    # First list is BUSY, second BUSY-TENTATIVE, third BUSY-UNAVAILABLE
    fbinfo = ([], [], [])
    
    matchcount = [0]
    
    def generateFreeBusyInfo(calresource, uri): #@UnusedVariable
        """
        Run a free busy report on the specified calendar collection
        accumulating the free busy info for later processing.
        @param calresource: the L{CalDAVResource} for a calendar collection.
        @param uri: the uri for the calendar collecton resource.
        """
        
        def _gotResult(result):
            matchcount[0] = result
            return True

        d = report_common.generateFreeBusyInfo(request, calresource, fbinfo, timerange, matchcount[0])
        d.addCallback(_gotResult)
        return d

    # Run report taking depth into account
    try:
        depth = request.headers.getHeader("depth", "0")
        yield report_common.applyToCalendarCollections(self, request, request.uri, depth, generateFreeBusyInfo, (caldavxml.ReadFreeBusy(),))
    except NumberOfMatchesWithinLimits:
        log.err("Too many matching components in free-busy report")
        raise HTTPError(ErrorResponse(
            responsecode.FORBIDDEN,
            davxml.NumberOfMatchesWithinLimits(),
            "Too many components"
        ))
    
    # Now build a new calendar object with the free busy info we have
    fbcalendar = report_common.buildFreeBusyResult(fbinfo, timerange)
    
    response = Response()
    response.stream = MemoryStream(str(fbcalendar))
    response.headers.setHeader("content-type", MimeType.fromString("text/calendar; charset=utf-8"))

    returnValue(response)
