# -*- test-case-name: twistedcaldav.test -*-
##
# Copyright (c) 2005-2010 Apple Inc. All rights reserved.
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
from twistedcaldav.index import Index

"""
CalDAV-aware static resources.
"""

__all__ = [
    "CalDAVFile",
    "AutoProvisioningFileMixIn",
    "DirectoryBackedAddressBookFile",
]

import os
import errno
from urlparse import urlsplit

from twext.python.log import Logger

from twisted.internet.defer import fail, succeed, inlineCallbacks, returnValue, maybeDeferred
from twext.web2 import responsecode, http, http_headers
from twext.web2.http import HTTPError, StatusResponse
from twext.web2.dav import davxml
from twext.web2.dav.fileop import mkcollection, rmdir
from twext.web2.dav.http import ErrorResponse
from twext.web2.dav.idav import IDAVResource
from twext.web2.dav.resource import AccessDeniedError
from twext.web2.dav.resource import davPrivilegeSet
from twext.web2.dav.util import parentForURL, bindMethods

from twistedcaldav import caldavxml
from twistedcaldav import carddavxml
from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.config import config
from twistedcaldav.customxml import TwistedCalendarAccessProperty, TwistedScheduleMatchETags
from twistedcaldav.datafilters.peruserdata import PerUserDataFilter
from twistedcaldav.extensions import DAVFile, CachingPropertyStore
from twistedcaldav.linkresource import LinkFollowerMixIn

from twistedcaldav.ical import Component as iComponent
from twistedcaldav.ical import Property as iProperty
from twistedcaldav.resource import CalDAVResource, isCalendarCollectionResource, isPseudoCalendarCollectionResource
from twistedcaldav.resource import isAddressBookCollectionResource
from twistedcaldav.datafilters.privateevents import PrivateEventFilter
from twistedcaldav.directorybackedaddressbook import DirectoryBackedAddressBookResource
from twistedcaldav.directory.resource import AutoProvisioningResourceMixIn
from twistedcaldav.vcardindex import AddressBookIndex

log = Logger()

class ReadOnlyResourceMixIn(object):

    def http_PUT        (self, request): return responsecode.FORBIDDEN
    def http_COPY       (self, request): return responsecode.FORBIDDEN
    def http_MOVE       (self, request): return responsecode.FORBIDDEN
    def http_DELETE     (self, request): return responsecode.FORBIDDEN
    def http_MKCOL      (self, request): return responsecode.FORBIDDEN

    def http_MKCALENDAR(self, request):
        return ErrorResponse(
            responsecode.FORBIDDEN,
            (caldav_namespace, "calendar-collection-location-ok")
        )



class CalDAVFile (LinkFollowerMixIn, CalDAVResource, DAVFile):
    """
    CalDAV-accessible L{DAVFile} resource.
    """
#    def __repr__(self):
#        if self.isCalendarCollection():
#            return "<%s (calendar collection): %s>" % (self.__class__.__name__, self.fp.path)
#        else:
#            return super(CalDAVFile, self).__repr__()

    def __eq__(self, other):
        if not isinstance(other, CalDAVFile):
            return False
        return self.fp.path == other.fp.path

    def checkPreconditions(self, request):
        """
        We override the base class to handle the special implicit scheduling weak ETag behavior
        for compatibility with old clients using If-Match.
        """
        
        if config.Scheduling.CalDAV.ScheduleTagCompatibility:
            
            if self.exists() and self.hasDeadProperty(TwistedScheduleMatchETags):
                etags = self.readDeadProperty(TwistedScheduleMatchETags).children
                if len(etags) > 1:
                    # This is almost verbatim from twext.web2.static.checkPreconditions
                    if request.method not in ("GET", "HEAD"):
                        
                        # Loop over each tag and succeed if any one matches, else re-raise last exception
                        exists = self.exists()
                        last_modified = self.lastModified()
                        last_exception = None
                        for etag in etags:
                            try:
                                http.checkPreconditions(
                                    request,
                                    entityExists = exists,
                                    etag = http_headers.ETag(etag),
                                    lastModified = last_modified,
                                )
                            except HTTPError, e:
                                last_exception = e
                            else:
                                break
                        else:
                            if last_exception:
                                raise last_exception
            
                    # Check per-method preconditions
                    method = getattr(self, "preconditions_" + request.method, None)
                    if method:
                        response = maybeDeferred(method, request)
                        response.addCallback(lambda _: request)
                        return response
                    else:
                        return None

        return super(CalDAVFile, self).checkPreconditions(request)

    def deadProperties(self, caching=True):
        if not hasattr(self, "_dead_properties"):
            # FIXME: this code should actually be dead, as the property store
            # should be initialized as part of the traversal process.
 
            # Get the property store from super
            deadProperties = super(CalDAVFile, self).deadProperties()

            if caching:
                # Wrap the property store in a memory store
                deadProperties = CachingPropertyStore(deadProperties)

            self._dead_properties = deadProperties

        return self._dead_properties

    ##
    # CalDAV
    ##

    def createCalendar(self, request):
        """
        External API for creating a calendar.  Verify that the parent is a
        collection, exists, is I{not} a calendar collection; that this resource
        does not yet exist, then create it.

        @param request: the request used to look up parent resources to
            validate.

        @type request: L{twext.web2.iweb.IRequest}

        @return: a deferred that fires when a calendar collection has been
            created in this resource.
        """
        if self.fp.exists():
            log.err("Attempt to create collection where file exists: %s" % (self.fp.path,))
            raise HTTPError(StatusResponse(responsecode.NOT_ALLOWED, "File exists"))

        # newStore guarantees that we always have a parent calendar home
        #if not self.fp.parent().isdir():
        #    log.err("Attempt to create collection with no parent: %s" % (self.fp.path,))
        #    raise HTTPError(StatusResponse(responsecode.CONFLICT, "No parent collection"))

        #
        # Verify that no parent collection is a calendar also
        #
        log.msg("Creating calendar collection %s" % (self,))

        def _defer(parent):
            if parent is not None:
                log.err("Cannot create a calendar collection within a calendar collection %s" % (parent,))
                raise HTTPError(ErrorResponse(
                    responsecode.FORBIDDEN,
                    (caldavxml.caldav_namespace, "calendar-collection-location-ok")
                ))

            return self.createCalendarCollection()

        parent = self._checkParents(request, isPseudoCalendarCollectionResource)
        parent.addCallback(_defer)
        return parent


    def createCalendarCollection(self):
        """
        Internal API for creating a calendar collection.

        This will immediately create the collection without performing any
        verification.  For the normal API, see L{CalDAVFile.createCalendar}.

        The default behavior is to return a failing Deferred; for a working
        implementation, see L{twistedcaldav.legacy}.

        @return: a L{Deferred} which fires when the underlying collection has
            actually been created.
        """
        return fail(NotImplementedError())


    def createSpecialCollection(self, resourceType=None):
        #
        # Create the collection once we know it is safe to do so
        #
        def onCollection(status):
            if status != responsecode.CREATED:
                raise HTTPError(status)

            self.writeDeadProperty(resourceType)
            return status

        def onError(f):
            try:
                rmdir(self.fp)
            except Exception, e:
                log.err("Unable to clean up after failed MKCOL (special resource type: %s): %s" % (e, resourceType,))
            return f

        d = mkcollection(self.fp)
        if resourceType is not None:
            d.addCallback(onCollection)
        d.addErrback(onError)
        return d

    @inlineCallbacks
    def iCalendarRolledup(self, request):
        if self.isPseudoCalendarCollection():


# FIXME: move cache implementation!
            # Determine the cache key
#            isvirt = self.isVirtualShare()
#            if isvirt:
#                principal = (yield self.resourceOwnerPrincipal(request))
#                if principal:
#                    cacheKey = principal.principalUID()
#                else:
#                    cacheKey = "unknown"
#            else:
#                isowner = (yield self.isOwner(request, adminprincipals=True, readprincipals=True))
#                cacheKey = "owner" if isowner else "notowner"
                
            # Now check for a cached .ics
#            rolled = self.fp.child(".subscriptions")
#            if not rolled.exists():
#                try:
#                    rolled.makedirs()
#                except IOError, e:
#                    log.err("Unable to create internet calendar subscription cache directory: %s because of: %s" % (rolled.path, e,))
#                    raise HTTPError(ErrorResponse(responsecode.INTERNAL_SERVER_ERROR))
#            cached = rolled.child(cacheKey)
#            if cached.exists():
#                try:
#                    cachedData = cached.open().read()
#                except IOError, e:
#                    log.err("Unable to open or read internet calendar subscription cache file: %s because of: %s" % (cached.path, e,))
#                else:
#                    # Check the cache token
#                    token, data = cachedData.split("\r\n", 1)
#                    if token == self.getSyncToken():
#                        returnValue(data)

            # Generate a monolithic calendar
            calendar = iComponent("VCALENDAR")
            calendar.addProperty(iProperty("VERSION", "2.0"))

            # Do some optimisation of access control calculation by determining any inherited ACLs outside of
            # the child resource loop and supply those to the checkPrivileges on each child.
            filteredaces = (yield self.inheritedACEsforChildren(request))

            tzids = set()
            isowner = (yield self.isOwner(request, adminprincipals=True, readprincipals=True))
            accessPrincipal = (yield self.resourceOwnerPrincipal(request))

            for name, uid, type in self.index().bruteForceSearch(): #@UnusedVariable
                try:
                    child = yield request.locateChildResource(self, name)
                    child = IDAVResource(child)
                except TypeError:
                    child = None

                if child is not None:
                    # Check privileges of child - skip if access denied
                    try:
                        yield child.checkPrivileges(request, (davxml.Read(),), inherited_aces=filteredaces)
                    except AccessDeniedError:
                        continue

                    # Get the access filtered view of the data
                    caldata = child.iCalendarTextFiltered(isowner, accessPrincipal.principalUID() if accessPrincipal else "")
                    try:
                        subcalendar = iComponent.fromString(caldata)
                    except ValueError:
                        continue
                    assert subcalendar.name() == "VCALENDAR"

                    for component in subcalendar.subcomponents():
                        
                        # Only insert VTIMEZONEs once
                        if component.name() == "VTIMEZONE":
                            tzid = component.propertyValue("TZID")
                            if tzid in tzids:
                                continue
                            tzids.add(tzid)

                        calendar.addComponent(component)

            # Cache the data
            data = str(calendar)
            data = self.getSyncToken() + "\r\n" + data
#            try:
#                cached.open(mode='w').write(data)
#            except IOError, e:
#                log.err("Unable to open or write internet calendar subscription cache file: %s because of: %s" % (cached.path, e,))
                
            returnValue(calendar)

        raise HTTPError(ErrorResponse(responsecode.BAD_REQUEST))

    def iCalendarTextFiltered(self, isowner, accessUID=None):
        try:
            access = self.readDeadProperty(TwistedCalendarAccessProperty)
        except HTTPError:
            access = None

        # Now "filter" the resource calendar data
        caldata = PrivateEventFilter(access, isowner).filter(self.iCalendarText())
        if accessUID:
            caldata = PerUserDataFilter(accessUID).filter(caldata)
        return str(caldata)

    def iCalendarText(self, name=None):
        if self.isPseudoCalendarCollection():
            if name is None:
                return str(self.iCalendar())

            try:
                calendar_file = self.fp.child(name).open()
            except IOError, e:
                if e[0] == errno.ENOENT: return None
                raise

        elif self.isCollection():
            return None

        else:
            if name is not None:
                raise AssertionError("name must be None for non-collection calendar resource")

            calendar_file = self.fp.open()

        # FIXME: This is blocking I/O
        try:
            calendar_data = calendar_file.read()
        finally:
            calendar_file.close()

        return calendar_data

    def createAddressBook(self, request):
        """
        External API for creating an addressbook.  Verify that the parent is a
        collection, exists, is I{not} an addressbook collection; that this resource
        does not yet exist, then create it.

        @param request: the request used to look up parent resources to
            validate.

        @type request: L{twext.web2.iweb.IRequest}

        @return: a deferred that fires when an addressbook collection has been
            created in this resource.
        """
        #
        # request object is required because we need to validate against parent
        # resources, and we need the request in order to locate the parents.
        #

        if self.fp.exists():
            log.err("Attempt to create collection where file exists: %s" % (self.fp.path,))
            raise HTTPError(StatusResponse(responsecode.NOT_ALLOWED, "File exists"))

        # newStore guarantees that we always have a parent calendar home
        #if not os.path.isdir(os.path.dirname(self.fp.path)):
        #    log.err("Attempt to create collection with no parent: %s" % (self.fp.path,))
        #    raise HTTPError(StatusResponse(responsecode.CONFLICT, "No parent collection"))

        #
        # Verify that no parent collection is a calendar also
        #
        log.msg("Creating address book collection %s" % (self,))

        def _defer(parent):
            if parent is not None:
                log.err("Cannot create an address book collection within an address book collection %s" % (parent,))
                raise HTTPError(ErrorResponse(
                    responsecode.FORBIDDEN,
                    (carddavxml.carddav_namespace, "addressbook-collection-location-ok")
                ))

            return self.createAddressBookCollection()

        parent = self._checkParents(request, isAddressBookCollectionResource)
        parent.addCallback(_defer)
        return parent

    def createAddressBookCollection(self):
        """
        Internal API for creating an addressbook collection.

        This will immediately create the collection without performing any
        verification.  For the normal API, see L{CalDAVFile.createAddressBook}.

        The default behavior is to return a failing Deferred; for a working
        implementation, see L{twistedcaldav.legacy}.

        @return: a L{Deferred} which fires when the underlying collection has
            actually been created.
        """
        return fail(NotImplementedError())

    @inlineCallbacks
    def vCardRolledup(self, request):
        # TODO: just catenate all the vCards together 
        yield fail(HTTPError((ErrorResponse(responsecode.BAD_REQUEST))))

    def vCardText(self, name=None):
        if self.isAddressBookCollection():
            if name is None:
                return str(self.vCard())

            try:
                vcard_file = self.fp.child(name).open()
            except IOError, e:
                if e[0] == errno.ENOENT: return None
                raise

        elif self.isCollection():
            return None

        else:
            if name is not None:
                raise AssertionError("name must be None for non-collection vcard resource")

            vcard_file = self.fp.open()

        # FIXME: This is blocking I/O
        try:
            vcard_data = vcard_file.read()
        finally:
            vcard_file.close()

        return vcard_data

    def vCardXML(self, name=None):
        return carddavxml.AddressData.fromAddressData(self.vCardText(name))

    def supportedPrivileges(self, request):
        # read-free-busy support on calendar collection and calendar object resources
        if self.isCollection():
            return succeed(calendarPrivilegeSet)
        else:
            def gotParent(parent):
                if parent and isCalendarCollectionResource(parent):
                    return succeed(calendarPrivilegeSet)
                else:
                    return super(CalDAVFile, self).supportedPrivileges(request)

            d = self.locateParent(request, request.urlForResource(self))
            d.addCallback(gotParent)
            return d

        return super(CalDAVFile, self).supportedPrivileges(request)

    ##
    # Public additions
    ##

    def index(self):
        """
        Obtains the index for a calendar collection resource.
        @return: the index object for this resource.
        @raise AssertionError: if this resource is not a calendar collection
            resource.
        """
        if self.isAddressBookCollection():
            return AddressBookIndex(self)
        else:
            return Index(self)

    def listChildren(self):
        return [
            child for child in super(CalDAVFile, self).listChildren()
            if not child.startswith(".")
        ]

    def createSimilarFile(self, path):
        if self.comparePath(path):
            return self

        similar = super(CalDAVFile, self).createSimilarFile(path)

        if isCalendarCollectionResource(self):
            raise RuntimeError("Calendar collection resources should really "
                               "be represented by a different class.")

        return similar

    ##
    # Quota
    ##

    def quotaSize(self, request):
        """
        Get the size of this resource.
        TODO: Take into account size of dead-properties. Does stat include xattrs size?

        @return: an L{Deferred} with a C{int} result containing the size of the resource.
        """
        if self.isCollection():
            @inlineCallbacks
            def walktree(top):
                """
                Recursively descend the directory tree rooted at top,
                calling the callback function for each regular file

                @param top: L{FilePath} for the directory to walk.
                """

                total = 0
                for f in top.listdir():

                    # Ignore the database
                    if f.startswith("."):
                        continue

                    child = top.child(f)
                    if child.isdir():
                        # It's a directory, recurse into it
                        total += yield walktree(child)
                    elif child.isfile():
                        # It's a file, call the callback function
                        total += child.getsize()
                    else:
                        # Unknown file type, print a message
                        pass

                returnValue(total)

            return walktree(self.fp)
        else:
            return succeed(self.fp.getsize())

    ##
    # Utilities
    ##

    @staticmethod
    def _isChildURI(request, uri, immediateChild=True):
        """
        Verify that the supplied URI represents a resource that is a child
        of the request resource.
        @param request: the request currently in progress
        @param uri: the URI to test
        @return: True if the supplied URI is a child resource
                 False if not
        """
        if uri is None: return False

        #
        # Parse the URI
        #

        (scheme, host, path, query, fragment) = urlsplit(uri) #@UnusedVariable

        # Request hostname and child uri hostname have to be the same.
        if host and host != request.headers.getHeader("host"):
            return False

        # Child URI must start with request uri text.
        parent = request.uri
        if not parent.endswith("/"):
            parent += "/"

        return path.startswith(parent) and (len(path) > len(parent)) and (not immediateChild or (path.find("/", len(parent)) == -1))

    @inlineCallbacks
    def _checkParents(self, request, test):
        """
        @param request: the request being processed.
        @param test: a callable
        @return: the closest parent for this resource using the request URI from
            the given request for which C{test(parent)} evaluates to a true
            value, or C{None} if no parent matches.
        """
        parent = self
        parent_uri = request.uri

        while True:
            parent_uri = parentForURL(parent_uri)
            if not parent_uri: break

            parent = yield request.locateResource(parent_uri)

            if test(parent):
                returnValue(parent)

class AutoProvisioningFileMixIn (LinkFollowerMixIn, AutoProvisioningResourceMixIn):
    def provision(self):
        self.provisionFile()
        return super(AutoProvisioningFileMixIn, self).provision()


    def provisionFile(self):
        if hasattr(self, "_provisioned_file"):
            return False
        else:
            self._provisioned_file = True

        # If the file already exists we can just exit here - there is no need to go further
        if self.fp.exists():
            return False

        # At this point the original FilePath did not indicate an existing file, but we should
        # recheck it to see if some other request sneaked in and already created/provisioned it

        fp = self.fp

        fp.restat(False)
        if fp.exists():
            return False

        log.msg("Provisioning file: %s" % (self,))

        if hasattr(self, "parent"):
            parent = self.parent
            if not parent.exists() and isinstance(parent, AutoProvisioningFileMixIn):
                parent.provision()

            assert parent.exists(), "Parent %s of %s does not exist" % (parent, self)
            assert parent.isCollection(), "Parent %s of %s is not a collection" % (parent, self)

        if self.isCollection():
            try:
                fp.makedirs()
            except OSError:
                # It's possible someone else created the directory in the meantime...
                # Check our status again, and re-raise if we're not a collection.
                if not self.isCollection():
                    raise
            fp.changed()
        else:
            fp.open("w").close()
            fp.changed()

        return True

    def _initTypeAndEncoding(self):

        # Handle cases not covered by getTypeAndEncoding()
        if self.isCollection():
            self._type = "httpd/unix-directory"
        else:
            super(AutoProvisioningFileMixIn, self)._initTypeAndEncoding()


class DirectoryBackedAddressBookFile (ReadOnlyResourceMixIn, DirectoryBackedAddressBookResource, CalDAVFile):
    """
    Directory-backed address book, supporting directory vcard search.
    """
    def __init__(self, path, principalCollections):
        CalDAVFile.__init__(self, path, principalCollections=principalCollections)
        DirectoryBackedAddressBookResource.__init__(self)

        # create with permissions, similar to CardDAVOptions in tap.py
        # FIXME:  /Directory does not need to be in file system unless debug-only caching options are used
        try:
            os.mkdir(path)
            os.chmod(path, 0750)
            if config.UserName and config.GroupName:
                import pwd
                import grp
                uid = pwd.getpwnam(config.UserName)[2]
                gid = grp.getgrnam(config.GroupName)[2]
                os.chown(path, uid, gid)
 
            log.msg("Created %s" % (path,))
            
        except (OSError,), e:
            # this is caused by multiprocessor race and is harmless
            if e.errno != errno.EEXIST:
                raise

    
    def getChild(self, name):
        
        if name is "":
            return self
        else:
            from twistedcaldav.simpleresource import SimpleCalDAVResource
            return SimpleCalDAVResource(principalCollections=self.principalCollections())
       
    def createSimilarFile(self, path):
        if self.comparePath(path):
            return self
        else:
            from twistedcaldav.simpleresource import SimpleCalDAVResource
            return SimpleCalDAVResource(principalCollections=self.principalCollections())
 
##
# Utilities
##

def locateExistingChild(resource, request, segments):
    """
    This C{locateChild()} implementation fails to find children if C{getChild()}
    doesn't return one.
    """
    # If getChild() finds a child resource, return it
    child = resource.getChild(segments[0])
    if child is not None:
        return (child, segments[1:])

    # Otherwise, there is no child
    return (None, ())

def _calendarPrivilegeSet ():
    edited = False

    top_supported_privileges = []

    for supported_privilege in davPrivilegeSet.childrenOfType(davxml.SupportedPrivilege):
        all_privilege = supported_privilege.childOfType(davxml.Privilege)
        if isinstance(all_privilege.children[0], davxml.All):
            all_description = supported_privilege.childOfType(davxml.Description)
            all_supported_privileges = []
            for all_supported_privilege in supported_privilege.childrenOfType(davxml.SupportedPrivilege):
                read_privilege = all_supported_privilege.childOfType(davxml.Privilege)
                if isinstance(read_privilege.children[0], davxml.Read):
                    read_description = all_supported_privilege.childOfType(davxml.Description)
                    read_supported_privileges = list(all_supported_privilege.childrenOfType(davxml.SupportedPrivilege))
                    read_supported_privileges.append(
                        davxml.SupportedPrivilege(
                            davxml.Privilege(caldavxml.ReadFreeBusy()),
                            davxml.Description("allow free busy report query", **{"xml:lang": "en"}),
                        )
                    )
                    all_supported_privileges.append(
                        davxml.SupportedPrivilege(read_privilege, read_description, *read_supported_privileges)
                    )
                    edited = True
                else:
                    all_supported_privileges.append(all_supported_privilege)
            top_supported_privileges.append(
                davxml.SupportedPrivilege(all_privilege, all_description, *all_supported_privileges)
            )
        else:
            top_supported_privileges.append(supported_privilege)

    assert edited, "Structure of davPrivilegeSet changed in a way that I don't know how to extend for calendarPrivilegeSet"

    return davxml.SupportedPrivilegeSet(*top_supported_privileges)

calendarPrivilegeSet = _calendarPrivilegeSet()

##
# Attach methods
##

import twistedcaldav.method

bindMethods(twistedcaldav.method, CalDAVFile)

# FIXME: Little bit of a circular dependency here...
twistedcaldav.method.acl.CalDAVFile      = CalDAVFile
twistedcaldav.method.copymove.CalDAVFile = CalDAVFile
twistedcaldav.method.delete.CalDAVFile   = CalDAVFile
twistedcaldav.method.get.CalDAVFile      = CalDAVFile
twistedcaldav.method.mkcol.CalDAVFile    = CalDAVFile
twistedcaldav.method.propfind.CalDAVFile = CalDAVFile
twistedcaldav.method.put.CalDAVFile      = CalDAVFile


