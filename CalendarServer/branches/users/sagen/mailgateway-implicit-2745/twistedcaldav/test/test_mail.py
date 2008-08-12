##
# Copyright (c) 2008 Apple Inc. All rights reserved.
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
from twisted.internet.task import Clock
from twistedcaldav.mail import *
from twistedcaldav import config as config_mod
from twistedcaldav.config import Config
import email
import os



class MailHandlerTests(TestCase):

    def setUp(self):
        self.handler = MailHandler(dataRoot=":memory:")
        self.dataDir = os.path.join(os.path.dirname(__file__), "data", "mail")

    def test_CheckDSNFailure(self):

        data = {
            'good_reply' : (False, None, None),
            'dsn_failure_no_ics' : (True, "failed", "<40900559-dab1-4956-ba87-f88e00cf5104@example.com>"),
            'dsn_failure_with_ics' : (True, "failed", "<20080812191939.51369.1538816694.0@plugh.example.com>"),
            'dsn_failure_no_original' : (True, None, None),
        }

        for filename, expected in data.iteritems():
            msg = email.message_from_string(
                file(os.path.join(self.dataDir, filename)).read()
            )
            self.assertEquals(self.handler.checkDSN(msg), expected)
