# -*- test-case-name: txdav.carddav.datastore,txdav.carddav.datastore.test.test_sql.AddressBookSQLStorageTests -*-
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
Tests for common addressbook store API functions.
"""
from twisted.internet.defer import inlineCallbacks, returnValue, maybeDeferred

from txdav.idav import IPropertyStore, IDataStore
from txdav.base.propertystore.base import PropertyName

from txdav.common.icommondatastore import (
    HomeChildNameAlreadyExistsError, ICommonTransaction
)
from txdav.common.icommondatastore import InvalidObjectResourceError
from txdav.common.icommondatastore import NoSuchHomeChildError
from txdav.common.icommondatastore import NoSuchObjectResourceError
from txdav.common.icommondatastore import ObjectResourceNameAlreadyExistsError

from txdav.carddav.iaddressbookstore import (
    IAddressBookObject, IAddressBookHome,
    IAddressBook, IAddressBookTransaction
)

from txdav.common.datastore.test.util import CommonCommonTests

from twistedcaldav.vcard import Component as VComponent

from twext.python.filepath import CachingFilePath as FilePath
from twext.web2.dav import davxml
from twext.web2.dav.element.base import WebDAVUnknownElement


storePath = FilePath(__file__).parent().child("addressbook_store")

homeRoot = storePath.child("ho").child("me").child("home1")

adbk1Root = homeRoot.child("addressbook_1")

addressbook1_objectNames = [
    "1.vcf",
    "2.vcf",
    "3.vcf",
]


home1_addressbookNames = [
    "addressbook_1",
    "addressbook_2",
    "addressbook_empty",
]


vcard4_text = (
    """BEGIN:VCARD
VERSION:3.0
N:Thompson;Default;;;
FN:Default Thompson
EMAIL;type=INTERNET;type=WORK;type=pref:lthompson@example.com
TEL;type=WORK;type=pref:1-555-555-5555
TEL;type=CELL:1-444-444-4444
item1.ADR;type=WORK;type=pref:;;1245 Test;Sesame Street;California;11111;USA
item1.X-ABADR:us
UID:uid4
END:VCARD
""".replace("\n", "\r\n")
)



vcard4notCardDAV_text = (# Missing UID, N and FN
"""BEGIN:VCARD
VERSION:3.0
EMAIL;type=INTERNET;type=WORK;type=pref:lthompson@example.com
TEL;type=WORK;type=pref:1-555-555-5555
TEL;type=CELL:1-444-444-4444
item1.ADR;type=WORK;type=pref:;;1245 Test;Sesame Street;California;11111;USA
item1.X-ABADR:us
END:VCARD
""".replace("\n", "\r\n")
)



vcard1modified_text = vcard4_text.replace(
    "\r\nUID:uid4\r\n",
    "\r\nUID:uid1\r\n"
)


class CommonTests(CommonCommonTests):
    """
    Tests for common functionality of interfaces defined in
    L{txdav.carddav.iaddressbookstore}.
    """

    requirements = {
        "home1": {
            "addressbook_1": {
                "1.vcf": adbk1Root.child("1.vcf").getContent(),
                "2.vcf": adbk1Root.child("2.vcf").getContent(),
                "3.vcf": adbk1Root.child("3.vcf").getContent()
            },
            "addressbook_2": {},
            "addressbook_empty": {},
            "not_a_addressbook": None
        },
        "not_a_home": None
    }

    def storeUnderTest(self):
        """
        Subclasses must override this to return an L{IAddressBookStore}
        provider which adheres to the structure detailed by
        L{CommonTests.requirements}. This attribute is a dict of dict of dicts;
        the outermost layer representing UIDs mapping to addressbook homes,
        then addressbook names mapping to addressbook collections, and finally
        addressbook object names mapping to addressbook object text.
        """
        raise NotImplementedError()


    def homeUnderTest(self):
        """
        Get the addressbook home detailed by C{requirements['home1']}.
        """
        return self.transactionUnderTest().addressbookHomeWithUID("home1")


    @inlineCallbacks
    def addressbookUnderTest(self):
        """
        Get the addressbook detailed by C{requirements['home1']['addressbook_1']}.
        """
        returnValue((yield (yield self.homeUnderTest())
            .addressbookWithName("addressbook_1")))


    @inlineCallbacks
    def addressbookObjectUnderTest(self):
        """
        Get the addressbook detailed by
        C{requirements['home1']['addressbook_1']['1.vcf']}.
        """
        returnValue((yield (yield self.addressbookUnderTest())
                    .addressbookObjectWithName("1.vcf")))


    def test_addressbookStoreProvides(self):
        """
        The addressbook store provides L{IAddressBookStore} and its required
        attributes.
        """
        addressbookStore = self.storeUnderTest()
        self.assertProvides(IDataStore, addressbookStore)


    def test_transactionProvides(self):
        """
        The transactions generated by the addressbook store provide
        L{IAddressBookStoreTransaction} and its required attributes.
        """
        txn = self.transactionUnderTest()
        self.assertProvides(ICommonTransaction, txn)
        self.assertProvides(IAddressBookTransaction, txn)


    @inlineCallbacks
    def test_homeProvides(self):
        """
        The addressbook homes generated by the addressbook store provide
        L{IAddressBookHome} and its required attributes.
        """
        self.assertProvides(IAddressBookHome, (yield self.homeUnderTest()))


    @inlineCallbacks
    def test_addressbookProvides(self):
        """
        The addressbooks generated by the addressbook store provide L{IAddressBook} and
        its required attributes.
        """
        self.assertProvides(IAddressBook, (yield self.addressbookUnderTest()))


    @inlineCallbacks
    def test_addressbookObjectProvides(self):
        """
        The addressbook objects generated by the addressbook store provide
        L{IAddressBookObject} and its required attributes.
        """
        self.assertProvides(IAddressBookObject,
                            (yield self.addressbookObjectUnderTest()))


    @inlineCallbacks
    def test_notifierID(self):
        home = yield self.homeUnderTest()
        self.assertEquals(home.notifierID(), "CardDAV|home1")
        addressbook = yield home.addressbookWithName("addressbook_1")
        self.assertEquals(addressbook.notifierID(), "CardDAV|home1")
        self.assertEquals(addressbook.notifierID(label="collection"), "CardDAV|home1/addressbook_1")


    @inlineCallbacks
    def test_addressbookHomeWithUID_exists(self):
        """
        Finding an existing addressbook home by UID results in an object that
        provides L{IAddressBookHome} and has a C{uid()} method that returns the
        same value that was passed in.
        """
        addressbookHome = (yield self.transactionUnderTest()
                            .addressbookHomeWithUID("home1"))
        self.assertEquals(addressbookHome.uid(), "home1")
        self.assertProvides(IAddressBookHome, addressbookHome)


    @inlineCallbacks
    def test_addressbookHomeWithUID_absent(self):
        """
        L{IAddressBookStoreTransaction.addressbookHomeWithUID} should return C{None}
        when asked for a non-existent addressbook home.
        """
        txn = self.transactionUnderTest()
        self.assertEquals((yield txn.addressbookHomeWithUID("xyzzy")), None)


    @inlineCallbacks
    def test_addressbookWithName_exists(self):
        """
        L{IAddressBookHome.addressbookWithName} returns an L{IAddressBook} provider,
        whose name matches the one passed in.
        """
        home = yield self.homeUnderTest()
        for name in home1_addressbookNames:
            addressbook = yield home.addressbookWithName(name)
            if addressbook is None:
                self.fail("addressbook %r didn't exist" % (name,))
            self.assertProvides(IAddressBook, addressbook)
            self.assertEquals(addressbook.name(), name)


    @inlineCallbacks
    def test_addressbookRename(self):
        """
        L{IAddressBook.rename} changes the name of the L{IAddressBook}.
        """
        home = yield self.homeUnderTest()
        addressbook = yield home.addressbookWithName("addressbook_1")
        yield addressbook.rename("some_other_name")
        @inlineCallbacks
        def positiveAssertions():
            self.assertEquals(addressbook.name(), "some_other_name")
            self.assertEquals(addressbook, (yield home.addressbookWithName("some_other_name")))
            self.assertEquals(None, (yield home.addressbookWithName("addressbook_1")))
        yield positiveAssertions()
        yield self.commit()
        home = yield self.homeUnderTest()
        addressbook = yield home.addressbookWithName("some_other_name")
        positiveAssertions()
        # FIXME: revert
        # FIXME: test for multiple renames
        # FIXME: test for conflicting renames (a->b, c->a in the same txn)


    @inlineCallbacks
    def test_addressbookWithName_absent(self):
        """
        L{IAddressBookHome.addressbookWithName} returns C{None} for addressbooks which
        do not exist.
        """
        self.assertEquals(
            (yield (yield self.homeUnderTest()).addressbookWithName("xyzzy")),
            None)


    @inlineCallbacks
    def test_createAddressBookWithName_absent(self):
        """
        L{IAddressBookHome.createAddressBookWithName} creates a new L{IAddressBook} that
        can be retrieved with L{IAddressBookHome.addressbookWithName}.
        """
        home = yield self.homeUnderTest()
        name = "new"
        self.assertIdentical((yield home.addressbookWithName(name)), None)
        yield home.createAddressBookWithName(name)
        self.assertNotIdentical((yield home.addressbookWithName(name)), None)
        def checkProperties():
            addressbookProperties = (yield home.addressbookWithName(name)).properties()
            addressbookType = davxml.ResourceType.addressbook #@UndefinedVariable
            self.assertEquals(
                addressbookProperties[
                    PropertyName.fromString(davxml.ResourceType.sname())
                ],
                addressbookType
            )
        checkProperties()
        yield self.commit()

        # Make sure notification fired after commit
        self.assertEquals(self.notifierFactory.history,
            [("update", "CardDAV|home1")])

        # Make sure it's available in a new transaction; i.e. test the commit.
        home = yield self.homeUnderTest()
        self.assertNotIdentical((yield home.addressbookWithName(name)), None)

        # FIXME: These two lines aren't in the calendar common tests:
        # home = self.addressbookStore.newTransaction().addressbookHomeWithUID(
        #     "home1")

        # Sanity check: are the properties actually persisted?
        # FIXME: no independent testing of this right now
        checkProperties()


    @inlineCallbacks
    def test_createAddressBookWithName_exists(self):
        """
        L{IAddressBookHome.createAddressBookWithName} raises
        L{AddressBookAlreadyExistsError} when the name conflicts with an already-
        existing address book.
        """
        for name in home1_addressbookNames:
            yield self.failUnlessFailure(
                maybeDeferred(
                    (yield self.homeUnderTest()).createAddressBookWithName, name),
                HomeChildNameAlreadyExistsError,
            )


    @inlineCallbacks
    def test_removeAddressBookWithName_exists(self):
        """
        L{IAddressBookHome.removeAddressBookWithName} removes a addressbook that already
        exists.
        """
        home = yield self.homeUnderTest()
        # FIXME: test transactions
        for name in home1_addressbookNames:
            self.assertNotIdentical((yield home.addressbookWithName(name)), None)
            yield home.removeAddressBookWithName(name)
            self.assertEquals((yield home.addressbookWithName(name)), None)

        yield self.commit()

        # Make sure notification fired after commit
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_1"),
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_2"),
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_empty")
            ]
        )


    @inlineCallbacks
    def test_removeAddressBookWithName_absent(self):
        """
        Attempt to remove an non-existing addressbook should raise.
        """
        home = yield self.homeUnderTest()
        yield self.failUnlessFailure(
            maybeDeferred(home.removeAddressBookWithName, "xyzzy"),
            NoSuchHomeChildError
        )


    @inlineCallbacks
    def test_addressbookObjects(self):
        """
        L{IAddressBook.addressbookObjects} will enumerate the addressbook objects present
        in the filesystem, in name order, but skip those with hidden names.
        """
        addressbook1 = yield self.addressbookUnderTest()
        addressbookObjects = list((yield addressbook1.addressbookObjects()))

        for addressbookObject in addressbookObjects:
            self.assertProvides(IAddressBookObject, addressbookObject)
            self.assertEquals(
                (yield addressbook1.addressbookObjectWithName(addressbookObject.name())),
                addressbookObject
            )

        self.assertEquals(
            set(o.name() for o in addressbookObjects),
            set(addressbook1_objectNames)
        )


    @inlineCallbacks
    def test_addressbookObjectsWithRemovedObject(self):
        """
        L{IAddressBook.addressbookObjects} skips those objects which have been
        removed by L{AddressBook.removeAddressBookObjectWithName} in the same
        transaction, even if it has not yet been committed.
        """
        addressbook1 = yield self.addressbookUnderTest()
        yield addressbook1.removeAddressBookObjectWithName("2.vcf")
        addressbookObjects = list((yield addressbook1.addressbookObjects()))
        self.assertEquals(set(o.name() for o in addressbookObjects),
                          set(addressbook1_objectNames) - set(["2.vcf"]))


    @inlineCallbacks
    def test_ownerAddressBookHome(self):
        """
        L{IAddressBook.ownerAddressBookHome} should match the home UID.
        """
        self.assertEquals(
            (yield self.addressbookUnderTest()).ownerAddressBookHome().uid(),
            (yield self.homeUnderTest()).uid()
        )


    @inlineCallbacks
    def test_addressbookObjectWithName_exists(self):
        """
        L{IAddressBook.addressbookObjectWithName} returns an L{IAddressBookObject}
        provider for addressbooks which already exist.
        """
        addressbook1 = yield self.addressbookUnderTest()
        for name in addressbook1_objectNames:
            addressbookObject = yield addressbook1.addressbookObjectWithName(name)
            self.assertProvides(IAddressBookObject, addressbookObject)
            self.assertEquals(addressbookObject.name(), name)
            # FIXME: add more tests based on CommonTests.requirements


    @inlineCallbacks
    def test_addressbookObjectWithName_absent(self):
        """
        L{IAddressBook.addressbookObjectWithName} returns C{None} for addressbooks which
        don't exist.
        """
        addressbook1 = yield self.addressbookUnderTest()
        self.assertEquals((yield addressbook1.addressbookObjectWithName("xyzzy")), None)


    @inlineCallbacks
    def test_removeAddressBookObjectWithUID_exists(self):
        """
        Remove an existing addressbook object.
        """
        addressbook = yield self.addressbookUnderTest()
        for name in addressbook1_objectNames:
            uid = (u'uid' + name.rstrip(".vcf"))
            self.assertNotIdentical(
                (yield addressbook.addressbookObjectWithUID(uid)),
                None
            )
            yield addressbook.removeAddressBookObjectWithUID(uid)
            self.assertEquals(
                (yield addressbook.addressbookObjectWithUID(uid)),
                None
            )
            self.assertEquals(
                (yield addressbook.addressbookObjectWithName(name)),
                None
            )


    @inlineCallbacks
    def test_removeAddressBookObjectWithName_exists(self):
        """
        Remove an existing addressbook object.
        """
        addressbook = yield self.addressbookUnderTest()
        for name in addressbook1_objectNames:
            self.assertNotIdentical(
                (yield addressbook.addressbookObjectWithName(name)), None
            )
            yield addressbook.removeAddressBookObjectWithName(name)
            self.assertIdentical(
                (yield addressbook.addressbookObjectWithName(name)), None
            )

        # Make sure notifications are fired after commit
        yield self.commit()
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_1"),
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_1"),
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_1"),
            ]
        )


    @inlineCallbacks
    def test_removeAddressBookObjectWithName_absent(self):
        """
        Attempt to remove an non-existing addressbook object should raise.
        """
        addressbook = yield self.addressbookUnderTest()
        yield self.failUnlessFailure(
            maybeDeferred(addressbook.removeAddressBookObjectWithName, "xyzzy"),
            NoSuchObjectResourceError
        )


    @inlineCallbacks
    def test_addressbookName(self):
        """
        L{AddressBook.name} reflects the name of the addressbook.
        """
        self.assertEquals((yield self.addressbookUnderTest()).name(), "addressbook_1")


    @inlineCallbacks
    def test_addressbookObjectName(self):
        """
        L{IAddressBookObject.name} reflects the name of the addressbook object.
        """
        self.assertEquals(
            (yield self.addressbookObjectUnderTest()).name(),
            "1.vcf")


    @inlineCallbacks
    def test_addressbookObjectMetaData(self):
        """
        The objects retrieved from the addressbook have various
        methods which return metadata values.
        """
        adbk = yield self.addressbookObjectUnderTest()
        self.assertIsInstance(adbk.name(), basestring)
        self.assertIsInstance(adbk.uid(), basestring)
        self.assertIsInstance(adbk.md5(), basestring)
        self.assertIsInstance(adbk.size(), int)
        self.assertIsInstance(adbk.created(), int)
        self.assertIsInstance(adbk.modified(), int)


    @inlineCallbacks
    def test_component(self):
        """
        L{IAddressBookObject.component} returns a L{VComponent} describing the
        addressbook data underlying that addressbook object.
        """
        component = yield (yield self.addressbookObjectUnderTest()).component()

        self.failUnless(
            isinstance(component, VComponent),
            component
        )

        self.assertEquals(component.name(), "VCARD")
        self.assertEquals(component.resourceUID(), "uid1")


    @inlineCallbacks
    def test_iAddressBookText(self):
        """
        L{IAddressBookObject.iAddressBookText} returns a C{str} describing the same
        data provided by L{IAddressBookObject.component}.
        """
        text = yield (yield self.addressbookObjectUnderTest()).vCardText()
        self.assertIsInstance(text, str)
        self.failUnless(text.startswith("BEGIN:VCARD\r\n"))
        self.assertIn("\r\nUID:uid1\r\n", text)
        self.failUnless(text.endswith("\r\nEND:VCARD\r\n"))


    @inlineCallbacks
    def test_addressbookObjectUID(self):
        """
        L{IAddressBookObject.uid} returns a C{str} describing the C{UID} property
        of the addressbook object's component.
        """
        self.assertEquals((yield self.addressbookObjectUnderTest()).uid(), "uid1")


    @inlineCallbacks
    def test_addressbookObjectWithUID_absent(self):
        """
        L{IAddressBook.addressbookObjectWithUID} returns C{None} for addressbooks which
        don't exist.
        """
        addressbook1 = yield self.addressbookUnderTest()
        self.assertEquals(
            (yield addressbook1.addressbookObjectWithUID("xyzzy")),
            None
        )


    @inlineCallbacks
    def test_addressbooks(self):
        """
        L{IAddressBookHome.addressbooks} returns an iterable of L{IAddressBook}
        providers, which are consistent with the results from
        L{IAddressBook.addressbookWithName}.
        """
        # Add a dot directory to make sure we don't find it
        # self.home1._path.child(".foo").createDirectory()
        home = yield self.homeUnderTest()
        addressbooks = list((yield home.addressbooks()))

        for addressbook in addressbooks:
            self.assertProvides(IAddressBook, addressbook)
            self.assertEquals(
                addressbook,
                (yield home.addressbookWithName(addressbook.name()))
            )

        self.assertEquals(
            set(c.name() for c in addressbooks),
            set(home1_addressbookNames)
        )


    @inlineCallbacks
    def test_loadAllAddressBooks(self):
        """
        L{IAddressBookHome.loadAddressBooks} returns an iterable of L{IAddressBook}
        providers, which are consistent with the results from
        L{IAddressBook.addressbookWithName}.
        """
        # Add a dot directory to make sure we don't find it
        # self.home1._path.child(".foo").createDirectory()
        home = yield self.homeUnderTest()
        addressbooks = (yield home.loadAddressbooks())

        for addressbook in addressbooks:
            self.assertProvides(IAddressBook, addressbook)
            self.assertEquals(addressbook,
                              (yield home.addressbookWithName(addressbook.name())))

        self.assertEquals(
            set(c.name() for c in addressbooks),
            set(home1_addressbookNames)
        )
        
        for c in addressbooks:
            self.assertTrue(c.properties() is not None)


    @inlineCallbacks
    def test_addressbooksAfterAddAddressBook(self):
        """
        L{IAddressBookHome.addressbooks} includes addressbooks recently added with
        L{IAddressBookHome.createAddressBookWithName}.
        """
        home = yield self.homeUnderTest()
        allAddressbooks = yield home.addressbooks()
        before = set(x.name() for x in allAddressbooks)
        yield home.createAddressBookWithName("new-name")
        allAddressbooks = yield home.addressbooks()
        after = set(x.name() for x in allAddressbooks)
        self.assertEquals(before | set(['new-name']), after)


    @inlineCallbacks
    def test_createAddressBookObjectWithName_absent(self):
        """
        L{IAddressBook.createAddressBookObjectWithName} creates a new
        L{IAddressBookObject}.
        """
        addressbook1 = yield self.addressbookUnderTest()
        name = "4.vcf"
        self.assertIdentical((yield addressbook1.addressbookObjectWithName(name)), None)
        component = VComponent.fromString(vcard4_text)
        yield addressbook1.createAddressBookObjectWithName(name, component)

        addressbookObject = yield addressbook1.addressbookObjectWithName(name)
        self.assertEquals((yield addressbookObject.component()), component)

        yield self.commit()

        # Make sure notifications fire after commit
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_1"),
            ]
        )


    @inlineCallbacks
    def test_createAddressBookObjectWithName_exists(self):
        """
        L{IAddressBook.createAddressBookObjectWithName} raises
        L{AddressBookObjectNameAlreadyExistsError} if a addressbook object with the
        given name already exists in that addressbook.
        """
        yield self.failUnlessFailure(
            maybeDeferred(
                (yield self.addressbookUnderTest()).createAddressBookObjectWithName,
                "1.vcf", VComponent.fromString(vcard4_text)),
            ObjectResourceNameAlreadyExistsError
        )


    @inlineCallbacks
    def test_createAddressBookObjectWithName_invalid(self):
        """
        L{IAddressBook.createAddressBookObjectWithName} raises
        L{InvalidAddressBookComponentError} if presented with invalid iAddressBook
        text.
        """
        yield self.failUnlessFailure(
            maybeDeferred((yield self.addressbookUnderTest())
                .createAddressBookObjectWithName,
                "new", VComponent.fromString(vcard4notCardDAV_text)),
            InvalidObjectResourceError
        )


    @inlineCallbacks
    def test_setComponent_invalid(self):
        """
        L{IAddressBookObject.setComponent} raises L{InvalidIAddressBookDataError} if
        presented with invalid iAddressBook text.
        """
        addressbookObject = (yield self.addressbookObjectUnderTest())
        yield self.failUnlessFailure(
            maybeDeferred(addressbookObject.setComponent,
                VComponent.fromString(vcard4notCardDAV_text)),
            InvalidObjectResourceError
        )


    @inlineCallbacks
    def test_setComponent_uidchanged(self):
        """
        L{IAddressBookObject.setComponent} raises
        L{InvalidAddressBookComponentError} when given a L{VComponent} whose
        UID does not match its existing UID.
        """
        addressbook1 = yield self.addressbookUnderTest()
        component = VComponent.fromString(vcard4_text)
        addressbookObject = yield addressbook1.addressbookObjectWithName("1.vcf")
        yield self.failUnlessFailure(
            maybeDeferred(addressbookObject.setComponent, component),
            InvalidObjectResourceError
        )


    @inlineCallbacks
    def test_addressbookHomeWithUID_create(self):
        """
        L{IAddressBookStoreTransaction.addressbookHomeWithUID} with
        C{create=True} will create a addressbook home that doesn't exist yet.
        """
        txn = self.transactionUnderTest()
        noHomeUID = "xyzzy"
        addressbookHome = yield txn.addressbookHomeWithUID(
            noHomeUID,
            create=True
        )
        @inlineCallbacks
        def readOtherTxn():
            otherTxn = self.savedStore.newTransaction()
            self.addCleanup(otherTxn.commit)
            returnValue((yield otherTxn.addressbookHomeWithUID(noHomeUID)))
        self.assertProvides(IAddressBookHome, addressbookHome)
        # A concurrent tnransaction shouldn't be able to read it yet:
        self.assertIdentical((yield readOtherTxn()), None)
        yield self.commit()
        # But once it's committed, other transactions should see it.
        self.assertProvides(IAddressBookHome, (yield readOtherTxn()))


    @inlineCallbacks
    def test_setComponent(self):
        """
        L{AddressBookObject.setComponent} changes the result of
        L{AddressBookObject.component} within the same transaction.
        """
        component = VComponent.fromString(vcard1modified_text)

        addressbook1 = yield self.addressbookUnderTest()
        addressbookObject = yield addressbook1.addressbookObjectWithName("1.vcf")
        oldComponent = yield addressbookObject.component()
        self.assertNotEqual(component, oldComponent)
        yield addressbookObject.setComponent(component)
        self.assertEquals((yield addressbookObject.component()), component)

        # Also check a new instance
        addressbookObject = yield addressbook1.addressbookObjectWithName("1.vcf")
        self.assertEquals((yield addressbookObject.component()), component)

        yield self.commit()

        # Make sure notification fired after commit
        self.assertEquals(
            self.notifierFactory.history,
            [
                ("update", "CardDAV|home1"),
                ("update", "CardDAV|home1/addressbook_1"),
            ]
        )


    def checkPropertiesMethod(self, thunk):
        """
        Verify that the given object has a properties method that returns an
        L{IPropertyStore}.
        """
        properties = thunk.properties()
        self.assertProvides(IPropertyStore, properties)


    @inlineCallbacks
    def test_homeProperties(self):
        """
        L{IAddressBookHome.properties} returns a property store.
        """
        self.checkPropertiesMethod((yield self.homeUnderTest()))


    @inlineCallbacks
    def test_addressbookProperties(self):
        """
        L{IAddressBook.properties} returns a property store.
        """
        self.checkPropertiesMethod((yield self.addressbookUnderTest()))


    @inlineCallbacks
    def test_addressbookObjectProperties(self):
        """
        L{IAddressBookObject.properties} returns a property store.
        """
        self.checkPropertiesMethod((yield self.addressbookObjectUnderTest()))


    @inlineCallbacks
    def test_newAddressBookObjectProperties(self):
        """
        L{IAddressBookObject.properties} returns an empty property store for a
        addressbook object which has been created but not committed.
        """
        addressbook = yield self.addressbookUnderTest()
        yield addressbook.createAddressBookObjectWithName(
            "4.vcf", VComponent.fromString(vcard4_text)
        )
        newEvent = yield addressbook.addressbookObjectWithName("4.vcf")
        self.assertEquals(newEvent.properties().items(), [])


    @inlineCallbacks
    def test_setComponentPreservesProperties(self):
        """
        L{IAddressBookObject.setComponent} preserves properties.

        (Some implementations must go to extra trouble to provide this
        behavior; for example, file storage must copy extended attributes from
        the existing file to the temporary file replacing it.)
        """
        propertyName = PropertyName("http://example.com/ns", "example")
        propertyContent = WebDAVUnknownElement("sample content")
        propertyContent.name = propertyName.name
        propertyContent.namespace = propertyName.namespace

        abobject = (yield self.addressbookObjectUnderTest())
        if abobject._parentCollection.objectResourcesHaveProperties():
            (yield self.addressbookObjectUnderTest()).properties()[
                propertyName] = propertyContent
            yield self.commit()
            # Sanity check; are properties even readable in a separate transaction?
            # Should probably be a separate test.
            self.assertEquals(
                (yield self.addressbookObjectUnderTest()).properties()[
                    propertyName
                ],
                propertyContent)
            obj = yield self.addressbookObjectUnderTest()
            vcard1_text = yield obj.vCardText()
            vcard1_text_withDifferentNote = vcard1_text.replace(
                "NOTE:CardDAV protocol updates",
                "NOTE:Changed"
            )
            # Sanity check; make sure the test has the right idea of the subject.
            self.assertNotEquals(vcard1_text, vcard1_text_withDifferentNote)
            newComponent = VComponent.fromString(vcard1_text_withDifferentNote)
            yield obj.setComponent(newComponent)
    
            # Putting everything into a separate transaction to account for any
            # caching that may take place.
            yield self.commit()
            self.assertEquals(
                (yield self.addressbookObjectUnderTest()).properties()[propertyName],
                propertyContent
            )


    @inlineCallbacks
    def test_dontLeakAddressbooks(self):
        """
        Addressbooks in one user's addressbook home should not show up in another
        user's addressbook home.
        """
        home2 = yield self.transactionUnderTest().addressbookHomeWithUID(
            "home2", create=True
        )
        self.assertIdentical((yield home2.addressbookWithName("addressbook_1")), None)


    @inlineCallbacks
    def test_dontLeakObjects(self):
        """
        Addressbook objects in one user's addressbook should not show up in another
        user's via uid or name queries.
        """
        home1 = yield self.homeUnderTest()
        home2 = yield self.transactionUnderTest().addressbookHomeWithUID(
            "home2", create=True)
        addressbook1 = yield home1.addressbookWithName("addressbook_1")
        addressbook2 = yield home2.addressbookWithName("addressbook")
        objects = list((yield (yield home2.addressbookWithName("addressbook")).addressbookObjects()))
        self.assertEquals(objects, [])
        for resourceName in self.requirements['home1']['addressbook_1'].keys():
            obj = yield addressbook1.addressbookObjectWithName(resourceName)
            self.assertIdentical(
                (yield addressbook2.addressbookObjectWithName(resourceName)), None)
            self.assertIdentical(
                (yield addressbook2.addressbookObjectWithUID(obj.uid())), None)


    @inlineCallbacks
    def test_eachAddressbookHome(self):
        """
        L{IAddressbookTransaction.eachAddressbookHome} returns an iterator that
        yields 2-tuples of (transaction, home).
        """
        # create some additional addressbook homes
        additionalUIDs = set('alpha-uid home2 home3 beta-uid'.split())
        txn = self.transactionUnderTest()
        for name in additionalUIDs:
            yield txn.addressbookHomeWithUID(name, create=True)
        yield self.commit()
        foundUIDs = set([])
        lastTxn = None
        for txn, home in (yield self.storeUnderTest().eachAddressbookHome()):
            self.addCleanup(txn.commit)
            foundUIDs.add(home.uid())
            self.assertNotIdentical(lastTxn, txn)
            lastTxn = txn
        requiredUIDs = set([
            uid for uid in self.requirements
            if self.requirements[uid] is not None
        ])
        expectedUIDs = additionalUIDs.union(requiredUIDs)
        self.assertEquals(foundUIDs, expectedUIDs)



