# -*- test-case-name: txdav.carddav.datastore.test.test_sql -*-
##
# Copyright (c) 2010-2012 Apple Inc. All rights reserved.
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
from txdav.common.icommondatastore import InternalDataStoreError

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

from txdav.xml.rfc2518 import ResourceType
from twext.web2.http_headers import MimeType

from twistedcaldav import carddavxml, customxml
from twistedcaldav.memcacher import Memcacher
from twistedcaldav.vcard import Component as VCard, InvalidVCardDataError

from txdav.common.datastore.sql_legacy import \
    PostgresLegacyABIndexEmulator, SQLLegacyAddressBookInvites,\
    SQLLegacyAddressBookShares, SQLLegacySharedGroupInvites

from txdav.carddav.datastore.util import validateAddressBookComponent
from txdav.carddav.iaddressbookstore import IAddressBookHome, IAddressBook,\
    IAddressBookObject

from txdav.common.datastore.sql import CommonHome, CommonHomeChild,\
    CommonObjectResource
from twext.enterprise.dal.syntax import Delete
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

from txdav.base.propertystore.sql import PropertyStore
from txdav.common.datastore.sql_tables import _BIND_MODE_OWN, \
    _BIND_STATUS_ACCEPTED

from twext.enterprise.dal.syntax import Delete, utcNowSQL
from twext.enterprise.dal.syntax import Insert
from twext.enterprise.dal.syntax import Max
from twext.enterprise.dal.syntax import Parameter
from twext.enterprise.dal.syntax import Select
from twext.enterprise.dal.syntax import Update

from twext.python.clsprop import classproperty

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

    _dataVersionKey = "ADDRESSBOOK-DATAVERSION"

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


    @inlineCallbacks
    def remove(self):
        ah = schema.ADDRESSBOOK_HOME
        ab = schema.ADDRESSBOOK_BIND
        ahm = schema.ADDRESSBOOK_HOME_METADATA
        aor = schema.ADDRESSBOOK_OBJECT_REVISIONS

        yield Delete(
            From=ahm,
            Where=ahm.RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield Delete(
            From=ab,
            Where=ab.ADDRESSBOOK_HOME_RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield Delete(
            From=aor,
            Where=aor.ADDRESSBOOK_HOME_RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield Delete(
            From=ah,
            Where=ah.RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield self._cacher.delete(str(self._ownerUID))


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
    _homeChildMetaDataSchema = schema.ADDRESSBOOK_METADATA
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


    def contentType(self):
        """
        The content type of Addresbook objects is text/vcard.
        """
        return MimeType.fromString("text/vcard; charset=utf-8")

    @classmethod
    @inlineCallbacks
    def loadAllObjects(cls, home, owned):
        print("xxx AddressBook.loadAllObjects() cls=%s" % (cls,))
        """
        Load all child objects and return a list of them. This must create the
        child classes and initialize them using "batched" SQL operations to keep
        this constant wrt the number of children. This is an optimization for
        Depth:1 operations on the home.
        """
        results = []

        # Load from the main table first
        if owned:
            query = cls._ownedHomeChildrenQuery
        else:
            query = cls._sharedHomeChildrenQuery
        dataRows = (yield query.on(home._txn, resourceID=home._resourceID))
        print("xxx AddressBook.loadAllObjects() dataRows=%s" % (dataRows,))

        if dataRows:
            # Get property stores for all these child resources (if any found)
            propertyStores = (yield PropertyStore.forMultipleResources(
                home.uid(), home._txn,
                cls._bindSchema.RESOURCE_ID, cls._bindSchema.HOME_RESOURCE_ID,
                home._resourceID
            ))

            bind = cls._bindSchema
            rev = cls._revisionsSchema
            if owned:
                ownedCond = bind.BIND_MODE == _BIND_MODE_OWN
            else:
                ownedCond = bind.BIND_MODE != _BIND_MODE_OWN
            revisions = (yield Select(
                [rev.RESOURCE_ID, Max(rev.REVISION)],
                From=rev.join(bind, rev.RESOURCE_ID == bind.RESOURCE_ID, 'left'),
                Where=(bind.HOME_RESOURCE_ID == home._resourceID).
                    And(ownedCond).
                    And((rev.RESOURCE_NAME != None).Or(rev.DELETED == False)),
                GroupBy=rev.RESOURCE_ID
            ).on(home._txn))
            revisions = dict(revisions)
            
        
 
        # Create the actual objects merging in properties
        for items in dataRows:
            resourceID, resource_name, groupBindID = items[:3]
            
            print("xxx AddressBook.loadAllObjects() groupBindID=%s" % (groupBindID,))
            
            child = None
            if not owned:
                
                bind = schema.GROUP_BIND
                groupIDRows = (yield Select([bind.GROUP_ID,],
                     From=bind,
                     Where=(bind.ADDRESSBOOK_BIND_ID == groupBindID)).on(home._txn))
                
                print("xxx AddressBook.loadAllObjects() groupIDRows=%s" % (groupIDRows,))
                
                if groupIDRows:
                    [groupIDs] = groupIDRows
                    
                    for groupID in groupIDs:
                        
                        #debug, print group members
                        bind = schema.GROUP_MEMBERSHIP
                        memberRows = (yield Select([bind.MEMBER_ID,],
                                      From=bind,
                                      Where=bind.GROUP_ID == groupID).on(home._txn))
                    
                        print("xxx AddressBook.loadAllObjects() for groupID=%s" % (memberRows,))
                   
                        child = GroupAddressBook(home, resource_name, resourceID, groupIDs)
            
            if not child:
                child = cls(home, resource_name, resourceID, owned)
            
            metadata = items[3:]
            for attr, value in zip(cls.metadataAttributes(), metadata):
                setattr(child, attr, value)
            child._syncTokenRevision = revisions[resourceID]
            propstore = propertyStores.get(resourceID, None)
            yield child._loadPropertyStore(propstore)
            results.append(child)
        returnValue(results)

    @classmethod
    def _homeChildLookup(cls, ownedPart):
        """
        Common portions of C{_ownedResourceIDByName}
        C{_resourceIDSharedToHomeByName}, except for the 'owned' fragment of the
        Where clause, supplied as an argument.
        
        ADDED return of bind.GROUP_BIND_ID for shared groups
        """
        bind = cls._bindSchema
        return Select(
            [bind.RESOURCE_ID, bind.GROUP_BIND_ID,],
            From=bind,
            Where=(bind.RESOURCE_NAME == Parameter('objectName')).And(
                   bind.HOME_RESOURCE_ID == Parameter('homeID')).And(
                    ownedPart))

    @classproperty
    def _resourceIDOwnedByHomeByName(cls): #@NoSelf
        print("xxx AddressBook._resourceIDOwnedByHomeByName() cls=%s" % (cls,))
        """
        DAL query to look up an object resource ID owned by a home, given a
        resource name (C{objectName}), and a home resource ID
        (C{homeID}).
        """
        return cls._homeChildLookup(
            cls._bindSchema.BIND_MODE == _BIND_MODE_OWN)


    @classproperty
    def _resourceIDSharedToHomeByName(cls): #@NoSelf
        print("xxx AddressBook._resourceIDSharedToHomeByName() cls=%s" % (cls,))
        """
        DAL query to look up an object resource ID shared to a home, given a
        resource name (C{objectName}), and a home resource ID
        (C{homeID}).
        """
        return cls._homeChildLookup(
            (cls._bindSchema.BIND_MODE != _BIND_MODE_OWN).And(
                cls._bindSchema.BIND_STATUS == _BIND_STATUS_ACCEPTED))

    @classmethod
    @inlineCallbacks
    def objectWithName(cls, home, name, owned):
        print("xxx AddressBook.objectWithName() cls=%s, home=%s, name=%s, owned=%s" % (cls, home, name, owned,))
        """
        Retrieve the child with the given C{name} contained in the given
        C{home}.

        @param home: a L{CommonHome}.

        @param name: a string; the name of the L{CommonHomeChild} to retrieve.

        @param owned: a boolean - whether or not to get a shared child
        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        data = None
        queryCacher = home._txn.store().queryCacher
        # Only caching non-shared objects so that we don't need to invalidate
        # in sql_legacy
        if owned and queryCacher:
            # Retrieve data from cache
            cacheKey = queryCacher.keyForObjectWithName(home._resourceID, name)
            data = yield queryCacher.get(cacheKey)

        if data is None:
            # No cached copy
            if owned:
                query = cls._resourceIDOwnedByHomeByName
            else:
                query = cls._resourceIDSharedToHomeByName
            data = yield query.on(home._txn,
                                  objectName=name, homeID=home._resourceID)
            if owned and data and queryCacher:
                # Cache the result
                queryCacher.setAfterCommit(home._txn, cacheKey, data)

        print("xxx AddressBook.objectWithName() data=%s name=%s" % (data, name,))
        #traceback.print_exc()
        
        didGroupAddressbookQuery = False
        if not data and owned:
            data = yield cls._resourceIDSharedToHomeByName.on(home._txn,
                                  objectName=name, homeID=home._resourceID)

            print("xxx AddressBook.objectWithName() SHARED data=%s name=%s" % (data, name,))
            didGroupAddressbookQuery = True
            
        if not data:
            returnValue(None)

        resourceID, groupBindID = data[0]
        
        bind = schema.GROUP_BIND
        groupIDRows = (yield Select([bind.GROUP_ID,],
             From=bind,
             Where=(bind.ADDRESSBOOK_BIND_ID == groupBindID)).on(home._txn))
        
        print("xxx AddressBook.objectWithName() memberIDRows=%s name=%s" % (groupIDRows, name,))
        
        if groupIDRows:
            groupIDs = [];
            for groupIDRow in groupIDRows:
                groupIDs.extend(groupIDRow)
            child = GroupAddressBook(home, name, resourceID, groupIDs)
        elif didGroupAddressbookQuery:
            child = None
        else:
            child = cls(home, name, resourceID, owned)
            
        if child:
            yield child.initFromStore()
        returnValue(child)




class GroupAddressBook(AddressBook):
    """
    Implementation of L{IAddressBook} for shared group address books
    """
    implements(IAddressBook)


    def __init__(self, home, name, resourceID, groupIDs):
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

        super(GroupAddressBook, self).__init__(home, name, resourceID, False)
        print("xxx GroupAddressBook.__init__() self=%s, memberIDs=%s" % (self, groupIDs,))
        self._objectResourceClass = GroupAddressBookObject

        print("xxx GroupAddressBook.objectResources() self=%s, self._objectResourceClass=%s" % (self, self._objectResourceClass))

        self._groupIDs = groupIDs


    @classproperty
    def _memberIDsForGroupIDQuery(cls): #@NoSelf
        bind = schema.GROUP_MEMBERSHIP
        return Select([bind.MEMBER_ID,],
                      From=bind,
                      Where=bind.GROUP_ID == Parameter("groupID"))




    @classmethod
    @inlineCallbacks
    def listObjects(cls, home, owned):
        print("xxx GroupAddressBook.listObjects() cls=%s" % (cls,))
        """
        Retrieve the names of the children that exist in the given home.

        @return: an iterable of C{str}s.
        """
        # FIXME: tests don't cover this as directly as they should.
        if owned:
            rows = yield cls._ownedChildListQuery.on(
                home._txn, resourceID=home._resourceID)
        else:
            rows = yield cls._sharedChildListQuery.on(
                home._txn, resourceID=home._resourceID)
        names = [row[0] for row in rows]
        returnValue(names)


    @classmethod
    @inlineCallbacks
    def objectWithName(cls, home, name, owned):
        print("xxx GroupAddressBook.objectWithName() cls=%s" % (cls,))
        """
        Retrieve the child with the given C{name} contained in the given
        C{home}.

        @param home: a L{CommonHome}.

        @param name: a string; the name of the L{CommonHomeChild} to retrieve.

        @param owned: a boolean - whether or not to get a shared child
        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        data = None
        queryCacher = home._txn.store().queryCacher
        # Only caching non-shared objects so that we don't need to invalidate
        # in sql_legacy
        if owned and queryCacher:
            # Retrieve data from cache
            cacheKey = queryCacher.keyForObjectWithName(home._resourceID, name)
            data = yield queryCacher.get(cacheKey)

        if data is None:
            # No cached copy
            if owned:
                query = cls._resourceIDOwnedByHomeByName
            else:
                query = cls._resourceIDSharedToHomeByName
            data = yield query.on(home._txn,
                                  objectName=name, homeID=home._resourceID)
            if owned and data and queryCacher:
                # Cache the result
                queryCacher.setAfterCommit(home._txn, cacheKey, data)

        if not data:
            returnValue(None)

        resourceID = data[0][0]
        child = cls(home, name, resourceID, owned)
        yield child.initFromStore()
        returnValue(child)


    @classmethod
    @inlineCallbacks
    def objectWithID(cls, home, resourceID):
        print("xxx GroupAddressBook.objectWithID() cls=%s" % (cls,))
        """
        Retrieve the child with the given C{resourceID} contained in the given
        C{home}.

        @param home: a L{CommonHome}.
        @param resourceID: a string.
        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        data = yield cls._homeChildByIDQuery.on(
            home._txn, resourceID=resourceID, homeID=home._resourceID)
        if not data:
            returnValue(None)
        
        # TODO: filter here
            
        name, mode = data[0]
        child = cls(home, name, resourceID, mode == _BIND_MODE_OWN)
        yield child.initFromStore()
        returnValue(child)

    
    @classproperty
    def _objectResourceNamesAndIDsQuery(cls): #@NoSelf
        """
        DAL query to load all object resource names for a home child.
        """
        obj = cls._objectSchema
        return Select([obj.RESOURCE_NAME, obj.RESOURCE_ID], From=obj,
                      Where=obj.PARENT_RESOURCE_ID == Parameter('resourceID'))

    @inlineCallbacks
    def allowedChildResourceIDs(self):
        """
            get the allowed resource ids for this group address book
            TODO:
                include subgroup member resource ids
                return read-only/read-write members indication
        """
        
        print("xxx GroupAddressBook.allowedChildResourceIDs() self=%s" % (self,))
        allowedChildResourceIDs = []
        for groupID in tuple(self._groupIDs):
            groupMemberIDRows = (yield self._memberIDsForGroupIDQuery.on(self._txn, groupID=groupID))
                
            print("xxx GroupAddressBook.allowedChildResourceIDs(): groupMemberIDRows=%s" % (groupMemberIDRows,))
            for groupMemberIDRow in groupMemberIDRows:
                allowedChildResourceIDs += groupMemberIDRow
            print("xxx GroupAddressBook.allowedChildResourceIDs(): allowedChildResourceIDs=%s" % (allowedChildResourceIDs,))
        returnValue(set(allowedChildResourceIDs))


    @inlineCallbacks
    def listObjectResources(self):
        """
            AddressBookObject.listObjectResources + filtering
        """
        
        print("xxx GroupAddressBook.allowedChildResourceIDs() self=%s" % (self,))
        print("xxx GroupAddressBook.listObjectResources() self=%s" % (self,))
        if self._objectNames is None:
        
            rows = yield self._objectResourceNamesAndIDsQuery.on(
                self._txn, resourceID=self._resourceID)
            print("xxx GroupAddressBook.listObjectResources(): rows=%s" % (rows,))
            
            allowedChildResourceIDs = (yield self.allowedChildResourceIDs()) if rows else []
            print("xxx GroupAddressBook.listObjectResources(): allowedChildResourceIDs=%s" % (allowedChildResourceIDs,))
            
            names = []
            for row in rows:
                print("xxx GroupAddressBook.listObjectResources(): row=%s" % (row,))
                if row[1] in allowedChildResourceIDs:
                    names += [row[0],]
                
            self._objectNames = sorted(names)
            #self._objectNames = sorted([row[0] for row in rows])
        print("xxx GroupAddressBook.listObjectResources(): self=%s returning=%s" % (self, self._objectNames,))
        returnValue(self._objectNames)


    @inlineCallbacks
    def resourceNameForUID(self, uid):
        print("xxx GroupAddressBook.resourceNameForUID() self=%s" % (self,))
        try:
            resource = self._objects[uid]
            returnValue(resource.name() if resource else None)
        except KeyError:
            pass
        rows = yield self._resourceNameForUIDQuery.on(
            self._txn, uid=uid, resourceID=self._resourceID)
        
        #FIXME: Filter

        if rows:
            returnValue(rows[0][0])
        else:
            self._objects[uid] = None
            returnValue(None)

    @inlineCallbacks
    def resourceUIDForName(self, name):
        print("xxx GroupAddressBook.resourceUIDForName() self=%s" % (self,))
        try:
            resource = self._objects[name]
            returnValue(resource.uid() if resource else None)
        except KeyError:
            pass
        rows = yield self._resourceUIDForNameQuery.on(
            self._txn, name=name, resourceID=self._resourceID)
        
        #FIXME: Filter
        if rows:
            returnValue(rows[0][0])
        else:
            self._objects[name] = None
            returnValue(None)

 
class AddressBookObject(CommonObjectResource):

    implements(IAddressBookObject)

    _objectTable = ADDRESSBOOK_OBJECT_TABLE
    _objectSchema = schema.ADDRESSBOOK_OBJECT

    def __init__(self, addressbook, name, uid, resourceID=None, metadata=None):

        super(AddressBookObject, self).__init__(addressbook, name, uid, resourceID)
        self._invites = SQLLegacySharedGroupInvites(self)


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

        yield self._addressbook.notifyChanged()


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

        # Special - if migrating we need to preserve the original md5    
        if self._txn._migrating and hasattr(component, "md5"):
            self._md5 = component.md5

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
        """
        Read address data and validate/fix it. Do not raise a store error here if there are unfixable
        errors as that could prevent the overall request to fail. Instead we will hand bad data off to
        the caller - that is not ideal but in theory we should have checked everything on the way in and
        only allowed in good data.
        """
        text = yield self._text()

        try:
            component = VCard.fromString(text)
        except InvalidVCardDataError, e:
            # This is a really bad situation, so do raise
            raise InternalDataStoreError(
                "Data corruption detected (%s) in id: %s"
                % (e, self._resourceID)
            )

        # Fix any bogus data we can
        fixed, unfixed = component.validVCardData(doFix=True, doRaise=False)

        if unfixed:
            self.log_error("Address data id=%s had unfixable problems:\n  %s" % (self._resourceID, "\n  ".join(unfixed),))
        
        if fixed:
            self.log_error("Address data id=%s had fixable problems:\n  %s" % (self._resourceID, "\n  ".join(fixed),))

        returnValue(component)


    # IDataStoreObject
    def contentType(self):
        """
        The content type of Addressbook objects is text/vcard.
        """
        return MimeType.fromString("text/vcard; charset=utf-8")


    @inlineCallbacks
    def isSharedGroup(self):
        """
        FIXME:
        How do we tell?
        A shared address book is marked with a property.  Should we do the same with a group when it is shared?
        
        Otherwise, we can find the sharees address book bind table and see if there are any binds to this group.
        But that requires the sharee context.
        """
        self.log_info("isSharedGroup(), self = %s, _resourceID=%s returing True by default" % (self, self._resourceID,))
        
        
        
        #need this for now, yuk!
        (yield None)
        returnValue(True)

    def retrieveOldInvites(self):
        return self._invites
        

class GroupAddressBookObject(AddressBookObject):
    """
    Override of AddressBookObject that filters children of GroupAddressBook
    """

    @classmethod
    @inlineCallbacks
    def loadAllObjects(cls, parent):
        print("xxx GroupAddressBookObject.loadAllObjects() cls=%s, parent=%s" % (cls, parent))
        """
        Load all child objects and return a list of them. This must create the
        child classes and initialize them using "batched" SQL operations to keep
        this constant wrt the number of children. This is an optimization for
        Depth:1 operations on the collection.
        """

        results = []

        # Load from the main table first
        dataRows = yield cls._allColumnsWithParent.on(
            parent._txn, parentID=parent._resourceID)
            

        #filter
        print("xxx GroupAddressBookObject.loadAllObjects(): dataRows=%s" % dataRows)
        if dataRows and hasattr(parent, "allowedChildResourceIDs"):

            allowedChildResourceIDs = (yield parent.allowedChildResourceIDs())
            print("xxx GroupAddressBookObject.loadAllObjects(): allowedChildResourceIDs=%s" % allowedChildResourceIDs)
            
            filteredDataRows = []
            for dataRow in dataRows:
                if dataRow[0] in allowedChildResourceIDs:
                    filteredDataRows += [dataRow,]
            
            dataRows = filteredDataRows
            print("xxx GroupAddressBookObject.loadAllObjects(): filteredDataRows=%s" % dataRows)
           
            

        if dataRows:
        
            # Get property stores for all these child resources (if any found)
            if parent.objectResourcesHaveProperties():
                propertyStores =(yield PropertyStore.forMultipleResources(
                    parent._home.uid(),
                    parent._txn,
                    cls._objectSchema.RESOURCE_ID,
                    cls._objectSchema.PARENT_RESOURCE_ID,
                    parent._resourceID
                ))
            else:
                propertyStores = {}

        # Create the actual objects merging in properties
        for row in dataRows:
            child = cls(parent, "", None)
            child._initFromRow(tuple(row))
            yield child._loadPropertyStore(
                props=propertyStores.get(child._resourceID, None)
            )
            results.append(child)

        returnValue(results)



