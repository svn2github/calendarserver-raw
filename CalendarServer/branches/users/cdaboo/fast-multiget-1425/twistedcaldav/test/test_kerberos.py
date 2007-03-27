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
# DRI: Cyrus Daboo, cdaboo@apple.com
##
from twisted.cred.error import LoginFailed

from twisted.cred.error import UnauthorizedLogin
from twisted.web2.test.test_server import SimpleRequest

from twistedcaldav import authkerb
import twistedcaldav.test.util

"""
We can't test kerberos for real without actually having a working Kerberos infrastructure
which we are not guaranteed to have for the test.
"""

class KerberosTests(twistedcaldav.test.util.TestCase):

    def test_BasicKerberosCredentials(self):
        authkerb.BasicKerberosCredentials("test", "test", "http/example.com@EXAMPLE.COM", "EXAMPLE.COM")

    def test_BasicKerberosCredentialFactory(self):
        factory = authkerb.BasicKerberosCredentialFactory("http/example.com@EXAMPLE.COM", "EXAMPLE.COM")

        challenge = factory.getChallenge("peer")
        expected_challenge = {'realm': "EXAMPLE.COM"}
        self.assertTrue(challenge == expected_challenge,
                        msg="BasicKerberosCredentialFactory challenge %s != %s" % (challenge, expected_challenge))

    def test_NegotiateCredentials(self):
        authkerb.NegotiateCredentials("test")

    def test_NegotiateCredentialFactory(self):
        factory = authkerb.NegotiateCredentialFactory("http/example.com@EXAMPLE.COM", "EXAMPLE.COM")

        challenge = factory.getChallenge("peer")
        expected_challenge = {}
        self.assertTrue(challenge == expected_challenge,
                        msg="NegotiateCredentialFactory challenge %s != %s" % (challenge, expected_challenge))

        request = SimpleRequest(self.site, "GET", "/")
        try:
            factory.decode("Bogus Data".encode("base64"), request)
        except (UnauthorizedLogin, LoginFailed):
            pass
        except Exception, ex:
            self.fail(msg="NegotiateCredentialFactory decode failed with exception: %s" % (ex,))
        else:
            self.fail(msg="NegotiateCredentialFactory decode did not fail")
