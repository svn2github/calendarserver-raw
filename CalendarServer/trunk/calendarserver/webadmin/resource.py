##
# Copyright (c) 2009 Apple Inc. All rights reserved.
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
Calendar Server Web Admin UI.
"""

__all__ = [
    "WebAdminResource",
]

import cgi
import operator
import os
import urllib
import urlparse

from calendarserver.webadmin.helper import search, ResourceWrapper

from twistedcaldav.config import config
from twistedcaldav.extensions import DAVFile, ReadOnlyResourceMixIn

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.web2.http import Response
from twisted.web2.http_headers import MimeType
from twisted.web2.stream import MemoryStream
from twisted.web2.dav import davxml
from twisted.web2.dav.resource import TwistedACLInheritable


class WebAdminResource (ReadOnlyResourceMixIn, DAVFile):

    def __init__(self, path, root, directory, principalCollections=()):
        self.root = root
        self.directory = directory
        super(WebAdminResource, self).__init__(path,
            principalCollections=principalCollections)

    # Only allow administrators to access
    def defaultAccessControlList(self):
        return davxml.ACL(*config.AdminACEs)
    
    def etag(self):
        # Can't be calculated here
        return None

    def contentLength(self):
        # Can't be calculated here
        return None

    def lastModified(self):
        return None

    def exists(self):
        return True

    def displayName(self):
        return "Web Admin"

    def contentType(self):
        return MimeType.fromString("text/html; charset=utf-8");

    def contentEncoding(self):
        return None

    def createSimilarFile(self, path):
        return DAVFile(path, principalCollections=self.principalCollections())

    def directoryStyleSheet(self):
        return (
            "th, .even td, .odd td { padding-right: 0.5em; font-family: monospace}"
            ".even-dir { background-color: #efe0ef }"
            ".even { background-color: #eee }"
            ".odd-dir {background-color: #f0d0ef }"
            ".odd { background-color: #dedede }"
            ".icon { text-align: center }"
            ".listing {"
              "margin-left: auto;"
              "margin-right: auto;"
              "width: 50%;"
              "padding: 0.1em;"
            "}"
            "body { border: 0; padding: 0; margin: 0; background-color: #efefef;}"
            "h1 {padding: 0.1em; padding-left:10px; padding-right:10px; background-color: #777; color: white; border-bottom: thin white dashed;}"
        )

    def header(self, title):

        if title is None or title == "":
            title = "Calendar Server Web Administration"
        else:
            title = "Calendar Server Web Administration: " + title
        
        return ( "<html>\n"
                 "<head>\n"
                 "<h1>%(title)s</h1>\n" 
                 "<title>%(title)s</title>\n"
                 "<style>\n  %(style)s\n</style>\n"
                 "</head>\n"
                 "<body>\n"
                 "<div style=\"padding-left:10px; padding-right:10px\">\n" % { "title": title, "style": self.directoryStyleSheet() })

    def footer(self) :
        return ( "\n</div>\n"
                 "</body>\n"
                 "</html>" )
              
    @inlineCallbacks
    def htmlContent(self, resourceWrapper, directory, request):

        def queryValue(arg):
            query = cgi.parse_qs(urlparse.urlparse(request.uri).query, True)
            return query.get(arg, [""])[0]

        def queryValues(arg):
            query = cgi.parse_qs(urlparse.urlparse(request.uri).query, True)
            matches = []
            for key in query.keys():
                if key.startswith(arg):
                    matches.append(key[len(arg):])
            return matches

        # Read request parameters.
        resourceGuid = queryValue("resourceGuid")
        resourceSearch = queryValue("resourceSearch")
        davPropertyName = queryValue("davPropertyName")
        autoSchedule = queryValue("autoSchedule")
        delegateSearch = queryValue("delegateSearch")
        makeReadDelegates = queryValues("mkReadDelegate|")
        makeWriteDelegates = queryValues("mkWriteDelegate|")
        removeDelegates = queryValues("rmDelegate|")

        # Begin the content
        content = ("%(header)s\n"
                   "<h2>Resource Management</h2>\n"
                   "%(search)s\n" % { "header": self.header(None),
                                     "search": (yield self.searchContent(directory, resourceSearch)) })

        # Add details if a resource has been selected.
        if resourceGuid is not None and resourceGuid != "":
        
            resource = resourceWrapper.lookupResource(resourceGuid)
    
            # Update the auto-schedule value if specified.
            if autoSchedule is not None and (autoSchedule == "true" or autoSchedule == "false"):
                result = (yield resource.setAutoSchedule(autoSchedule == "true"))

            # Update the delegates if specified.
            for delegateGuid in removeDelegates:
                delegate = resourceWrapper.lookupResource(delegateGuid)
                result = (yield resource.removeDelegate(delegate, "read"))
                result = (yield resource.removeDelegate(delegate, "write"))

            for delegateGuid in makeReadDelegates:
                delegate = resourceWrapper.lookupResource(delegateGuid)
                result = (yield resource.addDelegate(delegate, "read"))

            for delegateGuid in makeWriteDelegates:
                delegate = resourceWrapper.lookupResource(delegateGuid)
                result = (yield resource.addDelegate(delegate, "write"))
                
            # Add the detailed content
            content += (yield self.detailContent(resourceWrapper, directory, resource, resourceGuid, davPropertyName, delegateSearch))

        # Add the footer
        content += self.footer()

        returnValue(content)
        
    @inlineCallbacks
    def searchContent(self, directory, resourceSearch):
        
        formHtml = ("""
<form id=\"frm_resource\" name=\"resourceForm\" action=\"/admin/\">
  Search for resource to manage:
  <input type=\"text\" id=\"txt_resourceSearch\" name=\"resourceSearch\" value=\"%s\" size=\"40\" />
  <input type=\"submit\" value=\"Search\" />
</form>
""" % resourceSearch)

        # Perform the search if a parameter was specified.
        resultHtml = ""
        if resourceSearch is not None and resourceSearch != "":

            records = (yield search(directory, resourceSearch))
            if records:
                records.sort(key=operator.attrgetter('fullName'))
                resultHtml = """
<table cellspacing=\"0\" cellpadding=\"3\" border=\"1\" style=\"margin-top:2px\">
  <tr class=\"odd\">
    <th>GUID</th>
    <th>Full Name</th>
    <th>Type</th>
    <th>Short Names</th>
    <th>Auth IDs</th>
    <th>Email Addresses</th>
  </tr>""" % { "resourceSearch": resourceSearch }

                for _i in range(0, len(records)):
                    resultHtml += """
  <tr class=\"%(rowClass)s\">
    <td><a href=\"/admin/?resourceGuid=%(guid)s\">%(guid)s</a></td>
    <td>%(name)s</td>
    <td>%(type)s</td>
    <td>%(shortNames)s</td>
    <td>%(authIds)s</td>
    <td>%(emails)s</td>
  </tr>""" % { "rowClass": "even" if _i%2 == 0 else "odd",
               "guid": urllib.quote(records[_i].guid),
               "name": records[_i].fullName,
               "type": { "users"     : "User",
                         "groups"    : "Group",
                         "locations" : "Place",
                         "resources" : "Resource",
                       }.get(records[_i].recordType),
               "shortNames": str(", ".join(records[_i].shortNames),),
               "authIds": str(", ".join(records[_i].authIDs),),
               "emails": str(", ".join(records[_i].emailAddresses),)
             }
                resultHtml += "\n</table>"
            else:
                resultHtml += "<div style=\"margin-top:4px\">No matches found for resource <b>%(resourceSearch)s</b>.</div>\n" % { "resourceSearch": resourceSearch }

        result = "%s%s" % (formHtml, resultHtml)
        returnValue(result)

    @inlineCallbacks
    def detailContent(self, resourceWrapper, directory, resource, resourceGuid, davPropertyName, delegateSearch):

        ###
        # Resource title
        ###
        headerHtml = """
<div style=\"margin-top:15px; background-color: #777; border-bottom:1px #ffffff dotted\"></div>
<div style=\"background-color: #777; padding-top:2px; border-bottom:1px #ffffff dotted\"></div>
<h3>Resource Details: %(resourceTitle)s</h3>""" % { "resourceTitle": resource.resource }

        ###
        # DAV properties
        ###
        propertyHtml = """
<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>
<form id=\"frm_davProperty\" name=\"davPropertyForm\" action=\"/admin/\" style=\"margin-top:15px; margin-bottom:0; padding-bottom:0\">
  Show a DAV property value:
  <input type=\"hidden\" id=\"hdn_resourceGuid\" name=\"resourceGuid\" value=\"%(resourceGuid)s\" />
  <input type=\"text\" id=\"txt_davPropertyName\" name=\"davPropertyName\" value=\"%(davPropertyName)s\" size=\"40\" />
  <input type=\"submit\" value=\"Get Value\" />
</form>
""" % { "resourceGuid": urllib.quote(resourceGuid),
        "davPropertyName": davPropertyName if davPropertyName is not None and davPropertyName != "" else "DAV:#" }
        
        if davPropertyName is not None and davPropertyName != "":
            try:
                namespace, name = davPropertyName.split("#")
            except Exception, e:
                propertyHtml += "<div>Unable to parse property to read: <b>%s</b></div>" % davPropertyName

            result = (yield resource.readProperty((namespace, name)))
            propertyHtml += "<div style=\"margin-top:7px\">Value of property <b>%(name)s</b>:</div><pre style=\"margin-top:5px; padding-top:0\">%(value)s</pre>" % { 
                "name": davPropertyName, 
                "value": cgi.escape(result.toxml())
            }

        ###
        # Auto-schedule
        ###
        autoSchedule = (yield resource.getAutoSchedule())
        autoScheduleHtml = """
<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>
<form id=\"frm_autoSchedule\" name=\"autoScheduleForm\" action=\"/admin/\" style=\"margin-top:15px\">
  <input type=\"hidden\" id=\"hdn_resourceGuid\" name=\"resourceGuid\" value=\"%(resourceGuid)s\" />
  <div style=\"margin-top:7px\">
    Auto-Schedule
    <select id=\"sel_autoSchedule\" name=\"autoSchedule\">
      <option value=\"true\"%(trueSelected)s>Yes</option>
      <option value=\"false\"%(falseSelected)s>No</option>
    </select>
    <input type=\"submit\" value=\"Change\" />
  </div>
</form>\n""" % { "resourceGuid": urllib.quote(resourceGuid),
               "trueSelected": " selected=\"selected\"" if autoSchedule else "",
               "falseSelected": "" if autoSchedule else " selected=\"selected\"" }

        ###
        # Current delegates
        ###
        currentDelegatesHtml = "\n<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>"

        (readDelegates, writeDelegates) = ((yield resource.getDelegates("read")), (yield resource.getDelegates("write")))
        if len(readDelegates) > 0 or len(writeDelegates) > 0:
            currentDelegatesHtml += """
<form id=\"frm_delegates\" name=\"delegatesForm\" action=\"/admin/\" style=\"margin-top:15px\">
  <input type=\"hidden\" id=\"hdn_resourceGuid\" name=\"resourceGuid\" value=\"%(resourceGuid)s\" />
  <table cellspacing=\"0\" cellpadding=\"3\" border=\"1\">
    <tr class=\"odd\">
      <th colspan=\"2\">Read-Only Delegates</th>
      <th colspan=\"2\">Read-Write Delegates</th>
    </tr>
""" % { "resourceTitle": resource.resource,
        "resourceGuid": urllib.quote(resourceGuid) }

            for _i in range(0, max(len(readDelegates), len(writeDelegates))):
                currentDelegatesHtml += "    <tr class=\"%(rowClass)s\">" % { "rowClass": "even" if _i%2 == 0 else "odd" }
                if (_i < len(readDelegates)) :
                    currentDelegatesHtml += """
      <td>%(delegate)s</td>
      <td>
        <input type=\"submit\" name=\"mkWriteDelegate|%(delegatePath)s\" value=\"Make Read-Write\" />
        <input type=\"submit\" name=\"rmDelegate|%(delegatePath)s\" value=\"Remove Delegate\" />
      </td>""" % { "delegatePath": readDelegates[_i][21:-1], # GUID only, not full path
                   "delegate" : resourceWrapper.getChild(readDelegates[_i]).resource }
                else :
                    currentDelegatesHtml += "\n      <td colspan=\"2\"></td>"
                if (_i < len(writeDelegates)) :
                    currentDelegatesHtml += """
      <td>%(delegate)s</td>
      <td>
        <input type=\"submit\" name=\"mkReadDelegate|%(delegatePath)s\" value=\"Make Read-Only\" />
        <input type=\"submit\" name=\"rmDelegate|%(delegatePath)s\" value=\"Remove Delegate\" />
      </td>""" % { "delegatePath": writeDelegates[_i][21:-1], # GUID only, not full path
                   "delegate" : resourceWrapper.getChild(writeDelegates[_i]).resource }
                else :
                    currentDelegatesHtml += "\n      <td colspan=\"2\"></td>"
                currentDelegatesHtml += "\n    </tr>\n"

            currentDelegatesHtml += "  </table>\n</form>\n"
        else:
            currentDelegatesHtml += "<div style=\"margin-top:15px\">This resource has no delegates.</div>\n"

        ###
        # Search for new delegates
        ###
        delegateSearchHtml = """
<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>
<form id=\"frm_delegateSearch\" name=\"delegateSearchForm\" action=\"/admin/\" style=\"margin-top:15px; margin-bottom:0; padding-bottom:0\">
  Search to add new delegates:
  <input type=\"hidden\" id=\"hdn_resourceGuid\" name=\"resourceGuid\" value=\"%(resourceGuid)s\" />
  <input type=\"text\" id=\"txt_delegateSearch\" name=\"delegateSearch\" value=\"%(delegateSearch)s\" size=\"40\" />
  <input type=\"submit\" value=\"Search\" />
</form>
""" % { "resourceGuid": urllib.quote(resourceGuid),
        "delegateSearch": delegateSearch }

        # Perform the search if a parameter was specified.
        if delegateSearch is not None and delegateSearch != "":
            records = (yield search(directory, delegateSearch))
            if records:
                records.sort(key=operator.attrgetter('fullName'))

                delegateSearchHtml += """
<form id=\"frm_delegateAdd\" name=\"delegateAddForm\" action=\"/admin/\" style=\"margin-top:2px; padding-top:0\">
  <input type=\"hidden\" id=\"hdn_resourceGuid\" name=\"resourceGuid\" value=\"%(resourceGuid)s\" />
  <table cellspacing=\"0\" cellpadding=\"3\" border=\"1\">
    <tr class=\"odd\">
      <th>Full Name</th>
      <th>Type</th>
      <th>Short Names</th>
      <th>Email Addresses</th>
      <th>Add Delegate</th>
    </tr>""" % { "resourceGuid": urllib.quote(resourceGuid) }

                for _i in range(0, len(records)):
                    delegateSearchHtml += """
    <tr class=\"%(rowClass)s\">
      <td>%(name)s</td>
      <td>%(type)s</td>
      <td>%(shortNames)s</td>
      <td>%(emails)s</td>
      <td>
        <input type=\"submit\" name=\"mkReadDelegate|%(delegateGuid)s\" value=\"Make Read-Only Delegate\" />
        <input type=\"submit\" name=\"mkWriteDelegate|%(delegateGuid)s\" value=\"Make Read-Write Delegate\" />
      </td>
    </tr>""" % { "rowClass": "even" if _i%2 == 0 else "odd",
               "delegateGuid": urllib.quote(records[_i].guid),
               "name": records[_i].fullName,
               "type": { "users"     : "User",
                         "groups"    : "Group",
                         "locations" : "Place",
                         "resources" : "Resource",
                       }.get(records[_i].recordType),
               "shortNames": str(", ".join(records[_i].shortNames),),
               "emails": str(", ".join(records[_i].emailAddresses),)
             }
                delegateSearchHtml += "  </table>\n</form>\n"
            else:
                delegateSearchHtml += "<div style=\"margin-top:4px\">No matches found for delegate resource <b>%(delegateSearch)s</b>.</div>\n" % { "delegateSearch": delegateSearch }
        
        ###
        # Put it all together
        ###
        detailHtml = "%s%s%s%s%s" % (headerHtml, propertyHtml, autoScheduleHtml, currentDelegatesHtml, delegateSearchHtml)

        returnValue(detailHtml)

    def render(self, request):

        # Prepare the ResourceWrapper, which will be used to get and modify resource info.
        resourceWrapper = ResourceWrapper(self.root)
        
        # The response-generation will be deferred.
        def _defer(htmlContent):
            response = Response()
            response.stream = MemoryStream(str(htmlContent))
            for (header, value) in (
                ("content-type", self.contentType()),
                ("content-encoding", self.contentEncoding()),
            ):
                if value is not None:
                    response.headers.setHeader(header, value)
            return response

        # Generate the HTML and return the response when it's ready.
        htmlContent = self.htmlContent(resourceWrapper, self.directory, request)
        htmlContent.addCallback(_defer)
        return htmlContent
