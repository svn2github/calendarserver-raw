# -*- test-case-name: calendarserver.push.test.test_applepush -*-
##
# Copyright (c) 2011-2013 Apple Inc. All rights reserved.
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

from twext.internet.ssl import ChainingOpenSSLContextFactory
from twext.python.log import Logger, LoggingMixIn

from twext.web2 import responsecode
from txdav.xml import element as davxml
from twext.web2.dav.noneprops import NonePropertyStore
from twext.web2.http import Response
from twext.web2.http_headers import MimeType
from twext.web2.server import parsePOSTData
from twisted.application import service
from twisted.internet import protocol
from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twisted.internet.protocol import ClientFactory, ReconnectingClientFactory
from twisted.internet.task import LoopingCall
from twistedcaldav.extensions import DAVResource, DAVResourceWithoutChildrenMixin
from twistedcaldav.resource import ReadOnlyNoCopyResourceMixIn
import json
import OpenSSL
import struct
import time
from txdav.common.icommondatastore import InvalidSubscriptionValues

from calendarserver.push.util import validToken, TokenHistory, PushScheduler

from twext.internet.adaptendpoint import connect
from twext.internet.gaiendpoint import GAIEndpoint

log = Logger()



class ApplePushNotifierService(service.MultiService, LoggingMixIn):
    """
    ApplePushNotifierService is a MultiService responsible for
    setting up the APN provider and feedback connections.  Once
    connected, calling its enqueue( ) method sends notifications
    to any device token which is subscribed to the enqueued key.

    The Apple Push Notification protocol is described here:

    http://developer.apple.com/library/ios/#documentation/NetworkingInternet/Conceptual/RemoteNotificationsPG/CommunicatingWIthAPS/CommunicatingWIthAPS.html
    """

    @classmethod
    def makeService(cls, settings, store, serverHostName, testConnectorClass=None,
        reactor=None):
        """
        Creates the various "subservices" that work together to implement
        APN, including "provider" and "feedback" services for CalDAV and
        CardDAV.

        @param settings: The portion of the configuration specific to APN
        @type settings: C{dict}

        @param store: The db store for storing/retrieving subscriptions
        @type store: L{IDataStore}

        @param testConnectorClass: Used for unit testing; implements
            connect( ) and receiveData( )
        @type testConnectorClass: C{class}

        @param reactor: Used for unit testing; allows tests to advance the
            clock in order to test the feedback polling service.
        @type reactor: L{twisted.internet.task.Clock}

        @return: instance of L{ApplePushNotifierService}
        """

        service = cls()

        service.store = store
        service.providers = {}
        service.feedbacks = {}
        service.dataHost = settings["DataHost"]
        service.purgeCall = None
        service.purgeIntervalSeconds = settings["SubscriptionPurgeIntervalSeconds"]
        service.purgeSeconds = settings["SubscriptionPurgeSeconds"]

        for protocol in ("CalDAV", "CardDAV"):

            if settings[protocol]["CertificatePath"]:

                providerTestConnector = None
                feedbackTestConnector = None
                if testConnectorClass is not None:
                    providerTestConnector = testConnectorClass()
                    feedbackTestConnector = testConnectorClass()

                provider = APNProviderService(
                    service.store,
                    settings["ProviderHost"],
                    settings["ProviderPort"],
                    settings[protocol]["CertificatePath"],
                    settings[protocol]["PrivateKeyPath"],
                    chainPath=settings[protocol]["AuthorityChainPath"],
                    passphrase=settings[protocol]["Passphrase"],
                    staggerNotifications=settings["EnableStaggering"],
                    staggerSeconds=settings["StaggerSeconds"],
                    testConnector=providerTestConnector,
                    reactor=reactor,
                )
                provider.setServiceParent(service)
                service.providers[protocol] = provider
                service.log_info("APNS %s topic: %s" %
                    (protocol, settings[protocol]["Topic"]))

                feedback = APNFeedbackService(
                    service.store,
                    settings["FeedbackUpdateSeconds"],
                    settings["FeedbackHost"],
                    settings["FeedbackPort"],
                    settings[protocol]["CertificatePath"],
                    settings[protocol]["PrivateKeyPath"],
                    chainPath=settings[protocol]["AuthorityChainPath"],
                    passphrase=settings[protocol]["Passphrase"],
                    testConnector=feedbackTestConnector,
                    reactor=reactor,
                )
                feedback.setServiceParent(service)
                service.feedbacks[protocol] = feedback

        return service


    def startService(self):
        """
        In addition to starting the provider and feedback sub-services, start a
        LoopingCall whose job it is to purge old subscriptions
        """
        service.MultiService.startService(self)
        self.log_debug("ApplePushNotifierService startService")
        self.purgeCall = LoopingCall(self.purgeOldSubscriptions, self.purgeSeconds)
        self.purgeCall.start(self.purgeIntervalSeconds, now=False)


    def stopService(self):
        """
        In addition to stopping the provider and feedback sub-services, stop the
        LoopingCall
        """
        service.MultiService.stopService(self)
        self.log_debug("ApplePushNotifierService stopService")
        if self.purgeCall is not None:
            self.purgeCall.stop()
            self.purgeCall = None


    @inlineCallbacks
    def purgeOldSubscriptions(self, purgeSeconds):
        """
        Remove any subscriptions that registered more than purgeSeconds ago

        @param purgeSeconds: The cutoff given in seconds
        @type purgeSeconds: C{int}
        """
        self.log_debug("ApplePushNotifierService purgeOldSubscriptions")
        txn = self.store.newTransaction()
        yield txn.purgeOldAPNSubscriptions(int(time.time()) - purgeSeconds)
        yield txn.commit()


    @inlineCallbacks
    def enqueue(self, op, id, dataChangedTimestamp=None):
        """
        Sends an Apple Push Notification to any device token subscribed to
        this id.

        @param op: The operation that took place, either "create" or "update"
            (ignored in this implementation)
        @type op: C{str}
        @param id: The identifier of the resource that was updated, including
            a prefix indicating whether this is CalDAV or CardDAV related.
            The prefix is separated from the id with "|", e.g.:

            "CalDAV|abc/def"

            The id is an opaque token as far as this code is concerned, and
            is used in conjunction with the prefix and the server hostname
            to build the actual key value that devices subscribe to.
        @type id: C{str}
        @param dataChangedTimestamp: Timestamp (epoch seconds) for the data change
            which triggered this notification (Only used for unit tests)
        @type key: C{int}
        """

        try:
            protocol, id = id.split("|", 1)
        except ValueError:
            # id has no protocol, so we can't do anything with it
            self.log_error("Notification id '%s' is missing protocol" % (id,))
            return

        # Unit tests can pass this value in; otherwise it defaults to now
        if dataChangedTimestamp is None:
            dataChangedTimestamp = int(time.time())

        provider = self.providers.get(protocol, None)
        if provider is not None:
            key = "/%s/%s/%s/" % (protocol, self.dataHost, id)

            # Look up subscriptions for this key
            txn = self.store.newTransaction()
            subscriptions = (yield txn.apnSubscriptionsByKey(key))
            yield txn.commit()

            numSubscriptions = len(subscriptions)
            if numSubscriptions > 0:
                self.log_debug("Sending %d APNS notifications for %s" %
                    (numSubscriptions, key))
                tokens = []
                for token, uid in subscriptions:
                    if token and uid:
                        tokens.append(token)
                if tokens:
                    provider.scheduleNotifications(tokens, key, dataChangedTimestamp)



class APNProviderProtocol(protocol.Protocol, LoggingMixIn):
    """
    Implements the Provider portion of APNS
    """

    # Sent by provider
    COMMAND_SIMPLE   = 0
    COMMAND_ENHANCED = 1

    # Received by provider
    COMMAND_ERROR    = 8

    # Returned only for an error.  Successful notifications get no response.
    STATUS_CODES = {
        0   : "No errors encountered",
        1   : "Processing error",
        2   : "Missing device token",
        3   : "Missing topic",
        4   : "Missing payload",
        5   : "Invalid token size",
        6   : "Invalid topic size",
        7   : "Invalid payload size",
        8   : "Invalid token",
        255 : "None (unknown)",
    }

    # If error code comes back as one of these, remove the associated device
    # token
    TOKEN_REMOVAL_CODES = (5, 8)

    MESSAGE_LENGTH = 6

    def makeConnection(self, transport):
        self.history = TokenHistory()
        self.log_debug("ProviderProtocol makeConnection")
        protocol.Protocol.makeConnection(self, transport)

    def connectionMade(self):
        self.log_debug("ProviderProtocol connectionMade")
        self.buffer = ""
        # Store a reference to ourself on the factory so the service can
        # later call us
        self.factory.connection = self
        self.factory.clientConnectionMade()

    def connectionLost(self, reason=None):
        # self.log_debug("ProviderProtocol connectionLost: %s" % (reason,))
        # Clear the reference to us from the factory
        self.factory.connection = None

    @inlineCallbacks
    def dataReceived(self, data, fn=None):
        """
        Buffer and divide up received data into error messages which are
        always 6 bytes long
        """

        if fn is None:
            fn = self.processError

        self.log_debug("ProviderProtocol dataReceived %d bytes" % (len(data),))
        self.buffer += data

        while len(self.buffer) >= self.MESSAGE_LENGTH:
            message = self.buffer[:self.MESSAGE_LENGTH]
            self.buffer = self.buffer[self.MESSAGE_LENGTH:]

            try:
                command, status, identifier = struct.unpack("!BBI", message)
                if command == self.COMMAND_ERROR:
                    yield fn(status, identifier)
            except Exception, e:
                self.log_warn("ProviderProtocol could not process error: %s (%s)" %
                    (message.encode("hex"), e))


    @inlineCallbacks
    def processError(self, status, identifier):
        """
        Handles an error message we've received on the provider channel.
        If the error code is one that indicates a bad token, remove all
        subscriptions corresponding to that token.

        @param status: The status value returned from APN Feedback server
        @type status: C{int}

        @param identifier: The identifier of the outbound push notification
            message which had a problem.
        @type status: C{int}
        """
        msg = self.STATUS_CODES.get(status, "Unknown status code")
        self.log_info("Received APN error %d on identifier %d: %s" % (status, identifier, msg))
        if status in self.TOKEN_REMOVAL_CODES:
            token = self.history.extractIdentifier(identifier)
            if token is not None:
                self.log_debug("Removing subscriptions for bad token: %s" %
                    (token,))
                txn = self.factory.store.newTransaction()
                subscriptions = (yield txn.apnSubscriptionsByToken(token))
                for key, modified, uid in subscriptions:
                    self.log_debug("Removing subscription: %s %s" %
                        (token, key))
                    yield txn.removeAPNSubscription(token, key)
                yield txn.commit()


    def sendNotification(self, token, key, dataChangedTimestamp):
        """
        Sends a push notification message for the key to the device associated
        with the token.

        @param token: The device token subscribed to the key
        @type token: C{str}
        @param key: The key we're sending a notification about
        @type key: C{str}
        @param dataChangedTimestamp: Timestamp (epoch seconds) for the data change
            which triggered this notification
        @type key: C{int}
        """

        if not (token and key and dataChangedTimestamp):
            return

        try:
            binaryToken = token.replace(" ", "").decode("hex")
        except:
            self.log_error("Invalid APN token in database: %s" % (token,))
            return

        identifier = self.history.add(token)
        payload = json.dumps(
            {
                "key" : key,
                "dataChangedTimestamp" : dataChangedTimestamp,
                "pushRequestSubmittedTimestamp" : int(time.time()),
            }
        )
        payloadLength = len(payload)
        self.log_debug("Sending APNS notification to %s: id=%d payload=%s" %
            (token, identifier, payload))

        self.transport.write(
            struct.pack("!BIIH32sH%ds" % (payloadLength,),
                self.COMMAND_ENHANCED,  # Command
                identifier,             # Identifier
                0,                      # Expiry
                32,                     # Token Length
                binaryToken,            # Token
                payloadLength,          # Payload Length
                payload,                # Payload in JSON format
            )
        )


class APNProviderFactory(ReconnectingClientFactory, LoggingMixIn):

    protocol = APNProviderProtocol

    def __init__(self, service, store):
        self.service = service
        self.store = store
        self.noisy = True
        self.maxDelay = 30 # max seconds between connection attempts
        self.shuttingDown = False

    def clientConnectionMade(self):
        self.log_info("Connection to APN server made")
        self.service.clientConnectionMade()
        self.delay = 1.0

    def clientConnectionLost(self, connector, reason):
        if not self.shuttingDown:
            self.log_info("Connection to APN server lost: %s" % (reason,))
        ReconnectingClientFactory.clientConnectionLost(self, connector, reason)

    def clientConnectionFailed(self, connector, reason):
        self.log_error("Unable to connect to APN server: %s" % (reason,))
        self.connected = False
        ReconnectingClientFactory.clientConnectionFailed(self, connector,
            reason)

    def retry(self, connector=None):
        self.log_info("Reconnecting to APN server")
        ReconnectingClientFactory.retry(self, connector)

    def stopTrying(self):
        self.shuttingDown = True
        ReconnectingClientFactory.stopTrying(self)



class APNConnectionService(service.Service, LoggingMixIn):

    def __init__(self, host, port, certPath, keyPath, chainPath="",
        passphrase="", sslMethod="TLSv1_METHOD", testConnector=None,
        reactor=None):

        self.host = host
        self.port = port
        self.certPath = certPath
        self.keyPath = keyPath
        self.chainPath = chainPath
        self.passphrase = passphrase
        self.sslMethod = sslMethod
        self.testConnector = testConnector

        if reactor is None:
            from twisted.internet import reactor
        self.reactor = reactor

    def connect(self, factory):
        if self.testConnector is not None:
            # For testing purposes
            self.testConnector.connect(self, factory)
        else:
            if self.passphrase:
                passwdCallback = lambda *ignored : self.passphrase
            else:
                passwdCallback = None
            context = ChainingOpenSSLContextFactory(
                self.keyPath,
                self.certPath,
                certificateChainFile=self.chainPath,
                passwdCallback=passwdCallback,
                sslmethod=getattr(OpenSSL.SSL, self.sslMethod)
            )
            connect(GAIEndpoint(self.reactor, self.host, self.port, context),
                    factory)



class APNProviderService(APNConnectionService):

    def __init__(self, store, host, port, certPath, keyPath, chainPath="",
        passphrase="", sslMethod="TLSv1_METHOD",
        staggerNotifications=False, staggerSeconds=3,
        testConnector=None, reactor=None):

        APNConnectionService.__init__(self, host, port, certPath, keyPath,
            chainPath=chainPath, passphrase=passphrase, sslMethod=sslMethod,
            testConnector=testConnector, reactor=reactor)

        self.store = store
        self.factory = None
        self.queue = []
        if staggerNotifications:
            self.scheduler = PushScheduler(self.reactor, self.sendNotification,
                staggerSeconds=staggerSeconds)
        else:
            self.scheduler = None

    def startService(self):
        self.log_debug("APNProviderService startService")
        self.factory = APNProviderFactory(self, self.store)
        self.connect(self.factory)

    def stopService(self):
        self.log_debug("APNProviderService stopService")
        if self.factory is not None:
            self.factory.stopTrying()
        if self.scheduler is not None:
            self.scheduler.stop()

    def clientConnectionMade(self):
        # Service the queue
        if self.queue:
            # Copy and clear the queue.  Any notifications that don't get
            # sent will be put back into the queue.
            queued = list(self.queue)
            self.queue = []
            for (token, key), dataChangedTimestamp in queued:
                if token and key and dataChangedTimestamp:
                    self.sendNotification(token, key, dataChangedTimestamp)


    def scheduleNotifications(self, tokens, key, dataChangedTimestamp):
        """
        The starting point for getting notifications to the APNS server.  If there is
        a connection to the APNS server, these notifications are scheduled (or directly
        sent if there is no scheduler).  If there is no connection, the notifications
        are saved for later.

        @param tokens: The device tokens to schedule notifications for
        @type tokens: List of strings
        @param key: The key to use for this batch of notifications
        @type key: String
        @param dataChangedTimestamp: Timestamp (epoch seconds) for the data change
            which triggered this notification
        @type key: C{int}
        """
        # Service has reference to factory has reference to protocol instance
        connection = getattr(self.factory, "connection", None)
        if connection is not None:
            if self.scheduler is not None:
                self.scheduler.schedule(tokens, key, dataChangedTimestamp)
            else:
                for token in tokens:
                    self.sendNotification(token, key, dataChangedTimestamp)
        else:
            self._saveForWhenConnected(tokens, key, dataChangedTimestamp)


    def _saveForWhenConnected(self, tokens, key, dataChangedTimestamp):
        """
        Called in order to save notifications that can't be sent now because there
        is no connection to the APNS server.  (token, key) tuples are appended to
        the queue which is serviced during clientConnectionMade()

        @param tokens: The device tokens to schedule notifications for
        @type tokens: List of C{str}
        @param key: The key to use for this batch of notifications
        @type key: C{str}
        @param dataChangedTimestamp: Timestamp (epoch seconds) for the data change
            which triggered this notification
        @type key: C{int}
        """
        for token in tokens:
            tokenKeyPair = (token, key)
            for existingPair, ignored in self.queue:
                if tokenKeyPair == existingPair:
                    self.log_debug("APNProviderService has no connection; skipping duplicate: %s %s" % (token, key))
                    break # Already scheduled
            else:
                self.log_debug("APNProviderService has no connection; queuing: %s %s" % (token, key))
                self.queue.append(((token, key), dataChangedTimestamp))



    def sendNotification(self, token, key, dataChangedTimestamp):
        """
        If there is a connection the notification is sent right away, otherwise
        the notification is saved for later.

        @param token: The device token to send a notifications to
        @type token: C{str}
        @param key: The key to use for this notification
        @type key: C{str}
        @param dataChangedTimestamp: Timestamp (epoch seconds) for the data change
            which triggered this notification
        @type key: C{int}
        """
        if not (token and key and dataChangedTimestamp):
            return

        # Service has reference to factory has reference to protocol instance
        connection = getattr(self.factory, "connection", None)
        if connection is None:
            self._saveForWhenConnected([token], key, dataChangedTimestamp)
        else:
            connection.sendNotification(token, key, dataChangedTimestamp)



class APNFeedbackProtocol(protocol.Protocol, LoggingMixIn):
    """
    Implements the Feedback portion of APNS
    """

    MESSAGE_LENGTH = 38

    def connectionMade(self):
        self.log_debug("FeedbackProtocol connectionMade")
        self.buffer = ""

    @inlineCallbacks
    def dataReceived(self, data, fn=None):
        """
        Buffer and divide up received data into feedback messages which are
        always 38 bytes long
        """

        if fn is None:
            fn = self.processFeedback

        self.log_debug("FeedbackProtocol dataReceived %d bytes" % (len(data),))
        self.buffer += data

        while len(self.buffer) >= self.MESSAGE_LENGTH:
            message = self.buffer[:self.MESSAGE_LENGTH]
            self.buffer = self.buffer[self.MESSAGE_LENGTH:]

            try:
                timestamp, tokenLength, binaryToken = struct.unpack("!IH32s",
                    message)
                token = binaryToken.encode("hex").lower()
                yield fn(timestamp, token)
            except Exception, e:
                self.log_warn("FeedbackProtocol could not process message: %s (%s)" %
                    (message.encode("hex"), e))

    @inlineCallbacks
    def processFeedback(self, timestamp, token):
        """
        Handles a feedback message indicating that the given token is no
        longer active as of the timestamp, and its subscription should be
        removed as long as that device has not re-subscribed since the
        timestamp.

        @param timestamp: Seconds since the epoch
        @type timestamp: C{int}

        @param token: The device token to unsubscribe
        @type token: C{str}
        """

        self.log_debug("FeedbackProtocol processFeedback time=%d token=%s" %
            (timestamp, token))
        txn = self.factory.store.newTransaction()
        subscriptions = (yield txn.apnSubscriptionsByToken(token))

        for key, modified, uid in subscriptions:
            if timestamp > modified:
                self.log_debug("FeedbackProtocol removing subscription: %s %s" %
                    (token, key))
                yield txn.removeAPNSubscription(token, key)
        yield txn.commit()



class APNFeedbackFactory(ClientFactory, LoggingMixIn):

    protocol = APNFeedbackProtocol

    def __init__(self, store):
        self.store = store

    def clientConnectionFailed(self, connector, reason):
        self.log_error("Unable to connect to APN feedback server: %s" %
            (reason,))
        self.connected = False
        ClientFactory.clientConnectionFailed(self, connector, reason)


class APNFeedbackService(APNConnectionService):

    def __init__(self, store, updateSeconds, host, port,
        certPath, keyPath, chainPath="", passphrase="", sslMethod="TLSv1_METHOD",
        testConnector=None, reactor=None):

        APNConnectionService.__init__(self, host, port, certPath, keyPath,
            chainPath=chainPath, passphrase=passphrase, sslMethod=sslMethod,
            testConnector=testConnector, reactor=reactor)

        self.store = store
        self.updateSeconds = updateSeconds

    def startService(self):
        self.log_debug("APNFeedbackService startService")
        self.factory = APNFeedbackFactory(self.store)
        self.checkForFeedback()

    def stopService(self):
        self.log_debug("APNFeedbackService stopService")
        if self.nextCheck is not None:
            self.nextCheck.cancel()

    def checkForFeedback(self):
        self.nextCheck = None
        self.log_debug("APNFeedbackService checkForFeedback")
        self.connect(self.factory)
        self.nextCheck = self.reactor.callLater(self.updateSeconds,
            self.checkForFeedback)



class APNSubscriptionResource(ReadOnlyNoCopyResourceMixIn,
    DAVResourceWithoutChildrenMixin, DAVResource, LoggingMixIn):
    """
    The DAV resource allowing clients to subscribe to Apple push notifications.
    To subscribe, a client should first determine the key they are interested
    in my examining the "pushkey" DAV property on the home or collection they
    want to monitor.  Next the client sends an authenticated HTTP GET or POST
    request to this resource, passing their device token and the key in either
    the URL params or in the POST body.
    """

    def __init__(self, parent, store):
        DAVResource.__init__(
            self, principalCollections=parent.principalCollections()
        )
        self.parent = parent
        self.store = store

    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties

    def etag(self):
        return succeed(None)

    def checkPreconditions(self, request):
        return None

    def defaultAccessControlList(self):
        return davxml.ACL(
            # DAV:Read for authenticated principals
            davxml.ACE(
                davxml.Principal(davxml.Authenticated()),
                davxml.Grant(
                    davxml.Privilege(davxml.Read()),
                ),
                davxml.Protected(),
            ),
            # DAV:Write for authenticated principals
            davxml.ACE(
                davxml.Principal(davxml.Authenticated()),
                davxml.Grant(
                    davxml.Privilege(davxml.Write()),
                ),
                davxml.Protected(),
            ),
        )

    def contentType(self):
        return MimeType.fromString("text/html; charset=utf-8");

    def resourceType(self):
        return None

    def isCollection(self):
        return False

    def isCalendarCollection(self):
        return False

    def isPseudoCalendarCollection(self):
        return False

    @inlineCallbacks
    def http_POST(self, request):
        yield self.authorize(request, (davxml.Write(),))
        yield parsePOSTData(request)
        code, msg = (yield self.processSubscription(request))
        returnValue(self.renderResponse(code, body=msg))

    http_GET = http_POST

    def principalFromRequest(self, request):
        """
        Given an authenticated request, return the principal based on
        request.authnUser
        """
        principal = None
        for collection in self.principalCollections():
            data = request.authnUser.children[0].children[0].data
            principal = collection._principalForURI(data)
            if principal is not None:
                return principal

    @inlineCallbacks
    def processSubscription(self, request):
        """
        Given an authenticated request, use the token and key arguments
        to add a subscription entry to the database.

        @param request: The request to process
        @type request: L{twext.web2.server.Request}
        """

        token = request.args.get("token", ("",))[0].replace(" ", "").lower()
        key = request.args.get("key", ("",))[0]

        userAgent = request.headers.getHeader("user-agent", "-")
        host = request.remoteAddr.host
        fwdHeaders = request.headers.getRawHeaders("x-forwarded-for", [])
        if fwdHeaders:
            host = fwdHeaders[0]

        if not (key and token):
            code = responsecode.BAD_REQUEST
            msg = "Invalid request: both 'token' and 'key' must be provided"

        elif not validToken(token):
            code = responsecode.BAD_REQUEST
            msg = "Invalid request: bad 'token' %s" % (token,)

        else:
            principal = self.principalFromRequest(request)
            uid = principal.record.uid
            try:
                yield self.addSubscription(token, key, uid, userAgent, host)
                code = responsecode.OK
                msg = None
            except InvalidSubscriptionValues:
                code = responsecode.BAD_REQUEST
                msg = "Invalid subscription values"

        returnValue((code, msg))

    @inlineCallbacks
    def addSubscription(self, token, key, uid, userAgent, host):
        """
        Add a subscription (or update its timestamp if already there).

        @param token: The device token, must be lowercase
        @type token: C{str}

        @param key: The push key
        @type key: C{str}

        @param uid: The uid of the subscriber principal
        @type uid: C{str}

        @param userAgent: The user-agent requesting the subscription
        @type key: C{str}

        @param host: The host requesting the subscription
        @type key: C{str}
        """
        now = int(time.time()) # epoch seconds
        txn = self.store.newTransaction()
        yield txn.addAPNSubscription(token, key, now, uid, userAgent, host)
        yield txn.commit()

    def renderResponse(self, code, body=None):
        response = Response(code, {}, body)
        response.headers.setHeader("content-type", MimeType("text", "html"))
        return response
