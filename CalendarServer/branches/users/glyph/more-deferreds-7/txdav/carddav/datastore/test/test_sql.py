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
Tests for L{txdav.carddav.datastore.sql}, mostly based on
L{txdav.carddav.datastore.test.common}.
"""

from txdav.carddav.datastore.test.common import CommonTests as AddressBookCommonTests

from txdav.common.datastore.sql import EADDRESSBOOKTYPE
from txdav.common.datastore.test.util import buildStore
from txdav.carddav.datastore.test.test_file import setUpAddressBookStore
from twext.web2.dav.element.rfc2518 import GETContentLanguage, ResourceType
from txdav.base.propertystore.base import PropertyName
from txdav.carddav.datastore.util import _migrateAddressbook, migrateHome

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks, returnValue

from twistedcaldav.vcard import Component as VCard


class AddressBookSQLStorageTests(AddressBookCommonTests, unittest.TestCase):
    """
    AddressBook SQL storage tests.
    """

    @inlineCallbacks
    def setUp(self):
        super(AddressBookSQLStorageTests, self).setUp()
        self._sqlStore = yield buildStore(self, self.notifierFactory)
        yield self.populate()

    @inlineCallbacks
    def populate(self):
        populateTxn = self.storeUnderTest().newTransaction()
        for homeUID in self.requirements:
            addressbooks = self.requirements[homeUID]
            if addressbooks is not None:
                home = yield populateTxn.addressbookHomeWithUID(homeUID, True)
                # We don't want the default addressbook to appear unless it's
                # explicitly listed.
                yield home.removeAddressBookWithName("addressbook")
                for addressbookName in addressbooks:
                    addressbookObjNames = addressbooks[addressbookName]
                    if addressbookObjNames is not None:
                        yield home.createAddressBookWithName(addressbookName)
                        addressbook = yield home.addressbookWithName(addressbookName)
                        for objectName in addressbookObjNames:
                            objData = addressbookObjNames[objectName]
                            yield addressbook.createAddressBookObjectWithName(
                                objectName, VCard.fromString(objData)
                            )

        yield populateTxn.commit()
        self.notifierFactory.reset()



    def storeUnderTest(self):
        """
        Create and return a L{AddressBookStore} for testing.
        """
        return self._sqlStore


    @inlineCallbacks
    def assertAddressbooksSimilar(self, a, b, bAddressbookFilter=None):
        """
        Assert that two addressbooks have a similar structure (contain the same
        events).
        """
        @inlineCallbacks
        def namesAndComponents(x, filter=lambda x:x.component()):
            fromObjs = yield x.addressbookObjects()
            returnValue(dict([(fromObj.name(), (yield filter(fromObj)))
                              for fromObj in fromObjs]))
        if bAddressbookFilter is not None:
            extra = [bAddressbookFilter]
        else:
            extra = []
        self.assertEquals((yield namesAndComponents(a)),
                          (yield namesAndComponents(b, *extra)))


    def assertPropertiesSimilar(self, a, b, disregard=[]):
        """
        Assert that two objects with C{properties} methods have similar
        properties.
        
        @param disregard: a list of L{PropertyName} keys to discard from both
            input and output.
        """
        def sanitize(x):
            result = dict(x.properties().items())
            for key in disregard:
                result.pop(key, None)
            return result
        self.assertEquals(sanitize(a), sanitize(b))


    def fileTransaction(self):
        """
        Create a file-backed addressbook transaction, for migration testing.
        """
        setUpAddressBookStore(self)
        fileStore = self.addressbookStore
        txn = fileStore.newTransaction()
        self.addCleanup(txn.commit)
        return txn


    @inlineCallbacks
    def test_migrateAddressbookFromFile(self):
        """
        C{_migrateAddressbook()} can migrate a file-backed addressbook to a
        database- backed addressbook.
        """
        fromAddressbook = yield self.fileTransaction().addressbookHomeWithUID(
            "home1").addressbookWithName("addressbook_1")
        toHome = yield self.transactionUnderTest().addressbookHomeWithUID(
            "new-home", create=True)
        toAddressbook = yield toHome.addressbookWithName("addressbook")
        yield _migrateAddressbook(fromAddressbook, toAddressbook,
                                  lambda x: x.component())
        yield self.assertAddressbooksSimilar(fromAddressbook, toAddressbook)


    @inlineCallbacks
    def test_migrateHomeFromFile(self):
        """
        L{migrateHome} will migrate an L{IAddressbookHome} provider from one
        backend to another; in this specific case, from the file-based backend
        to the SQL-based backend.
        """
        fromHome = yield self.fileTransaction().addressbookHomeWithUID("home1")

        builtinProperties = [PropertyName.fromElement(ResourceType)]

        # Populate an arbitrary / unused dead properties so there's something
        # to verify against.

        key = PropertyName.fromElement(GETContentLanguage)
        fromHome.properties()[key] = GETContentLanguage("C")
        (yield fromHome.addressbookWithName("addressbook_1")).properties()[
            key] = (
            GETContentLanguage("pig-latin")
        )
        toHome = yield self.transactionUnderTest().addressbookHomeWithUID(
            "new-home", create=True
        )
        yield migrateHome(fromHome, toHome, lambda x: x.component())
        toAddressbooks = yield toHome.addressbooks()
        self.assertEquals(set([c.name() for c in toAddressbooks]),
                          set([k for k in self.requirements['home1'].keys()
                               if self.requirements['home1'][k] is not None]))
        fromAddressbooks = yield fromHome.addressbooks()
        for c in fromAddressbooks:
            self.assertPropertiesSimilar(
                c, (yield toHome.addressbookWithName(c.name())),
                builtinProperties
            )
        self.assertPropertiesSimilar(fromHome, toHome, builtinProperties)


    def test_eachAddressbookHome(self):
        """
        L{IAddressbookStore.eachAddressbookHome} is currently stubbed out by
        L{txdav.common.datastore.sql.CommonDataStore}.
        """
        return super(AddressBookSQLStorageTests, self).test_eachAddressbookHome()


    test_eachAddressbookHome.todo = (
        "stubbed out, as migration only needs to go from file->sql currently")


    @inlineCallbacks
    def test_putConcurrency(self):
        """
        Test that two concurrent attempts to PUT different address book object resources to the
        same address book home does not cause a deadlock.
        """

        addressbookStore1 = yield buildStore(self, self.notifierFactory)
        addressbookStore2 = yield buildStore(self, self.notifierFactory)

        # Provision the home now
        txn = addressbookStore1.newTransaction()
        home = yield txn.homeWithUID(EADDRESSBOOKTYPE, "uid1", create=True)
        self.assertNotEqual(home, None)
        yield txn.commit()

        txn1 = addressbookStore1.newTransaction()
        txn2 = addressbookStore2.newTransaction()

        home1 = yield txn1.homeWithUID(EADDRESSBOOKTYPE, "uid1", create=True)
        home2 = yield txn2.homeWithUID(EADDRESSBOOKTYPE, "uid1", create=True)

        adbk1 = yield home1.addressbookWithName("addressbook")
        adbk2 = yield home2.addressbookWithName("addressbook")

        @inlineCallbacks
        def _defer1():
            yield adbk1.createObjectResourceWithName("1.vcf", VCard.fromString(
                """BEGIN:VCARD
VERSION:3.0
N:Thompson;Default1;;;
FN:Default1 Thompson
EMAIL;type=INTERNET;type=WORK;type=pref:lthompson1@example.com
TEL;type=WORK;type=pref:1-555-555-5555
TEL;type=CELL:1-444-444-4444
item1.ADR;type=WORK;type=pref:;;1245 Test;Sesame Street;California;11111;USA
item1.X-ABADR:us
UID:uid1
END:VCARD
""".replace("\n", "\r\n")
            ))
            yield txn1.commit() # FIXME: CONCURRENT
        d1 = _defer1()

        @inlineCallbacks
        def _defer2():
            yield adbk2.createObjectResourceWithName("2.vcf", VCard.fromString(
                """BEGIN:VCARD
VERSION:3.0
N:Thompson;Default2;;;
FN:Default2 Thompson
EMAIL;type=INTERNET;type=WORK;type=pref:lthompson2@example.com
TEL;type=WORK;type=pref:1-555-555-5556
TEL;type=CELL:1-444-444-4445
item1.ADR;type=WORK;type=pref:;;1234 Test;Sesame Street;California;11111;USA
item1.X-ABADR:us
UID:uid2
END:VCARD
""".replace("\n", "\r\n")
            ))
            yield txn2.commit() # FIXME: CONCURRENT
        d2 = _defer2()

        yield d1
        yield d2
