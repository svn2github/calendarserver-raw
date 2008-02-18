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

from twisted.trial import unittest

from twistedcaldav.config import config, defaultConfig
from twistedcaldav.directory.directory import DirectoryRecord
from twistedcaldav.directory.directory import DirectoryService
from twistedcaldav.accounting import Accounting
from twistedcaldav.logger import logger
import os

testConfig = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>LoggerOptionsFile</key>
    <string>%s</string>
</dict>
</plist>
"""

testAccounting_Disabled = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Default Log Level</key>
    <string>error</string>
    <key>System Log Levels</key>
    <dict>
        <key>Startup</key>
        <string>info</string>
    </dict>
    <key>Accounting</key>
    <dict>
        <key>Enabled</key>
        <false/>
        <key>LogDirectory</key>
        <string>%s</string>
        <key>iTIP</key>
        <false/>
        <key>principals</key>
        <array></array>
    </dict>
</dict>
</plist>
"""

testAccounting_EnabledAll = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Default Log Level</key>
    <string>error</string>
    <key>System Log Levels</key>
    <dict>
        <key>Startup</key>
        <string>info</string>
    </dict>
    <key>Accounting</key>
    <dict>
        <key>Enabled</key>
        <true/>
        <key>LogDirectory</key>
        <string>%s</string>
        <key>iTIP</key>
        <true/>
        <key>principals</key>
        <array></array>
    </dict>
</dict>
</plist>
"""

testAccounting_EnabledSome = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Default Log Level</key>
    <string>error</string>
    <key>System Log Levels</key>
    <dict>
        <key>Startup</key>
        <string>info</string>
    </dict>
    <key>Accounting</key>
    <dict>
        <key>Enabled</key>
        <true/>
        <key>LogDirectory</key>
        <string>%s</string>
        <key>iTIP</key>
        <true/>
        <key>principals</key>
        <array>
            <string>/principals/users/user01/</string>
            <string>/principals/__uids__/user02/</string>
        </array>
    </dict>
</dict>
</plist>
"""

class LoggerTests(unittest.TestCase):

    class DummyPrincipal(object):
        
        class DummyDirectoryService(DirectoryService):
            def __init__(self):
                self.realmName = "example.com"

        def __init__(self, url, alturis, rtype, rname):
            self.url = url
            self.alturis = alturis
            self.record = DirectoryRecord(self.DummyDirectoryService(), rtype, rname, rname, rname, set(), False, True)

        def principalURL(self):
            return self.url
            
        def alternateURIs(self):
            return self.alturis

    principal01 = DummyPrincipal("/principals/__uids__/user01/", ("/principals/users/user01/",), DirectoryService.recordType_users, "user01")
    principal02 = DummyPrincipal("/principals/__uids__/user02/", ("/principals/users/user02/",), DirectoryService.recordType_users, "user02")
    principal03 = DummyPrincipal("/principals/__uids__/user03/", ("/principals/users/user03/",), DirectoryService.recordType_users, "user03")

    def setUp(self):
        config.update(defaultConfig)
        self.testLogger = self.mktemp()
        self.testConfig = self.mktemp()
        open(self.testConfig, 'w').write(testConfig % (self.testLogger,))
        config.loadConfig(self.testConfig)
        
        self.temp_disabled = self.mktemp()
        self.test_Disabled = testAccounting_Disabled % (self.temp_disabled,)
        self.temp_enabledall = self.mktemp()
        self.test_EnabledAll = testAccounting_EnabledAll % (self.temp_enabledall,)
        self.temp_enabledsome = self.mktemp()
        self.test_EnabledSome = testAccounting_EnabledSome % (self.temp_enabledsome,)

    def tearDown(self):
        self.loadLogfile(self.test_Disabled)

    def loadLogfile(self, plist):
        open(self.testLogger, 'w').write(plist)
        logger.readOptions()

    def validAccount(self, principal, root_path):
        self.assertTrue(Accounting.isiTIPEnabled(principal))
        self.assertEquals(Accounting.getLog(principal, Accounting.type_iTIP), os.path.join(root_path, principal.record.recordType, principal.record.shortName, "iTIP",))
        self.assertTrue(os.path.exists(os.path.join(root_path, principal.record.recordType, principal.record.shortName, "iTIP",)))
    
    def testDefaultAccounting(self):
        self.assertFalse(Accounting.isEnabled())
        self.assertFalse(Accounting.isiTIPEnabled(LoggerTests.principal01))
        self.assertFalse(Accounting.isiTIPEnabled(LoggerTests.principal02))
        self.assertFalse(Accounting.isiTIPEnabled(LoggerTests.principal03))

    def testDisabledAccounting(self):
        self.loadLogfile(self.test_Disabled)

        self.assertFalse(Accounting.isEnabled())
        self.assertFalse(Accounting.isiTIPEnabled(LoggerTests.principal01))
        self.assertFalse(Accounting.isiTIPEnabled(LoggerTests.principal02))
        self.assertFalse(Accounting.isiTIPEnabled(LoggerTests.principal03))

    def testEnabledAllAccounting(self):
        self.loadLogfile(self.test_EnabledAll)

        self.validAccount(LoggerTests.principal01, self.temp_enabledall)
        self.validAccount(LoggerTests.principal02, self.temp_enabledall)
        self.validAccount(LoggerTests.principal03, self.temp_enabledall)

    def testEnabledSomeAccounting(self):
        self.loadLogfile(self.test_EnabledSome)

        self.validAccount(LoggerTests.principal01, self.temp_enabledsome)
        self.validAccount(LoggerTests.principal02, self.temp_enabledsome)
        self.assertFalse(Accounting.isiTIPEnabled(LoggerTests.principal03))

    def testInvalidAccountingType(self):
        self.loadLogfile(self.test_EnabledSome)

        self.assertRaises(AssertionError, Accounting.getLog, LoggerTests.principal02, ("PUT", True,))
