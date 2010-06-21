# -*- test-case-name: txcarddav.addressbookstore.test.test_file -*-
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
File addressbook store.
"""

__all__ = [
    "AddressBookStore",
    "AddressBookHome",
    "AddressBook",
    "AddressBookObject",
]

from uuid import uuid4
from errno import EEXIST, ENOENT

from zope.interface import implements

from twisted.python.util import FancyEqMixin

from twisted.internet.defer import succeed

from twisted.python import log
from twext.python.log import LoggingMixIn
from twext.web2.dav.element.rfc2518 import ResourceType

from txdav.propertystore.xattr import PropertyStore
from txdav.propertystore.base import PropertyName
PN = PropertyName.fromString

from txcarddav.iaddressbookstore import IAddressBookStoreTransaction
from txcarddav.iaddressbookstore import IAddressBookStore, IAddressBookHome
from txcarddav.iaddressbookstore import IAddressBook, IAddressBookObject
from txcarddav.iaddressbookstore import AddressBookNameNotAllowedError
from txcarddav.iaddressbookstore import AddressBookObjectNameNotAllowedError
from txcarddav.iaddressbookstore import AddressBookAlreadyExistsError
from txcarddav.iaddressbookstore import AddressBookObjectNameAlreadyExistsError
from txcarddav.iaddressbookstore import NoSuchAddressBookError
from txcarddav.iaddressbookstore import NoSuchAddressBookObjectError
from txcarddav.iaddressbookstore import InvalidAddressBookComponentError
from txcarddav.iaddressbookstore import InternalDataStoreError

from twistedcaldav.customxml import GETCTag

from twistedcaldav.vcardindex import AddressBookIndex as OldIndex
from twistedcaldav.vcard import Component as VComponent, InvalidVCardDataError

def _isValidName(name):
    """
    Determine if the given string is a valid name.  i.e. does it conflict with
    any of the other entities which may be on the filesystem?

    @param name: a name which might be given to a addressbook.
    """
    return not name.startswith(".")


def _hidden(path):
    return path.sibling('.' + path.basename())


_unset = object()

class _cached(object):
    """
    This object is a decorator for a 0-argument method which should be called
    only once, and its result cached so that future invocations just return the
    same result without calling the underlying method again.

    @ivar thunk: the function to call to generate a cached value.
    """

    def __init__(self, thunk):
        self.thunk = thunk


    def __get__(self, oself, owner):
        def inner():
            cacheKey = "_" + self.thunk.__name__ + "_cached"
            cached = getattr(oself, cacheKey, _unset)
            if cached is _unset:
                value = self.thunk(oself)
                setattr(oself, cacheKey, value)
                return value
            else:
                return cached
        return inner



def _writeOperation(thunk):
    # FIXME: tests
    def inner(self, *a, **kw):
        if self._transaction._termination is not None:
            raise RuntimeError(
                "%s.%s is a write operation, but transaction already %s"
                % (self, thunk.__name__, self._transaction._termination))
        return thunk(self, *a, **kw)
    return inner


class AddressBookStore(LoggingMixIn):
    """
    An implementation of L{IAddressBookObject} backed by a
    L{twext.python.filepath.CachingFilePath}.

    @ivar _path: A L{CachingFilePath} referencing a directory on disk that
        stores all addressbook data for a group of uids.
    """
    implements(IAddressBookStore)

    def __init__(self, path):
        """
        Create an addressbook store.

        @param path: a L{FilePath} pointing at a directory on disk.
        """
        self._path = path

#        if not path.isdir():
            # FIXME: Add AddressBookStoreNotFoundError?
#            raise NotFoundError("No such addressbook store")

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)

    def newTransaction(self):
        """
        Create a new filesystem-based transaction.

        @see Transaction
        """
        return Transaction(self)



class _CommitTracker(object):
    """
    Diagnostic tool to find transactions that were never committed.
    """

    def __init__(self):
        self.done = False
        self.info = []

    def __del__(self):
        if not self.done and self.info:
            print '**** UNCOMMITTED TRANSACTION BEING GARBAGE COLLECTED ****'
            for info in self.info:
                print '   ', info
            print '---- END OF OPERATIONS'



class Transaction(LoggingMixIn):
    """
    In-memory implementation of

    Note that this provides basic 'undo' support, but not truly transactional
    operations.
    """

    implements(IAddressBookStoreTransaction)

    def __init__(self, addressbookStore):
        """
        Initialize a transaction; do not call this directly, instead call
        L{AddressBookStore.newTransaction}.

        @param addressbookStore: The store that created this transaction.

        @type addressbookStore: L{AddressBookStore}
        """
        self._addressbookStore = addressbookStore
        self._termination = None
        self._operations = []
        self._tracker = _CommitTracker()
        self._addressbookHomes = {}


    def addOperation(self, operation, name):
        self._operations.append(operation)
        self._tracker.info.append(name)


    def _terminate(self, mode):
        """
        Check to see if this transaction has already been terminated somehow,
        either via committing or aborting, and if not, note that it has been
        terminated.

        @param mode: The manner of the termination of this transaction.
        
        @type mode: C{str}

        @raise RuntimeError: This transaction has already been terminated.
        """
        if self._termination is not None:
            raise RuntimeError("already %s" % (self._termination,))
        self._termination = mode
        self._tracker.done = True


    def abort(self):
        self._terminate("aborted")


    def commit(self):
        self._terminate("committed")

        self.committed = True
        undos = []

        for operation in self._operations:
            try:
                undo = operation()
                if undo is not None:
                    undos.append(undo)
            except:
                log.err()
                for undo in undos:
                    try:
                        undo()
                    except:
                        log.err()
                raise


    def addressbookHomeWithUID(self, uid, create=False):
        if (uid, self) in self._addressbookHomes:
            return self._addressbookHomes[(uid, self)]

        if uid.startswith("."):
            return None

        assert len(uid) >= 4

        childPath1 = self._addressbookStore._path.child(uid[0:2])
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
                addressbookPath = childPath3
            else:
                creating = True
                addressbookPath = childPath3.temporarySibling()
                createDirectory(addressbookPath)
                def do():
                    def lastly():
                        addressbookPath.moveTo(childPath3)
                        # addressbookHome._path = addressbookPath
                        # do this _after_ all other file operations
                        addressbookHome._path = childPath3
                        return lambda : None
                    self.addOperation(lastly, "create home finalize")
                    return lambda : None
                self.addOperation(do, "create home UID %r" % (uid,))

        elif not childPath3.isdir():
            return None
        else:
            addressbookPath = childPath3

        addressbookHome = AddressBookHome(addressbookPath, self._addressbookStore, self)
        self._addressbookHomes[(uid, self)] = addressbookHome
        if creating:
            addressbookHome.createAddressBookWithName("addressbook")
        return addressbookHome



class AddressBookHome(LoggingMixIn):
    implements(IAddressBookHome)

    def __init__(self, path, addressbookStore, transaction):
        self._addressbookStore = addressbookStore
        self._path = path
        self._transaction = transaction
        self._newAddressBooks = {}
        self._removedAddressBooks = set()


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path)


    def uid(self):
        return self._path.basename()


    def _updateSyncToken(self, reset=False):
        "Stub for updating sync token."
        # FIXME: actually update something


    def addressbooks(self):
        return set(self._newAddressBooks.itervalues()) | set(
            self.addressbookWithName(name)
            for name in self._path.listdir()
            if not name.startswith(".")
        )

    def addressbookWithName(self, name):
        addressbook = self._newAddressBooks.get(name)
        if addressbook is not None:
            return addressbook
        if name in self._removedAddressBooks:
            return None

        if name.startswith("."):
            return None

        childPath = self._path.child(name)
        if childPath.isdir():
            return AddressBook(name, self)
        else:
            return None


    @_writeOperation
    def createAddressBookWithName(self, name):
        if name.startswith("."):
            raise AddressBookNameNotAllowedError(name)

        childPath = self._path.child(name)

        if name not in self._removedAddressBooks and childPath.isdir():
            raise AddressBookAlreadyExistsError(name)

        temporary = _hidden(childPath.temporarySibling())
        temporary.createDirectory()
        # In order for the index to work (which is doing real file ops on disk
        # via SQLite) we need to create a real directory _immediately_.

        # FIXME: some way to roll this back.

        c = self._newAddressBooks[name] = AddressBook(temporary.basename(), self, name)
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
                    raise AddressBookAlreadyExistsError(name)
                raise
            # FIXME: direct tests, undo for index creation
            # Return undo
            return lambda: childPath.remove()

        self._transaction.addOperation(do, "create addressbook %r" % (name,))
        props = c.properties()
        AddressBookType = ResourceType.addressbook #@UndefinedVariable
        props[PN(ResourceType.sname())] = AddressBookType

        # FIXME: there's no need for 'flush' to be a public method of the
        # property store any more.  It should just be transactional like
        # everything else; the API here would better be expressed as
        # c.properties().participateInTxn(txn)
        # FIXME: return c # maybe ?

    @_writeOperation
    def removeAddressBookWithName(self, name):
        if name.startswith(".") or name in self._removedAddressBooks:
            raise NoSuchAddressBookError(name)

        self._removedAddressBooks.add(name)
        childPath = self._path.child(name)
        if name not in self._newAddressBooks and not childPath.isdir():
            raise NoSuchAddressBookError(name)

        def do(transaction=self._transaction):
            for i in xrange(1000):
                trash = childPath.sibling("._del_%s_%d" % (childPath.basename(), i))
                if not trash.exists():
                    break
            else:
                raise InternalDataStoreError("Unable to create trash target for addressbook at %s" % (childPath,))

            try:
                childPath.moveTo(trash)
            except (IOError, OSError), e:
                if e.errno == ENOENT:
                    raise NoSuchAddressBookError(name)
                raise

            def cleanup():
                try:
                    trash.remove()
                except Exception, e:
                    self.log_error("Unable to delete trashed addressbook at %s: %s" % (trash.fp, e))

            transaction.addOperation(cleanup, "remove addressbook %r" % (name,))

            def undo():
                trash.moveTo(childPath)

            return undo


    # @_cached
    def properties(self):
        # FIXME: needs tests for actual functionality
        # FIXME: needs to be cached
        # FIXME: transaction tests
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "flush home properties")
        return props



class AddressBook(LoggingMixIn, FancyEqMixin):
    """
    File-based implementation of L{IAddressBook}.
    """
    implements(IAddressBook)

    compareAttributes = '_name _addressbookHome _transaction'.split()

    def __init__(self, name, addressbookHome, realName=None):
        """
        Initialize an addressbook pointing at a path on disk.

        @param name: the subdirectory of addressbookHome where this addressbook
            resides.
        @type name: C{str}

        @param addressbookHome: the home containing this addressbook.
        @type addressbookHome: L{AddressBookHome}

        @param realName: If this addressbook was just created, the name which it
        will eventually have on disk.
        @type realName: C{str}
        """
        self._name = name
        self._addressbookHome = addressbookHome
        self._transaction = addressbookHome._transaction
        self._newAddressBookObjects = {}
        self._cachedAddressBookObjects = {}
        self._removedAddressBookObjects = set()
        self._index = Index(self)
        self._renamedName = realName


    @property
    def _path(self):
        return self._addressbookHome._path.child(self._name)


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

    @_writeOperation
    def rename(self, name):
        oldName = self.name()
        self._renamedName = name
        self._addressbookHome._newAddressBooks[name] = self
        self._addressbookHome._removedAddressBooks.add(oldName)
        def doIt():
            self._path.moveTo(self._path.sibling(name))
            return lambda : None # FIXME: revert
        self._transaction.addOperation(doIt, "rename addressbook %r -> %r" %
                                       (oldName, name))


    def ownerAddressBookHome(self):
        return self._addressbookHome


    def addressbookObjects(self):
        return sorted((
            self.addressbookObjectWithName(name)
            for name in (
                set(self._newAddressBookObjects.iterkeys()) |
                set(name for name in self._path.listdir()
                    if not name.startswith(".")) -
                set(self._removedAddressBookObjects)
            )),
            key=lambda calObj: calObj.name()
        )


    def addressbookObjectWithName(self, name):
        if name in self._removedAddressBookObjects:
            return None
        if name in self._newAddressBookObjects:
            return self._newAddressBookObjects[name]
        if name in self._cachedAddressBookObjects:
            return self._cachedAddressBookObjects[name]

        addressbookObjectPath = self._path.child(name)
        if addressbookObjectPath.isfile():
            obj = AddressBookObject(name, self)
            self._cachedAddressBookObjects[name] = obj
            return obj
        else:
            return None


    def addressbookObjectWithUID(self, uid):
        # FIXME: This _really_ needs to be inspecting an index, not parsing
        # every resource.
        for addressbookObjectPath in self._path.children():
            if not _isValidName(addressbookObjectPath.basename()):
                continue
            obj = AddressBookObject(addressbookObjectPath.basename(), self)
            if obj.component().resourceUID() == uid:
                if obj.name() in self._removedAddressBookObjects:
                    return None
                return obj


    @_writeOperation
    def createAddressBookObjectWithName(self, name, component):
        if name.startswith("."):
            raise AddressBookObjectNameNotAllowedError(name)

        addressbookObjectPath = self._path.child(name)
        if addressbookObjectPath.exists():
            raise AddressBookObjectNameAlreadyExistsError(name)

        addressbookObject = AddressBookObject(name, self)
        addressbookObject.setComponent(component)
        self._cachedAddressBookObjects[name] = addressbookObject


    @_writeOperation
    def removeAddressBookObjectWithName(self, name):
        if name.startswith("."):
            raise NoSuchAddressBookObjectError(name)

        addressbookObjectPath = self._path.child(name)
        if addressbookObjectPath.isfile():
            self._removedAddressBookObjects.add(name)
            # FIXME: test for undo
            def do():
                addressbookObjectPath.remove()
                return lambda: None
            self._transaction.addOperation(do, "remove addressbook object %r" %
                                           (name,))
        else:
            raise NoSuchAddressBookObjectError(name)


    @_writeOperation
    def removeAddressBookObjectWithUID(self, uid):
        self.removeAddressBookObjectWithName(
            self.addressbookObjectWithUID(uid)._path.basename())


    def syncToken(self):
        raise NotImplementedError()


    def _updateSyncToken(self, reset=False):
        # FIXME: add locking a-la CalDAVFile.bumpSyncToken
        # FIXME: tests for desired concurrency properties
        ctag = PropertyName.fromString(GETCTag.sname())
        props = self.properties()
        token = props.get(ctag)
        if token is None or reset:
            adbkuuid = uuid4()
            revision = 1
        else:
            adbkuuid, revision = token.split("#", 1)
            revision = int(revision) + 1
        token = "%s#%d" % (adbkuuid, revision)
        props[ctag] = GETCTag(token)
        # FIXME: no direct tests for commit
        succeed(token)


    def addressbookObjectsSinceToken(self, token):
        raise NotImplementedError()


    # FIXME: property writes should be a write operation
    @_cached
    def properties(self):
        # FIXME: needs direct tests - only covered by addressbook store tests
        # FIXME: transactions
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "flush addressbook properties")
        return props
    
    
    def _doValidate(self, component):
        component.validForCardDAV()



class AddressBookObject(LoggingMixIn):
    """
    @ivar _path: The path of the .vcf file on disk

    @type _path: L{FilePath}
    """
    implements(IAddressBookObject)

    def __init__(self, name, addressbook):
        self._name = name
        self._addressbook = addressbook
        self._transaction = addressbook._transaction
        self._component = None


    @property
    def _path(self):
        return self._addressbook._path.child(self._name)


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    def name(self):
        return self._path.basename()


    @_writeOperation
    def setComponent(self, component):
        if not isinstance(component, VComponent):
            raise TypeError(type(component))

        try:
            if component.resourceUID() != self.uid():
                raise InvalidAddressBookComponentError(
                    "UID may not change (%s != %s)" % (
                        component.resourceUID(), self.uid()
                     )
                )
        except NoSuchAddressBookObjectError:
            pass

        try:
            self._addressbook._doValidate(component)
        except InvalidVCardDataError, e:
            raise InvalidAddressBookComponentError(e)

        self._component = component
        # FIXME: needs to clear text cache

        def do():
            backup = None
            if self._path.exists():
                backup = _hidden(self._path.temporarySibling())
                self._path.moveTo(backup)
            fh = self._path.open("w")
            try:
                # FIXME: concurrency problem; if this write is interrupted
                # halfway through, the underlying file will be corrupt.
                fh.write(str(component))
            finally:
                fh.close()
            def undo():
                if backup:
                    backup.moveTo(self._path)
                else:
                    self._path.remove()
            return undo
        self._transaction.addOperation(do, "set addressbook component %r" % (self.name(),))
        # Mark all properties as dirty, so they will be re-added to the
        # temporary file when the main file is deleted. NOTE: if there were a
        # temporary file and a rename() as there should be, this should really
        # happen after the write but before the rename.
        self.properties().update(self.properties())
        # FIXME: the property store's flush() method may already have been
        # added to the transaction, but we need to add it again to make sure it
        # happens _after_ the new file has been written.  we may end up doing
        # the work multiple times, and external callers to property-
        # manipulation methods won't work.
        self._transaction.addOperation(self.properties().flush, "post-update property flush")


    def component(self):
        if self._component is not None:
            return self._component
        text = self.vCardText()

        try:
            component = VComponent.fromString(text)
        except InvalidVCardDataError, e:
            raise InternalDataStoreError(
                "File corruption detected (%s) in file: %s"
                % (e, self._path.path)
            )
        return component


    def vCardText(self):
        if self._component is not None:
            return str(self._component)
        try:
            fh = self._path.open()
        except IOError, e:
            if e[0] == ENOENT:
                raise NoSuchAddressBookObjectError(self)
            else:
                raise

        try:
            text = fh.read()
        finally:
            fh.close()

        if not (
            text.startswith("BEGIN:VCARD\r\n") or
            text.endswith("\r\nEND:VCARD\r\n")
        ):
            raise InternalDataStoreError(
                "File corruption detected (improper start) in file: %s"
                % (self._path.path,)
            )
        return text


    def uid(self):
        if not hasattr(self, "_uid"):
            self._uid = self.component().resourceUID()
        return self._uid

    @_cached
    def properties(self):
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "object properties flush")
        return props



class Index(object):
    #
    # OK, here's where we get ugly.
    # The index code needs to be rewritten also, but in the meantime...
    #
    class StubResource(object):
        """
        Just enough resource to keep the Index class going.
        """
        def __init__(self, addressbook):
            self.addressbook = addressbook
            self.fp = self.addressbook._path

        def isAddressBookCollection(self):
            return True

        def getChild(self, name):
            addressbookObject = self.addressbook.addressbookObjectWithName(name)
            if addressbookObject:
                class ChildResource(object):
                    def __init__(self, addressbookObject):
                        self.addressbookObject = addressbookObject

                    def iAddressBook(self):
                        return self.addressbookObject.component()

                return ChildResource(addressbookObject)
            else:
                return None

        def bumpSyncToken(self, reset=False):
            # FIXME: needs direct tests
            return self.addressbook._updateSyncToken(reset)


        def initSyncToken(self):
            # FIXME: needs direct tests
            self.bumpSyncToken(True)


    def __init__(self, addressbook):
        self.addressbook = addressbook
        stubResource = Index.StubResource(addressbook)
        self._oldIndex = OldIndex(stubResource)


    def addressbookObjects(self):
        addressbook = self.addressbook
        for name, uid, componentType in self._oldIndex.bruteForceSearch():
            addressbookObject = addressbook.addressbookObjectWithName(name)

            # Precache what we found in the index
            addressbookObject._uid = uid
            addressbookObject._componentType = componentType

            yield addressbookObject
