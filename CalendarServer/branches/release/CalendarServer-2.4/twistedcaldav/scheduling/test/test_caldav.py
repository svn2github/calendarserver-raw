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

import twistedcaldav.test.util
from twistedcaldav.scheduling.caldav import ScheduleViaCalDAV
from twistedcaldav.config import config

class CalDAV (twistedcaldav.test.util.TestCase):
    """
    twistedcaldav.scheduling.caldav tests
    """

    def test_matchCalendarUserAddress(self):
        """
        Make sure we do an exact comparison on EmailDomain
        """
        config.Scheduling[ScheduleViaCalDAV.serviceType()]["EmailDomain"] = "example.com"
        self.assertTrue(ScheduleViaCalDAV.matchCalendarUserAddress("mailto:user@example.com"))
        self.assertFalse(ScheduleViaCalDAV.matchCalendarUserAddress("mailto:user@foo.example.com"))
        self.assertFalse(ScheduleViaCalDAV.matchCalendarUserAddress("mailto:user@xyzexample.com"))
