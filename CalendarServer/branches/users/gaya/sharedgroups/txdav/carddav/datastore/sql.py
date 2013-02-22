# -*- test-case-name: txdav.carddav.datastore.test.test_sql -*-
##
# Copyright (c) 2010-2013 Apple Inc. All rights reserved.
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

from twext.enterprise.dal.syntax import Delete, Insert, Len, Parameter, \
    Update, Union, Max, Select, utcNowSQL

from twext.python.clsprop import classproperty
from twext.web2.http import HTTPError
from twext.web2.http_headers import MimeType
from twext.web2.responsecode import FORBIDDEN

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import hashlib
from twistedcaldav import carddavxml, customxml
from twistedcaldav.memcacher import Memcacher
from twistedcaldav.vcard import Component as VCard, InvalidVCardDataError, \
    vCardProductID, Property

from txdav.base.propertystore.base import PropertyName
from txdav.base.propertystore.sql import PropertyStore
from txdav.carddav.datastore.util import validateAddressBookComponent
from txdav.carddav.iaddressbookstore import IAddressBookHome, IAddressBook, \
    IAddressBookObject, GroupForSharedAddressBookDeleteNotAllowedError, \
    GroupWithUnsharedAddressNotAllowedError, SharedGroupDeleteNotAllowedError
from txdav.common.datastore.sql import CommonHome, CommonHomeChild, \
    CommonObjectResource, EADDRESSBOOKTYPE, SharingMixIn
from txdav.common.datastore.sql_legacy import PostgresLegacyABIndexEmulator
from txdav.common.datastore.sql_tables import ADDRESSBOOK_TABLE, \
    ADDRESSBOOK_BIND_TABLE, ADDRESSBOOK_OBJECT_REVISIONS_TABLE, \
    ADDRESSBOOK_OBJECT_TABLE, ADDRESSBOOK_HOME_TABLE, \
    ADDRESSBOOK_HOME_METADATA_TABLE, ADDRESSBOOK_AND_ADDRESSBOOK_BIND, \
    ADDRESSBOOK_OBJECT_AND_BIND_TABLE, \
    ADDRESSBOOK_OBJECT_REVISIONS_AND_BIND_TABLE, \
    _ABO_KIND_PERSON, _ABO_KIND_GROUP, _ABO_KIND_RESOURCE, \
    _ABO_KIND_LOCATION, schema, \
    _BIND_MODE_OWN, _BIND_MODE_WRITE, _BIND_STATUS_ACCEPTED, \
    _BIND_STATUS_DECLINED, _BIND_STATUS_INVITED
from txdav.xml.rfc2518 import ResourceType

from zope.interface.declarations import implements


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
        aor = schema.ADDRESSBOOK_OBJECT_REVISIONS
        rp = schema.RESOURCE_PROPERTY

        yield Delete(
            From=ab,
            Where=ab.ADDRESSBOOK_HOME_RESOURCE_ID == self._resourceID,
        ).on(self._txn)

        yield Delete(
            From=aor,
            Where=aor.ADDRESSBOOK_HOME_RESOURCE_ID == self._resourceID,
        ).on(self._txn)

        yield Delete(
            From=ah,
            Where=ah.RESOURCE_ID == self._resourceID,
        ).on(self._txn)

        yield Delete(
            From=rp,
            Where=rp.RESOURCE_ID == self._resourceID,
        ).on(self._txn)

        yield self._cacher.delete(str(self._ownerUID))


    def createdHome(self):
        return self.createAddressBookWithName(self.addressbookName())


    @inlineCallbacks
    def removeUnacceptedShares(self):
        """
        Unbinds any collections that have been shared to this home but not yet
        accepted.  Associated invite entries are also removed.
        """
        super(AddressBookHome, self).removeUnacceptedShares()

        bind = AddressBookObject._bindSchema
        kwds = {"homeResourceID" : self._resourceID}
        yield Delete(
            From=bind,
            Where=(bind.HOME_RESOURCE_ID == Parameter("homeResourceID")
                   ).And(bind.BIND_STATUS != _BIND_STATUS_ACCEPTED)
        ).on(self._txn, **kwds)


    @classmethod
    def addressbookName(cls):
        return "addressbook"


    @inlineCallbacks
    def addressbook(self):
        returnValue((yield self.addressbookWithName(self.addressbookName())))


    def objectWithShareUID(self, shareUID):
        """
        Retrieve the child with the given bind identifier contained in this
        home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such child exists.
        """
        return self._childClass.objectWithBindName(self, shareUID, accepted=True)


    def invitedObjectWithShareUID(self, shareUID):
        """
        Retrieve the child invitation with the given bind identifier contained in this
        home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such child exists.
        """
        return self._childClass.objectWithBindName(self, shareUID, accepted=False)


AddressBookHome._register(EADDRESSBOOKTYPE)


class AddressBook(CommonHomeChild, SharingMixIn):
    """
    SQL-based implementation of L{IAddressBook}.
    """
    implements(IAddressBook)

    # structured tables.  (new, preferred)
    _homeSchema = schema.ADDRESSBOOK_HOME
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


    def __init__(self, home, name, resourceID, mode, status, message=None, ownerHome=None, bindName=None):
        super(AddressBook, self).__init__(home, name, resourceID, mode, status, message=message, ownerHome=ownerHome)
        self._index = PostgresLegacyABIndexEmulator(self)
        self._bindName = bindName

    @property
    def _addressbookHome(self):
        return self._home


    def resourceType(self):
        return ResourceType.addressbook #@UndefinedVariable


    #FIXME: Only used for shared group resouretype in SharedResourceMixin.upgradeToShare() and SharedResourceMixin.downgradeFromShare()
    def objectResourcesHaveProperties(self):
        return True


    ownerAddressBookHome = CommonHomeChild.ownerHome
    addressbookObjects = CommonHomeChild.objectResources
    listAddressbookObjects = CommonHomeChild.listObjectResources
    addressbookObjectWithName = CommonHomeChild.objectResourceWithName
    addressbookObjectWithUID = CommonHomeChild.objectResourceWithUID
    createAddressBookObjectWithName = CommonHomeChild.createObjectResourceWithName
    removeAddressBookObjectWithName = CommonHomeChild.removeObjectResourceWithName
    removeAddressBookObjectWithUID = CommonHomeChild.removeObjectResourceWithUID
    addressbookObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken


    def shareeABName(self):
        return self._home.uid()


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
    def create(cls, home, name):

        if name != home.addressbookName():
            raise HTTPError(FORBIDDEN)

        returnValue((yield super(AddressBook, cls).create(home, name)))


    @inlineCallbacks
    def listObjectResources(self):

        result = yield super(AddressBook, self).listObjectResources()
        if not self.owned() and not self.fullyShared():
            groupForSharedAB = yield self._groupForEntireAB_Name()
            if not groupForSharedAB in result:
                result.append(groupForSharedAB)
        returnValue(result)


    @inlineCallbacks
    def countObjectResources(self):
        if self._objectNames is None:
            rows = yield self._objectCountQuery.on(
                self._txn, resourceID=self._resourceID
            )
            count = rows[0][0]

            #add in group for shared address book
            if not self.owned() and not self.fullyShared():
                count += 1
            returnValue(count)
        returnValue(len(self._objectNames))


    @inlineCallbacks
    def ownerAddressBook(self):
        if not hasattr(self, "_ownerAddressBook"):
            if self.owned():
                yield None
                self._ownerAddressBook = self
            else:
                ownerHome = yield self.ownerAddressBookHome()
                assert self._home != ownerHome
                self._ownerAddressBook = yield ownerHome.addressbook()
                assert self._ownerAddressBook != self
        returnValue(self._ownerAddressBook)


    @classmethod
    def _abObjectColumnsWithAddressBookResourceID(cls, columns,): #@NoSelf
        """
        DAL statement to retrieve addressbook object rows with given columns.
        """
        obj = cls._objectSchema
        return Select(columns, From=obj,
                      Where=obj.ADDRESSBOOK_RESOURCE_ID == Parameter("addressbookResourceID"),)


    @inlineCallbacks
    def _groupForEntireAB_Row(self): #@NoSelf

        returnValue([
            self._resourceID, # obj.ADDRESSBOOK_RESOURCE_ID,
            self._resourceID, # obj.RESOURCE_ID,
            (yield self._groupForEntireAB_Name()), # obj.RESOURCE_NAME, shared name is UID and thus avoids collisions
            (yield self._groupForEntireAB_UID()), # obj.UID, shared name is uuid
            _ABO_KIND_GROUP, # obj.KIND,
            "1", # obj.MD5, unused
            "1", # Len(obj.TEXT), unused
            self._created, # obj.CREATED,
            self._modified, # obj.CREATED,
        ])


    @inlineCallbacks
    def _groupForEntireAB_Name(self):
        returnValue((yield self.ownerAddressBook()).name() + ".vcf")


    def _groupForEntireAB_UID(self):
        return self.name()


    @inlineCallbacks
    def _groupForEntireAB_Component(self):

        n = (yield self.ownerAddressBook()).shareeABName()
        fn = n
        uid = self.name()

        #  it would be nice to use the owner.displayName() full name here
        #      uid = (yield self.ownerAddressBook()).ownerHome().uid()
        #      owner = yield self.principalForUID(uid)
        #      n = owner.name()
        #      fn = owner.displayName()

        component = VCard.fromString(
            """BEGIN:VCARD
VERSION:3.0
PRODID:%s
UID:%s
FN:%s
N:%s;;;;
X-ADDRESSBOOKSERVER-KIND:group
END:VCARD
""".replace("\n", "\r\n") % (vCardProductID, uid, n, fn,)
        )

        # then get member UIDs
        abo = schema.ADDRESSBOOK_OBJECT
        memberUIDRows = yield self._abObjectColumnsWithAddressBookResourceID(
            [abo.VCARD_UID]
        ).on(self._txn, addressbookResourceID=self._resourceID)
        memberUIDs = [memberUIDRow[0] for memberUIDRow in memberUIDRows]

        # add prefix to get property string
        memberAddresses = ["urn:uuid:" + memberUID for memberUID in memberUIDs]

        # now add the properties to the component
        for memberAddress in sorted(memberAddresses):
            component.addProperty(Property("X-ADDRESSBOOKSERVER-MEMBER", memberAddress))

        returnValue(component)


    @classproperty
    def _metadataByIDQuery(cls): #@NoSelf
        """
        DAL query to retrieve created/modified dates based on a resource ID.
        """
        child = cls._homeChildMetaDataSchema
        abo = schema.ADDRESSBOOK_OBJECT
        return Select(
            cls.metadataColumns(),
            From=child,
            Where=child.RESOURCE_ID == Parameter("resourceID"),
            SetExpression=Union(
                Select(
                    [abo.CREATED, abo.MODIFIED],
                    From=abo,
                    Where=abo.RESOURCE_ID == Parameter("resourceID"),
                    ),
                optype=Union.OPTYPE_ALL,
            )
        )


    @classproperty
    def _changeABForSharedGroupLastModifiedQuery(cls): #@NoSelf
        schema = AddressBookObject._objectSchema
        return Update({schema.MODIFIED: utcNowSQL},
                      Where=schema.RESOURCE_ID == Parameter("resourceID"),
                      Return=schema.MODIFIED)


    @inlineCallbacks
    def _bumpModified(self, subtxn):

        yield self._lockLastModifiedQuery.on(
            subtxn, resourceID=self._resourceID
        )

        # can't call self.ownerGroup() on a subtranaction,
        # So just try AB schema first, then ABOBject schema 
        # The following line makes shared groups faster, but a bit of a hack
        #if hasattr(self, "_ownerGroup") and not (yield self.ownerGroup()):
        result = yield self._changeLastModifiedQuery.on(
            subtxn, resourceID=self._resourceID
        )

        if not result:
            result = yield self._changeABForSharedGroupLastModifiedQuery.on(
                subtxn, resourceID=self._resourceID
            )

        returnValue(result)


    @classmethod
    @inlineCallbacks
    def loadAllObjects(cls, home):
        """
        Load all L{CommonHomeChild} instances which are children of a given
        L{CommonHome} and return a L{Deferred} firing a list of them.  This must
        create the child classes and initialize them using "batched" SQL
        operations to keep this constant wrt the number of children.  This is an
        optimization for Depth:1 operations on the home.
        """
        results = []

        # Load from the main table first
        dataRows = yield cls._childrenAndMetadataForHomeID.on(
            home._txn, homeID=home._resourceID
        )

        # get ownerHomeIDs
        ownerHomeIDToDataRowMap = {}
        for dataRow in dataRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = dataRow[:6] #@UnusedVariable
            if bindStatus == _BIND_MODE_OWN:
                ownerHomeIDToDataRowMap[home._resourceID] = dataRow
            else:
                ownerHomeID = yield cls.ownerHomeID(home._txn, resourceID)
                ownerHomeIDToDataRowMap[ownerHomeID] = dataRow


        # now get group rows:
        groupBindRows = yield AddressBookObject._childrenAndMetadataForHomeID.on(
            home._txn, homeID=home._resourceID
        )
        for groupBindRow in groupBindRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = groupBindRow[:6] #@UnusedVariable        
            ownerAddressBookID = yield AddressBookObject.ownerAddressBookID(home._txn, resourceID)
            ownerHomeID = yield cls.ownerHomeID(home._txn, ownerAddressBookID)
            if ownerHomeID not in ownerHomeIDToDataRowMap:
                groupBindRow[0] = _BIND_MODE_WRITE
                groupBindRow[3] = None # bindName
                groupBindRow[4] = None # bindStatus
                groupBindRow[5] = None # bindMessage
                ownerHomeIDToDataRowMap[ownerHomeID] = groupBindRow

        if ownerHomeIDToDataRowMap:

            # Get property stores for all these child resources (if any found)
            propertyStores = (yield PropertyStore.forMultipleResources(
                home.uid(), home._txn,
                cls._bindSchema.RESOURCE_ID, cls._bindSchema.HOME_RESOURCE_ID,
                home._resourceID
            ))

            revisions = yield cls._revisionsForHomeID.on(home._txn, homeID=home._resourceID)
            revisions = dict(revisions)

            # Create the actual objects merging in properties
            for ownerHomeID, dataRow in ownerHomeIDToDataRowMap.iteritems():
                bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = dataRow[:6] #@UnusedVariable
                metadata = dataRow[6:]

                if bindStatus == _BIND_MODE_OWN:
                    child = cls(
                        home=home,
                        name=bindName, resourceID=resourceID,
                        mode=bindMode, status=bindStatus,
                        message=bindMessage,
                    )
                else:
                    ownerHome = yield home._txn.homeWithResourceID(home._homeType, ownerHomeID)
                    ownerAddressBook = yield ownerHome.addressbook()

                    child = cls(
                        home=home,
                        name=ownerAddressBook.shareeABName(), resourceID=ownerAddressBook._resourceID,
                        mode=bindMode, status=bindStatus,
                        message=bindMessage, ownerHome=ownerHome,
                        bindName=bindName
                    )

                for attr, value in zip(cls.metadataAttributes(), metadata):
                    setattr(child, attr, value)
                child._syncTokenRevision = revisions[resourceID]
                propstore = propertyStores.get(resourceID, None)

                # We have to re-adjust the property store object to account for possible shared
                # collections as previously we loaded them all as if they were owned
                if bindStatus != _BIND_MODE_OWN:
                    propstore._setDefaultUserUID(ownerHome.uid())
                yield child._loadPropertyStore(propstore)
                results.append(child)

        returnValue(results)


    @classmethod
    @inlineCallbacks
    def objectWithName(cls, home, name, accepted=True):
        # replaces objectWithName()
        """
        Retrieve the child with the given C{name} contained in the given
        C{home}.

        @param home: a L{CommonHome}.

        @param name: a string; the name of the L{CommonHomeChild} to retrieve.

        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        if name == home.addressbookName():
            returnValue((yield super(AddressBook, cls).objectWithName(home, name)))

        #all shared address books now

        rows = None
        queryCacher = home._txn._queryCacher
        ownerHome = None

        if queryCacher:
            # Retrieve data from cache
            cacheKey = queryCacher.keyForObjectWithName(home._resourceID, name)
            rows = yield queryCacher.get(cacheKey)

        if rows is None:

            # name must be a home uid
            ownerHome = yield home._txn.addressbookHomeWithUID(name)
            if ownerHome:
                # see if address book resource id in bind table
                ownerAddressBook = yield ownerHome.addressbook()

                sharedABBindRows = yield cls._bindForResourceIDAndHomeID.on(
                    home._txn, resourceID=ownerAddressBook._resourceID, homeID=home._resourceID
                )
                if sharedABBindRows:
                    bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = sharedABBindRows[0] #@UnusedVariable
                    sharedABBindRows[0].append(ownerHome._resourceID)
                    rows = [sharedABBindRows[0]]

                if not rows:
                    groupBindRows = yield AddressBookObject._bindWithHomeIDAndAddressBookID.on(
                            home._txn, homeID=home._resourceID, addressbookID=ownerAddressBook._resourceID
                    )
                    if groupBindRows:
                        groupBindRow = groupBindRows[0]
                        groupBindRow[0] = _BIND_MODE_WRITE
                        groupBindRow[3] = None # bindName
                        groupBindRow[4] = None # bindStatus
                        groupBindRow[5] = None # bindMessage
                        groupBindRow.append(ownerHome._resourceID)
                        rows = [groupBindRow]

            if rows and queryCacher:
                # Cache the result
                queryCacher.setAfterCommit(home._txn, cacheKey, rows)

        if not rows:
            returnValue(None)

        bindMode, homeID, resourceID, bindName, bindStatus, bindMessage, ownerHomeID = rows[0] #@UnusedVariable
        ownerHome = yield home._txn.homeWithResourceID(home._homeType, ownerHomeID)
        ownerAddressBook = yield ownerHome.addressbook()
        child = cls(
                home=home,
                name=ownerAddressBook.shareeABName(), resourceID=ownerAddressBook._resourceID,
                mode=bindMode, status=bindStatus,
                message=bindMessage, ownerHome=ownerHome,
                bindName=bindName,
            )
        yield child.initFromStore()
        returnValue(child)


    @classmethod
    @inlineCallbacks
    def objectWithBindName(cls, home, name, accepted):
        """
        Retrieve the child or objectResource with the given bind name C{name} contained in the given
        C{home}.

        @param home: a L{CommonHome}.

        @param name: a string; the name of the L{CommonHomeChild} to retrieve.

        @return: an L{CommonHomeChild} or L{ObjectResource} or C{None} if no such child
            exists.
        """
        bindRows = yield cls._bindForNameAndHomeID.on(home._txn, name=name, homeID=home._resourceID)
        if bindRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = bindRows[0] #@UnusedVariable
            if (bindStatus == _BIND_STATUS_ACCEPTED) == bool(accepted):
                # alt:
                # returnValue((yield cls.objectWithID(home, resourceID)))
                ownerHomeID = yield cls.ownerHomeID(home._txn, resourceID)
                ownerHome = yield home._txn.homeWithResourceID(home._homeType, ownerHomeID)
                ownerAddressBook = yield ownerHome.addressbook()
                if accepted:
                    returnValue((yield home.childWithName(ownerAddressBook.shareeABName())))
                else:
                    returnValue((yield cls.objectWithName(home, ownerAddressBook.shareeABName(), accepted=False)))

        groupBindRows = yield AddressBookObject._bindForNameAndHomeID.on(
            home._txn, name=name, homeID=home._resourceID
        )
        if groupBindRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = groupBindRows[0] #@UnusedVariable
            if (bindStatus == _BIND_STATUS_ACCEPTED) == bool(accepted):
                ownerAddressBookID = yield AddressBookObject.ownerAddressBookID(home._txn, resourceID)
                # alt:
                # addressbook = yield cls.objectWithID(home, ownerAddressBookID)
                ownerHomeID = yield cls.ownerHomeID(home._txn, ownerAddressBookID)
                ownerHome = yield home._txn.homeWithResourceID(home._homeType, ownerHomeID)
                ownerAddressBook = yield ownerHome.addressbook()
                if accepted:
                    addressbook = yield home.childWithName(ownerAddressBook.shareeABName())
                    returnValue((yield addressbook.objectResourceWithID(resourceID)))
                else:
                    addressbook = yield cls.objectWithName(home, ownerAddressBook.shareeABName(), accepted=False)
                    returnValue((yield AddressBookObject.objectWithID(addressbook, resourceID))) # avoids object cache

        returnValue(None)


    @classmethod
    @inlineCallbacks
    def objectWithID(cls, home, resourceID):
        """
        Retrieve the child with the given C{resourceID} contained in the given
        C{home}.

        @param home: a L{CommonHome}.
        @param resourceID: a string.
        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        bindRows = yield cls._bindForResourceIDAndHomeID.on(
            home._txn, resourceID=resourceID, homeID=home._resourceID
        )
        if bindRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = bindRows[0] #@UnusedVariable
            ownerHomeID = yield cls.ownerHomeID(home._txn, resourceID)
            ownerHome = yield home._txn.homeWithResourceID(home._homeType, ownerHomeID)
            ownerAddressBook = yield ownerHome.addressbook()
            if bindStatus == _BIND_STATUS_ACCEPTED:
                returnValue((yield home.childWithName(ownerAddressBook.shareeABName())))
            else:
                returnValue((yield cls.objectWithName(home, ownerAddressBook.shareeABName(), accepted=False)))

        groupBindRows = yield AddressBookObject._bindWithHomeIDAndAddressBookID.on(
                    home._txn, homeID=home._resourceID, addressbookID=resourceID
        )
        if groupBindRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = groupBindRows[0] #@UnusedVariable
            ownerAddressBookID = yield AddressBookObject.ownerAddressBookID(home._txn, resourceID)
            ownerHomeID = yield cls.ownerHomeID(home._txn, ownerAddressBookID)
            ownerHome = yield home._txn.homeWithResourceID(home._homeType, ownerHomeID)
            ownerAddressBook = yield ownerHome.addressbook()
            if bindStatus == _BIND_STATUS_ACCEPTED:
                returnValue((yield home.childWithName(ownerAddressBook.shareeABName())))
            else:
                returnValue((yield cls.objectWithName(home, ownerAddressBook.shareeABName(), accepted=False)))

        returnValue(None)



    @classproperty
    def _revisionsForHomeID(cls): #@NoSelf

        def columns(rev):
            return [rev.RESOURCE_ID, Max(rev.REVISION)]

        def _from(rev, bind):
            return rev.join(bind, rev.RESOURCE_ID == bind.RESOURCE_ID, 'left')

        def where(rev, bind):
            return ((bind.HOME_RESOURCE_ID == Parameter("homeID")).
                And((rev.RESOURCE_NAME != None).Or(rev.DELETED == False)))

        addressbookBind = cls._bindSchema
        aboBind = AddressBookObject._bindSchema
        rev = cls._revisionsSchema
        return Select(
            columns(rev),
            From=_from(rev, addressbookBind),
            Where=where(rev, addressbookBind),
            GroupBy=rev.RESOURCE_ID,
            SetExpression=Union(
                Select(
                    columns(rev),
                    From=_from(rev, aboBind),
                    Where=where(rev, aboBind),
                    GroupBy=rev.RESOURCE_ID
                    ),
                optype=Union.OPTYPE_ALL,
           ),
        )


    def shareUID(self):
        """
        @see: L{ICalendar.shareUID}
        """
        return self._bindName


    def fullyShared(self):
        return not self.owned() and self._bindStatus == _BIND_STATUS_ACCEPTED


    @classmethod
    @inlineCallbacks
    def listObjects(cls, home):
        """
        Retrieve the names of the children with invitations in the given home.

        @return: an iterable of C{str}s.
        """
        names = set()
        rows = yield cls._acceptedBindForHomeID.on(
            home._txn, homeID=home._resourceID
        )
        rows.extend((yield AddressBookObject._acceptedBindForHomeID.on(
            home._txn, homeID=home._resourceID
        )))
        for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in rows: #@UnusedVariable
            if bindMode == _BIND_MODE_OWN:
                addressbook = yield home.addressbook()
                names |= set([addressbook.name()])
            else:
                ownerHome = yield home._txn.homeWithResourceID(home._homeType, homeID)
                ownerAddressBook = yield ownerHome.addressbook()
                names |= set([ownerAddressBook.shareeABName()])
        returnValue(tuple(names))


    @classmethod
    def _memberIDsWithGroupIDsQuery(cls, groupIDs): #@NoSelf
        """
        DAL query to load all object resource names for a home child.
        """
        aboMembers = schema.ABO_MEMBERS
        return Select([aboMembers.MEMBER_ID], From=aboMembers,
                      Where=aboMembers.GROUP_ID.In(Parameter("groupIDs", len(groupIDs))),
                      )


    @classmethod
    @inlineCallbacks
    def _objectIDsInExpandedGroupIDs(cls, txn, groupIDs, includeGroupIDs=True):
        """
        Get all AddressBookObject resource IDs contains in the given shared groups with the given groupIDs
        """
        objectIDs = set(groupIDs) if includeGroupIDs else set()
        examinedIDs = set()
        remainingIDs = set(groupIDs)
        while remainingIDs:
            memberRows = yield cls._memberIDsWithGroupIDsQuery(remainingIDs).on(
                txn, groupIDs=remainingIDs
            )
            objectIDs |= set(memberRow[0] for memberRow in memberRows)
            examinedIDs |= remainingIDs
            remainingIDs = objectIDs - examinedIDs

        returnValue(tuple(objectIDs))


    @inlineCallbacks
    def invitedGroupIDs(self):
        if self.owned():
            returnValue([])
        else:
            groupBindRows = yield AddressBookObject._invitedBindWithHomeIDAndAddressBookID.on(
                    self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
            )
            #for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in groupBindRows: #@UnusedVariable
            returnValue([groupBindRow[2] for groupBindRow in groupBindRows])


    @inlineCallbacks
    def acceptedGroupIDs(self):
        if self.owned():
            returnValue([])
        else:
            groupBindRows = yield AddressBookObject._acceptedBindWithHomeIDAndAddressBookID.on(
                    self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
            )
            #for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in groupBindRows: #@UnusedVariable
        returnValue([groupBindRow[2] for groupBindRow in groupBindRows])


    @inlineCallbacks
    def writeableAcceptedGroupIDs(self):
        if self.owned():
            returnValue([])
        else:
            groupBindRows = yield AddressBookObject._acceptedBindWithHomeIDAndAddressBookID.on(
                    self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
            )
            readwriteGroupIDs = []
            readonlyGroupIDs = []
            for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in groupBindRows: #@UnusedVariable
                if bindMode == _BIND_MODE_WRITE:
                    readwriteGroupIDs.append(resourceID)
                else:
                    readonlyGroupIDs.append(resourceID)

            if readonlyGroupIDs and readwriteGroupIDs:
                # expand read-write groups and remove any subgroups from read-only group list
                allWriteableIDs = yield self._objectIDsInExpandedGroupIDs(self._txn, readwriteGroupIDs)
                adjustedReadOnlyGroupIDs = set(readonlyGroupIDs) - set(allWriteableIDs)
                adjustedReadWriteGroupIDs = set(readwriteGroupIDs) | (set(readonlyGroupIDs) - adjustedReadOnlyGroupIDs)
            else:
                #adjustedReadOnlyGroupIDs = readonlyGroupIDs
                adjustedReadWriteGroupIDs = readwriteGroupIDs
            returnValue(tuple(adjustedReadWriteGroupIDs))


    @inlineCallbacks
    def updateShare(self, shareeView, mode=None, status=None, message=None, name=None):
        """
        Update share mode, status, and message for a home child shared with
        this (owned) L{CommonHomeChild}.

        @param shareeView: The sharee home child that shares this.
        @type shareeView: L{CommonHomeChild}

        @param mode: The sharing mode; L{_BIND_MODE_READ} or
            L{_BIND_MODE_WRITE} or None to not update
        @type mode: L{str}

        @param status: The sharing status; L{_BIND_STATUS_INVITED} or
            L{_BIND_STATUS_ACCEPTED} or L{_BIND_STATUS_DECLINED} or
            L{_BIND_STATUS_INVALID}  or None to not update
        @type status: L{str}

        @param message: The proposed message to go along with the share, which
            will be used as the default display name, or None to not update
        @type message: L{str}

        @param name: The bind resource name or None to not update
        @type message: L{str}

        @return: the name of the shared item in the sharee's home.
        @rtype: a L{Deferred} which fires with a L{str}
        """
        # TODO: raise a nice exception if shareeView is not, in fact, a shared
        # version of this same L{CommonHomeChild}

        #remove None parameters, and substitute None for empty string
        bind = self._bindSchema
        columnMap = dict([(k, v if v else None)
                          for k, v in {bind.BIND_MODE:mode,
                            bind.BIND_STATUS:status,
                            bind.MESSAGE:message,
                            bind.RESOURCE_NAME:name}.iteritems() if v is not None])

        if len(columnMap):

            # count accepted 
            if status is not None:
                previouslyAcceptedBindCount = 1 if shareeView.fullyShared() else 0
                previouslyAcceptedBindCount += len((yield AddressBookObject._acceptedBindWithHomeIDAndAddressBookID.on(
                        self._txn, homeID=shareeView._home._resourceID, addressbookID=shareeView._resourceID
                )))

            #TODO:  with bit of parameter wrangling, call shareWith() here instead.
            sharedname = yield self._updateBindColumnsQuery(columnMap).on(
                self._txn,
                resourceID=self._resourceID, homeID=shareeView._home._resourceID
            )

            #update affected attributes
            if mode is not None:
                shareeView._bindMode = columnMap[bind.BIND_MODE]

            if status is not None:
                shareeView._bindStatus = columnMap[bind.BIND_STATUS]
                if shareeView._bindStatus == _BIND_STATUS_ACCEPTED:
                    if 0 == previouslyAcceptedBindCount:
                        yield shareeView._initSyncToken()
                elif shareeView._bindStatus == _BIND_STATUS_DECLINED:
                    if 1 == previouslyAcceptedBindCount:
                        shareeView._deletedSyncToken(sharedRemoval=True)
                        shareeView._home._children.pop(shareeView._name, None)


            if message is not None:
                shareeView._bindMessage = columnMap[bind.MESSAGE]

            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeView._home._resourceID, shareeView._name)
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)

            shareeView._name = sharedname[0][0]

            # Must send notification to ensure cache invalidation occurs
            yield self.notifyChanged()

        returnValue(shareeView._name)

    @inlineCallbacks
    def asShared(self):
        """
        Retrieve all the versions of this L{CommonHomeChild} as it is shared to
        everyone.

        @see: L{ICalendarHome.asShared}

        @return: L{CommonHomeChild} objects that represent this
            L{CommonHomeChild} as a child of different L{CommonHome}s
        @rtype: a L{Deferred} which fires with a L{list} of L{ICalendar}s.
        """
        if not self.owned():
            returnValue([])

        # get all accepted shared binds
        bindRows = yield self._sharedBindForResourceID.on(
            self._txn, resourceID=self._resourceID, homeID=self._home._resourceID
        )

        result = []
        cls = self._home._childClass # for ease of grepping...
        for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in bindRows: #@UnusedVariable

            home = yield self._txn.homeWithResourceID(self._home._homeType, homeID)
            new = cls(
                home=home,
                name=self.shareeABName(), resourceID=self._resourceID,
                mode=bindMode, status=bindStatus,
                message=bindMessage, ownerHome=self._home,
                bindName=bindName
            )
            yield new.initFromStore()
            result.append(new)

        returnValue(result)


    @inlineCallbacks
    def asInvited(self):
        """
        Retrieve all the versions of this L{CommonHomeChild} as it is invited to
        everyone.

        @see: L{ICalendarHome.asInvited}

        @return: L{CommonHomeChild} objects that represent this
            L{CommonHomeChild} as a child of different L{CommonHome}s
        @rtype: a L{Deferred} which fires with a L{list} of L{ICalendar}s.
        """
        if not self.owned():
            returnValue([])

        # get all accepted shared binds
        bindRows = yield self._unacceptedBindForResourceID.on(
            self._txn, resourceID=self._resourceID
        )

        result = []
        cls = self._home._childClass # for ease of grepping...
        for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in bindRows: #@UnusedVariable

            home = yield self._txn.homeWithResourceID(self._home._homeType, homeID)
            new = cls(
                home=home,
                name=self.shareeABName(), resourceID=self._resourceID,
                mode=bindMode, status=bindStatus,
                message=bindMessage, ownerHome=self._home,
                bindName=bindName
            )
            yield new.initFromStore()
            result.append(new)

        returnValue(result)


    @inlineCallbacks
    def unshareWith(self, shareeHome):
        """
        Remove the shared version of this (owned) L{CommonHomeChild} from the
        referenced L{CommonHome}.

        @see: L{CommonHomeChild.shareWith}

        @param shareeHome: The home with which this L{CommonHomeChild} was
            previously shared.

        @return: a L{Deferred} which will fire with the previous shareUID
        """
        sharedAddressBook = yield shareeHome.addressbookWithName(self.shareeABName())
        if sharedAddressBook:

            acceptedBindCount = 1 if sharedAddressBook.fullyShared() else 0
            acceptedBindCount += len((yield AddressBookObject._acceptedBindWithHomeIDAndAddressBookID.on(
                    self._txn, homeID=shareeHome._resourceID, addressbookID=sharedAddressBook._resourceID
            )))
            if acceptedBindCount == 1:
                sharedAddressBook._deletedSyncToken(sharedRemoval=True)
                shareeHome._children.pop(self.shareeABName(), None)
            elif not sharedAddressBook.fullyShared():
                #TODO: Just remove objects for this group only
                self._objectNames = None

            # Must send notification to ensure cache invalidation occurs
            yield self.notifyChanged()

        # delete binds including invites
        deletedBindNameRows = yield self._deleteBindWithResourceIDAndHomeID.on(self._txn, resourceID=self._resourceID,
             homeID=shareeHome._resourceID
        )
        if deletedBindNameRows:
            deletedBindName = deletedBindNameRows[0][0]
            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeHome._resourceID, self.shareeABName())
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)
        else:
            deletedBindName = None

        returnValue(deletedBindName)


class AddressBookObject(CommonObjectResource, SharingMixIn):

    implements(IAddressBookObject)

    _objectTable = ADDRESSBOOK_OBJECT_TABLE
    _objectSchema = schema.ADDRESSBOOK_OBJECT
    _bindSchema = schema.GROUP_ADDRESSBOOK_BIND


    def __init__(self, addressbook, name, uid, resourceID=None, metadata=None):

        self._kind = None
        self._ownerAddressBookResourceID = None
        # _self._component is the cached, current component
        # super._objectText now contains the text as read of the database only,
        #     not including group member text
        self._component = None
        self._bindMode = None
        self._bindStatus = None
        self._bindMessage = None
        self._bindName = None
        super(AddressBookObject, self).__init__(addressbook, name, uid, resourceID)


    @property
    def _addressbook(self):
        return self._parentCollection


    def addressbook(self):
        return self._addressbook


    def kind(self):
        return self._kind


    @classmethod
    def _deleteMembersWithMemberIDAndGroupIDsQuery(cls, memberID, groupIDs): #@NoSelf
        aboMembers = schema.ABO_MEMBERS
        return Delete(
            aboMembers,
            Where=(aboMembers.MEMBER_ID == memberID).And(
                    aboMembers.GROUP_ID.In(Parameter("groupIDs", len(groupIDs)))))


    @inlineCallbacks
    def remove(self):

        if self.owned():
            if self._kind == _ABO_KIND_GROUP: # optimization
                self.unshare()
        else:
            # Can't delete a share here with notification so raise.
            if self._resourceID == self._addressbook._resourceID:
                raise GroupForSharedAddressBookDeleteNotAllowedError
            elif self.shareUID():
                raise SharedGroupDeleteNotAllowedError

        aboMembers = schema.ABO_MEMBERS
        aboForeignMembers = schema.ABO_FOREIGN_MEMBERS

        if not self.owned() and not self._addressbook.fullyShared():
            # convert delete in sharee shared group address book to remove of memberships
            # that make this object visible to the sharee

            writeableAcceptedGroupIDs = yield self._addressbook.writeableAcceptedGroupIDs()
            if writeableAcceptedGroupIDs:
                objectsIDs = yield self._addressbook._objectIDsInExpandedGroupIDs(self._txn, writeableAcceptedGroupIDs)
                yield self._deleteMembersWithMemberIDAndGroupIDsQuery(self._resourceID, objectsIDs).on(
                    self._txn, groupIDs=objectsIDs
                )

            ownerAddressBook = yield self._addressbook.ownerAddressBook()
            yield self._changeAddressBookRevision(ownerAddressBook)

        else:
            # delete members table rows for this object,...
            groupIDRows = yield Delete(
                aboMembers,
                Where=aboMembers.MEMBER_ID == self._resourceID,
                Return=aboMembers.GROUP_ID
            ).on(self._txn)

            # add to foreign member table row by UID
            memberAddress = "urn:uuid:" + self._uid
            for groupID in [groupIDRow[0] for groupIDRow in groupIDRows]:
                if groupID != self._ownerAddressBookResourceID: # no aboForeignMembers on address books
                    yield Insert(
                        {aboForeignMembers.GROUP_ID: groupID,
                         aboForeignMembers.ADDRESSBOOK_ID: self._ownerAddressBookResourceID,
                         aboForeignMembers.MEMBER_ADDRESS: memberAddress, }
                    ).on(self._txn)

            yield super(AddressBookObject, self).remove()
            self._kind = None
            self._ownerAddressBookResourceID = None
            self._component = None


    @classmethod
    def _allColumnsWithResourceIDsAnd(cls, resourceIDs, column, paramName):
        """
        DAL query for all columns where PARENT_RESOURCE_ID matches a parentID
        parameter and a given instance column matches a given parameter name.
        """
        obj = cls._objectSchema
        return Select(
            cls._allColumns, From=obj,
            Where=(column == Parameter(paramName)).And(
                obj.RESOURCE_ID.In(Parameter("resourceIDs", len(resourceIDs)))),
        )


    @classmethod
    def _allColumnsWithResourceIDsAndName(cls, resourceIDs): #@NoSelf
        return cls._allColumnsWithResourceIDsAnd(resourceIDs, cls._objectSchema.RESOURCE_NAME, "name")


    @classmethod
    def _allColumnsWithResourceIDsAndUID(cls, resourceIDs): #@NoSelf
        return cls._allColumnsWithResourceIDsAnd(resourceIDs, cls._objectSchema.UID, "uid")


    @classproperty
    def _allColumnsWithResourceID(cls): #@NoSelf
        obj = cls._objectSchema
        return Select(
            cls._allColumns, From=obj,
            Where=obj.RESOURCE_ID == Parameter("resourceID"),)


    @inlineCallbacks
    def initFromStore(self):
        """
        Initialise this object from the store. We read in and cache all the
        extra metadata from the DB to avoid having to do DB queries for those
        individually later. Either the name or uid is present, so we have to
        tweak the query accordingly.

        @return: L{self} if object exists in the DB, else C{None}
        """
        rows = None
        if self.owned() or self._addressbook.fullyShared(): # owned or fully shared
            if self._name:
                rows = yield self._allColumnsWithParentAndName.on(
                    self._txn, name=self._name,
                    parentID=self._parentCollection._resourceID
                )
            elif self._uid:
                rows = yield self._allColumnsWithParentAndUID.on(
                    self._txn, uid=self._uid,
                    parentID=self._parentCollection._resourceID
                )
            elif self._resourceID:
                rows = yield self._allColumnsWithParentAndID.on(
                    self._txn, resourceID=self._resourceID,
                    parentID=self._parentCollection._resourceID
                )

            if not rows and self._addressbook.fullyShared(): # perhaps add special group
                if self._name:
                    if self._name == (yield self._addressbook._groupForEntireAB_Name()):
                        rows = [(yield self._addressbook._groupForEntireAB_Row())]
                elif self._uid:
                    if self._uid == (yield self._addressbook._groupForEntireAB_UID()):
                        rows = [(yield self._addressbook._groupForEntireAB_Row())]
                elif self._resourceID:
                    if self._resourceID == self._addressbook._resourceID:
                        rows = [(yield self._addressbook._groupForEntireAB_Row())]
        else:
            acceptedGroupIDs = yield self._addressbook.acceptedGroupIDs()
            allowedObjectIDs = yield self._addressbook._objectIDsInExpandedGroupIDs(self._txn, acceptedGroupIDs)
            if self._name:
                rows = (yield self._allColumnsWithResourceIDsAndName(allowedObjectIDs).on(
                    self._txn, name=self._name,
                    resourceIDs=allowedObjectIDs,
                )) if allowedObjectIDs else []
            elif self._uid:
                rows = (yield self._allColumnsWithResourceIDsAndUID(allowedObjectIDs).on(
                    self._txn, uid=self._uid,
                    resourceIDs=allowedObjectIDs,
                )) if allowedObjectIDs else []
            elif self._resourceID:
                if self._resourceID not in allowedObjectIDs:
                    allowedObjectIDs = yield self._addressbook.invitedGroupIDs()
                rows = (yield self._allColumnsWithResourceID.on(
                    self._txn, resourceID=self._resourceID,
                )) if (self._resourceID in allowedObjectIDs) else []

        if rows:
            self._initFromRow(tuple(rows[0]))

            if self._kind == _ABO_KIND_GROUP:
                # generate "X-ADDRESSBOOKSERVER-MEMBER" properties
                # calc md5 and set size
                componentText = str((yield self.component()))
                self._md5 = hashlib.md5(componentText).hexdigest()
                self._size = len(componentText)

                groupBindRows = yield AddressBookObject._bindForResourceIDAndHomeID.on(
                    self._txn, resourceID=self._resourceID, homeID=self._home._resourceID
                )

                if groupBindRows:
                    bindMode, homeID, resourceID, bindName, bindStatus, bindMessage = groupBindRows[0] #@UnusedVariable\
                    self._bindMode = bindMode
                    self._bindStatus = bindStatus
                    self._bindMessage = bindMessage
                    self._bindName = bindName

            yield self._loadPropertyStore()

            returnValue(self)
        else:
            returnValue(None)


    @inlineCallbacks
    def _objectIDsInExpandedGroup(self):
        """
        Get all AddressBookObject resource IDs contained in this shared group
        """
        returnValue((yield self._addressbook._objectIDsInExpandedGroupIDs(self._txn, [self._resourceID])))


    @classproperty
    def _allColumns(cls): #@NoSelf
        """
        Full set of columns in the object table that need to be loaded to
        initialize the object resource state.
        """
        obj = cls._objectSchema
        return [
            obj.ADDRESSBOOK_RESOURCE_ID,
            obj.RESOURCE_ID,
            obj.RESOURCE_NAME,
            obj.UID,
            obj.KIND,
            obj.MD5,
            Len(obj.TEXT),
            obj.CREATED,
            obj.MODIFIED,
        ]


    def _initFromRow(self, row):
        """
        Given a select result using the columns from L{_allColumns}, initialize
        the object resource state.
        """
        (self._ownerAddressBookResourceID,
         self._resourceID,
         self._name,
         self._uid,
         self._kind,
         self._md5,
         self._size,
         self._created,
         self._modified,) = tuple(row)


    @classmethod
    def _columnsWithResourceIDsQuery(cls, columns, resourceIDs): #@NoSelf
        """
        DAL statement to retrieve addressbook object rows with given columns.
        """
        obj = cls._objectSchema
        return Select(columns, From=obj,
                      Where=obj.RESOURCE_ID.In(Parameter("resourceIDs", len(resourceIDs))),)


    @classmethod
    @inlineCallbacks
    def _allColumnsWithParent(cls, addressbook): #@NoSelf
        if addressbook.owned() or addressbook.fullyShared():
            rows = yield super(AddressBookObject, cls)._allColumnsWithParent(addressbook)
            if addressbook.fullyShared():
                rows.append((yield addressbook._groupForEntireAB_Row()))
        else:
            acceptedGroupIDs = addressbook.acceptedGroupIDs()
            allowedObjectIDs = yield addressbook._objectIDsInExpandedGroupIDs(addressbook._txn, acceptedGroupIDs)
            rows = yield cls._columnsWithResourceIDsQuery(cls._allColumns, allowedObjectIDs).on(
                addressbook._txn, resourceIDs=allowedObjectIDs
            )
        returnValue(rows)


    @classmethod
    def _allColumnsWithResourceIDsAndNamesQuery(cls, resourceIDs, names): #@NoSelf
        obj = cls._objectSchema
        return Select(cls._allColumns, From=obj,
                      Where=(obj.RESOURCE_ID.In(Parameter("resourceIDs", len(resourceIDs))).And(
                          obj.RESOURCE_NAME.In(Parameter("names", len(names))))),)


    @classmethod
    @inlineCallbacks
    def _allColumnsWithParentAndNames(cls, addressbook, names): #@NoSelf

        if addressbook.owned() or addressbook.fullyShared():
            rows = yield super(AddressBookObject, cls)._allColumnsWithParentAndNames(addressbook, names)
            if addressbook.fullyShared() and (yield addressbook._groupForEntireAB_Name()) in names:
                rows.append((yield addressbook._groupForEntireAB_Row()))
        else:
            acceptedGroupIDs = addressbook.acceptedGroupIDs()
            allowedObjectIDs = yield addressbook._objectIDsInExpandedGroupIDs(addressbook._txn, acceptedGroupIDs)
            rows = yield cls._allColumnsWithResourceIDsAndNamesQuery(allowedObjectIDs, names).on(
                addressbook._txn, resourceIDs=allowedObjectIDs, names=names
            )
        returnValue(rows)


    @inlineCallbacks
    def _changeAddressBookRevision(self, addressbook, inserting=False):
        if inserting:
            yield addressbook._insertRevision(self._name)
        else:
            yield addressbook._updateRevision(self._name)

        yield addressbook.notifyChanged()


    @inlineCallbacks
    def setComponent(self, component, inserting=False):

        validateAddressBookComponent(self, self._addressbook, component, inserting)
        yield self.updateDatabase(component, inserting=inserting)
        yield self._changeAddressBookRevision(self._addressbook, inserting)

        if self.owned():
            # update revision table of the sharee group address book
            if self._kind == _ABO_KIND_GROUP:  # optimization
                for shareeAddressBook in (yield self.asShared()):
                    yield self._changeAddressBookRevision(shareeAddressBook, inserting)
                    # one is enough because all have the same resourceID
                    break
        else:
            if self._addressbook._resourceID != self._ownerAddressBookResourceID:
                # update revisions table of shared group's containing address book
                ownerAddressBook = yield self._addressbook.ownerAddressBook()
                yield self._changeAddressBookRevision(ownerAddressBook, inserting)

        self._component = component


    @classmethod
    def _resourceIDAndUIDForUIDsAndAddressBookResourceIDQuery(cls, uids): #@NoSelf
        abo = schema.ADDRESSBOOK_OBJECT
        return Select([abo.RESOURCE_ID, abo.VCARD_UID],
                      From=abo,
                      Where=((abo.ADDRESSBOOK_RESOURCE_ID == Parameter("addressbookResourceID")
                              ).And(
                                    abo.VCARD_UID.In(Parameter("uids", len(uids))))),
                      )


    @classmethod
    def _deleteMembersWithGroupIDAndMemberIDsQuery(cls, groupID, memberIDs): #@NoSelf
        aboMembers = schema.ABO_MEMBERS
        return Delete(
            aboMembers,
            Where=(aboMembers.GROUP_ID == groupID).And(
                    aboMembers.MEMBER_ID.In(Parameter("memberIDs", len(memberIDs)))))


    @classmethod
    def _deleteForeignMembersWithGroupIDAndMembeAddrsQuery(cls, groupID, memberAddrs): #@NoSelf
        aboForeignMembers = schema.ABO_FOREIGN_MEMBERS
        return Delete(
            aboForeignMembers,
            Where=(aboForeignMembers.GROUP_ID == groupID).And(
                    aboForeignMembers.MEMBER_ADDRESS.In(Parameter("memberAddrs", len(memberAddrs)))))


    @classproperty
    def _insertABObject(cls): #@NoSelf
        """
        DAL statement to create an addressbook object with all default values.
        """
        abo = schema.ADDRESSBOOK_OBJECT
        return Insert(
            {abo.RESOURCE_ID: schema.RESOURCE_ID_SEQ,
             abo.ADDRESSBOOK_RESOURCE_ID: Parameter("addressbookResourceID"),
             abo.RESOURCE_NAME: Parameter("name"),
             abo.VCARD_TEXT: Parameter("text"),
             abo.VCARD_UID: Parameter("uid"),
             abo.KIND: Parameter("kind"),
             abo.MD5: Parameter("md5"),
             },
            Return=(abo.RESOURCE_ID,
                    abo.CREATED,
                    abo.MODIFIED))


    @inlineCallbacks
    def updateDatabase(self, component, expand_until=None, reCreate=False,
                       inserting=False):
        """
        Update the database tables for the new data being written.

        @param component: addressbook data to store
        @type component: L{Component}
        """

        componentResourceKindToAddressBookObjectKindMap = {
            "person": _ABO_KIND_PERSON,
            "group": _ABO_KIND_GROUP,
            "resource": _ABO_KIND_RESOURCE,
            "location": _ABO_KIND_LOCATION,
        }
        lcResourceKind = component.resourceKind().lower() if component.resourceKind() else component.resourceKind();
        kind = componentResourceKindToAddressBookObjectKindMap.get(lcResourceKind, _ABO_KIND_PERSON)
        assert inserting or self._kind == kind  # can't change kind. Should be checked in upper layers
        self._kind = kind

        # For shared groups:  Non owner may NOT add group members not currently in group!
        # (Or it would be possible to troll for unshared vCard UIDs and make them shared.)
        if not self._ownerAddressBookResourceID:
            ownerAddressBook = yield self._addressbook.ownerAddressBook()
            self._ownerAddressBookResourceID = ownerAddressBook._resourceID

        if self._kind == _ABO_KIND_GROUP:

            # get member ids
            memberUIDs = []
            foreignMemberAddrs = []
            for memberAddr in component.resourceMemberAddresses():
                if len(memberAddr) > len("urn:uuid:") and memberAddr.startswith("urn:uuid:"):
                    memberUIDs.append(memberAddr[len("urn:uuid:"):])
                else:
                    foreignMemberAddrs.append(memberAddr)

            memberRows = yield self._resourceIDAndUIDForUIDsAndAddressBookResourceIDQuery(memberUIDs).on(
                self._txn, addressbookResourceID=self._ownerAddressBookResourceID, uids=memberUIDs
            ) if memberUIDs else []
            memberIDs = [memberRow[0] for memberRow in memberRows]
            foundUIDs = [memberRow[1] for memberRow in memberRows]
            foreignMemberAddrs.extend(["urn:uuid:" + missingUID for missingUID in set(memberUIDs) - set(foundUIDs)])

            if not self.owned():
                if not self._addressbook.fullyShared():
                    #in shared ab defined by groups, all members must be inside the shared groups

                    #FIXME: does this apply to whole-shared address books too?
                    if foreignMemberAddrs:
                        raise GroupWithUnsharedAddressNotAllowedError

                    acceptedGroupIDs = yield self._addressbook.acceptedGroupIDs()
                    allowedObjectIDs = yield self._addressbook._objectIDsInExpandedGroupIDs(self._txn, acceptedGroupIDs)
                    if set(memberIDs) - set(allowedObjectIDs):
                        raise GroupWithUnsharedAddressNotAllowedError

            # don't store group members in object text

            # sort addreses in component text
            memberAddresses = component.resourceMemberAddresses()
            component.removeProperties("X-ADDRESSBOOKSERVER-MEMBER")
            for memberAddress in sorted(memberAddresses):
                component.addProperty(Property("X-ADDRESSBOOKSERVER-MEMBER", memberAddress))

            # use sorted test to get size and md5
            componentText = str(component)
            self._md5 = hashlib.md5(componentText).hexdigest()
            self._size = len(componentText)

            # remove members from component get new text
            component.removeProperties("X-ADDRESSBOOKSERVER-MEMBER")
            componentText = str(component)
            self._objectText = componentText

        else:
            componentText = str(component)
            self._md5 = hashlib.md5(componentText).hexdigest()
            self._size = len(componentText)
            self._objectText = componentText

        uid = component.resourceUID()
        assert inserting or self._uid == uid # can't change UID. Should be checked in upper layers
        self._uid = uid

        # Special - if migrating we need to preserve the original md5    
        if self._txn._migrating and hasattr(component, "md5"):
            self._md5 = component.md5

        abo = schema.ADDRESSBOOK_OBJECT
        aboForeignMembers = schema.ABO_FOREIGN_MEMBERS
        aboMembers = schema.ABO_MEMBERS

        if inserting:

            self._resourceID, self._created, self._modified = (
                yield self._insertABObject.on(
                    self._txn,
                    addressbookResourceID=self._ownerAddressBookResourceID,
                    name=self._name,
                    text=self._objectText,
                    uid=self._uid,
                    md5=self._md5,
                    kind=self._kind,
                )
            )[0]

            # delete foreign members table row for this object
            groupIDRows = yield Delete(
                aboForeignMembers,
                # should this be scoped to the owner address book?
                Where=aboForeignMembers.MEMBER_ADDRESS == "urn:uuid:" + self._uid,
                Return=aboForeignMembers.GROUP_ID
            ).on(self._txn)
            groupIDs = [groupIDRow[0] for groupIDRow in groupIDRows]

            # FIXME: Is this correct?
            if not self.owned():
                if not self._addressbook.fullyShared() or self._addressbook.shareMode() != _BIND_MODE_WRITE:
                    writeableAcceptedGroupIDs = yield self._addressbook.writeableAcceptedGroupIDs()
                    assert writeableAcceptedGroupIDs, "no access"
                    groupIDs.extend(writeableAcceptedGroupIDs)

            # add to member table rows
            for groupID in groupIDs:
                yield Insert(
                    {aboMembers.GROUP_ID: groupID,
                     aboMembers.ADDRESSBOOK_ID: self._ownerAddressBookResourceID,
                     aboMembers.MEMBER_ID: self._resourceID, }
                ).on(self._txn)

        else:
            self._modified = (yield Update(
                {abo.VCARD_TEXT: self._objectText,
                 abo.MD5: self._md5,
                 abo.MODIFIED: utcNowSQL},
                Where=abo.RESOURCE_ID == self._resourceID,
                Return=abo.MODIFIED).on(self._txn))[0][0]

        if self._kind == _ABO_KIND_GROUP:

            #get current members
            currentMemberRows = yield Select([aboMembers.MEMBER_ID],
                 From=aboMembers,
                 Where=aboMembers.GROUP_ID == self._resourceID,).on(self._txn)
            currentMemberIDs = [currentMemberRow[0] for currentMemberRow in currentMemberRows]

            memberIDsToDelete = set(currentMemberIDs) - set(memberIDs)
            memberIDsToAdd = set(memberIDs) - set(currentMemberIDs)

            if memberIDsToDelete:
                yield self._deleteMembersWithGroupIDAndMemberIDsQuery(self._resourceID, memberIDsToDelete).on(
                    self._txn, memberIDs=memberIDsToDelete
                )

            for memberIDToAdd in memberIDsToAdd:
                yield Insert(
                    {aboMembers.GROUP_ID: self._resourceID,
                     aboMembers.ADDRESSBOOK_ID: self._ownerAddressBookResourceID,
                     aboMembers.MEMBER_ID: memberIDToAdd, }
                ).on(self._txn)

            # don't bother with aboForeignMembers on address books
            if self._resourceID != self._ownerAddressBookResourceID:

                #get current foreign members 
                currentForeignMemberRows = yield Select(
                    [aboForeignMembers.MEMBER_ADDRESS],
                     From=aboForeignMembers,
                     Where=aboForeignMembers.GROUP_ID == self._resourceID,).on(self._txn)
                currentForeignMemberAddrs = [currentForeignMemberRow[0] for currentForeignMemberRow in currentForeignMemberRows]

                foreignMemberAddrsToDelete = set(currentForeignMemberAddrs) - set(foreignMemberAddrs)
                foreignMemberAddrsToAdd = set(foreignMemberAddrs) - set(currentForeignMemberAddrs)

                if foreignMemberAddrsToDelete:
                    yield self._deleteForeignMembersWithGroupIDAndMembeAddrsQuery(self._resourceID, foreignMemberAddrsToDelete).on(
                        self._txn, memberAddrs=foreignMemberAddrsToDelete
                    )

                for foreignMemberAddrToAdd in foreignMemberAddrsToAdd:
                    yield Insert(
                        {aboForeignMembers.GROUP_ID: self._resourceID,
                         aboForeignMembers.ADDRESSBOOK_ID: self._ownerAddressBookResourceID,
                         aboForeignMembers.MEMBER_ADDRESS: foreignMemberAddrToAdd, }
                    ).on(self._txn)


    @inlineCallbacks
    def component(self):
        """
        Read address data and validate/fix it. Do not raise a store error here if there are unfixable
        errors as that could prevent the overall request to fail. Instead we will hand bad data off to
        the caller - that is not ideal but in theory we should have checked everything on the way in and
        only allowed in good data.
        """

        if self._component is None:

            if not self.owned() and  self._resourceID == self._addressbook._resourceID:
                component = yield self._addressbook._groupForEntireAB_Component()
            else:
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

                if self._kind == _ABO_KIND_GROUP:
                    assert not component.hasProperty("X-ADDRESSBOOKSERVER-MEMBER"), "database group vCard text contains members %s" % (component,)

                    # generate "X-ADDRESSBOOKSERVER-MEMBER" properties
                    # first get member resource ids
                    aboMembers = schema.ABO_MEMBERS
                    memberRows = yield Select(
                        [aboMembers.MEMBER_ID],
                         From=aboMembers,
                         Where=aboMembers.GROUP_ID == self._resourceID,
                    ).on(self._txn)
                    memberIDs = [memberRow[0] for memberRow in memberRows]

                    # then get member UIDs
                    abo = schema.ADDRESSBOOK_OBJECT
                    memberUIDRows = (
                        yield self._columnsWithResourceIDsQuery(
                            [abo.VCARD_UID],
                            memberIDs
                        ).on(self._txn, resourceIDs=memberIDs)
                    ) if memberIDs else []
                    memberUIDs = [memberUIDRow[0] for memberUIDRow in memberUIDRows]

                    # add prefix to get property string
                    memberAddresses = ["urn:uuid:" + memberUID for memberUID in memberUIDs]

                    # get foreign members
                    aboForeignMembers = schema.ABO_FOREIGN_MEMBERS
                    foreignMemberRows = yield Select(
                         [aboForeignMembers.MEMBER_ADDRESS],
                         From=aboForeignMembers,
                         Where=aboForeignMembers.GROUP_ID == self._resourceID,
                    ).on(self._txn)
                    foreignMembers = [foreignMemberRow[0] for foreignMemberRow in foreignMemberRows]

                    # now add the properties to the component
                    for memberAddress in sorted(memberAddresses + foreignMembers):
                        component.addProperty(Property("X-ADDRESSBOOKSERVER-MEMBER", memberAddress))

            self._component = component

        returnValue(self._component)


    # IDataStoreObject
    def contentType(self):
        """
        The content type of Addressbook objects is text/vcard.
        """
        return MimeType.fromString("text/vcard; charset=utf-8")


    def owned(self):
        return self._addressbook.owned()


    def ownerHome(self):
        return self._addressbook.ownerHome()


    def viewerHome(self):
        return self._addressbook.viewerHome()


    def shareUID(self):
        """
        @see: L{ICalendar.shareUID}
        """
        return self._bindName


    @classmethod
    @inlineCallbacks
    def ownerHomeID(cls, txn, resourceID):

        # no owner, so shared item must be group
        abo = schema.ADDRESSBOOK_OBJECT
        groupAddressBookRows = yield Select(
            [abo.ADDRESSBOOK_RESOURCE_ID],
            From=abo,
            Where=(abo.RESOURCE_ID == Parameter("resourceID"))
        ).on(txn, resourceID=resourceID)
        groupAddressBookID = groupAddressBookRows[0][0]
        ownerHomeRows = yield cls._ownerHomeWithResourceID.on(txn, resourceID=groupAddressBookID)

        returnValue(ownerHomeRows[0][0])


    # TODO: use _homeChildMetaDataSchema and CommonHomeChild._childrenAndMetadataForHomeID() instead
    @classproperty
    def _childrenAndMetadataForHomeID(cls): #@NoSelf
        aboBind = cls._bindSchema
        aboSchema = cls._objectSchema
        aboMetaDataColumns = [aboSchema.CREATED, aboSchema.MODIFIED, ]

        columns = [aboBind.BIND_MODE,
                   aboBind.HOME_RESOURCE_ID,
                   aboBind.RESOURCE_ID,
                   aboBind.RESOURCE_NAME,
                   aboBind.BIND_STATUS,
                   aboBind.MESSAGE]
        columns.extend(aboMetaDataColumns)
        return Select(columns,
                     From=aboSchema.join(
                         aboBind, aboSchema.RESOURCE_ID == aboBind.RESOURCE_ID,
                         'left outer'),
                     Where=(aboBind.HOME_RESOURCE_ID == Parameter("homeID")
                           ).And(aboBind.BIND_STATUS == _BIND_STATUS_ACCEPTED))


    @inlineCallbacks
    def notifyChanged(self):
        returnValue((yield self._addressbook.notifyChanged()))


    @inlineCallbacks
    def asShared(self):
        """
        Retrieve all the versions of this L{CommonHomeChild} as it is shared to
        everyone.

        @see: L{ICalendarHome.asShared}

        @return: L{CommonHomeChild} objects that represent this
            L{CommonHomeChild} as a child of different L{CommonHome}s
        @rtype: a L{Deferred} which fires with a L{list} of L{ICalendar}s.
        """
        if not self.owned():
            returnValue([])

        # get all accepted shared binds
        groupBindRows = yield self._sharedBindForResourceID.on(
            self._txn, resourceID=self._resourceID, homeID=self._home._resourceID
        )

        result = []
        for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in groupBindRows: #@UnusedVariable
            home = yield self._txn.homeWithResourceID(self._home._homeType, homeID)
            addressbook = yield home.childWithID(self._addressbook._resourceID)
            new = yield addressbook.objectResourceWithID(resourceID)
            result.append(new)

        returnValue(result)


    @inlineCallbacks
    def asInvited(self):
        """
        Retrieve all the versions of this L{CommonHomeChild} as it is shared to
        everyone.

        @see: L{ICalendarHome.asShared}

        @return: L{CommonHomeChild} objects that represent this
            L{CommonHomeChild} as a child of different L{CommonHome}s
        @rtype: a L{Deferred} which fires with a L{list} of L{ICalendar}s.
        """
        if not self.owned():
            returnValue([])

        # get all accepted shared binds
        groupBindRows = yield self._unacceptedBindForResourceID.on(
            self._txn, resourceID=self._resourceID
        )

        result = []
        for bindMode, homeID, resourceID, bindName, bindStatus, bindMessage in groupBindRows: #@UnusedVariable
            home = yield self._txn.homeWithResourceID(self._home._homeType, homeID)
            addressbook = yield home.childWithID(self._addressbook._resourceID)
            new = yield AddressBookObject.objectWithID(addressbook, resourceID) # avoids object cache
            result.append(new)

        returnValue(result)


    @classproperty
    def _addressbookIDForResourceID(cls): #@NoSelf
        #TODO: This query could be part of previously called query using object schema join
        obj = cls._objectSchema
        return Select([obj.PARENT_RESOURCE_ID],
                      From=obj,
                      Where=obj.RESOURCE_ID == Parameter("resourceID")
                    )


    @classmethod
    @inlineCallbacks
    def ownerAddressBookID(cls, txn, resourceID):
        ownerAddressBookIDRows = yield cls._addressbookIDForResourceID.on(txn, resourceID=resourceID)
        returnValue(ownerAddressBookIDRows[0][0])


    @inlineCallbacks
    def unshare(self):
        """
        Unshares a group, regardless of which "direction" it was shared.
        """
        if self.owned():
            # This collection may be shared to others
            for sharedToHome in [x.viewerHome() for x in (yield self.asShared())]:
                yield self.unshareWith(sharedToHome)
        else:
            # This collection is shared to me
            ownerHomeID = yield self.ownerHomeID(self._txn, self._resourceID)
            ownerHome = yield self._txn.homeWithResourceID(self._home._homeType,
                ownerHomeID)
            ownerAddressBook = yield ownerHome.addressbook()
            ownerGroup = yield ownerAddressBook.objectResourceWithID(self._resourceID)
            yield ownerGroup.unshareWith(self._home)


    @inlineCallbacks
    def unshareWith(self, shareeHome):
        """
        Remove the shared version of this (owned) L{CommonHomeChild} from the
        referenced L{CommonHome}.

        @see: L{CommonHomeChild.shareWith}

        @param shareeHome: The home with which this L{CommonHomeChild} was
            previously shared.

        @return: a L{Deferred} which will fire with the previously-used name.
        """
        sharedAddressBook = yield shareeHome.addressbookWithName(self._addressbook.shareeABName())
        if sharedAddressBook:

            acceptedBindCount = 1 if sharedAddressBook.fullyShared() else 0
            acceptedBindCount += len((
                yield AddressBookObject._acceptedBindWithHomeIDAndAddressBookID.on(
                    self._txn, homeID=shareeHome._resourceID, addressbookID=sharedAddressBook._resourceID
                )
            ))

            if acceptedBindCount == 1:
                sharedAddressBook._deletedSyncToken(sharedRemoval=True)
                shareeHome._children.pop(self._addressbook.shareeABName(), None)

            # Must send notification to ensure cache invalidation occurs
            yield self._addressbook.notifyChanged()

        # delete binds including invites
        deletedBindNameRows = yield self._deleteBindWithResourceIDAndHomeID.on(
            self._txn, resourceID=self._resourceID,
             homeID=shareeHome._resourceID
        )
        if deletedBindNameRows:
            deletedBindName = deletedBindNameRows[0][0]
            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeHome._resourceID, self._addressbook.shareeABName())
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)
        else:
            deletedBindName = None

        returnValue(deletedBindName)


    @inlineCallbacks
    def updateShare(self, shareeView, mode=None, status=None, message=None, name=None):
        """
        Update share mode, status, and message for a home child shared with
        this (owned) L{CommonHomeChild}.

        @param shareeView: The sharee home child that shares this.
        @type shareeView: L{CommonHomeChild}

        @param mode: The sharing mode; L{_BIND_MODE_READ} or
            L{_BIND_MODE_WRITE} or None to not update
        @type mode: L{str}

        @param status: The sharing status; L{_BIND_STATUS_INVITED} or
            L{_BIND_STATUS_ACCEPTED} or L{_BIND_STATUS_DECLINED} or
            L{_BIND_STATUS_INVALID}  or None to not update
        @type status: L{str}

        @param message: The proposed message to go along with the share, which
            will be used as the default display name, or None to not update
        @type message: L{str}

        @param name: The bind resource name or None to not update
        @type message: L{str}

        @return: the name of the shared item in the sharee's home.
        @rtype: a L{Deferred} which fires with a L{str}
        """
        # TODO: raise a nice exception if shareeView is not, in fact, a shared
        # version of this same L{CommonHomeChild}


        #remove None parameters, and substitute None for empty string
        bind = self._bindSchema
        columnMap = dict([(k, v if v else None)
                          for k, v in {bind.BIND_MODE:mode,
                            bind.BIND_STATUS:status,
                            bind.MESSAGE:message,
                            bind.RESOURCE_NAME:name}.iteritems() if v is not None])

        if len(columnMap):

            # count accepted 
            if status is not None:
                previouslyAcceptedBindCount = 1 if shareeView._addressbook.fullyShared() else 0
                previouslyAcceptedBindCount += len((
                    yield AddressBookObject._acceptedBindWithHomeIDAndAddressBookID.on(
                        self._txn, homeID=shareeView._home._resourceID, addressbookID=self._addressbook._resourceID
                    )
                ))

            sharedname = yield self._updateBindColumnsQuery(columnMap).on(
                self._txn,
                resourceID=self._resourceID, homeID=shareeView._home._resourceID
            )

            #update affected attributes
            if mode is not None:
                shareeView._bindMode = columnMap[bind.BIND_MODE]

            if status is not None:
                shareeView._bindStatus = columnMap[bind.BIND_STATUS]
                if shareeView._bindStatus == _BIND_STATUS_ACCEPTED:
                    if 0 == previouslyAcceptedBindCount:
                        yield shareeView._addressbook._initSyncToken()
                elif shareeView._bindStatus != _BIND_STATUS_INVITED:
                    if 1 == previouslyAcceptedBindCount:
                        shareeView._addressbook._deletedSyncToken(sharedRemoval=True)
                        shareeView._home._children.pop(shareeView._addressbook._name, None)

            if message is not None:
                shareeView._bindMessage = columnMap[bind.MESSAGE]

            # safer to just invalidate in all cases rather than calculate when to invalidate
            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeView._home._resourceID, shareeView._addressbook._name)
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)

            shareeView._name = sharedname[0][0]

            # Must send notification to ensure cache invalidation occurs
            yield self._addressbook.notifyChanged()

        returnValue(shareeView._name)


    @classproperty
    def _acceptedBindWithAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls._bindColumns(),
                  From=bind.join(abo),
                  Where=(bind.BIND_STATUS == _BIND_STATUS_ACCEPTED)
                        .And(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(abo.ADDRESSBOOK_RESOURCE_ID == Parameter("addressbookID"))
        )

    @classproperty
    def _unacceptedBindWithAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls._bindColumns(),
                  From=bind.join(abo),
                  Where=(bind.BIND_STATUS != _BIND_STATUS_ACCEPTED)
                        .And(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(abo.ADDRESSBOOK_RESOURCE_ID == Parameter("addressbookID"))
        )


    @classproperty
    def _acceptedBindWithHomeIDAndAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls._bindColumns(),
                  From=bind.join(abo),
                  Where=(bind.BIND_STATUS == _BIND_STATUS_ACCEPTED)
                        .And(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(bind.HOME_RESOURCE_ID == Parameter("homeID"))
                        .And(abo.ADDRESSBOOK_RESOURCE_ID == Parameter("addressbookID"))
        )


    @classproperty
    def _invitedBindWithHomeIDAndAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls._bindColumns(),
                  From=bind.join(abo),
                  Where=(bind.BIND_STATUS == _BIND_STATUS_INVITED)
                        .And(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(bind.HOME_RESOURCE_ID == Parameter("homeID"))
                        .And(abo.ADDRESSBOOK_RESOURCE_ID == Parameter("addressbookID"))
        )


    @classproperty
    def _bindWithHomeIDAndAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls._bindColumns(),
                  From=bind.join(abo),
                  Where=(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(bind.HOME_RESOURCE_ID == Parameter("homeID"))
                        .And(abo.ADDRESSBOOK_RESOURCE_ID == Parameter("addressbookID"))
        )


AddressBook._objectResourceClass = AddressBookObject
