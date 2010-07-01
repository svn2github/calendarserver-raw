# -*- test-case-name: txcaldav.calendarstore.test.test_file -*-
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
Common utility functions for a file based datastore.
"""

from errno import EEXIST, ENOENT

from twext.python.log import LoggingMixIn
from twext.web2.dav.element.rfc2518 import ResourceType, GETContentType

from txdav.datastore.file import DataStoreTransaction, DataStore, writeOperation, \
    hidden, isValidName, cached
from txdav.propertystore.base import PropertyName
from txdav.propertystore.xattr import PropertyStore
from txdav.common.icommondatastore import HomeChildNameNotAllowedError, \
    HomeChildNameAlreadyExistsError, NoSuchHomeChildError, \
    InternalDataStoreError, ObjectResourceNameNotAllowedError, \
    ObjectResourceNameAlreadyExistsError, NoSuchObjectResourceError

from twisted.python.util import FancyEqMixin
from twistedcaldav.customxml import GETCTag, NotificationType
from uuid import uuid4
from zope.interface import implements, directlyProvides
from txdav.common.inotifications import INotificationCollection, \
    INotificationObject
from twistedcaldav.notifications import NotificationRecord
from twistedcaldav.notifications import NotificationsDatabase as OldNotificationIndex
from twext.web2.http_headers import generateContentType, MimeType
from twistedcaldav.sharing import SharedCollectionsDatabase
from txdav.idav import IDataStore


ECALENDARTYPE = 0
EADDRESSBOOKTYPE = 1
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
    implements(IDataStore)

    def __init__(self, path, enableCalendars=True, enableAddressBooks=True):
        """
        Create a store.

        @param path: a L{FilePath} pointing at a directory on disk.
        """
        assert enableCalendars or enableAddressBooks

        super(CommonDataStore, self).__init__(path)
        self.enableCalendars = enableCalendars
        self.enableAddressBooks = enableAddressBooks
        self._transactionClass = CommonStoreTransaction

    def newTransaction(self):
        """
        Create a new transaction.

        @see Transaction
        """
        return self._transactionClass(self, self.enableCalendars, self.enableAddressBooks)

class CommonStoreTransaction(DataStoreTransaction):
    """
    In-memory implementation of

    Note that this provides basic 'undo' support, but not truly transactional
    operations.
    """

    _homeClass = {}

    def __init__(self, dataStore, enableCalendars, enableAddressBooks):
        """
        Initialize a transaction; do not call this directly, instead call
        L{DataStore.newTransaction}.

        @param dataStore: The store that created this transaction.

        @type dataStore: L{CommonDataStore}
        """
        from txcaldav.icalendarstore import ICalendarTransaction
        from txcarddav.iaddressbookstore import IAddressBookTransaction
        from txcaldav.calendarstore.file import CalendarHome
        from txcarddav.addressbookstore.file import AddressBookHome

        super(CommonStoreTransaction, self).__init__(dataStore)
        self._homes = {}
        self._homes[ECALENDARTYPE] = {}
        self._homes[EADDRESSBOOKTYPE] = {}
        self._notifications = {}

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


    def calendarHomeWithUID(self, uid, create=False):
        return self.homeWithUID(ECALENDARTYPE, uid, create)

    def addressbookHomeWithUID(self, uid, create=False):
        return self.homeWithUID(EADDRESSBOOKTYPE, uid, create)

    def homeWithUID(self, storeType, uid, create=False):
        if (uid, self) in self._homes[storeType]:
            return self._homes[storeType][(uid, self)]

        if uid.startswith("."):
            return None

        assert len(uid) >= 4

        childPathSegments = []
        childPathSegments.append(self._dataStore._path.child(TOPPATHS[storeType]))
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
                    def lastly():
                        homePath.moveTo(childPath)
                        # home._path = homePath
                        # do this _after_ all other file operations
                        home._path = childPath
                        return lambda : None
                    self.addOperation(lastly, "create home finalize")
                    return lambda : None
                self.addOperation(do, "create home UID %r" % (uid,))

        elif not childPath.isdir():
            return None
        else:
            homePath = childPath

        home = self._homeClass[storeType](uid, homePath, self._dataStore, self)
        self._homes[storeType][(uid, self)] = home
        if creating:
            home.created()

            # Create notification collection
            if storeType == ECALENDARTYPE:
                self.notificationsWithUID(uid)
        return home

    def notificationsWithUID(self, uid):

        if (uid, self) in self._notifications:
            return self._notifications[(uid, self)]

        home = self.homeWithUID(self._notificationHomeType, uid, create=True)
        notificationPath = home._path.child("notification")
        if not notificationPath.isdir():
            notificationPath = self.createNotifcationCollection(home, notificationPath)

        notifications = NotificationCollection(notificationPath.basename(), home)
        self._notifications[(uid, self)] = notifications
        return notifications

    def createNotifcationCollection(self, home, notificationPath):

        if notificationPath.isdir():
            return notificationPath

        name = notificationPath.basename()

        temporary = hidden(notificationPath.temporarySibling())
        temporary.createDirectory()
        # In order for the index to work (which is doing real file ops on disk
        # via SQLite) we need to create a real directory _immediately_.

        # FIXME: some way to roll this back.

        c = NotificationCollection(temporary.basename(), home)
        def do():
            try:
                props = c.properties()
                temporary.moveTo(notificationPath)
                c._name = name
                # FIXME: _lots_ of duplication of work here.
                props.path = notificationPath
                props.flush()
            except (IOError, OSError), e:
                if e.errno == EEXIST and notificationPath.isdir():
                    raise HomeChildNameAlreadyExistsError(name)
                raise
            # FIXME: direct tests, undo for index creation
            # Return undo
            return lambda: notificationPath.remove()

        self.addOperation(do, "create child %r" % (name,))
        props = c.properties()
        props[PropertyName(*ResourceType.qname())] = c.resourceType()
        return temporary

class StubResource(object):
    """
    Just enough resource to keep the shared sql DB classes going.
    """
    def __init__(self, stubit):
        self.fp = stubit._path

class CommonHome(LoggingMixIn):

    _childClass = None

    def __init__(self, uid, path, dataStore, transaction):
        self._dataStore = dataStore
        self._uid = uid
        self._path = path
        self._transaction = transaction
        self._shares = SharedCollectionsDatabase(StubResource(self))
        self._newChildren = {}
        self._removedChildren = set()
        self._cachedChildren = {}


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path)


    def uid(self):
        return self._uid


    def _updateSyncToken(self, reset=False):
        "Stub for updating sync token."
        # FIXME: actually update something


    def retrieveOldShares(self):
        """
        Retrieve the old Index object.
        """
        return self._shares

    def children(self):
        return set(self._newChildren.itervalues()) | set(
            self.childWithName(name)
            for name in self._path.listdir()
            if not name.startswith(".")
        )

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

        childPath = self._path.child(name)
        if childPath.isdir():
            existingChild = self._childClass(name, self)
            self._cachedChildren[name] = existingChild
            return existingChild
        else:
            return None


    @writeOperation
    def createChildWithName(self, name):
        if name.startswith("."):
            raise HomeChildNameNotAllowedError(name)

        childPath = self._path.child(name)

        if name not in self._removedChildren and childPath.isdir():
            raise HomeChildNameAlreadyExistsError(name)

        temporary = hidden(childPath.temporarySibling())
        temporary.createDirectory()
        # In order for the index to work (which is doing real file ops on disk
        # via SQLite) we need to create a real directory _immediately_.

        # FIXME: some way to roll this back.

        c = self._newChildren[name] = self._childClass(temporary.basename(), self, name)
        c.retrieveOldIndex().create()
        def do():
            try:
                props = c.properties()
                temporary.moveTo(childPath)
                c._name = name
                # FIXME: _lots_ of duplication of work here.
                props.path = childPath
                props.flush()
            except (IOError, OSError), e:
                if e.errno == EEXIST and childPath.isdir():
                    raise HomeChildNameAlreadyExistsError(name)
                raise
            # FIXME: direct tests, undo for index creation
            # Return undo
            return lambda: childPath.remove()

        self._transaction.addOperation(do, "create child %r" % (name,))
        props = c.properties()
        props[PropertyName(*ResourceType.qname())] = c.resourceType()
        self.createdChild(c)

    def createdChild(self, child):
        pass

    @writeOperation
    def removeChildWithName(self, name):
        if name.startswith(".") or name in self._removedChildren:
            raise NoSuchHomeChildError(name)

        self._removedChildren.add(name)
        childPath = self._path.child(name)
        if name not in self._newChildren and not childPath.isdir():
            raise NoSuchHomeChildError(name)

        def do(transaction=self._transaction):
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
                    raise NoSuchHomeChildError(name)
                raise

            def cleanup():
                try:
                    trash.remove()
                except Exception, e:
                    self.log_error("Unable to delete trashed child at %s: %s" % (trash.fp, e))

            transaction.addOperation(cleanup, "remove child backup %r" % (name,))

            def undo():
                trash.moveTo(childPath)

            return undo

        # FIXME: direct tests
        self._transaction.addOperation(
            do, "prepare child remove %r" % (name,)
        )

    # @cached
    def properties(self):
        # FIXME: needs tests for actual functionality
        # FIXME: needs to be cached
        # FIXME: transaction tests
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "flush home properties")
        return props


class CommonHomeChild(LoggingMixIn, FancyEqMixin):
    """
    """

    compareAttributes = '_name _home _transaction'.split()

    _objectResourceClass = None

    def __init__(self, name, home, realName=None):
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
        self._transaction = home._transaction
        self._newObjectResources = {}
        self._cachedObjectResources = {}
        self._removedObjectResources = set()
        self._index = None  # Derived classes need to set this
        self._invites = None # Derived classes need to set this
        self._renamedName = realName


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
        self._updateSyncToken()
        oldName = self.name()
        self._renamedName = name
        self._home._newChildren[name] = self
        self._home._removedChildren.add(oldName)
        def doIt():
            self._path.moveTo(self._path.sibling(name))
            return lambda : None # FIXME: revert
        self._transaction.addOperation(doIt, "rename home child %r -> %r" %
                                       (oldName, name))


    def ownerHome(self):
        return self._home


    def objectResources(self):
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
        # FIXME: This _really_ needs to be inspecting an index, not parsing
        # every resource.
        for objectResourcePath in self._path.children():
            if not isValidName(objectResourcePath.basename()):
                continue
            obj = self._objectResourceClass(objectResourcePath.basename(), self)
            if obj.component().resourceUID() == uid:
                if obj.name() in self._removedObjectResources:
                    return None
                return obj


    @writeOperation
    def createObjectResourceWithName(self, name, component):
        if name.startswith("."):
            raise ObjectResourceNameNotAllowedError(name)

        objectResourcePath = self._path.child(name)
        if objectResourcePath.exists():
            raise ObjectResourceNameAlreadyExistsError(name)

        objectResource = self._objectResourceClass(name, self)
        objectResource.setComponent(component)
        self._cachedObjectResources[name] = objectResource


    @writeOperation
    def removeObjectResourceWithName(self, name):
        if name.startswith("."):
            raise NoSuchObjectResourceError(name)

        newRevision = self._updateSyncToken() # FIXME: Test
        self.retrieveOldIndex().deleteResource(name, newRevision)

        objectResourcePath = self._path.child(name)
        if objectResourcePath.isfile():
            self._removedObjectResources.add(name)
            # FIXME: test for undo
            def do():
                objectResourcePath.remove()
                return lambda: None
            self._transaction.addOperation(do, "remove object resource object %r" %
                                           (name,))
        else:
            raise NoSuchObjectResourceError(name)


    @writeOperation
    def removeObjectResourceWithUID(self, uid):
        self.removeObjectResourceWithName(
            self.objectResourceWithUID(uid)._path.basename())


    def syncToken(self):
        raise NotImplementedError()


    def _updateSyncToken(self, reset=False):
        # FIXME: add locking a-la CalDAVFile.bumpSyncToken
        # FIXME: tests for desired concurrency properties
        ctag = PropertyName.fromString(GETCTag.sname())
        props = self.properties()
        token = props.get(ctag)
        if token is None or reset:
            tokenuuid = uuid4()
            revision = 1
        else:
            # FIXME: no direct tests for update
            token = str(token)
            tokenuuid, revision = token.split("#", 1)
            revision = int(revision) + 1
        token = "%s#%d" % (tokenuuid, revision)
        props[ctag] = GETCTag(token)
        # FIXME: no direct tests for commit
        return revision


    def objectResourcesSinceToken(self, token):
        raise NotImplementedError()


    # FIXME: property writes should be a write operation
    @cached
    def properties(self):
        # FIXME: needs direct tests - only covered by store tests
        # FIXME: transactions
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "flush object resource properties")
        return props


    def _doValidate(self, component):
        raise NotImplementedError


class CommonObjectResource(LoggingMixIn):
    """
    @ivar _path: The path of the file on disk

    @type _path: L{FilePath}
    """

    def __init__(self, name, parent):
        self._name = name
        self._parentCollection = parent
        self._transaction = parent._transaction
        self._component = None


    @property
    def _path(self):
        return self._parentCollection._path.child(self._name)


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    def name(self):
        return self._path.basename()


    @writeOperation
    def setComponent(self, component):
        raise NotImplementedError


    def component(self):
        raise NotImplementedError


    def text(self):
        raise NotImplementedError


    def uid(self):
        raise NotImplementedError

    @cached
    def properties(self):
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "object properties flush")
        return props

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

    def resourceType(self):
        return ResourceType.notification

    notificationObjects = CommonHomeChild.objectResources
    notificationObjectWithName = CommonHomeChild.objectResourceWithName
    removeNotificationObjectWithUID = CommonHomeChild.removeObjectResourceWithUID
    notificationObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken

    def notificationObjectWithUID(self, uid):

        record = self.retrieveOldIndex().recordForUID(uid)
        return self.notificationObjectWithName(record.name) if record else None

    def writeNotificationObject(self, uid, xmltype, xmldata):
        name = uid + ".xml"
        if name.startswith("."):
            raise ObjectResourceNameNotAllowedError(name)

        objectResource = NotificationObject(name, self)
        objectResource.setData(uid, xmltype, xmldata)
        self._cachedObjectResources[name] = objectResource

        # Update database
        self.retrieveOldIndex().addOrUpdateRecord(NotificationRecord(uid, name, xmltype.name))

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
        else:
            raise NoSuchObjectResourceError(name)

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


    @property
    def _notificationCollection(self):
        return self._parentCollection

    @writeOperation
    def setData(self, uid, xmltype, xmldata):

        rname = uid + ".xml"
        self._notificationCollection.retrieveOldIndex().addOrUpdateRecord(
            NotificationRecord(uid, rname, xmltype.name)
        )

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
        props[PropertyName(*NotificationType.qname())] = NotificationType(xmltype)

        # FIXME: the property store's flush() method may already have been
        # added to the transaction, but we need to add it again to make sure it
        # happens _after_ the new file has been written.  we may end up doing
        # the work multiple times, and external callers to property-
        # manipulation methods won't work.
        self._transaction.addOperation(self.properties().flush, "post-update property flush")

    def xmldata(self):
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
        if not hasattr(self, "_uid"):
            self._uid = self.xmldata
        return self._uid

class NotificationIndex(object):
    #
    # OK, here's where we get ugly.
    # The index code needs to be rewritten also, but in the meantime...
    #
    def __init__(self, notificationCollection):
        self.notificationCollection = notificationCollection
        stubResource = CommonStubResource(notificationCollection)
        self._oldIndex = OldNotificationIndex(stubResource)

