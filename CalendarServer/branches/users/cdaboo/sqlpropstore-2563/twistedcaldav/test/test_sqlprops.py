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
##

from twistedcaldav.ical import Component
from twisted.web2.dav.element.rfc2518 import DisplayName

import os

from twisted.web2 import responsecode
from twisted.web2.dav import davxml
from twisted.web2.dav.fileop import put
from twisted.web2.dav.resource import TwistedGETContentMD5
from twisted.web2.dav.test.util import serialize
from twisted.web2.iweb import IResponse
from twisted.web2.stream import MemoryStream
from twisted.web2.test.test_server import SimpleRequest

from twistedcaldav import caldavxml, customxml
from twistedcaldav.root import RootResource
from twistedcaldav.sqlprops import sqlPropertyStore, SQLPropertiesDatabase
from twistedcaldav.static import CalDAVFile
import twistedcaldav.test.util

class SQLProps (twistedcaldav.test.util.TestCase):
    """
    SQL properties tests
    """
    data_dir = os.path.join(os.path.dirname(__file__), "data")

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
    )
    
    def _setUpIndex(self):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        rsrc = CalDAVFile(os.path.join(self.collection_name, "file.ics"))
        return sqlPropertyStore(rsrc, False)
        
    def _setOnePropertyAndTest(self, prop):
        index = self._setUpIndex()
        index.set(prop)
        self.assertTrue(index.contains(prop.qname()),
                        msg="Could not find property %s." % prop)
        self.assertTrue(index.get(prop.qname()) == prop,
                        msg="Could not get property %s." % prop)
        
    def _setProperty(self, index, prop):
        index.set(prop)
        
    def _testProperty(self, index, prop, description = ""):
        self.assertTrue(index.contains(prop.qname()),
                        msg="Could not find property %s %s." % (description, prop,))
        self.assertTrue(index.get(prop.qname()) == prop,
                        msg="Could not get property %s %s." % (description, prop,))
    
    def _testPropertyList(self, proplist):
        self.assertTrue(len(proplist) == len(SQLProps.props),
                        msg="Number of properties returned %s not equal to number queried %s." % (len(proplist), len(SQLProps.props),))
        for prop in SQLProps.props:
            for k, v in proplist.iteritems():
                if prop == v:
                    del proplist[k]
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
            index = sqlPropertyStore(rsrc, False)
            index.setSeveral(SQLProps.props)
        return index

    def _setupMultipleDifferentResources(self, number):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        for i in xrange(number):
            rsrc = CalDAVFile(os.path.join(self.collection_name, "file%04s.ics" % (i,)))
            index = sqlPropertyStore(rsrc, False)
            props = (
                davxml.DisplayName.fromString("My Name %s" % (i,)),
                davxml.ACL(
                    davxml.ACE(
                        davxml.Principal(davxml.HRef.fromString("/principals/users/user%s" % (i + 30,))),
                        davxml.Grant(davxml.Privilege(davxml.Read())),
                        davxml.Protected(),
                    ),
                ),
                caldavxml.CalendarDescription.fromString("My Calendar %s" % (i + 50,)),
                customxml.TwistedScheduleAutoRespond(),
            )
            index.setSeveral(props)
        return index

    def test_db_init_directory(self):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        rsrc = CalDAVFile(self.collection_name)
        index = sqlPropertyStore(rsrc, False)
        index.index._db()
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(self.collection_name), SQLPropertiesDatabase.dbFilename)),
                        msg="Could not initialize index via collection resource.")

    def test_db_init_root(self):
        self.collection_name, self.collection_uri = self.mkdtemp("sql")
        rsrc = RootResource(self.collection_name)
        index = sqlPropertyStore(rsrc, False)
        index.index._db()
        self.assertTrue(os.path.exists(os.path.join(self.collection_name, SQLPropertiesDatabase.dbFilename)),
                        msg="Could not initialize index via collection resource.")

    def test_db_init_file(self):
        index = self._setUpIndex()
        index.index._db()
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
                            msg="Found deleted property %s." % prop)

    def _do_delete(self, parent):
        fpath = self.docroot
        if parent:
            fpath = os.path.join(fpath, parent)
        fpath = os.path.join(fpath, "file.ics")
        rsrc = CalDAVFile(fpath)
        ms = MemoryStream("Some Data")

        def donePut(status):
            self.assertTrue(status == responsecode.CREATED)
            md5 = TwistedGETContentMD5.fromString("MD5")
            rsrc.writeDeadProperty(md5)
            
            # Check index
            index = sqlPropertyStore(rsrc, False)
            self._testProperty(index, md5)
            
            def doneDelete(response):
                response = IResponse(response)
    
                if response.code != responsecode.NO_CONTENT:
                    self.fail("DELETE response %s != %s" % (response.code, responsecode.NO_CONTENT))
    
                if os.path.exists(fpath):
                    self.fail("DELETE did not remove path %s" % (fpath,))

                self.assertFalse(index.contains(md5.qname()),
                                 msg="Property %s exists after resource was deleted." % md5)

            def work():
                # Delete resource and test
                request = SimpleRequest(self.site, "DELETE", "/%sfile.ics" % (parent,))
                yield (request, doneDelete)

            return serialize(self.send, work())

        d = put(ms, rsrc.fp)
        d.addCallback(donePut)
        return d

    def test_deleteresource(self):
        return self._do_delete("")

    def test_deletecalendarresource(self):

        def doneMake(response):
            self.assertTrue(response.code == responsecode.CREATED)
            return self._do_delete("calendar/")

        # Make a calendar
        request = SimpleRequest(self.site, "MKCALENDAR", "/calendar/")
        return self.send(request, doneMake)

    event = """BEGIN:VCALENDAR
PRODID:Test Case
CALSCALE:GREGORIAN
BEGIN:VEVENT
DTSTAMP:20070219T120000Z
DTSTART:20070219T120000Z
DTEND:20070219T130000Z
UID:12345-67890-54321
END:VEVENT
END:VCALENDAR
"""

    def _do_copy(self, src, dst):
        fpath = self.docroot
        if src:
            fpath = os.path.join(fpath, src)
        fpath = os.path.join(fpath, "file.ics")
        fpath_new = self.docroot
        if dst:
            fpath_new = os.path.join(fpath_new, dst)
        fpath_new = os.path.join(fpath_new, "copy.ics")
        rsrc = CalDAVFile(fpath)

        def donePut(response):
            self.assertTrue(response.code == responsecode.CREATED)
            displayname = DisplayName.fromString("adisplayname")
            rsrc.writeDeadProperty(displayname)
            
            # Check index
            index = sqlPropertyStore(rsrc, False)
            self._testProperty(index, displayname)
            
            def doneCopy(response):
                response = IResponse(response)
    
                if response.code != responsecode.CREATED:
                    self.fail("COPY response %s != %s" % (response.code, responsecode.NO_CONTENT))
            
                if not os.path.exists(fpath):
                    self.fail("COPY removed original path %s" % (fpath,))

                if not os.path.exists(fpath_new):
                    self.fail("COPY did not create new path %s" % (fpath_new,))

                self._testProperty(index, displayname, "on original resource")

                rsrc_new = CalDAVFile(fpath_new)
                index_new = sqlPropertyStore(rsrc_new, False)
                self._testProperty(index_new, displayname, "on new resource")

            # Copy resource and test
            request = SimpleRequest(self.site, "COPY", "/%sfile.ics" % (src,))
            request.headers.setHeader("destination", "/%scopy.ics" % (dst,))
            return self.send(request, doneCopy)

        stream = file(os.path.join(self.data_dir, "Holidays", "C318AA54-1ED0-11D9-A5E0-000A958A3252.ics"))
        try: calendar = str(Component.fromStream(stream))
        finally: stream.close()

        request = SimpleRequest(self.site, "PUT", "/%sfile.ics" % (src,))
        request.stream = MemoryStream(calendar)
        return self.send(request, donePut)
    
    def test_copyresource(self):
        return self._do_copy("", "")

    def test_copycalendarresource(self):

        def doneMake2(response):
            self.assertTrue(response.code == responsecode.CREATED)
            return self._do_copy("calendar1/", "calendar2/")

        def doneMake1(response):
            self.assertTrue(response.code == responsecode.CREATED)
            request = SimpleRequest(self.site, "MKCALENDAR", "/calendar2/")
            return self.send(request, doneMake2)

        # Make a calendar
        request = SimpleRequest(self.site, "MKCALENDAR", "/calendar1/")
        return self.send(request, doneMake1)

    def _do_move(self, src, dst):
        fpath = self.docroot
        if src:
            fpath = os.path.join(fpath, src)
        fpath = os.path.join(fpath, "file.ics")
        fpath_new = self.docroot
        if dst:
            fpath_new = os.path.join(fpath_new, dst)
        fpath_new = os.path.join(fpath_new, "move.ics")
        rsrc = CalDAVFile(fpath)

        def donePut(response):
            self.assertTrue(response.code == responsecode.CREATED)
            displayname = DisplayName.fromString("adisplayname")
            rsrc.writeDeadProperty(displayname)
            
            # Check index
            index = sqlPropertyStore(rsrc, False)
            self._testProperty(index, displayname)
            
            def doneMove(response):
                response = IResponse(response)
    
                if response.code != responsecode.CREATED:
                    self.fail("MOVE response %s != %s" % (response.code, responsecode.NO_CONTENT))
            
                if os.path.exists(fpath):
                    self.fail("MOVE did not remove original path %s" % (fpath,))

                if not os.path.exists(fpath_new):
                    self.fail("MOVE did not create new path %s" % (fpath_new,))

                self.assertFalse(index.contains(displayname.qname()),
                                 msg="Property %s exists after resource was moved." % displayname)

                rsrc_new = CalDAVFile(fpath_new)
                index_new = sqlPropertyStore(rsrc_new, False)
                self._testProperty(index_new, displayname, "on new resource")

            def work():
                # Move resource and test
                request = SimpleRequest(self.site, "MOVE", "/%sfile.ics" % (src,))
                request.headers.setHeader("destination", "/%smove.ics" % (dst,))
                yield (request, doneMove)

            return serialize(self.send, work())

        stream = file(os.path.join(self.data_dir, "Holidays", "C318AA54-1ED0-11D9-A5E0-000A958A3252.ics"))
        try: calendar = str(Component.fromStream(stream))
        finally: stream.close()

        request = SimpleRequest(self.site, "PUT", "/%sfile.ics" % (src,))
        request.stream = MemoryStream(calendar)
        return self.send(request, donePut)
    
    def test_moveresource(self):
        return self._do_move("", "")

    def test_movecalendarresource(self):

        def doneMake2(response):
            self.assertTrue(response.code == responsecode.CREATED)
            return self._do_move("calendar1/", "calendar2/")

        def doneMake1(response):
            self.assertTrue(response.code == responsecode.CREATED)
            request = SimpleRequest(self.site, "MKCALENDAR", "/calendar2/")
            return self.send(request, doneMake2)

        # Make a calendar
        request = SimpleRequest(self.site, "MKCALENDAR", "/calendar1/")
        return self.send(request, doneMake1)
