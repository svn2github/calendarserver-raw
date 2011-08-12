# -*- test-case-name: calendarserver.webadmin.test.test_resource -*-
##
# Copyright (c) 2009-2010 Apple Inc. All rights reserved.
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
    "WebAdminPage",
]

import cgi
import operator
import urlparse

from calendarserver.tools.principals import (
    principalForPrincipalID, proxySubprincipal, action_addProxyPrincipal,
    action_removeProxyPrincipal
)

from twistedcaldav.config import config
from twistedcaldav.extensions import DAVFile, ReadOnlyResourceMixIn

from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twext.web2.http import Response
from twisted.python.modules import getModule
from twext.web2.http_headers import MimeType
from zope.interface.declarations import implements
from twext.web2.stream import MemoryStream
from twext.web2.dav import davxml

from twisted.web.iweb import ITemplateLoader
from twisted.web.template import (
    Element, renderer, XMLFile, flattenString
)



class WebAdminPage(Element):
    """
    Web administration renderer for HTML.

    @ivar resource: a L{WebAdminResource}.
    """

    loader = XMLFile(
        getModule(__name__).filePath.sibling("template.html").open()
    )

    def __init__(self, resource):
        super(WebAdminPage, self).__init__()
        self.resource = resource


    @renderer
    def main(self, request, tag):
        """
        Main renderer, which fills page-global slots like 'title'.
        """
        searchTerm = request.args.get('resourceSearch', [''])[0]
        return tag.fillSlots(resourceSearch=searchTerm)


    @renderer
    @inlineCallbacks
    def hasSearchResults(self, request, tag):
        """
        Renderer which detects if there are resource search results and
        continues if so.
        """
        yield self.performSearch(request)
        returnValue(tag)


    @renderer
    @inlineCallbacks
    def noSearchResults(self, request, tag):
        """
        Renderer which detects if there are resource search results and
        continues if so.
        """
        rows = yield self.performSearch(request)
        if rows:
            returnValue("")
        else:
            returnValue(tag)


    _searchResults = None

    @inlineCallbacks
    def performSearch(self, request):
        """
        Perform a directory search for users, groups, and resources based on the
        resourceSearch query parameter.  Cache the results of that search so
        that it will only be done once per request.
        """
        if self._searchResults is not None:
            returnValue(self._searchResults)
        searchTerm = request.args.get('resourceSearch', [''])[0]
        if searchTerm:
            results = yield self.resource.search(searchTerm)
        else:
            results = []
        self._searchResults = results
        returnValue(results)


    @renderer
    def searchResults(self, request, tag):
        """
        Renderer which renders resource search results.
        """
        d = self.performSearch(request)
        return d.addCallback(searchToSlots, tag)


    @renderer
    def resourceDetails(self, request, tag):
        """
        Renderer which fills slots for details of the resource selected by
        the resourceId request parameter.
        """
        resourceId = request.args.get('resourceId', [''])[0]
        propertyName = request.args.get('davPropertyName', [''])[0]
        proxySearch = request.args.get('proxySearch', [''])[0]
        if resourceId:
            principalResource = self.resource.getResourceById(
                request, resourceId)
            return DetailsElement(
                resourceId, principalResource, propertyName, proxySearch, tag,
                self.resource
            )
        else:
            return ""


def searchToSlots(results, tag):
    """
    Convert the result of doing a search to an iterable of tags.
    """
    for idx, record in enumerate(results):
        yield tag.clone().fillSlots(
            rowClass="even" if (idx % 2 == 0) else "odd",
            type=record.recordType,
            shortName=record.shortNames[0],
            name=record.fullName,
            typeStr={
                "users"     : "User",
                "groups"    : "Group",
                "locations" : "Place",
                "resources" : "Resource",
            }.get(record.recordType),
            shortNames=str(", ".join(record.shortNames)),
            authIds=str(", ".join(record.authIDs)),
            emails=str(", ".join(record.emailAddresses)),
        )



class stan(object):
    """
    L{ITemplateLoader} wrapper for an existing tag, in the style of Nevow's
    'stan' loader.
    """
    implements(ITemplateLoader)

    def __init__(self, tag):
        self.tag = tag


    def load(self):
        return self.tag



class DetailsElement(Element):

    def __init__(self, resourceId, principalResource, davPropertyName,
                 proxySearch, tag, adminResource):
        self.principalResource = principalResource
        self.adminResource = adminResource
        self.proxySearch = proxySearch
        tag.fillSlots(resourceTitle=unicode(principalResource),
                      resourceId=resourceId,
                      davPropertyName=davPropertyName,
                      # FIXME implement
                      proxySearch=proxySearch)
        try:
            namespace, name = davPropertyName.split("#")
        except Exception:
            self.namespace = None
            self.name = None
            self.error = davPropertyName
        else:
            self.namespace = namespace
            self.name = name
            self.error = None

        super(DetailsElement, self).__init__(loader=stan(tag))


    @renderer
    def propertyParseError(self, request, tag):
        """
        Renderer to display an error when the user specifies an invalid property
        name.
        """
        if self.error is None:
            return ""
        else:
            return tag.fillSlots(davPropertyName=self.error)


    @renderer
    @inlineCallbacks
    def davProperty(self, request, tag):
        """
        Renderer to display an error when the user specifies an invalid property
        name.
        """
        if self.error is None:
            propval = yield self.principalResource.readProperty(
                (self.namespace, self.name), request
            )
            returnValue(tag.fillSlots(value=propval.toxml()))
        else:
            returnValue("")


    @renderer
    def autoSchedule(self, request, tag):
        """
        Renderer which elides its tag for non-resource-type principals.
        """
        if (self.principalResource.record.recordType != "users" and
            self.principalResource.record.recordType != "groups"):
            return tag
        return ""


    @renderer
    def isAutoSchedule(self, request, tag):
        """
        Renderer which sets the 'selected' attribute on its tag if the resource
        is auto-schedule.
        """
        if self.principalResource.getAutoSchedule():
            tag(selected='selected')
        return tag


    @renderer
    def isntAutoSchedule(self, request, tag):
        """
        Renderer which sets the 'selected' attribute on its tag if the resource
        is not auto-schedule.
        """
        if not self.principalResource.getAutoSchedule():
            tag(selected='selected')
        return tag


    @renderer
    @inlineCallbacks
    def proxyRows(self, request, tag):
        """
        Renderer which does zipping logic to render read-only and read-write
        rows of existing proxies for the currently-viewed resource.
        """
        result = []
        (readSubPrincipal, writeSubPrincipal) = (
            proxySubprincipal(self.principalResource, "read"),
            proxySubprincipal(self.principalResource, "write")
        )
        if readSubPrincipal or writeSubPrincipal:
            (readMembers, writeMembers) = (
                (yield readSubPrincipal.readProperty(davxml.GroupMemberSet,
                                                     None)),
                (yield writeSubPrincipal.readProperty(davxml.GroupMemberSet,
                                                      None))
            )
            if readMembers.children or writeMembers.children:
                # FIXME: 'else' case needs to be handled by separate renderer
                readProxies = []
                writeProxies = []
                def getres(ref):
                    return self.adminResource.getResourceById(str(proxyHRef))
                for proxyHRef in readMembers.children:
                    readProxies.append((yield getres(proxyHRef)))
                for proxyHRef in writeMembers.children:
                    writeProxies.append((yield getres(proxyHRef)))
                lendiff = len(readProxies) - len(writeProxies)
                if lendiff > 0:
                    writeProxies += [None] * lendiff
                elif lendiff < 0:
                    readProxies += [None] * lendiff
                for idx, (readProxy, writeProxy) in enumerate(
                        zip(readProxies, writeProxies)
                    ):
                    result.append(
                        ProxyRow(tag.clone(), idx, readProxy, writeProxy)
                    )
        returnValue(result)


    def performProxySearch(self, request):
        if self.proxySearch:
            return self.adminResource.search(self.proxySearch)
        else:
            return succeed([])


    @renderer
    def proxySearchRows(self, request, tag):
        """
        Renderer which renders search results for the proxy form.
        """
        d = self.performProxySearch(request)
        return d.addCallback(searchToSlots, tag)



class ProxyRow(Element):

    def __init__(self, tag, index, readProxy, writeProxy):
        tag.fillSlots(rowClass="even" if (index % 2 == 0) else "odd")
        super(ProxyRow, self).__init__(loader=stan(tag))
        self.readProxy = readProxy
        self.writeProxy = writeProxy


    def proxies(self, proxyResource, tag):
        if proxyResource is None:
            return ''
        return tag.fillSlots(proxy=unicode(proxyResource.record),
                             type=proxyResource.record.recordType,
                             shortName=proxyResource.record.shortNames[0])


    def noProxies(self, proxyResource, tag):
        if proxyResource is None:
            return tag
        else:
            return ""


    @renderer
    def readOnlyProxies(self, request, tag):
        return self.proxies(self.readProxy, tag)


    @renderer
    def noReadOnlyProxies(self, request, tag):
        return self.noProxies(self.readProxy, tag)


    @renderer
    def readWriteProxies(self, request, tag):
        return self.proxies(self.writeProxy, tag)


    @renderer
    def noReadWriteProxies(self, request, tag):
        return self.noProxies(self.writeProxy, tag)



class WebAdminResource (ReadOnlyResourceMixIn, DAVFile):
    """
    Web administration HTTP resource.
    """

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
    def htmlContent(self, request):

        def queryValue(arg):
            return request.args.get(arg, [""])[0]

        # Read request parameters.
        resourceId = queryValue("resourceId")
        resourceSearch = queryValue("resourceSearch")
        davPropertyName = queryValue("davPropertyName")
        proxySearch = queryValue("proxySearch")

        # Begin the content
        content = (
            "%(header)s\n"
            "<h2>Resource Management</h2>\n"
            "%(search)s\n" %
            {
                "header": self.header(None),
                "search": (yield self.searchContent(self.directory,
                                                    resourceSearch))
            }
        )

        # Add details if a resource has been selected.
        if resourceId:
            principal = self.getResourceById(request, resourceId)
            yield self.resourceActions(request, principal)
            # Add the detailed content
            content += (yield self.detailContent(
                self.directory, request, principal, resourceId, davPropertyName,
                proxySearch)
            )

        # Add the footer
        content += self.footer()

        returnValue(content)


    @inlineCallbacks
    def resourceActions(self, request, principal):
        """
        Take all actions on the given principal based on the given request.
        """

        def queryValue(arg):
            return request.args.get(arg, [""])[0]

        def queryValues(arg):
            query = cgi.parse_qs(urlparse.urlparse(request.uri).query, True)
            matches = []
            for key in query.keys():
                if key.startswith(arg):
                    matches.append(key[len(arg):])
            return matches

        autoSchedule = queryValue("autoSchedule")
        makeReadProxies = queryValues("mkReadProxy|")
        makeWriteProxies = queryValues("mkWriteProxy|")
        removeProxies = queryValues("rmProxy|")

        # Update the auto-schedule value if specified.
        if autoSchedule is not None and (autoSchedule == "true" or
                                         autoSchedule == "false"):
            if ( principal.record.recordType != "users" and
                 principal.record.recordType != "groups"):
                principal.setAutoSchedule(autoSchedule == "true")

        # Update the proxies if specified.
        for proxyId in removeProxies:
            proxy = self.getResourceById(request, proxyId)
            (yield action_removeProxyPrincipal(principal, proxy,
                                               proxyTypes=["read", "write"]))

        for proxyId in makeReadProxies:
            proxy = self.getResourceById(request, proxyId)
            (yield action_addProxyPrincipal(principal, "read", proxy))

        for proxyId in makeWriteProxies:
            proxy = self.getResourceById(request, proxyId)
            (yield action_addProxyPrincipal(principal, "write", proxy))


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

            records = (yield self.search(resourceSearch))
            if records:
                records.sort(key=operator.attrgetter('fullName'))
                resultHtml = """
<table cellspacing=\"0\" cellpadding=\"3\" border=\"1\" style=\"margin-top:2px\">
  <tr class=\"odd\">
    <th>ID</th>
    <th>Full Name</th>
    <th>Type</th>
    <th>Short Names</th>
    <th>Auth IDs</th>
    <th>Email Addresses</th>
  </tr>""" % { "resourceSearch": resourceSearch }

                for _i in range(0, len(records)):
                    resultHtml += """
  <tr class=\"%(rowClass)s\">
    <td><a href=\"/admin/?resourceId=%(type)s:%(shortName)s\">select</a></td>
    <td>%(name)s</td>
    <td>%(typeStr)s</td>
    <td>%(shortNames)s</td>
    <td>%(authIds)s</td>
    <td>%(emails)s</td>
  </tr>""" % { "rowClass": "even" if _i%2 == 0 else "odd",
               "type": records[_i].recordType,
               "shortName": records[_i].shortNames[0],
               "name": records[_i].fullName,
               "typeStr": { "users"     : "User",
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
    def detailContent(self, directory, request, resource, resourceId, davPropertyName, proxySearch):

        ###
        # Resource title
        ###
        headerHtml = """
<div style=\"margin-top:15px; background-color: #777; border-bottom:1px #ffffff dotted\"></div>
<div style=\"background-color: #777; padding-top:2px; border-bottom:1px #ffffff dotted\"></div>
<h3>Resource Details: %(resourceTitle)s</h3>""" % { "resourceTitle": resource }

        ###
        # DAV properties
        ###
        propertyHtml = """
<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>
<form id=\"frm_davProperty\" name=\"davPropertyForm\" action=\"/admin/\" style=\"margin-top:15px; margin-bottom:0; padding-bottom:0\">
  Show a DAV property value:
  <input type=\"hidden\" id=\"hdn_resourceId\" name=\"resourceId\" value=\"%(resourceId)s\" />
  <input type=\"text\" id=\"txt_davPropertyName\" name=\"davPropertyName\" value=\"%(davPropertyName)s\" size=\"40\" />
  <input type=\"submit\" value=\"Get Value\" />
</form>
""" % { "resourceId": resourceId,
        "davPropertyName": davPropertyName if davPropertyName is not None and davPropertyName != "" else "DAV:#" }
        
        if davPropertyName:
            try:
                namespace, name = davPropertyName.split("#")
            except Exception:
                propertyHtml += "<div>Unable to parse property to read: <b>%s</b></div>" % davPropertyName
            else:
                result = (yield resource.readProperty((namespace, name), None))
                propertyHtml += "<div style=\"margin-top:7px\">Value of property <b>%(name)s</b>:</div><pre style=\"margin-top:5px; padding-top:0\">%(value)s</pre>" % { 
                    "name": davPropertyName, 
                    "value": cgi.escape(result.toxml())
                }

        ###
        # Auto-schedule
        ###
        autoScheduleHtml = ""
        if resource.record.recordType != "users" and resource.record.recordType != "groups":
            autoSchedule = resource.getAutoSchedule()
            autoScheduleHtml = """
<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>
<form id=\"frm_autoSchedule\" name=\"autoScheduleForm\" action=\"/admin/\" style=\"margin-top:15px\">
  <input type=\"hidden\" id=\"hdn_resourceId\" name=\"resourceId\" value=\"%(resourceId)s\" />
  <div style=\"margin-top:7px\">
    Auto-Schedule
    <select id=\"sel_autoSchedule\" name=\"autoSchedule\">
      <option value=\"true\"%(trueSelected)s>Yes</option>
      <option value=\"false\"%(falseSelected)s>No</option>
    </select>
    <input type=\"submit\" value=\"Change\" />
  </div>
</form>\n""" % { "resourceId": resourceId,
               "trueSelected": " selected=\"selected\"" if autoSchedule else "",
               "falseSelected": "" if autoSchedule else " selected=\"selected\"" }

        ###
        # Current proxies
        ###
        currentProxiesHtml = "\n<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>"
        
        (readSubPrincipal, writeSubPrincipal) = (proxySubprincipal(resource, "read"), proxySubprincipal(resource, "write"))
        if readSubPrincipal or writeSubPrincipal:
            (readMembers, writeMembers) = ((yield readSubPrincipal.readProperty(davxml.GroupMemberSet, None)), (yield writeSubPrincipal.readProperty(davxml.GroupMemberSet, None)))

            if readMembers.children or writeMembers.children:
                currentProxiesHtml += """
<form id=\"frm_proxies\" name=\"proxiesForm\" action=\"/admin/\" style=\"margin-top:15px\">
  <input type=\"hidden\" id=\"hdn_resourceId\" name=\"resourceId\" value=\"%(resourceId)s\" />
  <table cellspacing=\"0\" cellpadding=\"3\" border=\"1\">
    <tr class=\"odd\">
      <th colspan=\"2\">Read-Only Proxies</th>
      <th colspan=\"2\">Read-Write Proxies</th>
    </tr>\n""" % { "resourceTitle": resource,
                   "resourceId": resourceId }

                for _i in range(0, max(len(readMembers.children), len(writeMembers.children))):
                    currentProxiesHtml += "    <tr class=\"%(rowClass)s\">" % { "rowClass": "even" if _i%2 == 0 else "odd" }
                    if (_i < len(readMembers.children)) :
                        proxyResource = (yield self.getResourceById(request, str(readMembers.children[_i])))
                        currentProxiesHtml += """
      <td>%(proxy)s</td>
      <td>
        <input type=\"submit\" name=\"mkWriteProxy|%(type)s:%(shortName)s\" value=\"Make Read-Write\" />
        <input type=\"submit\" name=\"rmProxy|%(type)s:%(shortName)s\" value=\"Remove Proxy\" />
      </td>""" % { "proxy": proxyResource,
                   "type": proxyResource.record.recordType,
                   "shortName": proxyResource.record.shortNames[0]
                 }
                    else :
                        currentProxiesHtml += "\n      <td colspan=\"2\"></td>"
                    if (_i < len(writeMembers.children)) :
                        proxyResource = (yield self.getResourceById(request, str(writeMembers.children[_i])))
                        currentProxiesHtml += """
      <td>%(proxy)s</td>
      <td>
        <input type=\"submit\" name=\"mkReadProxy|%(type)s:%(shortName)s\" value=\"Make Read-Only\" />
        <input type=\"submit\" name=\"rmProxy|%(type)s:%(shortName)s\" value=\"Remove Proxy\" />
      </td>""" % { "proxy": proxyResource,
                   "type": proxyResource.record.recordType,
                   "shortName": proxyResource.record.shortNames[0]
                 }
                    else :
                        currentProxiesHtml += "\n      <td colspan=\"2\"></td>"
                    currentProxiesHtml += "\n    </tr>\n"
    
                currentProxiesHtml += "  </table>\n</form>\n"
            else:
                currentProxiesHtml += "<div style=\"margin-top:15px\">This resource has no proxies.</div>\n"
        else:
            currentProxiesHtml += "<div style=\"margin-top:15px\">This resource has no proxies.</div>\n"

        ###
        # Search for new proxies
        ###
        proxySearchHtml = """
<div style=\"margin-top:15px; border-bottom:1px #444444 dotted\"></div>
<form id=\"frm_proxySearch\" name=\"proxySearchForm\" action=\"/admin/\" style=\"margin-top:15px; margin-bottom:0; padding-bottom:0\">
  Search to add proxies:
  <input type=\"hidden\" id=\"hdn_resourceId\" name=\"resourceId\" value=\"%(resourceId)s\" />
  <input type=\"text\" id=\"txt_proxySearch\" name=\"proxySearch\" value=\"%(proxySearch)s\" size=\"40\" />
  <input type=\"submit\" value=\"Search\" />
</form>
""" % { "resourceId": resourceId,
        "proxySearch": proxySearch }

        # Perform the search if a parameter was specified.
        if proxySearch:
            records = (yield self.search(proxySearch))
            if records:
                records.sort(key=operator.attrgetter('fullName'))

                proxySearchHtml += """
<form id=\"frm_proxyAdd\" name=\"proxyAddForm\" action=\"/admin/\" style=\"margin-top:2px; padding-top:0\">
  <input type=\"hidden\" id=\"hdn_resourceId\" name=\"resourceId\" value=\"%(resourceId)s\" />
  <table cellspacing=\"0\" cellpadding=\"3\" border=\"1\">
    <tr class=\"odd\">
      <th>Full Name</th>
      <th>Type</th>
      <th>Short Names</th>
      <th>Email Addresses</th>
      <th></th>
    </tr>""" % { "resourceId": resourceId }

                for _i in range(0, len(records)):
                    proxySearchHtml += """
    <tr class=\"%(rowClass)s\">
      <td>%(name)s</td>
      <td>%(typeStr)s</td>
      <td>%(shortNames)s</td>
      <td>%(emails)s</td>
      <td>
        <input type=\"submit\" name=\"mkReadProxy|%(type)s:%(shortName)s\" value=\"Make Read-Only Proxy\" />
        <input type=\"submit\" name=\"mkWriteProxy|%(type)s:%(shortName)s\" value=\"Make Read-Write Proxy\" />
      </td>
    </tr>""" % { "rowClass": "even" if _i%2 == 0 else "odd",
                 "type": records[_i].recordType,
                 "shortName": records[_i].shortNames[0],
                 "name": records[_i].fullName,
                 "typeStr": { "users"     : "User",
                           "groups"    : "Group",
                           "locations" : "Place",
                           "resources" : "Resource",
                           }.get(records[_i].recordType),
                 "shortNames": str(", ".join(records[_i].shortNames),),
                 "emails": str(", ".join(records[_i].emailAddresses),)
             }
                proxySearchHtml += "\n  </table>\n</form>\n"
            else:
                proxySearchHtml += "<div style=\"margin-top:4px\">No matches found for proxy resource <b>%(proxySearch)s</b>.</div>\n" % { "proxySearch": proxySearch }
        
        ###
        # Put it all together
        ###
        detailHtml = "%s%s%s%s%s" % (headerHtml, propertyHtml, autoScheduleHtml, currentProxiesHtml, proxySearchHtml)

        returnValue(detailHtml)


    @inlineCallbacks
    def renderNew(self, request):
        """
        Create a L{WebAdminPage} to render HTML content for this request, and
        return a response.
        """
        htmlContent = yield flattenString(request, WebAdminPage(self))
        response = Response()
        response.stream = MemoryStream(htmlContent)
        for (header, value) in (
                ("content-type", self.contentType()),
                ("content-encoding", self.contentEncoding()),
            ):
            if value is not None:
                response.headers.setHeader(header, value)
        returnValue(response)


    def render(self, request):

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
        htmlContent = self.htmlContent(request)
        htmlContent.addCallback(_defer)
        return htmlContent

    def getResourceById(self, request, resourceId):
        if resourceId.startswith("/"):
            return request.locateResource(resourceId)
        else:
            return principalForPrincipalID(resourceId, directory=self.directory)

    @inlineCallbacks
    def search(self, searchStr):
        fields = []
        for fieldName in ("fullName", "firstName", "lastName", "emailAddresses"):
            fields.append((fieldName, searchStr, True, "contains"))
        
        records = list((yield self.directory.recordsMatchingFields(fields)))
        returnValue(records)
