##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
#
# This file contains Original Code and/or Modifications of Original Code
# as defined in and that are subject to the Apple Public Source License
# Version 2.0 (the 'License'). You may not use this file except in
# compliance with the License. Please obtain a copy of the License at
# http://www.opensource.apple.com/apsl/ and read it before using this
# file.
# 
# The Original Code and all software distributed under the License are
# distributed on an 'AS IS' basis, WITHOUT WARRANTY OF ANY KIND, EITHER
# EXPRESS OR IMPLIED, AND APPLE HEREBY DISCLAIMS ALL SUCH WARRANTIES,
# INCLUDING WITHOUT LIMITATION, ANY WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE, QUIET ENJOYMENT OR NON-INFRINGEMENT.
# Please see the License for the specific language governing rights and
# limitations under the License.
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##

from twistedcaldav.ical import Component
from twisted.trial.unittest import SkipTest

import os
import shutil

from twisted.web2 import responsecode
from twisted.web2.iweb import IResponse
from twisted.web2.stream import MemoryStream
from twisted.web2.dav import davxml
from twisted.web2.dav.fileop import rmdir
from twisted.web2.dav.test.util import SimpleRequest
from twisted.web2.dav.util import davXMLFromStream

import twistedcaldav.test.util
from twistedcaldav import caldavxml
from twistedcaldav import ical
from twistedcaldav.index import db_basename

class FreeBusyQuery (twistedcaldav.test.util.TestCase):
    """
    free-busy-query REPORT
    """
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    holidays_dir = os.path.join(data_dir, "Holidays")

    def test_free_busy_basic(self):
        """
        Free-busy on plain events.
        (CalDAV-access-09, section 7.8)
        """
        raise SkipTest("test unimplemented")

    def test_free_busy_recurring(self):
        """
        Free-busy on recurring events.
        (CalDAV-access-09, section 7.8)
        """
        raise SkipTest("test unimplemented")

    def test_free_busy_statustransp(self):
        """
        SFree-busy on events with different STATUS/TRANSP property values.
        (CalDAV-access-09, section 7.8)
        """
        raise SkipTest("test unimplemented")

    def test_free_busy_free_busy(self):
        """
        Free-busy on free busy components.
        (CalDAV-access-09, section 7.8)
        """
        raise SkipTest("test unimplemented")

    def simple_free_busy_query(self, cal_uri, start, end):
        
        query_timerange = caldavxml.TimeRange(
            start=start,
            end=end,
        )

        query = caldavxml.FreeBusyQuery(query_timerange,)
        
        def got_calendar(calendar):
            pass

        return self.free_busy_query(cal_uri, query, got_calendar)

    def free_busy_query(self, calendar_uri, query, got_calendar):
        calendar_path = os.path.join(self.docroot, calendar_uri[1:])

        if os.path.exists(calendar_path): rmdir(calendar_path)

        def do_report(response):
            response = IResponse(response)

            if response.code != responsecode.CREATED:
                self.fail("MKCALENDAR failed: %s" % (response.code,))

            # Add holiday events to calendar
            # We're cheating by simply copying the files in
            for filename in os.listdir(self.holidays_dir):
                if os.path.splitext(filename)[1] != ".ics": continue
                path = os.path.join(self.holidays_dir, filename)
                shutil.copy(path, calendar_path)

            # Delete the index because we cheated
            index_path = os.path.join(calendar_path, db_basename)
            if os.path.isfile(index_path): os.remove(index_path)

            request = SimpleRequest(self.site, "REPORT", calendar_uri)
            request.stream = MemoryStream(query.toxml())

            def do_test(response):
                response = IResponse(response)

                if response.code != responsecode.OK:
                    self.fail("REPORT failed: %s" % (response.code,))

                return Component.fromIStream(response.stream).addCallback(got_calendar)

            return self.send(request, do_test, calendar_path)

        request = SimpleRequest(self.site, "MKCALENDAR", calendar_uri)

        return self.send(request, do_report, calendar_path)
