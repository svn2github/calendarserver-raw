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
#
# DRI: David Reid, dreid@apple.com
##

import sys
import os
import stat

from zope.interface import implements

from twisted.python import log
from twisted.python.usage import Options, UsageError
from twisted.python.reflect import namedClass

from twisted.application import internet, service
from twisted.plugin import IPlugin

from twisted.scripts.mktap import getid

from twisted.cred.portal import Portal

from twisted.web2.dav import auth
from twisted.web2.dav import davxml
from twisted.web2.dav.resource import TwistedACLInheritable
from twisted.web2.auth.basic import BasicCredentialFactory
from twisted.web2.channel import http

from twisted.web2.server import Site

from twistedcaldav import logging
from twistedcaldav.cluster import makeService_Combined, makeService_Master
from twistedcaldav.config import config, parseConfig, defaultConfig, ConfigurationError
from twistedcaldav.root import RootResource
from twistedcaldav.resource import CalDAVResource
from twistedcaldav.directory.digest import QopDigestCredentialFactory
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twistedcaldav.directory.aggregate import AggregateDirectoryService
from twistedcaldav.directory.sudo import SudoDirectoryService

from twistedcaldav import pdmonster
from twistedcaldav.static import CalendarHomeProvisioningFile
from twistedcaldav.timezones import TimezoneCache

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

        try:
            parseConfig(self['config'])
        except ConfigurationError, e:
            log.err("Invalid configuration: %s" % (e,))
            sys.exit(1)

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

        # Verify that ssl certs exist if needed
        if config.SSLPort:
            try:
                self.checkFile(
                    config.SSLPrivateKey,
                    "SSL Private key",
                    access=os.R_OK,
                    #permissions=0640,
                )
                self.checkFile(
                    config.SSLCertificate,
                    "SSL Public key",
                    access=os.R_OK,
                    #permissions=0644,
                )
            except ConfigurationError, e:
                log.err(str(e))
                log.err("Disabling SSL port")
                config.SSLPort = 0

        #
        # Nuke the file log observer's time format.
        #

        if not config.ErrorLogFile and config.ProcessType == "Slave":
            log.FileLogObserver.timeFormat = ""

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
        logging.err("Could not get private key type for %s"
                    % (config.SSLPrivateKey,))
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


class CalDAVServiceMaker(object):
    implements(IPlugin, service.IServiceMaker)

    tapname = "caldav"

    description = "The Darwin Calendar Server"

    options = CalDAVOptions

    #
    # default resource classes
    #

    rootResourceClass      = RootResource
    principalResourceClass = DirectoryPrincipalProvisioningResource
    calendarResourceClass  = CalendarHomeProvisioningFile

    def makeService_Slave(self, options):
        # Change log level to at least "info" as its useful to have
        # that during startup
        old_logging = logging.currentLogLevel
        if logging.currentLogLevel < logging.logtypes["info"]:
            logging.currentLogLevel = logging.logtypes["info"]

        #
        # Setup the Directory
        #
        directories = []

        directoryClass = namedClass(config.DirectoryService["type"])

        logging.info("Configuring directory service of type: %s"
                     % (config.DirectoryService['type'],), system="startup")

        baseDirectory = directoryClass(**config.DirectoryService["params"])

        directories.append(baseDirectory)

        sudoDirectory = None

        if config.SudoersFile and os.path.exists(config.SudoersFile):
            logging.info("Configuring SudoDirectoryService with file: %s"
                         % (config.SudoersFile,), system="startup")

            sudoDirectory = SudoDirectoryService(config.SudoersFile)
            sudoDirectory.realmName = baseDirectory.realmName

            CalDAVResource.sudoDirectory = sudoDirectory
            directories.insert(0, sudoDirectory)
        else:
            logging.info("Not using SudoDirectoryService; file doesn't exist: %s"
                         % (config.SudoersFile,), system="startup")

        directory = AggregateDirectoryService(directories)

        if sudoDirectory:
            directory.userRecordTypes.insert(0,
                SudoDirectoryService.recordType_sudoers)

        #
        # Setup Resource hierarchy
        #

        logging.info("Setting up document root at: %s"
                     % (config.DocumentRoot,), system="startup")

        logging.info("Setting up principal collection: %r"
                     % (self.principalResourceClass,), system="startup")

        principalCollection = self.principalResourceClass(
            os.path.join(config.DocumentRoot, "principals"),
            "/principals/", directory
        )

        logging.info("Setting up calendar collection: %r"
                     % (self.calendarResourceClass,), system="startup")

        calendarCollection = self.calendarResourceClass(
            os.path.join(config.DocumentRoot, "calendars"),
            directory, "/calendars/"
        )

        logging.info("Setting up root resource: %r"
                     % (self.rootResourceClass,), system="startup")

        root = self.rootResourceClass(
            config.DocumentRoot,
            principalCollections=(principalCollection,)
        )

        root.putChild('principals', principalCollection)
        root.putChild('calendars', calendarCollection)

        # Configure default ACLs on the root resource

        logging.info("Setting up default ACEs on root resource",
                     system="startup")

        rootACEs = [
            davxml.ACE(
                davxml.Principal(davxml.All()),
                davxml.Grant(davxml.Privilege(davxml.Read())),
            ),
        ]

        log.msg("Setting up AdminPrincipals")

        for principal in config.AdminPrincipals:
            logging.info("Added %s as admin principal"
                         % (principal,), system="startup")

            rootACEs.append(
                davxml.ACE(
                    davxml.Principal(davxml.HRef(principal)),
                    davxml.Grant(davxml.Privilege(davxml.All())),
                    davxml.Protected(),
                    TwistedACLInheritable(),
                )
            )

        log.msg("Setting root ACL")

        root.setAccessControlList(davxml.ACL(*rootACEs))

        #
        # Configure ancillary data
        #
        logging.info("Setting up Timezone Cache", system="startup")
        TimezoneCache().register()

        #
        # Configure the Site and Wrappers
        #

        credentialFactories = []

        portal = Portal(auth.DavRealm())

        portal.registerChecker(directory)

        realm = directory.realmName or ""

        logging.info("Configuring authentication for realm: %s"
                     % (realm,), system="startup")

        for scheme, schemeConfig in config.Authentication.iteritems():
            scheme = scheme.lower()

            credFactory = None

            if schemeConfig["Enabled"]:
                logging.info("Setting up scheme: %s"
                             % (scheme,), system="startup")

                if scheme == "kerberos":
                    if not NegotiateCredentialFactory:
                        logging.info("Kerberos support not available",
                                     system="startup")
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
                        logging.info("Could not start Kerberos",
                                     system="startup")
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
                    logging.err("Unknown scheme: %s"
                                % (scheme,), system="startup")

            if credFactory:
                credentialFactories.append(credFactory)

        log.msg("Configuring authentication wrapper")

        authWrapper = auth.AuthenticationWrapper(
            root,
            portal,
            credentialFactories,
            (auth.IPrincipal,)
        )

        logWrapper = logging.DirectoryLogWrapperResource(
            authWrapper,
            directory
        )

        #
        # Configure the service
        #

        log.msg("Setting up service")

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

            logObserver = logging.AMPCommonAccessLoggingObserver(
                config.ControlSocket
            )

        elif config.ProcessType == "Single":
            realRoot = logWrapper

            logObserver = logging.RotatingFileAccessLoggingObserver(
                config.AccessLogFile
            )

        logging.info("Configuring log observer: %s"
                     % (logObserver,), system="startup")

        service = CalDAVService(logObserver)

        site = Site(realRoot)

        channel = http.HTTPFactory(site)

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
                logging.info("Adding server at %s:%s"
                             % (bindAddress, port), system="startup")

                httpService = internet.TCPServer(
                    int(port), channel,
                    interface=bindAddress
                )
                httpService.setServiceParent(service)

            for port in config.BindSSLPorts:
                logging.info("Adding SSL server at %s:%s"
                             % (bindAddress, port), system="startup")

                contextFactory = ChainingOpenSSLContextFactory(
                    config.SSLPrivateKey,
                    config.SSLCertificate,
                    certificateChainFile=config.SSLAuthorityChain,
                    passwdCallback=_getSSLPassphrase
                )

                httpsService = internet.SSLServer(
                    int(port), channel,
                    contextFactory, interface=bindAddress
                )
                httpsService.setServiceParent(service)

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
            # Temporary hack to work around SIGHUP problem
            # If there is a stopped process in the same session as the
            # calendar server and the calendar server is the group
            # leader then when twistd forks to drop privelages a
            # SIGHUP may be sent by the kernel. This SIGHUP should be
            # ignored.
            # Note that this handler is not unset, so any further
            # SIGHUPs are also ignored.
            #

            def location(frame):
                if frame is None:
                    return "Unknown"
                else:
                    return "%s: %s" % (frame.f_code.co_name, frame.f_lineno)

            import signal
            def sighup_handler(num, frame):
                log.msg("SIGHUP recieved at %s" % (location(frame),))
            signal.signal(signal.SIGHUP, sighup_handler)

            def sigusr1_handler(num, frame):
                log.msg("SIGUSR1 recieved at %s" % (location(frame),))
                logging.toggle()
            signal.signal(signal.SIGUSR1, sigusr1_handler)

            return service
