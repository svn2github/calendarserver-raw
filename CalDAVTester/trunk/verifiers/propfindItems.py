##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
#
# This file contains Original Code and/or Modifications of Original Code
# as defined in and that are subject to the Apple Public Source License
# Version 2.0 (the 'License'). You may not use this file except in
# compliance with the License. Please obtain a copy of the License at
# http://www.opensource.apple.com/apsl/ and read it before using this
# file.
#
# The Original Code and all software distributed under the License are
# distributed on an 'AS IS' basis, WITHOUT WARRANTY OF ANY KIND, EITHER
# EXPRESS OR IMPLIED, AND APPLE HEREBY DISCLAIMS ALL SUCH WARRANTIES,
# INCLUDING WITHOUT LIMITATION, ANY WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE, QUIET ENJOYMENT OR NON-INFRINGEMENT.
# Please see the License for the specific language governing rights and
# limitations under the License.
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##

"""
Verifier that checks a propfind response to make sure that the specified properties
are returned with appropriate status codes.
"""

from xml.dom.minicompat import NodeList
from xml.dom.minidom import Element
from xml.dom.minidom import Node
import xml.dom.minidom

class Verifier(object):
    
    def verify(self, uri, response, respdata, args): #@UnusedVariable
        def ElementsByName(parent, nsURI, localName):
            rc = NodeList()
            for node in parent.childNodes:
                if node.nodeType == Node.ELEMENT_NODE:
                    if ((localName == "*" or node.localName == localName) and
                        (nsURI == "*" or node.namespaceURI == nsURI)):
                        rc.append(node)
            return rc

        # If no status veriffication requested, then assume all 2xx codes are OK
        ignores = args.get("ignore", [])

        # Get property arguments and split on $ delimited for name, value tuples
        okprops = args.get("okprops", [])
        for i in range(len(okprops)):
            p = okprops[i]
            if (p.find("$") != -1):
                if  p.find("$") != len(p) - 1:
                    okprops[i] = (p.split("$")[0], p.split("$")[1],)
                else:
                    okprops[i] = (p.split("$")[0], None,)
            else:
                okprops[i] = (p, None,)
        badprops = args.get("badprops", [])
        for i in range(len(badprops)):
            p = badprops[i]
            if p.find("$") != -1:
                badprops[i] = (p.split("$")[0], p.split("$")[1],)
            else:
                badprops[i] = (p, None,)

        ok_test_set = set( okprops )
        bad_test_set = set( badprops )
        
        # Process the multistatus response, extracting all hrefs
        # and comparing with the set defined for this test. Report any
        # mismatches.
        
        # Must have MULTISTATUS response code
        if response.status != 207:
            return False, "           HTTP Status for Request: %d\n" % (response.status,)
            
        doc = xml.dom.minidom.parseString( respdata )
        result = True
        resulttxt = ""
        for response in doc.getElementsByTagNameNS( "DAV:", "response" ):

            # Get href for this response
            href = ElementsByName(response, "DAV:", "href")
            if len(href) != 1:
                return False, "           Wrong number of DAV:href elements\n"
            if href[0].firstChild is not None:
                href = href[0].firstChild.data
            else:
                href = ""
            if href in ignores:
                continue

            # Get all property status
            ok_status_props = []
            bad_status_props = []
            propstatus = ElementsByName(response, "DAV:", "propstat")
            for props in propstatus:
                # Determine status for this propstat
                status = ElementsByName(props, "DAV:", "status")
                if len(status) == 1:
                    statustxt = status[0].firstChild.data
                    status = False
                    if statustxt.startswith("HTTP/1.1 ") and (len(statustxt) >= 10):
                        status = (statustxt[9] == "2")
                else:
                    status = False
                
                # Get properties for this propstat
                prop = ElementsByName(props, "DAV:", "prop")
                if len(prop) != 1:
                    return False, "           Wrong number of DAV:prop elements\n"

                for child in prop[0]._get_childNodes():
                    if isinstance(child, Element):
                        qname = (child.namespaceURI, child.localName)
                        fqname = qname[0] + qname[1]
                        if child.firstChild is not None:
                            # Copy sub-element data as text into one long string and strip leading/trailing space
                            value = ""
                            for p in child._get_childNodes():
                                temp = p.toprettyxml("", "")
                                temp = temp.strip()
                                value += temp
                            if status:
                                if (fqname, None,) in ok_test_set:
                                    value = None
                            else:
                                if (fqname, None,) in bad_test_set:
                                    value = None
                        else:
                            value = None
                        
                        if status:
                            ok_status_props.append( (fqname, value,) )
                        else:
                            bad_status_props.append( (fqname, value,) )
    
            ok_result_set = set( ok_status_props )
            bad_result_set = set( bad_status_props )
            
            # Now do set difference
            ok_missing = ok_test_set.difference( ok_result_set )
            ok_extras = ok_result_set.difference( ok_test_set )
            bad_missing = bad_test_set.difference( bad_result_set )
            bad_extras = bad_result_set.difference( bad_test_set )
            
            if len( ok_missing ) + len( ok_extras ) + len( bad_missing ) + len( bad_extras )!= 0:
                if len( ok_missing ) != 0:
                    l = list( ok_missing )
                    resulttxt += "        Items not returned in report (OK) for %s:" % href
                    for i in l:
                        resulttxt += " " + str(i) 
                    resulttxt += "\n"
                if len( ok_extras ) != 0:
                    l = list( ok_extras )
                    resulttxt += "        Unexpected items returned in report (OK) for %s:" % href
                    for i in l:
                        resulttxt += " " + str(i) 
                    resulttxt += "\n"
                if len( bad_missing ) != 0:
                    l = list( bad_missing )
                    resulttxt += "        Items not returned in report (BAD) for %s:" % href
                    for i in l:
                        resulttxt += " " + str(i) 
                    resulttxt += "\n"
                if len( bad_extras ) != 0:
                    l = list( bad_extras )
                    resulttxt += "        Unexpected items returned in report (BAD) for %s:" % href
                    for i in l:
                        resulttxt += " " + str(i) 
                    resulttxt += "\n"
                result = False
            
        return result, resulttxt
