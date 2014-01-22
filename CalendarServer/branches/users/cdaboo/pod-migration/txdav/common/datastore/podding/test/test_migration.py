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
from twistedcaldav.ical import Component

class TestCalendarMigration(MultiStoreConduitTest):
    """
    Test that the migration api works for migration.
    """

    alarmhome1 = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT1M
END:VALARM
"""

    alarmhome2 = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT2M
END:VALARM
"""

    alarmhome3 = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT3M
END:VALARM
"""

    alarmhome4 = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT4M
END:VALARM
"""

    av1 = Component.fromString("""BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//calendarserver.org//Zonal//EN
BEGIN:VAVAILABILITY
ORGANIZER:mailto:user01@example.com
UID:1@example.com
DTSTAMP:20061005T133225Z
DTEND:20140101T000000Z
BEGIN:AVAILABLE
UID:1-1@example.com
DTSTAMP:20061005T133225Z
SUMMARY:Monday to Friday from 9:00 to 17:00
DTSTART:20130101T090000Z
DTEND:20130101T170000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR
""")

    alarmhome1_changed = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT5M
END:VALARM
"""

    alarmhome2_changed = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT6M
END:VALARM
"""

    alarmhome3_changed = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT7M
END:VALARM
"""

    alarmhome4_changed = """BEGIN:VALARM
ACTION:AUDIO
TRIGGER;RELATED=START:-PT8M
END:VALARM
"""

    av1_changed = Component.fromString("""BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//calendarserver.org//Zonal//EN
BEGIN:VAVAILABILITY
ORGANIZER:mailto:user01@example.com
UID:2@example.com
DTSTAMP:20061005T133225Z
DTEND:20140101T000000Z
BEGIN:AVAILABLE
UID:2-1@example.com
DTSTAMP:20061005T133225Z
SUMMARY:Monday to Friday from 9:00 to 17:00
DTSTART:20130101T090000Z
DTEND:20130101T170000Z
RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR
END:AVAILABLE
END:VAVAILABILITY
END:VCALENDAR
""")

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
        self.assertTrue(local_home is None)
        yield self.commit()

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        self.assertTrue(local_home is not None)
        self.assertTrue(not local_home.external())

        results = yield local_home.loadChildren()
        self.assertEqual(set([result.name() for result in results]), set(("calendar", "tasks", "inbox",)))


    @inlineCallbacks
    def test_step1_home_metadata(self):
        """
        Test that step1 works for sync'ing home metadata.
        """

        yield self._provision_remote()

        # Setup remote metadata
        txn = self.otherTransactionUnderTest()
        remote_home = yield self.homeUnderTest(txn, name="puser01")
        new_calendar = yield remote_home.createCalendarWithName("new_calendar")
        remote_home.setDefaultCalendar(new_calendar, "VEVENT")
        tasks_calendar = yield remote_home.calendarWithName("tasks")
        remote_home.setDefaultCalendar(tasks_calendar, "VTODO")
        yield remote_home.setDefaultAlarm(self.alarmhome1, True, True)
        yield remote_home.setDefaultAlarm(self.alarmhome2, True, False)
        yield remote_home.setDefaultAlarm(self.alarmhome3, False, True)
        yield remote_home.setDefaultAlarm(self.alarmhome4, False, False)
        yield remote_home.setAvailability(self.av1)
        yield self.otherCommit()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step1("puser01")

        # Verify local home is not visible to normal api calls
        local_home = yield self.homeUnderTest(name="puser01")
        self.assertTrue(local_home is None)
        yield self.commit()

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        self.assertTrue(local_home is not None)
        self.assertTrue(not local_home.external())

        results = yield local_home.loadChildren()
        results = dict([(child.name(), child) for child in results])
        self.assertEqual(set(results.keys()), set(("new_calendar", "calendar", "tasks", "inbox",)))

        # Verify metadata
        self.assertTrue(local_home.defaultCalendar("VEVENT"), results["new_calendar"].id())
        self.assertTrue(local_home.defaultCalendar("VTODO"), results["tasks"].id())

        self.assertTrue(local_home.getDefaultAlarm(True, True), self.alarmhome1)
        self.assertTrue(local_home.getDefaultAlarm(True, False), self.alarmhome2)
        self.assertTrue(local_home.getDefaultAlarm(False, True), self.alarmhome3)
        self.assertTrue(local_home.getDefaultAlarm(False, False), self.alarmhome4)

        self.assertTrue(local_home.getAvailability(), self.av1)


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
        Test that step2 works when there are no remote changes.
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
        Test that step2 syncs changes.
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


    @inlineCallbacks
    def test_step2_changes_home_metadata(self):
        """
        Test that step2 syncs home metadata changes.
        """

        yield self._provision_remote()

        # Setup remote metadata
        txn = self.otherTransactionUnderTest()
        remote_home = yield self.homeUnderTest(txn, name="puser01")
        event_calendar = yield remote_home.calendarWithName("calendar")
        remote_home.setDefaultCalendar(event_calendar, "VEVENT")
        tasks_calendar = yield remote_home.calendarWithName("tasks")
        remote_home.setDefaultCalendar(tasks_calendar, "VTODO")
        yield remote_home.setDefaultAlarm(self.alarmhome1, True, True)
        yield remote_home.setDefaultAlarm(self.alarmhome2, True, False)
        yield remote_home.setDefaultAlarm(self.alarmhome3, False, True)
        yield remote_home.setDefaultAlarm(self.alarmhome4, False, False)
        yield remote_home.setAvailability(self.av1)
        yield self.otherCommit()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step1("puser01")

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        self.assertTrue(local_home is not None)
        self.assertTrue(not local_home.external())

        results = yield local_home.loadChildren()
        results = dict([(child.name(), child) for child in results])
        self.assertEqual(set(results.keys()), set(("calendar", "tasks", "inbox",)))

        # Verify metadata
        self.assertTrue(local_home.defaultCalendar("VEVENT"), results["calendar"].id())
        self.assertTrue(local_home.defaultCalendar("VTODO"), results["tasks"].id())

        self.assertTrue(local_home.getDefaultAlarm(True, True), self.alarmhome1)
        self.assertTrue(local_home.getDefaultAlarm(True, False), self.alarmhome2)
        self.assertTrue(local_home.getDefaultAlarm(False, True), self.alarmhome3)
        self.assertTrue(local_home.getDefaultAlarm(False, False), self.alarmhome4)

        self.assertTrue(local_home.getAvailability(), self.av1)

        # Create new calendar and change metadata
        txn = self.otherTransactionUnderTest()
        remote_home = yield self.homeUnderTest(txn, name="puser01")
        new_calendar = yield remote_home.createCalendarWithName("new_calendar")
        remote_home.setDefaultCalendar(new_calendar, "VEVENT")
        tasks_calendar = yield remote_home.calendarWithName("tasks")
        remote_home.setDefaultCalendar(tasks_calendar, "VTODO")
        yield remote_home.setDefaultAlarm(self.alarmhome1_changed, True, True)
        yield remote_home.setDefaultAlarm(self.alarmhome2_changed, True, False)
        yield remote_home.setDefaultAlarm(self.alarmhome3_changed, False, True)
        yield remote_home.setDefaultAlarm(self.alarmhome4_changed, False, False)
        yield remote_home.setAvailability(self.av1_changed)

        yield self.otherCommit()

        migrator = MigrationController(self.storeUnderTest(), homeTypes=(ECALENDARTYPE,))
        yield migrator.step2("puser01")

        # Verify local migrating items exist
        local_home = yield self.homeUnderTest(name="puser01", migration=_MIGRATION_STATUS_MIGRATING)
        self.assertTrue(local_home is not None)
        self.assertTrue(not local_home.external())

        results = yield local_home.loadChildren()
        results = dict([(child.name(), child) for child in results])
        self.assertEqual(set(results.keys()), set(("new_calendar", "calendar", "tasks", "inbox",)))

        # Verify metadata
        self.assertTrue(local_home.defaultCalendar("VEVENT"), results["new_calendar"].id())
        self.assertTrue(local_home.defaultCalendar("VTODO"), results["tasks"].id())

        self.assertTrue(local_home.getDefaultAlarm(True, True), self.alarmhome1_changed)
        self.assertTrue(local_home.getDefaultAlarm(True, False), self.alarmhome2_changed)
        self.assertTrue(local_home.getDefaultAlarm(False, True), self.alarmhome3_changed)
        self.assertTrue(local_home.getDefaultAlarm(False, False), self.alarmhome4_changed)

        self.assertTrue(local_home.getAvailability(), self.av1_changed)
