# -*- test-case-name: txdav.datastore.test.test_file -*-
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

from errno import EEXIST, ENOENT

from twext.python.log import LoggingMixIn
from twext.web2.dav.element.rfc2518 import ResourceType

from txdav.datastore.file import DataStoreTransaction, DataStore, writeOperation,\
    hidden, isValidName, cached
from txdav.propertystore.base import PropertyName
from txdav.propertystore.xattr import PropertyStore
from txdav.common.icommondatastore import HomeChildNameNotAllowedError,\
    HomeChildNameAlreadyExistsError, NoSuchHomeChildError,\
    InternalDataStoreError, ObjectResourceNameNotAllowedError,\
    ObjectResourceNameAlreadyExistsError, NoSuchObjectResourceError
from twisted.python.util import FancyEqMixin
from twistedcaldav.customxml import GETCTag
from uuid import uuid4

"""
Common utility functions for a file based datastore.
"""

class CommonDataStore(DataStore):
    """
    Generic data store.
    """
    pass

class CommonStoreTransaction(DataStoreTransaction):
    """
    In-memory implementation of

    Note that this provides basic 'undo' support, but not truly transactional
    operations.
    """

    _homeClass = None

    def __init__(self, dataStore):
        """
        Initialize a transaction; do not call this directly, instead call
        L{DataStore.newTransaction}.

        @param dataStore: The store that created this transaction.

        @type dataStore: L{DataStore}
        """
        super(CommonStoreTransaction, self).__init__(dataStore)
        self._homes = {}


    def homeWithUID(self, uid, create=False):
        if (uid, self) in self._homes:
            return self._homes[(uid, self)]

        if uid.startswith("."):
            return None

        assert len(uid) >= 4

        childPath1 = self._dataStore._path.child(uid[0:2])
        childPath2 = childPath1.child(uid[2:4])
        childPath3 = childPath2.child(uid)
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
            if not childPath2.isdir():
                if not childPath1.isdir():
                    createDirectory(childPath1)
                createDirectory(childPath2)
            if childPath3.isdir():
                homePath = childPath3
            else:
                creating = True
                homePath = childPath3.temporarySibling()
                createDirectory(homePath)
                def do():
                    def lastly():
                        homePath.moveTo(childPath3)
                        # home._path = homePath
                        # do this _after_ all other file operations
                        home._path = childPath3
                        return lambda : None
                    self.addOperation(lastly, "create home finalize")
                    return lambda : None
                self.addOperation(do, "create home UID %r" % (uid,))

        elif not childPath3.isdir():
            return None
        else:
            homePath = childPath3

        home = self._homeClass(homePath, self._dataStore, self)
        self._homes[(uid, self)] = home
        if creating:
            self.creatingHome(home)
        return home

    def creatingHome(self, home):
        raise NotImplementedError

class CommonHome(LoggingMixIn):

    _childClass = None

    def __init__(self, path, dataStore, transaction):
        self._dataStore = dataStore
        self._path = path
        self._transaction = transaction
        self._newChildren = {}
        self._removedChildren = set()
        self._cachedChildren = {}


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path)


    def uid(self):
        return self._path.basename()


    def _updateSyncToken(self, reset=False):
        "Stub for updating sync token."
        # FIXME: actually update something


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


    def vCardText(self):
        raise NotImplementedError


    def uid(self):
        raise NotImplementedError

    @cached
    def properties(self):
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "object properties flush")
        return props

