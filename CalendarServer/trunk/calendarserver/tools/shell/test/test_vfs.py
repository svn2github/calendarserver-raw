#!/usr/bin/env python
##
# Copyright (c) 2012 Apple Inc. All rights reserved.
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

from twisted.trial.unittest import TestCase
from twisted.internet.defer import succeed, inlineCallbacks

from calendarserver.tools.shell.vfs import ListEntry
from calendarserver.tools.shell.vfs import File, Folder
from calendarserver.tools.shell.vfs import UIDsFolder
from calendarserver.tools.shell.terminal import ShellService
from twistedcaldav.directory.test.test_xmlfile import XMLFileBase
from txdav.common.datastore.test.util import buildStore


class TestListEntry(TestCase):
    def test_toString(self):
        self.assertEquals(ListEntry(None, File  , "thingo"           ).toString(), "thingo" )
        self.assertEquals(ListEntry(None, File  , "thingo", Foo="foo").toString(), "thingo" )
        self.assertEquals(ListEntry(None, Folder, "thingo"           ).toString(), "thingo/")
        self.assertEquals(ListEntry(None, Folder, "thingo", Foo="foo").toString(), "thingo/")

    def test_fieldNamesImplicit(self):
        # This test assumes File doesn't set list.fieldNames.
        assert not hasattr(File.list, "fieldNames")

        self.assertEquals(set(ListEntry(File(None, ()), File, "thingo").fieldNames), set(("Name",)))

    def test_fieldNamesExplicit(self):
        def fieldNames(fileClass):
            return ListEntry(fileClass(None, ()), fileClass, "thingo", Flavor="Coconut", Style="Hard")

        # Full list
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ("Name", "Flavor")
        self.assertEquals(fieldNames(MyFile).fieldNames, ("Name", "Flavor"))

        # Full list, different order
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ("Flavor", "Name")
        self.assertEquals(fieldNames(MyFile).fieldNames, ("Flavor", "Name"))

        # Omits Name, which is implicitly added
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ("Flavor",)
        self.assertEquals(fieldNames(MyFile).fieldNames, ("Name", "Flavor"))

        # Emtpy
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ()
        self.assertEquals(fieldNames(MyFile).fieldNames, ("Name",))

    def test_toFieldsImplicit(self):
        # This test assumes File doesn't set list.fieldNames.
        assert not hasattr(File.list, "fieldNames")

        # Name first, rest sorted by field name
        self.assertEquals(
            tuple(ListEntry(File(None, ()), File, "thingo", Flavor="Coconut", Style="Hard").toFields()),
            ("thingo", "Coconut", "Hard")
        )

    def test_toFieldsExplicit(self):
        def fields(fileClass):
            return tuple(ListEntry(fileClass(None, ()), fileClass, "thingo", Flavor="Coconut", Style="Hard").toFields())

        # Full list
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ("Name", "Flavor")
        self.assertEquals(fields(MyFile), ("thingo", "Coconut"))

        # Full list, different order
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ("Flavor", "Name")
        self.assertEquals(fields(MyFile), ("Coconut", "thingo"))

        # Omits Name, which is implicitly added
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ("Flavor",)
        self.assertEquals(fields(MyFile), ("thingo", "Coconut"))

        # Emtpy
        class MyFile(File):
            def list(self): return succeed(())
            list.fieldNames = ()
        self.assertEquals(fields(MyFile), ("thingo",))



class DirectoryStubber(XMLFileBase):
    """
    Object which creates a stub L{IDirectoryService}.
    """
    def __init__(self, testCase):
        self.testCase = testCase

    def mktemp(self):
        return self.testCase.mktemp()



class UIDsFolderTests(TestCase):
    """
    L{UIDsFolder} contains all principals and is keyed by UID.
    """

    @inlineCallbacks
    def setUp(self):
        """
        Create a L{UIDsFolder}.
        """
        self.svc = ShellService(store=(yield buildStore(self, None)),
                                directory=DirectoryStubber(self).service(),
                                options=None, reactor=None, config=None)
        self.folder = UIDsFolder(self.svc, ())


    @inlineCallbacks
    def test_list(self):
        """
        L{UIDsFolder.list} returns a L{Deferred} firing an iterable of
        L{ListEntry} objects, reflecting the directory information for all
        calendars and addressbooks created in the store.
        """
        txn = self.svc.store.newTransaction()
        wsanchez = "6423F94A-6B76-4A3A-815B-D52CFD77935D"
        dreid = "5FF60DAD-0BDE-4508-8C77-15F0CA5C8DD1"
        yield txn.calendarHomeWithUID(wsanchez, create=True)
        yield txn.addressbookHomeWithUID(dreid, create=True)
        yield txn.commit()
        listing = list((yield self.folder.list()))
        self.assertEquals(
            [x.fields for x in listing],
            [{"Record Type": "users", "Short Name": "wsanchez",
              "Full Name": "Wilfredo Sanchez", "Name": wsanchez},
              {"Record Type": "users", "Short Name": "dreid",
              "Full Name": "David Reid", "Name": dreid}]
        )



