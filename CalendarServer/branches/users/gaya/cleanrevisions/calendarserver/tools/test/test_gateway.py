##
# Copyright (c) 2005-2014 Apple Inc. All rights reserved.
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
from __future__ import print_function

import os
import sys
from plistlib import readPlistFromString
import xml

from twext.python.filepath import CachingFilePath as FilePath
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, Deferred, returnValue

from twistedcaldav.config import config
from twistedcaldav.test.util import TestCase, CapturingProcessProtocol
from calendarserver.tools.util import getDirectory
import plistlib


class RunCommandTestCase(TestCase):

    def setUp(self):
        super(RunCommandTestCase, self).setUp()

        testRoot = os.path.join(os.path.dirname(__file__), "gateway")
        templateName = os.path.join(testRoot, "caldavd.plist")
        templateFile = open(templateName)
        template = templateFile.read()
        templateFile.close()

        databaseRoot = os.path.abspath("_spawned_scripts_db" + str(os.getpid()))
        newConfig = template % {
            "ServerRoot" : os.path.abspath(config.ServerRoot),
            "DatabaseRoot" : databaseRoot,
            "WritablePlist" : os.path.join(os.path.abspath(config.ConfigRoot), "caldavd-writable.plist"),
        }
        configFilePath = FilePath(os.path.join(config.ConfigRoot, "caldavd.plist"))
        configFilePath.setContent(newConfig)

        self.configFileName = configFilePath.path
        config.load(self.configFileName)

        origUsersFile = FilePath(os.path.join(os.path.dirname(__file__),
            "gateway", "users-groups.xml"))
        copyUsersFile = FilePath(os.path.join(config.DataRoot, "accounts.xml"))
        origUsersFile.copyTo(copyUsersFile)

        origResourcesFile = FilePath(os.path.join(os.path.dirname(__file__),
            "gateway", "resources-locations.xml"))
        copyResourcesFile = FilePath(os.path.join(config.DataRoot, "resources.xml"))
        origResourcesFile.copyTo(copyResourcesFile)

        origAugmentFile = FilePath(os.path.join(os.path.dirname(__file__),
            "gateway", "augments.xml"))
        copyAugmentFile = FilePath(os.path.join(config.DataRoot, "augments.xml"))
        origAugmentFile.copyTo(copyAugmentFile)

        # Make sure trial puts the reactor in the right state, by letting it
        # run one reactor iteration.  (Ignore me, please.)
        d = Deferred()
        reactor.callLater(0, d.callback, True)
        return d


    @inlineCallbacks
    def runCommand(self, command, error=False,
        script="calendarserver_command_gateway"):
        """
        Run the given command by feeding it as standard input to
        calendarserver_command_gateway in a subprocess.
        """

        if isinstance(command, unicode):
            command = command.encode("utf-8")

        sourceRoot = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        python = sys.executable
        script = os.path.join(sourceRoot, "bin", script)

        args = [python, script, "-f", self.configFileName]
        if error:
            args.append("--error")

        cwd = sourceRoot

        deferred = Deferred()
        reactor.spawnProcess(CapturingProcessProtocol(deferred, command), python, args, env=os.environ, path=cwd)
        output = yield deferred
        try:
            plist = readPlistFromString(output)
        except xml.parsers.expat.ExpatError, e:
            print("Error (%s) parsing (%s)" % (e, output))
            raise

        returnValue(plist)



class GatewayTestCase(RunCommandTestCase):

    @inlineCallbacks
    def test_getLocationAndResourceList(self):
        results = yield self.runCommand(command_getLocationAndResourceList)
        self.assertEquals(len(results["result"]), 20)


    @inlineCallbacks
    def test_getLocationList(self):
        results = yield self.runCommand(command_getLocationList)
        self.assertEquals(len(results["result"]), 10)


    @inlineCallbacks
    def test_getLocationAttributes(self):
        yield self.runCommand(command_createLocation)
        results = yield self.runCommand(command_getLocationAttributes)
        self.assertEquals(results["result"]["Capacity"], "40")
        self.assertEquals(results["result"]["Description"], "Test Description")
        self.assertEquals(results["result"]["RecordName"], ["createdlocation01"])
        self.assertEquals(results["result"]["RealName"],
            "Created Location 01 %s %s" % (unichr(208), u"\ud83d\udca3"))
        self.assertEquals(results["result"]["Comment"], "Test Comment")
        self.assertEquals(results["result"]["AutoSchedule"], True)
        self.assertEquals(results["result"]["AutoAcceptGroup"], "E5A6142C-4189-4E9E-90B0-9CD0268B314B")
        self.assertEquals(set(results["result"]["ReadProxies"]), set(['user03', 'user04']))
        self.assertEquals(set(results["result"]["WriteProxies"]), set(['user05', 'user06']))


    @inlineCallbacks
    def test_getResourceList(self):
        results = yield self.runCommand(command_getResourceList)
        self.assertEquals(len(results["result"]), 10)


    @inlineCallbacks
    def test_getResourceAttributes(self):
        yield self.runCommand(command_createResource)
        results = yield self.runCommand(command_getResourceAttributes)
        self.assertEquals(results["result"]["Comment"], "Test Comment")
        self.assertEquals(results["result"]["Type"], "Computer")
        self.assertEquals(set(results["result"]["ReadProxies"]), set(['user03', 'user04']))
        self.assertEquals(set(results["result"]["WriteProxies"]), set(['user05', 'user06']))


    @inlineCallbacks
    def test_createAddress(self):
        directory = getDirectory()

        record = directory.recordWithUID("C701069D-9CA1-4925-A1A9-5CD94767B74B")
        self.assertEquals(record, None)
        yield self.runCommand(command_createAddress)

        directory.flushCaches()

        record = directory.recordWithUID("C701069D-9CA1-4925-A1A9-5CD94767B74B")
        self.assertEquals(record.fullName.decode("utf-8"),
            "Created Address 01 %s %s" % (unichr(208), u"\ud83d\udca3"))

        self.assertNotEquals(record, None)

        self.assertEquals(record.extras["abbreviatedName"], "Addr1")
        self.assertEquals(record.extras["streetAddress"], "1 Infinite Loop\nCupertino, 95014\nCA")
        self.assertEquals(record.extras["geo"], "geo:37.331,-122.030")

        results = yield self.runCommand(command_getAddressList)
        self.assertEquals(len(results["result"]), 1)

        results = yield self.runCommand(command_getAddressAttributes)
        self.assertEquals(results["result"]["RealName"], u'Created Address 01 \xd0 \U0001f4a3')

        results = yield self.runCommand(command_setAddressAttributes)

        results = yield self.runCommand(command_getAddressAttributes)
        self.assertEquals(results["result"]["RealName"], u'Updated Address')
        self.assertEquals(results["result"]["StreetAddress"], u'Updated Street Address')
        self.assertEquals(results["result"]["Geo"], u'Updated Geo')
        
        results = yield self.runCommand(command_deleteAddress)

        results = yield self.runCommand(command_getAddressList)
        self.assertEquals(len(results["result"]), 0)


    @inlineCallbacks
    def test_createLocation(self):
        directory = getDirectory()

        record = directory.recordWithUID("836B1B66-2E9A-4F46-8B1C-3DD6772C20B2")
        self.assertEquals(record, None)
        yield self.runCommand(command_createLocation)

        directory.flushCaches()

        # This appears to be necessary in order for record.autoSchedule to
        # reflect the change prior to the directory record expiration
        augmentService = directory.serviceForRecordType(directory.recordType_locations).augmentService
        augmentService.refresh()

        record = directory.recordWithUID("836B1B66-2E9A-4F46-8B1C-3DD6772C20B2")
        self.assertEquals(record.fullName.decode("utf-8"),
            "Created Location 01 %s %s" % (unichr(208), u"\ud83d\udca3"))

        self.assertNotEquals(record, None)
        self.assertEquals(record.autoSchedule, True)

        self.assertEquals(record.extras["comment"], "Test Comment")
        self.assertEquals(record.extras["floor"], "First")
        self.assertEquals(record.extras["capacity"], "40")

        results = yield self.runCommand(command_getLocationAttributes)
        self.assertEquals(set(results["result"]["ReadProxies"]), set(['user03', 'user04']))
        self.assertEquals(set(results["result"]["WriteProxies"]), set(['user05', 'user06']))


    @inlineCallbacks
    def test_setLocationAttributes(self):
        directory = getDirectory()

        yield self.runCommand(command_createLocation)
        yield self.runCommand(command_setLocationAttributes)
        directory.flushCaches()

        # This appears to be necessary in order for record.autoSchedule to
        # reflect the change
        augmentService = directory.serviceForRecordType(directory.recordType_locations).augmentService
        augmentService.refresh()

        record = directory.recordWithUID("836B1B66-2E9A-4F46-8B1C-3DD6772C20B2")

        self.assertEquals(record.extras["comment"], "Updated Test Comment")
        self.assertEquals(record.extras["floor"], "Second")
        self.assertEquals(record.extras["capacity"], "41")
        self.assertEquals(record.extras["streetAddress"], "2 Infinite Loop\nCupertino, 95014\nCA")
        self.assertEquals(record.autoSchedule, True)
        self.assertEquals(record.autoAcceptGroup, "F5A6142C-4189-4E9E-90B0-9CD0268B314B")

        results = yield self.runCommand(command_getLocationAttributes)
        self.assertEquals(results["result"]["AutoSchedule"], True)
        self.assertEquals(results["result"]["AutoAcceptGroup"], "F5A6142C-4189-4E9E-90B0-9CD0268B314B")
        self.assertEquals(set(results["result"]["ReadProxies"]), set(['user03']))
        self.assertEquals(set(results["result"]["WriteProxies"]), set(['user05', 'user06', 'user07']))


    @inlineCallbacks
    def test_destroyLocation(self):
        directory = getDirectory()

        record = directory.recordWithUID("location01")
        self.assertNotEquals(record, None)

        yield self.runCommand(command_deleteLocation)

        directory.flushCaches()
        record = directory.recordWithUID("location01")
        self.assertEquals(record, None)


    @inlineCallbacks
    def test_createResource(self):
        directory = getDirectory()

        record = directory.recordWithUID("AF575A61-CFA6-49E1-A0F6-B5662C9D9801")
        self.assertEquals(record, None)

        yield self.runCommand(command_createResource)

        directory.flushCaches()
        record = directory.recordWithUID("AF575A61-CFA6-49E1-A0F6-B5662C9D9801")
        self.assertNotEquals(record, None)


    @inlineCallbacks
    def test_setResourceAttributes(self):
        directory = getDirectory()

        yield self.runCommand(command_createResource)
        directory.flushCaches()
        record = directory.recordWithUID("AF575A61-CFA6-49E1-A0F6-B5662C9D9801")
        self.assertEquals(record.fullName, "Laptop 1")

        yield self.runCommand(command_setResourceAttributes)

        directory.flushCaches()
        record = directory.recordWithUID("AF575A61-CFA6-49E1-A0F6-B5662C9D9801")
        self.assertEquals(record.fullName, "Updated Laptop 1")


    @inlineCallbacks
    def test_destroyResource(self):
        directory = getDirectory()

        record = directory.recordWithUID("resource01")
        self.assertNotEquals(record, None)

        yield self.runCommand(command_deleteResource)

        directory.flushCaches()
        record = directory.recordWithUID("resource01")
        self.assertEquals(record, None)


    @inlineCallbacks
    def test_addWriteProxy(self):
        results = yield self.runCommand(command_addWriteProxy)
        self.assertEquals(len(results["result"]["Proxies"]), 1)


    @inlineCallbacks
    def test_removeWriteProxy(self):
        yield self.runCommand(command_addWriteProxy)
        results = yield self.runCommand(command_removeWriteProxy)
        self.assertEquals(len(results["result"]["Proxies"]), 0)


    @inlineCallbacks
    def test_purgeOldEvents(self):
        results = yield self.runCommand(command_purgeOldEvents)
        self.assertEquals(results["result"]["EventsRemoved"], 0)
        self.assertEquals(results["result"]["RetainDays"], 42)
        results = yield self.runCommand(command_purgeOldEventsNoDays)
        self.assertEquals(results["result"]["RetainDays"], 365)

    @inlineCallbacks
    def test_readConfig(self):
        """
        Verify readConfig returns with only the writable keys
        """
        results = yield self.runCommand(command_readConfig,
            script="calendarserver_config")

        self.assertEquals(results["result"]["RedirectHTTPToHTTPS"], False)
        self.assertEquals(results["result"]["EnableSearchAddressBook"], False)
        self.assertEquals(results["result"]["EnableCalDAV"], True)
        self.assertEquals(results["result"]["EnableCardDAV"], True)
        self.assertEquals(results["result"]["EnableSSL"], False)
        self.assertEquals(results["result"]["DefaultLogLevel"], "warn")

        self.assertEquals(results["result"]["Notifications"]["Services"]["APNS"]["Enabled"], False)
        self.assertEquals(results["result"]["Notifications"]["Services"]["APNS"]["CalDAV"]["CertificatePath"], "/example/calendar.cer")

        # Verify not all keys are present, such as ServerRoot which is not writable
        self.assertFalse("ServerRoot" in results["result"])


    @inlineCallbacks
    def test_writeConfig(self):
        """
        Verify writeConfig updates the writable plist file only
        """
        results = yield self.runCommand(command_writeConfig,
            script="calendarserver_config")

        self.assertEquals(results["result"]["EnableCalDAV"], False)
        self.assertEquals(results["result"]["EnableCardDAV"], False)
        self.assertEquals(results["result"]["EnableSSL"], True)
        self.assertEquals(results["result"]["Notifications"]["Services"]["APNS"]["Enabled"], True)
        self.assertEquals(results["result"]["Notifications"]["Services"]["APNS"]["CalDAV"]["CertificatePath"], "/example/changed.cer")
        dataRoot = "Data/%s/%s" % (unichr(208), u"\ud83d\udca3")
        self.assertTrue(results["result"]["DataRoot"].endswith(dataRoot))

        # The static plist should still have EnableCalDAV = True
        staticPlist = plistlib.readPlist(self.configFileName)
        self.assertTrue(staticPlist["EnableCalDAV"])


command_addReadProxy = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>addReadProxy</string>
        <key>Principal</key>
        <string>locations:location01</string>
        <key>Proxy</key>
        <string>users:user03</string>
</dict>
</plist>
"""

command_addWriteProxy = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>addWriteProxy</string>
        <key>Principal</key>
        <string>locations:location01</string>
        <key>Proxy</key>
        <string>users:user01</string>
</dict>
</plist>
"""

command_createAddress = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>createAddress</string>
        <key>GeneratedUID</key>
        <string>C701069D-9CA1-4925-A1A9-5CD94767B74B</string>
        <key>RealName</key>
        <string>Created Address 01 %s %s</string>
        <key>AbbreviatedName</key>
        <string>Addr1</string>
        <key>RecordName</key>
        <array>
                <string>createdaddress01</string>
        </array>
        <key>StreetAddress</key>
        <string>1 Infinite Loop\nCupertino, 95014\nCA</string>
        <key>Geo</key>
        <string>geo:37.331,-122.030</string>
</dict>
</plist>
""" % (unichr(208), u"\ud83d\udca3")


command_createLocation = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>createLocation</string>
        <key>AutoSchedule</key>
        <true/>
        <key>AutoAcceptGroup</key>
        <string>E5A6142C-4189-4E9E-90B0-9CD0268B314B</string>
        <key>GeneratedUID</key>
        <string>836B1B66-2E9A-4F46-8B1C-3DD6772C20B2</string>
        <key>RealName</key>
        <string>Created Location 01 %s %s</string>
        <key>RecordName</key>
        <array>
                <string>createdlocation01</string>
        </array>
        <key>Comment</key>
        <string>Test Comment</string>
        <key>Description</key>
        <string>Test Description</string>
        <key>Floor</key>
        <string>First</string>
        <key>Capacity</key>
        <string>40</string>
        <key>AssociatedAddress</key>
        <string>C701069D-9CA1-4925-A1A9-5CD94767B74B</string>
        <key>ReadProxies</key>
        <array>
            <string>users:user03</string>
            <string>users:user04</string>
        </array>
        <key>WriteProxies</key>
        <array>
            <string>users:user05</string>
            <string>users:user06</string>
        </array>
</dict>
</plist>
""" % (unichr(208), u"\ud83d\udca3")


command_createResource = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>createResource</string>
        <key>AutoSchedule</key>
        <true/>
        <key>GeneratedUID</key>
        <string>AF575A61-CFA6-49E1-A0F6-B5662C9D9801</string>
        <key>RealName</key>
        <string>Laptop 1</string>
        <key>Comment</key>
        <string>Test Comment</string>
        <key>Description</key>
        <string>Test Description</string>
        <key>Type</key>
        <string>Computer</string>
        <key>RecordName</key>
        <array>
                <string>laptop1</string>
        </array>
        <key>ReadProxies</key>
        <array>
            <string>users:user03</string>
            <string>users:user04</string>
        </array>
        <key>WriteProxies</key>
        <array>
            <string>users:user05</string>
            <string>users:user06</string>
        </array>
</dict>
</plist>
"""

command_deleteLocation = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>deleteLocation</string>
        <key>GeneratedUID</key>
        <string>location01</string>
</dict>
</plist>
"""

command_deleteResource = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>deleteResource</string>
        <key>GeneratedUID</key>
        <string>resource01</string>
</dict>
</plist>
"""


command_deleteAddress = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>deleteAddress</string>
        <key>GeneratedUID</key>
        <string>C701069D-9CA1-4925-A1A9-5CD94767B74B</string>
</dict>
</plist>
"""

command_getLocationAndResourceList = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>getLocationAndResourceList</string>
</dict>
</plist>
"""

command_getLocationList = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>getLocationList</string>
</dict>
</plist>
"""

command_getResourceList = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>getResourceList</string>
</dict>
</plist>
"""

command_getAddressList = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>getAddressList</string>
</dict>
</plist>
"""


command_listReadProxies = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>listReadProxies</string>
        <key>Principal</key>
        <string>836B1B66-2E9A-4F46-8B1C-3DD6772C20B2</string>
</dict>
</plist>
"""

command_listWriteProxies = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>listWriteProxies</string>
        <key>Principal</key>
        <string>836B1B66-2E9A-4F46-8B1C-3DD6772C20B2</string>
</dict>
</plist>
"""

command_removeReadProxy = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>removeReadProxy</string>
        <key>Principal</key>
        <string>locations:location01</string>
        <key>Proxy</key>
        <string>users:user03</string>
</dict>
</plist>
"""

command_removeWriteProxy = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>removeWriteProxy</string>
        <key>Principal</key>
        <string>locations:location01</string>
        <key>Proxy</key>
        <string>users:user01</string>
</dict>
</plist>
"""

command_setLocationAttributes = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>setLocationAttributes</string>
        <key>AutoSchedule</key>
        <true/>
        <key>AutoAcceptGroup</key>
        <string>F5A6142C-4189-4E9E-90B0-9CD0268B314B</string>
        <key>GeneratedUID</key>
        <string>836B1B66-2E9A-4F46-8B1C-3DD6772C20B2</string>
        <key>RealName</key>
        <string>Updated Location 01</string>
        <key>RecordName</key>
        <array>
                <string>createdlocation01</string>
        </array>
        <key>Comment</key>
        <string>Updated Test Comment</string>
        <key>Description</key>
        <string>Updated Test Description</string>
        <key>Floor</key>
        <string>Second</string>
        <key>Capacity</key>
        <string>41</string>
        <key>StreetAddress</key>
        <string>2 Infinite Loop\nCupertino, 95014\nCA</string>
        <key>ReadProxies</key>
        <array>
            <string>users:user03</string>
        </array>
        <key>WriteProxies</key>
        <array>
            <string>users:user05</string>
            <string>users:user06</string>
            <string>users:user07</string>
        </array>
</dict>
</plist>
"""

command_getLocationAttributes = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>getLocationAttributes</string>
        <key>GeneratedUID</key>
        <string>836B1B66-2E9A-4F46-8B1C-3DD6772C20B2</string>
</dict>
</plist>
"""

command_getAddressAttributes = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>getAddressAttributes</string>
        <key>GeneratedUID</key>
        <string>C701069D-9CA1-4925-A1A9-5CD94767B74B</string>
</dict>
</plist>
"""

command_setAddressAttributes = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>setAddressAttributes</string>
        <key>GeneratedUID</key>
        <string>C701069D-9CA1-4925-A1A9-5CD94767B74B</string>
        <key>RealName</key>
        <string>Updated Address</string>
        <key>StreetAddress</key>
        <string>Updated Street Address</string>
        <key>Geo</key>
        <string>Updated Geo</string>

</dict>
</plist>
"""


command_setResourceAttributes = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>setResourceAttributes</string>
        <key>AutoSchedule</key>
        <false/>
        <key>GeneratedUID</key>
        <string>AF575A61-CFA6-49E1-A0F6-B5662C9D9801</string>
        <key>RealName</key>
        <string>Updated Laptop 1</string>
        <key>RecordName</key>
        <array>
                <string>laptop1</string>
        </array>
</dict>
</plist>
"""

command_getResourceAttributes = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>getResourceAttributes</string>
        <key>GeneratedUID</key>
        <string>AF575A61-CFA6-49E1-A0F6-B5662C9D9801</string>
</dict>
</plist>
"""

command_purgeOldEvents = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>purgeOldEvents</string>
        <key>RetainDays</key>
        <integer>42</integer>
</dict>
</plist>
"""

command_purgeOldEventsNoDays = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>purgeOldEvents</string>
</dict>
</plist>
"""

command_readConfig = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>readConfig</string>
</dict>
</plist>
"""

command_writeConfig = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>command</key>
        <string>writeConfig</string>
        <key>Values</key>
        <dict>
            <key>EnableCalDAV</key>
            <false/>
            <key>EnableCardDAV</key>
            <false/>
            <key>EnableSSL</key>
            <true/>
            <key>Notifications.Services.APNS.Enabled</key>
            <true/>
            <key>Notifications.Services.APNS.CalDAV.CertificatePath</key>
            <string>/example/changed.cer</string>
            <key>DataRoot</key>
            <string>Data/%s/%s</string>
        </dict>
</dict>
</plist>
""" % (unichr(208), u"\ud83d\udca3")
