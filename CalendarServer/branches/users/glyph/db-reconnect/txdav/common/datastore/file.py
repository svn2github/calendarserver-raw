# -*- test-case-name: txdav.caldav.datastore.test.test_file -*-
##
# Copyright (c) 2010-2011 Apple Inc. All rights reserved.
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
Common utility functions for a file based datastore.
"""

from twext.internet.decorate import memoizedKey
from twext.python.log import LoggingMixIn
from twext.web2.dav.element.rfc2518 import ResourceType, GETContentType, HRef
from twext.web2.dav.element.rfc5842 import ResourceID
from twext.web2.http_headers import generateContentType, MimeType
from twext.web2.dav.resource import TwistedGETContentMD5,\
    TwistedQuotaUsedProperty

from twisted.internet.defer import succeed, inlineCallbacks, returnValue
from twisted.python.util import FancyEqMixin
from twisted.python import hashlib

from twistedcaldav import customxml
from twistedcaldav.customxml import NotificationType
from twistedcaldav.notifications import NotificationRecord
from twistedcaldav.notifications import NotificationsDatabase as OldNotificationIndex
from twistedcaldav.sharing import SharedCollectionsDatabase
from txdav.caldav.icalendarstore import ICalendarStore

from txdav.common.icommondatastore import HomeChildNameNotAllowedError, \
    HomeChildNameAlreadyExistsError, NoSuchHomeChildError, \
    InternalDataStoreError, ObjectResourceNameNotAllowedError, \
    ObjectResourceNameAlreadyExistsError, NoSuchObjectResourceError
from txdav.common.inotifications import INotificationCollection, \
    INotificationObject
from txdav.base.datastore.file import DataStoreTransaction, DataStore, writeOperation, \
    hidden, isValidName, FileMetaDataMixin
from txdav.base.datastore.util import cached

from txdav.base.propertystore.base import PropertyName
from txdav.base.propertystore.xattr import PropertyStore

from errno import EEXIST, ENOENT
from zope.interface import implements, directlyProvides

import uuid

ECALENDARTYPE = 0
EADDRESSBOOKTYPE = 1

# Labels used to identify the class of resource being modified, so that
# notification systems can target the correct application
NotifierPrefixes = {
    ECALENDARTYPE : "CalDAV",
    EADDRESSBOOKTYPE : "CardDAV",
}

TOPPATHS = (
    "calendars",
    "addressbooks"
)
UIDPATH = "__uids__"

class CommonDataStore(DataStore):
    """
    An implementation of data store.

    @ivar _path: A L{CachingFilePath} referencing a directory on disk that
        stores all calendar and addressbook data for a group of UIDs.
    """
    implements(ICalendarStore)

    def __init__(self, path, notifierFactory, enableCalendars=True,
        enableAddressBooks=True):
        """
        Create a store.

        @param path: a L{FilePath} pointing at a directory on disk.
        """
        assert enableCalendars or enableAddressBooks

        super(CommonDataStore, self).__init__(path)
        self.enableCalendars = enableCalendars
        self.enableAddressBooks = enableAddressBooks
        self._notifierFactory = notifierFactory
        self._transactionClass = CommonStoreTransaction


    def newTransaction(self, name='no name', migrating=False):
        """
        Create a new transaction.

        @see Transaction
        """
        return self._transactionClass(
            self,
            name,
            self.enableCalendars,
            self.enableAddressBooks,
            self._notifierFactory,
            migrating,
        )


    def _homesOfType(self, storeType):
        """
        Common implementation of L{ICalendarStore.eachCalendarHome} and
        L{IAddressBookStore.eachAddressbookHome}; see those for a description
        of the return type.

        @param storeType: one of L{EADDRESSBOOKTYPE} or L{ECALENDARTYPE}.
        """
        top = self._path.child(TOPPATHS[storeType]).child(UIDPATH)
        if top.exists() and top.isdir():
            for firstPrefix in top.children():
                if not isValidName(firstPrefix.basename()):
                    continue
                for secondPrefix in firstPrefix.children():
                    if not isValidName(secondPrefix.basename()):
                        continue
                    for actualHome in secondPrefix.children():
                        uid = actualHome.basename()
                        if not isValidName(uid):
                            continue
                        txn = self.newTransaction("enumerate home %r" % (uid,))
                        home = txn.homeWithUID(storeType, uid, False)
                        yield (txn, home)


    def eachCalendarHome(self):
        return self._homesOfType(ECALENDARTYPE)


    def eachAddressbookHome(self):
        return self._homesOfType(EADDRESSBOOKTYPE)


class CommonStoreTransaction(DataStoreTransaction):
    """
    In-memory implementation of

    Note that this provides basic 'undo' support, but not truly transactional
    operations.
    """

    _homeClass = {}

    def __init__(self, dataStore, name, enableCalendars, enableAddressBooks, notifierFactory, migrating=False):
        """
        Initialize a transaction; do not call this directly, instead call
        L{DataStore.newTransaction}.

        @param dataStore: The store that created this transaction.

        @type dataStore: L{CommonDataStore}
        """
        from txdav.caldav.icalendarstore import ICalendarTransaction
        from txdav.carddav.iaddressbookstore import IAddressBookTransaction
        from txdav.caldav.datastore.file import CalendarHome
        from txdav.carddav.datastore.file import AddressBookHome

        super(CommonStoreTransaction, self).__init__(dataStore, name)
        self._calendarHomes = {}
        self._addressbookHomes = {}
        self._notificationHomes = {}
        self._notifierFactory = notifierFactory
        self._migrating = migrating

        extraInterfaces = []
        if enableCalendars:
            extraInterfaces.append(ICalendarTransaction)
            self._notificationHomeType = ECALENDARTYPE
        else:
            self._notificationHomeType = EADDRESSBOOKTYPE
        if enableAddressBooks:
            extraInterfaces.append(IAddressBookTransaction)
        directlyProvides(self, *extraInterfaces)

        CommonStoreTransaction._homeClass[ECALENDARTYPE] = CalendarHome
        CommonStoreTransaction._homeClass[EADDRESSBOOKTYPE] = AddressBookHome


    @memoizedKey('uid', '_calendarHomes')
    def calendarHomeWithUID(self, uid, create=False):
        return self.homeWithUID(ECALENDARTYPE, uid, create=create)

    @memoizedKey("uid", "_addressbookHomes")
    def addressbookHomeWithUID(self, uid, create=False):
        return self.homeWithUID(EADDRESSBOOKTYPE, uid, create=create)

    def homeWithUID(self, storeType, uid, create=False):
        if uid.startswith("."):
            return None

        if storeType not in (ECALENDARTYPE, EADDRESSBOOKTYPE):
            raise RuntimeError("Unknown home type.")

        return self._homeClass[storeType].homeWithUID(self, uid, create, storeType == ECALENDARTYPE)

    @memoizedKey("uid", "_notificationHomes")
    def notificationsWithUID(self, uid, home=None):

        if home is None:
            home = self.homeWithUID(self._notificationHomeType, uid, create=True)
        return NotificationCollection.notificationsFromHome(self, home)


class StubResource(object):
    """
    Just enough resource to keep the shared sql DB classes going.
    """
    def __init__(self, commonHome):
        self._commonHome = commonHome


    @property
    def fp(self):
        return self._commonHome._path



class CommonHome(FileMetaDataMixin, LoggingMixIn):

    # All these need to be initialized by derived classes for each store type
    _childClass = None
    _topPath = None
    _notifierPrefix = None

    def __init__(self, uid, path, dataStore, transaction, notifiers):
        self._dataStore = dataStore
        self._uid = uid
        self._path = path
        self._transaction = transaction
        self._notifiers = notifiers
        self._shares = SharedCollectionsDatabase(StubResource(self))
        self._newChildren = {}
        self._removedChildren = set()
        self._cachedChildren = {}


    @classmethod
    def homeWithUID(cls, txn, uid, create=False, withNotifications=False):

        assert len(uid) >= 4

        childPathSegments = []
        childPathSegments.append(txn._dataStore._path.child(cls._topPath))
        childPathSegments.append(childPathSegments[-1].child(UIDPATH))
        childPathSegments.append(childPathSegments[-1].child(uid[0:2]))
        childPathSegments.append(childPathSegments[-1].child(uid[2:4]))
        childPath = childPathSegments[-1].child(uid)

        def createDirectory(path):
            try:
                path.createDirectory()
            except (IOError, OSError), e:
                if e.errno != EEXIST:
                    # Ignore, in case someone else created the
                    # directory while we were trying to as well.
                    raise

        creating = False
        if create:
            # Create intermediate directories
            for child in childPathSegments:
                if not child.isdir():
                    createDirectory(child)

            if childPath.isdir():
                homePath = childPath
            else:
                creating = True
                homePath = childPath.temporarySibling()
                createDirectory(homePath)
                def do():
                    homePath.moveTo(childPath)
                    # do this _after_ all other file operations
                    home._path = childPath
                    return lambda : None
                txn.addOperation(do, "create home UID %r" % (uid,))

        elif not childPath.isdir():
            return None
        else:
            homePath = childPath

        if txn._notifierFactory:
            notifiers = (txn._notifierFactory.newNotifier(id=uid,
                prefix=cls._notifierPrefix),)
        else:
            notifiers = None

        home = cls(uid, homePath, txn._dataStore, txn, notifiers)
        if creating:
            home.createdHome()
            if withNotifications:
                txn.notificationsWithUID(uid, home)

        return home

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path)


    def uid(self):
        return self._uid


    def transaction(self):
        return self._transaction

    def retrieveOldShares(self):
        """
        Retrieve the old Index object.
        """
        return self._shares


    def children(self):
        """
        Return a set of the child resource objects.
        """
        return set(self._newChildren.itervalues()) | set(
            self.childWithName(name)
            for name in self._path.listdir()
            if not name.startswith(".")
        )

    # For file store there is no efficient "bulk" load of all children so just
    # use the "iterate over each child" method.
    loadChildren = children


    def listChildren(self):
        """
        Return a set of the names of the child resources.
        """
        return sorted(set(
            [child.name() for child in self._newChildren.itervalues()]
        ) | set(
            name
            for name in self._path.listdir()
            if not name.startswith(".") and self._path.child(name).isdir()
        ))


    def listSharedChildren(self):
        """
        Retrieve the names of the children in this home.

        @return: an iterable of C{str}s.
        """
        return [share.localname for share in self._shares.allRecords()]

        if self._childrenLoaded:
            return succeed(self._sharedChildren.keys())
        else:
            return self._childClass.listObjects(self, owned=False)


    def childWithName(self, name):
        child = self._newChildren.get(name)
        if child is not None:
            return child
        if name in self._removedChildren:
            return None
        if name in self._cachedChildren:
            return self._cachedChildren[name]

        if name.startswith("."):
            return None

        child = self._childClass.objectWithName(self, name, True)
        if child is not None:
            self._cachedChildren[name] = child
        return child


    @writeOperation
    def createChildWithName(self, name):
        if name.startswith("."):
            raise HomeChildNameNotAllowedError(name)

        childPath = self._path.child(name)

        if name not in self._removedChildren and childPath.isdir():
            raise HomeChildNameAlreadyExistsError(name)

        temporary = hidden(childPath.temporarySibling())
        temporaryName = temporary.basename()
        temporary.createDirectory()
        # In order for the index to work (which is doing real file ops on disk
        # via SQLite) we need to create a real directory _immediately_.

        # FIXME: some way to roll this back.

        c = self._newChildren[name] = self._childClass(temporary.basename(), self, True, realName=name)
        c.retrieveOldIndex().create()
        def do():
            childPath = self._path.child(name)
            temporary = childPath.sibling(temporaryName)
            try:
                props = c.properties()
                temporary.moveTo(childPath)
                c._name = name
                # FIXME: _lots_ of duplication of work here.
                props.flush()
            except (IOError, OSError), e:
                if e.errno == EEXIST and childPath.isdir():
                    raise HomeChildNameAlreadyExistsError(name)
                raise
            # FIXME: direct tests, undo for index creation
            # Return undo
            return lambda: self._path.child(childPath.basename()).remove()

        self._transaction.addOperation(do, "create child %r" % (name,))
        props = c.properties()
        props[PropertyName(*ResourceType.qname())] = c.resourceType()
        self.createdChild(c)

        self.notifyChanged()
        return c

    def createdChild(self, child):
        pass


    @writeOperation
    def removeChildWithName(self, name):
        if name.startswith(".") or name in self._removedChildren:
            raise NoSuchHomeChildError(name)

        child = self.childWithName(name)
        if child is None:
            raise NoSuchHomeChildError()

        try:
            child.remove()
        finally:
            self._removedChildren.add(name)

    @inlineCallbacks
    def syncToken(self):
        
        maxrev = 0
        for child in self.children():
            maxrev = max(int((yield child.syncToken()).split("#")[1]), maxrev)
            
        try:
            urnuuid = str(self.properties()[PropertyName.fromElement(ResourceID)].children[0])
        except KeyError:
            urnuuid = uuid.uuid4().urn
            self.properties()[PropertyName(*ResourceID.qname())] = ResourceID(HRef.fromString(urnuuid))
        returnValue("%s#%s" % (urnuuid[9:], maxrev))


    def resourceNamesSinceToken(self, token, depth):
        deleted = []
        changed = []
        return succeed((changed, deleted))


    # @cached
    def properties(self):
        # FIXME: needs tests for actual functionality
        # FIXME: needs to be cached
        # FIXME: transaction tests
        props = PropertyStore(self.uid(), lambda : self._path)
        self._transaction.addOperation(props.flush, "flush home properties")
        return props

    def objectResourcesWithUID(self, uid, ignore_children=()):
        """
        Return all child object resources with the specified UID, ignoring any in the
        named child collections. The file implementation just iterates all child collections.
        """
        results = []
        for child in self.children():
            if child.name() in ignore_children:
                continue
            object = child.objectResourceWithUID(uid)
            if object:
                results.append(object)
        return results

    def quotaUsedBytes(self):

        try:
            return int(str(self.properties()[PropertyName.fromElement(TwistedQuotaUsedProperty)]))
        except KeyError:
            return 0

    def adjustQuotaUsedBytes(self, delta):
        """
        Adjust quota used. We need to get a lock on the row first so that the adjustment
        is done atomically.
        """
        
        old_used = self.quotaUsedBytes()
        new_used = old_used + delta
        if new_used < 0:
            self.log_error("Fixing quota adjusted below zero to %s by change amount %s" % (new_used, delta,))
            new_used = 0
        self.properties()[PropertyName.fromElement(TwistedQuotaUsedProperty)] = TwistedQuotaUsedProperty(str(new_used))
            

    def addNotifier(self, notifier):
        if self._notifiers is None:
            self._notifiers = ()
        self._notifiers += (notifier,)
 
    def notifierID(self, label="default"):
        if self._notifiers:
            return self._notifiers[0].getID(label)
        else:
            return None

    @inlineCallbacks
    def nodeName(self, label="default"):
        if self._notifiers:
            for notifier in self._notifiers:
                name = (yield notifier.nodeName(label=label))
                if name is not None:
                    returnValue(name)
        else:
            returnValue(None)

    def notifyChanged(self):
        """
        Trigger a notification of a change
        """
        if self._notifiers:
            for notifier in self._notifiers:
                self._transaction.postCommit(notifier.notify)


class CommonHomeChild(FileMetaDataMixin, LoggingMixIn, FancyEqMixin):
    """
    Common ancestor class of AddressBooks and Calendars.
    """

    compareAttributes = '_name _home _transaction'.split()

    _objectResourceClass = None

    def __init__(self, name, home, owned, realName=None):
        """
        Initialize an home child pointing at a path on disk.

        @param name: the subdirectory of home where this child
            resides.
        @type name: C{str}

        @param home: the home containing this child.
        @type home: L{CommonHome}

        @param realName: If this child was just created, the name which it
        will eventually have on disk.
        @type realName: C{str}
        """
        self._name = name
        self._home = home
        self._owned = owned
        self._transaction = home._transaction
        self._newObjectResources = {}
        self._cachedObjectResources = {}
        self._removedObjectResources = set()
        self._index = None  # Derived classes need to set this
        self._invites = None # Derived classes need to set this
        self._renamedName = realName

        if home._notifiers:
            childID = "%s/%s" % (home.uid(), name)
            notifiers = [notifier.clone(label="collection", id=childID) for notifier in home._notifiers]
        else:
            notifiers = None
        self._notifiers = notifiers


    @classmethod
    def objectWithName(cls, home, name, owned):
        return cls(name, home, owned) if home._path.child(name).isdir() else None

    @property
    def _path(self):
        return self._home._path.child(self._name)


    def resourceType(self):
        return NotImplementedError

    def retrieveOldIndex(self):
        """
        Retrieve the old Index object.
        """
        return self._index._oldIndex

    def retrieveOldInvites(self):
        """
        Retrieve the old Invites DB object.
        """
        return self._invites._oldInvites


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    def name(self):
        if self._renamedName is not None:
            return self._renamedName
        return self._path.basename()


    _renamedName = None

    @writeOperation
    def rename(self, name):
        oldName = self.name()
        self._renamedName = name
        self._home._newChildren[name] = self
        self._home._removedChildren.add(oldName)
        def doIt():
            self._path.moveTo(self._path.sibling(name))
            return lambda : None # FIXME: revert
        self._transaction.addOperation(doIt, "rename home child %r -> %r" %
                                       (oldName, name))

        self.retrieveOldIndex().bumpRevision()

        self.notifyChanged()

    @writeOperation
    def remove(self):

        def do(transaction=self._transaction):
            childPath = self._path
            for i in xrange(1000):
                trash = childPath.sibling("._del_%s_%d" % (childPath.basename(), i))
                if not trash.exists():
                    break
            else:
                raise InternalDataStoreError("Unable to create trash target for child at %s" % (childPath,))

            try:
                childPath.moveTo(trash)
            except (IOError, OSError), e:
                if e.errno == ENOENT:
                    raise NoSuchHomeChildError(self._name)
                raise

            def cleanup():
                try:
                    trash.remove()
                except Exception, e:
                    self.log_error("Unable to delete trashed child at %s: %s" % (trash.fp, e))

            self._transaction.addOperation(cleanup, "remove child backup %r" % (self._name,))
            def undo():
                trash.moveTo(childPath)

            return undo

        # FIXME: direct tests
        self._transaction.addOperation(
            do, "prepare child remove %r" % (self._name,)
        )

        self.notifyChanged()

    def ownerHome(self):
        return self._home


    def setSharingUID(self, uid):
        self.properties()._setPerUserUID(uid)


    def objectResources(self):
        """
        Return a list of object resource objects.
        """
        return sorted((
            self.objectResourceWithName(name)
            for name in (
                set(self._newObjectResources.iterkeys()) |
                set(name for name in self._path.listdir()
                    if not name.startswith(".")) -
                set(self._removedObjectResources)
            )),
            key=lambda calObj: calObj.name()
        )


    def listObjectResources(self):
        """
        Return a list of object resource names.
        """
        return sorted((
            name
            for name in (
                set(self._newObjectResources.iterkeys()) |
                set(name for name in self._path.listdir()
                    if not name.startswith(".")) -
                set(self._removedObjectResources)
            ))
        )


    def objectResourceWithName(self, name):
        if name in self._removedObjectResources:
            return None
        if name in self._newObjectResources:
            return self._newObjectResources[name]
        if name in self._cachedObjectResources:
            return self._cachedObjectResources[name]

        objectResourcePath = self._path.child(name)
        if objectResourcePath.isfile():
            obj = self._objectResourceClass(name, self)
            self._cachedObjectResources[name] = obj
            return obj
        else:
            return None


    def objectResourceWithUID(self, uid):
        rname = self.retrieveOldIndex().resourceNameForUID(uid)
        if rname and rname not in self._removedObjectResources:
            return self.objectResourceWithName(rname)
        
        return None


    @writeOperation
    def createObjectResourceWithName(self, name, component, metadata=None):
        """
        Create a new resource with component data and optional metadata. We create the
        python object using the metadata then create the actual store object with setComponent. 
        """
        if name.startswith("."):
            raise ObjectResourceNameNotAllowedError(name)

        objectResourcePath = self._path.child(name)
        if objectResourcePath.exists():
            raise ObjectResourceNameAlreadyExistsError(name)

        objectResource = self._objectResourceClass(name, self, metadata)
        objectResource.setComponent(component, inserting=True)
        self._cachedObjectResources[name] = objectResource

        # Note: setComponent triggers a notification, so we don't need to
        # call notify( ) here like we do for object removal.

        return objectResource


    @writeOperation
    def removeObjectResourceWithName(self, name):
        if name.startswith("."):
            raise NoSuchObjectResourceError(name)

        self.retrieveOldIndex().deleteResource(name)

        objectResourcePath = self._path.child(name)
        if objectResourcePath.isfile():
            self._removedObjectResources.add(name)
            # FIXME: test for undo
            def do():
                objectResourcePath.remove()
                return lambda: None
            self._transaction.addOperation(do, "remove object resource object %r" %
                                           (name,))

            self.notifyChanged()
        else:
            raise NoSuchObjectResourceError(name)


    @writeOperation
    def removeObjectResourceWithUID(self, uid):
        self.removeObjectResourceWithName(
            self.objectResourceWithUID(uid)._path.basename())


    def syncToken(self):

        try:
            urnuuid = str(self.properties()[PropertyName.fromElement(ResourceID)].children[0])
        except KeyError:
            urnuuid = uuid.uuid4().urn
            self.properties()[PropertyName(*ResourceID.qname())] = ResourceID(HRef.fromString(urnuuid))
        return succeed("%s#%s" % (urnuuid[9:], self.retrieveOldIndex().lastRevision()))


    def objectResourcesSinceToken(self, token):
        raise NotImplementedError()


    def resourceNamesSinceToken(self, token):
        return succeed(self.retrieveOldIndex().whatchanged(token))


    # FIXME: property writes should be a write operation
    @cached
    def properties(self):
        # FIXME: needs direct tests - only covered by store tests
        # FIXME: transactions
        props = PropertyStore(self._home.uid(), lambda: self._path)
        self.initPropertyStore(props)

        self._transaction.addOperation(props.flush,
                                       "flush object resource properties")
        return props


    def initPropertyStore(self, props):
        """
        A hook for subclasses to override in order to set up their property
        store after it's been created.

        @param props: the L{PropertyStore} from C{properties()}.
        """
        pass

    def _doValidate(self, component):
        raise NotImplementedError

    def addNotifier(self, notifier):
        if self._notifiers is None:
            self._notifiers = ()
        self._notifiers += (notifier,)
 
    def notifierID(self, label="default"):
        if self._notifiers:
            return self._notifiers[0].getID(label)
        else:
            return None

    @inlineCallbacks
    def nodeName(self, label="default"):
        if self._notifiers:
            for notifier in self._notifiers:
                name = (yield notifier.nodeName(label=label))
                if name is not None:
                    returnValue(name)
        else:
            returnValue(None)

    def notifyChanged(self):
        """
        Trigger a notification of a change
        """
        if self._notifiers:
            for notifier in self._notifiers:
                self._transaction.postCommit(notifier.notify)


class CommonObjectResource(FileMetaDataMixin, LoggingMixIn, FancyEqMixin):
    """
    @ivar _path: The path of the file on disk

    @type _path: L{FilePath}
    """

    compareAttributes = '_name _parentCollection'.split()

    def __init__(self, name, parent, metadata=None):
        self._name = name
        self._parentCollection = parent
        self._transaction = parent._transaction
        self._component = None


    @property
    def _path(self):
        return self._parentCollection._path.child(self._name)


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    @writeOperation
    def setComponent(self, component, inserting=False):
        raise NotImplementedError


    def component(self):
        raise NotImplementedError


    def text(self):
        raise NotImplementedError


    def uid(self):
        raise NotImplementedError

    @cached
    def properties(self):
        uid = self._parentCollection._home.uid()
        props = PropertyStore(uid, lambda : self._path)
        self.initPropertyStore(props)
        self._transaction.addOperation(props.flush, "object properties flush")
        return props

    def initPropertyStore(self, props):
        """
        A hook for subclasses to override in order to set up their property
        store after it's been created.

        @param props: the L{PropertyStore} from C{properties()}.
        """
        pass

class CommonStubResource(object):
    """
    Just enough resource to keep the collection sql DB classes going.
    """
    def __init__(self, resource):
        self.resource = resource
        self.fp = self.resource._path

    def bumpSyncToken(self, reset=False):
        # FIXME: needs direct tests
        return self.resource._updateSyncToken(reset)


    def initSyncToken(self):
        # FIXME: needs direct tests
        self.bumpSyncToken(True)


class NotificationCollection(CommonHomeChild):
    """
    File-based implementation of L{INotificationCollection}.
    """
    implements(INotificationCollection)

    def __init__(self, name, parent, realName=None):
        """
        Initialize an notification collection pointing at a path on disk.

        @param name: the subdirectory of parent where this notification collection
            resides.
        @type name: C{str}

        @param parent: the home containing this notification collection.
        @type parent: L{CommonHome}
        """

        super(NotificationCollection, self).__init__(name, parent, realName)

        self._index = NotificationIndex(self)
        self._invites = None
        self._objectResourceClass = NotificationObject

    @classmethod
    def notificationsFromHome(cls, txn, home):

        notificationCollectionName = "notification"
        if not home._path.child(notificationCollectionName).isdir():
            notifications = cls._create(txn, home, notificationCollectionName)
        else:
            notifications = cls(notificationCollectionName, home)
        return notifications


    @classmethod
    def _create(cls, txn, home, collectionName):
        # FIXME: this is a near-copy of CommonHome.createChildWithName.
        temporary = hidden(home._path.child(collectionName).temporarySibling())
        temporary.createDirectory()
        temporaryName = temporary.basename()

        c = cls(temporary.basename(), home)

        def do():
            childPath = home._path.child(collectionName)
            temporary = childPath.sibling(temporaryName)
            try:
                props = c.properties()
                temporary.moveTo(childPath)
                c._name = collectionName
                # FIXME: _lots_ of duplication of work here.
                props.flush()
            except (IOError, OSError), e:
                if e.errno == EEXIST and childPath.isdir():
                    raise HomeChildNameAlreadyExistsError(collectionName)
                raise
            # FIXME: direct tests, undo for index creation
            # Return undo
            return lambda: home._path.child(collectionName).remove()

        txn.addOperation(do, "create notification child %r" %
                          (collectionName,))
        props = c.properties()
        props[PropertyName(*ResourceType.qname())] = c.resourceType()
        return c

    def resourceType(self):
        return ResourceType.notification #@UndefinedVariable

    notificationObjects = CommonHomeChild.objectResources
    listNotificationObjects = CommonHomeChild.listObjectResources
    notificationObjectWithName = CommonHomeChild.objectResourceWithName
    removeNotificationObjectWithUID = CommonHomeChild.removeObjectResourceWithUID

    def notificationObjectWithUID(self, uid):
        name = uid + ".xml"
        return self.notificationObjectWithName(name)

    def writeNotificationObject(self, uid, xmltype, xmldata):
        name = uid + ".xml"
        if name.startswith("."):
            raise ObjectResourceNameNotAllowedError(name)

        objectResource = NotificationObject(name, self)
        objectResource.setData(uid, xmltype, xmldata)
        self._cachedObjectResources[name] = objectResource

        # Update database
        self.retrieveOldIndex().addOrUpdateRecord(NotificationRecord(uid, name, xmltype.name))
        
        self.notifyChanged()

    @writeOperation
    def removeNotificationObjectWithName(self, name):
        if name.startswith("."):
            raise NoSuchObjectResourceError(name)

        self.retrieveOldIndex().removeRecordForName(name)

        objectResourcePath = self._path.child(name)
        if objectResourcePath.isfile():
            self._removedObjectResources.add(name)
            # FIXME: test for undo
            def do():
                objectResourcePath.remove()
                return lambda: None
            self._transaction.addOperation(do, "remove object resource object %r" %
                                           (name,))
        
            self.notifyChanged()
        else:
            raise NoSuchObjectResourceError(name)

    @writeOperation
    def removeNotificationObjectWithUID(self, uid):
        name = uid + ".xml"
        self.removeNotificationObjectWithName(name)

    def _doValidate(self, component):
        # Nothing to do - notifications are always generated internally by the server
        # so they better be valid all the time!
        pass


class NotificationObject(CommonObjectResource):
    """
    """
    implements(INotificationObject)

    def __init__(self, name, notifications):
        super(NotificationObject, self).__init__(name, notifications)
        self._uid = name[:-4]

    def notificationCollection(self):
        return self._parentCollection


    def created(self):
        if not self._path.exists():
            from twisted.internet import reactor
            return int(reactor.seconds())
        return super(NotificationObject, self).created()

    def modified(self):
        if not self._path.exists():
            from twisted.internet import reactor
            return int(reactor.seconds())
        return super(NotificationObject, self).modified()


    @writeOperation
    def setData(self, uid, xmltype, xmldata, inserting=False):

        rname = uid + ".xml"
        self._parentCollection.retrieveOldIndex().addOrUpdateRecord(
            NotificationRecord(uid, rname, xmltype.name)
        )

        self._xmldata = xmldata
        md5 = hashlib.md5(xmldata).hexdigest()

        def do():
            backup = None
            if self._path.exists():
                backup = hidden(self._path.temporarySibling())
                self._path.moveTo(backup)
            fh = self._path.open("w")
            try:
                # FIXME: concurrency problem; if this write is interrupted
                # halfway through, the underlying file will be corrupt.
                fh.write(xmldata)
            finally:
                fh.close()
            def undo():
                if backup:
                    backup.moveTo(self._path)
                else:
                    self._path.remove()
            return undo
        self._transaction.addOperation(do, "set notification data %r" % (self.name(),))

        # Mark all properties as dirty, so they will be re-added to the
        # temporary file when the main file is deleted. NOTE: if there were a
        # temporary file and a rename() as there should be, this should really
        # happen after the write but before the rename.
        self.properties().update(self.properties())

        props = self.properties()
        props[PropertyName(*GETContentType.qname())] = GETContentType.fromString(generateContentType(MimeType("text", "xml", params={"charset":"utf-8"})))
        props[PropertyName.fromElement(NotificationType)] = NotificationType(xmltype)
        props[PropertyName.fromElement(TwistedGETContentMD5)] = TwistedGETContentMD5.fromString(md5)


        # FIXME: the property store's flush() method may already have been
        # added to the transaction, but we need to add it again to make sure it
        # happens _after_ the new file has been written.  we may end up doing
        # the work multiple times, and external callers to property-
        # manipulation methods won't work.
        self._transaction.addOperation(self.properties().flush, "post-update property flush")


    _xmldata = None

    def xmldata(self):
        if self._xmldata is not None:
            return self._xmldata
        try:
            fh = self._path.open()
        except IOError, e:
            if e[0] == ENOENT:
                raise NoSuchObjectResourceError(self)
            else:
                raise

        try:
            text = fh.read()
        finally:
            fh.close()

        return text


    def uid(self):
        return self._uid

    def initPropertyStore(self, props):
        # Setup peruser special properties
        props.setSpecialProperties(
            (
            ),
            (
                PropertyName.fromElement(customxml.NotificationType),
            ),
        )


class NotificationIndex(object):
    #
    # OK, here's where we get ugly.
    # The index code needs to be rewritten also, but in the meantime...
    #
    def __init__(self, notificationCollection):
        self.notificationCollection = notificationCollection
        stubResource = CommonStubResource(notificationCollection)
        self._oldIndex = OldNotificationIndex(stubResource)

