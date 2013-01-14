# Copyright (c) 2006-2013 Apple Inc. All rights reserved.
# Copyright (c) 2008 Lime Nest LLC
# Copyright (c) 2008 Lime Spot LLC
# Copyright (c) 2009 Ramon Ziai
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

'''
Parts of the following are inspired by urllib2_kerberos,
which is also under the Apache 2.0 License,
see http://limedav.com/hg/urllib2_kerberos
'''

from caldavclientlibrary.protocol.http.authentication.authenticator import Authenticator
from caldavclientlibrary.protocol.http.definitions import headers
import re
import kerberos

class Kerberos(Authenticator):

    def __init__(self, user):
        self.user = user
        self.context = None


    def addHeaders(self, hdrs, request):
        neg_value = self.negotiate_value(hdrs)
        header = self.generate_request_header(request, hdrs, neg_value)

        # Generate header
        hdrs.append((headers.Authorization, header))
        self.clean_context()


    def negotiate_value(self, headers):
        """checks for "Negotiate" in proper auth header
        taken from urllib2_kerberos, see http://limedav.com/hg/urllib2_kerberos
        """
        authreq = None
        for hdr in headers:
            if hdr[0].lower == "www-authenticate" and "Negotiate" in hdr[1]:
                authreq = hdr[1]

        if authreq:
            rx = re.compile('(?:.*,)*\s*Negotiate\s*([^,]*),?', re.I)
            mo = rx.search(authreq)
            if mo:
                return mo.group(1)
            else:
                # regex failed
                pass

        else:
            pass
            # header not found

        return None


    def generate_request_header(self, req, headers, neg_value):
        """
        taken from urllib2_kerberos, see http://limedav.com/hg/urllib2_kerberos
        """

        host = None
        # assuming that "Host" is one of the headers, which is usually the case
        for hdr in headers:
            if hdr[0] == "Host":
                host = hdr[1]

        tail, _ignore_sep, head = host.rpartition(':')
        domain = tail if tail else head

        # do GSS init
        result, self.context = kerberos.authGSSClientInit("http@%s" % domain)

        if result < 1:
            # authGSSClientInit returned negative result
            return None

        # authGSSClientInit() succeeded
        data = ""
        if neg_value != None:
            data = neg_value
        result = kerberos.authGSSClientStep(self.context, data)

        if result < 0:
            # authGSSClientStep returned bad result
            return None

        # authGSSClientStep() succeeded

        response = kerberos.authGSSClientResponse(self.context)
        # authGSSClientResponse() succeeded

        return "Negotiate %s" % response


    def clean_context(self):
        """
        taken from urllib2_kerberos, see http://limedav.com/hg/urllib2_kerberos
        """
        if self.context is not None:
            kerberos.authGSSClientClean(self.context)
            self.context = None
