##
# Copyright (c) 2005-2013 Apple Inc. All rights reserved.
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

from twisted.internet.defer import inlineCallbacks
from twisted.trial import unittest

from twistedcaldav import memcacher
from twistedcaldav.ical import Component
from twistedcaldav.stdconfig import config

from txdav.caldav.datastore.scheduling.processing import ImplicitProcessor


class FakeImplicitProcessor(ImplicitProcessor):
    """
    A fake ImplicitProcessor that tracks batch refreshes.
    """

    def __init__(self):
        self.batches = 0


    def _enqueueBatchRefresh(self):
        self.batches += 1



class FakePrincipal(object):

    def __init__(self, cuaddr):
        self.cuaddr = cuaddr


    def calendarUserAddresses(self):
        return (self.cuaddr,)



class BatchRefresh (unittest.TestCase):
    """
    iCalendar support tests
    """

    def setUp(self):
        super(BatchRefresh, self).setUp()
        config.Memcached.Pools.Default.ClientEnabled = False
        config.Memcached.Pools.Default.ServerEnabled = False
        memcacher.Memcacher.allowTestCache = True
        memcacher.Memcacher.memoryCacheInstance = None


    @inlineCallbacks
    def test_queueAttendeeUpdate_no_refresh(self):

        self.patch(config.Scheduling.Options, "AttendeeRefreshBatch", 5)

        calendar = Component.fromString("""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
ORGANIZER:urn:uuid:user01
ATTENDEE:urn:uuid:user01
ATTENDEE:urn:uuid:user02
END:VEVENT
END:VCALENDAR
""")
        processor = FakeImplicitProcessor()
        processor.txn = ""
        processor.uid = "12345-67890"
        processor.recipient_calendar = calendar
        yield processor.queueAttendeeUpdate(("urn:uuid:user02", "urn:uuid:user01",))
        self.assertEqual(processor.batches, 0)


    @inlineCallbacks
    def test_queueAttendeeUpdate_with_refresh(self):

        self.patch(config.Scheduling.Options, "AttendeeRefreshBatch", 5)

        calendar = Component.fromString("""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
ORGANIZER:urn:uuid:user01
ATTENDEE:urn:uuid:user01
ATTENDEE:urn:uuid:user02
ATTENDEE:urn:uuid:user03
END:VEVENT
END:VCALENDAR
""")
        processor = FakeImplicitProcessor()
        processor.txn = ""
        processor.uid = "12345-67890"
        processor.recipient_calendar = calendar
        yield processor.queueAttendeeUpdate(("urn:uuid:user02", "urn:uuid:user01",))
        self.assertEqual(processor.batches, 1)
