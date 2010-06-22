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

from errno import ENOENT

from twext.web2.dav.element.rfc2518 import ResourceType

from twistedcaldav.vcard import Component as VComponent, InvalidVCardDataError
from twistedcaldav.vcardindex import AddressBookIndex as OldIndex

from txcarddav.iaddressbookstore import IAddressBook, IAddressBookObject
from txcarddav.iaddressbookstore import IAddressBookStore, IAddressBookHome
from txcarddav.iaddressbookstore import IAddressBookStoreTransaction

from txdav.common.datastore.file import CommonDataStore, CommonHome,\
    CommonStoreTransaction, CommonHomeChild, CommonObjectResource
from txdav.common.icommondatastore import InvalidObjectResourceError,\
    NoSuchObjectResourceError, InternalDataStoreError
from txdav.datastore.file import hidden, writeOperation

from zope.interface import implements

class AddressBookStore(CommonDataStore):
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
        super(AddressBookStore, self).__init__(path)
        self._transactionClass = AddressBookStoreTransaction


class AddressBookStoreTransaction(CommonStoreTransaction):
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
        super(AddressBookStoreTransaction, self).__init__(addressbookStore)
        self._homeClass = AddressBookHome


    addressbookHomeWithUID = CommonStoreTransaction.homeWithUID

    def creatingHome(self, home):
        home.createAddressBookWithName("addressbook")

class AddressBookHome(CommonHome):
    implements(IAddressBookHome)

    def __init__(self, path, addressbookStore, transaction):
        super(AddressBookHome, self).__init__(path, addressbookStore, transaction)

        self._childClass = AddressBook

    addressbooks = CommonHome.children
    addressbookWithName = CommonHome.childWithName
    createAddressBookWithName = CommonHome.createChildWithName
    removeAddressBookWithName = CommonHome.removeChildWithName

    @property
    def _addressbookStore(self):
        return self._dataStore



class AddressBook(CommonHomeChild):
    """
    File-based implementation of L{IAddressBook}.
    """
    implements(IAddressBook)

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
        
        super(AddressBook, self).__init__(name, addressbookHome, realName)

        self._index = Index(self)
        self._objectResourceClass = AddressBookObject

    @property
    def _addressbookHome(self):
        return self._home

    def resourceType(self):
        return ResourceType.addressbook

    addressbooks = CommonHome.children
    ownerAddressBookHome = CommonHomeChild.ownerHome
    addressbookObjects = CommonHomeChild.objectResources
    addressbookObjectWithName = CommonHomeChild.objectResourceWithName
    addressbookObjectWithUID = CommonHomeChild.objectResourceWithUID
    createAddressBookObjectWithName = CommonHomeChild.createObjectResourceWithName
    removeAddressBookObjectWithName = CommonHomeChild.removeObjectResourceWithName
    removeAddressBookObjectWithUID = CommonHomeChild.removeObjectResourceWithUID
    addressbookObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken


    def _doValidate(self, component):
        component.validForCardDAV()


class AddressBookObject(CommonObjectResource):
    """
    """
    implements(IAddressBookObject)

    def __init__(self, name, addressbook):

        super(AddressBookObject, self).__init__(name, addressbook)


    @property
    def _addressbook(self):
        return self._parentCollection

    @writeOperation
    def setComponent(self, component):
        if not isinstance(component, VComponent):
            raise TypeError(type(component))

        try:
            if component.resourceUID() != self.uid():
                raise InvalidObjectResourceError(
                    "UID may not change (%s != %s)" % (
                        component.resourceUID(), self.uid()
                     )
                )
        except NoSuchObjectResourceError:
            pass

        try:
            self._addressbook._doValidate(component)
        except InvalidVCardDataError, e:
            raise InvalidObjectResourceError(e)

        newRevision = self._addressbook._updateSyncToken() # FIXME: test
        self._addressbook.retrieveOldIndex().addResource(
            self.name(), component, newRevision
        )

        self._component = component
        # FIXME: needs to clear text cache

        def do():
            backup = None
            if self._path.exists():
                backup = hidden(self._path.temporarySibling())
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
                raise NoSuchObjectResourceError(self)
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
