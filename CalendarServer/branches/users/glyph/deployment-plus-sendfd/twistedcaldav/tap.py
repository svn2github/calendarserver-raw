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

import os
import stat
import socket
import signal

from zope.interface import implements

from twisted.internet.address import IPv4Address
from twisted.internet import tcp, ssl

from twisted.python.log import FileLogObserver
from twisted.python.usage import Options, UsageError
from twisted.python.reflect import namedClass

from twisted.application import internet, service
from twisted.plugin import IPlugin

from twisted.scripts.mktap import getid

from twisted.cred.portal import Portal

from twisted.web2.dav import auth
from twisted.web2.auth.basic import BasicCredentialFactory


from twistedcaldav.log import Logger, logLevelForNamespace, setLogLevelForNamespace
from twistedcaldav.accesslog import DirectoryLogWrapperResource
from twistedcaldav.accesslog import RotatingFileAccessLoggingObserver
from twistedcaldav.accesslog import AMPCommonAccessLoggingObserver
from twistedcaldav.cluster import makeService_Combined, makeService_Master
from twistedcaldav.config import config, parseConfig, defaultConfig, ConfigurationError
from twistedcaldav.root import RootResource
from twistedcaldav.resource import CalDAVResource
from twistedcaldav.directory.digest import QopDigestCredentialFactory
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twistedcaldav.directory.aggregate import AggregateDirectoryService
from twistedcaldav.directory.sudo import SudoDirectoryService
from twistedcaldav.httpfactory import HTTP503LoggingFactory, LimitingHTTPFactory, LimitingSite
from twistedcaldav.inspection import InspectionFactory
from twistedcaldav.static import CalendarHomeProvisioningFile
from twistedcaldav.static import TimezoneServiceFile
from twistedcaldav.timezones import TimezoneCache
from twistedcaldav import pdmonster
from twistedcaldav import memcachepool
from twistedcaldav.notify import installNotificationClient

from twistedcaldav.metafd import ReportingHTTPService

log = Logger()

try:
    from twistedcaldav.authkerb import NegotiateCredentialFactory
except ImportError:
    NegotiateCredentialFactory = None


class CalDAVService(service.MultiService):
    def __init__(self, logObserver):
        self.logObserver = logObserver
        service.MultiService.__init__(self)

    def privilegedStartService(self):
        service.MultiService.privilegedStartService(self)
        self.logObserver.start()

    def stopService(self):
        service.MultiService.stopService(self)
        self.logObserver.stop()


class CalDAVOptions(Options):
    optParameters = [[
        "config", "f", "/etc/caldavd/caldavd.plist", "Path to configuration file."
    ]]

    zsh_actions = {"config" : "_files -g '*.plist'"}

    def __init__(self, *args, **kwargs):
        super(CalDAVOptions, self).__init__(*args, **kwargs)

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
        if not os.path.exists(self['config']):
            log.msg("Config file %s not found, using defaults"
                    % (self['config'],))

        log.info("Reading configuration from file: %s" % (self['config'],))
        parseConfig(self['config'])

        config.updateDefaults(self.overrides)

        uid, gid = None, None

        if self.parent['uid'] or self.parent['gid']:
            uid, gid = getid(self.parent['uid'],
                             self.parent['gid'])

        if uid:
            if uid != os.getuid() and os.getuid() != 0:
                import pwd
                username = pwd.getpwuid(os.getuid())[0]
                raise UsageError("Only root can drop privileges you are: %r"
                                 % (username,))

        if gid:
            if gid != os.getgid() and os.getgid() != 0:
                import grp
                groupname = grp.getgrgid(os.getgid())[0]
                raise UsageError("Only root can drop privileges, you are: %s"
                                 % (groupname,))

        # Ignore the logfile parameter if not daemonized and log to stdout.
        if self.parent['nodaemon']:
            self.parent['logfile'] = None
        else:
            self.parent['logfile'] = config.ErrorLogFile

        self.parent['pidfile'] = config.PIDFile

        # Verify that document root actually exists
        self.checkDirectory(
            config.DocumentRoot,
            "Document root",
            access=os.W_OK,
            #permissions=0750,
            #uname=config.UserName,
            #gname=config.GroupName,
        )

        # Verify that data root actually exists
        self.checkDirectory(
            config.DataRoot,
            "Data root",
            access=os.W_OK,
            #permissions=0750,
            #uname=config.UserName,
            #gname=config.GroupName,
            create=(0750, config.UserName, config.GroupName,),
        )

        #
        # Nuke the file log observer's time format.
        #

        if not config.ErrorLogFile and config.ProcessType == "Slave":
            FileLogObserver.timeFormat = ""

        # Check current umask and warn if changed
        oldmask = os.umask(config.umask)
        if oldmask != config.umask:
            log.msg("WARNING: changing umask from: 0%03o to 0%03o"
                    % (oldmask, config.umask,))

    def checkDirectory(
        self, dirpath, description,
        access=None, fail=False, permissions=None,
        uname=None, gname=None, create=None
    ):
        if not os.path.exists(dirpath):
            if create is not None:
                # create is a tuple of (mode, username, groupname)
                try:
                    os.mkdir(dirpath)
                    os.chmod(dirpath, create[0])
                    if create[1] and create[2]:
                        import pwd
                        import grp
                        uid = pwd.getpwnam(create[1])[2]
                        gid = grp.getgrnam(create[2])[2]
                        os.chown(dirpath, uid, gid)
                except:
                    log.msg("Could not create %s" % (dirpath,))
                    raise ConfigurationError(
                        "%s does not exist and cannot be created: %s"
                        % (description, dirpath,)
                    )

                log.msg("Created %s" % (dirpath,))
            else:
                raise ConfigurationError("%s does not exist: %s"
                                         % (description, dirpath,))

        if not os.path.isdir(dirpath):
            raise ConfigurationError("%s is not a directory: %s"
                                     % (description, dirpath,))

        if access and not os.access(dirpath, access):
            raise ConfigurationError(
                "Insufficient permissions for server on %s directory: %s"
                % (description, dirpath,)
            )

        self.securityCheck(
            dirpath, description,
            fail=fail, permissions=permissions,
            uname=uname, gname=gname
        )

    def checkFile(
        self, filepath, description,
        access=None, fail=False, permissions=None,
        uname=None, gname=None
    ):
        if not os.path.exists(filepath):
            raise ConfigurationError(
                "%s does not exist: %s"
                % (description, filepath,)
            )
        elif not os.path.isfile(filepath):
            raise ConfigurationError(
                "%s is not a file: %s"
                % (description, filepath,)
            )
        elif access and not os.access(filepath, access):
            raise ConfigurationError(
                "Insufficient permissions for server on %s directory: %s"
                % (description, filepath,)
            )
        self.securityCheck(
            filepath, description,
            fail=fail, permissions=permissions,
            uname=uname, gname=gname
        )

    def securityCheck(
        self, path, description,
        fail=False, permissions=None,
        uname=None, gname=None
    ):
        def raiseOrPrint(txt):
            if fail:
                raise ConfigurationError(txt)
            else:
                log.msg("WARNING: %s" % (txt,))

        pathstat = os.stat(path)
        if permissions:
            if stat.S_IMODE(pathstat[stat.ST_MODE]) != permissions:
                raiseOrPrint(
                    "The permisions on %s directory %s are 0%03o "
                    "and do not match expected permissions: 0%03o"
                    % (description, path,
                       stat.S_IMODE(pathstat[stat.ST_MODE]), permissions)
                )
        if uname:
            import pwd
            try:
                pathuname = pwd.getpwuid(pathstat[stat.ST_UID])[0]
                if pathuname not in (uname, "_" + uname):
                    raiseOrPrint(
                        "The owner of %s directory %s is %s "
                        "and does not match the expected owner: %s"
                        % (description, path, pathuname, uname)
                    )
            except KeyError:
                raiseOrPrint(
                    "The owner of %s directory %s is unknown (%s) "
                    "and does not match the expected owner: %s"
                    % (description, path, pathstat[stat.ST_UID], uname)
                )

        if gname:
            import grp
            try:
                pathgname = grp.getgrgid(pathstat[stat.ST_GID])[0]
                if pathgname != gname:
                    raiseOrPrint(
                        "The group of %s directory %s is %s "
                        "and does not match the expected group: %s"
                        % (description, path, pathgname, gname)
                    )
            except KeyError:
                raiseOrPrint(
                    "The group of %s directory %s is unknown (%s) "
                    "and does not match the expected group: %s"
                    % (description, path, pathstat[stat.ST_GID], gname)
                )

from OpenSSL import SSL
from twisted.internet.ssl import DefaultOpenSSLContextFactory

def _getSSLPassphrase(*args):
    sslPrivKey = open(config.SSLPrivateKey)

    type = None
    for line in sslPrivKey.readlines():
        if "-----BEGIN RSA PRIVATE KEY-----" in line:
            type = "RSA"
            break
        elif "-----BEGIN DSA PRIVATE KEY-----" in line:
            type = "DSA"
            break

    sslPrivKey.close()

    if type is None:
        log.err("Could not get private key type for %s" % (config.SSLPrivateKey,))
        return False

    import commands
    return commands.getoutput("%s %s:%s %s" % (
        config.SSLPassPhraseDialog,
        config.ServerHostName,
        config.SSLPort,
        type
    ))


class ChainingOpenSSLContextFactory(DefaultOpenSSLContextFactory):
    def __init__(
        self, privateKeyFileName, certificateFileName,
        sslmethod=SSL.SSLv23_METHOD, certificateChainFile=None,
        passwdCallback=None
    ):
        self.certificateChainFile = certificateChainFile
        self.passwdCallback = passwdCallback

        DefaultOpenSSLContextFactory.__init__(
            self,
            privateKeyFileName,
            certificateFileName,
            sslmethod=sslmethod
        )

    def cacheContext(self):
        # Unfortunate code duplication.
        ctx = SSL.Context(self.sslmethod)

        if self.passwdCallback is not None:
            ctx.set_passwd_cb(self.passwdCallback)

        ctx.use_certificate_file(self.certificateFileName)
        ctx.use_privatekey_file(self.privateKeyFileName)

        if self.certificateChainFile != "":
            ctx.use_certificate_chain_file(self.certificateChainFile)

        self._context = ctx

class InheritedPort(tcp.Port):

    def __init__(self, fd, factory, reactor):
        tcp.Port.__init__(self, 0, factory, reactor=reactor)
        # MOR: careful because fromfd dup()'s the socket, so we need to
        # make sure we don't leak file descriptors
        self.socket = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
        self._realPortNumber = self.port = self.socket.getsockname()[1]

    def createInternetSocket(self):
        return self.socket

    def startListening(self):
        log.msg("%s starting on %s" % (self.factory.__class__, self._realPortNumber))
        self.factory.doStart()
        self.connected = 1
        self.fileno = self.socket.fileno
        self.numberAccepts = self.factory.maxRequests
        self.startReading()

    def doRead(self):
        self.numberAccepts = min(self.factory.maxRequests - self.factory.outstandingRequests, self.factory.maxAccepts)
        tcp.Port.doRead(self)


class InheritedSSLPort(InheritedPort):
    _socketShutdownMethod = 'sock_shutdown'

    transport = ssl.Server

    def __init__(self, fd, factory, ctxFactory, reactor):
        InheritedPort.__init__(self, fd, factory, reactor)
        self.ctxFactory = ctxFactory
        self.socket = SSL.Connection(self.ctxFactory.getContext(), self.socket)

    def _preMakeConnection(self, transport):
        transport._startTLS()
        return tcp.Port._preMakeConnection(self, transport)

class InheritTCPServer(internet.TCPServer):

    def __init__(self, *args, **kwargs):
        internet.TCPServer.__init__(self, *args, **kwargs)
        self.args[1].myServer = self

    def _getPort(self):
        from twisted.internet import reactor
        port = InheritedPort(self.args[0], self.args[1], reactor)
        port.startListening()
        self.myPort = port
        return port

class InheritSSLServer(internet.SSLServer):

    def __init__(self, *args, **kwargs):
        internet.SSLServer.__init__(self, *args, **kwargs)
        self.args[1].myServer = self

    def _getPort(self):
        from twisted.internet import reactor
        port = InheritedSSLPort(self.args[0], self.args[1], self.args[2], reactor)
        port.startListening()
        self.myPort = port
        return port

class CalDAVServiceMaker(object):
    implements(IPlugin, service.IServiceMaker)

    tapname = "caldav"

    description = "The Darwin Calendar Server"

    options = CalDAVOptions

    #
    # default resource classes
    #

    rootResourceClass            = RootResource
    principalResourceClass       = DirectoryPrincipalProvisioningResource
    calendarResourceClass        = CalendarHomeProvisioningFile
    timezoneServiceResourceClass = TimezoneServiceFile


    def createContextFactory(self):
        """
        Create an SSL context factory for use with any SSL socket talking to
        this server.
        """
        return ChainingOpenSSLContextFactory(
            config.SSLPrivateKey,
            config.SSLCertificate,
            certificateChainFile=config.SSLAuthorityChain,
            passwdCallback=_getSSLPassphrase
        )


    def makeService_Slave(self, options):
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

        directoryClass = namedClass(config.DirectoryService["type"])

        log.info("Configuring directory service of type: %s"
                 % (config.DirectoryService["type"],))

        baseDirectory = directoryClass(**config.DirectoryService["params"])

        directories.append(baseDirectory)

        sudoDirectory = None

        if config.SudoersFile and os.path.exists(config.SudoersFile):
            log.info("Configuring SudoDirectoryService with file: %s"
                     % (config.SudoersFile,))

            sudoDirectory = SudoDirectoryService(config.SudoersFile)
            sudoDirectory.realmName = baseDirectory.realmName

            CalDAVResource.sudoDirectory = sudoDirectory
            directories.insert(0, sudoDirectory)
        else:
            log.info("Not using SudoDirectoryService; file doesn't exist: %s"
                     % (config.SudoersFile,))

        directory = AggregateDirectoryService(directories)

        if sudoDirectory:
            directory.userRecordTypes.insert(0,
                SudoDirectoryService.recordType_sudoers)

        #
        # Configure Memcached Client Pool
        #
        if config.Memcached["ClientEnabled"]:
            memcachepool.installPool(
                IPv4Address(
                    'TCP',
                    config.Memcached["BindAddress"],
                    config.Memcached["Port"]),
                config.Memcached["MaxClients"])

        #
        # Configure NotificationClient
        #
        if config.Notifications["Enabled"]:
            installNotificationClient(
                config.Notifications["InternalNotificationHost"],
                config.Notifications["InternalNotificationPort"],
            )

        #
        # Setup Resource hierarchy
        #

        log.info("Setting up document root at: %s" % (config.DocumentRoot,))
        log.info("Setting up principal collection: %r" % (self.principalResourceClass,))

        principalCollection = self.principalResourceClass("/principals/", directory)

        log.info("Setting up calendar collection: %r" % (self.calendarResourceClass,))

        calendarCollection = self.calendarResourceClass(
            os.path.join(config.DocumentRoot, "calendars"),
            directory, "/calendars/"
        )

        log.info("Setting up root resource: %r" % (self.rootResourceClass,))

        root = self.rootResourceClass(
            config.DocumentRoot,
            principalCollections=(principalCollection,)
        )

        root.putChild('principals', principalCollection)
        root.putChild('calendars', calendarCollection)

        # Timezone service is optional
        if config.EnableTimezoneService:
            timezoneService = self.timezoneServiceResourceClass(
                os.path.join(config.DocumentRoot, "timezones"),
                root
            )
            root.putChild('timezones', timezoneService)

        #
        # Configure ancillary data
        #
        log.info("Setting up Timezone Cache")
        TimezoneCache.create()

        #
        # Configure the Site and Wrappers
        #

        credentialFactories = []

        portal = Portal(auth.DavRealm())

        portal.registerChecker(directory)

        realm = directory.realmName or ""

        log.info("Configuring authentication for realm: %s" % (realm,))

        for scheme, schemeConfig in config.Authentication.iteritems():
            scheme = scheme.lower()

            credFactory = None

            if schemeConfig["Enabled"]:
                log.info("Setting up scheme: %s" % (scheme,))

                if scheme == "kerberos":
                    if not NegotiateCredentialFactory:
                        log.info("Kerberos support not available")
                        continue

                    try:
                        principal = schemeConfig["ServicePrincipal"]
                        if not principal:
                            credFactory = NegotiateCredentialFactory(
                                type="http",
                                hostname=config.ServerHostName
                            )
                        else:
                            credFactory = NegotiateCredentialFactory(
                                principal=principal
                            )
                    except ValueError:
                        log.info("Could not start Kerberos")
                        continue

                elif scheme == 'digest':
                    credFactory = QopDigestCredentialFactory(
                        schemeConfig['Algorithm'],
                        schemeConfig['Qop'],
                        realm,
                        config.DataRoot,
                    )

                elif scheme == 'basic':
                    credFactory = BasicCredentialFactory(realm)

                else:
                    log.err("Unknown scheme: %s" % (scheme,))

            if credFactory:
                credentialFactories.append(credFactory)

        log.info("Configuring authentication wrapper")

        authWrapper = auth.AuthenticationWrapper(
            root,
            portal,
            credentialFactories,
            (auth.IPrincipal,)
        )

        logWrapper = DirectoryLogWrapperResource(
            authWrapper,
            directory
        )

        #
        # Configure the service
        #

        log.info("Setting up service")

        if config.ProcessType == "Slave":
            if (
                config.MultiProcess["ProcessCount"] > 1 and
                config.MultiProcess["LoadBalancer"]["Enabled"]
            ):
                realRoot = pdmonster.PDClientAddressWrapper(
                    logWrapper,
                    config.PythonDirector["ControlSocket"],
                    directory
                )
            else:
                realRoot = logWrapper

            logObserver = AMPCommonAccessLoggingObserver(
                config.ControlSocket
            )

        elif config.ProcessType == "Single":
            realRoot = logWrapper

            logObserver = RotatingFileAccessLoggingObserver(
                config.AccessLogFile
            )

        log.info("Configuring log observer: %s" % (logObserver,))

        service = CalDAVService(logObserver)

        site = LimitingSite(realRoot)


        # If inheriting file descriptors from the master, use those to handle
        # requests instead of opening ports.

        if (config.EnableConnectionInheriting and
           (config.InheritFDs or config.InheritSSLFDs)):

            channel = LimitingHTTPFactory(
                site,
                maxRequests=config.MaxRequests,
                maxAccepts=config.MaxAccepts,
                resumeRequests=config.ResumeRequests,
                betweenRequestsTimeOut=config.IdleConnectionTimeOut)

            for fd in config.InheritFDs:
                fd = int(fd)
                inheritedService = InheritTCPServer(
                    fd, channel,
                    backlog=config.ListenBacklog
                )
                inheritedService.setServiceParent(service)

            for fd in config.InheritSSLFDs:
                fd = int(fd)

                try:
                    contextFactory = self.createContextFactory()
                except SSL.Error, e:
                    log.error("Unable to set up SSL context factory: %s" % (e,))
                else:
                    inheritedService = InheritSSLServer(
                        fd, channel,
                        contextFactory,
                        backlog=config.ListenBacklog
                    )
                    inheritedService.setServiceParent(service)

        elif config.MetaFD:
            # Inherit a single socket to receive accept()ed connections via
            # recvmsg() and SCM_RIGHTS.
 
            fd = int(config.MetaFD)

            ReportingHTTPService(
                site, fd, self.createContextFactory()
            ).setServiceParent(service)

        else: # Not inheriting, therefore open our own:

            channel = HTTP503LoggingFactory(
                site,
                maxRequests=config.MaxRequests,
                betweenRequestsTimeOut=config.IdleConnectionTimeOut)

            if not config.BindAddresses:
                config.BindAddresses = [""]

            for bindAddress in config.BindAddresses:
                if config.BindHTTPPorts:
                    if config.HTTPPort == 0:
                        raise UsageError(
                            "HTTPPort required if BindHTTPPorts is not empty"
                        )
                elif config.HTTPPort != 0:
                    config.BindHTTPPorts = [config.HTTPPort]

                if config.BindSSLPorts:
                    if config.SSLPort == 0:
                        raise UsageError(
                            "SSLPort required if BindSSLPorts is not empty"
                        )
                elif config.SSLPort != 0:
                    config.BindSSLPorts = [config.SSLPort]

                for port in config.BindHTTPPorts:
                    log.info("Adding server at %s:%s" % (bindAddress, port))

                    httpService = internet.TCPServer(
                        int(port), channel,
                        interface=bindAddress,
                        backlog=config.ListenBacklog
                    )
                    httpService.setServiceParent(service)

                for port in config.BindSSLPorts:
                    log.info("Adding SSL server at %s:%s" % (bindAddress, port))

                    try:
                        contextFactory = self.createContextFactory()
                    except SSL.Error, e:
                        log.error("Unable to set up SSL context factory: %s" % (e,))
                        log.error("Disabling SSL port: %s" % (port,))
                    else:
                        httpsService = internet.SSLServer(
                            int(port), channel,
                            contextFactory, interface=bindAddress,
                            backlog=config.ListenBacklog
                        )
                        httpsService.setServiceParent(service)

        def updateChannel(config, items):
            channel.maxRequests = config.MaxRequests
            channel.maxAccepts = config.MaxAccepts
            channel.requestsRequests = config.ResumeRequests

        config.addHook(updateChannel)


        # Change log level back to what it was before
        setLogLevelForNamespace(None, oldLogLevel)


        # Register USR1 handler
        def sigusr1_handler(num, frame):
            from twisted.internet import reactor
            log.debug("SIGUSR1 received, triggering directory refresh")
            reactor.callLater(0, baseDirectory.refresh)
            return

        signal.signal(signal.SIGUSR1, sigusr1_handler)

        if config.EnableInspection:
            inspector = internet.TCPServer(
                int(config.InspectionPort) if config.InspectionPort else config.BaseInspectionPort,
                InspectionFactory(),
                interface="127.0.0.1"
            )
            inspector.setServiceParent(service)

        return service

    makeService_Combined = makeService_Combined
    makeService_Master   = makeService_Master
    makeService_Single   = makeService_Slave

    def makeService(self, options):
        serverType = config.ProcessType

        serviceMethod = getattr(self, "makeService_%s" % (serverType,), None)

        if not serviceMethod:
            raise UsageError(
                "Unknown server type %s. "
                "Please choose: Master, Slave or Combined"
                % (serverType,)
            )
        else:
            service = serviceMethod(options)

            #
            # Note: if there is a stopped process in the same session
            # as the calendar server and the calendar server is the
            # group leader then when twistd forks to drop privileges a
            # SIGHUP may be sent by the kernel, which can cause the
            # process to exit. This SIGHUP should be, at a minimum,
            # ignored.
            #

            def location(frame):
                if frame is None:
                    return "Unknown"
                else:
                    return "%s: %s" % (frame.f_code.co_name, frame.f_lineno)

            def sighup_handler(num, frame):
                log.info("SIGHUP recieved at %s" % (location(frame),))

                # Reload the config file
                config.reload()

                # If combined service send signal to all caldavd children
                if serverType == "Combined":
                    service.processMonitor.signalAll(signal.SIGHUP, "caldav")

                # FIXME: There is no memcachepool.getCachePool
                #   Also, better option is probably to add a hook to
                #   the config object instead of doing things here.
                #log.info("Suggesting new max clients for memcache.")
                #memcachepool.getCachePool().suggestMaxClients(
                #    config.Memcached["MaxClients"]
                #)

            signal.signal(signal.SIGHUP, sighup_handler)

            return service
