# -*- test-case-name: twistedcaldav.test.test_wrapping -*-
##
# Copyright (c) 2005-2011 Apple Inc. All rights reserved.
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
Wrappers to translate between the APIs in L{txdav.caldav.icalendarstore} and
L{txdav.carddav.iaddressbookstore} and those in L{twistedcaldav}.
"""

from urlparse import urlsplit

from twisted.internet.defer import succeed, inlineCallbacks, returnValue,\
    maybeDeferred
from twisted.internet.protocol import Protocol
from twisted.python.log import err as logDefaultException
from twisted.python.util import FancyEqMixin

from twext.python.log import Logger

from twext.web2 import responsecode
from twext.web2.dav import davxml
from twext.web2.dav.element.base import dav_namespace
from twext.web2.dav.http import ErrorResponse, ResponseQueue
from twext.web2.dav.noneprops import NonePropertyStore
from twext.web2.dav.resource import TwistedACLInheritable, AccessDeniedError
from twext.web2.dav.util import parentForURL, allDataFromStream, joinURL, \
    davXMLFromStream
from twext.web2.http import HTTPError, StatusResponse, Response
from twext.web2.http_headers import ETag, MimeType
from twext.web2.responsecode import (
    FORBIDDEN, NO_CONTENT, NOT_FOUND, CREATED, CONFLICT, PRECONDITION_FAILED,
    BAD_REQUEST, OK,
)
from twext.web2.stream import ProducerStream, readStream, MemoryStream

from twistedcaldav.cache import CacheStoreNotifier, ResponseCacheMixin,\
    DisabledCacheNotifier
from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.config import config
from twistedcaldav import customxml
from twistedcaldav.memcachelock import MemcacheLock, MemcacheLockTimeoutError
from twistedcaldav.notifications import NotificationCollectionResource, \
    NotificationResource
from twistedcaldav.resource import CalDAVResource, GlobalAddressBookResource
from twistedcaldav.schedule import ScheduleInboxResource
from twistedcaldav.scheduling.implicit import ImplicitScheduler
from twistedcaldav.ical import Component as VCalendar
from twistedcaldav.ical import Property as VProperty
from twistedcaldav.vcard import Component as VCard

from txdav.base.propertystore.base import PropertyName
from txdav.common.icommondatastore import NoSuchObjectResourceError

log = Logger()


class _NewStorePropertiesWrapper(object):
    """
    Wrap a new-style property store (a L{txdav.idav.IPropertyStore}) in the old-
    style interface for compatibility with existing code.
    """

    # FIXME: UID arguments on everything need to be tested against something.
    def __init__(self, newPropertyStore):
        """
        Initialize an old-style property store from a new one.

        @param newPropertyStore: the new-style property store.
        @type newPropertyStore: L{txdav.idav.IPropertyStore}
        """
        self._newPropertyStore = newPropertyStore

    @classmethod
    def _convertKey(cls, qname):
        namespace, name = qname
        return PropertyName(namespace, name)


    # FIXME 'uid' here should be verifying something.
    def get(self, qname, uid=None):
        """
        
        """
        try:
            return self._newPropertyStore[self._convertKey(qname)]
        except KeyError:
            raise HTTPError(StatusResponse(
                    NOT_FOUND,
                    "No such property: {%s}%s" % qname))


    def set(self, property, uid=None):
        """
        
        """
        self._newPropertyStore[self._convertKey(property.qname())] = property


    def delete(self, qname, uid=None):
        """
        
        """
        del self._newPropertyStore[self._convertKey(qname)]


    def contains(self, qname, uid=None, cache=True):
        """
        
        """
        return (self._convertKey(qname) in self._newPropertyStore)


    def list(self, uid=None, filterByUID=True, cache=True):
        """
        
        """
        return [(pname.namespace, pname.name) for pname in
                self._newPropertyStore.keys()]



def requiresPermissions(*permissions, **kw):
    """
    A decorator to wrap http_ methods in, to indicate that they should not be
    run until the current user principal has been authorized for the given
    permission set.
    """
    fromParent = kw.get('fromParent')
    # FIXME: direct unit tests
    def wrap(thunk):
        def authAndContinue(self, request):
            if permissions:
                d = self.authorize(request, permissions)
            else:
                d = succeed(None)
            if fromParent:
                d.addCallback(
                    lambda whatever:
                        request.locateResource(parentForURL(request.uri))
                ).addCallback(
                    lambda parent:
                        parent.authorize(request, fromParent)
                )
            d.addCallback(lambda whatever: thunk(self, request))
            return d
        return authAndContinue
    return wrap



class _NewStoreFileMetaDataHelper(object):

    def exists(self):
        return self._newStoreObject is not None

    def name(self):
        return self._newStoreObject.name() if self._newStoreObject is not None else self._name


    def etag(self):
        return ETag(self._newStoreObject.md5()) if self._newStoreObject is not None else None


    def contentType(self):
        return self._newStoreObject.contentType() if self._newStoreObject is not None else None


    def contentLength(self):
        return self._newStoreObject.size() if self._newStoreObject is not None else None


    def lastModified(self):
        return self._newStoreObject.modified() if self._newStoreObject is not None else None


    def creationDate(self):
        return self._newStoreObject.created() if self._newStoreObject is not None else None


    def newStoreProperties(self):
        return self._newStoreObject.properties() if self._newStoreObject is not None else None



class _CommonHomeChildCollectionMixin(ResponseCacheMixin):
    """
    Methods for things which are like calendars.
    """

    _childClass = None
    cacheNotifierFactory = DisabledCacheNotifier

    def _initializeWithHomeChild(self, child, home):
        """
        Initialize with a home child object.

        @param child: the new store home child object.
        @type calendar: L{txdav.common._.CommonHomeChild}

        @param home: the home through which the given home child was accessed.
        @type home: L{txdav.common._.CommonHome}
        """
        self._newStoreObject = child
        self._newStoreParentHome = home._newStoreHome
        self._parentResource = home
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreObject.properties()
        ) if self._newStoreObject else NonePropertyStore(self)
        if self._newStoreObject:
            self.cacheNotifier = self.cacheNotifierFactory(self)
            self._newStoreObject.addNotifier(CacheStoreNotifier(self))


    def liveProperties(self):

        props = super(_CommonHomeChildCollectionMixin, self).liveProperties()
        
        if config.MaxResourcesPerCollection:
            props += (customxml.MaxResources.qname(),)

        return props

    @inlineCallbacks
    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        if qname == customxml.MaxResources.qname() and config.MaxResourcesPerCollection:
            returnValue(customxml.MaxResources.fromString(config.MaxResourcesPerCollection))

        returnValue((yield super(_CommonHomeChildCollectionMixin, self).readProperty(property, request)))

    def url(self):
        return joinURL(self._parentResource.url(), self._name, "/")

    def index(self):
        """
        Retrieve the new-style index wrapper.
        """
        return self._newStoreObject.retrieveOldIndex()


    def invitesDB(self):
        """
        Retrieve the new-style invites DB wrapper.
        """
        if not hasattr(self, "_invitesDB"):
            self._invitesDB = self._newStoreObject.retrieveOldInvites()
        return self._invitesDB


    def exists(self):
        # FIXME: tests
        return self._newStoreObject is not None


    @inlineCallbacks
    def _indexWhatChanged(self, revision, depth):
        # The newstore implementation supports this directly
        returnValue(
            (yield self._newStoreObject.resourceNamesSinceToken(revision))
            + ([],)
        )


    @inlineCallbacks
    def makeChild(self, name):
        """
        Create a L{CalendarObjectResource} based on a calendar object name.
        """

        if self._newStoreObject:
            newStoreObject = yield self._newStoreObject.objectResourceWithName(name)
    
            similar = self._childClass(
                newStoreObject,
                self._newStoreObject,
                name,
                principalCollections=self._principalCollections
            )
    
            self.propagateTransaction(similar)
            returnValue(similar)
        else:
            returnValue(NoParent())

    @inlineCallbacks
    def listChildren(self):
        """
        @return: a sequence of the names of all known children of this resource.
        """
        children = set(self.putChildren.keys())
        children.update((yield self._newStoreObject.listObjectResources()))
        returnValue(sorted(children))

    def name(self):
        return self._name


    def etag(self):
        return ETag(self._newStoreObject.md5()) if self._newStoreObject else None


    def lastModified(self):
        return self._newStoreObject.modified() if self._newStoreObject else None


    def creationDate(self):
        return self._newStoreObject.created() if self._newStoreObject else None


    def getSyncToken(self):
        return self._newStoreObject.syncToken() if self._newStoreObject else None

    @inlineCallbacks
    def findChildrenFaster(
        self, depth, request, okcallback, badcallback,
        names, privileges, inherited_aces
    ):
        """
        Override to pre-load children in certain collection types for better performance.
        """
        
        if depth == "1":
            yield self._newStoreObject.objectResources()
        
        result = (yield super(_CommonHomeChildCollectionMixin, self).findChildrenFaster(
            depth, request, okcallback, badcallback, names, privileges, inherited_aces
        ))
        
        returnValue(result)
    
    @inlineCallbacks
    def createCollection(self):
        """
        Override C{createCollection} to actually do the work.
        """
        self._newStoreObject = (yield self._newStoreParentHome.createChildWithName(self._name))
        
        # Re-initialize to get stuff setup again now we have a "real" object
        self._initializeWithHomeChild(self._newStoreObject, self._parentResource)

        returnValue(CREATED)

    @requiresPermissions(fromParent=[davxml.Unbind()])
    @inlineCallbacks
    def http_DELETE(self, request):
        """
        Override http_DELETE to validate 'depth' header. 
        """

        if not self.exists():
            log.err("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        depth = request.headers.getHeader("depth", "infinity")
        if depth != "infinity":
            msg = "illegal depth header for DELETE on collection: %s" % (
                depth,
            )
            log.err(msg)
            raise HTTPError(StatusResponse(BAD_REQUEST, msg))
        response = (yield self.storeRemove(request, True, request.uri))
        returnValue(response)

    @inlineCallbacks
    def storeRemove(self, request, viaRequest, where):
        """
        Delete this collection resource, first deleting each contained
        object resource.

        This has to emulate the behavior in fileop.delete in that any errors
        need to be reported back in a multistatus response.

        @param request: The request used to locate child resources.  Note that
            this is the request which I{triggered} the C{DELETE}, but which may
            not actually be a C{DELETE} request itself.

        @type request: L{twext.web2.iweb.IRequest}

        @param viaRequest: Indicates if the delete was a direct result of an http_DELETE
        which for calendars at least will require implicit cancels to be sent.

        @type request: C{bool}

        @param where: the URI at which the resource is being deleted.
        @type where: C{str}

        @return: an HTTP response suitable for sending to a client (or
            including in a multi-status).

        @rtype: something adaptable to L{twext.web2.iweb.IResponse}
        """

        # Check virtual share first
        isVirtual = self.isVirtualShare()
        if isVirtual:
            log.debug("Removing shared collection %s" % (self,))
            yield self.removeVirtualShare(request)
            returnValue(NO_CONTENT)

        log.debug("Deleting collection %s" % (self,))

        # 'deluri' is this resource's URI; I should be able to synthesize it
        # from 'self'.

        errors = ResponseQueue(where, "DELETE", NO_CONTENT)

        for childname in (yield self.listChildren()):

            childurl = joinURL(where, childname)

            # FIXME: use a more specific API; we should know what this child
            # resource is, and not have to look it up.  (Sharing information
            # needs to move into the back-end first, though.)
            child = (yield request.locateChildResource(self, childname))

            try:
                yield child.storeRemove(request, viaRequest, childurl)
            except:
                logDefaultException()
                errors.add(childurl, BAD_REQUEST)

        # Now do normal delete

        # Handle sharing
        wasShared = (yield self.isShared(request))
        if wasShared:
            yield self.downgradeFromShare(request)

        # Actually delete it.
        yield self._newStoreObject.remove()
        
        # Re-initialize to get stuff setup again now we have no object
        self._initializeWithHomeChild(None, self._parentResource)

        # FIXME: handle exceptions, possibly like this:

        #        if isinstance(more_responses, MultiStatusResponse):
        #            # Merge errors
        #            errors.responses.update(more_responses.children)

        response = errors.response()

        returnValue(response)

    def http_COPY(self, request):
        """
        Copying of calendar collections isn't allowed.
        """
        # FIXME: no direct tests
        return FORBIDDEN


    # FIXME: access control
    @inlineCallbacks
    def http_MOVE(self, request):
        """
        Moving a collection is allowed for the purposes of changing
        that collections's name.
        """
        if not self.exists():
            log.err("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        # Can not move outside of home or to existing collection
        sourceURI = request.uri
        destinationURI = urlsplit(request.headers.getHeader("destination"))[2]
        if parentForURL(sourceURI) != parentForURL(destinationURI):
            returnValue(FORBIDDEN)

        destination = yield request.locateResource(destinationURI)
        if destination.exists():
            returnValue(FORBIDDEN)
            
        # Forget the destination now as after the move we will need to re-init it with its
        # new store object
        request._forgetResource(destination, destinationURI)

        # Move is valid so do it
        basename = destinationURI.rstrip("/").split("/")[-1]
        yield self._newStoreObject.rename(basename)
        returnValue(NO_CONTENT)

    def notifierID(self, label="default"):
        self._newStoreObject.notifierID(label)

    def notifyChanged(self):
        self._newStoreObject.notifyChanged()

class CalendarCollectionResource(_CommonHomeChildCollectionMixin, CalDAVResource):
    """
    Wrapper around a L{txdav.caldav.icalendar.ICalendar}.
    """

 
    def __init__(self, calendar, home, name=None, *args, **kw):
        """
        Create a CalendarCollectionResource from a L{txdav.caldav.icalendar.ICalendar}
        and the arguments required for L{CalDAVResource}.
        """

        self._childClass = CalendarObjectResource
        super(CalendarCollectionResource, self).__init__(*args, **kw)
        self._initializeWithHomeChild(calendar, home)
        self._name = calendar.name() if calendar else name


    def __repr__(self):
        return "<Calendar Collection Resource %r:%r %s>" % (
            self._newStoreParentHome.uid(),
            self._name,
            "" if self._newStoreObject else "Non-existent"
        )


    def isCollection(self):
        return True


    def isCalendarCollection(self):
        """
        Yes, it is a calendar collection.
        """
        return True


    @inlineCallbacks
    def iCalendarRolledup(self, request):
        # FIXME: uncached: implement cache in the storage layer

        # Generate a monolithic calendar
        calendar = VCalendar("VCALENDAR")
        calendar.addProperty(VProperty("VERSION", "2.0"))

        # Do some optimisation of access control calculation by determining any
        # inherited ACLs outside of the child resource loop and supply those to
        # the checkPrivileges on each child.
        filteredaces = (yield self.inheritedACEsforChildren(request))

        tzids = set()
        isowner = (yield self.isOwner(request, adminprincipals=True, readprincipals=True))
        accessPrincipal = (yield self.resourceOwnerPrincipal(request))

        for name, uid, type in (yield maybeDeferred(self.index().bruteForceSearch)): #@UnusedVariable
            try:
                child = yield request.locateChildResource(self, name)
            except TypeError:
                child = None

            if child is not None:
                # Check privileges of child - skip if access denied
                try:
                    yield child.checkPrivileges(request, (davxml.Read(),), inherited_aces=filteredaces)
                except AccessDeniedError:
                    continue

                # Get the access filtered view of the data
                caldata = yield child.iCalendarTextFiltered(isowner, accessPrincipal.principalUID() if accessPrincipal else "")
                try:
                    subcalendar = VCalendar.fromString(caldata)
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
        data = (yield self.getSyncToken()) + "\r\n" + data

        returnValue(calendar)


    createCalendarCollection = _CommonHomeChildCollectionMixin.createCollection


    @inlineCallbacks
    def storeRemove(self, request, implicitly, where):
        """
        Delete this calendar collection resource, first deleting each contained
        calendar resource.

        This has to emulate the behavior in fileop.delete in that any errors
        need to be reported back in a multistatus response.

        @param request: The request used to locate child resources.  Note that
            this is the request which I{triggered} the C{DELETE}, but which may
            not actually be a C{DELETE} request itself.

        @type request: L{twext.web2.iweb.IRequest}

        @param implicitly: Should implicit scheduling operations be triggered
            as a resut of this C{DELETE}?

        @type implicitly: C{bool}

        @param where: the URI at which the resource is being deleted.
        @type where: C{str}

        @return: an HTTP response suitable for sending to a client (or
            including in a multi-status).

        @rtype: something adaptable to L{twext.web2.iweb.IResponse}
        """

        # Not allowed to delete the default calendar
        default = (yield self.isDefaultCalendar(request))
        if default:
            log.err("Cannot DELETE default calendar: %s" % (self,))
            raise HTTPError(ErrorResponse(
                FORBIDDEN,
                (caldav_namespace, "default-calendar-delete-allowed",)
            ))

        response = (
            yield super(CalendarCollectionResource, self).storeRemove(
                request, implicitly, where
            )
        )

        if response == NO_CONTENT:
            # Do some clean up
            yield self.deletedCalendar(request)

        returnValue(response)


    # FIXME: access control
    @inlineCallbacks
    def http_MOVE(self, request):
        """
        Moving a calendar collection is allowed for the purposes of changing
        that calendar's name.
        """
        defaultCalendar = (yield self.isDefaultCalendar(request))
        
        result = (yield super(CalendarCollectionResource, self).http_MOVE(request))
        if result == NO_CONTENT:
            destinationURI = urlsplit(request.headers.getHeader("destination"))[2]
            destination = yield request.locateResource(destinationURI)
            yield self.movedCalendar(request, defaultCalendar,
                               destination, destinationURI)
        returnValue(result)


class StoreScheduleInboxResource(_CommonHomeChildCollectionMixin, ScheduleInboxResource):

    def __init__(self, *a, **kw):

        self._childClass = CalendarObjectResource
        super(StoreScheduleInboxResource, self).__init__(*a, **kw)
        self.parent.propagateTransaction(self)


    @classmethod
    @inlineCallbacks
    def maybeCreateInbox(cls, *a, **kw):
        self = cls(*a, **kw)
        home = self.parent._newStoreHome
        storage = yield home.calendarWithName("inbox")
        if storage is None:
            # raise RuntimeError("backend should be handling this for us")
            # FIXME: spurious error, sanity check, should not be needed;
            # unfortunately, user09's calendar home does not have an inbox, so
            # this is a temporary workaround.
            yield home.createCalendarWithName("inbox")
            storage = yield home.calendarWithName("inbox")
        self._initializeWithHomeChild(
            storage,
            self.parent
        )
        self._name = storage.name()
        returnValue(self)


    def provisionFile(self):
        pass

    def provision(self):
        pass

    def http_DELETE(self, request):
        return FORBIDDEN

    def http_COPY(self, request):
        return FORBIDDEN

    def http_MOVE(self, request):
        return FORBIDDEN


class _GetChildHelper(CalDAVResource):

    def locateChild(self, request, segments):
        if segments[0] == '':
            return self, segments[1:]
        return self.getChild(segments[0]), segments[1:]


    def getChild(self, name):
        return None


    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        if qname == (dav_namespace, "resourcetype"):
            return succeed(self.resourceType())
        return super(_GetChildHelper, self).readProperty(property, request)


    def davComplianceClasses(self):
        return ("1", "access-control")


    @requiresPermissions(davxml.Read())
    def http_GET(self, request):
        return super(_GetChildHelper, self).http_GET(request)



class DropboxCollection(_GetChildHelper):
    """
    A collection of all dropboxes (containers for attachments), presented as a
    resource under the user's calendar home, where a dropbox is a
    L{CalendarObjectDropbox}.
    """
    # FIXME: no direct tests for this class at all.

    def __init__(self, parent, *a, **kw):
        kw.update(principalCollections=parent.principalCollections())
        super(DropboxCollection, self).__init__(*a, **kw)
        self._newStoreHome = parent._newStoreHome
        parent.propagateTransaction(self)


    def isCollection(self):
        """
        It is a collection.
        """
        return True


    @inlineCallbacks
    def getChild(self, name):
        calendarObject = yield self._newStoreHome.calendarObjectWithDropboxID(name)
        if calendarObject is None:
            returnValue(NoDropboxHere())
        objectDropbox = CalendarObjectDropbox(
            calendarObject, principalCollections=self.principalCollections()
        )
        self.propagateTransaction(objectDropbox)
        returnValue(objectDropbox)


    def resourceType(self,):
        return davxml.ResourceType.dropboxhome #@UndefinedVariable


    def listChildren(self):
        return self._newStoreHome.getAllDropboxIDs()



class NoDropboxHere(_GetChildHelper):

    def isCollection(self):
        return False


    def exists(self):
        return False


    def http_GET(self, request):
        return FORBIDDEN


    def http_MKCALENDAR(self, request):
        return FORBIDDEN


    @requiresPermissions(fromParent=[davxml.Bind()])
    def http_MKCOL(self, request):
        return CREATED



class CalendarObjectDropbox(_GetChildHelper):
    """
    A wrapper around a calendar object which serves that calendar object's
    attachments as a DAV collection.
    """

    def __init__(self, calendarObject, *a, **kw):
        super(CalendarObjectDropbox, self).__init__(*a, **kw)
        self._newStoreCalendarObject = calendarObject


    def isCollection(self):
        return True


    def resourceType(self):
        return davxml.ResourceType.dropbox #@UndefinedVariable


    @inlineCallbacks
    def getChild(self, name):
        attachment = yield self._newStoreCalendarObject.attachmentWithName(name)
        result = CalendarAttachment(
            self._newStoreCalendarObject,
            attachment,
            name,
            principalCollections=self.principalCollections()
        )
        self.propagateTransaction(result)
        returnValue(result)


    @requiresPermissions(davxml.WriteACL())
    @inlineCallbacks
    def http_ACL(self, request):
        """
        Don't ever actually make changes, but attempt to deny any ACL requests
        that refer to permissions not referenced by attendees in the iCalendar
        data.
        """

        attendees = (yield self._newStoreCalendarObject.component()).getAttendees()
        attendees = [attendee.split("urn:uuid:")[-1] for attendee in attendees]
        document = yield davXMLFromStream(request.stream)
        for ace in document.root_element.children:
            for element in ace.children:
                if isinstance(element, davxml.Principal):
                    for href in element.children:
                        principalURI = href.children[0].data
                        uidsPrefix = '/principals/__uids__/'
                        if not principalURI.startswith(uidsPrefix):
                            # Unknown principal.
                            returnValue(FORBIDDEN)
                        principalElements = principalURI[
                            len(uidsPrefix):].split("/")
                        if principalElements[-1] == '':
                            principalElements.pop()
                        if principalElements[-1] in ('calendar-proxy-read',
                                                     'calendar-proxy-write'):
                            principalElements.pop()
                        if len(principalElements) != 1:
                            returnValue(FORBIDDEN)
                        principalUID = principalElements[0]
                        if principalUID not in attendees:
                            returnValue(FORBIDDEN)
        returnValue(OK)


    @requiresPermissions(fromParent=[davxml.Bind()])
    def http_MKCOL(self, request):
        return CREATED


    @requiresPermissions(fromParent=[davxml.Unbind()])
    def http_DELETE(self, request):
        return NO_CONTENT


    @inlineCallbacks
    def listChildren(self):
        l = []
        for attachment in (yield self._newStoreCalendarObject.attachments()):
            l.append(attachment.name())
        returnValue(l)


    @inlineCallbacks
    def accessControlList(self, request, *a, **kw):
        """
        All principals identified as ATTENDEEs on the event for this dropbox
        may read all its children. Also include proxies of ATTENDEEs. Ignore
        unknown attendees.
        """
        originalACL = yield super(
            CalendarObjectDropbox, self).accessControlList(request, *a, **kw)
        originalACEs = list(originalACL.children)

        if config.EnableProxyPrincipals:
            owner = (yield self.ownerPrincipal(request))

            originalACEs += (
                # DAV:write-acl access for this principal's calendar-proxy-write users.
                davxml.ACE(
                    davxml.Principal(davxml.HRef(joinURL(owner.principalURL(), "calendar-proxy-write/"))),
                    davxml.Grant(
                        davxml.Privilege(davxml.WriteACL()),
                    ),
                    davxml.Protected(),
                    TwistedACLInheritable(),
                ),
            )

        othersCanWrite = self._newStoreCalendarObject.attendeesCanManageAttachments()
        cuas = (yield self._newStoreCalendarObject.component()).getAttendees()
        newACEs = []
        for calendarUserAddress in cuas:
            principal = self.principalForCalendarUserAddress(
                calendarUserAddress
            )
            if principal is None:
                continue

            principalURL = principal.principalURL()
            writePrivileges = [
                davxml.Privilege(davxml.Read()),
                davxml.Privilege(davxml.ReadCurrentUserPrivilegeSet()),
                davxml.Privilege(davxml.Write()),
            ]
            readPrivileges = [
                davxml.Privilege(davxml.Read()),
                davxml.Privilege(davxml.ReadCurrentUserPrivilegeSet()),
            ]
            if othersCanWrite:
                privileges = writePrivileges
            else:
                privileges = readPrivileges
            newACEs.append(davxml.ACE(
                davxml.Principal(davxml.HRef(principalURL)),
                davxml.Grant(*privileges),
                davxml.Protected(),
                TwistedACLInheritable(),
            ))
            newACEs.append(davxml.ACE(
                davxml.Principal(davxml.HRef(joinURL(principalURL, "calendar-proxy-write/"))),
                davxml.Grant(*privileges),
                davxml.Protected(),
                TwistedACLInheritable(),
            ))
            newACEs.append(davxml.ACE(
                davxml.Principal(davxml.HRef(joinURL(principalURL, "calendar-proxy-read/"))),
                davxml.Grant(*readPrivileges),
                davxml.Protected(),
                TwistedACLInheritable(),
            ))

        # Now also need invitees
        newACEs.extend((yield self.sharedDropboxACEs()))

        returnValue(davxml.ACL(*tuple(originalACEs + newACEs)))

    @inlineCallbacks
    def sharedDropboxACEs(self):

        aces = ()
        records = yield self._newStoreCalendarObject._parentCollection.retrieveOldInvites().allRecords()
        for record in records:
            # Invite shares use access mode from the invite
            if record.state != "ACCEPTED":
                continue
            
            userprivs = [
            ]
            if record.access in ("read-only", "read-write", "read-write-schedule",):
                userprivs.append(davxml.Privilege(davxml.Read()))
                userprivs.append(davxml.Privilege(davxml.ReadACL()))
                userprivs.append(davxml.Privilege(davxml.ReadCurrentUserPrivilegeSet()))
            if record.access in ("read-only",):
                userprivs.append(davxml.Privilege(davxml.WriteProperties()))
            if record.access in ("read-write", "read-write-schedule",):
                userprivs.append(davxml.Privilege(davxml.Write()))
            proxyprivs = list(userprivs)
            proxyprivs.remove(davxml.Privilege(davxml.ReadACL()))

            aces += (
                # Inheritable specific access for the resource's associated principal.
                davxml.ACE(
                    davxml.Principal(davxml.HRef(record.principalURL)),
                    davxml.Grant(*userprivs),
                    davxml.Protected(),
                    TwistedACLInheritable(),
                ),
            )

            if config.EnableProxyPrincipals:
                aces += (
                    # DAV:read/DAV:read-current-user-privilege-set access for this principal's calendar-proxy-read users.
                    davxml.ACE(
                        davxml.Principal(davxml.HRef(joinURL(record.principalURL, "calendar-proxy-read/"))),
                        davxml.Grant(
                            davxml.Privilege(davxml.Read()),
                            davxml.Privilege(davxml.ReadCurrentUserPrivilegeSet()),
                        ),
                        davxml.Protected(),
                        TwistedACLInheritable(),
                    ),
                    # DAV:read/DAV:read-current-user-privilege-set/DAV:write access for this principal's calendar-proxy-write users.
                    davxml.ACE(
                        davxml.Principal(davxml.HRef(joinURL(record.principalURL, "calendar-proxy-write/"))),
                        davxml.Grant(*proxyprivs),
                        davxml.Protected(),
                        TwistedACLInheritable(),
                    ),
                )

        returnValue(aces)


class CalendarAttachment(_NewStoreFileMetaDataHelper, _GetChildHelper):

    def __init__(self, calendarObject, attachment, attachmentName, **kw):
        super(CalendarAttachment, self).__init__(**kw)
        self._newStoreCalendarObject = calendarObject
        self._newStoreAttachment = self._newStoreObject = attachment
        self.attachmentName = attachmentName


    def getChild(self, name):
        return None


    @requiresPermissions(davxml.WriteContent())
    @inlineCallbacks
    def http_PUT(self, request):
        # FIXME: direct test
        # FIXME: CDT test to make sure that permissions are enforced.

        content_type = request.headers.getHeader("content-type")
        if content_type is None:
            content_type = MimeType("application", "octet-stream")

        if self._newStoreAttachment:
            t = self._newStoreAttachment.store(content_type)
            yield readStream(request.stream, t.write)
            yield t.loseConnection()
            returnValue(NO_CONTENT)
        else:
            t = yield self._newStoreCalendarObject.createAttachmentWithName(
                self.attachmentName,
                content_type,
            )
            yield readStream(request.stream, t.write)
            self._newStoreAttachment = self._newStoreObject = yield self._newStoreCalendarObject.attachmentWithName(
                self.attachmentName
            )
            yield t.loseConnection()
            returnValue(CREATED)


    @requiresPermissions(davxml.Read())
    def http_GET(self, request):

        if not self.exists():
            log.debug("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        stream = ProducerStream()
        class StreamProtocol(Protocol):
            def dataReceived(self, data):
                stream.write(data)
            def connectionLost(self, reason):
                stream.finish()
        self._newStoreAttachment.retrieve(StreamProtocol())
        return Response(OK, {"content-type":self.contentType()}, stream)


    @requiresPermissions(fromParent=[davxml.Unbind()])
    @inlineCallbacks
    def http_DELETE(self, request):
        if not self.exists():
            log.debug("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        yield self._newStoreCalendarObject.removeAttachmentWithName(
            self._newStoreAttachment.name()
        )
        self._newStoreAttachment = self._newStoreCalendarObject = None
        returnValue(NO_CONTENT)


    http_MKCOL = None
    http_MKCALENDAR = None

    def isCollection(self):
        return False






class NoParent(CalDAVResource):
    def http_MKCALENDAR(self, request):
        return CONFLICT


    def http_PUT(self, request):
        return CONFLICT

    def isCollection(self):
        return False

class _CommonObjectResource(_NewStoreFileMetaDataHelper, CalDAVResource, FancyEqMixin):

    _componentFromStream = None

    def __init__(self, storeObject, parentObject, name, *args, **kw):
        """
        Construct a L{_CommonObjectResource} from an L{CommonObjectResource}.

        @param storeObject: The storage for the object.
        @type storeObject: L{txdav.common.CommonObjectResource}
        """
        super(_CommonObjectResource, self).__init__(*args, **kw)
        self._initializeWithObject(storeObject, parentObject)
        self._name = name
        self._metadata = {}


    def _initializeWithObject(self, storeObject, parentObject):
        self._newStoreParent = parentObject
        self._newStoreObject = storeObject
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreObject.properties()
        ) if self._newStoreObject else NonePropertyStore(self)


    def isCollection(self):
        return False


    def quotaSize(self, request):
        return succeed(self._newStoreObject.size())


    def text(self):
        return self._newStoreObject.text()

    def component(self):
        return self._newStoreObject.component()

    @inlineCallbacks
    def render(self, request):
        if not self.exists():
            log.debug("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        output = yield self.text()

        response = Response(200, {}, output)
        response.headers.setHeader("content-type", self.contentType())
        returnValue(response)


    @requiresPermissions(fromParent=[davxml.Unbind()])
    def http_DELETE(self, request):
        """
        Override http_DELETE to validate 'depth' header. 
        """
        if not self.exists():
            log.err("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        return self.storeRemove(request, True, request.uri)


    @inlineCallbacks
    def storeStream(self, stream):

        # FIXME: direct tests
        component = self._componentFromStream(
            (yield allDataFromStream(stream))
        )
        if self._newStoreObject:
            yield self._newStoreObject.setComponent(component)
            returnValue(NO_CONTENT)
        else:
            self._newStoreObject = (yield self._newStoreParent.createObjectResourceWithName(
                self.name(), component, self._metadata
            ))

            # Re-initialize to get stuff setup again now we have no object
            self._initializeWithObject(self._newStoreObject, self._newStoreParent)

            returnValue(CREATED)


    @inlineCallbacks
    def storeRemove(self, request, implicitly, where):
        """
        Delete this object.

        @param request: Unused by this implementation; present for signature
            compatibility with L{CalendarCollectionResource.storeRemove}.

        @type request: L{twext.web2.iweb.IRequest}

        @return: an HTTP response suitable for sending to a client (or
            including in a multi-status).

         @rtype: something adaptable to L{twext.web2.iweb.IResponse}
        """

        # Do delete

        try:
            yield self._newStoreParent.removeObjectResourceWithName(
                self._newStoreObject.name()
            )
        except NoSuchObjectResourceError:
            raise HTTPError(responsecode.NOT_FOUND)

        # Re-initialize to get stuff setup again now we have no object
        self._initializeWithObject(None, self._newStoreParent)

        returnValue(NO_CONTENT)


class _CalendarObjectMetaDataMixin(object):
    """
    Dynamically create the required meta-data for an object resource 
    """

    def _get_accessMode(self):
        return self._newStoreObject.accessMode if self._newStoreObject else self._metadata.get("accessMode", None)

    def _set_accessMode(self, value):
        if self._newStoreObject:
            self._newStoreObject.accessMode = value
        else:
            self._metadata["accessMode"] = value

    accessMode = property(_get_accessMode, _set_accessMode)

    def _get_isScheduleObject(self):
        return self._newStoreObject.isScheduleObject if self._newStoreObject else self._metadata.get("isScheduleObject", None)

    def _set_isScheduleObject(self, value):
        if self._newStoreObject:
            self._newStoreObject.isScheduleObject = value
        else:
            self._metadata["isScheduleObject"] = value

    isScheduleObject = property(_get_isScheduleObject, _set_isScheduleObject)

    def _get_scheduleTag(self):
        return self._newStoreObject.scheduleTag if self._newStoreObject else self._metadata.get("scheduleTag", None)

    def _set_scheduleTag(self, value):
        if self._newStoreObject:
            self._newStoreObject.scheduleTag = value
        else:
            self._metadata["scheduleTag"] = value

    scheduleTag = property(_get_scheduleTag, _set_scheduleTag)

    def _get_scheduleEtags(self):
        return self._newStoreObject.scheduleEtags if self._newStoreObject else self._metadata.get("scheduleEtags", None)

    def _set_scheduleEtags(self, value):
        if self._newStoreObject:
            self._newStoreObject.scheduleEtags = value
        else:
            self._metadata["scheduleEtags"] = value

    scheduleEtags = property(_get_scheduleEtags, _set_scheduleEtags)

    def _get_hasPrivateComment(self):
        return self._newStoreObject.hasPrivateComment if self._newStoreObject else self._metadata.get("hasPrivateComment", None)

    def _set_hasPrivateComment(self, value):
        if self._newStoreObject:
            self._newStoreObject.hasPrivateComment = value
        else:
            self._metadata["hasPrivateComment"] = value

    hasPrivateComment = property(_get_hasPrivateComment, _set_hasPrivateComment)

class CalendarObjectResource(_CalendarObjectMetaDataMixin, _CommonObjectResource):
    """
    A resource wrapping a calendar object.
    """

    compareAttributes = '_newStoreObject'.split()

    _componentFromStream = VCalendar.fromString

    @inlineCallbacks
    def inNewTransaction(self, request):
        """
        Implicit auto-replies need to span multiple transactions.  Clean out
        the given request's resource-lookup mapping, transaction, and re-look-
        up this L{CalendarObjectResource}'s calendar object in a new
        transaction.

        @return: a Deferred which fires with the new transaction, so it can be
            committed.
        """
        objectName = self._newStoreObject.name()
        calendar = self._newStoreObject.calendar()
        calendarName = calendar.name()
        ownerHome = calendar.ownerCalendarHome()
        homeUID = ownerHome.uid()
        txn = ownerHome.transaction().store().newTransaction(
            "new transaction for " + self._newStoreObject.name())
        newParent = (yield (yield txn.calendarHomeWithUID(homeUID))
                             .calendarWithName(calendarName))
        newObject = (yield newParent.calendarObjectWithName(objectName))
        request._newStoreTransaction = txn
        request._resourcesByURL.clear()
        request._urlsByResource.clear()
        self._initializeWithObject(newObject, newParent)
        returnValue(txn)


    iCalendarText = _CommonObjectResource.text
    iCalendar = _CommonObjectResource.component


    def validIfScheduleMatch(self, request):
        """
        Check to see if the given request's C{If-Schedule-Tag-Match} header
        matches this resource's schedule tag.

        @raise HTTPError: if the tag does not match.

        @return: None
        """
        # Note, internal requests shouldn't issue this.
        header = request.headers.getHeader("If-Schedule-Tag-Match")
        if header:
            # Do "precondition" test
            if (self.scheduleTag != header):
                log.debug(
                    "If-Schedule-Tag-Match: header value '%s' does not match resource value '%s'" %
                    (header, self.scheduleTag,))
                raise HTTPError(PRECONDITION_FAILED)


    @inlineCallbacks
    def storeRemove(self, request, implicitly, where):
        """
        Delete this calendar object and do implicit scheduling actions if
        required.

        @param request: Unused by this implementation; present for signature
            compatibility with L{CalendarCollectionResource.storeRemove}.

        @type request: L{twext.web2.iweb.IRequest}

        @param implicitly: Should implicit scheduling operations be triggered
            as a resut of this C{DELETE}?

        @type implicitly: C{bool}

        @param where: the URI at which the resource is being deleted.
        @type where: C{str}

        @return: an HTTP response suitable for sending to a client (or
            including in a multi-status).

         @rtype: something adaptable to L{twext.web2.iweb.IResponse}
        """

        # TODO: need to use transaction based delete on live scheduling object
        # resources as the iTIP operation may fail and may need to prevent the
        # delete from happening.

        isinbox = self._newStoreObject._calendar.name() == "inbox"

        # Do If-Schedule-Tag-Match behavior first
        if not isinbox:
            self.validIfScheduleMatch(request)

        scheduler = None
        lock = None
        if not isinbox and implicitly:
            # Get data we need for implicit scheduling
            calendar = (yield self.iCalendarForUser(request))
            scheduler = ImplicitScheduler()
            do_implicit_action, _ignore = (
                yield scheduler.testImplicitSchedulingDELETE(
                    request, self, calendar
                )
            )
            if do_implicit_action:
                lock = MemcacheLock(
                    "ImplicitUIDLock", calendar.resourceUID(), timeout=60.0
                )

        try:
            if lock:
                yield lock.acquire()

            yield super(CalendarObjectResource, self).storeRemove(request, implicitly, where)

            # Do scheduling
            if not isinbox and implicitly:
                yield scheduler.doImplicitScheduling()

        except MemcacheLockTimeoutError:
            raise HTTPError(StatusResponse(
                CONFLICT,
                "Resource: %s currently in use on the server." % (where,))
            )

        finally:
            if lock:
                yield lock.clean()

        returnValue(NO_CONTENT)


class AddressBookCollectionResource(_CommonHomeChildCollectionMixin, CalDAVResource):
    """
    Wrapper around a L{txdav.carddav.iaddressbook.IAddressBook}.
    """

    def __init__(self, addressbook, home, name=None, *args, **kw):
        """
        Create a AddressBookCollectionResource from a L{txdav.carddav.iaddressbook.IAddressBook}
        and the arguments required for L{CalDAVResource}.
        """

        self._childClass = AddressBookObjectResource
        super(AddressBookCollectionResource, self).__init__(*args, **kw)
        self._initializeWithHomeChild(addressbook, home)
        self._name = addressbook.name() if addressbook else name


    def __repr__(self):
        return "<AddressBook Collection Resource %r:%r %s>" % (
            self._newStoreParentHome.uid(),
            self._name,
            "" if self._newStoreObject else "Non-existent"
        )


    def isCollection(self):
        return True


    def isAddressBookCollection(self):
        """
        Yes, it is a calendar collection.
        """
        return True


    createAddressBookCollection = _CommonHomeChildCollectionMixin.createCollection


class GlobalAddressBookCollectionResource(GlobalAddressBookResource, AddressBookCollectionResource):
    """
    Wrapper around a L{txdav.carddav.iaddressbook.IAddressBook}.
    """
    pass

class AddressBookObjectResource(_CommonObjectResource):
    """
    A resource wrapping a addressbook object.
    """

    compareAttributes = '_newStoreObject'.split()

    _componentFromStream = VCard.fromString

    vCardText = _CommonObjectResource.text


class _NotificationChildHelper(object):
    """
    Methods for things which are like notification objects.
    """

    def _initializeWithNotifications(self, notifications, home):
        """
        Initialize with a notification collection.

        @param notifications: the wrapped notification collection backend
            object.
        @type notifications: L{txdav.common.inotification.INotificationCollection}

        @param home: the home through which the given notification collection
            was accessed.
        @type home: L{txdav.icommonstore.ICommonHome}
        """
        self._newStoreNotifications = notifications
        self._newStoreParentHome = home
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreNotifications.properties()
        )


    def locateChild(self, request, segments):
        if segments[0] == '':
            return self, segments[1:]
        return self.getChild(segments[0]), segments[1:]


    def notificationsDB(self):
        """
        Retrieve the new-style index wrapper.
        """
        return self._newStoreNotifications.retrieveOldIndex()


    def exists(self):
        # FIXME: tests
        return True


    @inlineCallbacks
    def makeChild(self, name):
        """
        Create a L{NotificationObjectFile} or L{ProtoNotificationObjectFile}
        based on the name of a notification.
        """
        newStoreObject = (
            yield self._newStoreNotifications.notificationObjectWithName(name)
        )

        similar = StoreNotificationObjectFile(newStoreObject, self)

        # FIXME: tests should be failing without this line.
        # Specifically, http_PUT won't be committing its transaction properly.
        self.propagateTransaction(similar)
        returnValue(similar)


    @inlineCallbacks
    def listChildren(self):
        """
        @return: a sequence of the names of all known children of this resource.
        """
        children = set(self.putChildren.keys())
        children.update(self._newStoreNotifications.listNotificationObjects())
        returnValue(children)



class StoreNotificationCollectionResource(_NotificationChildHelper,
                                      NotificationCollectionResource):
    """
    Wrapper around a L{txdav.caldav.icalendar.ICalendar}.
    """

    def __init__(self, notifications, home, *args, **kw):
        """
        Create a CalendarCollectionResource from a L{txdav.caldav.icalendar.ICalendar}
        and the arguments required for L{CalDAVResource}.
        """
        super(StoreNotificationCollectionResource, self).__init__(*args, **kw)
        self._initializeWithNotifications(notifications, home)


    def name(self):
        return "notification"

    @inlineCallbacks
    def listChildren(self):
        l = []
        for notification in (yield self._newStoreNotifications.notificationObjects()):
            l.append(notification.name())
        returnValue(l)

    def isCollection(self):
        return True


    def getSyncToken(self):
        return self._newStoreNotifications.syncToken()


    @inlineCallbacks
    def _indexWhatChanged(self, revision, depth):
        # The newstore implementation supports this directly
        returnValue(
            (yield self._newStoreNotifications.resourceNamesSinceToken(revision))
            + ([],)
        )


    def addNotification(self, request, uid, xmltype, xmldata):
        return maybeDeferred(
            self._newStoreNotifications.writeNotificationObject,
            uid, xmltype, xmldata
        )


    def deleteNotification(self, request, record):
        return maybeDeferred(
            self._newStoreNotifications.removeNotificationObjectWithName,
            record.name
        )


class StoreNotificationObjectFile(_NewStoreFileMetaDataHelper, NotificationResource):
    """
    A resource wrapping a calendar object.
    """

    def __init__(self, notificationObject, *args, **kw):
        """
        Construct a L{CalendarObjectResource} from an L{ICalendarObject}.

        @param calendarObject: The storage for the calendar object.
        @type calendarObject: L{txdav.caldav.icalendarstore.ICalendarObject}
        """
        super(StoreNotificationObjectFile, self).__init__(*args, **kw)
        self._initializeWithObject(notificationObject)


    def _initializeWithObject(self, notificationObject):
        self._newStoreObject = notificationObject
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreObject.properties()
        ) if self._newStoreObject else NonePropertyStore(self)


    def isCollection(self):
        return False


    def quotaSize(self, request):
        return succeed(self._newStoreObject.size())


    def text(self, ignored=None):
        assert ignored is None, "This is a notification object, not a notification"
        return self._newStoreObject.xmldata()


    @requiresPermissions(davxml.Read())
    @inlineCallbacks
    def http_GET(self, request):
        if not self.exists():
            log.err("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        returnValue(
            Response(OK, {"content-type":self.contentType()},
                     MemoryStream((yield self.text())))
        )


    @requiresPermissions(fromParent=[davxml.Unbind()])
    def http_DELETE(self, request):
        """
        Override http_DELETE to validate 'depth' header. 
        """
        if not self.exists():
            log.err("Resource not found: %s" % (self,))
            raise HTTPError(responsecode.NOT_FOUND)

        return self.storeRemove(request, request.uri)


    @inlineCallbacks
    def storeRemove(self, request, where):
        """
        Remove this notification object.
        """
        try:

            storeNotifications = self._newStoreObject.notificationCollection()

            # Do delete

            # FIXME: public attribute please
            yield storeNotifications.removeNotificationObjectWithName(
                self._newStoreObject.name()
            )

            self._initializeWithObject(None)

        except MemcacheLockTimeoutError:
            raise HTTPError(StatusResponse(CONFLICT, "Resource: %s currently in use on the server." % (where,)))
        except NoSuchObjectResourceError:
            raise HTTPError(responsecode.NOT_FOUND)

        returnValue(NO_CONTENT)
