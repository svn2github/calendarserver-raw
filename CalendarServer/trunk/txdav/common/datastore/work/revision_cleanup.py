# -*- test-case-name: txdav.common.datastore.work.test.test_revision_cleanup -*-
##
# Copyright (c) 2013-2014 Apple Inc. All rights reserved.
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
Remove old and unused REVISION rows
"""

from twext.enterprise.dal.record import fromTable
from twext.enterprise.dal.syntax import Delete, Select, Max
from twext.enterprise.jobqueue import WorkItem
from twext.python.log import Logger
from twisted.internet.defer import inlineCallbacks, returnValue
from twistedcaldav.config import config
from txdav.common.datastore.sql_tables import schema
import datetime

log = Logger()


class FindMinValidRevisionWork(WorkItem,
    fromTable(schema.FIND_MIN_VALID_REVISION_WORK)):

    group = "find_min_revision"

    @classmethod
    @inlineCallbacks
    def _schedule(cls, txn, seconds):
        notBefore = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
        log.debug("Scheduling find minimum valid revision work: %s" % (notBefore,))
        wp = yield txn.enqueue(cls, notBefore=notBefore)
        returnValue(wp)


    @inlineCallbacks
    def doWork(self):

        # Delete all other work items
        yield Delete(From=self.table, Where=None).on(self.transaction)

        # Get the minimum valid revision
        minValidRevision = int((yield self.transaction.calendarserverValue("MIN-VALID-REVISION")))

        # get max revision on table rows before dateLimit
        dateLimit = (datetime.datetime.utcnow() -
            datetime.timedelta(days=float(config.RevisionCleanup.SyncTokenLifetimeDays)))
        maxRevOlderThanDate = 0

        # TODO: Use one Select statement
        for table in (
            schema.CALENDAR_OBJECT_REVISIONS,
            schema.NOTIFICATION_OBJECT_REVISIONS,
            schema.ADDRESSBOOK_OBJECT_REVISIONS,
            schema.ABO_MEMBERS,
        ):
            revisionRows = yield Select(
                [Max(table.REVISION)],
                From=table,
                Where=(table.MODIFIED < dateLimit),
            ).on(self.transaction)

            if revisionRows:
                tableMaxRevision = revisionRows[0][0]
                if tableMaxRevision > maxRevOlderThanDate:
                    maxRevOlderThanDate = tableMaxRevision

        if maxRevOlderThanDate > minValidRevision:
            # save new min valid revision
            yield self.transaction.updateCalendarserverValue("MIN-VALID-REVISION", maxRevOlderThanDate)

            # Schedule revision cleanup
            yield RevisionCleanupWork._schedule(self.transaction, seconds=0)

        else:
            # Schedule next check
            yield FindMinValidRevisionWork._schedule(
                self.transaction,
                float(config.RevisionCleanup.CleanupPeriodDays) * 24 * 60 * 60
            )



class RevisionCleanupWork(WorkItem,
    fromTable(schema.REVISION_CLEANUP_WORK)):

    group = "group_revsion_cleanup"

    @classmethod
    @inlineCallbacks
    def _schedule(cls, txn, seconds):
        notBefore = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
        log.debug("Scheduling revision cleanup work: %s" % (notBefore,))
        wp = yield txn.enqueue(cls, notBefore=notBefore)
        returnValue(wp)


    @inlineCallbacks
    def doWork(self):

        # Delete all other work items
        yield Delete(From=self.table, Where=None).on(self.transaction)

        # Get the minimum valid revision
        minValidRevision = int((yield self.transaction.calendarserverValue("MIN-VALID-REVISION")))

        # delete revisions
        yield self.transaction.deleteRevisionsBefore(minValidRevision)

        # Schedule next update
        yield FindMinValidRevisionWork._schedule(
            self.transaction,
            float(config.RevisionCleanup.CleanupPeriodDays) * 24 * 60 * 60
        )



@inlineCallbacks
def scheduleFirstFindMinRevision(store, seconds):
    if config.RevisionCleanup.Enabled:
        txn = store.newTransaction(label="scheduleFirstFindMinRevision")
        wp = yield FindMinValidRevisionWork._schedule(txn, seconds)
        yield txn.commit()
        returnValue(wp)
    else:
        log.debug("Revision cleanup work disabled.")
