# -*- test-case-name: txdav.carddav.datastore.test.test_sql -*-
# #
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
# #

"""
SQL backend for CardDAV storage.
"""

__all__ = [
    "AddressBookHome",
    "AddressBook",
    "AddressBookObject",
]

from copy import deepcopy

from twext.enterprise.dal.syntax import Delete, Insert, Len, Parameter, \
    Update, Union, Max, Select, utcNowSQL
from twext.enterprise.locking import NamedLock
from twext.python.clsprop import classproperty
from twext.web2.http import HTTPError
from twext.web2.http_headers import MimeType
from twext.web2.responsecode import FORBIDDEN

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python import hashlib

from twistedcaldav import carddavxml, customxml
from twistedcaldav.config import config
from twistedcaldav.memcacher import Memcacher
from twistedcaldav.vcard import Component as VCard, InvalidVCardDataError, \
    vCardProductID, Property

from txdav.base.propertystore.base import PropertyName
from txdav.base.propertystore.sql import PropertyStore
from txdav.carddav.iaddressbookstore import IAddressBookHome, IAddressBook, \
    IAddressBookObject, GroupWithUnsharedAddressNotAllowedError, \
    KindChangeNotAllowedError
from txdav.common.datastore.sql import CommonHome, CommonHomeChild, \
    CommonObjectResource, EADDRESSBOOKTYPE, SharingMixIn, SharingInvitation
from txdav.common.datastore.sql_legacy import PostgresLegacyABIndexEmulator
from txdav.common.datastore.sql_tables import _ABO_KIND_PERSON, \
    _ABO_KIND_GROUP, _ABO_KIND_RESOURCE, _ABO_KIND_LOCATION, schema, \
    _BIND_MODE_OWN, _BIND_MODE_WRITE, _BIND_STATUS_ACCEPTED, \
    _BIND_STATUS_DECLINED
from txdav.common.icommondatastore import InternalDataStoreError, \
    InvalidUIDError, UIDExistsError, ObjectResourceTooBigError, \
    InvalidObjectResourceError, InvalidComponentForStoreError, \
    AllRetriesFailed, ObjectResourceNameAlreadyExistsError, \
    SyncTokenValidException

from uuid import uuid4

from zope.interface.declarations import implements


class AddressBookHome(CommonHome):

    implements(IAddressBookHome)

    # structured tables.  (new, preferred)
    _homeSchema = schema.ADDRESSBOOK_HOME
    _bindSchema = schema.SHARED_ADDRESSBOOK_BIND
    _homeMetaDataSchema = schema.ADDRESSBOOK_HOME_METADATA
    _revisionsSchema = schema.ADDRESSBOOK_OBJECT_REVISIONS
    _objectSchema = schema.ADDRESSBOOK_OBJECT

    _notifierPrefix = "CardDAV"
    _dataVersionKey = "ADDRESSBOOK-DATAVERSION"
    _cacher = Memcacher("SQL.adbkhome", pickle=True, key_normalization=False)


    def __init__(self, transaction, ownerUID):

        self._childClass = AddressBook
        super(AddressBookHome, self).__init__(transaction, ownerUID)
        self._addressbookPropertyStoreID = None
        self._addressbook = None


    def __repr__(self):
        return '<%s: %s("%s")>' % (self.__class__.__name__, self._resourceID, self.name())

    addressbooks = CommonHome.children
    listAddressbooks = CommonHome.listChildren
    loadAddressbooks = CommonHome.loadChildren
    addressbookWithName = CommonHome.childWithName
    createAddressBookWithName = CommonHome.createChildWithName
    removeAddressBookWithName = CommonHome.removeChildWithName


    @classproperty
    def _resourceIDAndHomeResourceIDFromOwnerQuery(cls): #@NoSelf
        home = cls._homeSchema
        return Select([home.RESOURCE_ID, home.ADDRESSBOOK_PROPERTY_STORE_ID],
                      From=home, Where=home.OWNER_UID == Parameter("ownerUID"))


    @inlineCallbacks
    def initFromStore(self, no_cache=False):
        """
        Initialize this object from the store. We read in and cache all the
        extra meta-data from the DB to avoid having to do DB queries for those
        individually later.
        """
        result = yield self._cacher.get(self._ownerUID)
        if result is None:
            result = yield self._resourceIDAndHomeResourceIDFromOwnerQuery.on(
                self._txn, ownerUID=self._ownerUID)
            if result and not no_cache:
                yield self._cacher.set(self._ownerUID, result)

        if result:
            self._resourceID, self._addressbookPropertyStoreID = result[0]

            queryCacher = self._txn._queryCacher
            if queryCacher:
                # Get cached copy
                cacheKey = queryCacher.keyForHomeMetaData(self._resourceID)
                data = yield queryCacher.get(cacheKey)
            else:
                data = None
            if data is None:
                # Don't have a cached copy
                data = (yield self._metaDataQuery.on(
                    self._txn, resourceID=self._resourceID))[0]
                if queryCacher:
                    # Cache the data
                    yield queryCacher.setAfterCommit(self._txn, cacheKey, data)

            # self._created, self._modified = data
            yield self._loadPropertyStore()

            # created owned address book
            addressbook = AddressBook(
                home=self,
                name="addressbook", resourceID=self._resourceID,
                mode=_BIND_MODE_OWN, status=_BIND_STATUS_ACCEPTED,
            )
            self._created, self._modified = data
            yield addressbook._loadPropertyStore()
            yield addressbook._initIsShared()
            self._addressbook = addressbook

            returnValue(self)
        else:
            returnValue(None)


    @inlineCallbacks
    def remove(self):
        ah = schema.ADDRESSBOOK_HOME
        ahb = schema.SHARED_ADDRESSBOOK_BIND
        aor = schema.ADDRESSBOOK_OBJECT_REVISIONS
        rp = schema.RESOURCE_PROPERTY

        yield Delete(
            From=ahb,
            Where=ahb.ADDRESSBOOK_HOME_RESOURCE_ID == self._resourceID,
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
            Where=(rp.RESOURCE_ID == self._resourceID).Or(
                rp.RESOURCE_ID == self._addressbookPropertyStoreID
            )
        ).on(self._txn)

        yield self._cacher.delete(str(self._ownerUID))


    @inlineCallbacks
    def createdHome(self):
        yield self.addressbook()._initSyncToken()


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


    def addressbook(self):
        return self._addressbook


    def shareeAddressBookName(self):
        return self.uid()


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


    @inlineCallbacks
    def ownerHomeWithChildID(self, resourceID):
        """
        Get the owner home for a shared child ID
        """
        # addressbook and home have same resourceID
        ownerHome = yield self._txn.homeWithResourceID(self._homeType, resourceID, create=True)
        returnValue(ownerHome)


    @inlineCallbacks
    def ownerHomeAndChildNameForChildID(self, resourceID):
        """
        Get the owner home for a shared child ID and the owner's name for that bound child.
        Subclasses may override.
        """
        ownerHome = yield self.ownerHomeWithChildID(resourceID)
        ownerName = ownerHome.addressbook().name()
        returnValue((ownerHome, ownerName))


    @classproperty
    def _syncTokenQuery(cls): #@NoSelf
        """
        DAL Select statement to find the sync token.
        """
        rev = cls._revisionsSchema
        bind = cls._bindSchema
        abo = cls._objectSchema
        groupBind = AddressBookObject._bindSchema
        return Select(
            [Max(rev.REVISION)],
            # active shared address books
            From=Select(
                [rev.REVISION],
                From=rev,
                Where=(
                    rev.RESOURCE_ID.In(
                        Select(
                            [bind.RESOURCE_ID],
                            From=bind,
                            Where=bind.HOME_RESOURCE_ID == Parameter("resourceID"),
                            SetExpression=Union(
                                Select(
                                    [abo.ADDRESSBOOK_HOME_RESOURCE_ID],
                                    From=abo,
                                    Where=(
                                        abo.RESOURCE_ID.In(
                                            Select(
                                                [groupBind.GROUP_RESOURCE_ID],
                                                From=groupBind,
                                                Where=groupBind.ADDRESSBOOK_HOME_RESOURCE_ID == Parameter("resourceID"),
                                            )
                                        )
                                    )
                                ),
                                optype=Union.OPTYPE_ALL,
                            )
                        )
                    )
                ),
                SetExpression=Union(
                    # deleted shared address books
                    Select(
                        [rev.REVISION],
                        From=rev,
                        Where=(rev.HOME_RESOURCE_ID == Parameter("resourceID")).And(rev.RESOURCE_ID == None),
                        SetExpression=Union(
                            # owned address book
                            Select(
                                [rev.REVISION],
                                From=rev,
                                Where=(rev.HOME_RESOURCE_ID == Parameter("resourceID")).And(rev.RESOURCE_ID == rev.HOME_RESOURCE_ID),
                            ),
                            optype=Union.OPTYPE_ALL,
                        )
                    ),
                    optype=Union.OPTYPE_ALL,
                )
            ),
        )


    @classproperty
    def _changesQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Select(
            [rev.ADDRESSBOOK_NAME,
             rev.RESOURCE_NAME,
             rev.DELETED],
            From=rev,
            Where=(rev.REVISION > Parameter("revision")).And(
                rev.HOME_RESOURCE_ID == Parameter("resourceID"))
        )


    @inlineCallbacks
    def doChangesQuery(self, revision):

        rows = yield self._changesQuery.on(
            self._txn,
            resourceID=self._resourceID,
            revision=revision
        )

        # If the collection name is None that means we have a change to the owner's default address book,
        # so substitute in the name of that. If collection name is not None, then we have a revision
        # for the owned or a shared address book itself.
        result = [[row[0] if row[0] is not None else self.addressbook().name()] + row for row in rows]
        returnValue(result)


AddressBookHome._register(EADDRESSBOOKTYPE)



class AddressBookSharingMixIn(SharingMixIn):
    """
        Sharing code shared between AddressBook and AddressBookObject
    """

    def setShared(self, shared):
        """
        Set an owned collection to shared or unshared state. Technically this is not useful as "shared"
        really means it has invitees, but the current sharing spec supports a notion of a shared collection
        that has not yet had invitees added. For the time being we will support that option by using a new
        MESSAGE value to indicate an owned collection that is "shared".

        @param shared: whether or not the owned collection is "shared"
        @type shared: C{bool}
        """
        if self.owned():
            self._bindMessage = "shared" if shared else None


    @inlineCallbacks
    def _isSharedOrInvited(self):
        """
        return True if this L{AddressBook} is shared or invited
        """
        sharedRows = []
        if self.owned():
            bind = self._bindSchema
            sharedRows = yield self._bindFor(
                (bind.RESOURCE_ID == Parameter("resourceID"))).on(
                self._txn, resourceID=self._resourceID,
            )

        returnValue(bool(sharedRows))


    @inlineCallbacks
    def _initIsShared(self):
        isShared = yield self._isSharedOrInvited()
        self.setShared(isShared)



class AddressBook(CommonHomeChild, AddressBookSharingMixIn):
    """
    SQL-based implementation of L{IAddressBook}.
    """
    implements(IAddressBook)

    # structured tables.  (new, preferred)
    _homeSchema = schema.ADDRESSBOOK_HOME
    _bindSchema = schema.SHARED_ADDRESSBOOK_BIND
    _homeChildSchema = schema.ADDRESSBOOK_HOME
    _homeChildMetaDataSchema = schema.ADDRESSBOOK_HOME_METADATA
    _revisionsSchema = schema.ADDRESSBOOK_OBJECT_REVISIONS
    _objectSchema = schema.ADDRESSBOOK_OBJECT


    def __init__(self, home, name, resourceID, mode, status, revision=0, message=None, ownerHome=None, bindName=None):
        ownerName = ownerHome.addressbook().name() if ownerHome else None
        super(AddressBook, self).__init__(
            home, name, resourceID, mode, status, revision=revision,
            message=message, ownerHome=ownerHome, ownerName=ownerName
        )
        self._index = PostgresLegacyABIndexEmulator(self)
        self._bindName = bindName


    def __repr__(self):
        return '<%s: %s("%s")>' % (self.__class__.__name__, self._resourceID, self.name())


    def getCreated(self):
        return self.ownerHome()._created


    def setCreated(self, newValue):
        self.ownerHome()._created = newValue


    def getModified(self):
        return self.ownerHome()._modified


    def setModified(self, newValue):
        self.ownerHome()._modified = newValue

    _created = property(getCreated, setCreated,)
    _modified = property(getModified, setModified,)

    ownerAddressBookHome = CommonHomeChild.ownerHome
    viewerAddressBookHome = CommonHomeChild.viewerHome
    addressbookObjects = CommonHomeChild.objectResources
    listAddressBookObjects = CommonHomeChild.listObjectResources
    addressbookObjectWithName = CommonHomeChild.objectResourceWithName
    addressbookObjectWithUID = CommonHomeChild.objectResourceWithUID
    createAddressBookObjectWithName = CommonHomeChild.createObjectResourceWithName
    addressbookObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken


    def shareeName(self):
        """
        The sharee's name for a shared address book is the sharer's home ownerUID.
        """
        return self.ownerHome().shareeAddressBookName()


    def bindNameIsResourceName(self):
        """
        For shared address books the resource name of an accepted share is not the same as the name
        in the bind table.
        """
        return False


    @classproperty
    def _deleteBumpTokenQuery(cls): #@NoSelf
        rev = cls._revisionsSchema
        return Update({rev.REVISION: schema.REVISION_SEQ,
                       rev.OBJECT_RESOURCE_ID: Parameter("id"),
                       rev.DELETED: True},
                      Where=(rev.RESOURCE_ID == Parameter("resourceID")).And(
                           rev.RESOURCE_NAME == Parameter("name")),
                      Return=rev.REVISION)


    @inlineCallbacks
    def _changeRevision(self, action, name, id=0):

        # Need to handle the case where for some reason the revision entry is
        # actually missing. For a "delete" we don't care, for an "update" we
        # will turn it into an "insert".
        if action == "delete":
            rows = (
                yield self._deleteBumpTokenQuery.on(
                    self._txn, resourceID=self._resourceID, name=name, id=id))
            if rows:
                self._syncTokenRevision = rows[0][0]
        elif action == "update":
            rows = (
                yield self._updateBumpTokenQuery.on(
                    self._txn, resourceID=self._resourceID, name=name))
            if rows:
                self._syncTokenRevision = rows[0][0]
            else:
                action = "insert"

        if action == "insert":
            # Note that an "insert" may happen for a resource that previously
            # existed and then was deleted. In that case an entry in the
            # REVISIONS table still exists so we have to detect that and do db
            # INSERT or UPDATE as appropriate

            found = bool((
                yield self._insertFindPreviouslyNamedQuery.on(
                    self._txn, resourceID=self._resourceID, name=name)))
            if found:
                self._syncTokenRevision = (
                    yield self._updatePreviouslyNamedQuery.on(
                        self._txn, resourceID=self._resourceID, name=name)
                )[0][0]
            else:
                self._syncTokenRevision = (
                    yield self._completelyNewRevisionQuery.on(
                        self._txn, homeID=self.ownerHome()._resourceID,
                        resourceID=self._resourceID, name=name)
                )[0][0]
        self._maybeNotify()
        returnValue(self._syncTokenRevision)


    def _deleteRevision(self, name, id=0):
        return self._changeRevision("delete", name, id)


    @inlineCallbacks
    def resourceNamesSinceRevision(self, revision):
        """
        Return the changed and deleted resources since a particular revision. This implementation takes
        into account sharing by making use of the bindRevision attribute to determine if the requested
        revision is earlier than the share acceptance. If so, then we need to return all resources in
        the results since the collection is in effect "new".

        @param revision: the revision to determine changes since
        @type revision: C{int}
        """
        if self.owned():
            returnValue((yield super(AddressBook, self).resourceNamesSinceRevision(revision)))

        # call sharedChildResourceNamesSinceRevision() and filter results
        sharedChildChanged, sharedChildDeleted = yield self.sharedChildResourceNamesSinceRevision(revision, "infinity")

        selfPath = self.name() + '/'
        lenpath = len(selfPath)
        changed = [item[lenpath:] for item in sharedChildChanged if item.startswith(selfPath) and item != selfPath]
        deleted = [item[lenpath:] for item in sharedChildDeleted if item.startswith(selfPath) and item != selfPath]
        returnValue((changed, deleted,))


    @inlineCallbacks
    def sharedChildResourceNamesSinceRevision(self, revision, depth):
        """
        Determine the list of child resources that have changed since the specified sync revision.
        We do the same SQL query for both depth "1" and "infinity", but filter the results for
        "1" to only account for a collection change.

        We need to handle shared collection a little differently from owned ones. When a shared collection
        is bound into a home we record a revision for it using the sharee home id and sharee collection name.
        That revision is the "starting point" for changes: so if sync occurs with a revision earlier than
        that, we return the list of all resources in the shared collection since they are all "new" as far
        as the client is concerned since the shared collection has just appeared. For a later revision, we
        just report the changes since that one. When a shared collection is removed from a home, we again
        record a revision for the sharee home and sharee collection name with the "deleted" flag set. That way
        the shared collection can be reported as removed.

        For shared groups.  Find the items that have be added and removed since revision in the aboMembers
        tables.  Then add in changes from the revision table.

        TODO: Cover the case where the sharing changes. Then we can handle revision < bindRevision

        @param revision: the sync revision to compare to
        @type revision: C{str}
        @param depth: depth for determine what changed
        @type depth: C{str}
        """
        assert not self.owned()

        bindRevisions = [self._bindRevision] if self.fullyShared() else []

        groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
        )
        if groupBindRows:
            bindRevisions += [groupBindRow[5] for groupBindRow in groupBindRows]

        if revision != 0 and revision < max(bindRevisions):
            if depth != '1':
                raise SyncTokenValidException
            else:
                revision = 0

        path = self.name()

        if self.fullyShared():
            # add change for addressbook group
            changed, deleted = yield super(AddressBook, self).sharedChildResourceNamesSinceRevision(revision, depth)

            #===================================================================
            # # Add the following to add the addressbook group in sync report:
            #
            # if changed or deleted and depth != "1":
            #     changed.add("%s/%s" % (path, self._groupForSharedAddressBookName(),))
            #===================================================================

            returnValue((changed, deleted))

        changed = set()
        deleted = set()
        acceptedGroupIDs = set([groupBindRow[2] for groupBindRow in groupBindRows])

        allowedObjectIDs = set((yield self.expandGroupIDs(self._txn, acceptedGroupIDs)))
        oldAllowedObjectIDs = set((yield self.expandGroupIDs(self._txn, acceptedGroupIDs, revision)))
        addedObjectIDs = allowedObjectIDs - oldAllowedObjectIDs
        removedObjectIDs = oldAllowedObjectIDs - allowedObjectIDs

        # get revision table changes
        rev = self._revisionsSchema
        results = [(
                name,
                id,
                wasdeleted,
            ) for name, id, wasdeleted in (
                yield Select([rev.RESOURCE_NAME, rev.OBJECT_RESOURCE_ID, rev.DELETED],
                             From=rev,
                            Where=(rev.REVISION > revision).And(
                            rev.RESOURCE_ID == self._resourceID)).on(self._txn)
            ) if name
        ]

        # get deleted object names if any
        idToNameMap = dict([(id, name) for name, id, wasdeleted in results if wasdeleted])

        # now get other names of existing objects
        missingNameIDs = (allowedObjectIDs | oldAllowedObjectIDs) - set(idToNameMap.keys())
        if missingNameIDs:
            abo = schema.ADDRESSBOOK_OBJECT
            memberIDNameRows = (
                yield AddressBookObject._columnsWithResourceIDsQuery(
                    [abo.RESOURCE_ID, abo.RESOURCE_NAME],
                    missingNameIDs
                ).on(self._txn, resourceIDs=missingNameIDs)
            )
            idToNameMap = dict(dict(idToNameMap), **dict(memberIDNameRows))

        # now do revisions
        if revision:

            # handled added or removed objects
            if removedObjectIDs or addedObjectIDs:
                changed.add("%s/" % (path,))

            if depth != "1":
                for removedObjectID in removedObjectIDs:
                    deleted.add("%s/%s" % (path, idToNameMap[removedObjectID],))

                for addedObjectID in addedObjectIDs:
                    changed.add("%s/%s" % (path, idToNameMap[addedObjectID],))

            # use revisions to handle changed objects
            for name, id, wasdeleted in results:
                if not wasdeleted and name in idToNameMap.values():
                    # Always report collection as changed
                    changed.add("%s/" % (path,))

                    # Resource changed - for depth "infinity" report resource as changed
                    if depth != "1":
                        item = "%s/%s" % (path, name,)
                        if item not in deleted:
                            changed.add("%s/%s" % (path, name,))

        else:
            changed.add("%s/" % (path,))
            if depth != "1":
                for addedObjectID in allowedObjectIDs:
                    changed.add("%s/%s" % (path, idToNameMap[addedObjectID],))

        returnValue((changed, deleted))


    @inlineCallbacks
    def _loadPropertyStore(self, props=None):
        if props is None:
            props = yield PropertyStore.load(
                self.ownerHome().uid(),
                self.viewerHome().uid(),
                self._txn,
                self.ownerHome()._addressbookPropertyStoreID,  # not ._resourceID as in CommonHomeChild._loadPropertyStore()
                notifyCallback=self.notifyPropertyChanged
            )
        super(AddressBook, self)._loadPropertyStore(props)


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
        The content type of addressbook objects is text/vcard.
        """
        return MimeType.fromString("text/vcard; charset=utf-8")


    @classmethod
    def create(cls, home, name):
        if name == home.addressbook().name():
            # raise HomeChildNameAlreadyExistsError
            pass
        else:
            # raise HomeChildNameNotAllowedError
            raise HTTPError(FORBIDDEN)


    @inlineCallbacks
    def removedObjectResource(self, child):
        """
            just like CommonHomeChild.removedObjectResource() but does not call self._deleteRevision()
        """
        self._objects.pop(child.name(), None)
        self._objects.pop(child.uid(), None)
        if self._objectNames and child.name() in self._objectNames:
            self._objectNames.remove(child.name())
        #yield self._deleteRevision(child.name())
        yield self.notifyChanged()


    @inlineCallbacks
    def remove(self):

        if self._resourceID == self._home._resourceID:
            # Allow remove, as a way to reset the address book to an empty state
            for abo in (yield self.objectResources()):
                yield abo.remove()
                yield self.removedObjectResource(abo)

            yield self.unshare()  # storebridge should already have done this

            yield self.properties()._removeResource()
            yield self._loadPropertyStore()

            yield self.notifyPropertyChanged()
            yield self._home.notifyChanged()
        else:
            returnValue((yield super(AddressBook, self).remove()))


    def rename(self, name):
        # better error?
        # raise HomeChildNameNotAllowedError
        raise HTTPError(FORBIDDEN)


    @classmethod
    def _objectResourceNamesWithResourceIDsQuery(cls, resourceIDs):
        """
        DAL statement to retrieve addressbook object name with given resourceIDs
        """
        obj = cls._objectSchema
        return Select([obj.RESOURCE_NAME], From=obj,
                      Where=obj.RESOURCE_ID.In(Parameter("resourceIDs", len(resourceIDs))),)


    @inlineCallbacks
    def listObjectResources(self):
        if self._objectNames is None:
            if self.owned() or self.fullyShared():
                rows = yield self._objectResourceNamesQuery.on(
                    self._txn, resourceID=self._resourceID)
            else:
                acceptedGroupIDs = yield self.acceptedGroupIDs()
                allowedObjectIDs = yield self.expandGroupIDs(self._txn, acceptedGroupIDs)
                rows = (yield self._objectResourceNamesWithResourceIDsQuery(allowedObjectIDs).on(
                    self._txn, resourceIDs=allowedObjectIDs
                ))
            objectNames = [row[0] for row in rows]

            # account for fully-shared address book group
            if self.fullyShared():
                if not self._groupForSharedAddressBookName() in objectNames:
                    objectNames.append(self._groupForSharedAddressBookName())
            self._objectNames = sorted(objectNames)

        returnValue(self._objectNames)


    @inlineCallbacks
    def countObjectResources(self):
        if self._objectNames is None:
            if self.owned() or self.fullyShared():
                rows = yield self._objectCountQuery.on(
                    self._txn, resourceID=self._resourceID
                )
                count = rows[0][0]
            else:
                acceptedGroupIDs = yield self.acceptedGroupIDs()
                count = len((yield self.expandGroupIDs(self._txn, acceptedGroupIDs)))

            # account for fully-shared address book group
            if self.fullyShared():
                count += 1
            returnValue(count)

        returnValue(len(self._objectNames))


    @classmethod
    def _abObjectColumnsWithAddressBookResourceID(cls, columns):
        """
        DAL statement to retrieve addressbook object rows with given columns.
        """
        obj = cls._objectSchema
        return Select(columns, From=obj,
                      Where=obj.ADDRESSBOOK_HOME_RESOURCE_ID == Parameter("addressbookResourceID"),)


    def _groupForSharedAddressBookRow(self): #@NoSelf
        return [
            self._resourceID,  # obj.ADDRESSBOOK_HOME_RESOURCE_ID,
            self._resourceID,  # obj.RESOURCE_ID,
            self._groupForSharedAddressBookName(),  # obj.RESOURCE_NAME, shared name is UID and thus avoids collisions
            self._groupForSharedAddressBookUID(),  # obj.UID, shared name is uuid
            _ABO_KIND_GROUP,  # obj.KIND,
            "1",  # obj.MD5, non-zero temporary value; set to correct value when known
            "1",  # Len(obj.TEXT), non-zero temporary value; set to correct value when known
            self._created,  # obj.CREATED,
            self._modified,  # obj.CREATED,
        ]


    def _groupForSharedAddressBookName(self):
        return self.ownerHome().addressbook().name() + ".vcf"


    def _groupForSharedAddressBookUID(self):
        return self.shareUID()


    @inlineCallbacks
    def _groupForSharedAddressBookComponent(self):

        n = self.shareeName()
        fn = n
        uid = self._groupForSharedAddressBookUID()

        #  storebridge should substitute principal name and full name
        #      owner = yield CalDAVResource.principalForUID(self.ownerHome().uid())
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


    @inlineCallbacks
    def bumpModified(self):
        if self._resourceID == self._home._resourceID:
            returnValue((yield self._home.bumpModified()))
        else:
            returnValue((yield super(AddressBook, self).bumpModified()))


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
        results = [home.addressbook()]
        ownerHomeToDataRowMap = {}

        # Load from the main table first
        dataRows = yield cls._childrenAndMetadataForHomeID.on(
            home._txn, homeID=home._resourceID
        )
        # get ownerHomeIDs
        for dataRow in dataRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage = dataRow[:cls.bindColumnCount] #@UnusedVariable
            ownerHome = yield home.ownerHomeWithChildID(resourceID)
            ownerHomeToDataRowMap[ownerHome] = dataRow

        # now get group rows:
        groupBindRows = yield AddressBookObject._childrenAndMetadataForHomeID.on(
            home._txn, homeID=home._resourceID
        )
        for groupBindRow in groupBindRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage = groupBindRow[:AddressBookObject.bindColumnCount] #@UnusedVariable
            ownerAddressBookID = yield AddressBookObject.ownerAddressBookIDFromGroupID(home._txn, resourceID)
            ownerHome = yield home.ownerHomeWithChildID(ownerAddressBookID)
            if ownerHome not in ownerHomeToDataRowMap:
                groupBindRow[0] = _BIND_MODE_WRITE
                groupBindRow[3:7] = 4 * [None]  # bindName, bindStatus, bindRevision, bindMessage
                ownerHomeToDataRowMap[ownerHome] = groupBindRow

        if ownerHomeToDataRowMap:
            # Get property stores for all these child resources (if any found)
            addressbookPropertyStoreIDs = [ownerHome._addressbookPropertyStoreID for ownerHome in ownerHomeToDataRowMap]
            propertyStores = yield PropertyStore.forMultipleResourcesWithResourceIDs(
                home.uid(), home._txn, addressbookPropertyStoreIDs
            )

            addressbookResourceIDs = [ownerHome.addressbook()._resourceID for ownerHome in ownerHomeToDataRowMap]
            revisions = yield cls._revisionsForResourceIDs(addressbookResourceIDs).on(home._txn, resourceIDs=addressbookResourceIDs)
            revisions = dict(revisions)

            # Create the actual objects merging in properties
            for ownerHome, dataRow in ownerHomeToDataRowMap.iteritems():
                bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage = dataRow[:cls.bindColumnCount] #@UnusedVariable
                additionalBind = dataRow[cls.bindColumnCount:cls.bindColumnCount + len(cls.additionalBindColumns())]
                metadata = dataRow[cls.bindColumnCount + len(cls.additionalBindColumns()):]

                child = cls(
                    home=home,
                    name=ownerHome.shareeAddressBookName(),
                    resourceID=ownerHome._resourceID,
                    mode=bindMode, status=bindStatus,
                    revision=bindRevision,
                    message=bindMessage, ownerHome=ownerHome,
                    bindName=bindName
                )

                for attr, value in zip(cls.additionalBindAttributes(), additionalBind):
                    setattr(child, attr, value)
                for attr, value in zip(cls.metadataAttributes(), metadata):
                    setattr(child, attr, value)
                child._syncTokenRevision = revisions[child._resourceID]
                propstore = propertyStores.get(ownerHome._addressbookPropertyStoreID, None)
                # We have to re-adjust the property store object to account for possible shared
                # collections as previously we loaded them all as if they were owned
                if propstore:
                    propstore._setDefaultUserUID(ownerHome.uid())
                yield child._loadPropertyStore(propstore)
                results.append(child)

        returnValue(results)


    @classmethod
    @inlineCallbacks
    def objectWithName(cls, home, name, accepted=True):
        """
        Retrieve the child with the given C{name} contained in the given
        C{home}.

        @param home: a L{CommonHome}.

        @param name: a string; the name of the L{CommonHomeChild} to retrieve.

        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        if accepted and name == home.addressbook().name():
            returnValue(home.addressbook())
        # shared address books only from this point on

        rows = None
        queryCacher = home._txn._queryCacher
        ownerHome = None

        if queryCacher:
            # Retrieve data from cache
            cacheKey = queryCacher.keyForObjectWithName(home._resourceID, name)
            cachedRows = yield queryCacher.get(cacheKey)
            if cachedRows and (cachedRows[0][8] == _BIND_STATUS_ACCEPTED) == bool(accepted): #cachedRows[0][8] == cachedBindStatus
                rows = cachedRows

        if not rows:
            # name must be a home uid
            ownerHome = yield home._txn.addressbookHomeWithUID(name)
            if ownerHome:
                # see if address book resource id in bind table
                ownerAddressBook = ownerHome.addressbook()
                bindRows = yield cls._bindForResourceIDAndHomeID.on(
                    home._txn, resourceID=ownerAddressBook._resourceID, homeID=home._resourceID
                )
                if bindRows and (bindRows[0][4] == _BIND_STATUS_ACCEPTED) == bool(accepted):
                    bindRows[0].insert(cls.bindColumnCount, ownerAddressBook._resourceID)
                    bindRows[0].insert(cls.bindColumnCount + 1, bindRows[0][4])  # cachedStatus = bindStatus
                    rows = bindRows
                else:
                    groupBindRows = yield AddressBookObject._bindForHomeIDAndAddressBookID.on(
                            home._txn, homeID=home._resourceID, addressbookID=ownerAddressBook._resourceID
                    )
                    # prefer a bindStatus match, otherwise get row for cache
                    matchingGroupBindRows = [groupBindRow for groupBindRow in groupBindRows if (groupBindRow[4] == _BIND_STATUS_ACCEPTED) == bool(accepted)]
                    if matchingGroupBindRows:
                        groupBindRows = matchingGroupBindRows
                    elif bindRows:
                        bindRows[0].insert(cls.bindColumnCount, ownerAddressBook._resourceID)
                        bindRows[0].insert(cls.bindColumnCount + 1, bindRows[0][4])  # cachedStatus = bindStatus
                        rows = bindRows

                    if not rows and groupBindRows:
                        groupBindRow = groupBindRows[0]
                        groupBindRow.insert(AddressBookObject.bindColumnCount, ownerAddressBook._resourceID)
                        groupBindRow.insert(AddressBookObject.bindColumnCount + 1, groupBindRow[4])
                        groupBindRow[0] = _BIND_MODE_WRITE
                        groupBindRow[3:7] = 4 * [None]  # bindName, bindStatus, bindRevision, bindMessage
                        rows = [groupBindRow]

            if rows and queryCacher:
                # Cache the result
                queryCacher.setAfterCommit(home._txn, cacheKey, rows)

        if not rows:
            returnValue(None)

        row = rows[0]
        bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage, ownerAddressBookID, cachedBindStatus = row[:cls.bindColumnCount + 2] #@UnusedVariable

        # if wrong status, exit here.  Item is in queryCache
        if (cachedBindStatus == _BIND_STATUS_ACCEPTED) != bool(accepted):
            returnValue(None)

        ownerHome = yield home.ownerHomeWithChildID(ownerAddressBookID)
        child = cls(
                home=home,
                name=ownerHome.shareeAddressBookName(), resourceID=ownerAddressBookID,
                mode=bindMode, status=bindStatus,
                revision=bindRevision,
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
        if bindRows and (bindRows[0][4] == _BIND_STATUS_ACCEPTED) == bool(accepted):
            resourceID = bindRows[0][2]

            # alt:
            # returnValue((yield cls.objectWithID(home, resourceID)))
            ownerHome = yield home.ownerHomeWithChildID(resourceID)
            if accepted:
                returnValue((yield home.childWithName(ownerHome.shareeAddressBookName())))
            else:
                returnValue((yield cls.objectWithName(home, ownerHome.shareeAddressBookName(), accepted=False)))

        groupBindRows = yield AddressBookObject._bindForNameAndHomeID.on(
            home._txn, name=name, homeID=home._resourceID
        )
        if groupBindRows and (groupBindRows[0][4] == _BIND_STATUS_ACCEPTED) == bool(accepted):
            resourceID = groupBindRows[0][2]

            ownerAddressBookID = yield AddressBookObject.ownerAddressBookIDFromGroupID(home._txn, resourceID)
            # alt:
            # addressbook = yield cls.objectWithID(home, ownerAddressBookID)
            ownerHome = yield home.ownerHomeWithChildID(ownerAddressBookID)
            addressbook = yield home.childWithName(ownerHome.shareeAddressBookName())
            if not addressbook:
                addressbook = yield cls.objectWithName(home, ownerHome.shareeAddressBookName(), accepted=False)
                assert addressbook

            if accepted:
                returnValue((yield addressbook.objectResourceWithID(resourceID)))
            else:
                returnValue((yield AddressBookObject.objectWithID(addressbook, resourceID)))  # avoids object cache

        returnValue(None)


    @classmethod
    @inlineCallbacks
    def objectWithID(cls, home, resourceID, accepted=True):
        """
        Retrieve the child with the given C{resourceID} contained in the given
        C{home}.

        @param home: a L{CommonHome}.
        @param resourceID: a string.
        @return: an L{CommonHomeChild} or C{None} if no such child
            exists.
        """
        if home._resourceID == resourceID:
            returnValue(home.addressbook())

        bindRows = yield cls._bindForResourceIDAndHomeID.on(
            home._txn, resourceID=resourceID, homeID=home._resourceID
        )
        if bindRows and (bindRows[0][4] == _BIND_STATUS_ACCEPTED) == bool(accepted):
            resourceID = bindRows[0][2]

            ownerHome = yield home.ownerHomeWithChildID(resourceID)
            if accepted:
                returnValue((yield home.childWithName(ownerHome.shareeAddressBookName())))
            else:
                returnValue((yield cls.objectWithName(home, ownerHome.shareeAddressBookName(), accepted=False)))

        groupBindRows = yield AddressBookObject._bindForHomeIDAndAddressBookID.on(
                    home._txn, homeID=home._resourceID, addressbookID=resourceID
        )
        if groupBindRows and (groupBindRows[0][4] == _BIND_STATUS_ACCEPTED) == bool(accepted):
            resourceID = groupBindRows[0][2]

            ownerAddressBookID = yield AddressBookObject.ownerAddressBookIDFromGroupID(home._txn, resourceID)
            ownerHome = yield home.ownerHomeWithChildID(ownerAddressBookID)
            if accepted:
                returnValue((yield home.childWithName(ownerHome.shareeAddressBookName())))
            else:
                returnValue((yield cls.objectWithName(home, ownerHome.shareeAddressBookName(), accepted=False)))

        returnValue(None)


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
        names = set([home.addressbook().name()])

        rows = yield cls._acceptedBindForHomeID.on(
            home._txn, homeID=home._resourceID
        )
        for row in rows:
            resourceID = row[2]
            ownerHome = yield home._txn.homeWithResourceID(home._homeType, resourceID, create=True)
            names.add(ownerHome.shareeAddressBookName())

        groupRows = yield AddressBookObject._acceptedBindForHomeID.on(
            home._txn, homeID=home._resourceID
        )
        for groupRow in groupRows:
            resourceID = groupRow[2]
            ownerAddressBookID = yield AddressBookObject.ownerAddressBookIDFromGroupID(home._txn, resourceID)
            ownerHome = yield home._txn.homeWithResourceID(home._homeType, ownerAddressBookID, create=True)
            names.add(ownerHome.shareeAddressBookName())
        returnValue(tuple(names))


    @classmethod
    def _memberIDsWithGroupIDsQuery(cls, groupIDs):
        """
        DAL query to find members and revisions
        """
        aboMembers = schema.ABO_MEMBERS
        return Select([aboMembers.MEMBER_ID, aboMembers.REMOVED, aboMembers.REVISION],
                      From=aboMembers,
                      Where=aboMembers.GROUP_ID.In(Parameter("groupIDs", len(groupIDs))),
                     )


    @classmethod
    def _memberIDsWithGroupIDsAndRevisionQuery(cls, groupIDs):
        """
        DAL query to find members and revisions
        """
        aboMembers = schema.ABO_MEMBERS
        return Select([aboMembers.MEMBER_ID, aboMembers.REMOVED, aboMembers.REVISION],
                      From=aboMembers,
                      Where=aboMembers.GROUP_ID.In(Parameter("groupIDs", len(groupIDs)))
                            .And(aboMembers.REVISION <= Parameter("revision")),
                     )


    @classmethod
    def _currentMemberIDsFromMemberIDRemovedRevisionRows(cls, memberRows):
        memberIDs = set()
        objectIDToVersionToRemovedMap = {}
        for id, removed, version in memberRows:
            versionRemovedRow = objectIDToVersionToRemovedMap.get(id, [])
            versionRemovedRow.append((version, removed,))
            objectIDToVersionToRemovedMap[id] = versionRemovedRow

        for id, versionRemovedRows in objectIDToVersionToRemovedMap.iteritems():
            versionToRemovedMap = dict(versionRemovedRows)
            if not versionToRemovedMap[max(versionToRemovedMap.keys())]:
                memberIDs.add(id)

        return memberIDs


    @classmethod
    @inlineCallbacks
    def memberIDsWithGroupIDs(cls, txn, groupIDs, atRevision=0):

        if atRevision == 0:
            memberRows = yield cls._memberIDsWithGroupIDsQuery(groupIDs).on(
                txn, groupIDs=groupIDs
            )
        else:
            memberRows = yield cls._memberIDsWithGroupIDsAndRevisionQuery(groupIDs).on(
                txn, groupIDs=groupIDs, revision=atRevision
            )

        memberIDs = cls._currentMemberIDsFromMemberIDRemovedRevisionRows(memberRows)
        returnValue(memberIDs)


    @classmethod
    @inlineCallbacks
    def expandGroupIDs(cls, txn, groupIDs, atRevision=0, includeGroupIDs=True):
        """
        Get all AddressBookObject resource IDs contains in the given shared groups with the given groupIDs
        """
        objectIDs = set(groupIDs) if includeGroupIDs else set()
        examinedIDs = set()
        remainingIDs = set(groupIDs)
        while remainingIDs:

            memberIDs = yield cls.memberIDsWithGroupIDs(txn, remainingIDs, atRevision)

            objectIDs |= memberIDs
            examinedIDs |= remainingIDs
            remainingIDs = objectIDs - examinedIDs

        returnValue(objectIDs)


    @inlineCallbacks
    def unacceptedGroupIDs(self):
        if self.owned():
            returnValue([])
        else:
            groupBindRows = yield AddressBookObject._unacceptedBindForHomeIDAndAddressBookID.on(
                    self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
            )
            returnValue([groupBindRow[2] for groupBindRow in groupBindRows])


    @inlineCallbacks
    def acceptedGroupIDs(self):
        if self.owned():
            returnValue([])
        else:
            groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                    self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
            )
            returnValue([groupBindRow[2] for groupBindRow in groupBindRows])


    @inlineCallbacks
    def _groupIDAccessSets(self):
        if self.owned():
            returnValue((set(), set()))
        else:
            groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                    self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
            )
            readWriteGroupIDs = set()
            readOnlyGroupIDs = set()
            for groupBindRow in groupBindRows:
                bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage = groupBindRow[:AddressBookObject.bindColumnCount] #@UnusedVariable
                if bindMode == _BIND_MODE_WRITE:
                    readWriteGroupIDs.add(resourceID)
                else:
                    readOnlyGroupIDs.add(resourceID)

            if readOnlyGroupIDs and readWriteGroupIDs:
                # expand read-write groups and remove any subgroups from read-only group list
                allWriteableIDs = yield self.expandGroupIDs(self._txn, readWriteGroupIDs)
                adjustedReadOnlyGroupIDs = set(readOnlyGroupIDs) - set(allWriteableIDs)
                adjustedReadWriteGroupIDs = set(readWriteGroupIDs) | (set(readOnlyGroupIDs) - adjustedReadOnlyGroupIDs)
            else:
                adjustedReadOnlyGroupIDs = readOnlyGroupIDs
                adjustedReadWriteGroupIDs = readWriteGroupIDs
            returnValue((adjustedReadOnlyGroupIDs, adjustedReadWriteGroupIDs))


    # FIXME: Unused
    @inlineCallbacks
    def readOnlyGroupIDs(self):
        returnValue((yield self._groupIDAccessSets())[0])


    @inlineCallbacks
    def readWriteGroupIDs(self):
        returnValue((yield self._groupIDAccessSets())[1])

    '''
    # FIXME: Unused:  Use for caching access
    @inlineCallbacks
    def accessControlObjectIDs(self):
        readOnlyIDs = set()
        readWriteIDs = set()
        if self.owned() or self.fullyShared():
            rows = yield self._allColumnsWithParent(self)
            ids = set([row[1] for row in rows])
            if self.fullyShared():
                ids.add(self._resourceID)
            if self.owned() or self._bindMode == _BIND_MODE_WRITE:
                returnValue(tuple(readOnlyIDs), tuple(readWriteIDs))
            readOnlyIDs = set(ids)

        groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                self._txn, homeID=self._home._resourceID, addressbookID=self._resourceID
        )
        readWriteGroupIDs = []
        readOnlyGroupIDs = []
        for groupBindRow in groupBindRows:
            bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage = groupBindRow[:AddressBookObject.bindColumnCount] #@UnusedVariable
            if bindMode == _BIND_MODE_WRITE:
                readWriteGroupIDs.append(resourceID)
            else:
                readOnlyGroupIDs.append(resourceID)

        if readOnlyGroupIDs:
            readOnlyIDs |= set((yield self.expandGroupIDs(self._txn, readOnlyGroupIDs)))
        if readWriteGroupIDs:
            readWriteIDs |= set((yield self.expandGroupIDs(self._txn, readWriteGroupIDs)))
        readOnlyIDs -= readWriteIDs
        returnValue(tuple(readOnlyIDs), tuple(readWriteIDs))


    # FIXME: Unused:  Use for caching access
    @inlineCallbacks
    def readOnlyGroupIDs(self):
        returnValue((yield self.accessControlObjectIDs())[1])


    # FIXME: Unused:  Use for caching access
    @inlineCallbacks
    def readWriteGroupIDs(self):
        returnValue((yield self.accessControlObjectIDs())[1])


    # FIXME: Unused:  Use for caching access
    @inlineCallbacks
    def allObjectIDs(self):
        readOnlyIDs, readWriteIDs = yield self.accessControlObjectIDs()
        returnValue((readOnlyIDs + readWriteIDs))
    '''

    @inlineCallbacks
    def updateShare(self, shareeView, mode=None, status=None, message=None):
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

        @return: the name of the shared item in the sharee's home.
        @rtype: a L{Deferred} which fires with a L{str}
        """
        # TODO: raise a nice exception if shareeView is not, in fact, a shared
        # version of this same L{CommonHomeChild}

        # remove None parameters, and substitute None for empty string
        bind = self._bindSchema
        columnMap = {}
        if mode != None and mode != shareeView._bindMode:
            columnMap[bind.BIND_MODE] = mode
        if status != None and status != shareeView._bindStatus:
            columnMap[bind.BIND_STATUS] = status
        if message != None and message != shareeView._bindMessage:
            columnMap[bind.MESSAGE] = message

        if columnMap:

            # count accepted
            if bind.BIND_STATUS in columnMap:
                previouslyAcceptedBindCount = 1 if shareeView.fullyShared() else 0
                groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                        self._txn, homeID=shareeView.viewerHome()._resourceID, addressbookID=shareeView._resourceID
                )
                previouslyAcceptedBindCount += len(groupBindRows)

            bindNameRows = yield self._updateBindColumnsQuery(columnMap).on(
                self._txn,
                resourceID=self._resourceID,
                homeID=shareeView.viewerHome()._resourceID
            )

            # update affected attributes
            if bind.BIND_MODE in columnMap:
                shareeView._bindMode = columnMap[bind.BIND_MODE]

            if bind.BIND_STATUS in columnMap:
                shareeView._bindStatus = columnMap[bind.BIND_STATUS]
                if shareeView._bindStatus == _BIND_STATUS_ACCEPTED:
                    if 0 == previouslyAcceptedBindCount:
                        yield shareeView._initSyncToken()
                        shareeView.viewerHome()._children[self.shareeName()] = shareeView
                        shareeView.viewerHome()._children[shareeView._resourceID] = shareeView
                    yield shareeView._initBindRevision()
                elif shareeView._bindStatus == _BIND_STATUS_DECLINED:
                    if 1 == previouslyAcceptedBindCount:
                        yield shareeView._deletedSyncToken(sharedRemoval=True)
                        shareeView.viewerHome()._children.pop(self.shareeName(), None)
                        shareeView.viewerHome()._children.pop(shareeView._resourceID, None)
                    else:
                        #update revision in all remaining bind table rows for this address book
                        yield shareeView.notifyPropertyChanged()
                        for groupBindRow in groupBindRows:
                            groupObject = yield shareeView.objectResourceWithID(groupBindRow[2])
                            yield groupObject._initBindRevision()
                        shareeView._objects = {}
                        shareeView._objectNames = None

            if bind.MESSAGE in columnMap:
                shareeView._bindMessage = columnMap[bind.MESSAGE]

            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeView.viewerHome()._resourceID, self.shareeName())
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)

            shareeView._name = bindNameRows[0][0]

            # Must send notification to ensure cache invalidation occurs
            yield self.notifyPropertyChanged()
            yield shareeView.viewerHome().notifyChanged()

        returnValue(shareeView._name)


    @inlineCallbacks
    def shareWith(self, shareeHome, mode, status=None, message=None):
        """
            call super and set self.isShared True
        """

        # Note: super always calls shareView._initSyncToken():
        # even when shareView is previously shared via group sharing
        bindName = yield super(AddressBook, self).shareWith(shareeHome, mode, status, message)

        queryCacher = self._txn._queryCacher
        if queryCacher:
            cacheKey = queryCacher.keyForObjectWithName(shareeHome._resourceID, self.shareeName())
            queryCacher.invalidateAfterCommit(self._txn, cacheKey)

        self.setShared(True)
        returnValue(bindName)


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
        shareeAddressBook = yield shareeHome.addressbookWithName(self.shareeName())
        if shareeAddressBook:

            acceptedBindCount = 1 if shareeAddressBook.fullyShared() else 0
            groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                    self._txn, homeID=shareeHome._resourceID, addressbookID=shareeAddressBook._resourceID
            )
            acceptedBindCount += len(groupBindRows)
            if acceptedBindCount == 1:
                yield shareeAddressBook._deletedSyncToken(sharedRemoval=True)
                shareeHome._children.pop(self.shareeName(), None)
                shareeHome._children.pop(shareeAddressBook._resourceID, None)
            else:
                yield shareeAddressBook.notifyPropertyChanged()
                #update revision in all remaining bind table rows for this address book
                for groupBindRow in groupBindRows:
                    groupObject = yield shareeAddressBook.objectResourceWithID(groupBindRow[2])
                    yield groupObject._initBindRevision()
                shareeAddressBook._objects = {}
                shareeAddressBook._objectNames = None

            # Must send notification to ensure cache invalidation occurs
            yield self.notifyPropertyChanged()
            yield shareeHome.notifyChanged()

        # delete bind table rows for this share
        deletedBindNameRows = yield self._deleteBindForResourceIDAndHomeID.on(self._txn, resourceID=self._resourceID,
             homeID=shareeHome._resourceID
        )
        if deletedBindNameRows:
            deletedBindName = deletedBindNameRows[0][0]
            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeHome._resourceID, self.shareeName())
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)
        else:
            deletedBindName = None

        self._initIsShared()
        returnValue(deletedBindName)



class AddressBookObject(CommonObjectResource, AddressBookSharingMixIn):

    implements(IAddressBookObject)

    _homeSchema = schema.ADDRESSBOOK_HOME
    _objectSchema = schema.ADDRESSBOOK_OBJECT
    _bindSchema = schema.SHARED_GROUP_BIND

    # used by CommonHomeChild._childrenAndMetadataForHomeID() only
    # _homeChildSchema = schema.ADDRESSBOOK_OBJECT
    # _homeChildMetaDataSchema = schema.ADDRESSBOOK_OBJECT


    def __init__(self, addressbook, name, uid, resourceID=None, options=None):

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
        self._bindRevision = None
        super(AddressBookObject, self).__init__(addressbook, name, uid, resourceID, options)
        self._options = {} if options is None else options


    def __repr__(self):
        return '<%s: %s("%s")>' % (self.__class__.__name__, self._resourceID, self.name())


    @property
    def _addressbook(self):
        return self._parentCollection


    def addressbook(self):
        return self._addressbook


    def kind(self):
        return self._kind


    def isGroupForSharedAddressBook(self):
        return self._resourceID == self.addressbook()._resourceID


    @inlineCallbacks
    def remove(self):

        if self.owned():
            yield self.unshare() # storebridge should already have done this
        else:
            # handled in storebridge as unshare, should not be here.  assert instead?
            if self.isGroupForSharedAddressBook() or self.shareUID():
                raise HTTPError(FORBIDDEN)

        partiallyShared = not self.owned() and not self.addressbook().fullyShared()
        if partiallyShared:
            readWriteGroupIDs = yield self.addressbook().readWriteGroupIDs()
            readWriteObjectIDs = (
                set((yield self.addressbook().expandGroupIDs(self._txn, readWriteGroupIDs)))
                    if readWriteGroupIDs else set()
            )
            # can't delete item in read-only shared group, even if user has addressbook unbind
            if self._resourceID not in readWriteObjectIDs:
                raise HTTPError(FORBIDDEN)

        # get sync token for delete now
        yield self.addressbook()._deleteRevision(self.name(), self._resourceID)

        # get groups where this object was once a member and version info
        aboMembers = schema.ABO_MEMBERS
        groupRows = yield Select([aboMembers.GROUP_ID, aboMembers.MEMBER_ID, aboMembers.REMOVED, aboMembers.REVISION],
            From=aboMembers,
            Where=aboMembers.MEMBER_ID == self._resourceID,
        ).on(self._txn)

        # combine by groupID
        groupIDToMemberRowMap = {}
        for groupID, id, removed, revision in groupRows:
            memberRow = groupIDToMemberRowMap.get(groupID, [])
            memberRow.append((id, removed, revision))
            groupIDToMemberRowMap[groupID] = memberRow

        # see if this object is in current version
        groupIDs = set([
            groupID for groupID, memberRows in groupIDToMemberRowMap.iteritems()
                if self._resourceID in AddressBook._currentMemberIDsFromMemberIDRemovedRevisionRows(memberRows)
        ])

        if partiallyShared:
            groupIDsToRemoveFrom = groupIDs & readWriteObjectIDs
            groupIDs -= readWriteObjectIDs

            # add to member table rows marked removed
            for groupIDToRemoveFrom in groupIDsToRemoveFrom:
                yield self._insertMemberIDQuery.on(self._txn,
                    groupID=groupIDToRemoveFrom,
                    addressbookID=self._ownerAddressBookResourceID,
                    memberID=self._resourceID,
                    revision=self._syncTokenRevision,
                    removed=True,
                )
                groupObject = yield self.addressbook().objectResourceWithID(groupIDToRemoveFrom)
                yield self.addressbook()._updateRevision(groupObject.name())

        else:
            yield Delete(
                aboMembers,
                Where=aboMembers.MEMBER_ID == self._resourceID,
            ).on(self._txn)

        # add to foreign member table row by member address (aboForeignMembers on address books)
        memberAddress = "urn:uuid:" + self._uid
        aboForeignMembers = schema.ABO_FOREIGN_MEMBERS
        for groupID in groupIDs:
            yield Insert(
                {aboForeignMembers.GROUP_ID: groupID,
                 aboForeignMembers.ADDRESSBOOK_ID: self._ownerAddressBookResourceID,
                 aboForeignMembers.MEMBER_ADDRESS: memberAddress, }
            ).on(self._txn)

        if self.kind() == _ABO_KIND_GROUP:
            if partiallyShared:
                # mark members as deleted
                memberIDsToRemove = yield AddressBook.memberIDsWithGroupIDs(self._txn, [self._resourceID])
                for memberIDToRemove in memberIDsToRemove:
                    yield self._insertMemberIDQuery.on(
                        self._txn,
                        groupID=self._resourceID,
                        addressbookID=self._ownerAddressBookResourceID,
                        memberID=memberIDToRemove,
                        revision=self._syncTokenRevision,
                        removed=True,
                    )
            else:
                yield Delete(
                    aboMembers,
                    Where=aboMembers.GROUP_ID == self._resourceID,
                ).on(self._txn)

        yield super(AddressBookObject, self).remove()
        self._kind = None
        self._ownerAddressBookResourceID = None
        self._component = None


    @inlineCallbacks
    def readWriteAccess(self):
        assert not self.owned(), "Don't call items in owned address book"
        yield None

        #shared address book group is always read-only
        if self.isGroupForSharedAddressBook():
            returnValue(False)

        # if fully shared and rw, must be RW since sharing group read-only has no affect
        if self.addressbook().fullyShared() and self.addressbook().shareMode() == _BIND_MODE_WRITE:
            returnValue(True)

        #otherwise, must be in a read-write group
        readWriteGroupIDs = yield self.addressbook().readWriteGroupIDs()
        readWriteObjectIDs = yield self.addressbook().expandGroupIDs(self._txn, readWriteGroupIDs)
        returnValue(self._resourceID in readWriteObjectIDs)


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
    def _allColumnsWithResourceIDsAndName(cls, resourceIDs):
        return cls._allColumnsWithResourceIDsAnd(resourceIDs, cls._objectSchema.RESOURCE_NAME, "name")


    @classmethod
    def _allColumnsWithResourceIDsAndUID(cls, resourceIDs):
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
        if self.owned() or self.addressbook().fullyShared():  # owned or fully shared
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

            if not rows and self.addressbook().fullyShared():  # perhaps add special group
                if self._name:
                    if self._name == self.addressbook()._groupForSharedAddressBookName():
                        rows = [self.addressbook()._groupForSharedAddressBookRow()]
                elif self._uid:
                    if self._uid == (yield self.addressbook()._groupForSharedAddressBookUID()):
                        rows = [self.addressbook()._groupForSharedAddressBookRow()]
                elif self._resourceID:
                    if self.isGroupForSharedAddressBook():
                        rows = [self.addressbook()._groupForSharedAddressBookRow()]
        else:
            acceptedGroupIDs = yield self.addressbook().acceptedGroupIDs()
            allowedObjectIDs = yield self.addressbook().expandGroupIDs(self._txn, acceptedGroupIDs)
            if self._name:
                if allowedObjectIDs:
                    rows = (yield self._allColumnsWithResourceIDsAndName(allowedObjectIDs).on(
                        self._txn, name=self._name,
                        resourceIDs=allowedObjectIDs,
                    ))
            elif self._uid:
                if allowedObjectIDs:
                    rows = (yield self._allColumnsWithResourceIDsAndUID(allowedObjectIDs).on(
                        self._txn, uid=self._uid,
                        resourceIDs=allowedObjectIDs,
                    ))
            elif self._resourceID:
                if (self._resourceID in allowedObjectIDs or
                        self._resourceID in (yield self.addressbook().unacceptedGroupIDs())): # allow invited groups
                    rows = (yield self._allColumnsWithResourceID.on(
                        self._txn, resourceID=self._resourceID,
                    ))

        if rows:
            self._initFromRow(tuple(rows[0]))

            if self._kind == _ABO_KIND_GROUP:

                groupBindRows = yield AddressBookObject._bindForResourceIDAndHomeID.on(
                    self._txn, resourceID=self._resourceID, homeID=self._home._resourceID
                )

                if groupBindRows:
                    groupBindRow = groupBindRows[0]
                    bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage = groupBindRow[:AddressBookObject.bindColumnCount] #@UnusedVariable
                    self._bindMode = bindMode
                    self._bindStatus = bindStatus
                    self._bindMessage = bindMessage
                    self._bindName = bindName
                    self._bindRevision = bindRevision

                yield self._initIsShared()

            yield self._loadPropertyStore()

            returnValue(self)
        else:
            returnValue(None)


    @classproperty
    def _allColumns(cls): #@NoSelf
        """
        Full set of columns in the object table that need to be loaded to
        initialize the object resource state.
        """
        obj = cls._objectSchema
        return [
            obj.ADDRESSBOOK_HOME_RESOURCE_ID,
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
    def _columnsWithResourceIDsQuery(cls, columns, resourceIDs):
        """
        DAL statement to retrieve addressbook object rows with given columns.
        """
        obj = cls._objectSchema
        return Select(columns, From=obj,
                      Where=obj.RESOURCE_ID.In(Parameter("resourceIDs", len(resourceIDs))),)


    @classmethod
    @inlineCallbacks
    def _allColumnsWithParent(cls, addressbook):
        if addressbook.owned() or addressbook.fullyShared():
            rows = yield super(AddressBookObject, cls)._allColumnsWithParent(addressbook)
            if addressbook.fullyShared():
                rows.append(addressbook._groupForSharedAddressBookRow())
        else:
            acceptedGroupIDs = yield addressbook.acceptedGroupIDs()
            allowedObjectIDs = yield addressbook.expandGroupIDs(addressbook._txn, acceptedGroupIDs)
            rows = yield cls._columnsWithResourceIDsQuery(cls._allColumns, allowedObjectIDs).on(
                addressbook._txn, resourceIDs=allowedObjectIDs
            )
        returnValue(rows)


    @classmethod
    def _allColumnsWithResourceIDsAndNamesQuery(cls, resourceIDs, names):
        obj = cls._objectSchema
        return Select(cls._allColumns, From=obj,
                      Where=(obj.RESOURCE_ID.In(Parameter("resourceIDs", len(resourceIDs))).And(
                          obj.RESOURCE_NAME.In(Parameter("names", len(names))))),)


    @classmethod
    @inlineCallbacks
    def _allColumnsWithParentAndNames(cls, addressbook, names):

        if addressbook.owned() or addressbook.fullyShared():
            rows = yield super(AddressBookObject, cls)._allColumnsWithParentAndNames(addressbook, names)
            if addressbook.fullyShared() and addressbook._groupForSharedAddressBookName() in names:
                rows.append(addressbook._groupForSharedAddressBookRow())
        else:
            acceptedGroupIDs = yield addressbook.acceptedGroupIDs()
            allowedObjectIDs = yield addressbook.expandGroupIDs(addressbook._txn, acceptedGroupIDs)
            rows = yield cls._allColumnsWithResourceIDsAndNamesQuery(allowedObjectIDs, names).on(
                addressbook._txn, resourceIDs=allowedObjectIDs, names=names
            )
        returnValue(rows)


    # Stuff from put_addressbook_common
    def fullValidation(self, component, inserting):
        """
        Do full validation of source and destination calendar data.
        """

        # Basic validation

        # Valid data sizes
        if config.MaxResourceSize:
            if self._componentResourceKindToKind(component) == _ABO_KIND_GROUP:
                thinGroup = deepcopy(component)
                thinGroup.removeProperties("X-ADDRESSBOOKSERVER-MEMBER")
                thinGroup.removeProperties("X-ADDRESSBOOKSERVER-KIND")
                thinGroup.removeProperties("UID")
                vcardsize = len(str(thinGroup))
            else:
                vcardsize = len(str(component))
            if vcardsize > config.MaxResourceSize:
                raise ObjectResourceTooBigError()

        # Valid calendar data checks
        self.validAddressDataCheck(component, inserting)


    def validAddressDataCheck(self, component, inserting): #@UnusedVariable
        """
        Check that the calendar data is valid iCalendar.
        @return:         tuple: (True/False if the calendar data is valid,
                                 log message string).
        """

        # Valid calendar data checks
        if not isinstance(component, VCard):
            raise InvalidObjectResourceError("Wrong type of object: %s" % (type(component),))

        try:
            component.validVCardData()
        except InvalidVCardDataError, e:
            raise InvalidObjectResourceError(str(e))
        try:
            component.validForCardDAV()
        except InvalidVCardDataError, e:
            raise InvalidComponentForStoreError(str(e))


    def _componentResourceKindToKind(self, component):
        componentResourceKindToAddressBookObjectKindMap = {
            "person": _ABO_KIND_PERSON,
            "group": _ABO_KIND_GROUP,
            "resource": _ABO_KIND_RESOURCE,
            "location": _ABO_KIND_LOCATION,
        }
        lcResourceKind = component.resourceKind().lower() if component.resourceKind() else component.resourceKind()
        kind = componentResourceKindToAddressBookObjectKindMap.get(lcResourceKind, _ABO_KIND_PERSON)
        return kind


    @inlineCallbacks
    def _lockUID(self, component, inserting):
        """
        Create a lock on the component's UID and verify, after getting the lock, that the incoming UID
        meets the requirements of the store.
        """
        new_uid = component.resourceUID()
        yield NamedLock.acquire(self._txn, "vCardUIDLock:%s/%s" % (self.ownerHome().uid(), hashlib.md5(new_uid).hexdigest(),))

        # UID conflict check - note we do this after reserving the UID to avoid a race condition where two requests
        # try to write the same address data to two different resource URIs.

        if not inserting:
            # Cannot overwrite a resource with different kind
            if self._kind != self._componentResourceKindToKind(component):
                raise KindChangeNotAllowedError

            # Cannot overwrite a resource with different UID
            if self._uid != new_uid:
                raise InvalidUIDError("Cannot change the UID in an existing resource.")
        else:
            # for partially shared addressbooks, cannot use name that already exists in owner
            if not self.owned() and not self.addressbook().fullyShared():
                nameElsewhere = (yield self.ownerHome().addressbook().addressbookObjectWithName(self.name()))
                if nameElsewhere is not None:
                    raise ObjectResourceNameAlreadyExistsError(self.name() + ' in use by owning addressbook.')

            # New UID must be unique for the owner
            uidElsewhere = (yield self.ownerHome().addressbook().addressbookObjectWithUID(new_uid))
            if uidElsewhere is not None:
                raise UIDExistsError("UID already exists in same addressbook.")


    @inlineCallbacks
    def setComponent(self, component, inserting=False):

        self._componentChanged = False

        if "coaddedUIDs" not in self._options:
            # Handle all validation operations here.
            self.fullValidation(component, inserting)

            # UID lock - this will remain active until the end of the current txn
            yield self._lockUID(component, inserting)

            if inserting:
                yield self.addressbook()._insertRevision(self._name)
            else:
                yield self.addressbook()._updateRevision(self._name)

            yield self.addressbook().notifyChanged()

        yield self.updateDatabase(component, inserting=inserting)

        returnValue(self._componentChanged)


    @classmethod
    def _resourceIDAndUIDForUIDsAndAddressBookResourceIDQuery(cls, uids):
        abo = schema.ADDRESSBOOK_OBJECT
        return Select([abo.RESOURCE_ID, abo.VCARD_UID],
                      From=abo,
                      Where=((abo.ADDRESSBOOK_HOME_RESOURCE_ID == Parameter("addressbookResourceID")
                              ).And(
                                    abo.VCARD_UID.In(Parameter("uids", len(uids))))),
                      )


    @classmethod
    def _deleteMembersWithGroupIDAndMemberIDsQuery(cls, groupID, memberIDs):
        aboMembers = schema.ABO_MEMBERS
        return Delete(
            aboMembers,
            Where=(aboMembers.GROUP_ID == groupID).And(
                    aboMembers.MEMBER_ID.In(Parameter("memberIDs", len(memberIDs)))))


    @classmethod
    def _deleteForeignMembersWithGroupIDAndMembeAddrsQuery(cls, groupID, memberAddrs):
        aboForeignMembers = schema.ABO_FOREIGN_MEMBERS
        return Delete(
            aboForeignMembers,
            Where=(aboForeignMembers.GROUP_ID == groupID).And(
                    aboForeignMembers.MEMBER_ADDRESS.In(Parameter("memberAddrs", len(memberAddrs)))))


    @classproperty
    def _insertABObjectQuery(cls): #@NoSelf
        """
        DAL statement to create an addressbook object with all default values.
        """
        abo = schema.ADDRESSBOOK_OBJECT
        return Insert(
            {abo.RESOURCE_ID: schema.RESOURCE_ID_SEQ,
             abo.ADDRESSBOOK_HOME_RESOURCE_ID: Parameter("addressbookResourceID"),
             abo.RESOURCE_NAME: Parameter("name"),
             abo.VCARD_TEXT: Parameter("text"),
             abo.VCARD_UID: Parameter("uid"),
             abo.KIND: Parameter("kind"),
             abo.MD5: Parameter("md5"),
             },
            Return=(abo.RESOURCE_ID,
                    abo.CREATED,
                    abo.MODIFIED))


    @classproperty
    def _insertMemberIDQuery(cls): #@NoSelf
        """
        DAL statement to add a member table row
        """
        aboMembers = schema.ABO_MEMBERS
        return Insert(
            {aboMembers.GROUP_ID: Parameter("groupID"),
             aboMembers.ADDRESSBOOK_ID: Parameter("addressbookID"),
             aboMembers.MEMBER_ID: Parameter("memberID"),
             aboMembers.REVISION: Parameter("revision"),
             aboMembers.REMOVED: Parameter("removed"),
             }
        )


    @classmethod
    def _deleteMembersIDsThruRevisionQuery(cls, groupIDs, memberIDs):
        """
        DAL statement deletes rows with groupsIDs and memberIDs < revision

        Note: Used after adding a member row in an owned address book, where only the last revision is needed.
            Could be used after adding a member row to a partially shared address book if the
            minimum valid revision is known.
            "minimum valid revision" is the max of the bind revisions on a home over all
            shared address books that have group binds.
        """
        aboMembers = schema.ABO_MEMBERS
        return Delete(
            aboMembers,
            Where=(aboMembers.GROUP_ID.In(Parameter("groupIDs", len(groupIDs)))).And(
                aboMembers.GROUP_ID.In(Parameter("memberIDs", len(memberIDs)))).And(
                    aboMembers.REVISION < Parameter("revision")
                )
        )


    @inlineCallbacks
    def updateDatabase(self, component, expand_until=None, reCreate=False, #@UnusedVariable
                       inserting=False):
        """
        Update the database tables for the new data being written.

        @param component: addressbook data to store
        @type component: L{Component}
        """

        if inserting:
            self._kind = self._componentResourceKindToKind(component)

        # For shared groups:  Non owner may NOT add group members not currently in group!
        # (Or it would be possible to troll for unshared vCard UIDs and make them shared.)
        if not self._ownerAddressBookResourceID:
            self._ownerAddressBookResourceID = self.ownerHome().addressbook()._resourceID

        uid = component.resourceUID()
        assert inserting or self._uid == uid  # can't change UID. Should be checked in upper layers
        self._uid = uid
        originalComponentText = str(component)

        if self._kind == _ABO_KIND_GROUP:
            memberAddresses = set(component.resourceMemberAddresses())

            # get member ids
            memberUIDs = []
            foreignMemberAddrs = []
            for memberAddr in memberAddresses:
                if len(memberAddr) > len("urn:uuid:") and memberAddr.startswith("urn:uuid:"):
                    memberUIDs.append(memberAddr[len("urn:uuid:"):])
                else:
                    foreignMemberAddrs.append(memberAddr)

            memberRows = yield self._resourceIDAndUIDForUIDsAndAddressBookResourceIDQuery(memberUIDs).on(
                self._txn, addressbookResourceID=self._ownerAddressBookResourceID, uids=memberUIDs
            ) if memberUIDs else []
            memberIDs = [memberRow[0] for memberRow in memberRows]
            foundUIDs = [memberRow[1] for memberRow in memberRows]
            foundUIDs.append(self._uid) # circular self reference is OK
            missingUIDs = set(memberUIDs) - set(foundUIDs)

            if not self.owned() and not self.addressbook().fullyShared():
                # in partially shared addressbook, all members UIDs must be inside the shared groups
                # except during bulk operations, when other UIDs added are OK
                coaddedUIDs = self._options.get("coaddedUIDs", set())
                if missingUIDs - coaddedUIDs:
                    raise GroupWithUnsharedAddressNotAllowedError(missingUIDs)

                # see if user has access all the members
                acceptedGroupIDs = yield self.addressbook().acceptedGroupIDs()
                allowedObjectIDs = yield self.addressbook().expandGroupIDs(self._txn, acceptedGroupIDs)
                if set(memberIDs) - set(allowedObjectIDs):
                    raise HTTPError(FORBIDDEN) # could give more info here, and use special exception

            # missing uids and other cuaddrs e.g. user@example.com, are stored in same schema table
            foreignMemberAddrs.extend(["urn:uuid:" + missingUID for missingUID in missingUIDs])

            # sort unique members
            component.removeProperties("X-ADDRESSBOOKSERVER-MEMBER")
            for memberAddress in sorted(memberAddresses):
                component.addProperty(Property("X-ADDRESSBOOKSERVER-MEMBER", memberAddress))
            componentText = str(component)

            # remove unneeded fields to get stored _objectText
            thinGroup = deepcopy(component)
            thinGroup.removeProperties("X-ADDRESSBOOKSERVER-MEMBER")
            thinGroup.removeProperties("X-ADDRESSBOOKSERVER-KIND")
            thinGroup.removeProperties("UID")
            self._objectText = str(thinGroup)
        else:
            componentText = str(component)
            self._objectText = componentText

        self._size = len(self._objectText)
        self._component = component
        self._md5 = hashlib.md5(componentText).hexdigest()
        self._componentChanged = originalComponentText != componentText

        # Special - if migrating we need to preserve the original md5
        if self._txn._migrating and hasattr(component, "md5"):
            self._md5 = component.md5

        abo = schema.ADDRESSBOOK_OBJECT
        aboForeignMembers = schema.ABO_FOREIGN_MEMBERS
        partiallyShared = not self.owned() and not self.addressbook().fullyShared()

        if inserting:
            self._resourceID, self._created, self._modified = (
                yield self._insertABObjectQuery.on(
                    self._txn,
                    addressbookResourceID=self._ownerAddressBookResourceID,
                    name=self._name,
                    text=self._objectText,
                    uid=self._uid,
                    md5=self._md5,
                    kind=self._kind,
                )
            )[0]

            # delete foreign members table rows for this object
            groupIDRows = yield Delete(
                aboForeignMembers,
                Where=aboForeignMembers.MEMBER_ADDRESS == "urn:uuid:" + self._uid,
                Return=aboForeignMembers.GROUP_ID
            ).on(self._txn)
            groupIDs = set([groupIDRow[0] for groupIDRow in groupIDRows])

            # add this object to shared groups
            if partiallyShared:
                readWriteGroupIDs = yield self.addressbook().readWriteGroupIDs()
                assert readWriteGroupIDs, "no access"
                groupIDs |= readWriteGroupIDs

                for readWriteGroupID in readWriteGroupIDs:
                    groupObject = yield self.addressbook().objectResourceWithID(readWriteGroupID)
                    yield self.addressbook()._updateRevision(groupObject.name())

            # add to member table rows
            for groupID in groupIDs:
                yield self._insertMemberIDQuery.on(self._txn,
                    groupID=groupID,
                    addressbookID=self._ownerAddressBookResourceID,
                    memberID=self._resourceID,
                    revision=self._syncTokenRevision,
                    removed=False,
                )

            # clean old revisions
            if groupIDs and not partiallyShared:
                yield self._deleteMembersIDsThruRevisionQuery(groupIDs, [self._resourceID]).on(
                    self._txn, groupIDs=groupIDs, memberIDs=[self._resourceID], revision=self._syncTokenRevision)

        else:
            self._modified = (yield Update(
                {abo.VCARD_TEXT: self._objectText,
                 abo.MD5: self._md5,
                 abo.MODIFIED: utcNowSQL},
                Where=abo.RESOURCE_ID == self._resourceID,
                Return=abo.MODIFIED).on(self._txn))[0][0]

        if self._kind == _ABO_KIND_GROUP:

            # allow circular group
            if inserting and "urn:uuid:" + self._uid in memberAddresses:
                memberIDs.append(self._resourceID)

            # get current members
            currentMemberIDs = yield AddressBook.memberIDsWithGroupIDs(self._txn, [self._resourceID])
            memberIDsToRemove = set(currentMemberIDs) - set(memberIDs)
            memberIDsToAdd = set(memberIDs) - set(currentMemberIDs)

            for memberID in memberIDsToAdd:
                yield self._insertMemberIDQuery.on(
                    self._txn,
                    groupID=self._resourceID,
                    addressbookID=self._ownerAddressBookResourceID,
                    memberID=memberID,
                    revision=self._syncTokenRevision,
                    removed=False,
                )

            if partiallyShared:
                for memberID in memberIDsToRemove:
                    yield self._insertMemberIDQuery.on(
                        self._txn,
                        groupID=self._resourceID,
                        addressbookID=self._ownerAddressBookResourceID,
                        memberID=memberID,
                        revision=self._syncTokenRevision,
                        removed=True,
                    )
            else:
                # clean old revisions
                if memberIDsToAdd:
                    yield self._deleteMembersIDsThruRevisionQuery([self._resourceID], memberIDsToAdd).on(
                        self._txn, groupIDs=[self._resourceID], memberIDs=memberIDsToAdd, revision=self._syncTokenRevision)

                if memberIDsToRemove:
                    yield self._deleteMembersWithGroupIDAndMemberIDsQuery(self._resourceID, memberIDsToRemove).on(
                        self._txn, memberIDs=memberIDsToRemove
                    )

            # get current foreign members
            currentForeignMemberRows = yield Select(
                [aboForeignMembers.MEMBER_ADDRESS],
                 From=aboForeignMembers,
                 Where=aboForeignMembers.GROUP_ID == self._resourceID,
            ).on(self._txn)
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

            if self.isGroupForSharedAddressBook():
                component = yield self.addressbook()._groupForSharedAddressBookComponent()
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
                    self.log.error("Address data id=%s had unfixable problems:\n  %s" % (self._resourceID, "\n  ".join(unfixed),))

                if fixed:
                    self.log.error("Address data id=%s had fixable problems:\n  %s" % (self._resourceID, "\n  ".join(fixed),))

                if self._kind == _ABO_KIND_GROUP:
                    assert not component.hasProperty("X-ADDRESSBOOKSERVER-MEMBER"), "database group vCard text contains members %s" % (component,)

                    # generate "X-ADDRESSBOOKSERVER-MEMBER" properties
                    # first get member resource ids
                    memberIDs = yield AddressBook.memberIDsWithGroupIDs(self._txn, [self._resourceID])

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
                    component.addProperty(Property("X-ADDRESSBOOKSERVER-KIND", "group"))
                    component.addProperty(Property("UID", self._uid))

            self._component = component

        returnValue(self._component)


    def moveValidation(self, destination, name):
        """
        Validate whether a move to the specified collection is allowed.

        @param destination: destination address book collection
        @type destination: L{AddressBookCollection}
        @param name: name of new resource
        @type name: C{str}
        """
        pass


    # IDataStoreObject
    def contentType(self):
        """
        The content type of Addressbook objects is text/vcard.
        """
        return MimeType.fromString("text/vcard; charset=utf-8")


    def owned(self):
        return self.addressbook().owned()


    def ownerHome(self):
        return self.addressbook().ownerHome()


    def viewerHome(self):
        return self.addressbook().viewerHome()


    def shareUID(self):
        """
        @see: L{ICalendar.shareUID}
        """
        return self._bindName


    @classmethod
    def metadataColumns(cls):
        """
        Return a list of column name for retrieval of metadata. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """
        # Common behavior is to have created and modified
        return (
            cls._objectSchema.CREATED,
            cls._objectSchema.MODIFIED,
        )


    # same as CommonHomeChild._childrenAndMetadataForHomeID() w/o metadata join
    @classproperty
    def _childrenAndMetadataForHomeID(cls): #@NoSelf
        bind = cls._bindSchema
        child = cls._objectSchema
        columns = cls.bindColumns() + cls.additionalBindColumns() + cls.metadataColumns()
        return Select(columns,
                     From=child.join(
                         bind, child.RESOURCE_ID == bind.RESOURCE_ID,
                         'left outer'),
                     Where=(bind.HOME_RESOURCE_ID == Parameter("homeID")
                           ).And(bind.BIND_STATUS == _BIND_STATUS_ACCEPTED))


    @classproperty
    def _addressbookIDForResourceID(cls): #@NoSelf
        obj = cls._objectSchema
        return Select([obj.PARENT_RESOURCE_ID],
                      From=obj,
                      Where=obj.RESOURCE_ID == Parameter("resourceID")
                    )


    @classmethod
    @inlineCallbacks
    def ownerAddressBookIDFromGroupID(cls, txn, resourceID):
        ownerAddressBookIDRows = yield cls._addressbookIDForResourceID.on(txn, resourceID=resourceID)
        returnValue(ownerAddressBookIDRows[0][0])


    @inlineCallbacks
    def sharingInvites(self):
        """
        Retrieve the list of all L{SharingInvitation} for this L{CommonHomeChild}, irrespective of mode.

        @return: L{SharingInvitation} objects
        @rtype: a L{Deferred} which fires with a L{list} of L{SharingInvitation}s.
        """
        if not self.owned():
            returnValue([])

        # get all accepted binds
        acceptedRows = yield self._sharedInvitationBindForResourceID.on(
            self._txn, resourceID=self._resourceID, homeID=self.addressbook()._home._resourceID
        )

        result = []
        for homeUID, homeRID, resourceID, resourceName, bindMode, bindStatus, bindMessage in acceptedRows: #@UnusedVariable
            invite = SharingInvitation(
                resourceName,
                self.addressbook()._home.name(),
                self.addressbook()._home._resourceID,
                homeUID,
                homeRID,
                resourceID,
                self.addressbook().shareeName(),
                bindMode,
                bindStatus,
                bindMessage,
            )
            result.append(invite)
        returnValue(result)


    @inlineCallbacks
    def unshare(self):
        """
        Unshares a group, regardless of which "direction" it was shared.
        """
        if self._kind == _ABO_KIND_GROUP:
            if self.owned():
                # This collection may be shared to others
                invites = yield self.sharingInvites()
                for invite in invites:
                    shareeHome = (yield self._txn.homeWithResourceID(self.addressbook()._home._homeType, invite.shareeHomeID()))
                    (yield self.unshareWith(shareeHome))
            else:
                # This collection is shared to me
                ownerAddressBook = self.addressbook().ownerHome().addressbook()
                ownerGroup = yield ownerAddressBook.objectResourceWithID(self._resourceID)
                if ownerGroup:
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
        shareeAddressBook = yield shareeHome.addressbookWithName(self.addressbook().shareeName())

        if shareeAddressBook and shareeAddressBook._bindStatus == _BIND_STATUS_ACCEPTED:

            acceptedBindCount = 1 if shareeAddressBook.fullyShared() else 0
            groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                self._txn, homeID=shareeHome._resourceID, addressbookID=shareeAddressBook._resourceID
            )
            acceptedBindCount += len(groupBindRows)

            if acceptedBindCount == 1:
                yield shareeAddressBook._deletedSyncToken(sharedRemoval=True)
                shareeHome._children.pop(self.addressbook().shareeName(), None)
                shareeHome._children.pop(self.addressbook()._resourceID, None)
            else:
                shareeAddressBook.notifyPropertyChanged()
                #update revision in all remaining bind table rows for this address book
                for groupBindRow in groupBindRows:
                    groupObject = yield shareeAddressBook.objectResourceWithID(groupBindRow[2])
                    yield groupObject._initBindRevision()
                if shareeAddressBook.fullyShared():
                    yield shareeAddressBook._initBindRevision()
                shareeAddressBook._objects = {}
                shareeAddressBook._objectNames = None

            # Must send notification to ensure cache invalidation occurs
            yield self.addressbook().notifyPropertyChanged()
            yield shareeHome.notifyChanged()

        # delete bind including invites
        deletedBindNameRows = yield self._deleteBindForResourceIDAndHomeID.on(
            self._txn, resourceID=self._resourceID,
             homeID=shareeHome._resourceID
        )
        if deletedBindNameRows:
            deletedBindName = deletedBindNameRows[0][0]
            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeHome._resourceID, self.addressbook().shareeName())
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)
        else:
            deletedBindName = None

        yield self._initIsShared()
        returnValue(deletedBindName)


    @inlineCallbacks
    def shareWith(self, shareeHome, mode, status=None, message=None):
        """
        Share this (owned) L{AddressBookObject} with another home.

        @param shareeHome: The home of the sharee.
        @type shareeHome: L{CommonHome}

        @param mode: The sharing mode; L{_BIND_MODE_READ} or
            L{_BIND_MODE_WRITE} or L{_BIND_MODE_DIRECT}
        @type mode: L{str}

        @param status: The sharing status; L{_BIND_STATUS_INVITED} or
            L{_BIND_STATUS_ACCEPTED}
        @type mode: L{str}

        @param message: The proposed message to go along with the share, which
            will be used as the default display name.
        @type mode: L{str}

        @return: the name of the shared group in the sharee home.
        @rtype: L{str}
        """

        if status is None:
            status = _BIND_STATUS_ACCEPTED

        @inlineCallbacks
        def doInsert(subt):
            newName = str(uuid4())
            yield self._bindInsertQuery.on(
                subt, homeID=shareeHome._resourceID,
                resourceID=self._resourceID, name=newName,
                mode=mode, bindStatus=status, message=message
            )
            returnValue(newName)
        try:
            bindName = yield self._txn.subtransaction(doInsert)
        except AllRetriesFailed:
            # FIXME: catch more specific exception
            groupBindRows = yield self._bindForResourceIDAndHomeID.on(
                self._txn, resourceID=self._resourceID, homeID=shareeHome._resourceID
            )
            groupBindRow = groupBindRows[0]
            bindMode, homeID, resourceID, bindName, bindStatus, bindRevision, bindMessage = groupBindRow[:self.bindColumnCount] #@UnusedVariable
            if bindStatus == _BIND_STATUS_ACCEPTED:
                group = yield shareeHome.objectWithShareUID(bindName)
            else:
                group = yield shareeHome.invitedObjectWithShareUID(bindName)
            bindName = yield self.updateShare(
                group, mode=mode, status=status,
                message=message
            )
        else:
            if status == _BIND_STATUS_ACCEPTED:
                shareeView = yield shareeHome.objectWithShareUID(bindName)
                if not shareeView.addressbook().fullyShared():
                    groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                            self._txn, homeID=shareeView.viewerHome()._resourceID, addressbookID=shareeView.addressbook()._resourceID
                    )
                    if len(groupBindRows) == 1:
                        yield shareeView.addressbook()._initSyncToken()
                yield shareeView._initBindRevision()

        queryCacher = self._txn._queryCacher
        if queryCacher:
            cacheKey = queryCacher.keyForObjectWithName(shareeHome._resourceID, self.addressbook().shareeName())
            queryCacher.invalidateAfterCommit(self._txn, cacheKey)

        # Must send notification to ensure cache invalidation occurs
        yield self.addressbook().notifyPropertyChanged()
        yield shareeHome.notifyChanged()

        self.setShared(True)
        returnValue(bindName)


    @property
    def _syncTokenRevision(self):
        return self.addressbook()._syncTokenRevision


    def syncToken(self):
        return self.addressbook().syncToken() # init self.addressbook()._syncTokenRevision


    @inlineCallbacks
    def updateShare(self, shareeView, mode=None, status=None, message=None):
        """
        Update share mode, status, and message for a address book group with
        this (owned) L{AddressBookObject}.

        @param shareeView: The sharee addressbook group that shares this.
        @type shareeView: L{AddressBookObject}

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

        @return: the name of the shared item in the sharee's home.
        @rtype: a L{Deferred} which fires with a L{str}
        """
        # TODO: raise a nice exception if shareeView is not, in fact, a shared
        # version of this same L{CommonHomeChild}

        # remove None parameters, and substitute None for empty string
        bind = self._bindSchema
        columnMap = {}
        if mode != None and mode != shareeView._bindMode:
            columnMap[bind.BIND_MODE] = mode
        if status != None and status != shareeView._bindStatus:
            columnMap[bind.BIND_STATUS] = status
        if message != None and message != shareeView._bindMessage:
            columnMap[bind.MESSAGE] = message

        if columnMap:

            # count accepted
            if bind.BIND_STATUS in columnMap:
                previouslyAcceptedBindCount = 1 if shareeView.addressbook().fullyShared() else 0
                groupBindRows = yield AddressBookObject._acceptedBindForHomeIDAndAddressBookID.on(
                    self._txn, homeID=shareeView.viewerHome()._resourceID, addressbookID=self.addressbook()._resourceID
                )
                previouslyAcceptedBindCount += len(groupBindRows)

            bindNameRows = yield self._updateBindColumnsQuery(columnMap).on(
                self._txn,
                resourceID=self._resourceID,
                homeID=shareeView.viewerHome()._resourceID
            )

            # update affected attributes
            if bind.BIND_MODE in columnMap:
                shareeView._bindMode = columnMap[bind.BIND_MODE]

            if bind.BIND_STATUS in columnMap:
                shareeView._bindStatus = columnMap[bind.BIND_STATUS]
                if shareeView._bindStatus == _BIND_STATUS_ACCEPTED:
                    if 0 == previouslyAcceptedBindCount:
                        yield shareeView.addressbook()._initSyncToken()
                        shareeView.viewerHome()._children[self.addressbook().shareeName()] = shareeView.addressbook()
                        shareeView.viewerHome()._children[shareeView._resourceID] = shareeView.addressbook()
                    yield shareeView._initBindRevision()
                elif shareeView._bindStatus == _BIND_STATUS_DECLINED:
                    if 1 == previouslyAcceptedBindCount:
                        yield shareeView.addressbook()._deletedSyncToken(sharedRemoval=True)
                        shareeView.viewerHome()._children.pop(self.addressbook().shareeName(), None)
                        shareeView.viewerHome()._children.pop(shareeView._resourceID, None)
                    else:
                        # update revision in all remaining bind table rows for this address book
                        yield shareeView.addressbook().notifyPropertyChanged()
                        for groupBindRow in groupBindRows:
                            if groupBindRow[2] != shareeView._resourceID:
                                groupObject = yield shareeView.addressbook().objectResourceWithID(groupBindRow[2])
                                yield groupObject._initBindRevision()
                        if shareeView.addressbook().fullyShared():
                            yield shareeView.addressbook()._initBindRevision()
                        shareeView.addressbook()._objects = {}
                        shareeView.addressbook()._objectNames = None

            if bind.MESSAGE in columnMap:
                shareeView._bindMessage = columnMap[bind.MESSAGE]

            # safer to just invalidate in all cases rather than calculate when to invalidate
            queryCacher = self._txn._queryCacher
            if queryCacher:
                cacheKey = queryCacher.keyForObjectWithName(shareeView.viewerHome()._resourceID, self.addressbook().shareeName())
                queryCacher.invalidateAfterCommit(self._txn, cacheKey)

            shareeView._name = bindNameRows[0][0]

            # Must send notification to ensure cache invalidation occurs
            yield self.addressbook().notifyPropertyChanged()
            yield shareeView.viewerHome().notifyChanged()

        returnValue(shareeView._name)


    @classproperty
    def _acceptedBindForHomeIDAndAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls.bindColumns() + cls.additionalBindColumns(),
                  From=bind.join(abo),
                  Where=(bind.BIND_STATUS == _BIND_STATUS_ACCEPTED)
                        .And(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(bind.HOME_RESOURCE_ID == Parameter("homeID"))
                        .And(abo.ADDRESSBOOK_HOME_RESOURCE_ID == Parameter("addressbookID"))
        )


    @classproperty
    def _unacceptedBindForHomeIDAndAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls.bindColumns() + cls.additionalBindColumns(),
                  From=bind.join(abo),
                  Where=(bind.BIND_STATUS != _BIND_STATUS_ACCEPTED)
                        .And(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(bind.HOME_RESOURCE_ID == Parameter("homeID"))
                        .And(abo.ADDRESSBOOK_HOME_RESOURCE_ID == Parameter("addressbookID"))
        )


    @classproperty
    def _bindForHomeIDAndAddressBookID(cls): #@NoSelf
        bind = cls._bindSchema
        abo = cls._objectSchema
        return Select(
                  cls.bindColumns() + cls.additionalBindColumns(),
                  From=bind.join(abo),
                  Where=(bind.RESOURCE_ID == abo.RESOURCE_ID)
                        .And(bind.HOME_RESOURCE_ID == Parameter("homeID"))
                        .And(abo.ADDRESSBOOK_HOME_RESOURCE_ID == Parameter("addressbookID"))
        )


AddressBook._objectResourceClass = AddressBookObject
