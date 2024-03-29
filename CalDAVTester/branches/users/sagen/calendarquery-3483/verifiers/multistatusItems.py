##
# Copyright (c) 2006-2007 Apple Inc. All rights reserved.
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

"""
Verifier that checks a multistatus response to make sure that the specified hrefs
are returned with appropriate status codes.
"""

import xml.dom.minidom

from utilities.xmlutils import ElementsByName

class Verifier(object):
    
    def verify(self, manager, uri, response, respdata, args):

        # If no hrefs requested, then assume none should come back
        okhrefs = args.get("okhrefs", [])
        badhrefs = args.get("badhrefs", [])
        count = args.get("count", [])
        prefix = args.get("prefix", [])
        if len(prefix):
            prefix = prefix[0] if prefix[0] != "-" else ""
        else:
            prefix = uri
        okhrefs = [prefix + i for i in okhrefs]
        badhrefs = [prefix + i for i in badhrefs]
        count = [int(i) for i in count]
        
        # Process the multistatus response, extracting all hrefs
        # and comparing with the set defined for this test. Report any
        # mismatches.
        
        # Must have MULTISTATUS response code
        if response.status != 207:
            return False, "           HTTP Status for Request: %d\n" % (response.status,)
            
        doc = xml.dom.minidom.parseString( respdata )
        ok_status_hrefs = []
        bad_status_hrefs = []
        multistatus = doc.getElementsByTagNameNS("DAV:", "multistatus" )
        for response in ElementsByName(multistatus[0], "DAV:", "response"):

            # Get href for this response
            href = ElementsByName(response, "DAV:", "href")
            if href is None or len(href) != 1:
                return False, "        Incorrect/missing DAV:Href element in response"
            if href[0].firstChild is not None:
                href = href[0].firstChild.data
            else:
                href = ""

            # Verify status
            status = ElementsByName(response, "DAV:", "status")
            if len(status) == 1:
                statustxt = status[0].firstChild.data
                status = False
                if statustxt.startswith("HTTP/1.1 ") and (len(statustxt) >= 10):
                    status = (statustxt[9] == "2")
            else:
                propstatus = ElementsByName(response, "DAV:", "propstat")
                if len(propstatus) > 0:
                    statustxt = "OK"
                    status = True
                else:
                    status = False
            
            if status:
                ok_status_hrefs.append( href )
            else:
                bad_status_hrefs.append( href )
        ok_result_set = set( ok_status_hrefs )
        ok_test_set = set( okhrefs )
        bad_result_set = set( bad_status_hrefs )
        bad_test_set = set( badhrefs )
        
        result = True
        resulttxt = ""

        # Check for count
        if len(count) == 1:
            if len(ok_result_set) != count[0] + 1:
                result = False
                resulttxt +=  "        %d items returned, but %d items expected" % (len(ok_result_set) - 1, count[0], )
            return result, resulttxt

        # Now do set difference
        ok_missing = ok_test_set.difference( ok_result_set )
        ok_extras = ok_result_set.difference( ok_test_set )
        bad_missing = bad_test_set.difference( bad_result_set )
        bad_extras = bad_result_set.difference( bad_test_set )
        
        if len( ok_missing ) + len( ok_extras ) + len( bad_missing ) + len( bad_extras )!= 0:
            if len( ok_missing ) != 0:
                l = list( ok_missing )
                resulttxt += "        Items not returned in report (OK):"
                for i in l:
                    resulttxt += " " + str(i) 
                resulttxt += "\n"
            if len( ok_extras ) != 0:
                l = list( ok_extras )
                resulttxt += "        Unexpected items returned in report (OK):"
                for i in l:
                    resulttxt += " " + str(i) 
                resulttxt += "\n"
            if len( bad_missing ) != 0:
                l = list( bad_missing )
                resulttxt += "        Items not returned in report (BAD):"
                for i in l:
                    resulttxt += " " + str(i) 
                resulttxt += "\n"
            if len( bad_extras ) != 0:
                l = list( bad_extras )
                resulttxt += "        Unexpected items returned in report (BAD):"
                for i in l:
                    resulttxt += " " + str(i) 
                resulttxt += "\n"
            result = False
            
        return result, resulttxt
