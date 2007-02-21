##
# Copyright (c) 2007 Apple Inc. All rights reserved.
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

import os
from copy import deepcopy

from twisted.trial import unittest

from twisted.python.usage import Options, UsageError
from twisted.python.util import sibpath
from twisted.application.service import IService
from twisted.application import internet

from twistedcaldav.tap import CalDAVOptions, CalDAVServiceMaker
from twistedcaldav import tap

from twistedcaldav.config import config
from twistedcaldav import config as config_mod
from twistedcaldav.py.plistlib import writePlist


class TestCalDAVOptions(CalDAVOptions):
    """
    A fake that implementation of CalDAVOptions that provides
    empty implementations of checkDirectory and checkFile.
    """

    def checkDirectory(*args):
        pass

    def checkFile(*args):
        pass


class CalDAVOptionsTest(unittest.TestCase):
    """
    Test various parameters of our usage.Options subclass
    """

    def setUp(self):
        """
        Set up our options object, giving it a parent, and forcing the
        global config to be loaded from defaults.
        """

        self.config = TestCalDAVOptions()
        self.config.parent = Options()
        self.config.parent['uid'] = None
        self.config.parent['gid'] = None

        config_mod.parseConfig('non-existant-config')

    def test_overridesConfig(self):
        """
        Test that values on the command line's -o and --option options
        overide the config file
        """

        argv = ['-o', 'SACLEnable',
                '-o', 'Port=80',
                '-o', 'BindAddress=127.0.0.1,127.0.0.2,127.0.0.3',
                '-o', 'DocumentRoot=/dev/null',
                '-o', 'Username=None',
                '-o', 'CalendarUserProxyEnabled=False']

        self.config.parseOptions(argv)

        self.assertEquals(config.SACLEnable, True)
        self.assertEquals(config.Port, 80)
        self.assertEquals(config.BindAddress, ['127.0.0.1',
                                               '127.0.0.2',
                                               '127.0.0.3'])
        self.assertEquals(config.DocumentRoot, '/dev/null')
        self.assertEquals(config.Username, None)
        self.assertEquals(config.CalendarUserProxyEnabled, False)

        argv = ['-o', 'Authentication=This Doesn\'t Matter']

        self.assertRaises(UsageError, self.config.parseOptions, argv)

    def test_setsParent(self):
        """
        Test that certain values are set on the parent (i.e. twistd's
        Option's object)
        """

        argv = ['-o', 'ErrorLogFile=/dev/null',
                '-o', 'PIDFile=/dev/null']

        self.config.parseOptions(argv)

        self.assertEquals(self.config.parent['logfile'], '/dev/null')

        self.assertEquals(self.config.parent['pidfile'], '/dev/null')

    def test_specifyConfigFile(self):
        """
        Test that specifying a config file from the command line
        loads the global config with those values properly.
        """

        myConfig = deepcopy(config_mod.defaultConfig)

        myConfig['Authentication']['Basic']['Enabled'] = False

        myConfig['MultiProcess']['LoadBalancer']['Enabled'] = False

        myConfig['Port'] = 80

        myConfig['ServerHostName'] = 'calendar.calenderserver.org'

        myConfigFile = self.mktemp()
        writePlist(myConfig, myConfigFile)

        args = ['-f', myConfigFile]

        self.config.parseOptions(args)

        self.assertEquals(config.ServerHostName, myConfig['ServerHostName'])

        self.assertEquals(config.MultiProcess['LoadBalancer']['Enabled'],
                          myConfig['MultiProcess']['LoadBalancer']['Enabled'])

        self.assertEquals(config.Port, myConfig['Port'])

        self.assertEquals(config.Authentication['Basic']['Enabled'],
                          myConfig['Authentication']['Basic']['Enabled'])


class BaseServiceMakerTests(unittest.TestCase):
    """
    Utility class for ServiceMaker tests.
    """

    def setUp(self):
        self.options = TestCalDAVOptions()
        self.options.parent = Options()
        self.options.parent['gid'] = None
        self.options.parent['uid'] = None

        self.config = deepcopy(config_mod.defaultConfig)

        accountsFile = sibpath(os.path.dirname(__file__),
                               'directory/test/accounts.xml')

        self.config['DirectoryService'] = {
            'params': {'xmlFile': accountsFile},
            'type': 'twistedcaldav.directory.xmlfile.XMLDirectoryService'
            }

        self.config['DocumentRoot'] = self.mktemp()

        self.config['SSLPrivateKey'] = sibpath(__file__, 'data/server.pem')
        self.config['SSLCertificate'] = sibpath(__file__, 'data/server.pem')
        self.config['SSLOnly'] = False

        os.mkdir(self.config['DocumentRoot'])

        self.configFile = self.mktemp()

        self.writeConfig()

    def writeConfig(self):
        """
        Flush self.config out to self.configFile
        """

        writePlist(self.config, self.configFile)

    def makeService(self):
        """
        Create a service by calling into CalDAVServiceMaker with
        self.configFile
        """

        self.options.parseOptions(['-f', self.configFile])

        return CalDAVServiceMaker().makeService(self.options)


class CalDAVServiceMakerTests(BaseServiceMakerTests):
    """
    Test the service maker's behavior
    """

    def test_makeServiceDispatcher(self):
        """
        Test the default options of the dispatching makeService
        """
        validServices = ['singleprocess', 'slave', 'master', 'multiprocess']

        for service in validServices:
            self.config['ServerType'] = service
            self.writeConfig()
            self.makeService()

        self.config['ServerType'] = 'Unknown Service'
        self.writeConfig()
        self.assertRaises(UsageError, self.makeService)


class SingleServiceTest(BaseServiceMakerTests):
    """
    Test various configurations of the single service
    """

    def test_defaultService(self):
        """
        Test the value of a single process service in it's simplest
        configuration.
        """

        service = self.makeService()

        self.failUnless(IService(service))

        self.failUnless(service.services)

        self.failUnless(isinstance(service, tap.CalDAVService))

    def test_defaultListeners(self):
        """
        Test that the single process service has sub services with the
        default TCP and SSL configuration
        """

        service = self.makeService()

        expectedSubServices = ((internet.TCPServer, self.config['Port']),
                               (internet.SSLServer, self.config['SSLPort']))

        configuredSubServices = [(s.__class__, s.args)
                                 for s in service.services]

        for serviceClass, serviceArgs in configuredSubServices:
            self.failUnless(
                serviceClass in (s[0] for s in expectedSubServices))

            self.assertEquals(serviceArgs[0],
                              dict(expectedSubServices)[serviceClass])

    def test_SSLKeyConfiguration(self):
        """
        Test that the configuration of the SSLServer reflect the config file's
        SSL Private Key and SSL Certificate
        """

        service = self.makeService()

        sslService = None
        for s in service.services:
            if isinstance(s, internet.SSLServer):
                sslService = s
                break

        context = sslService.args[2]

        self.assertEquals(self.config['SSLPrivateKey'],
                          context.privateKeyFileName)

        self.assertEquals(self.config['SSLCertificate'],
                          context.certificateFileName)

    def test_noSSL(self):
        """
        Test the single service to make sure there is no SSL Service when SSL
        is disabled
        """

        self.config['SSLEnable'] = False
        self.writeConfig()
        service = self.makeService()

        self.failIf(
            internet.SSLServer in [s.__class__ for s in service.services])

    def test_SSLOnly(self):
        """
        Test the single service to make sure there is no TCPServer when
        SSLOnly is turned on.
        """

        self.config['SSLOnly'] = True
        self.writeConfig()
        service = self.makeService()

        self.failIf(
            internet.TCPServer in [s.__class__ for s in service.services])

    def test_singleBindAddress(self):
        """
        Test that the TCPServer and SSLServers are bound to the proper address
        """

        self.config['BindAddress'] = ['127.0.0.1']
        self.writeConfig()
        service = self.makeService()

        for s in service.services:
            self.assertEquals(s.kwargs['interface'], '127.0.0.1')

    def test_multipleBindAddress(self):
        """
        Test that the TCPServer and SSLServers are bound to the proper
        addresses.
        """

        self.config['BindAddress'] = ['127.0.0.1', '10.0.0.2', '172.53.13.123']
        self.writeConfig()
        service = self.makeService()

        tcpServers = []
        sslServers = []

        for s in service.services:
            if isinstance(s, internet.TCPServer):
                tcpServers.append(s)
            elif isinstance(s, internet.SSLServer):
                sslServers.append(s)

        self.assertEquals(len(tcpServers), len(self.config['BindAddress']))
        self.assertEquals(len(sslServers), len(self.config['BindAddress']))

        for addr in self.config['BindAddress']:
            for s in tcpServers:
                if s.kwargs['interface'] == addr:
                    tcpServers.remove(s)

            for s in sslServers:
                if s.kwargs['interface'] == addr:
                    sslServers.remove(s)

        self.assertEquals(len(tcpServers), 0)
        self.assertEquals(len(sslServers), 0)


class ServiceHTTPFactoryTests(BaseServiceMakerTests):
    """
    Test the configuration of the initial resource hierarchy of the
    single service
    """

    def getSite(self):
        service = self.makeService()

        return service.services[0].args[1].protocolArgs['requestFactory']

    def test_AuthWrapperAllEnabled(self):
        """
        Test the configuration of the authentication wrapper
        when all schemes are enabled.
        """
        self.config['Authentication']['Digest']['Enabled'] = True
        self.config['Authentication']['Kerberos']['Enabled'] = True
        self.config['Authentication']['Basic']['Enabled'] = True

        self.writeConfig()
        site = self.getSite()

        from twisted.web2.dav import auth

        self.failUnless(isinstance(
                site.resource.resource,
                auth.AuthenticationWrapper))

        authWrapper = site.resource.resource

        expectedSchemes = ['negotiate', 'digest', 'basic']

        for scheme in authWrapper.credentialFactories:
            self.failUnless(scheme in expectedSchemes)

        self.assertEquals(len(expectedSchemes),
                          len(authWrapper.credentialFactories))

    def test_AuthWrapperPartialEnabled(self):
        """
        Test that the expected credential factories exist when
        only a partial set of authentication schemes is
        enabled.
        """

        self.config['Authentication']['Basic']['Enabled'] = False
        self.config['Authentication']['Kerberos']['Enabled'] = False

        self.writeConfig()
        site = self.getSite()

        authWrapper = site.resource.resource

        expectedSchemes = ['digest']

        for scheme in authWrapper.credentialFactories:
            self.failUnless(scheme in expectedSchemes)

        self.assertEquals(len(expectedSchemes),
                          len(authWrapper.credentialFactories))

    def test_LogWrapper(self):
        """
        Test the configuration of the log wrapper
        """

        site = self.getSite()

        from twisted.web2.log import LogWrapperResource

        self.failUnless(isinstance(
                site.resource,
                LogWrapperResource))

    def test_rootResource(self):
        """
        Test the root resource
        """
        site = self.getSite()
        root = site.resource.resource.resource

        self.failUnless(isinstance(root, CalDAVServiceMaker.rootResourceClass))

    def test_principalResource(self):
        """
        Test the principal resource
        """
        site = self.getSite()
        root = site.resource.resource.resource

        self.failUnless(isinstance(
                root.getChild('principals'),
                CalDAVServiceMaker.principalResourceClass))

    def test_calendarResource(self):
        """
        Test the calendar resource
        """
        site = self.getSite()
        root = site.resource.resource.resource

        self.failUnless(isinstance(
                root.getChild('calendars'),
                CalDAVServiceMaker.calendarResourceClass))


class DirectoryServiceTest(BaseServiceMakerTests):
    """
    Tests of the directory service
    """

    def test_sameDirectory(self):
        """
        Test that the principal hierarchy has a reference
        to the same DirectoryService as the calendar hierarchy
        """

    test_sameDirectory.todo = "Not Implemented Yet"

    def test_aggregateDirectory(self):
        """
        Assert that the base directory service is actually
        an AggregateDirectoryService
        """

    test_aggregateDirectory.todo = "Not implemented yet"

    def test_sudoDirectoryService(self):
        """
        Test that a sudo directory service is available if the
        SudoersFile is set and exists
        """

    test_sudoDirectoryService.todo = "Not implemented yet"

    def test_configuredDirectoryService(self):
        """
        Test that the real directory service is the directory service
        set in the configuration file.
        """

    test_configuredDirectoryService.todo = "Not implemented yet"
