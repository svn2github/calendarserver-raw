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

from twisted.trial import unittest

from twisted.python.usage import Options, UsageError

from twistedcaldav.tap import CalDAVOptions

from twistedcaldav.config import config
from twistedcaldav import config as config_mod


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

        from copy import deepcopy
        myConfig = deepcopy(config_mod.defaultConfig)

        myConfig['Authentication']['Basic']['Enabled'] = False

        myConfig['MultiProcess']['LoadBalancer']['Enabled'] = False

        myConfig['Port'] = 80

        myConfig['ServerHostName'] = 'calendar.calenderserver.org'

        from twistedcaldav.py.plistlib import writePlist

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
