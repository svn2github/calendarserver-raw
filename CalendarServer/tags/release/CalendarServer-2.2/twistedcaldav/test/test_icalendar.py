##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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

import os
import datetime
from dateutil.tz import tzutc
from difflib import unified_diff
import itertools

from twisted.trial.unittest import SkipTest

from twistedcaldav.ical import Component, parse_date, parse_datetime,\
    parse_date_or_datetime, parse_duration, Property
from twistedcaldav.instance import InvalidOverriddenInstanceError
import twistedcaldav.test.util

from vobject.icalendar import utc

class iCalendar (twistedcaldav.test.util.TestCase):
    """
    iCalendar support tests
    """
    data_dir = os.path.join(os.path.dirname(__file__), "data")

    def test_component(self):
        """
        Properties in components
        """
        calendar = Component.fromStream(file(os.path.join(self.data_dir, "Holidays.ics")))
        if calendar.name() != "VCALENDAR": self.fail("Calendar is not a VCALENDAR")

        for subcomponent in calendar.subcomponents():
            if subcomponent.name() == "VEVENT":
                if not subcomponent.propertyValue("UID")[8:] == "-1ED0-11D9-A5E0-000A958A3252":
                    self.fail("Incorrect UID in component: %r" % (subcomponent,))
                if not subcomponent.propertyValue("DTSTART"):
                    self.fail("No DTSTART in component: %r" % (subcomponent,))
            else:
                SkipTest("test unimplemented")

    def test_component_equality(self):
        for filename in (
            os.path.join(self.data_dir, "Holidays", "C318A4BA-1ED0-11D9-A5E0-000A958A3252.ics"),
            os.path.join(self.data_dir, "Holidays.ics"),
        ):
            data = file(filename).read()

            calendar1 = Component.fromString(data)
            calendar2 = Component.fromString(data)

            self.assertEqual(calendar1, calendar2)

    def test_component_validate(self):
        """
        CalDAV resource validation.
        """
        calendar = Component.fromStream(file(os.path.join(self.data_dir, "Holidays.ics")))
        try: calendar.validateForCalDAV()
        except ValueError: pass
        else: self.fail("Monolithic iCalendar shouldn't validate for CalDAV")

        resource_dir = os.path.join(self.data_dir, "Holidays")
        for filename in resource_dir:
            if os.path.splitext(filename)[1] != ".ics": continue
            filename = os.path.join(resource_dir, filename)

            calendar = Component.fromStream(file(filename))
            try: calendar.validateForCalDAV()
            except ValueError: self.fail("Resource iCalendar %s didn't validate for CalDAV" % (filename,))

    def test_component_timeranges(self):
        """
        Component time range query.
        """
        #
        # This event is the Independence Day
        #
        calendar = Component.fromStream(file(os.path.join(self.data_dir, "Holidays", "C318A4BA-1ED0-11D9-A5E0-000A958A3252.ics")))

        year = 2004

        instances = calendar.expandTimeRanges(datetime.date(2100, 1, 1))
        for key in instances:
            instance = instances[key]
            start = instance.start
            end = instance.end
            self.assertEqual(start, datetime.datetime(year, 7, 4))
            self.assertEqual(end  , datetime.datetime(year, 7, 5))
            if year == 2050: break
            year += 1

        self.assertEqual(year, 2050)

        #
        # This event is the Thanksgiving holiday (2 days)
        #
        calendar = Component.fromStream(file(os.path.join(self.data_dir, "Holidays", "C318ABFE-1ED0-11D9-A5E0-000A958A3252.ics")))
        results = {
            2004: (11, 25, 27),
            2005: (11, 24, 26),
            2006: (11, 23, 25),
            2007: (11, 22, 24),
            2008: (11, 27, 29),
        }
        year = 2004

        instances = calendar.expandTimeRanges(datetime.date(2100, 1, 1))
        for key in instances:
            instance = instances[key]
            start = instance.start
            end = instance.end
            if year in results:
                self.assertEqual(start, datetime.datetime(year, results[year][0], results[year][1]))
                self.assertEqual(end  , datetime.datetime(year, results[year][0], results[year][2]))
            if year == 2050: break
            year += 1

        self.assertEqual(year, 2050)

        #
        # This event is Father's Day
        #
        calendar = Component.fromStream(file(os.path.join(self.data_dir, "Holidays", "C3186426-1ED0-11D9-A5E0-000A958A3252.ics")))
        results = {
            2002: (6, 16, 17),
            2003: (6, 15, 16),
            2004: (6, 20, 21),
            2005: (6, 19, 20),
            2006: (6, 18, 19),
        }
        year = 2002

        instances = calendar.expandTimeRanges(datetime.date(2100, 1, 1))
        for key in instances:
            instance = instances[key]
            start = instance.start
            end = instance.end
            if year in results:
                self.assertEqual(start, datetime.datetime(year, results[year][0], results[year][1]))
                self.assertEqual(end  , datetime.datetime(year, results[year][0], results[year][2]))
            if year == 2050: break
            year += 1

        self.assertEqual(year, 2050)

    def test_component_timerange(self):
        """
        Component summary time range query.
        """
        calendar = Component.fromStream(file(os.path.join(self.data_dir, "Holidays", "C318ABFE-1ED0-11D9-A5E0-000A958A3252.ics")))

        instances = calendar.expandTimeRanges(datetime.date(2100, 1, 1))
        for key in instances:
            instance = instances[key]
            start = instance.start
            end = instance.end
            self.assertEqual(start, datetime.datetime(2004, 11, 25))
            self.assertEqual(end, datetime.datetime(2004, 11, 27))
            break;

    #test_component_timerange.todo = "recurrence expansion should give us no end date here"

    def test_parse_date(self):
        """
        parse_date()
        """
        self.assertEqual(parse_date("19970714"), datetime.date(1997, 7, 14))

    def test_parse_datetime(self):
        """
        parse_datetime()
        """
        try: parse_datetime("19980119T2300")
        except ValueError: pass
        else: self.fail("Invalid DATE-TIME should raise ValueError")

        dt = parse_datetime("19980118T230000")
        self.assertEqual(dt, datetime.datetime(1998, 1, 18, 23, 0))
        self.assertNot(dt.tzinfo)

        dt = parse_datetime("19980119T070000Z")
        self.assertEqual(dt, datetime.datetime(1998, 1, 19, 07, 0, tzinfo=utc))

    def test_parse_date_or_datetime(self):
        """
        parse_date_or_datetime()
        """
        self.assertEqual(parse_date_or_datetime("19970714"), datetime.date(1997, 7, 14))

        try: parse_date_or_datetime("19980119T2300")
        except ValueError: pass
        else: self.fail("Invalid DATE-TIME should raise ValueError")

        dt = parse_date_or_datetime("19980118T230000")
        self.assertEqual(dt, datetime.datetime(1998, 1, 18, 23, 0))
        self.assertNot(dt.tzinfo)

        dt = parse_date_or_datetime("19980119T070000Z")
        self.assertEqual(dt, datetime.datetime(1998, 1, 19, 07, 0, tzinfo=utc))

    def test_parse_duration(self):
        """
        parse_duration()
        """
        self.assertEqual(parse_duration( "P15DT5H0M20S"), datetime.timedelta(days= 15, hours= 5, minutes=0, seconds= 20))
        self.assertEqual(parse_duration("+P15DT5H0M20S"), datetime.timedelta(days= 15, hours= 5, minutes=0, seconds= 20))
        self.assertEqual(parse_duration("-P15DT5H0M20S"), datetime.timedelta(days=-15, hours=-5, minutes=0, seconds=-20))

        self.assertEqual(parse_duration("P7W"), datetime.timedelta(weeks=7))

    def test_correct_attendee_properties(self):
        
        data = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ATTENDEE:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
"""

        component = Component.fromString(data)
        self.assertEqual([p.value() for p in component.getAttendeeProperties(("mailto:user2@example.com",))], ["mailto:user2@example.com",])

    def test_empty_attendee_properties(self):
        
        data = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ATTENDEE:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
"""

        component = Component.fromString(data)
        self.assertEqual(component.getAttendeeProperties(("user3@example.com",)), [])

    def test_organizers_by_instance(self):
        
        data = (
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    (None, None),
                )
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    ("mailto:user1@example.com", None),
                )
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ORGANIZER:mailto:user2@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
""",
                ()
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    ("mailto:user1@example.com", None),
                    ("mailto:user1@example.com", datetime.datetime(2008, 11, 14, 0, 0, tzinfo=tzutc()))
                )
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20091114T000000Z
DTSTART:20071114T020000Z
ORGANIZER:mailto:user3@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    ("mailto:user1@example.com", None),
                    ("mailto:user3@example.com", datetime.datetime(2009, 11, 14, 0, 0, tzinfo=tzutc()))
                )
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20091114T000000Z
DTSTART:20071114T020000Z
ORGANIZER:mailto:user3@example.com
ORGANIZER:mailto:user4@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    ("mailto:user1@example.com", None),
                )
            ),
        )
        
        for caldata, result in data:
            component = Component.fromString(caldata)
            self.assertEqual(component.getOrganizersByInstance(), result)

    def test_attendees_by_instance(self):
        
        data = (
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                ()
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    ("mailto:user2@example.com", None),
                )
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
ATTENDEE:mailto:user3@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    ("mailto:user2@example.com", None),
                    ("mailto:user3@example.com", None),
                )
            ),
            (
                """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:12345-67890
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ORGANIZER:mailto:user1@example.com
ATTENDEE:mailto:user2@example.com
ATTENDEE:mailto:user3@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    ("mailto:user2@example.com", None),
                    ("mailto:user2@example.com", datetime.datetime(2008, 11, 14, 0, 0, tzinfo=tzutc())),
                    ("mailto:user3@example.com", datetime.datetime(2008, 11, 14, 0, 0, tzinfo=tzutc()))
                )
            ),
        )
        
        for caldata, result in data:
            component = Component.fromString(caldata)
            self.assertEqual(component.getAttendeesByInstance(), result)

    def test_set_parameter_value(self):
        data = (
            # ATTENDEE - no existing parameter
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE:mailto:user02@example.com
ORGANIZER:mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE;SCHEDULE-STATUS="2.0;OK":mailto:user02@example.com
ORGANIZER:mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    "SCHEDULE-STATUS",
                    "2.0;OK",
                    "ATTENDEE",
                    "mailto:user02@example.com",
                ),
            ),
            # ATTENDEE - existing parameter
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE;SCHEDULE-STATUS="5.0;BAD":mailto:user02@example.com
ORGANIZER:mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE;SCHEDULE-STATUS="2.0;OK":mailto:user02@example.com
ORGANIZER:mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    "SCHEDULE-STATUS",
                    "2.0;OK",
                    "ATTENDEE",
                    "mailto:user02@example.com",
                ),
            ),
            # ORGANIZER - no existing parameter
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE:mailto:user02@example.com
ORGANIZER:mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE:mailto:user02@example.com
ORGANIZER;SCHEDULE-STATUS="2.0;OK":mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    "SCHEDULE-STATUS",
                    "2.0;OK",
                    "ORGANIZER",
                    "mailto:user01@example.com",
                ),
            ),
            # ORGANIZER - existing parameter
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE:mailto:user02@example.com
ORGANIZER;SCHEDULE-STATUS="5.0;BAD":mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ATTENDEE:mailto:user01@example.com
ATTENDEE:mailto:user02@example.com
ORGANIZER;SCHEDULE-STATUS="2.0;OK":mailto:user01@example.com
END:VEVENT
END:VCALENDAR
""",
                (
                    "SCHEDULE-STATUS",
                    "2.0;OK",
                    "ORGANIZER",
                    "mailto:user01@example.com",
                ),
            ),
        )

        for original, result, args in data:
            component = Component.fromString(original)
            component.setParameterToValueForPropertyWithValue(*args)
            self.assertEqual(result, str(component).replace("\r", ""))        

    def test_add_property(self):
        data = (
            # Simple component
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
REQUEST-STATUS:2.0;Success
END:VEVENT
END:VCALENDAR
""",
            ),
            # Complex component
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T020000Z
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
REQUEST-STATUS:2.0;Success
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T020000Z
REQUEST-STATUS:2.0;Success
END:VEVENT
END:VCALENDAR
""",
            ),
        )

        for original, result in data:
            component = Component.fromString(original)
            component.addPropertyToAllComponents(Property("REQUEST-STATUS", ["2.0", "Success"]))
            self.assertEqual(result, str(component).replace("\r", ""))        

    def test_attendees_views(self):
        
        data = (
            # Simple component, no Attendees - no filtering
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                ()
            ),

            # Simple component, no Attendees - filtering
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-2
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
END:VCALENDAR
""",
                ("mailto:user01@example.com",)
            ),

            # Simple component, with one attendee - filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                ("mailto:user2@example.com",)
            ),

            # Simple component, with one attendee - no filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-4
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
END:VCALENDAR
""",
                ("mailto:user3@example.com",)
            ),

            # Recurring component with one instance, each with one attendee - filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                ("mailto:user2@example.com",)
            ),

            # Recurring component with one instance, each with one attendee - no filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-4
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
END:VCALENDAR
""",
                ("mailto:user3@example.com",)
            ),        

            # Recurring component with one instance, master with one attendee, instance without attendee - filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
EXDATE:20081114T000000Z
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
END:VCALENDAR
""",
                ("mailto:user2@example.com",)
            ),

            # Recurring component with one instance, master with one attendee, instance without attendee - no filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-4
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
END:VCALENDAR
""",
                ("mailto:user3@example.com",)
            ),

            # Recurring component with one instance, master without attendee, instance with attendee - filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                ("mailto:user2@example.com",)
            ),

            # Recurring component with one instance, master without attendee, instance with attendee - no filtering match
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-4
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
END:VCALENDAR
""",
                ("mailto:user3@example.com",)
            ),
        )
        
        for original, filtered, attendees in data:
            component = Component.fromString(original)
            component.attendeesView(attendees)
            self.assertEqual(filtered, str(component).replace("\r", ""))

    def test_all_but_one_attendee(self):
        
        data = (
            # One component, no attendees
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                "mailto:user02@example.com",
            ),

            # One component, one attendee - removed
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-2
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-2
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                "mailto:user1@example.com",
            ),

            # One component, one attendee - left
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                "mailto:user2@example.com",
            ),

            # One component, two attendees - none left
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-4
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ATTENDEE:mailto:user3@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-4
DTSTART:20071114T000000Z
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                "mailto:user1@example.com",
            ),

            # One component, two attendees - one left
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-5
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ATTENDEE:mailto:user3@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-5
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                "mailto:user2@example.com",
            ),

        )
        
        for original, result, attendee in data:
            component = Component.fromString(original)
            component.removeAllButOneAttendee(attendee)
            self.assertEqual(result, str(component).replace("\r", ""))

    def test_filter_properties_keep(self):
        
        data = (
            # One component
            (
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
SUMMARY:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                ("UID", "RECURRENCE-ID", "SEQUENCE", "DTSTAMP", "ORGANIZER", "ATTENDEE",),
            ),

            # Multiple components
            (
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                ("UID", "RECURRENCE-ID", "SEQUENCE", "DTSTAMP", "ORGANIZER", "ATTENDEE",),
            ),

        )
        
        for original, result, keep_properties in data:
            component = Component.fromString(original)
            component.filterProperties(keep=keep_properties)
            self.assertEqual(result, str(component).replace("\r", ""))

    def test_filter_properties_remove(self):
        
        data = (
            # One component
            (
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
SUMMARY:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                ("DTSTART", "SUMMARY",),
            ),

            # Multiple components
            (
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                ("DTSTART", "SUMMARY",),
            ),

        )
        
        for original, result, remove_properties in data:
            component = Component.fromString(original)
            component.filterProperties(remove=remove_properties)
            self.assertEqual(result, str(component).replace("\r", ""))

    def test_remove_alarms(self):
        
        data = (
            # One component, no alarms
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
            ),

            # One component, one alarm
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-2
DTSTART:20071114T000000Z
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-2
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
            ),

            # Multiple components, one alarm
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
            ),

            # Multiple components, multiple alarms
            (
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Test
TRIGGER;RELATED=START:-PT10M
END:VALARM
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-3
DTSTART:20071114T000000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
RRULE:FREQ=YEARLY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890
RECURRENCE-ID:20081114T000000Z
DTSTART:20071114T010000Z
ATTENDEE:mailto:user2@example.com
ORGANIZER:mailto:user1@example.com
END:VEVENT
END:VCALENDAR
""",
            ),
        )
        
        for original, result in data:
            component = Component.fromString(original)
            component.removeAlarms()
            self.assertEqual(result, str(component).replace("\r", ""))

    def test_expand_instances(self):
        
        data = (
            (
                "Non recurring",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                (datetime.datetime(2007, 11, 14, 0, 0, 0, tzinfo=tzutc()),)
            ),
            (
                "Simple recurring",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2007, 11, 14, 0, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2007, 11, 15, 0, 0, 0, tzinfo=tzutc()),
                )
            ),
            (
                "Recurring with RDATE",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY;COUNT=2
RDATE:20071116T010000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2007, 11, 14, 0, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2007, 11, 15, 0, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2007, 11, 16, 1, 0, 0, tzinfo=tzutc()),
                )
            ),
            (
                "Recurring with EXDATE",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY;COUNT=3
EXDATE:20071115T000000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2007, 11, 14, 0, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2007, 11, 16, 0, 0, 0, tzinfo=tzutc()),
                )
            ),
            (
                "Recurring with EXDATE on DTSTART",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY;COUNT=3
EXDATE:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2007, 11, 15, 0, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2007, 11, 16, 0, 0, 0, tzinfo=tzutc()),
                )
            ),
            (
                "Recurring with override",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2007, 11, 14, 0, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2007, 11, 15, 1, 0, 0, tzinfo=tzutc()),
                )
            ),
            (
                "Recurring with invalid override",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T010000Z
DTSTART:20071115T000000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                None
            ),
        )
        
        for description, original, results in data:
            component = Component.fromString(original)
            if results is None:
                self.assertRaises(InvalidOverriddenInstanceError, component.expandTimeRanges, datetime.date(2100, 1, 1))
            else:
                instances = component.expandTimeRanges(datetime.date(2100, 1, 1))
                self.assertTrue(len(instances.instances) == len(results), "%s: wrong number of instances" % (description,))
                for instance in instances:
                    self.assertTrue(instances[instance].start in results, "%s: %s missing" % (description, instance,))
       
    def test_has_property_in_any_component(self):
        
        data = (
            (
                "Single component - True",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("DTSTART",),
                True,
            ),
            (
                "Single component - False",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("DTEND",),
                False,
            ),
            (
                "Multiple components - True in both",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("DTSTART",),
                True,
            ),
            (
                "Multiple components - True in one",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("RECURRENCE-ID",),
                True,
            ),
            (
                "Multiple components - False",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("DTEND",),
                False,
            ),
            (
                "Multiple components/propnames - True in both",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("DTSTART", "RECURRENCE-ID",),
                True,
            ),
            (
                "Multiple components - True in one",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("STATUS", "RECURRENCE-ID",),
                True,
            ),
            (
                "Multiple components - False",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
END:VEVENT
END:VCALENDAR
""",
                ("STATUS", "DTEND",),
                False,
            ),
        )
        
        for description, caldata, propnames, result in data:
            component = Component.fromString(caldata)
            self.assertTrue(component.hasPropertyInAnyComponent(propnames) == result, "Property name match incorrect: %s" % (description,))
       
    def test_transfer_properties(self):
        
        data = (
            (
                "Non recurring - one property",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM1:True
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM2:True
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM1:True
X-ITEM2:True
END:VEVENT
END:VCALENDAR
""",
            ("X-ITEM2",),
            ),
            (
                "Non recurring - two properties",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM1:True
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM2:True
X-ITEM3:True
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM1:True
X-ITEM2:True
X-ITEM3:True
END:VEVENT
END:VCALENDAR
""",
            ("X-ITEM2","X-ITEM3",),
            ),
            (
                "Non recurring - two properties - one overlap",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM1:True
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM2:True
X-ITEM1:False
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
X-ITEM1:True
X-ITEM2:True
X-ITEM1:False
END:VEVENT
END:VCALENDAR
""",
            ("X-ITEM2","X-ITEM1",),
            ),
            (
                "Non recurring - one property",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
X-ITEM1:True
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
X-ITEM1:False
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
X-ITEM2:True
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
X-ITEM2:False
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
X-ITEM1:True
X-ITEM2:True
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
X-ITEM1:False
X-ITEM2:False
END:VEVENT
END:VCALENDAR
""",
            ("X-ITEM2",),
            ),
            (
                "Non recurring - new override, one property",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
X-ITEM1:True
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
X-ITEM1:False
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
X-ITEM2:True
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
DURATION:P1H
RRULE:FREQ=DAILY
X-ITEM1:True
X-ITEM2:True
END:VEVENT
BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20071115T000000Z
DTSTART:20071115T010000Z
DURATION:P1H
X-ITEM1:False
X-ITEM2:True
END:VEVENT
END:VCALENDAR
""",
            ("X-ITEM2",),
            ),
        )
        
        for description, transfer_to, transfer_from, result, propnames in data:
            component_to = Component.fromString(transfer_to)
            component_from = Component.fromString(transfer_from)
            component_result = Component.fromString(result)
            component_to.transferProperties(component_from, propnames)
            self.assertEqual(str(component_to), str(component_result), "%s: mismatch" % (description,))

    def test_normalize_all(self):
        
        data = (
            (
                "1.1",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114T000000Z
SEQUENCE:0
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
            ),
            (
                "1.2",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114T000000Z
TRANSP:OPAQUE
ORGANIZER:mailto:user01@example.com
ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:mailto:user02@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:user03@example.com
ATTENDEE;RSVP=FALSE:mailto:user04@example.com
SEQUENCE:1
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ORGANIZER:mailto:user01@example.com
ATTENDEE;RSVP=TRUE:mailto:user02@example.com
ATTENDEE:mailto:user03@example.com
ATTENDEE:mailto:user04@example.com
SEQUENCE:1
END:VEVENT
END:VCALENDAR
""",
            ),
            (
                "1.3",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114T000000Z
RRULE:FREQ=WEEKLY;WKST=SU;INTERVAL=1;BYDAY=MO,WE,FR
TRANSP:OPAQUE
ORGANIZER:mailto:user01@example.com
ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:mailto:user02@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:user03@example.com
ATTENDEE;RSVP=FALSE:mailto:user04@example.com
SEQUENCE:1
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
ORGANIZER:mailto:user01@example.com
ATTENDEE;RSVP=TRUE:mailto:user02@example.com
ATTENDEE:mailto:user03@example.com
ATTENDEE:mailto:user04@example.com
RRULE:BYDAY=MO,WE,FR;FREQ=WEEKLY;INTERVAL=1;WKST=SU
SEQUENCE:1
END:VEVENT
END:VCALENDAR
""",
            ),
        )
        
        for title, original, result in data:
            ical1 = Component.fromString(original)
            ical1.normalizeAll()
            ical1 = str(ical1)
            ical2 = Component.fromString(result)
            ical2 = str(ical2)
            diff = "\n".join(unified_diff(ical1.split("\n"), ical2.split("\n")))
            self.assertEqual(str(ical1), str(ical2), "Failed comparison: %s\n%s" % (title, diff,))

    def test_normalize_attachments(self):
        
        data = (
            (
                "1.1 - no attach",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
END:VEVENT
END:VCALENDAR
""",
            ),
            (
                "1.2 - attach with no dropbox",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
ATTACH:http://example.com/file.txt
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
ATTACH:http://example.com/file.txt
END:VEVENT
END:VCALENDAR
""",
            ),
            (
                "1.3 - attach with dropbox",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
ATTACH:http://example.com/calendars/user.dropbox/file.txt
X-APPLE-DROPBOX:/calendars/user.dropbox
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
X-APPLE-DROPBOX:/calendars/user.dropbox
END:VEVENT
END:VCALENDAR
""",
            ),
            (
                "1.4 - attach with different dropbox",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
ATTACH:http://example.com/calendars/user.dropbox/file.txt
X-APPLE-DROPBOX:/calendars/user1.dropbox
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE-TIME:20071114
ATTACH:http://example.com/calendars/user.dropbox/file.txt
X-APPLE-DROPBOX:/calendars/user1.dropbox
END:VEVENT
END:VCALENDAR
""",
            ),
        )
        
        for title, original, result in data:
            ical1 = Component.fromString(original)
            ical1.normalizeAttachments()
            ical1 = str(ical1)
            ical2 = Component.fromString(result)
            ical2 = str(ical2)
            diff = "\n".join(unified_diff(ical1.split("\n"), ical2.split("\n")))
            self.assertEqual(str(ical1), str(ical2), "Failed comparison: %s\n%s" % (title, diff,))

    def test_recurring_unbounded(self):
        
        data = (
            (
                "1.1 - non-recurring",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T000000Z
DTEND:20090102T000000Z
END:VEVENT
END:VCALENDAR
""",
                False
            ),
            (
                "1.2 - recurring bounded COUNT",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T000000Z
DTEND:20090102T000000Z
RRULE:FREQ=DAILY;COUNT=2
END:VEVENT
END:VCALENDAR
""",
                False
            ),
            (
                "1.3 - recurring bounded UNTIL",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T000000Z
DTEND:20090102T000000Z
RRULE:FREQ=DAILY;UNTIL=20090108T000000Z
END:VEVENT
END:VCALENDAR
""",
                False
            ),
            (
                "1.4 - recurring unbounded",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T000000Z
DTEND:20090102T000000Z
RRULE:FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
                True
            ),
        )
        
        for title, calendar, expected in data:
            ical = Component.fromString(calendar)
            result = ical.isRecurringUnbounded()
            self.assertEqual(result, expected, "Failed recurring unbounded test: %s" % (title,))

    def test_derive_instance(self):
        
        data = (
            (
                "1.1 - simple",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
                datetime.datetime(2009, 1, 2, 8, 0, 0, tzinfo=tzutc()),
                """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090102T080000Z
DTSTART:20090102T080000Z
DTEND:20090102T090000Z
END:VEVENT
""",
            ),
            (
                "1.2 - simple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z
END:VEVENT
END:VCALENDAR
""",
                datetime.datetime(2009, 1, 2, 18, 0, 0, tzinfo=tzutc()),
                """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090102T180000Z
DTSTART:20090102T180000Z
DTEND:20090102T190000Z
END:VEVENT
""",
            ),
            (
                "1.3 - multiple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z,20090103T180000Z
RDATE:20090104T180000Z
END:VEVENT
END:VCALENDAR
""",
                datetime.datetime(2009, 1, 3, 18, 0, 0, tzinfo=tzutc()),
                """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090103T180000Z
DTSTART:20090103T180000Z
DTEND:20090103T190000Z
END:VEVENT
""",
            ),
            (
                "2.1 - invalid simple",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
                datetime.datetime(2009, 1, 2, 9, 0, 0, tzinfo=tzutc()),
                None,
            ),
            (
                "2.2 - invalid simple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z
END:VEVENT
END:VCALENDAR
""",
                datetime.datetime(2009, 1, 2, 19, 0, 0, tzinfo=tzutc()),
                None,
            ),
            (
                "2.3 - invalid multiple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z,20090103T180000Z
RDATE:20090104T180000Z
END:VEVENT
END:VCALENDAR
""",
                datetime.datetime(2009, 1, 3, 19, 0, 0, tzinfo=tzutc()),
                None,
            ),
            (
                "3.1 - simple all-day",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE:20090101
DTEND;VALUE=DATE:20090102
RRULE:FREQ=WEEKLY
END:VEVENT
END:VCALENDAR
""",
                datetime.date(2009, 1, 8),
                """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID;VALUE=DATE:20090108
DTSTART;VALUE=DATE:20090108
DTEND;VALUE=DATE:20090109
END:VEVENT
""",
            ),
            (
                "3.2 - simple all-day rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE:20090101
DTEND;VALUE=DATE:20090102
RRULE:FREQ=WEEKLY
RDATE;VALUE=DATE:20090103
END:VEVENT
END:VCALENDAR
""",
                datetime.date(2009, 1, 3),
                """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID;VALUE=DATE:20090103
DTSTART;VALUE=DATE:20090103
DTEND;VALUE=DATE:20090104
END:VEVENT
""",
            ),
            (
                "3.3 - multiple all-day rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE:20090101
DTEND;VALUE=DATE:20090102
RRULE:FREQ=WEEKLY
RDATE;VALUE=DATE:20090103,20090110
RDATE;VALUE=DATE:20090118
END:VEVENT
END:VCALENDAR
""",
                datetime.date(2009, 1, 10),
                """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID;VALUE=DATE:20090110
DTSTART;VALUE=DATE:20090110
DTEND;VALUE=DATE:20090111
END:VEVENT
""",
            ),
            (
                "4.1 - invalid all-day simple",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE:20090101
DTEND;VALUE=DATE:20090102
RRULE:FREQ=WEEKLY
END:VEVENT
END:VCALENDAR
""",
                datetime.date(2009, 1, 3),
                None,
            ),
            (
                "4.2 - invalid all-day simple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE:20090101
DTEND;VALUE=DATE:20090102
RRULE:FREQ=WEEKLY
RDATE;VALUE=DATE:20090104
END:VEVENT
END:VCALENDAR
""",
                datetime.date(2009, 1, 5),
                None,
            ),
            (
                "4.3 - invalid all-day multiple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART;VALUE=DATE:20090101
DTEND;VALUE=DATE:20090102
RRULE:FREQ=WEEKLY
RDATE;VALUE=DATE:20090104,20090111
RDATE;VALUE=DATE:20090118
END:VEVENT
END:VCALENDAR
""",
                datetime.datetime(2009, 1, 19),
                None,
            ),
        )
        
        for title, calendar, rid, result in data:
            if not title.startswith("3"):
                continue
            ical = Component.fromString(calendar)
            derived = ical.deriveInstance(rid)
            derived = str(derived).replace("\r", "") if derived else None
            self.assertEqual(derived, result, "Failed derive instance test: %s" % (title,))

    def test_derive_instance_multiple(self):
        
        data = (
            (
                "1.1 - simple",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2009, 1, 2, 8, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2009, 1, 4, 8, 0, 0, tzinfo=tzutc()),
                ),
                (
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090102T080000Z
DTSTART:20090102T080000Z
DTEND:20090102T090000Z
END:VEVENT
""",
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090104T080000Z
DTSTART:20090104T080000Z
DTEND:20090104T090000Z
END:VEVENT
""",
                ),
            ),
            (
                "1.2 - simple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2009, 1, 2, 18, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2009, 1, 4, 8, 0, 0, tzinfo=tzutc()),
                ),
                (
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090102T180000Z
DTSTART:20090102T180000Z
DTEND:20090102T190000Z
END:VEVENT
""",
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090104T080000Z
DTSTART:20090104T080000Z
DTEND:20090104T090000Z
END:VEVENT
""",
                ),
            ),
            (
                "1.3 - multiple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z,20090103T180000Z
RDATE:20090104T180000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2009, 1, 3, 18, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2009, 1, 5, 8, 0, 0, tzinfo=tzutc()),
                ),
                (
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090103T180000Z
DTSTART:20090103T180000Z
DTEND:20090103T190000Z
END:VEVENT
""",
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090105T080000Z
DTSTART:20090105T080000Z
DTEND:20090105T090000Z
END:VEVENT
""",
                ),
            ),
            (
                "2.1 - invalid simple",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2009, 1, 2, 9, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2009, 1, 3, 8, 0, 0, tzinfo=tzutc()),
                ),
                (
                    None,
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090103T080000Z
DTSTART:20090103T080000Z
DTEND:20090103T090000Z
END:VEVENT
""",
                ),
            ),
            (
                "2.2 - invalid simple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2009, 1, 2, 19, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2009, 1, 3, 8, 0, 0, tzinfo=tzutc()),
                ),
                (
                    None,
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090103T080000Z
DTSTART:20090103T080000Z
DTEND:20090103T090000Z
END:VEVENT
""",
                ),
            ),
            (
                "2.3 - invalid multiple rdate",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20090101T080000Z
DTEND:20090101T090000Z
RRULE:FREQ=DAILY
RDATE:20090102T180000Z,20090103T180000Z
RDATE:20090104T180000Z
END:VEVENT
END:VCALENDAR
""",
                (
                    datetime.datetime(2009, 1, 3, 19, 0, 0, tzinfo=tzutc()),
                    datetime.datetime(2009, 1, 3, 8, 0, 0, tzinfo=tzutc()),
                ),
                (
                    None,
                    """BEGIN:VEVENT
UID:12345-67890-1
RECURRENCE-ID:20090103T080000Z
DTSTART:20090103T080000Z
DTEND:20090103T090000Z
END:VEVENT
""",
                ),
            ),
        )
        
        for title, calendar, rids, results in data:
            ical = Component.fromString(calendar)
            for rid, result in itertools.izip(rids, results):
                derived = ical.deriveInstance(rid)
                derived = str(derived).replace("\r", "") if derived else None
                self.assertEqual(derived, result, "Failed derive instance test: %s" % (title,))

    def test_truncate_recurrence(self):
        
        data = (
            (
                "1.1 - no recurrence",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
END:VEVENT
END:VCALENDAR
""",
                None,
            ),
            (
                "1.2 - no truncation - count",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:FREQ=WEEKLY;COUNT=2
END:VEVENT
END:VCALENDAR
""",
                None,
            ),
            (
                "1.3 - no truncation - until",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:FREQ=WEEKLY;UNTIL=20071128T000000Z
END:VEVENT
END:VCALENDAR
""",
                None,
            ),
            (
                "1.4 - truncation - count",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:FREQ=WEEKLY;COUNT=2000
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:COUNT=400;FREQ=WEEKLY
END:VEVENT
END:VCALENDAR
""",
            ),
            (
                "1.5 - truncation - until",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:FREQ=DAILY;UNTIL=20471128T000000Z
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:COUNT=400;FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
            ),
            (
                "1.6 - no truncation - unbounded yearly",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:FREQ=WEEKLY;UNTIL=20071128T000000Z
END:VEVENT
END:VCALENDAR
""",
                None,
            ),
            (
                "1.7 - truncation - unbounded daily",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PYVOBJECT//NONSGML Version 1//EN
BEGIN:VEVENT
UID:12345-67890-1
DTSTART:20071114T000000Z
RRULE:COUNT=400;FREQ=DAILY
END:VEVENT
END:VCALENDAR
""",
            ),
        )
        
        for title, original, result in data:
            ical1 = Component.fromString(original)
            changed = ical1.truncateRecurrence(400)
            ical1.normalizeAll()
            ical1 = str(ical1)
            if result is not None:
                if not changed:
                    self.fail("Truncation did not happen when expected: %s" % (title,))
                else:
                    ical2 = Component.fromString(result)
                    ical2 = str(ical2)
    
                    diff = "\n".join(unified_diff(ical1.split("\n"), ical2.split("\n")))
                    self.assertEqual(str(ical1), str(ical2), "Failed comparison: %s\n%s" % (title, diff,))
            elif changed:
                self.fail("Truncation happened when not expected: %s" % (title,))
