##
# Copyright (c) 2009 Apple Inc. All rights reserved.
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

from twisted.web2.http import HTTPError
import twistedcaldav.test.util
from twistedcaldav.datafilters.privateevents import PrivateEventFilter

class PrivateEventsTest (twistedcaldav.test.util.TestCase):

    def test_public_default(self):
        
        data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
ATTENDEE:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
ORGANIZER;CN=User 01:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")
        
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_PUBLIC, True).filter(data)), data)
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_PUBLIC, False).filter(data)), data)

    def test_public(self):
        
        data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
X-CALENDARSERVER-ACCESS:PUBLIC
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
ATTENDEE:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
ORGANIZER;CN=User 01:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")
        
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_PUBLIC, True).filter(data)), data)
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_PUBLIC, False).filter(data)), data)

    def test_private(self):
        
        data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
X-CALENDARSERVER-ACCESS:PRIVATE
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
ATTENDEE:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
ORGANIZER;CN=User 01:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")
        
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_PRIVATE, True).filter(data)), data)
        pfilter = PrivateEventFilter(PrivateEventFilter.ACCESS_PRIVATE, False)
        self.assertRaises(HTTPError, pfilter.filter, data)

    def test_confidential(self):
        
        data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
X-CALENDARSERVER-ACCESS:CONFIDENTIAL
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
ATTENDEE:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
ORGANIZER;CN=User 01:mailto:user1@example.com
SUMMARY:Confidential
DESCRIPTION:In confidence
LOCATION:My office
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")
        
        filtered = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
END:VEVENT
X-CALENDARSERVER-ACCESS:CONFIDENTIAL
END:VCALENDAR
""".replace("\n", "\r\n")
        
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_CONFIDENTIAL, True).filter(data)), data)
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_CONFIDENTIAL, False).filter(data)), filtered)

    def test_restricted(self):
        
        data = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
X-CALENDARSERVER-ACCESS:CONFIDENTIAL
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
ATTENDEE:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
ORGANIZER;CN=User 01:mailto:user1@example.com
SUMMARY:Confidential
DESCRIPTION:In confidence
LOCATION:My office
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n")
        
        filtered = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890
DTSTART:20080601T120000Z
DTEND:20080601T130000Z
LOCATION:My office
SUMMARY:Confidential
END:VEVENT
X-CALENDARSERVER-ACCESS:CONFIDENTIAL
END:VCALENDAR
""".replace("\n", "\r\n")
        
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_RESTRICTED, True).filter(data)), data)
        self.assertEqual(str(PrivateEventFilter(PrivateEventFilter.ACCESS_RESTRICTED, False).filter(data)), filtered)
