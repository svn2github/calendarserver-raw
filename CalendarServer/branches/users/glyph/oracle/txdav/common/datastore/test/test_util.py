##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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
Tests for L{txdav.common.datastore.util}.
"""

from twext.python.filepath import CachingFilePath
from twext.web2.http_headers import MimeType

from twisted.application.service import Service, MultiService
from twisted.internet.defer import inlineCallbacks, Deferred, returnValue
from twisted.internet.protocol import Protocol
from twisted.trial.unittest import TestCase

from twistedcaldav.config import config
from twistedcaldav.memcacher import Memcacher

from txdav.caldav.datastore.test.common import CommonTests
from txdav.common.datastore.file import CommonDataStore
from txdav.common.datastore.test.util import theStoreBuilder, \
    populateCalendarsFrom, StubNotifierFactory
from txdav.common.datastore.util import UpgradeToDatabaseService

class HomeMigrationTests(TestCase):
    """
    Tests for L{UpgradeToDatabaseService}.
    """

    @inlineCallbacks
    def setUp(self):
        """
        Set up two stores to migrate between.
        """
        # Add some files to the file store.
        self.patch(config.Memcached.Pools.Default, "ClientEnabled", False)
        self.patch(config.Memcached.Pools.Default, "ServerEnabled", False)
        self.patch(Memcacher, "allowTestCache", True)
        self.filesPath = CachingFilePath(self.mktemp())
        self.filesPath.createDirectory()
        fileStore = self.fileStore = CommonDataStore(
            self.filesPath, StubNotifierFactory(), True, True
        )
        self.sqlStore = yield theStoreBuilder.buildStore(
            self, StubNotifierFactory()
        )
        subStarted = self.subStarted = Deferred()
        class StubService(Service, object):
            def startService(self):
                super(StubService, self).startService()
                subStarted.callback(None)
        self.stubService = StubService()
        self.topService = MultiService()
        self.upgrader = UpgradeToDatabaseService(
            fileStore, self.sqlStore, self.stubService
        )
        self.upgrader.setServiceParent(self.topService)
        requirements = CommonTests.requirements
        yield populateCalendarsFrom(requirements, fileStore)
        self.filesPath.child("calendars").child(
            "__uids__").child("ho").child("me").child("home1").child(
            ".some-extra-data").setContent("some extra data")


    @inlineCallbacks
    def test_upgradeCalendarHomes(self):
        """
        L{UpgradeToDatabaseService.startService} will do the upgrade, then
        start its dependent service by adding it to its service hierarchy.
        """
        self.topService.startService()
        yield self.subStarted
        self.assertEquals(self.stubService.running, True)
        txn = self.sqlStore.newTransaction()
        self.addCleanup(txn.commit)
        for uid in CommonTests.requirements:
            if CommonTests.requirements[uid] is not None:
                self.assertNotIdentical(
                    None, (yield txn.calendarHomeWithUID(uid))
                )
        # Un-migrated data should be preserved.
        self.assertEquals(self.filesPath.child("calendars-migrated").child(
            "__uids__").child("ho").child("me").child("home1").child(
                ".some-extra-data").getContent(),
                "some extra data"
        )
        
        # Want metadata preserved
        home = (yield txn.calendarHomeWithUID("home1"))
        calendar = (yield home.calendarWithName("calendar_1"))
        for name, metadata in (
            ("1.ics", CommonTests.metadata1),
            ("2.ics", CommonTests.metadata2),
            ("3.ics", CommonTests.metadata3),
        ):
            object = (yield calendar.calendarObjectWithName(name))
            self.assertEquals(object.getMetadata(), metadata)


    @inlineCallbacks
    def test_upgradeExistingHome(self):
        """
        L{UpgradeToDatabaseService.startService} will skip migrating existing
        homes.
        """
        startTxn = self.sqlStore.newTransaction("populate empty sample")
        yield startTxn.calendarHomeWithUID("home1", create=True)
        yield startTxn.commit()
        self.topService.startService()
        yield self.subStarted
        vrfyTxn = self.sqlStore.newTransaction("verify sample still empty")
        self.addCleanup(vrfyTxn.commit)
        home = yield vrfyTxn.calendarHomeWithUID("home1")
        # The default calendar is still there.
        self.assertNotIdentical(None, (yield home.calendarWithName("calendar")))
        # The migrated calendar isn't.
        self.assertIdentical(None, (yield home.calendarWithName("calendar_1")))


    @inlineCallbacks
    def test_upgradeAttachments(self):
        """
        L{UpgradeToDatabaseService.startService} upgrades calendar attachments
        as well.
        """

        txn = self.fileStore.newTransaction()
        committed = []
        def maybeCommit():
            if not committed:
                committed.append(True)
                return txn.commit()
        self.addCleanup(maybeCommit)

        @inlineCallbacks
        def getSampleObj():
            home = (yield txn.calendarHomeWithUID("home1"))
            calendar = (yield home.calendarWithName("calendar_1"))
            object = (yield calendar.calendarObjectWithName("1.ics"))
            returnValue(object)

        inObject = yield getSampleObj()
        someAttachmentName = "some-attachment"
        someAttachmentType = MimeType.fromString("application/x-custom-type")
        attachment = yield inObject.createAttachmentWithName(
            someAttachmentName,
        )
        transport = attachment.store(someAttachmentType)
        someAttachmentData = "Here is some data for your attachment, enjoy."
        transport.write(someAttachmentData)
        yield transport.loseConnection()
        yield maybeCommit()
        self.topService.startService()
        yield self.subStarted
        committed = []
        txn = self.sqlStore.newTransaction()
        outObject = yield getSampleObj()
        outAttachment = yield outObject.attachmentWithName(someAttachmentName)
        allDone = Deferred()
        class SimpleProto(Protocol):
            data = ''
            def dataReceived(self, data):
                self.data += data
            def connectionLost(self, reason):
                allDone.callback(self.data)
        self.assertEquals(outAttachment.contentType(), someAttachmentType)
        outAttachment.retrieve(SimpleProto())
        allData = yield allDone
        self.assertEquals(allData, someAttachmentData)


