##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

import os
import time

from twisted.web2.dav import davxml

from twistedcaldav import caldavxml, customxml
from twistedcaldav.root import RootResource
from twistedcaldav.sqlprops import sqlPropertyStore, SQLPropertiesDatabase
from twistedcaldav.static import CalDAVFile
import twistedcaldav.test.util

class SQLProps (twistedcaldav.test.util.TestCase):
    """
    SQL properties tests
    """

    props = (
        davxml.DisplayName.fromString("My Name"),
        davxml.ACL(
            davxml.ACE(
                davxml.Principal(davxml.Authenticated()),
                davxml.Grant(davxml.Privilege(davxml.Read())),
                davxml.Protected(),
            ),
        ),
        caldavxml.CalendarDescription.fromString("My Calendar"),
        customxml.TwistedScheduleAutoRespond(),
    )
    
    def _setUpIndex(self):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        rsrc = CalDAVFile(os.path.join(self.collection_name, "file.ics"))
        return sqlPropertyStore(rsrc)
        
    def _setOnePropertyAndTest(self, prop):
        index = self._setUpIndex()
        index.set(prop)
        self.assertTrue(index.contains(prop.qname()),
                        msg="Could not find property %s." % prop)
        self.assertTrue(index.get(prop.qname()) == prop,
                        msg="Could not get property %s." % prop)
        
    def _setProperty(self, index, prop):
        index.set(prop)
        
    def _testProperty(self, index, prop):
        self.assertTrue(index.contains(prop.qname()),
                        msg="Could not find property %s." % prop)
        self.assertTrue(index.get(prop.qname()) == prop,
                        msg="Could not get property %s." % prop)
    
    def _testPropertyList(self, proplist):
        self.assertTrue(len(proplist) == len(SQLProps.props),
                        msg="Number of properties returned %s not equal to number queried %s." % (len(proplist), len(SQLProps.props),))
        for prop in SQLProps.props:
            for p in proplist:
                if prop == p:
                    proplist.remove(p)
                    break
        self.assertTrue(len(proplist) == 0,
                        msg="Incorrect properties returned %s." % proplist)

    def _testResourcePropertyList(self, num_resources, resourcedict):
        self.assertTrue(len(resourcedict) == num_resources,
                        msg="Number of resources returned %s not equal to number queried %s." % (len(resourcedict), num_resources,))
        for i in xrange(num_resources):
            fname = "file%04s.ics" % (i,)
            self.assertTrue(resourcedict.has_key(fname),
                            msg="Resource %s not returned in query results" % (fname,))
            self._testPropertyList(resourcedict[fname])

    def _setupMultipleResources(self, number):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        for i in xrange(number):
            rsrc = CalDAVFile(os.path.join(self.collection_name, "file%04s.ics" % (i,)))
            index = sqlPropertyStore(rsrc)
            for prop in SQLProps.props:
                index.set(prop)
        return index

    def test_db_init_directory(self):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        rsrc = CalDAVFile(self.collection_name)
        index = sqlPropertyStore(rsrc)
        db = index.index._db()
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(self.collection_name), SQLPropertiesDatabase.dbFilename)),
                        msg="Could not initialize index via collection resource.")

    def test_db_init_root(self):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        rsrc = RootResource(self.collection_name)
        index = sqlPropertyStore(rsrc)
        db = index.index._db()
        self.assertTrue(os.path.exists(os.path.join(self.collection_name, SQLPropertiesDatabase.dbFilename)),
                        msg="Could not initialize index via collection resource.")

    def test_db_init_file(self):
        index = self._setUpIndex()
        db = index.index._db()
        self.assertTrue(os.path.exists(os.path.join(self.collection_name, SQLPropertiesDatabase.dbFilename)),
                        msg="Could not initialize index via file resource.")

    def test_setoneproperty(self):
        for prop in SQLProps.props:
            self._setOnePropertyAndTest(prop)

    def test_setunknownproperty(self):
        doc = davxml.WebDAVDocument.fromString("""<?xml version="1.0" encoding="utf-8" ?><guess/>""")
        self._setOnePropertyAndTest(doc.root_element)

    def test_setmultipleproperties(self):
        index = self._setUpIndex()
        for prop in SQLProps.props:
            self._setProperty(index, prop)
        for prop in SQLProps.props:
            self._testProperty(index, prop)
        proplist = set(index.list())
        expected_proplist = set([prop.qname() for prop in SQLProps.props])
        self.assertTrue(proplist == expected_proplist,
                        msg="Property lists do not match: %s != %s." % (proplist, expected_proplist))

    def test_deleteproperties(self):
        index = self._setUpIndex()
        for prop in SQLProps.props:
            self._setProperty(index, prop)
        
        remaining_props = [p for p in SQLProps.props]
        for prop in SQLProps.props:
            remaining_props.pop(0)
            index.delete(prop.qname())
            proplist = set(index.list())
            expected_proplist = set([prop.qname() for prop in remaining_props])
            self.assertTrue(proplist == expected_proplist,
                            msg="Property lists do not match: %s != %s." % (proplist, expected_proplist))

    def test_deleteallproperties(self):
        index = self._setUpIndex()
        for prop in SQLProps.props:
            self._setProperty(index, prop)
        
        index.deleteAll()
        for prop in SQLProps.props:
            self.assertFalse(index.contains(prop.qname()),
                            msg="Could not find property %s." % prop)

    def test_getallproperties(self):
        index = self._setUpIndex()
        for prop in SQLProps.props:
            self._setProperty(index, prop)
        
        result = index.getAll([p.qname() for p in SQLProps.props])
        self._testPropertyList(result)

    def test_getallresourceproperties(self):
        num_resources = 10
        index = self._setupMultipleResources(num_resources)
        result = index.getAllResources([p.qname() for p in SQLProps.props])
        self._testResourcePropertyList(num_resources, result)

#    def test_timegetallresourceproperties(self):
#        num_resources = 1000
#        index = self._setupMultipleResources(num_resources)
#        t1 = time.time()
#        result = index.getAllResources([p.qname() for p in SQLProps.props])
#        t2 = time.time()
#        self.assertTrue(t1 == t2,
#                        msg="Time for 1000 prop query = %s" % (t2 - t1,))
#
#        self._testResourcePropertyList(num_resources, result)
