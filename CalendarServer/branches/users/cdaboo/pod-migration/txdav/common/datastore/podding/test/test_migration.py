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

from twisted.internet.defer import inlineCallbacks

from txdav.common.datastore.podding.test.util import MultiStoreConduitTest
from txdav.common.datastore.sql_tables import _MIGRATION_STATUS_MIGRATING
from txdav.common.datastore.podding.migration import MigrationController, \
    UserAlreadyBeingMigrated
from txdav.common.datastore.sql import ECALENDARTYPE

class TestCalendarMigration(MultiStoreConduitTest):
    """
    Test that the migration api works for migration.
    """

    @inlineCallbacks
    def _provision_remote(self):
        """
        Provision the user account on the remote pod.
        """
        yield self.homeUnderTest(txn=self.otherTransactionUnderTest(), name="puser01", create=True)
        yield self.addressbookHomeUnderTest(txn=self.otherTransactionUnderTest(), name="puser01", create=True)
        yield self.otherCommit()


    @inlineCallbacks
    def test_step1_ok(self):
        """
        Test that step1 works.
        """

        yield self._provision_remote()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step1("puser01")

        # Verify local home is not visible to normal api calls
        local_home = yield self.homeUnderTest(name="puser01")
        self.assertTrue(local_home is not None)
        self.assertTrue(local_home.external())
        yield self.commit()

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        self.assertTrue(local_home is not None)
        self.assertTrue(not local_home.external())

        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("calendar", "tasks", "inbox",)))


    @inlineCallbacks
    def test_step1_twice(self):
        """
        Test that step1 fails a second time.
        """

        yield self._provision_remote()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step1("puser01")

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        self.assertTrue(local_home is not None)
        self.assertTrue(not local_home.external())
        yield self.commit()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield self.failUnlessFailure(migrator.step1("puser01"), UserAlreadyBeingMigrated)


    @inlineCallbacks
    def test_step2_no_change(self):
        """
        Test that step1 fails a second time.
        """

        yield self._provision_remote()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step1("puser01")

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("calendar", "tasks", "inbox",)))
        rids = dict([(result.id(), result.external_id()) for result in results])

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step2("puser01")

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("calendar", "tasks", "inbox",)))
        rids2 = dict([(result.id(), result.external_id()) for result in results])
        self.assertEqual(rids, rids2)


    @inlineCallbacks
    def test_step2_changes(self):
        """
        Test that step1 fails a second time.
        """

        yield self._provision_remote()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step1("puser01")

        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("calendar", "tasks", "inbox",)))
        rids = dict([(result.id(), result.external_id()) for result in results])

        # Create new calendar
        txn = self.otherTransactionUnderTest()
        remote_home = yield self.homeUnderTest(txn, name="puser01")
        new_calendar = yield remote_home.createCalendarWithName("new_calendar")
        external_id = new_calendar.id()
        yield self.otherCommit()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step2("puser01")

        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        new_calendar = yield local_home.calendarWithName("new_calendar")
        rids[new_calendar.id()] = external_id

        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("new_calendar", "calendar", "tasks", "inbox",)))
        rids2 = dict([(result.id(), result.external_id()) for result in results])
        self.assertEqual(rids, rids2)

        # Remove old calendar
        txn = self.otherTransactionUnderTest()
        remote_home = yield self.homeUnderTest(txn, name="puser01")
        old_calendar = yield remote_home.calendarWithName("calendar")
        external_id = old_calendar.id()
        yield old_calendar.remove()
        del rids[dict([(v, k) for k, v in rids.items()])[external_id]]
        yield self.otherCommit()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step2("puser01")

        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("new_calendar", "tasks", "inbox",)))
        rids2 = dict([(result.id(), result.external_id()) for result in results])
        self.assertEqual(rids, rids2)
        tasks = yield local_home.calendarWithName("tasks")
        self.assertFalse(tasks.isUsedForFreeBusy())

        # Add, remove, rename, prop change
        txn = self.otherTransactionUnderTest()
        remote_home = yield self.homeUnderTest(txn, name="puser01")

        new_calendar = yield remote_home.createCalendarWithName("new_calendar2")
        external_id2 = new_calendar.id()

        old_calendar = yield remote_home.calendarWithName("new_calendar")
        external_id = old_calendar.id()
        yield old_calendar.remove()
        del rids[dict([(v, k) for k, v in rids.items()])[external_id]]

        tasks = yield remote_home.calendarWithName("tasks")
        yield tasks.rename("todos")
        yield tasks.setUsedForFreeBusy(True)

        yield self.otherCommit()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step2("puser01")

        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        new_calendar = yield local_home.calendarWithName("new_calendar2")
        rids[new_calendar.id()] = external_id2

        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("new_calendar2", "todos", "inbox",)))
        rids2 = dict([(result.id(), result.external_id()) for result in results])
        self.assertEqual(rids, rids2)
        tasks = yield local_home.calendarWithName("todos")
        self.assertTrue(tasks.isUsedForFreeBusy())
