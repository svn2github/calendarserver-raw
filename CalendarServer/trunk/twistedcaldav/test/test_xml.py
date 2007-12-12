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

import os

from twisted.trial.unittest import SkipTest
from twistedcaldav.ical import Component
from twistedcaldav.caldavxml import *
import twistedcaldav.test.util

class XML (twistedcaldav.test.util.TestCase):
    """
    XML tests
    """
    calendar_file = os.path.join(os.path.dirname(__file__), "data", "Holidays",
                                 "C3184A66-1ED0-11D9-A5E0-000A958A3252.ics")
    calendar = Component.fromStream(file(calendar_file))
    calendar.validateForCalDAV()

    def test_ComponentFilter(self):
        """
        Component filter element.
        """
        for component_name, has in (
            ("VEVENT", True),
            ("VTODO", False),
        ):
            if has: no = "no "
            else:   no = ""

            if has != ComponentFilter(
                ComponentFilter(
                    name=component_name
                ),
                name="VCALENDAR"
            ).match(self.calendar):
                self.fail("Calendar has %s%s?" % (no, component_name))

    def test_PropertyFilter(self):
        """
        Property filter element.
        """
        for property_name, has in (
            ("UID", True),
            ("BOOGER", False),
        ):
            if has: no = "no "
            else:   no = ""

            if has != ComponentFilter(
                ComponentFilter(
                    PropertyFilter(
                        name=property_name
                    ),
                    name="VEVENT"
                ),
                name="VCALENDAR"
            ).match(self.calendar):
                self.fail("Calendar has %sVEVENT with %s?" % (no, property_name))

    def test_ParameterFilter(self):
        """
        Parameter filter element.
        """
        raise SkipTest("test unimplemented")

    def test_TextMatch(self):
        """
        Text match element.
        """
        for uid, caseless, has in (
            ("C3184A66-1ED0-11D9-A5E0-000A958A3252", False, True),
            ("c3184a66-1ed0-11d9-a5e0-000a958a3252", True, True),
            ("BOOGER", False, False),
            ("BOOGER", True, False),
        ):
            if has: no = "no "
            else:   no = ""

            if has != ComponentFilter(
                ComponentFilter(
                    PropertyFilter(
                        TextMatch.fromString(uid, caseless=caseless),
                        name="UID"
                    ),
                    name="VEVENT"
                ),
                name="VCALENDAR"
            ).match(self.calendar):
                self.fail("Calendar has %sVEVENT with UID %s? (caseless=%s)" % (no, uid, caseless))

    def test_TimeRange(self):
        """
        Time range match element.
        """
        for start, end, has in (
            ("20020101T000000Z", "20020101T000001Z", True),
            ("20020101T000000Z", "20020101T000000Z", True), # Timespan of zero duration
            ("20020101", "20020101", True), # Timespan of zero duration
            ("20020101", "20020102", True),
            ("20020101", "20020103", True),
            ("20020102", "20020103", False),
            ("20011201", "20020101", False), # End is non-inclusive

            # Expanded recurrance
            ("20030101T000000Z", "20030101T000001Z", True),
            ("20030101T000000Z", "20030101T000000Z", True), # Timespan of zero duration
            ("20030101", "20030101", True), # Timespan of zero duration
            ("20030101", "20030102", True),
            ("20030101", "20030103", True),
            ("20030102", "20030103", False),
            ("20021201", "20030101", False), # End is non-inclusive
        ):
            if has: no = "no "
            else:   no = ""

            if has != Filter(ComponentFilter(
                ComponentFilter(
                    TimeRange(start=start, end=end),
                    name="VEVENT"
                ),
                name="VCALENDAR"
            )).match(self.calendar):
                self.fail("Calendar has %sVEVENT with timerange %s?" % (no, (start, end)))

    test_TimeRange.todo = "recurrance expansion"
