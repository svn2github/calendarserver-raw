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
Verifier that checks a propfind response to make sure that the specified ACL privileges
are available for the currently authenticated user.
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

        granted = args.get("granted", [])
        denied = args.get("denied", [])
        
        # Process the multistatus response, extracting all current-user-privilege-set elements
        # and check to see that each required privilege is present, or that denied ones are not.
        
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

            # Get all privileges
            granted_privs = []
            privset = response.getElementsByTagNameNS("DAV:", "current-user-privilege-set")
            for props in privset:
                # Determine status for this propstat
                privileges = ElementsByName(props, "DAV:", "privilege")
                for privilege in privileges:
                    for child in privilege._get_childNodes():
                        if isinstance(child, Element):
                            qname = (child.namespaceURI, child.localName)
                            fqname = qname[0] + qname[1]
                            granted_privs.append( fqname )
    
            granted_result_set = set( granted_privs )
            granted_test_set = set( granted )
            denied_test_set = set( denied )
            
            # Now do set difference
            granted_missing = granted_test_set.difference( granted_result_set )
            denied_present = granted_result_set.intersection( denied_test_set )
            
            if len( granted_missing ) + len( denied_present ) != 0:
                if len( granted_missing ) != 0:
                    l = list( granted_missing )
                    resulttxt += "        Missing privileges not granted for %s:" % href
                    for i in l:
                        resulttxt += " " + str(i) 
                    resulttxt += "\n"
                if len( denied_present ) != 0:
                    l = list( denied_present )
                    resulttxt += "        Available privileges that should be denied for %s:" % href
                    for i in l:
                        resulttxt += " " + str(i) 
                    resulttxt += "\n"
                result = False
            
        return result, resulttxt
