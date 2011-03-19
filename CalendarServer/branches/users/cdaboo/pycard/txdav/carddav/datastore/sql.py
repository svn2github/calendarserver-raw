# -*- test-case-name: txdav.carddav.datastore.test.test_sql -*-
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
SQL backend for CardDAV storage.
"""

__all__ = [
    "AddressBookHome",
    "AddressBook",
    "AddressBookObject",
]

from zope.interface.declarations import implements

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import hashlib

from twext.web2.dav.element.rfc2518 import ResourceType
from twext.web2.http_headers import MimeType

from twistedcaldav import carddavxml, customxml
from twistedcaldav.memcacher import Memcacher
from twistedcaldav.vcard import Component as VCard

from txdav.common.datastore.sql_legacy import \
    PostgresLegacyABIndexEmulator, SQLLegacyAddressBookInvites,\
    SQLLegacyAddressBookShares

from txdav.carddav.datastore.util import validateAddressBookComponent
from txdav.carddav.iaddressbookstore import IAddressBookHome, IAddressBook,\
    IAddressBookObject

from txdav.common.datastore.sql import CommonHome, CommonHomeChild,\
    CommonObjectResource
from twext.enterprise.dal.syntax import Insert
from twext.enterprise.dal.syntax import Update
from twext.enterprise.dal.syntax import utcNowSQL
from txdav.common.datastore.sql_tables import ADDRESSBOOK_TABLE,\
    ADDRESSBOOK_BIND_TABLE, ADDRESSBOOK_OBJECT_REVISIONS_TABLE,\
    ADDRESSBOOK_OBJECT_TABLE, ADDRESSBOOK_HOME_TABLE,\
    ADDRESSBOOK_HOME_METADATA_TABLE, ADDRESSBOOK_AND_ADDRESSBOOK_BIND,\
    ADDRESSBOOK_OBJECT_AND_BIND_TABLE, \
    ADDRESSBOOK_OBJECT_REVISIONS_AND_BIND_TABLE, schema
from txdav.base.propertystore.base import PropertyName



class AddressBookHome(CommonHome):

    implements(IAddressBookHome)

    # structured tables.  (new, preferred)
    _homeSchema = schema.ADDRESSBOOK_HOME
    _bindSchema = schema.ADDRESSBOOK_BIND
    _homeMetaDataSchema = schema.ADDRESSBOOK_HOME_METADATA
    _revisionsSchema = schema.ADDRESSBOOK_OBJECT_REVISIONS
    _objectSchema = schema.ADDRESSBOOK_OBJECT

    # string mappings (old, removing)
    _homeTable = ADDRESSBOOK_HOME_TABLE
    _homeMetaDataTable = ADDRESSBOOK_HOME_METADATA_TABLE
    _childTable = ADDRESSBOOK_TABLE
    _bindTable = ADDRESSBOOK_BIND_TABLE
    _objectBindTable = ADDRESSBOOK_OBJECT_AND_BIND_TABLE
    _notifierPrefix = "CardDAV"
    _revisionsTable = ADDRESSBOOK_OBJECT_REVISIONS_TABLE

    _cacher = Memcacher("SQL.adbkhome", pickle=True, key_normalization=False)

    def __init__(self, transaction, ownerUID, notifiers):

        self._childClass = AddressBook
        super(AddressBookHome, self).__init__(transaction, ownerUID, notifiers)
        self._shares = SQLLegacyAddressBookShares(self)


    addressbooks = CommonHome.children
    listAddressbooks = CommonHome.listChildren
    loadAddressbooks = CommonHome.loadChildren
    addressbookWithName = CommonHome.childWithName
    createAddressBookWithName = CommonHome.createChildWithName
    removeAddressBookWithName = CommonHome.removeChildWithName


    def createdHome(self):
        return self.createAddressBookWithName("addressbook")



class AddressBook(CommonHomeChild):
    """
    File-based implementation of L{IAddressBook}.
    """
    implements(IAddressBook)

    # structured tables.  (new, preferred)
    _bindSchema = schema.ADDRESSBOOK_BIND
    _homeChildSchema = schema.ADDRESSBOOK
    _revisionsSchema = schema.ADDRESSBOOK_OBJECT_REVISIONS
    _objectSchema = schema.ADDRESSBOOK_OBJECT

    # string mappings (old, removing)
    _bindTable = ADDRESSBOOK_BIND_TABLE
    _homeChildTable = ADDRESSBOOK_TABLE
    _homeChildBindTable = ADDRESSBOOK_AND_ADDRESSBOOK_BIND
    _revisionsTable = ADDRESSBOOK_OBJECT_REVISIONS_TABLE
    _revisionsBindTable = ADDRESSBOOK_OBJECT_REVISIONS_AND_BIND_TABLE
    _objectTable = ADDRESSBOOK_OBJECT_TABLE

    def __init__(self, home, name, resourceID, owned):
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

        super(AddressBook, self).__init__(home, name, resourceID, owned)

        self._index = PostgresLegacyABIndexEmulator(self)
        self._invites = SQLLegacyAddressBookInvites(self)
        self._objectResourceClass = AddressBookObject


    @property
    def _addressbookHome(self):
        return self._home


    def resourceType(self):
        return ResourceType.addressbook #@UndefinedVariable


    ownerAddressBookHome = CommonHomeChild.ownerHome
    addressbookObjects = CommonHomeChild.objectResources
    listAddressbookObjects = CommonHomeChild.listObjectResources
    addressbookObjectWithName = CommonHomeChild.objectResourceWithName
    addressbookObjectWithUID = CommonHomeChild.objectResourceWithUID
    createAddressBookObjectWithName = CommonHomeChild.createObjectResourceWithName
    removeAddressBookObjectWithName = CommonHomeChild.removeObjectResourceWithName
    removeAddressBookObjectWithUID = CommonHomeChild.removeObjectResourceWithUID
    addressbookObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken


    def initPropertyStore(self, props):
        # Setup peruser special properties
        props.setSpecialProperties(
            (
                PropertyName.fromElement(carddavxml.AddressBookDescription),
            ),
            (
                PropertyName.fromElement(customxml.GETCTag),
            ),
        )


    def _doValidate(self, component):
        component.validForCardDAV()


    def contentType(self):
        """
        The content type of Addresbook objects is text/vcard.
        """
        return MimeType.fromString("text/vcard; charset=utf-8")



class AddressBookObject(CommonObjectResource):

    implements(IAddressBookObject)

    _objectTable = ADDRESSBOOK_OBJECT_TABLE
    _objectSchema = schema.ADDRESSBOOK_OBJECT

    def __init__(self, addressbook, name, uid, resourceID=None, metadata=None):

        super(AddressBookObject, self).__init__(addressbook, name, uid, resourceID)


    @property
    def _addressbook(self):
        return self._parentCollection


    def addressbook(self):
        return self._addressbook


    @inlineCallbacks
    def setComponent(self, component, inserting=False):

        validateAddressBookComponent(self, self._addressbook, component, inserting)

        yield self.updateDatabase(component, inserting=inserting)
        if inserting:
            yield self._addressbook._insertRevision(self._name)
        else:
            yield self._addressbook._updateRevision(self._name)

        self._addressbook.notifyChanged()


    @inlineCallbacks
    def updateDatabase(self, component, expand_until=None, reCreate=False,
                       inserting=False):
        """
        Update the database tables for the new data being written.

        @param component: addressbook data to store
        @type component: L{Component}
        """

        ao = schema.ADDRESSBOOK_OBJECT

        componentText = str(component)
        self._objectText = componentText

        # ADDRESSBOOK_OBJECT table update
        self._md5 = hashlib.md5(componentText).hexdigest()
        self._size = len(componentText)
        if inserting:
            self._resourceID, self._created, self._modified = (
                yield Insert(
                    {ao.ADDRESSBOOK_RESOURCE_ID: self._addressbook._resourceID,
                     ao.RESOURCE_NAME: self._name,
                     ao.VCARD_TEXT: componentText,
                     ao.VCARD_UID: component.resourceUID(),
                     ao.MD5: self._md5},
                    Return=(ao.RESOURCE_ID,
                            ao.CREATED,
                            ao.MODIFIED)
                ).on(self._txn))[0]
        else:
            self._modified = (yield Update(
                {ao.VCARD_TEXT: componentText,
                 ao.VCARD_UID: component.resourceUID(),
                 ao.MD5: self._md5,
                 ao.MODIFIED: utcNowSQL},
                Where=ao.RESOURCE_ID == self._resourceID,
                Return=ao.MODIFIED).on(self._txn))[0][0]


    @inlineCallbacks
    def component(self):
        returnValue(VCard.fromString((yield self.vCardText())))


    vCardText = CommonObjectResource.text


    # IDataStoreObject
    def contentType(self):
        """
        The content type of Addressbook objects is text/x-vcard.
        """
        return MimeType.fromString("text/vcard; charset=utf-8")



