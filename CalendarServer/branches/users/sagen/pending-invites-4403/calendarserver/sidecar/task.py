##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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

__all__ = [
    "CalDAVService",
    "CalDAVOptions",
    "CalDAVServiceMaker",
]

import os
import socket
import stat
import sys
from time import sleep

from tempfile import mkstemp
from subprocess import Popen, PIPE
from pwd import getpwnam, getpwuid
from grp import getgrnam
from OpenSSL.SSL import Error as SSLError
import OpenSSL

from zope.interface import implements

from twisted.python.log import FileLogObserver
from twisted.python.usage import Options, UsageError
from twisted.python.reflect import namedClass
from twisted.plugin import IPlugin
from twisted.internet.defer import DeferredList, succeed, inlineCallbacks, returnValue
from twisted.internet.reactor import callLater
from twisted.internet.process import ProcessExitedAlready
from twisted.internet.protocol import Protocol, Factory
from twisted.internet.address import IPv4Address
from twisted.application.internet import TCPServer, SSLServer, UNIXServer
from twisted.application.service import Service, MultiService, IServiceMaker
from twisted.scripts.mktap import getid
from twisted.runner import procmon
from twisted.cred.portal import Portal
from twisted.web2.dav import auth
from twisted.web2.auth.basic import BasicCredentialFactory
from twisted.web2.server import Site
from twisted.web2.channel import HTTPFactory
from twisted.web2.static import File as FileResource
from twisted.web2.http import Request, RedirectResponse

from twext.internet.ssl import ChainingOpenSSLContextFactory
from twext.web2.channel.http import HTTP503LoggingFactory

from twistedcaldav.log import Logger, LoggingMixIn
from twistedcaldav.log import logLevelForNamespace, setLogLevelForNamespace
from twistedcaldav.accesslog import DirectoryLogWrapperResource
from twistedcaldav.accesslog import RotatingFileAccessLoggingObserver
from twistedcaldav.accesslog import AMPLoggingFactory
from twistedcaldav.accesslog import AMPCommonAccessLoggingObserver
from twistedcaldav.config import config, defaultConfig, defaultConfigFile
from twistedcaldav.config import ConfigurationError
from twistedcaldav.resource import CalDAVResource, AuthenticationWrapper
from twistedcaldav.directory.digest import QopDigestCredentialFactory
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twistedcaldav.directory.aggregate import AggregateDirectoryService
from twistedcaldav.directory.sudo import SudoDirectoryService
from twistedcaldav.directory.util import NotFilePath
from twistedcaldav.directory.wiki import WikiDirectoryService
from twistedcaldav.static import CalendarHomeProvisioningFile
from twistedcaldav.static import IScheduleInboxFile
from twistedcaldav.static import TimezoneServiceFile
from twistedcaldav.mail import IMIPReplyInboxResource
from twistedcaldav.timezones import TimezoneCache
from twistedcaldav.upgrade import upgradeData
from twistedcaldav.pdmonster import PDClientAddressWrapper
from twistedcaldav import memcachepool
from twistedcaldav.notify import installNotificationClient
from twistedcaldav.util import getNCPU
from twistedcaldav.localization import processLocalizationFiles

try:
    from twistedcaldav.authkerb import NegotiateCredentialFactory
except ImportError:
    NegotiateCredentialFactory = None

from calendarserver.provision.root import RootResource
from calendarserver.webadmin.resource import WebAdminResource
from calendarserver.webcal.resource import WebCalendarResource

log = Logger()


from twistedcaldav.scheduling.cuaddress import LocalCalendarUser
from twistedcaldav.scheduling.scheduler import DirectScheduler
from twistedcaldav.ical import Component
from twisted.web2.http_headers import Headers


class FakeRequest(object):

    def __init__(self, rootResource, method):
        self.rootResource = rootResource
        self.method = method
        self._resourcesByURL = {}
        self._urlsByResource = {}
        self.headers = Headers()


    @inlineCallbacks
    def _getChild(self, resource, segments):
        if not segments:
            returnValue(resource)

        child, remaining = (yield resource.locateChild(self, segments))
        returnValue((yield self._getChild(child, remaining)))

    @inlineCallbacks
    def locateResource(self, url):
        url = url.strip("/")
        segments = url.split("/")
        resource = (yield self._getChild(self.rootResource, segments))
        if resource:
            self._rememberResource(resource, url)
        returnValue(resource)

    def _rememberResource(self, resource, url):
        self._resourcesByURL[url] = resource
        self._urlsByResource[resource] = url
        return resource

    def urlForResource(self, resource):
        url = self._urlsByResource.get(resource, None)
        if url is None:
            raise NoURLForResourceError(resource)
        return url

    def addResponseFilter(*args, **kwds):
        pass

@inlineCallbacks
def processInbox(rootResource, directory, inboxFile, uuid):
    print "INSIDE PROCESS INBOX"
    print rootResource, directory, inboxFile, uuid

    principals = rootResource.getChild("principals")
    ownerPrincipal = principals.principalForUID(uuid)
    print "Owner principal", ownerPrincipal
    cua = "urn:uuid:%s" % (uuid,)
    owner = LocalCalendarUser(cua, ownerPrincipal,
        inboxFile, ownerPrincipal.scheduleInboxURL())
    print "Owner", owner

    for name in inboxFile.listChildren():
        icsFile = inboxFile.getChild(name)
        data = icsFile.iCalendarText()
        calendar = Component.fromString(data)
        try:
            method = calendar.propertyValue("METHOD")
        except ValueError:
            returnValue(None)

        if method == "REPLY":
            # originator is attendee sending reply
            originator = calendar.getAttendees()[0]
        else:
            # originator is the organizer
            originator = calendar.getOrganizer()

        originatorPrincipal = principals.principalForCalendarUserAddress(originator)
        originator = LocalCalendarUser(originator, originatorPrincipal)
        recipients = (owner,)
        scheduler = DirectScheduler(FakeRequest(rootResource, "PUT"), icsFile)
        result = (yield scheduler.doSchedulingViaPUT(originator, recipients,
            calendar, internal_request=False))

        if os.path.exists(icsFile.fp.path):
            os.remove(icsFile.fp.path)

class Task(object):

    def __init__(self, service, taskFile):
        self.service = service
        self.taskFile = taskFile

    @inlineCallbacks
    def run(self):
        log.info("Running task %s" % (self.taskFile))

        # Hardcoded task: process pending invites in all inboxes
        calendars = self.service.root.getChild("calendars")
        uidDir = calendars.getChild("__uids__")

        for first in os.listdir(uidDir.fp.path):
            if len(first) == 2:
                firstPath = os.path.join(uidDir.fp.path, first)
                for second in os.listdir(firstPath):
                    if len(second) == 2:
                        secondPath = os.path.join(firstPath, second)
                        for uuid in os.listdir(secondPath):
                            homeFile = uidDir.getChild(uuid)
                            inboxFile = homeFile.getChild("inbox")
                            if inboxFile:
                                yield processInbox(
                                    self.service.root,
                                    self.service.directory,
                                    inboxFile,
                                    uuid
                                )

        os.remove(os.path.join(self.service.processingDir, self.taskFile))

class CalDAVTaskService(Service):

    def __init__(self, root, directory):
        self.root = root
        self.directory = directory
        self.seconds = 5
        self.taskDir = os.path.join(config.DataRoot, "tasks")
        # New task files are placed into "incoming"
        self.incomingDir = os.path.join(self.taskDir, "incoming")
        # Task files get moved into "processing" and then removed when complete
        self.processingDir = os.path.join(self.taskDir, "processing")

    def startService(self):
        log.info("Starting task service")

        if not os.path.exists(self.taskDir):
            os.mkdir(self.taskDir)
        if not os.path.exists(self.incomingDir):
            os.mkdir(self.incomingDir)
        if not os.path.exists(self.processingDir):
            os.mkdir(self.processingDir)

        callLater(self.seconds, self.periodic, first=True)


    def periodic(self, first=False):
        deferreds = []

        try:
            log.info("PERIODIC, first=%s" % (first,))

            if first:
                # check the processing directory to see if there are any tasks
                # that didn't complete during the last server run; start those
                for child in os.listdir(self.processingDir):
                    deferreds.append(Task(self, child).run())

            for child in os.listdir(self.incomingDir):
                os.rename(os.path.join(self.incomingDir, child),
                    os.path.join(self.processingDir, child))
                deferreds.append(Task(self, child).run())

        finally:
            callLater(self.seconds, self.periodic)

        return DeferredList(deferreds)


class CalDAVTaskOptions(Options):
    optParameters = [[
        "config", "f", defaultConfigFile, "Path to configuration file."
    ]]

    def __init__(self, *args, **kwargs):
        super(CalDAVTaskOptions, self).__init__(*args, **kwargs)

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
        config.loadConfig(self['config'])
        config.updateDefaults(self.overrides)
        self.parent['pidfile'] = None


class CalDAVTaskServiceMaker (LoggingMixIn):
    implements(IPlugin, IServiceMaker)

    tapname = "caldav_task"
    description = "Calendar Server Task Process"
    options = CalDAVTaskOptions

    #
    # Default resource classes
    #
    rootResourceClass            = RootResource
    principalResourceClass       = DirectoryPrincipalProvisioningResource
    calendarResourceClass        = CalendarHomeProvisioningFile
    iScheduleResourceClass       = IScheduleInboxFile
    imipResourceClass            = IMIPReplyInboxResource
    timezoneServiceResourceClass = TimezoneServiceFile
    webCalendarResourceClass     = WebCalendarResource
    webAdminResourceClass        = WebAdminResource

    def makeService(self, options):

        print "CalDAVTaskServiceMaker -- makeService"
        #
        # Change default log level to "info" as its useful to have
        # that during startup
        #
        oldLogLevel = logLevelForNamespace(None)
        setLogLevelForNamespace(None, "info")

        #
        # Setup the Directory
        #
        directories = []

        directoryClass = namedClass(config.DirectoryService.type)

        self.log_info("Configuring directory service of type: %s"
                      % (config.DirectoryService.type,))

        directory = directoryClass(config.DirectoryService.params)

        # Wait for the directory to become available
        while not directory.isAvailable():
            sleep(5)

        #
        # Configure Memcached Client Pool
        #
        if config.Memcached.ClientEnabled:
            memcachepool.installPool(
                IPv4Address(
                    "TCP",
                    config.Memcached.BindAddress,
                    config.Memcached.Port,
                ),
                config.Memcached.MaxClients,
            )

        #
        # Configure NotificationClient
        #
        if config.Notifications.Enabled:
            installNotificationClient(
                config.Notifications.InternalNotificationHost,
                config.Notifications.InternalNotificationPort,
            )

        #
        # Setup Resource hierarchy
        #
        self.log_info("Setting up document root at: %s"
                      % (config.DocumentRoot,))
        self.log_info("Setting up principal collection: %r"
                      % (self.principalResourceClass,))

        principalCollection = self.principalResourceClass(
            "/principals/",
            directory,
        )

        self.log_info("Setting up calendar collection: %r"
                      % (self.calendarResourceClass,))

        calendarCollection = self.calendarResourceClass(
            os.path.join(config.DocumentRoot, "calendars"),
            directory, "/calendars/",
        )

        self.log_info("Setting up root resource: %r"
                      % (self.rootResourceClass,))

        root = self.rootResourceClass(
            config.DocumentRoot,
            principalCollections=(principalCollection,),
        )

        root.putChild("principals", principalCollection)
        root.putChild("calendars", calendarCollection)

        service = CalDAVTaskService(root, directory)

        # Change log level back to what it was before
        setLogLevelForNamespace(None, oldLogLevel)

        return service
