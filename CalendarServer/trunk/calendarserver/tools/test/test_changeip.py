##
# Copyright (c) 2005-2014 Apple Inc. All rights reserved.
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

from twistedcaldav.test.util import TestCase
from calendarserver.tools.changeip_calendar import updateConfig


class ChangeIPTestCase(TestCase):

    def test_updateConfig(self):

        plist = {
            "Untouched": "dont_change_me",
            "ServerHostName": "",
            "Scheduling": {
                "iMIP": {
                    "Receiving": {
                        "Server": "original_hostname",
                    },
                    "Sending": {
                        "Server": "original_hostname",
                        "Address": "user@original_hostname",
                    },
                },
            },
        }

        updateConfig(
            plist, "10.1.1.1", "10.1.1.2", "original_hostname", "new_hostname"
        )

        self.assertEquals(
            plist,
            {
                "Untouched": "dont_change_me",
                "ServerHostName": "",
                "Scheduling": {
                    "iMIP": {
                        "Receiving": {
                            "Server": "new_hostname",
                        },
                        "Sending": {
                            "Server": "new_hostname",
                            "Address": "user@new_hostname",
                        },
                    },
                },
            }
        )
