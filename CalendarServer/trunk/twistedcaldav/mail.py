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

"""
Mail Gateway for Calendar Server

"""
from __future__ import with_statement

import datetime
import email.utils
import os
import uuid

try:
    from cStringIO import StringIO
    StringIO
except ImportError:
    from StringIO import StringIO

from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from zope.interface import implements

from twisted.application import internet, service
from twisted.internet import protocol, defer, ssl, reactor as _reactor
from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twisted.mail import pop3client, imap4
from twisted.mail.smtp import messageid, rfc822date, ESMTPSenderFactory
from twisted.plugin import IPlugin
from twisted.python.usage import Options, UsageError
from twisted.web import client

from twext.web2 import server, responsecode
from twext.web2.channel.http import HTTPFactory
from twext.web2.dav import davxml
from twext.web2.dav.noneprops import NonePropertyStore
from twext.web2.http import Response, HTTPError
from twext.web2.http_headers import MimeType


from twext.python.log import Logger, LoggingMixIn

from twistedcaldav import ical, caldavxml
from twistedcaldav import memcachepool
from twistedcaldav.config import config
from twistedcaldav.directory.util import transactionFromRequest
from twistedcaldav.ical import Property
from twistedcaldav.localization import translationTo, _
from twistedcaldav.resource import CalDAVResource
from twistedcaldav.schedule import deliverSchedulePrivilegeSet
from twistedcaldav.scheduling.cuaddress import normalizeCUAddr
from twistedcaldav.scheduling.itip import iTIPRequestStatus
from twistedcaldav.scheduling.scheduler import IMIPScheduler
from twistedcaldav.sql import AbstractSQLDatabase
from twistedcaldav.util import AuthorizedHTTPGetter
from twistedcaldav.stdconfig import DEFAULT_CONFIG, DEFAULT_CONFIG_FILE

from calendarserver.tap.util import getRootResource, directoryFromConfig


__all__ = [
    "IMIPInboxResource",
    "MailGatewayServiceMaker",
    "MailGatewayTokensDatabase",
    "MailHandler",
]


log = Logger()

#
# Mail gateway service config
#

class MailGatewayOptions(Options):
    optParameters = [[
        "config", "f", DEFAULT_CONFIG_FILE, "Path to configuration file."
    ]]

    def __init__(self, *args, **kwargs):
        super(MailGatewayOptions, self).__init__(*args, **kwargs)

        self.overrides = {}

    def _coerceOption(self, configDict, key, value):
        """
        Coerce the given C{val} to type of C{configDict[key]}
        """
        if key in configDict:
            if isinstance(configDict[key], bool):
                value = value == "True"

            elif isinstance(configDict[key], (int, float, long)):
                value = type(configDict[key])(value)

            elif isinstance(configDict[key], (list, tuple)):
                value = value.split(',')

            elif isinstance(configDict[key], dict):
                raise UsageError(
                    "Dict options not supported on the command line"
                )

            elif value == 'None':
                value = None

        return value

    def _setOverride(self, configDict, path, value, overrideDict):
        """
        Set the value at path in configDict
        """
        key = path[0]

        if len(path) == 1:
            overrideDict[key] = self._coerceOption(configDict, key, value)
            return

        if key in configDict:
            if not isinstance(configDict[key], dict):
                raise UsageError(
                    "Found intermediate path element that is not a dictionary"
                )

            if key not in overrideDict:
                overrideDict[key] = {}

            self._setOverride(
                configDict[key], path[1:],
                value, overrideDict[key]
            )


    def opt_option(self, option):
        """
        Set an option to override a value in the config file. True, False, int,
        and float options are supported, as well as comma seperated lists. Only
        one option may be given for each --option flag, however multiple
        --option flags may be specified.
        """

        if "=" in option:
            path, value = option.split('=')
            self._setOverride(
                DEFAULT_CONFIG,
                path.split('/'),
                value,
                self.overrides
            )
        else:
            self.opt_option('%s=True' % (option,))

    opt_o = opt_option

    def postOptions(self):
        config.load(self['config'])
        config.updateDefaults(self.overrides)
        self.parent['pidfile'] = None



class IMIPInboxResource(CalDAVResource):
    """
    IMIP-delivery Inbox resource.

    Extends L{DAVResource} to provide IMIP delivery functionality.
    """

    def __init__(self, parent, store):
        """
        @param parent: the parent resource of this one.
        @param store: the store to use for transactions.
        """
        assert parent is not None

        CalDAVResource.__init__(self, principalCollections=parent.principalCollections())

        self.parent = parent
        self._newStore = store


    def accessControlList(self, request, inheritance=True,
        expanding=False, inherited_aces=None):

        if not hasattr(self, "iMIPACL"):
            guid = config.Scheduling.iMIP.GUID
            self.iMIPACL = davxml.ACL(
                davxml.ACE(
                    davxml.Principal(
                        davxml.HRef.fromString("/principals/__uids__/%s/" % (guid,))
                    ),
                    davxml.Grant(
                        davxml.Privilege(caldavxml.ScheduleDeliver()),
                    ),
                ),
            )

        return succeed(self.iMIPACL)

    def resourceType(self):
        return davxml.ResourceType.ischeduleinbox

    def isCollection(self):
        return False

    def isCalendarCollection(self):
        return False

    def isPseudoCalendarCollection(self):
        return False

    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties

    def etag(self):
        return None

    def checkPreconditions(self, request):
        return None

    def render(self, request):
        output = """<html>
<head>
<title>IMIP Delivery Resource</title>
</head>
<body>
<h1>IMIP Delivery Resource.</h1>
</body
</html>"""

        response = Response(200, {}, output)
        response.headers.setHeader("content-type", MimeType("text", "html"))
        return response

    ##
    # File
    ##

    def createSimilarFile(self, path):
        log.err("Attempt to create clone %r of resource %r" % (path, self))
        raise HTTPError(responsecode.NOT_FOUND)

    ##
    # ACL
    ##

    def defaultAccessControlList(self):
        privs = (
            davxml.Privilege(davxml.Read()),
            davxml.Privilege(caldavxml.ScheduleDeliver()),
        )
        if config.Scheduling.CalDAV.OldDraftCompatibility:
            privs += (davxml.Privilege(caldavxml.Schedule()),)
        return davxml.ACL(
            # DAV:Read, CalDAV:schedule-deliver for all principals (includes anonymous)
            davxml.ACE(
                davxml.Principal(davxml.All()),
                davxml.Grant(*privs),
                davxml.Protected(),
            ),
        )

    def supportedPrivileges(self, request):
        return succeed(deliverSchedulePrivilegeSet)


class IMIPReplyInboxResource(IMIPInboxResource):

    def renderHTTP(self, request):
        """
        Set up a transaction which will be used and committed by implicit
        scheduling.
        """
        txn = transactionFromRequest(request, self._newStore)
        return super(IMIPReplyInboxResource, self).renderHTTP(request, txn)

    @inlineCallbacks
    def http_POST(self, request):
        """
        The IMIP reply POST method (inbound)
        """

        # Check authentication and access controls
        yield self.authorize(request, (caldavxml.ScheduleDeliver(),))

        # Inject using the IMIPScheduler.
        scheduler = IMIPScheduler(request, self)

        # Do the POST processing treating this as a non-local schedule
        result = (yield scheduler.doSchedulingViaPOST(use_request_headers=True))
        returnValue(result.response())


class IMIPInvitationInboxResource(IMIPInboxResource):

    def __init__(self, parent, store, mailer):
        super(IMIPInvitationInboxResource, self).__init__(parent, store)
        self.mailer = mailer

    @inlineCallbacks
    def http_POST(self, request):
        """
        The IMIP invitation POST method (outbound)
        """

        # Check authentication and access controls
        yield self.authorize(request, (caldavxml.ScheduleDeliver(),))

        # Compute token, add to db, generate email and send it
        calendar = (yield ical.Component.fromIStream(request.stream))
        originator = request.headers.getRawHeaders("originator")[0]
        recipient = request.headers.getRawHeaders("recipient")[0]
        language = config.Localization.Language

        if not (yield self.mailer.outbound(originator,
            recipient, calendar, language=language)):
            returnValue(Response(code=responsecode.BAD_REQUEST))

        returnValue(Response(code=responsecode.OK))



def injectMessage(organizer, attendee, calendar, msgId, reactor=None):

    if reactor is None:
        reactor = _reactor

    headers = {
        'Content-Type' : 'text/calendar',
        'Originator' : attendee,
        'Recipient' : organizer,
        config.Scheduling.iMIP.Header : config.Scheduling.iMIP.Password,
    }

    data = str(calendar)

    if config.EnableSSL:
        useSSL = True
        port = config.SSLPort
    else:
        useSSL = False
        port = config.HTTPPort

    # If we're running on same host as calendar server, inject via localhost
    if config.Scheduling.iMIP.MailGatewayServer == 'localhost':
        host = 'localhost'
    else:
        host = config.ServerHostName
    path = "inbox"
    scheme = "https:" if useSSL else "http:"
    url = "%s//%s:%d/%s/" % (scheme, host, port, path)

    log.debug("Injecting to %s: %s %s" % (url, str(headers), data))

    factory = client.HTTPClientFactory(url, method='POST', headers=headers,
        postdata=data, agent="iMIP gateway")

    factory.noisy = False
    factory.protocol = AuthorizedHTTPGetter

    if useSSL:
        reactor.connectSSL(host, port, factory, ssl.ClientContextFactory())
    else:
        reactor.connectTCP(host, port, factory)

    def _success(result, msgId):
        log.info("Mail gateway successfully injected message %s" % (msgId,))

    def _failure(failure, msgId):
        log.err("Mail gateway failed to inject message %s (Reason: %s)" %
            (msgId, failure.getErrorMessage()))
        log.debug("Failed calendar body: %s" % (str(calendar),))

    factory.deferred.addCallback(_success, msgId).addErrback(_failure, msgId)
    return factory.deferred




class MailGatewayTokensDatabase(AbstractSQLDatabase, LoggingMixIn):
    """
    A database to maintain "plus-address" tokens for IMIP requests.

    SCHEMA:

    Token Database:

    ROW: TOKEN, ORGANIZER, ATTENDEE, ICALUID, DATESTAMP

    """

    dbType = "MAILGATEWAYTOKENS"
    dbFilename = "mailgatewaytokens.sqlite"
    dbFormatVersion = "1"


    def __init__(self, path):
        if path != ":memory:":
            path = os.path.join(path, MailGatewayTokensDatabase.dbFilename)
        super(MailGatewayTokensDatabase, self).__init__(path, True)

    def createToken(self, organizer, attendee, icaluid, token=None):
        if token is None:
            token = str(uuid.uuid4())
        self._db_execute(
            """
            insert into TOKENS (TOKEN, ORGANIZER, ATTENDEE, ICALUID, DATESTAMP)
            values (:1, :2, :3, :4, :5)
            """, token, organizer, attendee, icaluid, datetime.date.today()
        )
        self._db_commit()
        return token

    def lookupByToken(self, token):
        results = list(
            self._db_execute(
                """
                select ORGANIZER, ATTENDEE, ICALUID from TOKENS
                where TOKEN = :1
                """, token
            )
        )

        if len(results) != 1:
            return None

        return results[0]

    def getToken(self, organizer, attendee, icaluid):
        token = self._db_value_for_sql(
            """
            select TOKEN from TOKENS
            where ORGANIZER = :1 and ATTENDEE = :2 and ICALUID = :3
            """, organizer, attendee, icaluid
        )
        if token is not None:
            # update the datestamp on the token to keep it from being purged
            self._db_execute(
                """
                update TOKENS set DATESTAMP = :1 WHERE TOKEN = :2
                """, datetime.date.today(), token
            )
        return token

    def deleteToken(self, token):
        self._db_execute(
            """
            delete from TOKENS where TOKEN = :1
            """, token
        )
        self._db_commit()

    def purgeOldTokens(self, before):
        self._db_execute(
            """
            delete from TOKENS where DATESTAMP < :1
            """, before
        )
        self._db_commit()

    def _db_version(self):
        """
        @return: the schema version assigned to this index.
        """
        return MailGatewayTokensDatabase.dbFormatVersion

    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return MailGatewayTokensDatabase.dbType

    def _db_init_data_tables(self, q):
        """
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """

        #
        # TOKENS table
        #
        q.execute(
            """
            create table TOKENS (
                TOKEN       text,
                ORGANIZER   text,
                ATTENDEE    text,
                ICALUID     text,
                DATESTAMP   date
            )
            """
        )
        q.execute(
            """
            create index TOKENSINDEX on TOKENS (TOKEN)
            """
        )

    def _db_upgrade_data_tables(self, q, old_version):
        """
        Upgrade the data from an older version of the DB.
        @param q: a database cursor to use.
        @param old_version: existing DB's version number
        @type old_version: str
        """
        pass



#
# Service
#

class MailGatewayService(service.MultiService):

    def startService(self):
        """
        Purge old database tokens -- doing this in startService so that
        it happens after we've shed privileges
        """
        service.MultiService.startService(self)
        mailer = getattr(self, "mailer", None)
        if mailer is not None:
            mailer.purge()


class MailGatewayServiceMaker(LoggingMixIn):
    implements(IPlugin, service.IServiceMaker)

    tapname = "caldav_mailgateway"
    description = "Mail Gateway"
    options = MailGatewayOptions

    def makeService(self, options):

        memcachepool.installPools(
            config.Memcached.Pools,
            config.Memcached.MaxClients,
        )

        mailGatewayService = MailGatewayService()

        settings = config.Scheduling['iMIP']
        if settings['Enabled']:
            mailer = MailHandler()

            mailType = settings['Receiving']['Type']
            if mailType.lower().startswith('pop'):
                self.log_info("Starting Mail Gateway Service: POP3")
                client = POP3Service(settings['Receiving'], mailer)
            elif mailType.lower().startswith('imap'):
                self.log_info("Starting Mail Gateway Service: IMAP4")
                client = IMAP4Service(settings['Receiving'], mailer)
            else:
                # TODO: raise error?
                self.log_error("Invalid iMIP type in configuration: %s" %
                    (mailType,))
                return mailGatewayService

            client.setServiceParent(mailGatewayService)

            # Set up /inbox -- server POSTs to it to send out iMIP invites
            IScheduleService(settings, mailer).setServiceParent(mailGatewayService)

        else:
            mailer = None
            self.log_info("Mail Gateway Service not enabled")

        mailGatewayService.mailer = mailer
        return mailGatewayService


class IScheduleService(service.MultiService, LoggingMixIn):
    """
    ISchedule Inbox
    """

    def __init__(self, settings, mailer):
        service.MultiService.__init__(self)
        self.settings = settings
        self.mailer = mailer

        # Disable since we're only interested in /principals (for auth)
        config.EnableCalDAV = False
        config.EnableCardDAV = False

        rootResource = getRootResource(
            config,
            "IGNORED", # no need for a store - no /calendars nor /addressbooks
            (
                ("inbox", IMIPInvitationInboxResource, (mailer,), "digest"),
            )
        )

        self.factory = HTTPFactory(server.Site(rootResource))
        self.server = internet.TCPServer(settings['MailGatewayPort'],
            self.factory)
        self.server.setServiceParent(self)



class MailHandler(LoggingMixIn):

    def __init__(self, dataRoot=None):
        if dataRoot is None:
            dataRoot = config.DataRoot
        self.db = MailGatewayTokensDatabase(dataRoot)
        self.days = config.Scheduling['iMIP']['InvitationDaysToLive']
        self.directory = directoryFromConfig(config)

    def purge(self):
        """
        Purge old database tokens
        """
        self.db.purgeOldTokens(datetime.date.today() -
            datetime.timedelta(days=self.days))

    def checkDSN(self, message):
        # returns (isDSN, Action, icalendar attachment)

        report = deliveryStatus = calBody = None

        for part in message.walk():
            content_type = part.get_content_type()
            if content_type == "multipart/report":
                report = part
                continue
            elif content_type == "message/delivery-status":
                deliveryStatus = part
                continue
            elif content_type == "message/rfc822":
                #original = part
                continue
            elif content_type == "text/calendar":
                calBody = part.get_payload(decode=True)
                continue

        if report is not None and deliveryStatus is not None:
            # we have what appears to be a DSN

            lines = str(deliveryStatus).split("\n")
            for line in lines:
                lower = line.lower()
                if lower.startswith("action:"):
                    # found Action:
                    action = lower.split(' ')[1]
                    break
            else:
                action = None

            return True, action, calBody

        else:
            # Not a DSN
            return False, None, None


    def _extractToken(self, text):
        try:
            pre, post = text.split('@')
            pre, token = pre.split('+')
            return token
        except ValueError:
            return None

    def processDSN(self, calBody, msgId, fn):
        calendar = ical.Component.fromString(calBody)
        # Extract the token (from organizer property)
        organizer = calendar.getOrganizer()
        token = self._extractToken(organizer)
        if not token:
            self.log_error("Mail gateway can't find token in DSN %s" % (msgId,))
            return

        result = self.db.lookupByToken(token)
        if result is None:
            # This isn't a token we recognize
            self.log_error("Mail gateway found a token (%s) but didn't recognize it in DSN %s" % (token, msgId))
            return

        organizer, attendee, icaluid = result
        organizer = str(organizer)
        attendee = str(attendee)
        icaluid = str(icaluid)
        calendar.removeAllButOneAttendee(attendee)
        calendar.getOrganizerProperty().setValue(organizer)
        for comp in calendar.subcomponents():
            if comp.name() == "VEVENT":
                comp.addProperty(Property("REQUEST-STATUS",
                    ["5.1", "Service unavailable"]))
                break
        else:
            # no VEVENT in the calendar body.
            # TODO: what to do in this case?
            pass

        self.log_warn("Mail gateway processing DSN %s" % (msgId,))
        return fn(organizer, attendee, calendar, msgId)

    def processReply(self, msg, injectFunction, testMode=False):
        # extract the token from the To header
        name, addr = email.utils.parseaddr(msg['To'])
        if addr:
            # addr looks like: server_address+token@example.com
            token = self._extractToken(addr)
            if not token:
                self.log_error("Mail gateway didn't find a token in message %s (%s)" % (msg['Message-ID'], msg['To']))
                return
        else:
            self.log_error("Mail gateway couldn't parse To: address (%s) in message %s" % (msg['To'], msg['Message-ID']))
            return

        result = self.db.lookupByToken(token)
        if result is None:
            # This isn't a token we recognize
            self.log_error("Mail gateway found a token (%s) but didn't recognize it in message %s" % (token, msg['Message-ID']))
            return

        organizer, attendee, icaluid = result
        organizer = str(organizer)
        attendee = str(attendee)
        icaluid = str(icaluid)

        for part in msg.walk():
            if part.get_content_type() == "text/calendar":
                calBody = part.get_payload(decode=True)
                break
        else:
            # No icalendar attachment
            self.log_warn("Mail gateway didn't find an icalendar attachment in message %s" % (msg['Message-ID'],))

            toAddr = None
            fromAddr = attendee[7:]

            if organizer.startswith("mailto:"):
                toAddr = organizer[7:]
            elif organizer.startswith("urn:uuid:"):
                guid = organizer[9:]
                record = self.directory.recordWithGUID(guid)
                if record and record.emailAddresses:
                    toAddr = list(record.emailAddresses)[0]

            if toAddr is None:
                self.log_error("Don't have an email address for the organizer; ignoring reply.")
                return

            if testMode:
                return (toAddr, fromAddr)

            settings = config.Scheduling["iMIP"]["Sending"]
            if settings["UseSSL"]:
                contextFactory = ssl.ClientContextFactory()
            else:
                contextFactory = None

            deferred = defer.Deferred()
            del msg["From"]
            msg["From"] = fromAddr
            del msg["Reply-To"]
            msg["Reply-To"] = fromAddr
            del msg["To"]
            msg["To"] = toAddr
            factory = ESMTPSenderFactory(
                settings["Username"], settings["Password"],
                fromAddr, toAddr, StringIO(str(msg)), deferred,
                contextFactory=contextFactory,
                requireAuthentication=False,
                requireTransportSecurity=settings["UseSSL"],
            )

            self.log_warn("Mail gateway forwarding reply back to organizer")
            _reactor.connectTCP(settings["Server"], settings["Port"], factory)
            return deferred


        # Process the imip attachment; inject to calendar server

        self.log_debug(calBody)
        calendar = ical.Component.fromString(calBody)
        event = calendar.mainComponent()

        calendar.removeAllButOneAttendee(attendee)
        organizerProperty = calendar.getOrganizerProperty()
        if organizerProperty is None:
            # ORGANIZER is required per rfc2446 section 3.2.3
            self.log_warn("Mail gateway didn't find an ORGANIZER in REPLY %s" % (msg['Message-ID'],))
            event.addProperty(Property("ORGANIZER", organizer))
        else:
            organizerProperty.setValue(organizer)

        if not calendar.getAttendees():
            # The attendee we're expecting isn't there, so add it back
            # with a SCHEDULE-STATUS of SERVICE_UNAVAILABLE.
            # The organizer will then see that the reply was not successful.
            attendeeProp = Property("ATTENDEE", attendee,
                params = {
                    "SCHEDULE-STATUS": iTIPRequestStatus.SERVICE_UNAVAILABLE,
                }
            )
            event.addProperty(attendeeProp)

            # TODO: We have talked about sending an email to the reply-to
            # at this point, to let them know that their reply was missing
            # the appropriate ATTENDEE.  This will require a new localizable
            # email template for the message.

        return injectFunction(organizer, attendee, calendar, msg['Message-ID'])


    def inbound(self, message, fn=injectMessage):
        try:
            msg = email.message_from_string(message)

            isDSN, action, calBody = self.checkDSN(msg)
            if isDSN:
                if action == 'failed' and calBody:
                    # This is a DSN we can handle
                    return self.processDSN(calBody, msg['Message-ID'], fn)
                else:
                    # It's a DSN without enough to go on
                    self.log_error("Mail gateway can't process DSN %s" % (msg['Message-ID'],))
                    return

            self.log_info("Mail gateway received message %s from %s to %s" %
                (msg['Message-ID'], msg['From'], msg['To']))

            return self.processReply(msg, fn)

        except Exception, e:
            # Don't let a failure of any kind stop us
            self.log_error("Failed to process message: %s" % (e,))




    def outbound(self, originator, recipient, calendar, language='en', send=True):
        # create token, send email

        component = calendar.masterComponent()
        if component is None:
            component = calendar.mainComponent(True)
        icaluid = component.propertyValue("UID")
        method = calendar.propertyValue("METHOD")

        # Clean up the attendee list which is purely used within the human
        # readable email message (not modifying the calendar body)
        attendees = []
        for attendeeProp in calendar.getAllAttendeeProperties():
            cutype = attendeeProp.parameterValue("CUTYPE", "INDIVIDUAL")
            if cutype == "INDIVIDUAL":
                cn = attendeeProp.parameterValue("CN", None)
                if cn is not None:
                    cn = cn.decode("utf-8")
                cuaddr = normalizeCUAddr(attendeeProp.value())
                if cuaddr.startswith("mailto:"):
                    mailto = cuaddr[7:]
                    if not cn:
                        cn = mailto
                else:
                    emailAddress = attendeeProp.parameterValue("EMAIL", None)
                    if emailAddress:
                        mailto = emailAddress
                    else:
                        mailto = None

                if cn or mailto:
                    attendees.append( (cn, mailto) )


        recipient = recipient.lower()
        toAddr = recipient
        if not recipient.startswith("mailto:"):
            raise ValueError("ATTENDEE address '%s' must be mailto: for iMIP operation." % (recipient,))
        recipient = recipient[7:]

        settings = config.Scheduling['iMIP']['Sending']

        if method != "REPLY":
            # Invites and cancellations:

            # Reuse or generate a token based on originator, toAddr, and
            # event uid
            token = self.db.getToken(originator, toAddr, icaluid)
            if token is None:
                token = self.db.createToken(originator, toAddr, icaluid)
                self.log_debug("Mail gateway created token %s for %s (originator), %s (recipient) and %s (icaluid)" % (token, originator, toAddr, icaluid))
                inviteState = "new"
            else:
                self.log_debug("Mail gateway reusing token %s for %s (originator), %s (recipient) and %s (icaluid)" % (token, originator, toAddr, icaluid))
                inviteState = "update"

            fullServerAddress = settings['Address']
            name, serverAddress = email.utils.parseaddr(fullServerAddress)
            pre, post = serverAddress.split('@')
            addressWithToken = "%s+%s@%s" % (pre, token, post)

            organizerProperty = calendar.getOrganizerProperty()
            organizerEmailAddress = organizerProperty.parameterValue("EMAIL", None)
            organizerValue = organizerProperty.value()
            organizerProperty.setValue("mailto:%s" % (addressWithToken,))

            # If the organizer is also an attendee, update that attendee value
            # to match
            organizerAttendeeProperty = calendar.getAttendeeProperty([organizerValue])
            if organizerAttendeeProperty is not None:
                organizerAttendeeProperty.setValue("mailto:%s" % (addressWithToken,))

            # The email's From will include the originator's real name email
            # address if available.  Otherwise it will be the server's email
            # address (without # + addressing)
            if organizerEmailAddress:
                orgEmail = fromAddr = organizerEmailAddress
            else:
                fromAddr = serverAddress
                orgEmail = None
            cn = calendar.getOrganizerProperty().parameterValue('CN', None)
            if cn is None:
                cn = 'Calendar Server'
                orgCN = orgEmail
            else:
                orgCN = cn.decode("utf-8")
            formattedFrom = "%s <%s>" % (cn, fromAddr)

            # Reply-to address will be the server+token address

        else: # REPLY
            inviteState = "reply"

            # Look up the attendee property corresponding to the originator
            # of this reply
            originatorAttendeeProperty = calendar.getAttendeeProperty([originator])
            formattedFrom = fromAddr = originator = ""
            if originatorAttendeeProperty:
                originatorAttendeeEmailAddress = originatorAttendeeProperty.parameterValue("EMAIL", None)
                if originatorAttendeeEmailAddress:
                    formattedFrom = fromAddr = originator = originatorAttendeeEmailAddress

            organizerMailto = str(calendar.getOrganizer())
            if not organizerMailto.lower().startswith("mailto:"):
                raise ValueError("ORGANIZER address '%s' must be mailto: for REPLY." % (organizerMailto,))
            orgEmail = organizerMailto[7:]

            orgCN = calendar.getOrganizerProperty().parameterValue('CN', None)
            addressWithToken = formattedFrom


        # Now prevent any "internal" CUAs from being exposed by converting
        # to mailto: if we have one
        for attendeeProp in calendar.getAllAttendeeProperties():
            cutype = attendeeProp.parameterValue('CUTYPE', None)
            if cutype == "INDIVIDUAL":
                cuaddr = normalizeCUAddr(attendeeProp.value())
                if not cuaddr.startswith("mailto:"):
                    emailAddress = attendeeProp.parameterValue("EMAIL", None)
                    if emailAddress:
                        attendeeProp.setValue("mailto:%s" % (emailAddress,))


        msgId, message = self.generateEmail(inviteState, calendar, orgEmail,
            orgCN, attendees, formattedFrom, addressWithToken, recipient,
            language=language)

        if send:
            self.log_debug("Sending: %s" % (message,))
            def _success(result, msgId, fromAddr, toAddr):
                self.log_info("Mail gateway sent message %s from %s to %s" %
                    (msgId, fromAddr, toAddr))
                return True

            def _failure(failure, msgId, fromAddr, toAddr):
                self.log_error("Mail gateway failed to send message %s from %s to %s (Reason: %s)" %
                    (msgId, fromAddr, toAddr, failure.getErrorMessage()))
                return False

            deferred = defer.Deferred()

            if settings["UseSSL"]:
                contextFactory = ssl.ClientContextFactory()
            else:
                contextFactory = None

            factory = ESMTPSenderFactory(settings['Username'], settings['Password'],
                fromAddr, toAddr, StringIO(str(message)), deferred,
                contextFactory=contextFactory,
                requireAuthentication=False,
                requireTransportSecurity=settings["UseSSL"])

            _reactor.connectTCP(settings['Server'], settings['Port'], factory)
            deferred.addCallback(_success, msgId, fromAddr, toAddr)
            deferred.addErrback(_failure, msgId, fromAddr, toAddr)
            return deferred
        else:
            return succeed((inviteState, calendar, orgEmail, orgCN, attendees,
                formattedFrom, recipient, addressWithToken))


    def getIconPath(self, details, canceled, language='en'):
        iconDir = config.Scheduling.iMIP.MailIconsDirectory.rstrip("/")

        if canceled:
            iconName = "canceled.png"
            iconPath = os.path.join(iconDir, iconName)
            if os.path.exists(iconPath):
                return iconPath
            else:
                return None

        else:
            month = int(details['month'])
            day = int(details['day'])
            with translationTo(language) as trans:
                monthName = trans.monthAbbreviation(month)
            iconName = "%02d.png" % (day,)
            iconPath = os.path.join(iconDir, monthName, iconName)
            if not os.path.exists(iconPath):
                # Try the generic (numeric) version
                iconPath = os.path.join(iconDir, "%02d" % (month,), iconName)
                if not os.path.exists(iconPath):
                    return None
            return iconPath


    def generateEmail(self, inviteState, calendar, orgEmail, orgCN,
        attendees, fromAddress, replyToAddress, toAddress, language='en'):

        details = self.getEventDetails(calendar, language=language)
        canceled = (calendar.propertyValue("METHOD") == "CANCEL")
        iconPath = self.getIconPath(details, canceled, language=language)

        with translationTo(language):
            msg = MIMEMultipart()
            msg["From"] = fromAddress
            msg["Reply-To"] = replyToAddress
            msg["To"] = toAddress
            msg["Date"] = rfc822date()
            msgId = messageid()
            msg["Message-ID"] = msgId

            if canceled:
                formatString = _("Event canceled: %(summary)s")
            elif inviteState == "new":
                formatString = _("Event invitation: %(summary)s")
            elif inviteState == "update":
                formatString = _("Event update: %(summary)s")
            else:
                formatString = _("Event reply: %(summary)s")

            # The translations we get back from gettext are utf-8 encoded
            # strings, so convert to unicode
            formatString = formatString.decode("utf-8")

            details['subject'] = msg['Subject'] = formatString % {
                'summary' : details['summary']
            }

            msgAlt = MIMEMultipart("alternative")
            msg.attach(msgAlt)

            # Get localized labels
            if canceled:
                details['inviteLabel'] = _("Event Canceled")
            else:
                if inviteState == "new":
                    details['inviteLabel'] = _("Event Invitation")
                elif inviteState == "update":
                    details['inviteLabel'] = _("Event Update")
                else:
                    details['inviteLabel'] = _("Event Reply")

            details['dateLabel'] = _("Date")
            details['timeLabel'] = _("Time")
            details['durationLabel'] = _("Duration")
            details['recurrenceLabel'] = _("Occurs")
            details['descLabel'] = _("Description")
            details['orgLabel'] = _("Organizer")
            details['attLabel'] = _("Attendees")
            details['locLabel'] = _("Location")


            plainAttendeeList = []
            for cn, mailto in attendees:
                if cn:
                    plainAttendeeList.append(cn if not mailto else
                        "%s <%s>" % (cn, mailto))
                elif mailto:
                    plainAttendeeList.append("<%s>" % (mailto,))

            details['plainAttendees'] = ", ".join(plainAttendeeList)

            details['plainOrganizer'] = (orgCN if not orgEmail else
                "%s <%s>" % (orgCN, orgEmail))

            # The translations we get back from gettext are utf-8 encoded
            # strings, so convert to unicode
            for key in details.keys():
                if isinstance(details[key], str):
                    details[key] = details[key].decode("utf-8")

            # plain text version
            if canceled:
                plainTemplate = u"""%(subject)s

%(orgLabel)s: %(plainOrganizer)s
%(dateLabel)s: %(dateInfo)s %(recurrenceInfo)s
%(timeLabel)s: %(timeInfo)s %(durationInfo)s
"""
            else:
                plainTemplate = u"""%(subject)s

%(orgLabel)s: %(plainOrganizer)s
%(locLabel)s: %(location)s
%(dateLabel)s: %(dateInfo)s %(recurrenceInfo)s
%(timeLabel)s: %(timeInfo)s %(durationInfo)s
%(descLabel)s: %(description)s
%(attLabel)s: %(plainAttendees)s
"""

            plainText = plainTemplate % details

            msgPlain = MIMEText(plainText.encode("UTF-8"), "plain", "UTF-8")
            msgAlt.attach(msgPlain)

            # html version
            msgHtmlRelated = MIMEMultipart("related", type="text/html")
            msgAlt.attach(msgHtmlRelated)


            htmlAttendees = []
            for cn, mailto in attendees:
                if mailto:
                    htmlAttendees.append('<a href="mailto:%s">%s</a>' %
                        (mailto, cn))
                else:
                    htmlAttendees.append(cn)

            details['htmlAttendees'] = ", ".join(htmlAttendees)

            if orgEmail:
                details['htmlOrganizer'] = '<a href="mailto:%s">%s</a>' % (
                    orgEmail, orgCN)
            else:
                details['htmlOrganizer'] = orgCN

            details['iconName'] = iconName = "calicon.png"

            templateDir = config.Scheduling.iMIP.MailTemplatesDirectory.rstrip("/")
            templateName = "cancel.html" if canceled else "invite.html"
            templatePath = os.path.join(templateDir, templateName)

            if not os.path.exists(templatePath):
                # Fall back to built-in simple templates:
                if canceled:

                    htmlTemplate = u"""<html>
    <body><div>

    <h1>%(subject)s</h1>
    <p>
    <h3>%(orgLabel)s:</h3> %(htmlOrganizer)s
    </p>
    <p>
    <h3>%(dateLabel)s:</h3> %(dateInfo)s %(recurrenceInfo)s
    </p>
    <p>
    <h3>%(timeLabel)s:</h3> %(timeInfo)s %(durationInfo)s
    </p>

    """

                else:

                    htmlTemplate = u"""<html>
    <body><div>
    <p>%(inviteLabel)s</p>

    <h1>%(summary)s</h1>
    <p>
    <h3>%(orgLabel)s:</h3> %(htmlOrganizer)s
    </p>
    <p>
    <h3>%(locLabel)s:</h3> %(location)s
    </p>
    <p>
    <h3>%(dateLabel)s:</h3> %(dateInfo)s %(recurrenceInfo)s
    </p>
    <p>
    <h3>%(timeLabel)s:</h3> %(timeInfo)s %(durationInfo)s
    </p>
    <p>
    <h3>%(descLabel)s:</h3> %(description)s
    </p>
    <p>
    <h3>%(attLabel)s:</h3> %(htmlAttendees)s
    </p>

    """
            else: # HTML template file exists

                with open(templatePath) as templateFile:
                    htmlTemplate = templateFile.read()

            htmlText = htmlTemplate % details

        msgHtml = MIMEText(htmlText.encode("UTF-8"), "html", "UTF-8")
        msgHtmlRelated.attach(msgHtml)

        # an image for html version
        if (iconPath != None and os.path.exists(iconPath) and
            htmlTemplate.find("cid:%(iconName)s") != -1):

            with open(iconPath) as iconFile:
                msgIcon = MIMEImage(iconFile.read(),
                    _subtype='png;x-apple-mail-type=stationery;name="%s"' %
                    (iconName,))

            msgIcon.add_header("Content-ID", "<%s>" % (iconName,))
            msgIcon.add_header("Content-Disposition", "inline;filename=%s" %
                (iconName,))
            msgHtmlRelated.attach(msgIcon)

        # the icalendar attachment
        self.log_debug("Mail gateway sending calendar body: %s" % (str(calendar)))
        msgIcal = MIMEText(str(calendar), "calendar", "UTF-8")
        method = calendar.propertyValue("METHOD").lower()
        msgIcal.set_param("method", method)
        msgIcal.add_header("Content-ID", "<invitation.ics>")
        msgIcal.add_header("Content-Disposition",
            "inline;filename=invitation.ics")
        msg.attach(msgIcal)

        return msgId, msg.as_string()


    def getEventDetails(self, calendar, language='en'):

        # Get the most appropriate component
        component = calendar.masterComponent()
        if component is None:
            component = calendar.mainComponent(True)

        results = { }

        dtStart = component.propertyValue("DTSTART")
        results['month'] = dtStart.getMonth()
        results['day'] = dtStart.getDay()

        summary = component.propertyValue("SUMMARY").decode("utf-8")
        if summary is None:
            summary = ""
        results['summary'] = summary

        description = component.propertyValue("DESCRIPTION")
        if description is None:
            description = ""
        results['description'] = description

        location = component.propertyValue("LOCATION")
        if location is None:
            location = ""
        results['location'] = location

        with translationTo(language) as trans:
            results['dateInfo'] = trans.date(component)
            results['timeInfo'], duration = trans.time(component)
            results['durationInfo'] = "(%s)" % (duration,) if duration else ""

            for propertyName in ("RRULE", "RDATE", "EXRULE", "EXDATE",
                "RECURRENCE-ID"):
                if component.hasProperty(propertyName):
                    results['recurrenceInfo'] = _("(Repeating)")
                    break
            else:
                results['recurrenceInfo'] = ""

        return results







#
# POP3
#

class POP3Service(service.Service, LoggingMixIn):

    def __init__(self, settings, mailer):
        if settings["UseSSL"]:
            self.client = internet.SSLClient(settings["Server"],
                settings["Port"],
                POP3DownloadFactory(settings, mailer),
                ssl.ClientContextFactory())
        else:
            self.client = internet.TCPClient(settings["Server"],
                settings["Port"],
                POP3DownloadFactory(settings, mailer))

        self.mailer = mailer

    def startService(self):
        self.client.startService()

    def stopService(self):
        self.client.stopService()


class POP3DownloadProtocol(pop3client.POP3Client, LoggingMixIn):
    allowInsecureLogin = False

    def serverGreeting(self, greeting):
        self.log_debug("POP servergreeting")
        pop3client.POP3Client.serverGreeting(self, greeting)
        login = self.login(self.factory.settings["Username"],
            self.factory.settings["Password"])
        login.addCallback(self.cbLoggedIn)
        login.addErrback(self.cbLoginFailed)

    def cbLoginFailed(self, reason):
        self.log_error("POP3 login failed for %s" %
            (self.factory.settings["Username"],))
        return self.quit()

    def cbLoggedIn(self, result):
        self.log_debug("POP loggedin")
        return self.listSize().addCallback(self.cbGotMessageSizes)

    def cbGotMessageSizes(self, sizes):
        self.log_debug("POP gotmessagesizes")
        downloads = []
        for i in range(len(sizes)):
            downloads.append(self.retrieve(i).addCallback(self.cbDownloaded, i))
        return defer.DeferredList(downloads).addCallback(self.cbFinished)

    def cbDownloaded(self, lines, id):
        self.log_debug("POP downloaded message %d" % (id,))
        self.factory.handleMessage("\r\n".join(lines))
        self.log_debug("POP deleting message %d" % (id,))
        self.delete(id)

    def cbFinished(self, results):
        self.log_debug("POP finished")
        return self.quit()


class POP3DownloadFactory(protocol.ClientFactory, LoggingMixIn):
    protocol = POP3DownloadProtocol

    def __init__(self, settings, mailer, reactor=None):
        self.settings = settings
        self.mailer = mailer
        if reactor is None:
            from twisted.internet import reactor
        self.reactor = reactor
        self.nextPoll = None
        self.noisy = False

    def retry(self, connector=None):
        # TODO: if connector is None:

        if connector is None:
            if self.connector is None:
                self.log_error("No connector to retry")
                return
            else:
                connector = self.connector

        def reconnector():
            self.nextPoll = None
            connector.connect()

        self.log_debug("Scheduling next POP3 poll")
        self.nextPoll = self.reactor.callLater(self.settings["PollingSeconds"],
            reconnector)

    def clientConnectionLost(self, connector, reason):
        self.connector = connector
        self.log_debug("POP factory connection lost")
        self.retry(connector)


    def clientConnectionFailed(self, connector, reason):
        self.connector = connector
        self.log_info("POP factory connection failed")
        self.retry(connector)

    def handleMessage(self, message):
        self.log_debug("POP factory handle message")
        self.log_debug(message)

        return self.mailer.inbound(message)




#
# IMAP4
#

class IMAP4Service(service.Service):

    def __init__(self, settings, mailer):

        if settings["UseSSL"]:
            self.client = internet.SSLClient(settings["Server"],
                settings["Port"],
                IMAP4DownloadFactory(settings, mailer),
                ssl.ClientContextFactory())
        else:
            self.client = internet.TCPClient(settings["Server"],
                settings["Port"],
                IMAP4DownloadFactory(settings, mailer))

        self.mailer = mailer

    def startService(self):
        self.client.startService()

    def stopService(self):
        self.client.stopService()


class IMAP4DownloadProtocol(imap4.IMAP4Client, LoggingMixIn):

    def serverGreeting(self, capabilities):
        self.log_debug("IMAP servergreeting")
        return self.authenticate(self.factory.settings["Password"]
            ).addCallback(self.cbLoggedIn
            ).addErrback(self.ebAuthenticateFailed)

    def ebLogError(self, error):
        self.log_error("IMAP Error: %s" % (error,))

    def ebAuthenticateFailed(self, reason):
        self.log_debug("IMAP authenticate failed for %s, trying login" %
            (self.factory.settings["Username"],))
        return self.login(self.factory.settings["Username"],
            self.factory.settings["Password"]
            ).addCallback(self.cbLoggedIn
            ).addErrback(self.ebLoginFailed)

    def ebLoginFailed(self, reason):
        self.log_error("IMAP login failed for %s" %
            (self.factory.settings["Username"],))
        self.transport.loseConnection()

    def cbLoggedIn(self, result):
        self.log_debug("IMAP logged in [%s]" % (self.state,))
        self.select("Inbox").addCallback(self.cbInboxSelected)

    def cbInboxSelected(self, result):
        self.log_debug("IMAP Inbox selected [%s]" % (self.state,))
        allMessages = imap4.MessageSet(1, None)
        self.fetchUID(allMessages, True).addCallback(self.cbGotUIDs)

    def cbGotUIDs(self, results):
        self.log_debug("IMAP got uids [%s]" % (self.state,))
        self.messageUIDs = [result['UID'] for result in results.values()]
        self.messageCount = len(self.messageUIDs)
        self.log_debug("IMAP Inbox has %d messages" % (self.messageCount,))
        if self.messageCount:
            self.fetchNextMessage()
        else:
            # No messages; close it out
            self.close().addCallback(self.cbClosed)

    def fetchNextMessage(self):
        self.log_debug("IMAP in fetchnextmessage [%s]" % (self.state,))
        if self.messageUIDs:
            nextUID = self.messageUIDs.pop(0)
            messageListToFetch = imap4.MessageSet(nextUID)
            self.log_debug("Downloading message %d of %d (%s)" %
                (self.messageCount - len(self.messageUIDs), self.messageCount,
                nextUID))
            self.fetchMessage(messageListToFetch, True).addCallback(
                self.cbGotMessage, messageListToFetch).addErrback(self.ebLogError)
        else:
            self.log_debug("Seeing if anything new has arrived")
            # Go back and see if any more messages have come in
            self.expunge().addCallback(self.cbInboxSelected)

    def cbGotMessage(self, results, messageList):
        self.log_debug("IMAP in cbGotMessage [%s]" % (self.state,))
        try:
            messageData = results.values()[0]['RFC822']
        except IndexError:
            # results will be empty unless the "twistedmail-imap-flags-anywhere"
            # patch from http://twistedmatrix.com/trac/ticket/1105 is applied
            self.log_error("Skipping empty results -- apply twisted patch!")
            self.fetchNextMessage()
            return

        d = self.factory.handleMessage(messageData)
        if isinstance(d, defer.Deferred):
            d.addCallback(self.cbFlagDeleted, messageList)
        else:
            # No deferred returned, so no need for addCallback( )
            self.cbFlagDeleted(None, messageList)

    def cbFlagDeleted(self, results, messageList):
        self.addFlags(messageList, ("\\Deleted",),
            uid=True).addCallback(self.cbMessageDeleted, messageList)

    def cbMessageDeleted(self, results, messageList):
        self.log_debug("IMAP in cbMessageDeleted [%s]" % (self.state,))
        self.log_debug("Deleted message")
        self.fetchNextMessage()

    def cbClosed(self, results):
        self.log_debug("IMAP in cbClosed [%s]" % (self.state,))
        self.log_debug("Mailbox closed")
        self.logout().addCallback(
            lambda _: self.transport.loseConnection())

    def rawDataReceived(self, data):
        self.log_debug("RAW RECEIVED: %s" % (data,))
        imap4.IMAP4Client.rawDataReceived(self, data)

    def lineReceived(self, line):
        self.log_debug("RECEIVED: %s" % (line,))
        imap4.IMAP4Client.lineReceived(self, line)

    def sendLine(self, line):
        self.log_debug("SENDING: %s" % (line,))
        imap4.IMAP4Client.sendLine(self, line)


class IMAP4DownloadFactory(protocol.ClientFactory, LoggingMixIn):
    protocol = IMAP4DownloadProtocol

    def __init__(self, settings, mailer, reactor=None):
        self.log_debug("Setting up IMAPFactory")

        self.settings = settings
        self.mailer = mailer
        if reactor is None:
            from twisted.internet import reactor
        self.reactor = reactor
        self.noisy = False

    def buildProtocol(self, addr):
        p = protocol.ClientFactory.buildProtocol(self, addr)
        p.registerAuthenticator(imap4.CramMD5ClientAuthenticator(self.settings["Username"]))
        p.registerAuthenticator(imap4.LOGINAuthenticator(self.settings["Username"]))
        p.registerAuthenticator(imap4.PLAINAuthenticator(self.settings["Username"]))
        return p

    def handleMessage(self, message):
        self.log_debug("IMAP factory handle message")
        self.log_debug(message)

        return self.mailer.inbound(message)


    def retry(self, connector=None):
        # TODO: if connector is None:

        if connector is None:
            if self.connector is None:
                self.log_error("No connector to retry")
                return
            else:
                connector = self.connector

        def reconnector():
            self.nextPoll = None
            connector.connect()

        self.log_debug("Scheduling next IMAP4 poll")
        self.nextPoll = self.reactor.callLater(self.settings["PollingSeconds"],
            reconnector)

    def clientConnectionLost(self, connector, reason):
        self.connector = connector
        self.log_debug("IMAP factory connection lost")
        self.retry(connector)

    def clientConnectionFailed(self, connector, reason):
        self.connector = connector
        self.log_error("IMAP factory connection failed")
        self.retry(connector)
