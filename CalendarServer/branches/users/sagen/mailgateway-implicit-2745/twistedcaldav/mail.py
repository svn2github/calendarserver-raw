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
from twisted.protocols import basic
from twisted.mail import pop3client, imap4
from twisted.plugin import IPlugin
from twisted.application import internet, service
from twisted.python.usage import Options, UsageError
from twisted.python.reflect import namedClass
from twistedcaldav.log import LoggingMixIn
from twistedcaldav.config import config, parseConfig, defaultConfig
from zope.interface import Interface, implements
import email

__all__ = [
    "MailGatewayServiceMaker",
]


# TODO: generate reply-to headers for authenticating responses
# TODO: hand off authenticated responses to calendarserver
# TODO: use @inlineCallbacks

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


class MailGatewayServiceMaker(object):
    implements(IPlugin, service.IServiceMaker)

    tapname = "caldav_mailgateway"
    description = "Mail Gateway"
    options = MailGatewayOptions

    def makeService(self, options):

        multiService = service.MultiService()

        for settings in config.MailGateway["Services"]:
            if settings["Enabled"]:
                client = namedClass(settings["Service"])(settings)
                client.setServiceParent(multiService)

        return multiService


#
# POP3
#

class POP3Service(service.Service, LoggingMixIn):

    def __init__(self, settings):
        if settings["UseSSL"]:
            self.client = internet.SSLClient(settings["Host"], settings["Port"],
                POP3DownloadFactory(settings), ssl.ClientContextFactory())
        else:
            self.client = internet.TCPClient(settings["Host"], settings["Port"],
                POP3DownloadFactory(settings))

    def startService(self):
        self.client.startService()

    def stopService(self):
        self.client.stopService()


class POP3DownloadProtocol(pop3client.POP3Client, LoggingMixIn):
    allowInsecureLogin = False

    def serverGreeting(self, greeting):
        self.log_info("POP servergreeting")
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
        self.log_info("POP loggedin")
        return self.listSize().addCallback(self.cbGotMessageSizes)

    def cbGotMessageSizes(self, sizes):
        self.log_info("POP gotmessagesizes")
        downloads = []
        for i in range(len(sizes)):
            downloads.append(self.retrieve(i).addCallback(self.cbDownloaded, i))
        return defer.DeferredList(downloads).addCallback(self.cbFinished)

    def cbDownloaded(self, lines, id):
        self.log_info("POP downloaded message %d" % (id,))
        self.factory.handleMessage("\r\n".join(lines))
        self.log_info("POP deleting message %d" % (id,))
        self.delete(id)

    def cbFinished(self, results):
        self.log_info("POP finished")
        return self.quit()


class POP3DownloadFactory(protocol.ClientFactory, LoggingMixIn):
    protocol = POP3DownloadProtocol

    def __init__(self, settings, reactor=None):
        self.settings = settings
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

        self.log_info("Scheduling next POP3 poll")
        self.nextPoll = self.reactor.callLater(self.settings["PollingSeconds"],
            reconnector)

    def clientConnectionLost(self, connector, reason):
        self.connector = connector
        self.log_info("POP factory connection lost")
        self.retry(connector)


    def clientConnectionFailed(self, connector, reason):
        self.connector = connector
        self.log_info("POP factory connection failed")
        self.retry(connector)

    def handleMessage(self, message):
        self.log_info("POP factory handle message")
        self.log_info(message)
        parsedMessage = email.message_from_string(message)
        # TODO: messages can be handed off here...


#
# IMAP4
#

class IMAP4Service(service.Service):

    def __init__(self, settings):

        if settings["UseSSL"]:
            self.client = internet.SSLClient(settings["Host"], settings["Port"],
                IMAP4DownloadFactory(settings), ssl.ClientContextFactory())
        else:
            self.client = internet.TCPClient(settings["Host"], settings["Port"],
                IMAP4DownloadFactory(settings))


    def startService(self):
        self.client.startService()

    def stopService(self):
        self.client.stopService()


class IMAP4DownloadProtocol(imap4.IMAP4Client, LoggingMixIn):

    def serverGreeting(self, capabilities):
        self.log_info("IMAP servergreeting")
        return self.login(self.factory.settings["Username"],
            self.factory.settings["Password"]).addCallback(self.cbLoggedIn)

    def ebLogError(self, error):
        self.log_error("IMAP Error: %s" % (error,))

    def cbLoginFailed(self, reason):
        self.log_error("IMAP login failed for %s" %
            (self.factory.settings["Username"],))
        return self.transport.loseConnection()

    def cbLoggedIn(self, result):
        self.log_info("IMAP logged in [%s]" % (self.state,))
        return self.select("Inbox").addCallback(self.cbInboxSelected)

    def cbInboxSelected(self, result):
        self.log_info("IMAP Inbox selected [%s]" % (self.state,))
        allMessages = imap4.MessageSet(1, None)
        return self.fetchUID(allMessages, True).addCallback(self.cbGotUIDs)

    def cbGotUIDs(self, results):
        self.log_info("IMAP got uids [%s]" % (self.state,))
        self.messageUIDs = [result['UID'] for result in results.values()]
        self.messageCount = len(self.messageUIDs)
        self.log_info("IMAP Inbox has %d messages" % (self.messageCount,))
        return self.fetchNextMessage()

    def fetchNextMessage(self):
        self.log_info("IMAP in fetchnextmessage [%s]" % (self.state,))
        if self.messageUIDs:
            nextUID = self.messageUIDs.pop(0)
            messageListToFetch = imap4.MessageSet(nextUID)
            self.log_info("Downloading message %d of %d (%s)" %
                (self.messageCount - len(self.messageUIDs), self.messageCount,
                nextUID))
            return self.fetchMessage(messageListToFetch, True).addCallback(
                self.cbGotMessage, messageListToFetch).addErrback(self.ebLogError)
        else:
            self.log_info("All messages downloaded")
            return self.close().addCallback(self.cbClosed)

    def cbGotMessage(self, results, messageList):
        self.log_info("IMAP in cbGotMessage [%s]" % (self.state,))
        try:
            messageData = results.values()[0]['RFC822']
        except IndexError:
            # not sure what happened, but results is empty
            self.log_info("Skipping empty results")
            return self.fetchNextMessage()

        self.factory.handleMessage(messageData)
        return self.addFlags(messageList, ("\\Deleted",),
            uid=True).addCallback(self.cbMessageDeleted, messageList)

    def cbMessageDeleted(self, results, messageList):
        self.log_info("IMAP in cbMessageDeleted [%s]" % (self.state,))
        self.log_info("Deleted message")
        self.fetchNextMessage()

    def cbClosed(self, results):
        self.log_info("IMAP in cbClosed [%s]" % (self.state,))
        self.log_info("Mailbox closed")
        return self.logout().addCallback(
            lambda _: self.transport.loseConnection())

    def lineReceived(self, line):
        self.log_info("RECEIVED: %s" % (line,))
        imap4.IMAP4Client.lineReceived(self, line)

    def sendLine(self, line):
        self.log_info("SENDING: %s" % (line,))
        imap4.IMAP4Client.sendLine(self, line)


class IMAP4DownloadFactory(protocol.ClientFactory, LoggingMixIn):
    protocol = IMAP4DownloadProtocol

    def __init__(self, settings, reactor=None):
        self.log_info("Setting up IMAPFactory")

        self.settings = settings
        if reactor is None:
            from twisted.internet import reactor
        self.reactor = reactor


    def handleMessage(self, message):
        self.log_info("IMAP factory handle message")
        self.log_info(message)
        parsedMessage = email.message_from_string(message)
        # TODO: messages can be handed off here...


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

        self.log_info("Scheduling next IMAP4 poll")
        self.nextPoll = self.reactor.callLater(self.settings["PollingSeconds"],
            reconnector)

    def clientConnectionLost(self, connector, reason):
        self.connector = connector
        self.log_info("IMAP factory connection lost")
        self.retry(connector)

    def clientConnectionFailed(self, connector, reason):
        self.connector = connector
        self.log_info("IMAP factory connection failed")
        self.retry(connector)
