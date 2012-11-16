# -*- test-case-name: txdav.common.datastore.upgrade.test.test_migrate -*-
##
# Copyright (c) 2011-2012 Apple Inc. All rights reserved.
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
Migrating from file store to sql store.
"""

import os
import errno
import xattr

from twisted.python.failure import Failure
from twext.python.log import LoggingMixIn

from twisted.python.runtime import platform
from twisted.python.reflect import namedAny, qual

from twisted.application.service import Service
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.defer import maybeDeferred, gatherResults

from twext.python.filepath import CachingFilePath
from twext.python.parallel import Parallelizer
from twext.internet.spawnsvc import SpawnerService

from twisted.protocols.amp import AMP, Command, String, Boolean

from txdav.caldav.datastore.util import migrateHome as migrateCalendarHome
from txdav.carddav.datastore.util import migrateHome as migrateAddressbookHome
from txdav.common.datastore.file import CommonDataStore as FileStore, TOPPATHS
from txdav.base.propertystore.xattr import PropertyStore as XattrPropertyStore
from txdav.base.propertystore.appledouble_xattr import (PropertyStore
                                                        as AppleDoubleStore)
from txdav.caldav.datastore.util import fixOneCalendarObject
from txdav.base.datastore.util import normalizeUUIDOrNot



@inlineCallbacks
def _getFixedComponent(cobj):
    """
    Retrieve a UUID-normalized component from a calendar object (for migrating
    from the file store).

    @param cobj: a calendar object from the file store.
    @type cobj: L{ICalendarObject}

    @return: a L{Deferred} which fires with the appropriate L{Component}.
    """
    comp = yield cobj.component()
    fixes, fixed = fixOneCalendarObject(comp)
    returnValue(fixed)



homeTypeLookup = {
    "calendar": (
        lambda inHome, outHome, getComponent=None, merge=False:
            migrateCalendarHome(inHome, outHome, _getFixedComponent, merge),
        lambda txn: txn.calendarHomeWithUID),
    "addressbook": (migrateAddressbookHome,
                    lambda txn: txn.addressbookHomeWithUID)
}



def swapAMP(oldAMP, newAMP):
    """
    Swap delivery of messages from an old L{AMP} instance to a new one.

    This is useful for implementors of L{StoreSpawnerService} since they will
    typically want to create one protocol for initializing the store, and
    another for processing application commands.

    @param oldAMP: An AMP instance currently hooked up to a transport, whose
        job is done and wants to stop receiving messages.

    @param newAMP: An AMP instance who wants to take over and start receiving
        messages previously destined for oldAMP.

    @return: C{newAMP}
    """
    oldAMP.boxReceiver = newAMP
    newAMP.startReceivingBoxes(oldAMP)
    return newAMP



class StoreSpawnerService(SpawnerService):
    """
    Abstract subclass of L{SpawnerService} that describes how to spawn a subclass.
    """

    def spawnWithStore(self, here, there):
        """
        Like L{SpawnerService.spawn}, but instead of instantiating C{there}
        with 0 arguments, it instantiates it with an L{ICalendarStore} /
        L{IAddressbookStore}.
        """
        raise NotImplementedError("subclasses must implement the specifics")


    def spawnWithConfig(self, config, here, there):
        """
        Like L{SpawnerService.spawn}, but instead of instantiating C{there}
        with 0 arguments, it instantiates it with the given
        L{twistedcaldav.config.Config}.
        """
        raise NotImplementedError("subclasses must implement the specifics")



class Configure(Command):
    """
    Configure the upgrade helper process.
    """

    arguments = [("filename", String()),
                 ("appropriateStoreClass", String()),
                 ("merge", Boolean())]



class OneUpgrade(Command):
    """
    Upgrade a single calendar home.
    """

    arguments = [("uid", String()),
                 ("homeType", String())]



class LogIt(Command):
    """
    Log a message.
    """
    arguments = [("message", String())]



class UpgradeDriver(AMP):
    """
    Helper protocol which runs in the master process doing the upgrade.
    """

    def __init__(self, upgradeService):
        super(UpgradeDriver, self).__init__()
        self.service = upgradeService


    def configure(self, filename, storeClass):
        """
        Configure the subprocess to examine the file store at the given path
        name, with the given dead property storage class.
        """
        return self.callRemote(Configure, filename=filename,
                               appropriateStoreClass=qual(storeClass),
                               merge=self.service.merge)


    def oneUpgrade(self, uid, homeType):
        """
        Upgrade one calendar or addressbook home, with the given uid of the
        given type, and return a L{Deferred} which will fire when the upgrade
        is complete.
        """
        return self.callRemote(OneUpgrade, uid=uid, homeType=homeType)



class UpgradeHelperProcess(AMP):
    """
    Helper protocol which runs in a subprocess to upgrade.
    """

    def __init__(self, store):
        """
        Create with a reference to an SQL store.
        """
        super(UpgradeHelperProcess, self).__init__()
        self.store = store
        self.store.setMigrating(True)


    @Configure.responder
    def configure(self, filename, appropriateStoreClass, merge):
        subsvc = None
        self.upgrader = UpgradeToDatabaseService(
            FileStore(
                CachingFilePath(filename), None, True, True,
                propertyStoreClass=namedAny(appropriateStoreClass)
            ), self.store, subsvc, merge=merge
        )
        return {}


    @OneUpgrade.responder
    def oneUpgrade(self, uid, homeType):
        """
        Upgrade one calendar home.
        """
        migrateFunc, destFunc = homeTypeLookup[homeType]
        fileTxn = self.upgrader.fileStore.newTransaction()
        return (
            maybeDeferred(destFunc(fileTxn), uid)
            .addCallback(
                lambda fileHome:
                self.upgrader.migrateOneHome(fileTxn, homeType, fileHome)
            )
            .addCallbacks(lambda ignored: fileTxn.commit(),
                          lambda err: fileTxn.abort()
                                      .addCallback(lambda ign: err))
            .addCallback(lambda ignored: {})
        )



class UpgradeToDatabaseService(Service, LoggingMixIn, object):
    """
    Upgrade resources from a filesystem store to a database store.
    """

    @classmethod
    def wrapService(cls, path, service, store, uid=None, gid=None,
                    parallel=0, spawner=None, merge=False):
        """
        Create an L{UpgradeToDatabaseService} if there are still file-based
        calendar or addressbook homes remaining in the given path.

        @param path: a path pointing at the document root, where the file-based
            data-store is located.
        @type path: L{CachingFilePath}

        @param service: the service to wrap.  This service should be started
            when the upgrade is complete.  (This is accomplished by returning
            it directly when no upgrade needs to be done, and by adding it to
            the service hierarchy when the upgrade completes; assuming that the
            service parent of the resulting service will be set to a
            L{MultiService} or similar.)

        @param store: the SQL storage service.

        @type service: L{IService}

        @param parallel: The number of parallel subprocesses that should manage
            the upgrade.

        @param spawner: a concrete L{StoreSpawnerService} subclass that will be
            used to spawn helper processes.

        @param merge: merge filesystem homes into SQL homes, rather than
            skipping them.

        @return: a service
        @rtype: L{IService}
        """
        # TODO: TOPPATHS should be computed based on enabled flags in 'store',
        # not hard coded.
        for homeType in TOPPATHS:
            if path.child(homeType).exists():
                if platform.isMacOSX():
                    appropriateStoreClass = XattrPropertyStore
                else:
                    attrs = xattr.xattr(path.path)
                    try:
                        attrs.get('user.should-not-be-set')
                    except IOError, ioe:
                        if ioe.errno == errno.ENODATA:
                            # xattrs are supported and enabled on the filesystem
                            # where the calendar data lives.  this takes some
                            # doing (you have to edit fstab), so this means
                            # we're trying to migrate some 2.x data from a
                            # previous linux installation.
                            appropriateStoreClass = XattrPropertyStore
                        elif ioe.errno == errno.EOPNOTSUPP:
                            # The operation wasn't supported.  This is what will
                            # usually happen on a naively configured filesystem,
                            # so this means we're most likely trying to migrate
                            # some data from an untarred archive created on an
                            # OS X installation using xattrs.
                            appropriateStoreClass = AppleDoubleStore
                        else:
                            # No need to check for ENOENT and the like; we just
                            # checked above to make sure the parent exists.
                            # Other errors are not anticipated here, so fail
                            # fast.
                            raise

                    appropriateStoreClass = AppleDoubleStore

                self = cls(
                    FileStore(path, None, True, True,
                              propertyStoreClass=appropriateStoreClass),
                    store, service, uid=uid, gid=gid,
                    parallel=parallel, spawner=spawner, merge=merge
                )
                return self
        return service


    def __init__(self, fileStore, sqlStore, service, uid=None, gid=None,
                 parallel=0, spawner=None, merge=False):
        """
        Initialize the service.
        """
        self.wrappedService = service
        self.fileStore = fileStore
        self.sqlStore = sqlStore
        self.uid = uid
        self.gid = gid
        self.parallel = parallel
        self.spawner = spawner
        self.merge = merge


    @inlineCallbacks
    def migrateOneHome(self, fileTxn, homeType, fileHome):
        """
        Migrate an individual calendar or addressbook home.
        """
        migrateFunc, destFunc = homeTypeLookup.get(homeType)
        uid = normalizeUUIDOrNot(fileHome.uid())
        self.log_warn("Starting migration transaction %s UID %r" %
                      (homeType, uid))
        sqlTxn = self.sqlStore.newTransaction()
        homeGetter = destFunc(sqlTxn)
        sqlHome = yield homeGetter(uid, create=False)
        if sqlHome is not None and not self.merge:
            self.log_warn(
                "%s home %r already existed not migrating" % (
                    homeType, uid))
            yield sqlTxn.abort()
            returnValue(None)
        try:
            if sqlHome is None:
                sqlHome = yield homeGetter(uid, create=True)
            yield migrateFunc(fileHome, sqlHome, merge=self.merge)
        except:
            f = Failure()
            yield sqlTxn.abort()
            f.raiseException()
        else:
            yield sqlTxn.commit()
            # Remove file home after migration. FIXME: instead, this should be a
            # public remove...HomeWithUID() API for de-provisioning.  (If we had
            # this, this would simply be a store-to-store migrator rather than a
            # filesystem-to-database upgrade.)
            fileHome._path.remove()


    @inlineCallbacks
    def doMigration(self):
        """
        Do the migration.  Called by C{startService}, but a different method
        because C{startService} should return C{None}, not a L{Deferred}.

        @return: a Deferred which fires when the migration is complete.
        """
        self.sqlStore.setMigrating(True)
        parallel = self.parallel
        if parallel:
            self.log_warn("Starting %d upgrade helper processes." %
                          (parallel,))
            spawner = self.spawner
            spawner.startService()
            drivers = yield gatherResults(
                [spawner.spawnWithStore(UpgradeDriver(self),
                                        UpgradeHelperProcess)
                 for x in xrange(parallel)]
            )
            # Wait for all subprocesses to be fully configured before
            # continuing, but let them configure in any order.
            self.log_warn("Configuring upgrade helper processes.")

            # FIXME: abstraction violations galore here; not too important,
            # since fileStore and this code are part of the same conceptual
            # unit, but if these become more independent there should probably
            # be a store-serialization API so that this code doesn't need to
            # know the intimate details of the fileStore implementation.
            # (Alternately, wrapService could just hold on to the details that
            # it used to construct the service in the first place.)
            yield gatherResults(
                [driver.configure(self.fileStore._path.path,
                                  self.fileStore._propertyStoreClass)
                 for driver in drivers]
            )
            self.log_warn("Upgrade helpers ready.")
            parallelizer = Parallelizer(drivers)
        else:
            parallelizer = None

        self.log_warn("Beginning filesystem -> database upgrade.")

        for homeType, eachFunc in [
                ("calendar", self.fileStore.withEachCalendarHomeDo),
                ("addressbook", self.fileStore.withEachAddressbookHomeDo),
            ]:
            yield eachFunc(
                lambda txn, home: self._upgradeAction(
                    txn, home, homeType, parallel, parallelizer
                )
            )

        if parallel:
            yield parallelizer.done()

        for homeType in TOPPATHS:
            homesPath = self.fileStore._path.child(homeType)
            if homesPath.isdir():
                homesPath.remove()

        # Set attachment directory ownership.  FIXME: is this still necessary
        # since attachments started living outside the database directory
        # created by initdb?  default permissions might be correct now.
        sqlAttachmentsPath = self.sqlStore.attachmentsPath
        if (sqlAttachmentsPath and sqlAttachmentsPath.exists() and
            (self.uid or self.gid)):
            uid = self.uid or -1
            gid = self.gid or -1
            for fp in sqlAttachmentsPath.walk():
                os.chown(fp.path, uid, gid)

        self.sqlStore.setMigrating(False)

        if parallel:
            self.log_warn("Stopping upgrade helper processes.")
            yield spawner.stopService()
            self.log_warn("Upgrade helpers all stopped.")
        self.log_warn(
            "Filesystem upgrade complete, launching database service."
        )
        wrapped = self.wrappedService
        if wrapped is not None:
            # see http://twistedmatrix.com/trac/ticket/4649
            reactor.callLater(0, wrapped.setServiceParent, self.parent)


    @inlineCallbacks
    def _upgradeAction(self, fileTxn, fileHome, homeType, parallel,
                       parallelizer):
        uid = fileHome.uid()
        self.log_warn("Migrating %s UID %r" % (homeType, uid))
        if parallel:
            @inlineCallbacks
            def doOneUpgrade(driver, fileUID=uid, homeType=homeType):
                yield driver.oneUpgrade(fileUID, homeType)
                self.log_warn("Completed migration of %s uid %r" %
                              (homeType, fileUID))
            yield parallelizer.do(doOneUpgrade)
        else:
            yield self.migrateOneHome(fileTxn, homeType, fileHome)



    def startService(self):
        """
        Start the service.
        """
        self.doMigration()
