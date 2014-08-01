# -*- test-case-name: txdav.who.test.test_groups -*-
##
# Copyright (c) 2013 Apple Inc. All rights reserved.
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
Group membership caching
"""

from pycalendar.datetime import DateTime
from pycalendar.duration import Duration
from twext.enterprise.dal.record import fromTable
from twext.enterprise.dal.syntax import Delete, Select, Parameter
from twext.enterprise.jobqueue import WorkItem, RegeneratingWorkItem
from twext.python.log import Logger
from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twistedcaldav.config import config
from txdav.caldav.datastore.scheduling.icalsplitter import iCalSplitter
from txdav.caldav.datastore.sql import CalendarStoreFeatures, ComponentUpdateState
from txdav.common.datastore.sql_tables import schema, _BIND_MODE_OWN
import datetime

log = Logger()


class GroupCacherPollingWork(
    RegeneratingWorkItem,
    fromTable(schema.GROUP_CACHER_POLLING_WORK)
):

    group = "group_cacher_polling"

    @classmethod
    def initialSchedule(cls, store, seconds):
        def _enqueue(txn):
            return GroupCacherPollingWork.reschedule(txn, seconds)

        if config.InboxCleanup.Enabled:
            return store.inTransaction("GroupCacherPollingWork.initialSchedule", _enqueue)
        else:
            return succeed(None)


    def regenerateInterval(self):
        """
        Return the interval in seconds between regenerating instances.
        """
        groupCacher = getattr(self.transaction, "_groupCacher", None)
        return groupCacher.updateSeconds if groupCacher else 10


    @inlineCallbacks
    def doWork(self):

        groupCacher = getattr(self.transaction, "_groupCacher", None)
        if groupCacher is not None:

            # New implementation
            try:
                yield groupCacher.update(self.transaction)
            except Exception, e:
                log.error(
                    "Failed to update new group membership cache ({error})",
                    error=e
                )



class GroupRefreshWork(WorkItem, fromTable(schema.GROUP_REFRESH_WORK)):

    group = property(lambda self: (self.table.GROUP_UID == self.groupUid))

    @inlineCallbacks
    def doWork(self):
        # Delete all other work items for this group
        yield Delete(
            From=self.table,
            Where=self.group,
        ).on(self.transaction)

        groupCacher = getattr(self.transaction, "_groupCacher", None)
        if groupCacher is not None:

            try:
                yield groupCacher.refreshGroup(
                    self.transaction, self.groupUid.decode("utf-8")
                )
            except Exception, e:
                log.error(
                    "Failed to refresh group {group} {err}",
                    group=self.groupUid, err=e
                )

        else:
            log.debug(
                "Rescheduling group refresh for {group}: {when}",
                group=self.groupUid,
                when=datetime.datetime.utcnow() + datetime.timedelta(seconds=10)
            )
            yield self.reschedule(self.transaction, 10, groupUID=self.groupUid)



class GroupAttendeeReconciliationWork(
    WorkItem, fromTable(schema.GROUP_ATTENDEE_RECONCILE_WORK)
):

    group = property(
        lambda self: (self.table.RESOURCE_ID == self.resourceID)
    )


    @inlineCallbacks
    def doWork(self):

        # Delete all other work items for this event
        yield Delete(
            From=self.table,
            Where=self.group,
        ).on(self.transaction)

        # get db object
        calendarObject = yield CalendarStoreFeatures(
            self.transaction._store
        ).calendarObjectWithID(
            self.transaction, self.resourceID
        )
        component = yield calendarObject.componentForUser()

        # Change a copy of the original, as we need the original cached on the resource
        # so we can do a diff to test implicit scheduling changes
        component = component.duplicate()

        # sync group attendees
        if (yield calendarObject.reconcileGroupAttendees(component)):

            # group attendees in event have changed
            if (component.masterComponent() is None or not component.isRecurring()):

                # skip non-recurring old events, no instances
                if (
                    yield calendarObject.removeOldEventGroupLink(
                        component,
                        instances=None,
                        inserting=False,
                        txn=self.transaction
                    )
                ):
                    returnValue(None)
            else:
                # skip recurring old events
                expand = (DateTime.getToday() +
                          Duration(days=config.FreeBusyIndexExpandAheadDays))

                if config.FreeBusyIndexLowerLimitDays:
                    truncateLowerLimit = DateTime.getToday()
                    truncateLowerLimit.offsetDay(-config.FreeBusyIndexLowerLimitDays)
                else:
                    truncateLowerLimit = None

                instances = component.expandTimeRanges(
                    expand,
                    lowerLimit=truncateLowerLimit,
                    ignoreInvalidInstances=True
                )
                if (
                    yield calendarObject.removeOldEventGroupLink(
                        component,
                        instances=instances,
                        inserting=False,
                        txn=self.transaction
                    )
                ):
                    returnValue(None)

                # split spanning events and only update present-future split result
                splitter = iCalSplitter(0, 1)
                break_point = DateTime.getToday() - Duration(seconds=config.GroupAttendees.UpdateOldEventLimitSeconds)
                rid = splitter.whereSplit(component, break_point=break_point)
                if rid is not None:
                    yield calendarObject.split(onlyThis=True, rid=rid)

                    # remove group link to ensure update (update to unknown hash would work too)
                    # FIXME: its possible that more than one group id gets updated during this single work item, so we
                    # need to make sure that ALL the group_id's are removed by this query.
                    ga = schema.GROUP_ATTENDEE
                    yield Delete(
                        From=ga,
                        Where=(ga.RESOURCE_ID == self.resourceID).And(
                            ga.GROUP_ID == self.groupID
                        )
                    ).on(self.transaction)

                    # update group attendee in remaining component
                    component = yield calendarObject.componentForUser()
                    component = component.duplicate()
                    change = yield calendarObject.reconcileGroupAttendees(component)
                    assert change
                    yield calendarObject._setComponentInternal(component, False, ComponentUpdateState.SPLIT_OWNER)
                    returnValue(None)

            yield calendarObject.setComponent(component)



class GroupShareeReconciliationWork(
    WorkItem, fromTable(schema.GROUP_SHAREE_RECONCILE_WORK)
):

    group = property(
        lambda self: (self.table.CALENDAR_ID == self.calendarID)
    )


    @inlineCallbacks
    def doWork(self):

        # Delete all other work items for this event
        yield Delete(
            From=self.table,
            Where=self.group,
        ).on(self.transaction)

        bind = schema.CALENDAR_BIND
        rows = yield Select(
            [bind.HOME_RESOURCE_ID],
            From=bind,
            Where=(bind.CALENDAR_RESOURCE_ID == self.calendarID).And(
                bind.BIND_MODE == _BIND_MODE_OWN
            ),
        ).on(self.transaction)
        if rows:
            homeID = rows[0][0]
            home = yield self.transaction.calendarHomeWithResourceID(homeID)
            calendar = yield home.childWithID(self.calendarID)
            groupUID = ((yield self.transaction.groupByID(self.groupID)))[0]
            yield calendar.reconcileGroupSharee(groupUID)



def diffAssignments(old, new):
    """
    Compare two proxy assignment lists and return their differences in the form
    of two lists -- one for added/updated assignments, and one for removed
    assignments.

    @param old: dictionary of delegator: (readGroupUID, writeGroupUID)
    @type old: C{dict}

    @param new: dictionary of delegator: (readGroupUID, writeGroupUID)
    @type new: C{dict}

    @return: Tuple of two lists; the first list contains tuples of (delegator,
        (readGroupUID, writeGroupUID)), and represents all the new or updated
        assignments.  The second list contains all the delegators which used to
        have a delegate but don't anymore.
    """
    changed = []
    removed = []
    for key in old.iterkeys():
        if key not in new:
            removed.append(key)
        else:
            if old[key] != new[key]:
                changed.append((key, new[key]))
    for key in new.iterkeys():
        if key not in old:
            changed.append((key, new[key]))
    return changed, removed



class GroupCacher(object):
    log = Logger()


    def __init__(
        self, directory,
        updateSeconds=600,
        useExternalProxies=False,
        externalProxiesSource=None
    ):
        self.directory = directory
        self.useExternalProxies = useExternalProxies
        if useExternalProxies and externalProxiesSource is None:
            externalProxiesSource = self.directory.getExternalProxyAssignments
        self.externalProxiesSource = externalProxiesSource
        self.updateSeconds = updateSeconds


    @inlineCallbacks
    def update(self, txn):
        # TODO
        # Pull in external delegate assignments and stick in delegate db
        # if self.useExternalProxies:
        #     externalAssignments = (yield self.externalProxiesSource())
        # yield self.applyExternalAssignments(txn, externalAssignments)

        # Figure out which groups matter
        groupUIDs = yield self.groupsToRefresh(txn)
        self.log.debug(
            "Groups to refresh: {g}", g=groupUIDs
        )

        gr = schema.GROUPS
        if config.AutomaticPurging.Enabled and groupUIDs:
            # remove unused groups and groups that have not been seen in a while
            dateLimit = (
                datetime.datetime.utcnow() -
                datetime.timedelta(seconds=float(config.AutomaticPurging.GroupPurgeIntervalSeconds))
            )
            rows = yield Delete(
                From=gr,
                Where=(
                    (gr.EXTANT == 0).And(gr.MODIFIED < dateLimit)
                ).Or(
                    gr.GROUP_UID.NotIn(
                        Parameter("groupUIDs", len(groupUIDs))
                    )
                ) if groupUIDs else None,
                Return=[gr.GROUP_UID]
            ).on(txn, groupUIDs=groupUIDs)
        else:
            # remove unused groups
            rows = yield Delete(
                From=gr,
                Where=gr.GROUP_UID.NotIn(
                    Parameter("groupUIDs", len(groupUIDs))
                ) if groupUIDs else None,
                Return=[gr.GROUP_UID]
            ).on(txn, groupUIDs=groupUIDs)
        deletedGroupUIDs = [row[0] for row in rows]
        if deletedGroupUIDs:
            self.log.debug("Deleted old or unused groups {d}", d=deletedGroupUIDs)

        # For each of those groups, create a per-group refresh work item
        for groupUID in set(groupUIDs) - set(deletedGroupUIDs):
            self.log.debug("Enqueuing group refresh for {u}", u=groupUID)
            yield GroupRefreshWork.reschedule(txn, 0, groupUid=groupUID)


    @inlineCallbacks
    def applyExternalAssignments(self, txn, newAssignments):

        oldAssignments = (yield txn.externalDelegates())

        # external assignments is of the form:
        # { delegatorUID: (readDelegateGroupUID, writeDelegateGroupUID),
        # }

        changed, removed = diffAssignments(oldAssignments, newAssignments)
        if changed:
            for (
                delegatorUID, (readDelegateUID, writeDelegateUID)
            ) in changed:
                readDelegateGroupID = writeDelegateGroupID = None
                if readDelegateUID:
                    (
                        readDelegateGroupID, _ignore_name, _ignore_hash,
                        _ignore_modified, _ignore_extant
                    ) = (
                        yield txn.groupByUID(readDelegateUID)
                    )
                if writeDelegateUID:
                    (
                        writeDelegateGroupID, _ignore_name, _ignore_hash,
                        _ignore_modified, _ignore_extant
                    ) = (
                        yield txn.groupByUID(writeDelegateUID)
                    )
                yield txn.assignExternalDelegates(
                    delegatorUID, readDelegateGroupID, writeDelegateGroupID,
                    readDelegateUID, writeDelegateUID
                )
        if removed:
            for delegatorUID in removed:
                yield txn.assignExternalDelegates(
                    delegatorUID, None, None, None, None
                )


    @inlineCallbacks
    def refreshGroup(self, txn, groupUID):
        """
            Does the work of a per-group refresh work item
            Faults in the flattened membership of a group, as UIDs
            and updates the GROUP_MEMBERSHIP table
            WorkProposal is returned for tests
        """
        self.log.debug("Faulting in group: {g}", g=groupUID)
        record = (yield self.directory.recordWithUID(groupUID))
        if record is None:
            # the group has disappeared from the directory
            self.log.info("Group is missing: {g}", g=groupUID)
        else:
            self.log.debug("Got group record: {u}", u=record.uid)

        (
            groupID, cachedName, cachedMembershipHash, _ignore_modified,
            cachedExtant
        ) = yield txn.groupByUID(
            groupUID,
            create=(record is not None)
        )

        if groupID:
            membershipChanged = yield txn.refreshGroup(
                groupUID, record, groupID,
                cachedName, cachedMembershipHash, cachedExtant
            )

            if membershipChanged:
                wpsAttendee = yield self.scheduleGroupAttendeeReconciliations(txn, groupID)
                wpsShareee = yield self.scheduleGroupShareeReconciliations(txn, groupID)
                returnValue(wpsAttendee + wpsShareee)

        returnValue(tuple())


    def synchronizeMembers(self, txn, groupID, newMemberUIDs):
        return txn.synchronizeMembers(groupID, newMemberUIDs)


    def cachedMembers(self, txn, groupID):
        """
        The members of the given group as recorded in the db
        """
        return txn.groupMembers(groupID)


    def cachedGroupsFor(self, txn, uid):
        """
        The UIDs of the groups the uid is a member of
        """
        return txn.groupUIDsFor(uid)


    @inlineCallbacks
    def scheduleGroupAttendeeReconciliations(self, txn, groupID):
        """
        Find all events who have this groupID as an attendee and create
        work items for them.
        returns: WorkProposal
        """
        ga = schema.GROUP_ATTENDEE
        rows = yield Select(
            [ga.RESOURCE_ID, ],
            From=ga,
            Where=ga.GROUP_ID == groupID,
        ).on(txn)

        wps = []
        for [eventID] in rows:
            wp = yield GroupAttendeeReconciliationWork.reschedule(
                txn,
                seconds=float(config.GroupAttendees.ReconciliationDelaySeconds),
                resourceID=eventID,
                groupID=groupID,
            )
            wps.append(wp)
        returnValue(tuple(wps))


    @inlineCallbacks
    def scheduleGroupShareeReconciliations(self, txn, groupID):
        """
        Find all calendars who have shared to this groupID and create
        work items for them.
        returns: WorkProposal
        """
        gs = schema.GROUP_SHAREE
        rows = yield Select(
            [gs.CALENDAR_ID, ],
            From=gs,
            Where=gs.GROUP_ID == groupID,
        ).on(txn)

        wps = []
        for [calendarID] in rows:
            wp = yield GroupShareeReconciliationWork.reschedule(
                txn,
                seconds=float(config.Sharing.Calendars.Groups.ReconciliationDelaySeconds),
                calendarID=calendarID,
                groupID=groupID,
            )
            wps.append(wp)
        returnValue(tuple(wps))


    @inlineCallbacks
    def groupsToRefresh(self, txn):
        delegatedUIDs = frozenset((yield txn.allGroupDelegates()))
        self.log.info(
            "There are {count} group delegates", count=len(delegatedUIDs)
        )

        # Get groupUIDs for all group attendees
        ga = schema.GROUP_ATTENDEE
        gr = schema.GROUPS
        rows = yield Select(
            [gr.GROUP_UID],
            From=gr,
            Where=gr.GROUP_ID.In(
                Select(
                    [ga.GROUP_ID],
                    From=ga,
                    Distinct=True
                )
            )
        ).on(txn)
        attendeeGroupUIDs = frozenset([row[0] for row in rows])
        self.log.info(
            "There are {count} group attendees", count=len(attendeeGroupUIDs)
        )

        # Get groupUIDs for all group shares
        gs = schema.GROUP_SHAREE
        gr = schema.GROUPS
        rows = yield Select(
            [gr.GROUP_UID],
            From=gr,
            Where=gr.GROUP_ID.In(
                Select(
                    [gs.GROUP_ID],
                    From=gs,
                    Distinct=True
                )
            )
        ).on(txn)
        shareeGroupUIDs = frozenset([row[0] for row in rows])
        self.log.info(
            "There are {count} group sharees", count=len(shareeGroupUIDs)
        )

        returnValue(frozenset(delegatedUIDs | attendeeGroupUIDs | shareeGroupUIDs))
