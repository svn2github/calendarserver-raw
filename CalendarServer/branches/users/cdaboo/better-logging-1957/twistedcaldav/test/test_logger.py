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

from twisted.trial import unittest

from twistedcaldav.config import config, defaultConfig
from twistedcaldav.logger import logger, Logger
from twistedcaldav.logger import LoggerOptions

testConfig = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>LoggerOptionsFile</key>
    <string>%s</string>
</dict>
</plist>
"""

testLogger_Standard = """<?xml version="1.0" encoding="UTF-8"?>
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
</dict>
</plist>
"""

systemLogLevels_Standard = {
    "Startup": Logger.logtypes["info"],
}

testLogger_More = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Default Log Level</key>
    <string>none</string>
    <key>System Log Levels</key>
    <dict>
        <key>Startup</key>
        <string>info</string>
        <key>iTIP</key>
        <string>debug</string>
        <key>CalDAV Outbox POST</key>
        <string>warning</string>
    </dict>
</dict>
</plist>
"""

systemLogLevels_More = {
    "Startup":            Logger.logtypes["info"],
    "iTIP":               Logger.logtypes["debug"],
    "CalDAV Outbox POST": Logger.logtypes["warning"],
}

testLogger_Broken = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Default Log Level</key>
    <string>bogus</string>
    <key>System Log Levels</key>
    <dict>
        <key>Startup</key>
        <string>info</string>
        <key>iTIP</key>
        <string>busted</string>
    </dict>
</dict>
</plist>
"""

systemLogLevels_Broken = {
    "Startup": Logger.logtypes["info"],
}

class LoggerTests(unittest.TestCase):
    def setUp(self):
        config.update(defaultConfig)
        self.testLogger = self.mktemp()
        self.testConfig = self.mktemp()
        open(self.testConfig, 'w').write(testConfig % (self.testLogger,))
        config.loadConfig(self.testConfig)

    def tearDown(self):
        self.loadLogfile(testLogger_Standard)

    def loadLogfile(self, plist):
        open(self.testLogger, 'w').write(plist)
        logger.readOptions()

    def verify(self, options, current, previous, system):
        self.assertEquals(options.currentLogLevel, current)
        self.assertEquals(options.previousLogLevel, previous)
        self.assertEquals(options.systemLogLevels, system)

    def testDefaultLogger(self):
        options = LoggerOptions()
        self.verify(options, Logger.logtypes["error"], Logger.logtypes["debug"], {})

    def testStandardLogger(self):
        self.loadLogfile(testLogger_Standard)

        self.verify(logger.options, Logger.logtypes["error"], Logger.logtypes["debug"], systemLogLevels_Standard)

    def testMoreLogger(self):
        self.loadLogfile(testLogger_Standard)

        self.loadLogfile(testLogger_More)

        self.verify(logger.options, Logger.logtypes["none"], Logger.logtypes["debug"], systemLogLevels_More)

    def testBrokenLogger(self):
        self.testDefaultLogger()

        self.loadLogfile(testLogger_Broken)

        self.verify(logger.options, Logger.logtypes["error"], Logger.logtypes["debug"], systemLogLevels_Broken)

    def testToggle(self):
        self.loadLogfile(testLogger_Standard)

        logger.toggle()

        self.verify(logger.options, Logger.logtypes["debug"], Logger.logtypes["error"], systemLogLevels_Standard)

        logger.toggle()

        self.verify(logger.options, Logger.logtypes["error"], Logger.logtypes["debug"], systemLogLevels_Standard)

    def testDefaultLogging(self):
        self.loadLogfile(testLogger_Standard)

        self.assertTrue(logger.canLog("error", {"id":"Dummy"}))
        self.assertFalse(logger.canLog("warning", {"id":"Dummy"}))
        self.assertFalse(logger.canLog("info", {"id":"Dummy"}))
        self.assertFalse(logger.canLog("debug", {"id":"Dummy"}))

    def testStandardLogging(self):
        self.loadLogfile(testLogger_Standard)

        self.assertTrue(logger.canLog("error", {"id":"Startup"}))
        self.assertTrue(logger.canLog("warning", {"id":"Startup"}))
        self.assertTrue(logger.canLog("info", {"id":"Startup"}))
        self.assertFalse(logger.canLog("debug", {"id":"Startup"}))

    def testMoreLogging(self):
        self.loadLogfile(testLogger_More)

        self.assertTrue(logger.canLog("error", {"id":"Startup"}))
        self.assertTrue(logger.canLog("warning", {"id":"Startup"}))
        self.assertTrue(logger.canLog("info", {"id":"Startup"}))
        self.assertFalse(logger.canLog("debug", {"id":"Startup"}))

        self.assertTrue(logger.canLog("error", {"id":"iTIP"}))
        self.assertTrue(logger.canLog("warning", {"id":"iTIP"}))
        self.assertTrue(logger.canLog("info", {"id":"iTIP"}))
        self.assertTrue(logger.canLog("debug", {"id":"iTIP"}))

        self.assertTrue(logger.canLog("error", {"id":"CalDAV Outbox POST"}))
        self.assertTrue(logger.canLog("warning", {"id":"CalDAV Outbox POST"}))
        self.assertFalse(logger.canLog("info", {"id":"CalDAV Outbox POST"}))
        self.assertFalse(logger.canLog("debug", {"id":"CalDAV Outbox POST"}))

        self.assertFalse(logger.canLog("error", {"id":"Dummy"}))
        self.assertFalse(logger.canLog("warning", {"id":"Dummy"}))
        self.assertFalse(logger.canLog("info", {"id":"Dummy"}))
        self.assertFalse(logger.canLog("debug", {"id":"Dummy"}))

    def testMultipleLogging(self):
        self.loadLogfile(testLogger_More)

        args = {"id": ["Startup",]}
        self.assertTrue(logger.canLog("error", args))
        self.assertEquals(args["system"], "Startup")
        self.assertTrue(logger.canLog("warning", args))
        self.assertTrue(logger.canLog("info", args))
        self.assertFalse(logger.canLog("debug", args))

        args = {"id": ["Startup", "iTIP"]}
        self.assertTrue(logger.canLog("error", args))
        self.assertEquals(args["system"], "Startup,iTIP")
        self.assertTrue(logger.canLog("warning", args))
        self.assertTrue(logger.canLog("info", args))
        self.assertTrue(logger.canLog("debug", args))

        args = {"id": ["Dummy1", "Dummy2"]}
        self.assertFalse(logger.canLog("error", args))
        self.assertEquals(args["system"], "Dummy1,Dummy2")
        self.assertFalse(logger.canLog("warning", args))
        self.assertFalse(logger.canLog("info", args))
        self.assertFalse(logger.canLog("debug", args))

        args = {"id": ["Dummy1", "Startup"]}
        self.assertTrue(logger.canLog("error", args))
        self.assertEquals(args["system"], "Dummy1,Startup")
        self.assertTrue(logger.canLog("warning", args))
        self.assertTrue(logger.canLog("info", args))
        self.assertFalse(logger.canLog("debug", args))

    def testTypesLogging(self):
        self.loadLogfile(testLogger_More)

        class _A(object):
            pass

        class _B(object):
            pass

        args = {"id": [_A, "Startup"]}
        self.assertTrue(logger.canLog("error", args))
        self.assertEquals(args["system"], "_A,Startup")
        self.assertTrue(logger.canLog("warning", args))
        self.assertTrue(logger.canLog("info", args))
        self.assertFalse(logger.canLog("debug", args))

        args = {"id": [_B(), "Startup"]}
        self.assertTrue(logger.canLog("error", args))
        self.assertEquals(args["system"], "_B,Startup")
        self.assertTrue(logger.canLog("warning", args))
        self.assertTrue(logger.canLog("info", args))
        self.assertFalse(logger.canLog("debug", args))

        args = {"id": [123, "Startup"]}
        self.assertTrue(logger.canLog("error", args))
        self.assertEquals(args["system"], "123,Startup")
        self.assertTrue(logger.canLog("warning", args))
        self.assertTrue(logger.canLog("info", args))
        self.assertFalse(logger.canLog("debug", args))

        args = {"id": [u'Testing', "Startup"]}
        self.assertTrue(logger.canLog("error", args))
        self.assertEquals(args["system"], "Testing,Startup")
        self.assertTrue(logger.canLog("warning", args))
        self.assertTrue(logger.canLog("info", args))
        self.assertFalse(logger.canLog("debug", args))
