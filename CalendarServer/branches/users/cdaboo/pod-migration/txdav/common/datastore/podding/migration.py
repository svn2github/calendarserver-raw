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
from txdav.common.icommondatastore import CommonStoreError
from twisted.internet.defer import inlineCallbacks
from txdav.common.datastore.sql_tables import _HOME_STATUS_NORMAL, \
    _MIGRATION_STATUS_MIGRATING, _MIGRATION_STATUS_MIGRATED, \
    _MIGRATION_STATUS_NONE

"""
Support for cross-pod migration of users.

Migration will be divided into four steps:

1. Initial data-only sync.
    This will sync the actual CommonObjectResource data and owned collection bind information
    from the source pod to the destination pod. This must be able to execute whilst the
    service is running. The sync'd data will be store on the destination pod under an
    "inactive" home - i.e., one that can never be accessed via public store apis.

2. Incremental data-only sync.
    Will the update destination's previous sync'd state to match the current source state.
    This can be run multiple times prior to step 3 - with the last one ideally right before
    step 3 is done. This must be able to execute whilst the service is running.

3. Migration commit.
    1. The service is shut down.
    2. A final incremental data-sync is done.
    3. A sync/update of sharing state is done on both the source and destination (this may
        require creating external shares on the source for sharee's who remain on that pod,
        and converting external shares to internal ones on the destination pod for sharee's
        that are now on the same pod as the migrated owner).
    4. The source home is marked as inactive.
    5. The destination home is marked as active.
    6. The service is brought back up.

4. Data clean-up.
    The inactive home and associated owned data on the source pod is removed. This must be
    able to execute whilst the service is running.

X. A step that can be used any time prior to step 3 that stops the current migration. That
    should simply involve removing the local inactive homes. There are no changes on the old
    pod until step 3.

During migration we will create an "inactive" set of home collections for the user being
migrated on their new pod. An "inactive" home is one where the MIGRATION value is non-zero.
There will be two types - one for use during migration, and one for use
after migration, with the later being used to mark the data on the old pod as inactive
prior to deletion (step 4).

The migration process will be driven by calls on the new pod, which will then use cross-pod
request to sync data and trigger state changes on the old pod.

"""

class UserAlreadyOnThisPod(CommonStoreError):
    pass



class UserAlreadyBeingMigrated(CommonStoreError):
    pass



class UserNotAlreadyBeingMigrated(CommonStoreError):
    pass



class MigrationController(object):
    """
    Class that manages each of the four steps. Will assume that this is being run on the new pod,
    pulling data for a user whose directory record still points to the old pod for the first three
    steps.

    TODO: For now this only works with calendars.
    """

    def __init__(self, store, homeTypes=None):
        self.store = store
        self.homeTypes = homeTypes


    def migrationTypes(self, txn):
        return txn.homeTypes if self.homeTypes is None else self.homeTypes


    @inlineCallbacks
    def step1(self, user):
        """
        Carry out step 1 migration: full sync of user data to this pod.

        @param user: GUID of the user to migrate
        @type user: C{str}
        """

        # Validity checks
        self.verifyNonLocalUser(user)

        txn = self.store.newTransaction()

        # Can't already have a migrating home prior to step 1
        for home_type in self.migrationTypes(txn):
            home = yield txn.homeWithUID(home_type, user, create=False, migration=_MIGRATION_STATUS_MIGRATING)
            if home is not None:
                raise UserAlreadyBeingMigrated("Inactive {} home exists".format(txn.homeClass(home_type)))
        home = yield txn.notificationsWithUID(user, create=False, migration=_MIGRATION_STATUS_MIGRATING)
        if home is not None:
            raise UserAlreadyBeingMigrated("Inactive notifications exists")

        # Can't already have a migrated homes prior to step 1 - i.e., a step 4 is
        # still in progress on this pod. We can't migrate the user back until that is done.
        for home_type in self.migrationTypes(txn):
            home = yield txn.homeWithUID(home_type, user, create=False, migration=_MIGRATION_STATUS_MIGRATED)
            if home is not None:
                raise UserAlreadyBeingMigrated("Inactive {} home exists".format(txn.homeClass(home_type)))
        home = yield txn.notificationsWithUID(user, create=False, migration=_MIGRATION_STATUS_MIGRATED)
        if home is not None:
            raise UserAlreadyBeingMigrated("Inactive notifications exists")

        # Create the new invalid homes
        for home_type in self.migrationTypes(txn):
            home = yield txn.homeWithUID(home_type, user, create=True, migration=_MIGRATION_STATUS_MIGRATING)
        home = yield txn.notificationsWithUID(user, create=True, migration=_MIGRATION_STATUS_MIGRATING)
        yield txn.commit()

        # Now we do a full sync - but in fact we can simply do the incremental sync,
        # however that will get everything we need for step 1
        yield self.syncHomes(user)


    @inlineCallbacks
    def step2(self, user):
        """
        Carry out step 2 migration: incremental sync of user data to this pod.

        @param user: GUID of the user to migrate
        @type user: C{str}
        """

        # Validity checks
        self.verifyNonLocalUser(user)

        # Must already have a migrating home prior to step 2
        txn = self.store.newTransaction()
        yield self.validMigratingUser(txn, user)
        yield txn.commit()

        # Now make each home sync
        yield self.syncHomes(user)


    @inlineCallbacks
    def step3(self, user):
        """
        Carry out step 2 migration: incremental sync of user data to this pod.

        @param user: GUID of the user to migrate
        @type user: C{str}
        """

        # Validity checks
        self.verifyNonLocalUser(user)

        # Must already have a migrating home prior to step 3
        txn = self.store.newTransaction()
        yield self.validMigratingUser(txn, user)
        yield txn.commit()

        # Step 3.2 Now make each home sync
        yield self.syncHomes(user, final=True)

        # Up to this point everything has been non-destructive in that all the existing data is intact.
        # After this point we will start changing the existing data and we really want those changes to
        # be consistent across the pods (i.e., transactional). There shouldn't be a lot of SQL changes
        # that need to be done so we can do this all in one transaction on the local pod. Need to figure
        # out how to recover the remote pod, because we can't keep transactions open across multiple
        # cross-pod calls.

        # Step 3.3 Sync the sharing state
        yield self.syncSharingState(user)

        # Step 3.4 Deactivate user on old pod
        yield self.deactivateUserOnPod(user)

        # Step 3.5 Activate user on new pod
        yield self.activateUserOnPod(user)


    @inlineCallbacks
    def syncHomes(self, user, final=False):
        """
        Synchronize the contents of the user home from their current pod to this one. If L{final} is C{False},
        treat this as a step 1/step 2 full/incremental sync. If L{final} is C{True}, then this is the step 3
        sync where we need to sync additional meta-data and the notifications collection content.

        @param user: GUID of the user to migrate
        @type user: C{str}
        @param final: if C{True}, indicates if this is the step 3 sync
        @type final: C{bool}
        """

        txn = self.store.newTransaction()
        for home_type in self.migrationTypes(txn):
            home = yield txn.homeWithUID(home_type, user, migration=_MIGRATION_STATUS_MIGRATING)
            yield home.migrateToThisPod(user, final)

        if final:
            home = yield txn.notificationsWithUID(user, migration=_MIGRATION_STATUS_MIGRATING)
            yield home.migrateToThisPod(user, final)
        yield txn.commit()


    @inlineCallbacks
    def syncSharingState(self, user):
        """
        Synchronize the sharing state for the user being migrated. This involves:

        1. For each item being shared by this user, make a bind entry in the new Pod
            for each share, adjusting for whether the sharee is now local or external
            the pod. The old pod bind entry will be removed in step 4, no clean-up needed.

        2. For each item shared to this user, make a bind entry in the new pod, adjusting
            for whether the sharer is local or external to the pod. If the sharer is local
            to the old pod, adjust their bind row to point to the user on the new pod. If
            the sharer is on some other pod (not the new or old one) then the existing bind
            rows on that pod should still be valid - no change needed.

        @param user: GUID of the user to migrate
        @type user: C{str}
        """

        # TODO:
        pass


    @inlineCallbacks
    def deactivateUserOnOtherPod(self, user):
        """
        Deactivate the user on their old Pod by adjusting migration state.
        This method makes a cross-pod call to the other pod and calls its
        L{deactivateUserOnThisPod} method.

        @param user: GUID of the user to migrate
        @type user: C{str}
        """

        # TODO: Do a cross-pod call to tell the other Pod to deactivate
        pass


    @inlineCallbacks
    def deactivateUserOnThisPod(self, user):
        """
        Deactivate the user on their old Pod (this one). Called as a result of the cross-pod
        call from the new pod L{deactivateUserOnOtherPod}.

        @param user: GUID of the user to migrate
        @type user: C{str}
        """

        txn = self.store.newTransaction()
        for home_type in self.migrationTypes(txn):
            home = yield txn.homeWithUID(home_type, user, migration=_MIGRATION_STATUS_NONE)
            yield home.updateDetails(newMigration=_MIGRATION_STATUS_MIGRATED)

        home = yield txn.notificationsWithUID(user, migration=_MIGRATION_STATUS_NONE)
        yield home.updateDetails(newMigration=_MIGRATION_STATUS_MIGRATED)

        yield txn.commit()


    @inlineCallbacks
    def activateUserOnThisPod(self, user):
        """
        Activate the user on their new Pod (this one) by adjusting migration state
        on each home. We also need to force their state to normal to properly indicate
        they are now hosted on this pod.

        @param user: GUID of the user to migrate
        @type user: C{str}
        """

        yield self.changeActivateState(user, _MIGRATION_STATUS_NONE, _HOME_STATUS_NORMAL)

        txn = self.store.newTransaction()
        for home_type in self.migrationTypes(txn):
            home = yield txn.homeWithUID(home_type, user, migration=_MIGRATION_STATUS_MIGRATING)
            yield home.updateDetails(newStatus=_HOME_STATUS_NORMAL, newMigration=_MIGRATION_STATUS_NONE)

        home = yield txn.notificationsWithUID(user, migration=_MIGRATION_STATUS_MIGRATING)
        yield home.updateDetails(newStatus=_HOME_STATUS_NORMAL, newMigration=_MIGRATION_STATUS_NONE)

        yield txn.commit()


    def verifyNonLocalUser(self, user):
        record = self.store.directoryService().recordWithGUID(user)
        if record.thisServer():
            raise UserAlreadyOnThisPod("User being migrated is already hosted on this pod")


    @inlineCallbacks
    def validMigratingUser(self, txn, user):
        """
        Determine if the specified user is already setup to migrate (i.e., step 1 has been done).

        @param txn: transaction to use
        @type txn: L{txdav.common.datastore.sql.CommonStoreTransaction}
        @param user: the user GUID to migrate
        @type user: C{str}

        @raise: L{UserNotAlreadyBeingMigrated} if invalid
        """

        for home_type in self.migrationTypes(txn):
            home = yield txn.homeWithUID(home_type, user, create=False, migration=_MIGRATION_STATUS_MIGRATING)
            if home is None:
                raise UserNotAlreadyBeingMigrated("Inactive {} home does not exist".format(txn.homeClass(home_type)))
        home = yield txn.notificationsWithUID(user, create=False, migration=_MIGRATION_STATUS_MIGRATING)
        if home is None:
            raise UserNotAlreadyBeingMigrated("Inactive notifications does not exist")
