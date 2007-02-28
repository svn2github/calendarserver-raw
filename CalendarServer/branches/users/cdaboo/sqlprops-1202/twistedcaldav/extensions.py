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
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##
from twisted.web2.dav.element.base import dav_namespace

from twisted.web2.dav.util import parentForURL
from twisted.web2.dav.resource import TwistedACLInheritable
from twisted.web2.dav.util import joinURL
from twisted.internet import reactor
from twisted.web2.dav.resource import AccessDeniedError
from twisted.internet.defer import Deferred
from twisted.web2.http_headers import generateContentType
from twistedcaldav.sqlprops import sqlPropertyStore

"""
Extensions to web2.dav
"""

__all__ = [
    "DAVResource",
    "DAVFile",
    "ReadOnlyResourceMixIn",
    "SudoAuthIDMixin",
]

import urllib
import cgi
import os
import time

from twisted.python import log
from twisted.internet.defer import succeed, deferredGenerator, waitForDeferred
from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, Response, RedirectResponse
from twisted.web2.http_headers import MimeType
from twisted.web2.stream import FileStream
from twisted.web2.static import MetaDataMixin
from twisted.web2.dav import davxml
from twisted.web2.dav.davxml import dav_namespace
from twisted.web2.dav.http import StatusResponse
from twisted.web2.dav.static import DAVFile as SuperDAVFile
from twisted.web2.dav.resource import DAVResource as SuperDAVResource
from twisted.web2.dav.resource import DAVPrincipalResource as SuperDAVPrincipalResource
from twisted.web2.dav.resource import TwistedResourceClass
from twistedcaldav.directory.sudo import SudoDirectoryService


class SudoAuthIDMixin(object):
    """
    Mixin class to let DAVResource, and DAVFile subclasses below know
    about sudoer principals and how to find their AuthID
    """

    def findPrincipalForAuthID(self, authid):
        """
        Return an authentication and authorization principal identifiers for 
        the authentication identifier passed in.  Check for sudo users before
        regular users.
        """
        for collection in self.principalCollections():
            principal = collection.principalForShortName(
                SudoDirectoryService.recordType_sudoers, 
                authid)
            if principal is not None:
                return principal

        return super(SudoAuthIDMixin, self).findPrincipalForAuthID(authid)


class DAVPropertyHandlerMixin(object):

    def create(self):
        self.updateProperties()

    def update(self):
        self.updateProperties()

    def updateProperties(self):
        """
        Push "live" properties into the dead property store".
        """
        
        props_to_set = []
        # resourcetype
        if self.isCollection():
            props_to_set.append(davxml.ResourceType.collection)
        else:
            props_to_set.append(davxml.ResourceType.empty)
            
        # getetag
        etag = self.etag()
        if etag is not None:
            props_to_set.append(davxml.GETETag(etag.generate()))

        # getcontenttype
        mimeType = self.contentType()
        if mimeType is not None:
            mimeType.params = None # WebDAV getcontenttype property does not include parameters
            props_to_set.append(davxml.GETContentType(generateContentType(mimeType)))

        # getcontentlength
        length = self.contentLength()
        if length is None:
            # TODO: really we should "render" the resource and 
            # determine its size from that but for now we just 
            # return an empty element.
            props_to_set.append(davxml.GETContentLength(""))
        else:
            props_to_set.append(davxml.GETContentLength(str(length)))

        # getlastmodified
        lastModified = self.lastModified()
        if lastModified is not None:
            props_to_set.append(davxml.GETLastModified.fromDate(lastModified))

        # creationdate
        creationDate = self.creationDate()
        if creationDate is not None:
            props_to_set.append(davxml.CreationDate.fromDate(creationDate))

        # displayname
        displayName = self.displayName()
        if displayName is not None:
            props_to_set.append(davxml.DisplayName(displayName))

        # acl
        props_to_set.append(self.defaultAccessControlList())
        #from twistedcaldav.static import calendarPrivilegeSet
        #props_to_set.append(calendarPrivilegeSet)
        
        # resource class
        props_to_set.append(TwistedResourceClass(self.__class__.__name__))

        # Now write out all properties in one go
        self.deadProperties().setSeveral(props_to_set)

    def isClassProperty(self, property):
        if isinstance(property, tuple):
            qname = property
        else:
            qname = property.qname()
            
        return qname in ((dav_namespace, "supported-privilege-set"),)

    def computeClassProperty(self, classname, property):
        from twistedcaldav.static import calendarPrivilegeSet

        if isinstance(property, tuple):
            namespace, name = property
        else:
            namespace, name = property.qname()
        
        if namespace == dav_namespace:
            if name == "supported-privilege-set":
                return calendarPrivilegeSet

class DAVFastACLMixin(object):

    def findChildNames(self, request, callbackAllowed, callbackForbidden=None, privileges=None, inherited_aces=None):
        
        from twistedcaldav.resource import AccessDisabled
        from twistedcaldav.static import calendarPrivilegeSet

        # Get aces for children
        acls = self.deadProperties().getSeveralResources((
            davxml.ACL.qname(),
            TwistedResourceClass.qname(),
            davxml.GETETag.qname(),
            AccessDisabled.qname(),
        ))
        aclmap = {}
        for name, props in acls.iteritems():
            if props.has_key(AccessDisabled.qname()):
                if callbackForbidden:
                    callbackForbidden(name)
                continue
            acl = props.get(davxml.ACL.qname(), None)
            privyset = self.computeClassProperty(props.get(TwistedResourceClass.qname(), None), (dav_namespace, "supported-privilege-set"))
            aclmap.setdefault((acl, privyset), []).append(name)
            
        # Now determine whether each ace satisfies privileges
        for (acl, privyset), names in aclmap.iteritems():
            checked = waitForDeferred(self.checkACLPrivilege(request, acl, privyset, privileges, inherited_aces))
            yield checked
            checked = checked.getResult()
            if checked:
                for name in names:
                    callbackAllowed(name)
            elif callbackForbidden:
                for name in names:
                    callbackForbidden(name)

        yield None

    findChildNames = deferredGenerator(findChildNames)

    def checkACLPrivilege(self, request, acl, privyset, privileges, inherited_aces):
        
        principal = self.currentPrincipal(request)

        # Other principals types don't make sense as actors.
        assert (
            principal.children[0].name in ("unauthenticated", "href"),
            "Principal is not an actor: %r" % (principal,)
        )

        acl = self.fullAccessControlList(acl, inherited_aces)

        pending = list(privileges)
        denied = []

        for ace in acl.children:
            for privilege in tuple(pending):
                if not self.matchPrivilege(davxml.Privilege(privilege), ace.privileges, privyset):
                    continue

                match = waitForDeferred(self.matchPrincipal(principal, ace.principal, request))
                yield match
                match = match.getResult()

                if match:
                    if ace.invert:
                        continue
                else:
                    if not ace.invert:
                        continue

                pending.remove(privilege)

                if not ace.allow:
                    denied.append(privilege)

        yield len(denied) + len(pending) == 0

    checkACLPrivilege = deferredGenerator(checkACLPrivilege)

    def fullAccessControlList(self, acl, inherited_aces):
        """
        See L{IDAVResource.accessControlList}.

        This implementation looks up the ACL in the private property
        C{(L{twisted_private_namespace}, "acl")}.
        If no ACL has been stored for this resource, it returns the value
        returned by C{defaultAccessControlList}.
        If access is disabled it will return C{None}.
        """
        #
        # Inheritance is problematic. Here is what we do:
        #
        # 1. A private element <Twisted:inheritable> is defined for use inside
        #    of a <DAV:ace>. This private element is removed when the ACE is
        #    exposed via WebDAV.
        #
        # 2. When checking ACLs with inheritance resolution, the server must
        #    examine all parent resources of the current one looking for any
        #    <Twisted:inheritable> elements.
        #
        # If those are defined, the relevant ace is applied to the ACL on the
        # current resource.
        #

        # Dynamically update privileges for those ace's that are inherited.
        if acl:
            aces = list(acl.children)
        else:
            aces = []

        aces.extend(inherited_aces)

        acl = davxml.ACL(*aces)

        return acl
    
    def findChildrenBatch(self, depth, request, callback, privileges=None, inherited_aces=None):
        """
        See L{IDAVResource.findChildren}.

        This implementation works for C{depth} values of C{"0"}, C{"1"}, 
        and C{"infinity"}.  As long as C{self.listChildren} is implemented
        """
        assert depth in ("0", "1", "infinity"), "Invalid depth: %s" % (depth,)

        if depth == "0" or not self.isCollection():
            return succeed(None)

        # Get aces for children
        aclprops = self.deadProperties().getSeveralResources((
            davxml.ACL.qname(),
        ))
        acls = {}
        for name, props in acls.iteritems():
            acls[name] = props.get(davxml.ACL.qname(), None)
            
        completionDeferred = Deferred()
        basepath = request.urlForResource(self)
        children = list(self.listChildren())

        def checkPrivilegesError(failure):
            failure.trap(AccessDeniedError)
            reactor.callLater(0, getChild)

        def checkPrivileges(child):
            if child is None:
                return None

            if privileges is None:
                return child
   
            cname = os.path.basename(request.urlForResource(self))
            d = child.checkPrivilegesBatch(request, privileges, inherited_aces=inherited_aces, acl=acls.get(cname, None))
            d.addCallback(lambda _: child)
            return d

        def gotChild(child, childpath):
            if child is None:
                callback(None, childpath + "/")
            else:
                if child.isCollection():
                    callback(child, childpath + "/")
                    if depth == "infinity":
                        d = child.findChildren(depth, request, callback, privileges)
                        d.addCallback(lambda x: reactor.callLater(0, getChild))
                        return d
                else:
                    callback(child, childpath)

            reactor.callLater(0, getChild)

        def getChild():
            try:
                childname = children.pop()
            except IndexError:
                completionDeferred.callback(None)
            else:
                childpath = joinURL(basepath, childname)
                d = request.locateChildResource(self, childname)
                d.addCallback(checkPrivileges)
                d.addCallbacks(gotChild, checkPrivilegesError, (childpath,))
                d.addErrback(completionDeferred.errback)

        getChild()

        return completionDeferred

    def checkPrivilegesBatch(self, request, privileges, recurse=False, principal=None, inherited_aces=None, acl=None):
        """
        Check whether the given principal has the given privileges.
        (RFC 3744, section 5.5)
        @param request: the request being processed.
        @param privileges: an iterable of L{davxml.WebDAVElement} elements
            denoting access control privileges.
        @param recurse: C{True} if a recursive check on all child
            resources of this resource should be performed as well,
            C{False} otherwise.
        @param principal: the L{davxml.Principal} to check privileges
            for.  If C{None}, it is deduced from C{request} by calling
            L{currentPrincipal}.
        @param inherited_aces: a list of L{davxml.ACE}s corresponding to the precomputed
            inheritable aces from the parent resource hierarchy.
        @return: a L{Deferred} that callbacks with C{None} or errbacks with an
            L{AccessDeniedError}
        """
        if principal is None:
            principal = self.currentPrincipal(request)

        supportedPrivs = waitForDeferred(self.supportedPrivileges(request))
        yield supportedPrivs
        supportedPrivs = supportedPrivs.getResult()

        # Other principals types don't make sense as actors.
        assert (
            principal.children[0].name in ("unauthenticated", "href"),
            "Principal is not an actor: %r" % (principal,)
        )

        errors = []

        resources = [(self, None)]

        if recurse:
            x = self.findChildren("infinity", request, lambda x, y: resources.append((x,y)))
            x = waitForDeferred(x)
            yield x
            x.getResult()

        for resource, uri in resources:
            acl = waitForDeferred(resource.accessControlListBatch(request, inherited_aces=inherited_aces, acl=acl))
            yield acl
            acl = acl.getResult()

            # Check for disabled
            if acl is None:
                errors.append((uri, list(privileges)))
                continue

            pending = list(privileges)
            denied = []

            for ace in acl.children:
                for privilege in tuple(pending):
                    if not self.matchPrivilege(davxml.Privilege(privilege), ace.privileges, supportedPrivs):
                        continue

                    match = waitForDeferred(self.matchPrincipal(principal, ace.principal, request))
                    yield match
                    match = match.getResult()

                    if match:
                        if ace.invert:
                            continue
                    else:
                        if not ace.invert:
                            continue

                    pending.remove(privilege)

                    if not ace.allow:
                        denied.append(privilege)

            denied += pending # If no matching ACE, then denied

            if denied: 
                errors.append((uri, denied))

        if errors:
            raise AccessDeniedError(errors,)
        
        yield None

    checkPrivilegesBatch = deferredGenerator(checkPrivilegesBatch)

    def accessControlListBatch(self, request, inheritance=True, expanding=False, inherited_aces=None, acl=None):
        """
        See L{IDAVResource.accessControlList}.

        This implementation looks up the ACL in the private property
        C{(L{twisted_private_namespace}, "acl")}.
        If no ACL has been stored for this resource, it returns the value
        returned by C{defaultAccessControlList}.
        If access is disabled it will return C{None}.
        """
        #
        # Inheritance is problematic. Here is what we do:
        #
        # 1. A private element <Twisted:inheritable> is defined for use inside
        #    of a <DAV:ace>. This private element is removed when the ACE is
        #    exposed via WebDAV.
        #
        # 2. When checking ACLs with inheritance resolution, the server must
        #    examine all parent resources of the current one looking for any
        #    <Twisted:inheritable> elements.
        #
        # If those are defined, the relevant ace is applied to the ACL on the
        # current resource.
        #
        myURL = None

        def getMyURL():
            url = request.urlForResource(self)

            assert url is not None, "urlForResource(self) returned None for resource %s" % (self,)

            return url

        if acl is None:
            # Produce a sensible default for an empty ACL.
            if myURL is None:
                myURL = getMyURL()

            if myURL == "/":
                # If we get to the root without any ACLs, then use the default.
                acl = self.defaultRootAccessControlList()
            else:
                acl = self.defaultAccessControlList()

        # Dynamically update privileges for those ace's that are inherited.
        if inheritance:
            aces = list(acl.children)

            if myURL is None:
                myURL = getMyURL()

            if inherited_aces is None:
                if myURL != "/":
                    parentURL = parentForURL(myURL)
    
                    parent = waitForDeferred(request.locateResource(parentURL))
                    yield parent
                    parent = parent.getResult()
    
                    if parent:
                        parent_acl = waitForDeferred(
                            parent.accessControlList(request, inheritance=True, expanding=True)
                        )
                        yield parent_acl
                        parent_acl = parent_acl.getResult()
    
                        # Check disabled
                        if parent_acl is None:
                            yield None
                            return
    
                        for ace in parent_acl.children:
                            if ace.inherited:
                                aces.append(ace)
                            elif TwistedACLInheritable() in ace.children:
                                # Adjust ACE for inherit on this resource
                                children = list(ace.children)
                                children.remove(TwistedACLInheritable())
                                children.append(davxml.Inherited(davxml.HRef(parentURL)))
                                aces.append(davxml.ACE(*children))
            else:
                aces.extend(inherited_aces)

            # Always filter out any remaining private properties when we are
            # returning the ACL for the final resource after doing parent
            # inheritance.
            if not expanding:
                aces = [
                    davxml.ACE(*[
                        c for c in ace.children
                        if c != TwistedACLInheritable()
                    ])
                    for ace in aces
                ]

            acl = davxml.ACL(*aces)

        yield acl

    accessControlListBatch = deferredGenerator(accessControlListBatch)

class DAVResource (SudoAuthIDMixin, DAVPropertyHandlerMixin, DAVFastACLMixin, SuperDAVResource):
    """
    Extended L{twisted.web2.dav.resource.DAVResource} implementation.
    """

class DAVPrincipalResource (DAVPropertyHandlerMixin, DAVFastACLMixin, SuperDAVPrincipalResource):
    """
    Extended L{twisted.web2.dav.static.DAVFile} implementation.
    """
    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        if qname == (dav_namespace, "resourcetype"):
            return succeed(self.resourceType())

        return super(DAVPrincipalResource, self).readProperty(property, request)

    def resourceType(self):
        # Allow live property to be overriden by dead property
        if self.deadProperties().contains((dav_namespace, "resourcetype")):
            return self.deadProperties().get((dav_namespace, "resourcetype"))
        if self.isCollection():
            return davxml.ResourceType(davxml.Collection(), davxml.Principal())
        else:
            return davxml.ResourceType(davxml.Principal())


class DAVFile (SudoAuthIDMixin, DAVPropertyHandlerMixin, DAVFastACLMixin, SuperDAVFile):
    """
    Extended L{twisted.web2.dav.static.DAVFile} implementation.
    """
    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = sqlPropertyStore(self)
        return self._dead_properties

    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        if qname == (dav_namespace, "resourcetype"):
            return succeed(self.resourceType())

        return super(DAVFile, self).readProperty(property, request)

    def resourceType(self):
        # Allow live property to be overriden by dead property
        if self.deadProperties().contains((dav_namespace, "resourcetype")):
            return self.deadProperties().get((dav_namespace, "resourcetype"))
        if self.isCollection():
            return davxml.ResourceType.collection
        return davxml.ResourceType.empty

    def render(self, req):
        if not self.fp.exists():
            return responsecode.NOT_FOUND

        if self.fp.isdir():
            if req.uri[-1] != "/":
                # Redirect to include trailing '/' in URI
                return RedirectResponse(req.unparseURL(path=req.path+'/'))
            else:
                ifp = self.fp.childSearchPreauth(*self.indexNames)
                if ifp:
                    # Render from the index file
                    return self.createSimilarFile(ifp.path).render(req)

                return self.renderDirectory(req)

        try:
            f = self.fp.open()
        except IOError, e:
            import errno
            if e[0] == errno.EACCES:
                return responsecode.FORBIDDEN
            elif e[0] == errno.ENOENT:
                return responsecode.NOT_FOUND
            else:
                raise

        response = Response()
        response.stream = FileStream(f, 0, self.fp.getsize())

        for (header, value) in (
            ("content-type", self.contentType()),
            ("content-encoding", self.contentEncoding()),
        ):
            if value is not None:
                response.headers.setHeader(header, value)

        return response

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
            "h1 {padding: 0.1em; background-color: #777; color: white; border-bottom: thin white dashed;}"
        )

    def renderDirectory(self, request):
        """
        Render a directory listing.
        """
        output = [
            """<html>"""
            """<head>"""
            """<title>Collection listing for %(path)s</title>"""
            """<style>%(style)s</style>"""
            """</head>"""
            """<body>"""
            % {
                "path": "%s" % cgi.escape(urllib.unquote(request.path)),
                "style": self.directoryStyleSheet(),
            }
        ]

        def gotBody(body, output=output):
            output.append(body)
            output.append("</body></html>")

            output = "".join(output)

            if isinstance(output, unicode):
                output = output.encode("utf-8")

            response = Response(200, {}, output)
            response.headers.setHeader("content-type", MimeType("text", "html"))
            response.headers.setHeader("content-encoding", "utf-8")
            return response

        d = self.renderDirectoryBody(request)
        d.addCallback(gotBody)
        return d

    def renderDirectoryBody(self, request):
        """
        Generate a directory listing table in HTML.
        """
        output = [
            """<div class="directory-listing">"""
            """<h1>Collection Listing</h1>"""
            """<table>"""
            """<tr><th>Name</th> <th>Size</th> <th>Last Modified</th> <th>MIME Type</th></tr>"""
        ]

        even = False
        for name in sorted(self.listChildren()):
            child = self.getChild(name)

            url, name, size, lastModified, contentType = self.getChildDirectoryEntry(child, name)

            # FIXME: gray out resources that are not readable
            output.append(
                """<tr class="%(even)s">"""
                """<td><a href="%(url)s">%(name)s</a></td>"""
                """<td align="right">%(size)s</td>"""
                """<td>%(lastModified)s</td>"""
                """<td>%(type)s</td>"""
                """</tr>"""
                % {
                    "even": even and "even" or "odd",
                    "url": url,
                    "name": cgi.escape(name),
                    "size": size,
                    "lastModified": lastModified,
                    "type": contentType,
                }
            )
            even = not even

        output.append(
            """</table></div>"""
            """<div class="directory-listing">"""
            """<h1>Properties</h1>"""
            """<table>"""
            """<tr><th>Name</th> <th>Value</th></tr>"""
        )

        @deferredGenerator
        def gotProperties(qnames):
            even = False
            for qname in qnames:
                property = waitForDeferred(self.readProperty(qname, request))
                yield property
                try:
                    property = property.getResult()
                    name = property.sname()
                    value = property.toxml()
                except HTTPError, e:
                    if e.response.code != responsecode.UNAUTHORIZED:
                        log.err("Unable to read property %s for dirlist: %s" % (qname, e))
                        raise

                    name = "{%s}%s" % qname
                    value = "(access forbidden)"

                output.append(
                    """<tr class="%(even)s">"""
                    """<td valign="top">%(name)s</td>"""
                    """<td><pre>%(value)s</pre></td>"""
                    """</tr>"""
                    % {
                        "even": even and "even" or "odd",
                        "name": name,
                        "value": cgi.escape(value),
                    }
                )
                even = not even

            output.append("</div>")

            yield "".join(output)

        d = self.listProperties(request)
        d.addCallback(gotProperties)
        return d

    def getChildDirectoryEntry(self, child, name):
        def orNone(value, default="?", f=None):
            if value is None:
                return default
            elif f is not None:
                return f(value)
            else:
                return value
            
        url = urllib.quote(name, '/')
        if isinstance(child, SuperDAVFile) and child.isCollection():
            url += "/"
            name += "/"

        if isinstance(child, MetaDataMixin):
            size = child.contentLength()
            lastModified = child.lastModified()
            contentType = child.contentType()
        else:
            size = None
            lastModified = None
            contentType = None

        if self.fp.isdir():
            contentType = "(collection)"
        else:
            contentType = self._orNone(
                contentType,
                default="-",
                f=lambda m: "%s/%s %s" % (m.mediaType, m.mediaSubtype, m.params)
            )

        return (
            url,
            name,
            orNone(size),
            orNone(
                lastModified,
                default="",
                f=lambda t: time.strftime("%Y-%b-%d %H:%M", time.localtime(t))
             ),
             contentType,
         )

class ReadOnlyWritePropertiesResourceMixIn (object):
    """
    Read only that will allow writing of properties resource.
    """
    readOnlyResponse = StatusResponse(
        responsecode.FORBIDDEN,
        "Resource is read only."
    )

    def _forbidden(self, request):
        return self.readOnlyResponse

    http_DELETE = _forbidden
    http_MOVE   = _forbidden
    http_PUT    = _forbidden

class ReadOnlyResourceMixIn (ReadOnlyWritePropertiesResourceMixIn):
    """
    Read only resource.
    """
    http_PROPPATCH = ReadOnlyWritePropertiesResourceMixIn._forbidden

    def writeProperty(self, property, request):
        raise HTTPError(self.readOnlyResponse)
