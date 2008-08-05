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
Mail Gateway for Calendar Server

"""

from twisted.internet import protocol, defer, ssl
from twisted.web import resource, static, server, client
from twisted.internet.defer import fail, succeed, inlineCallbacks, returnValue
from twisted.protocols import basic
from twisted.mail import pop3client, imap4
from twisted.plugin import IPlugin
from twisted.application import internet, service
from twisted.python.usage import Options, UsageError
from twisted.python.reflect import namedClass
from twisted.mail.smtp import messageid, rfc822date, sendmail
from twistedcaldav.log import LoggingMixIn
from twistedcaldav import ical
from twistedcaldav.resource import CalDAVResource
from twistedcaldav.scheduling.scheduler import IMIPScheduler
from twistedcaldav.config import config, parseConfig, defaultConfig
from twistedcaldav.sql import AbstractSQLDatabase
from zope.interface import Interface, implements
import email, email.utils
import uuid
import os
import cStringIO
import datetime
import base64
import MimeWriter

__all__ = [
    "IMIPInboxResource",
    "MailGatewayServiceMaker",
]


#
# Mail gateway service config
#

class MailGatewayOptions(Options):
    optParameters = [[
        "config", "f", "/etc/caldavd/caldavd.plist", "Path to configuration file."
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
                defaultConfig,
                path.split('/'),
                value,
                self.overrides
            )
        else:
            self.opt_option('%s=True' % (option,))

    opt_o = opt_option

    def postOptions(self):
        parseConfig(self['config'])
        config.updateDefaults(self.overrides)
        self.parent['pidfile'] = None



class IMIPInboxResource(CalDAVResource):
    """
    IMIP-delivery Inbox resource.

    Extends L{DAVResource} to provide IMIP delivery functionality.
    """

    def __init__(self, parent):
        """
        @param parent: the parent resource of this one.
        """
        assert parent is not None

        CalDAVResource.__init__(self, principalCollections=parent.principalCollections())

        self.parent = parent

    def defaultAccessControlList(self):
        return davxml.ACL(
            # DAV:Read, CalDAV:schedule for all principals (includes anonymous)
            davxml.ACE(
                davxml.Principal(davxml.All()),
                davxml.Grant(
                    davxml.Privilege(davxml.Read()),
                    davxml.Privilege(caldavxml.Schedule()),
                ),
                davxml.Protected(),
            ),
        )

    def resourceType(self):
        return davxml.ResourceType.ischeduleinbox

    def isCollection(self):
        return False

    def isCalendarCollection(self):
        return False

    def isPseudoCalendarCollection(self):
        return False

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

    @inlineCallbacks
    def http_POST(self, request):
        """
        The IMIP delivery POST method.
        """

        # Check authentication and access controls
        # yield self.authorize(request, (caldavxml.Schedule(),))

        # Inject using the IMIPScheduler.
        scheduler = IMIPScheduler(request, self)

        # Do the POST processing treating this as a non-local schedule
        response = (yield scheduler.doSchedulingViaPOST())
        returnValue(response)



def injectMessage(organizer, attendee, calendar, reactor=None):

    if reactor is None:
        from twisted.internet import reactor

    headers = {
        'Content-Type' : 'text/calendar',
        'Originator' : attendee,
        'Recipient' : organizer,
    }

    data = str(calendar)

    if config.SSLPort:
        useSSL = True
        port = config.SSLPort
    else:
        useSSL = False
        port = config.HTTPPort

    host = config.ServerHostName
    path = "email-inbox"
    scheme = "https:" if useSSL else "http:"
    url = "%s//%s:%d/%s/" % (scheme, host, port, path)

    factory = client.HTTPClientFactory(url, method='POST', headers=headers,
        postdata=data)
    if useSSL:
        reactor.connectSSL(host, port, factory, ssl.ClientContextFactory())
    else:
        reactor.connectTCP(host, port, factory)
    return factory.deferred




class MailGatewayTokensDatabase(AbstractSQLDatabase):
    """
    A database to maintain "plus-address" tokens for IMIP requests.

    SCHEMA:

    Token Database:

    ROW: TOKEN, ORGANIZER, ATTENDEE

    """

    dbType = "MAILGATEWAYTOKENS"
    dbFilename = "mailgatewaytokens.sqlite"
    dbFormatVersion = "1"


    def __init__(self, path):
        path = os.path.join(path, MailGatewayTokensDatabase.dbFilename)
        super(MailGatewayTokensDatabase, self).__init__(path, True)

    def createToken(self, organizer, attendee):
        token = str(uuid.uuid4())
        self._db_execute(
            """
            insert into TOKENS (TOKEN, ORGANIZER, ATTENDEE)
            values (:1, :2, :3)
            """, token, organizer, attendee
        )
        self._db_commit()
        return token

    def lookupByToken(self, token):
        results = list(
            self._db_execute(
                """
                select ORGANIZER, ATTENDEE from TOKENS
                where TOKEN = :1
                """, token
            )
        )

        if len(results) != 1:
            return None

        return results[0]

    def getToken(self, organizer, attendee):
        token = self._db_value_for_sql(
            """
            select TOKEN from TOKENS
            where ORGANIZER = :1 and ATTENDEE = :2
            """, organizer, attendee
        )
        return token

    def deleteToken(self, token):
        self._db_execute(
            """
            delete from TOKENS where TOKEN = :1
            """, token
        )

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
                ATTENDEE    text
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

class MailGatewayServiceMaker(LoggingMixIn):
    implements(IPlugin, service.IServiceMaker)

    tapname = "caldav_mailgateway"
    description = "Mail Gateway"
    options = MailGatewayOptions

    def makeService(self, options):

        multiService = service.MultiService()

        settings = config.Scheduling['iMIP']
        if settings['Enabled']:
            mailer = MailHandler()

            mailType = settings['Receiving']['Type']
            if mailType.lower().startswith('pop'):
                client = POP3Service(settings['Receiving'], mailer)
            elif mailType.lower().startswith('imap'):
                client = IMAP4Service(settings['Receiving'], mailer)
            else:
                # TODO: raise error?
                self.log_error("Invalid iMIP type in configuration: %s" %
                    (mailType,))
                return multiService

            client.setServiceParent(multiService)

            IScheduleService(settings, mailer).setServiceParent(multiService)

        return multiService


#
# ISchedule Inbox
#
class IScheduleService(service.Service, LoggingMixIn):

    def __init__(self, settings, mailer):
        self.settings = settings
        self.mailer = mailer
        root = resource.Resource()
        root.putChild('', self.HomePage())
        root.putChild('inbox', self.IScheduleInbox(mailer))
        self.site = server.Site(root)
        self.server = internet.TCPServer(settings['MailGatewayPort'], self.site)

    def startService(self):
        self.server.startService()

    def stopService(self):
        self.server.stopService()


    class HomePage(resource.Resource):
        def render(self, request):
            return """
            <html>
            <head><title>ISchedule - IMIP Gateway</title></head>
            <body>ISchedule - IMIP Gateway</body>
            </html>
            """

    class IScheduleInbox(resource.Resource):

        def __init__(self, mailer):
            resource.Resource.__init__(self)
            self.mailer = mailer

        def render_GET(self, request):
            return """
            <html>
            <head><title>ISchedule Inbox</title></head>
            <body>ISchedule Inbox</body>
            </html>
            """

        def render_POST(self, request):
            # Compute token, add to db, generate email and send it
            calendar = ical.Component.fromString(request.content.read())
            headers = request.getAllHeaders()
            self.mailer.outbound(headers['originator'], headers['recipient'],
                calendar)

            # TODO: what to return?
            return """
            <html>
            <head><title>ISchedule Inbox</title></head>
            <body>ISchedule Inbox</body>
            </html>
            """

class MailHandler(LoggingMixIn):

    def __init__(self):
        self.db = MailGatewayTokensDatabase(config.DataRoot)

    @inlineCallbacks
    def inbound(self, message):
        parsedMessage = email.message_from_string(message)

        # TODO: make sure this is an email message we want to handle

        # extract the token from the To header
        name, addr = email.utils.parseaddr(parsedMessage['To'])
        if addr:
            # addr looks like: server_address+token@example.com
            try:
                pre, post = addr.split('@')
                pre, token = pre.split('+')
            except ValueError:
                # TODO: handle this error
                return
        else:
            # TODO: handle this error
            return

        for part in parsedMessage.walk():
            if part.get_content_type() == "text/calendar":
                calBody = part.get_payload(decode=True)
                break
        else:
            # TODO: handle this condition
            # No icalendear attachment
            return

        self.log_debug(calBody)
        calendar = ical.Component.fromString(calBody)

        # process mail messages from POP or IMAP, inject to calendar server
        result = self.db.lookupByToken(token)
        if result is None:
            # This isn't a token we recognize
            self.error("Received a token I don't recognize: %s" % (token,))
            return
        organizer, attendee = result
        organizer = str(organizer)
        attendee = str(attendee)
        calendar.getOrganizerProperty().setValue(organizer)
        yield injectMessage(organizer, attendee, calendar)


    @inlineCallbacks
    def outbound(self, organizer, attendee, calendar):
        # create token, send email
        token = self.db.getToken(organizer, attendee)
        if token is None:
            token = self.db.createToken(organizer, attendee)

        settings = config.Scheduling['iMIP']['Sending']
        fullServerAddress = settings['Address']
        name, serverAddress = email.utils.parseaddr(fullServerAddress)
        pre, post = serverAddress.split('@')
        addressWithToken = "%s+%s@%s" % (pre, token, post)
        calendar.getOrganizerProperty().setValue("mailto:%s" %
            (addressWithToken,))

        message = self._generateTemplateMessage(calendar)

        # The email's From: will be the calendar server's address (without
        # + addressing), while the Reply-To: will be the organizer's email
        # address.
        if not organizer.startswith("mailto:"):
            raise ValueError("ORGANIZER address '%s' must be mailto: for iMIP operation." % (organizer,))
        organizer = organizer[7:]
        fromAddr = serverAddress
        toAddr = attendee
        message = message.replace("${fromaddress}", fromAddr)
        message = message.replace("${replytoaddress}", organizer)

        if not attendee.startswith("mailto:"):
            raise ValueError("ATTENDEE address '%s' must be mailto: for iMIP operation." % (attendee,))
        attendee = attendee[7:]
        message = message.replace("${toaddress}", attendee)
        yield sendmail(settings['Server'], fromAddr, toAddr, message,
            port=settings['Port'])


    def _generateTemplateMessage(self, calendar):

        caldata = str(calendar)
        data = cStringIO.StringIO()
        writer = MimeWriter.MimeWriter(data)

        writer.addheader("From", "${fromaddress}")
        writer.addheader("Reply-To", "${replytoaddress}")
        writer.addheader("To", "${toaddress}")
        writer.addheader("Date", rfc822date())
        writer.addheader("Subject", "DO NOT REPLY: calendar invitation test")
        writer.addheader("Message-ID", messageid())
        writer.addheader("Mime-Version", "1.0")
        writer.flushheaders()

        writer.startmultipartbody("mixed")

        # message body
        part = writer.nextpart()
        body = part.startbody("text/plain")
        body.write("Hi, You've been invited to a cool event by CalendarServer's new iMIP processor.  %s " % (self._generateCalendarSummary(calendar),))

        part = writer.nextpart()
        encoding = "7bit"
        for i in caldata:
            if ord(i) > 127:
                encoding = "base64"
                caldata = base64.encodestring(caldata)
                break
        part.addheader("Content-Transfer-Encoding", encoding)
        body = part.startbody("text/calendar; charset=utf-8")
        body.write(caldata.replace("\r", ""))

        # finish
        writer.lastpart()

        return data.getvalue()

    def _generateCalendarSummary(self, calendar):

        # Get the most appropriate component
        component = calendar.masterComponent()
        if component is None:
            component = calendar.mainComponent(True)

        organizer = component.getOrganizerProperty()
        if "CN" in organizer.params():
            organizer = "%s <%s>" % (organizer.params()["CN"][0], organizer.value(),)
        else:
            organizer = organizer.value()

        dtinfo = self._getDateTimeInfo(component)

        summary = component.propertyValue("SUMMARY")
        if summary is None:
            summary = ""

        description = component.propertyValue("DESCRIPTION")
        if description is None:
            description = ""

        return """---- Begin Calendar Event Summary ----

Organizer:   %s
Summary:     %s
%sDescription: %s

----  End Calendar Event Summary  ----
""" % (organizer, summary, dtinfo, description,)

    def _getDateTimeInfo(self, component):

        dtstart = component.propertyNativeValue("DTSTART")
        tzid_start = component.getProperty("DTSTART").params().get("TZID", "UTC")

        dtend = component.propertyNativeValue("DTEND")
        if dtend:
            tzid_end = component.getProperty("DTEND").params().get("TZID", "UTC")
            duration = dtend - dtstart
        else:
            duration = component.propertyNativeValue("DURATION")
            if duration:
                dtend = dtstart + duration
                tzid_end = tzid_start
            else:
                if isinstance(dtstart, datetime.date):
                    dtend = None
                    duration = datetime.timedelta(days=1)
                else:
                    dtend = dtstart + datetime.timedelta(days=1)
                    dtend.hour = dtend.minute = dtend.second = 0
                    duration = dtend - dtstart
        result = "Starts:      %s\n" % (self._getDateTimeText(dtstart, tzid_start),)
        if dtend is not None:
            result += "Ends:        %s\n" % (self._getDateTimeText(dtend, tzid_end),)
        result += "Duration:    %s\n" % (self._getDurationText(duration),)

        if not isinstance(dtstart, datetime.datetime):
            result += "All Day\n"

        for property_name in ("RRULE", "RDATE", "EXRULE", "EXDATE", "RECURRENCE-ID",):
            if component.hasProperty(property_name):
                result += "Recurring\n"
                break

        return result

    def _getDateTimeText(self, dtvalue, tzid):

        if isinstance(dtvalue, datetime.datetime):
            timeformat = "%A, %B %e, %Y %I:%M %p"
        elif isinstance(dtvalue, datetime.date):
            timeformat = "%A, %B %e, %Y"
            tzid = ""
        if tzid:
            tzid = " (%s)" % (tzid,)

        return "%s%s" % (dtvalue.strftime(timeformat), tzid,)

    def _getDurationText(self, duration):

        result = ""
        if duration.days > 0:
            result += "%d %s" % (
                duration.days,
                self._pluralize(duration.days, "day", "days")
            )

        hours = duration.seconds / 3600
        minutes = divmod(duration.seconds / 60, 60)[1]
        seconds = divmod(duration.seconds, 60)[1]

        if hours > 0:
            if result:
                result += ", "
            result += "%d %s" % (
                hours,
                self._pluralize(hours, "hour", "hours")
            )

        if minutes > 0:
            if result:
                result += ", "
            result += "%d %s" % (
                minutes,
                self._pluralize(minutes, "minute", "minutes")
            )

        if seconds > 0:
            if result:
                result += ", "
            result += "%d %s" % (
                seconds,
                self._pluralize(seconds, "second", "seconds")
            )

        return result

    def _pluralize(self, number, unit1, unitS):
        return unit1 if number == 1 else unitS






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

    @inlineCallbacks
    def handleMessage(self, message):
        self.log_debug("POP factory handle message")
        self.log_debug(message)

        yield self.mailer.inbound(message)




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
        return self.login(self.factory.settings["Username"],
            self.factory.settings["Password"]).addCallback(self.cbLoggedIn)

    def ebLogError(self, error):
        self.log_error("IMAP Error: %s" % (error,))

    def cbLoginFailed(self, reason):
        self.log_error("IMAP login failed for %s" %
            (self.factory.settings["Username"],))
        return self.transport.loseConnection()

    def cbLoggedIn(self, result):
        self.log_debug("IMAP logged in [%s]" % (self.state,))
        return self.select("Inbox").addCallback(self.cbInboxSelected)

    def cbInboxSelected(self, result):
        self.log_debug("IMAP Inbox selected [%s]" % (self.state,))
        allMessages = imap4.MessageSet(1, None)
        return self.fetchUID(allMessages, True).addCallback(self.cbGotUIDs)

    def cbGotUIDs(self, results):
        self.log_debug("IMAP got uids [%s]" % (self.state,))
        self.messageUIDs = [result['UID'] for result in results.values()]
        self.messageCount = len(self.messageUIDs)
        self.log_debug("IMAP Inbox has %d messages" % (self.messageCount,))
        return self.fetchNextMessage()

    def fetchNextMessage(self):
        self.log_debug("IMAP in fetchnextmessage [%s]" % (self.state,))
        if self.messageUIDs:
            nextUID = self.messageUIDs.pop(0)
            messageListToFetch = imap4.MessageSet(nextUID)
            self.log_debug("Downloading message %d of %d (%s)" %
                (self.messageCount - len(self.messageUIDs), self.messageCount,
                nextUID))
            return self.fetchMessage(messageListToFetch, True).addCallback(
                self.cbGotMessage, messageListToFetch).addErrback(self.ebLogError)
        else:
            self.log_debug("All messages downloaded")
            return self.close().addCallback(self.cbClosed)

    def cbGotMessage(self, results, messageList):
        self.log_debug("IMAP in cbGotMessage [%s]" % (self.state,))
        try:
            messageData = results.values()[0]['RFC822']
        except IndexError:
            # not sure what happened, but results is empty
            self.log_error("Skipping empty results")
            return self.fetchNextMessage()

        self.factory.handleMessage(messageData)
        return self.addFlags(messageList, ("\\Deleted",),
            uid=True).addCallback(self.cbMessageDeleted, messageList)

    def cbMessageDeleted(self, results, messageList):
        self.log_debug("IMAP in cbMessageDeleted [%s]" % (self.state,))
        self.log_debug("Deleted message")
        self.fetchNextMessage()

    def cbClosed(self, results):
        self.log_debug("IMAP in cbClosed [%s]" % (self.state,))
        self.log_debug("Mailbox closed")
        return self.logout().addCallback(
            lambda _: self.transport.loseConnection())

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


    def handleMessage(self, message):
        self.log_debug("IMAP factory handle message")
        self.log_debug(message)

        yield self.mailer.inbound(message)


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
