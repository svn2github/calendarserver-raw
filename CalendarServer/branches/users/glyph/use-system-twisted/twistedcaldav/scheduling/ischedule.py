##
# Copyright (c) 2005-2010 Apple Inc. All rights reserved.
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

from StringIO import StringIO

from twisted.internet.defer import inlineCallbacks, DeferredList, succeed
from twisted.internet.protocol import ClientCreator

from twisted.python.failure import Failure

from twext.web2 import responsecode
from twext.web2.client.http import ClientRequest
from twext.web2.client.http import HTTPClientProtocol
from twext.web2.dav.util import davXMLFromStream, joinURL, allDataFromStream
from twext.web2.http import HTTPError
from twext.web2.http_headers import Headers
from twext.web2.http_headers import MimeType
from twext.web2.stream import MemoryStream

from twext.log import Logger, logLevels
from twext.internet.ssl import ChainingOpenSSLContextFactory
from twext.web2.dav.davxml import ErrorResponse

from twistedcaldav import caldavxml
from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.config import config
from twistedcaldav.scheduling.delivery import DeliveryService
from twistedcaldav.scheduling.ischeduleservers import IScheduleServers
from twistedcaldav.scheduling.ischeduleservers import IScheduleServerRecord
from twistedcaldav.scheduling.itip import iTIPRequestStatus
from twistedcaldav.util import utf8String
from twistedcaldav.scheduling.cuaddress import RemoteCalendarUser
from twistedcaldav.scheduling.cuaddress import PartitionedCalendarUser

import OpenSSL

"""
Server to server utility functions and client requests.
"""

__all__ = [
    "ScheduleViaISchedule",
]

log = Logger()

class ScheduleViaISchedule(DeliveryService):
    
    @classmethod
    def serviceType(cls):
        return DeliveryService.serviceType_ischedule

    @classmethod
    def matchCalendarUserAddress(cls, cuaddr):

        # TODO: here is where we would attempt service discovery based on the cuaddr.
        
        # Do default match
        return super(ScheduleViaISchedule, cls).matchCalendarUserAddress(cuaddr)

    def generateSchedulingResponses(self, refreshOnly=False):
        """
        Generate scheduling responses for remote recipients.
        """
        
        # Group recipients by server so that we can do a single request with multiple recipients
        # to each different server.
        groups = {}
        servermgr = IScheduleServers()
        for recipient in self.recipients:
            if isinstance(recipient, RemoteCalendarUser):
                # Map the recipient's domain to a server
                server = servermgr.mapDomain(recipient.domain)
            elif isinstance(recipient, PartitionedCalendarUser):
                server = self._getServerForPartitionedUser(recipient)
            else:
                assert False, "Incorrect calendar user address class"
            if not server:
                # Cannot do server-to-server for this recipient.
                err = HTTPError(ErrorResponse(responsecode.NOT_FOUND, (caldav_namespace, "recipient-allowed")))
                self.responses.add(recipient.cuaddr, Failure(exc_value=err), reqstatus=iTIPRequestStatus.NO_USER_SUPPORT)
            
                # Process next recipient
                continue
            
            if not server.allow_to:
                # Cannot do server-to-server outgoing requests for this server.
                err = HTTPError(ErrorResponse(responsecode.NOT_FOUND, (caldav_namespace, "recipient-allowed")))
                self.responses.add(recipient.cuaddr, Failure(exc_value=err), reqstatus=iTIPRequestStatus.SERVICE_UNAVAILABLE)
            
                # Process next recipient
                continue
            
            groups.setdefault(server, []).append(recipient)

        if len(groups) == 0:
            return

        # Now we process each server: let's use a DeferredList to aggregate all the Deferred's
        # we will generate for each request. That way we can have parallel requests in progress
        # rather than serialize them.
        deferreds = []
        for server, recipients in groups.iteritems():
            requestor = IScheduleRequest(self.scheduler, server, recipients, self.responses, refreshOnly)
            deferreds.append(requestor.doRequest())

        return DeferredList(deferreds)

    def _getServerForPartitionedUser(self, recipient):
        
        if not hasattr(self, "partitionedServers"):
            self.partitionedServers = {}
            
        partition = recipient.principal.hostedURL()
        if partition not in self.partitionedServers:
            self.partitionedServers[partition] = IScheduleServerRecord(uri=joinURL(partition, "/ischedule"))
            self.partitionedServers[partition].unNormalizeAddresses = False
        
        return self.partitionedServers[partition]

class IScheduleRequest(object):
    
    def __init__(self, scheduler, server, recipients, responses, refreshOnly=False):

        self.scheduler = scheduler
        self.server = server
        self.recipients = recipients
        self.responses = responses
        self.refreshOnly = refreshOnly
        
        self._generateHeaders()
        self._prepareData()
        
    @inlineCallbacks
    def doRequest(self):
        
        # Generate an HTTP client request
        try:
            from twisted.internet import reactor
            if self.server.ssl:
                context = ChainingOpenSSLContextFactory(config.SSLPrivateKey, config.SSLCertificate, certificateChainFile=config.SSLAuthorityChain, sslmethod=getattr(OpenSSL.SSL, config.SSLMethod))
                proto = (yield ClientCreator(reactor, HTTPClientProtocol).connectSSL(self.server.host, self.server.port, context))
            else:
                proto = (yield ClientCreator(reactor, HTTPClientProtocol).connectTCP(self.server.host, self.server.port))
            
            request = ClientRequest("POST", self.server.path, self.headers, self.data)
            yield log.logRequest("debug", "Sending server-to-server POST request:", request)
            response = (yield proto.submitRequest(request))
    
            yield log.logResponse("debug", "Received server-to-server POST response:", response)
            xml = (yield davXMLFromStream(response.stream))
    
            self._parseResponse(xml)

        except Exception, e:
            # Generated failed responses for each recipient
            log.err("Could not do server-to-server request : %s %s" % (self, e))
            for recipient in self.recipients:
                err = HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "recipient-failed")))
                self.responses.add(recipient.cuaddr, Failure(exc_value=err), reqstatus=iTIPRequestStatus.SERVICE_UNAVAILABLE)

    def logRequest(self, level, message, request, **kwargs):
        """
        Log an HTTP request.
        """

        assert level in logLevels

        if self.willLogAtLevel(level):
            iostr = StringIO()
            iostr.write("%s\n" % (message,))
            if hasattr(request, "clientproto"):
                protocol = "HTTP/%d.%d" % (request.clientproto[0], request.clientproto[1],)
            else:
                protocol = "HTTP/1.1"
            iostr.write("%s %s %s\n" % (request.method, request.uri, protocol,))
            for name, valuelist in request.headers.getAllRawHeaders():
                for value in valuelist:
                    # Do not log authorization details
                    if name not in ("Authorization",):
                        iostr.write("%s: %s\n" % (name, value))
                    else:
                        iostr.write("%s: xxxxxxxxx\n" % (name,))
            iostr.write("\n")
            
            # We need to play a trick with the request stream as we can only read it once. So we
            # read it, store the value in a MemoryStream, and replace the request's stream with that,
            # so the data can be read again.
            def _gotData(data):
                iostr.write(data)
                
                request.stream = MemoryStream(data if data is not None else "")
                request.stream.doStartReading = None
            
                self.emit(level, iostr.getvalue(), **kwargs)

            d = allDataFromStream(request.stream)
            d.addCallback(_gotData)
            return d
        
        else:
            return succeed(None)
    
    def logResponse(self, level, message, response, **kwargs):
        """
        Log an HTTP request.
        """
        assert level in logLevels

        if self.willLogAtLevel(level):
            iostr = StringIO()
            iostr.write("%s\n" % (message,))
            code_message = responsecode.RESPONSES.get(response.code, "Unknown Status")
            iostr.write("HTTP/1.1 %s %s\n" % (response.code, code_message,))
            for name, valuelist in response.headers.getAllRawHeaders():
                for value in valuelist:
                    # Do not log authorization details
                    if name not in ("WWW-Authenticate",):
                        iostr.write("%s: %s\n" % (name, value))
                    else:
                        iostr.write("%s: xxxxxxxxx\n" % (name,))
            iostr.write("\n")
            
            # We need to play a trick with the response stream to ensure we don't mess it up. So we
            # read it, store the value in a MemoryStream, and replace the response's stream with that,
            # so the data can be read again.
            def _gotData(data):
                iostr.write(data)
                
                response.stream = MemoryStream(data if data is not None else "")
                response.stream.doStartReading = None
            
                self.emit(level, iostr.getvalue(), **kwargs)
                
            d = allDataFromStream(response.stream)
            d.addCallback(_gotData)
            return d

    def _generateHeaders(self):
        self.headers = Headers()
        self.headers.setHeader('Host', utf8String(self.server.host + ":%s" % (self.server.port,)))
        
        # The Originator must be the ORGANIZER (for a request) or ATTENDEE (for a reply)
        self.headers.addRawHeader('Originator', utf8String(self.scheduler.organizer.cuaddr if self.scheduler.isiTIPRequest else self.scheduler.attendee))
        self._doAuthentication()
        for recipient in self.recipients:
            self.headers.addRawHeader('Recipient', utf8String(recipient.cuaddr))
        self.headers.setHeader('Content-Type', MimeType("text", "calendar", params={"charset":"utf-8"}))

        if self.refreshOnly:
            self.headers.addRawHeader("X-CALENDARSERVER-ITIP-REFRESHONLY", "T")

    def _doAuthentication(self):
        if self.server.authentication and self.server.authentication[0] == "basic":
            self.headers.setHeader(
                'Authorization',
                ('Basic', ("%s:%s" % (self.server.authentication[1], self.server.authentication[2],)).encode('base64')[:-1])
            )

    def _prepareData(self):
        if self.server.unNormalizeAddresses and self.scheduler.method == "PUT": 
            def lookupFunction(cuaddr):
                principal = self.scheduler.resource.principalForCalendarUserAddress(cuaddr)
                if principal is None:
                    return (None, None, None)
                else:
                    return (principal.record.fullName.decode("utf-8"),
                        principal.record.guid,
                        principal.record.calendarUserAddresses)

            normalizedCalendar = self.scheduler.calendar.duplicate()
            normalizedCalendar.normalizeCalendarUserAddresses(lookupFunction, toUUID=False)
        else:
            normalizedCalendar = self.scheduler.calendar
        self.data = str(normalizedCalendar)

    def _parseResponse(self, xml):

        # Check for correct root element
        schedule_response = xml.root_element
        if not isinstance(schedule_response, caldavxml.ScheduleResponse) or not schedule_response.children:
            raise HTTPError(responsecode.BAD_REQUEST)
        
        # Parse each response - do this twice: once looking for errors that will
        # result in all recipients shown as failures; the second loop adds all the
        # valid responses to the actual result.
        for response in schedule_response.children:
            if not isinstance(response, caldavxml.Response) or not response.children:
                raise HTTPError(responsecode.BAD_REQUEST)
            recipient = response.childOfType(caldavxml.Recipient)
            request_status = response.childOfType(caldavxml.RequestStatus)
            if not recipient or not request_status:
                raise HTTPError(responsecode.BAD_REQUEST)
        for response in schedule_response.children:
            self.responses.clone(response)
