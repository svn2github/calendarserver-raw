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

from __future__ import with_statement

__all__ = [
    "CalDAVTaskService",
    "CalDAVTaskServiceMaker",
    "CalDAVTaskOptions",
    "Task",
]

from time import sleep
import os

from zope.interface import implements

from twisted.application.service import Service, IServiceMaker
from twisted.internet.defer import DeferredList, inlineCallbacks, returnValue
from twisted.internet.reactor import callLater
from twisted.plugin import IPlugin
from twisted.python.reflect import namedClass
from twisted.python.usage import Options, UsageError
from twisted.web2.http_headers import Headers

from twext.log import Logger, LoggingMixIn
from twext.log import logLevelForNamespace, setLogLevelForNamespace

from twistedcaldav import memcachepool
from twistedcaldav.config import config
from twistedcaldav.stdconfig import DEFAULT_CONFIG, DEFAULT_CONFIG_FILE
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twistedcaldav.ical import Component
from twistedcaldav.notify import installNotificationClient
from twistedcaldav.scheduling.cuaddress import LocalCalendarUser
from twistedcaldav.scheduling.scheduler import DirectScheduler
from twistedcaldav.static import CalendarHomeProvisioningFile

from calendarserver.provision.root import RootResource

log = Logger()

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
            class NoURLForResourceError(RuntimeError):
                pass
            raise NoURLForResourceError(resource)
        return url

    def addResponseFilter(*args, **kwds):
        pass

@inlineCallbacks
def processInboxItem(rootResource, directory, inboxFile, inboxItemFile, uuid):
    log.debug("Processing inbox item %s" % (inboxItemFile,))

    principals = rootResource.getChild("principals")
    ownerPrincipal = principals.principalForUID(uuid)
    cua = "urn:uuid:%s" % (uuid,)
    owner = LocalCalendarUser(cua, ownerPrincipal,
        inboxFile, ownerPrincipal.scheduleInboxURL())

    data = inboxItemFile.iCalendarText()
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
    scheduler = DirectScheduler(FakeRequest(rootResource, "PUT"), inboxItemFile)
    yield scheduler.doSchedulingViaPUT(originator, recipients, calendar, internal_request=False)

    if os.path.exists(inboxItemFile.fp.path):
        os.remove(inboxItemFile.fp.path)



class Task(object):

    def __init__(self, service, fileName):
        self.service = service
        self.taskName = fileName.split(".")[0]
        self.taskFile = os.path.join(self.service.processingDir, fileName)

    @inlineCallbacks
    def run(self):
        methodName = "task_%s" % (self.taskName,)
        method = getattr(self, methodName, None)
        if method:
            try:
                log.warn("Running task '%s'" % (self.taskName))
                yield method()
                log.warn("Completed task '%s'" % (self.taskName))
            except Exception, e:
                log.error("Failed task '%s' (%s)" % (self.taskName, e))
                os.remove(self.taskFile)
                raise
        else:
            log.error("Unknown task requested: '%s'" % (self.taskName))
            os.remove(self.taskFile)
            returnValue(None)

    @inlineCallbacks
    def task_scheduleinboxes(self):

        calendars = self.service.root.getChild("calendars")
        uidDir = calendars.getChild("__uids__")

        inboxItems = set()
        with open(self.taskFile) as input:
            for inboxItem in input:
                inboxItem = inboxItem.strip()
                inboxItems.add(inboxItem)

        for inboxItem in list(inboxItems):
            log.info("Processing inbox item: %s" % (inboxItem,))
            ignore, uuid, ignore, fileName = inboxItem.rsplit("/", 3)

            homeFile = uidDir.getChild(uuid)
            if not homeFile:
                continue

            inboxFile = homeFile.getChild("inbox")
            if not inboxFile:
                continue

            inboxItemFile = inboxFile.getChild(fileName)

            yield processInboxItem(
                self.service.root,
                self.service.directory,
                inboxFile,
                inboxItemFile,
                uuid
            )
            inboxItems.remove(inboxItem)

            # Rewrite the task file in case we exit before we're done
            with open(self.taskFile + ".tmp", "w") as output:
                for inboxItem in inboxItems:
                    output.write("%s\n" % (inboxItem,))
            os.rename(self.taskFile + ".tmp", self.taskFile)

        os.remove(self.taskFile)



class CalDAVTaskService(Service):

    def __init__(self, root, directory):
        self.root = root
        self.directory = directory
        self.seconds = 30 # How often to check for new tasks in incomingDir
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
        log.debug("Checking for tasks")

        deferreds = []

        try:
            if first:
                # check the processing directory to see if there are any tasks
                # that didn't complete during the last server run; start those
                for fileName in os.listdir(self.processingDir):
                    if fileName.endswith(".task"):
                        log.debug("Restarting old task: %s" % (fileName,))
                        deferreds.append(Task(self, fileName).run())

            for fileName in os.listdir(self.incomingDir):
                if fileName.endswith(".task"):
                    log.debug("Found new task: %s" % (fileName,))
                    os.rename(os.path.join(self.incomingDir, fileName),
                        os.path.join(self.processingDir, fileName))
                    deferreds.append(Task(self, fileName).run())

        finally:
            callLater(self.seconds, self.periodic)

        return DeferredList(deferreds)



class CalDAVTaskOptions(Options):
    optParameters = [[
        "config", "f", DEFAULT_CONFIG_FILE, "Path to configuration file."
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

    def makeService(self, options):

        #
        # The task sidecar doesn't care about system SACLs
        #
        config.EnableSACLs = False

        #
        # Change default log level to "info" as its useful to have
        # that during startup
        #
        oldLogLevel = logLevelForNamespace(None)
        setLogLevelForNamespace(None, "info")

        #
        # Setup the Directory
        #
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
        memcachepool.installPools(
            config.Memcached.Pools,
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
