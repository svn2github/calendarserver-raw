# -*- test-case-name: twistedcaldav.test.test_wrapping -*-
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

"""
Wrappers to translate between the APIs in L{txcaldav.icalendarstore} and
L{txcarddav.iaddressbookstore} and those in L{twistedcaldav}.
"""

import hashlib

from urlparse import urlsplit

from twisted.internet.defer import succeed, inlineCallbacks, returnValue

from twext.python.filepath import CachingFilePath as FilePath
from twext.python import vcomponent

from twext.web2.http_headers import ETag
from twext.web2.dav.http import ErrorResponse, ResponseQueue
from twext.web2.responsecode import (
    FORBIDDEN, NO_CONTENT, NOT_FOUND, CREATED, CONFLICT, PRECONDITION_FAILED,
    BAD_REQUEST)
from twext.python.log import Logger
from twext.web2.dav.resource import TwistedGETContentMD5
from twext.web2.dav.util import parentForURL, allDataFromStream, joinURL
from twext.web2.http import HTTPError, StatusResponse

from twistedcaldav.static import CalDAVFile, ScheduleInboxFile

from txdav.propertystore.base import PropertyName
from txcaldav.icalendarstore import NoSuchCalendarObjectError
from txcarddav.iaddressbookstore import NoSuchAddressBookObjectError
from twistedcaldav.vcard import Component as VCard

from twistedcaldav.caldavxml import ScheduleTag, caldav_namespace
from twistedcaldav.scheduling.implicit import ImplicitScheduler
from twistedcaldav.memcachelock import MemcacheLock, MemcacheLockTimeoutError

from twisted.python.log import err as logDefaultException

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


class _CalendarChildHelper(object):
    """
    Methods for things which are like calendars.
    """

    def _initializeWithCalendar(self, calendar, home):
        """
        Initialize with a calendar.

        @param calendar: the wrapped calendar.
        @type calendar: L{txcaldav.icalendarstore.ICalendar}

        @param home: the home through which the given calendar was accessed.
        @type home: L{txcaldav.icalendarstore.ICalendarHome}
        """
        self._newStoreCalendar = calendar
        self._newStoreParentHome = home
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreCalendar.properties()
        )


    def index(self):
        """
        Retrieve the new-style index wrapper.
        """
        return self._newStoreCalendar.retrieveOldIndex()


    def exists(self):
        # FIXME: tests
        return True


    @classmethod
    def transform(cls, self, calendar, home):
        """
        Transform C{self} into a L{CalendarCollectionFile}.
        """
        self.__class__ = cls
        self._initializeWithCalendar(calendar, home)


    def createSimilarFile(self, path):
        """
        Create a L{CalendarObjectFile} or L{ProtoCalendarObjectFile} based on a
        path object.
        """
        if not isinstance(path, FilePath):
            path = FilePath(path)

        newStoreObject = self._newStoreCalendar.calendarObjectWithName(
            path.basename()
        )

        if newStoreObject is not None:
            similar = CalendarObjectFile(newStoreObject, path,
                principalCollections=self._principalCollections)
        else:
            # FIXME: creation in http_PUT should talk to a specific resource
            # type; this is the domain of StoreCalendarObjectResource.
            # similar = ProtoCalendarObjectFile(self._newStoreCalendar, path)
            similar = ProtoCalendarObjectFile(self._newStoreCalendar, path,
                principalCollections=self._principalCollections)

        # FIXME: tests should be failing without this line.
        # Specifically, http_PUT won't be committing its transaction properly.
        self.propagateTransaction(similar)
        return similar


    def quotaSize(self, request):
        # FIXME: tests, workingness
        return succeed(0)



class StoreScheduleInboxFile(_CalendarChildHelper, ScheduleInboxFile):

    def __init__(self, *a, **kw):
        super(StoreScheduleInboxFile, self).__init__(*a, **kw)
        self.parent.propagateTransaction(self)
        home = self.parent._newStoreCalendarHome
        storage = home.calendarWithName("inbox")
        if storage is None:
            # FIXME: spurious error, sanity check, should not be needed
            raise RuntimeError("backend should be handling this for us")
            storage = home.calendarWithName("inbox")
        self._initializeWithCalendar(
            storage,
            self.parent._newStoreCalendarHome
        )


    def isCollection(self):
        return True

    def provisionFile(self):
        pass


    def provision(self):
        pass


class CalendarCollectionFile(_CalendarChildHelper, CalDAVFile):
    """
    Wrapper around a L{txcaldav.icalendar.ICalendar}.
    """

    def __init__(self, calendar, home, *args, **kw):
        """
        Create a CalendarCollectionFile from a L{txcaldav.icalendar.ICalendar}
        and the arguments required for L{CalDAVFile}.
        """
        super(CalendarCollectionFile, self).__init__(*args, **kw)
        self._initializeWithCalendar(calendar, home)


    def isCalendarCollection(self):
        """
        Yes, it is a calendar collection.
        """
        return True


    def http_DELETE(self, request):
        """
        Override http_DELETE to validate 'depth' header. 
        """
        depth = request.headers.getHeader("depth", "infinity")
        if depth != "infinity":
            msg = "illegal depth header for DELETE on collection: %s" % (
                depth,
            )
            log.err(msg)
            raise HTTPError(StatusResponse(BAD_REQUEST, msg))
        return self.storeRemove(request, True, request.uri)


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
            raise HTTPError(ErrorResponse(FORBIDDEN,
                            (caldav_namespace,
                             "default-calendar-delete-allowed",)))

        # Is this a sharee's view of a shared calendar?  If so, they can't do
        # scheduling onto it, so just delete it and move on.
        isVirtual = yield self.isVirtualShare(request)
        if isVirtual:
            log.debug("Removing shared calendar %s" % (self,))
            yield self.removeVirtualShare(request)
            returnValue(NO_CONTENT)

        log.debug("Deleting calendar %s" % (self,))

        # 'deluri' is this resource's URI; I should be able to synthesize it
        # from 'self'.

        errors = ResponseQueue(where, "DELETE", NO_CONTENT)

        for childname in self.listChildren():

            childurl = joinURL(where, childname)

            # FIXME: use a more specific API; we should know what this child
            # resource is, and not have to look it up.  (Sharing information
            # needs to move into the back-end first, though.)
            child = (yield request.locateChildResource(self, childname))

            try:
                yield child.storeRemove(request, implicitly, childurl)
            except:
                logDefaultException()
                errors.add(childurl, BAD_REQUEST)

        # Now do normal delete

        # Handle sharing
        wasShared = (yield self.isShared(request))
        if wasShared:
            yield self.downgradeFromShare(request)

        # Actually delete it.
        self._newStoreParentHome.removeCalendarWithName(
            self._newStoreCalendar.name()
        )
        self.__class__ = ProtoCalendarCollectionFile
        del self._newStoreCalendar

        # FIXME: handle exceptions, possibly like this:

        #        if isinstance(more_responses, MultiStatusResponse):
        #            # Merge errors
        #            errors.responses.update(more_responses.children)

        response = errors.response()

        if response == NO_CONTENT:
            # Do some clean up
            yield self.deletedCalendar(request)

        returnValue(response)


    def isCollection(self):
        return True

    def http_COPY(self, request):
        """
        Copying of calendar collections isn't allowed.
        """
        # FIXME: no direct tests
        return FORBIDDEN


    @inlineCallbacks
    def http_MOVE(self, request):
        """
        Moving a calendar collection is allowed for the purposes of changing
        that calendar's name.
        """
        defaultCalendar = (yield self.isDefaultCalendar(request))
        # FIXME: created to fix CDT test, no unit tests yet
        sourceURI = request.uri
        destinationURI = urlsplit(request.headers.getHeader("destination"))[2]
        if parentForURL(sourceURI) != parentForURL(destinationURI):
            returnValue(FORBIDDEN)
        destination = yield request.locateResource(destinationURI)
        # FIXME: should really use something other than 'fp' attribute.
        basename = destination.fp.basename()
        calendar = self._newStoreCalendar
        calendar.rename(basename)
        CalendarCollectionFile.transform(destination, calendar,
                                         self._newStoreParentHome)
        del self._newStoreCalendar
        self.__class__ = ProtoCalendarCollectionFile
        self.movedCalendar(request, defaultCalendar,
                           destination, destinationURI)
        returnValue(NO_CONTENT)



class NoParent(CalDAVFile):
    def http_MKCALENDAR(self, request):
        return CONFLICT


    def http_PUT(self, request):
        return CONFLICT



class ProtoCalendarCollectionFile(CalDAVFile):
    """
    A resource representing a calendar collection which hasn't yet been created.
    """

    def __init__(self, home, *args, **kw):
        """
        A placeholder resource for a calendar collection which does not yet
        exist, but will become a L{CalendarCollectionFile}.

        @param home: The calendar home which will be this resource's parent,
            when it exists.

        @type home: L{txcaldav.icalendarstore.ICalendarHome}
        """
        self._newStoreParentHome = home
        super(ProtoCalendarCollectionFile, self).__init__(*args, **kw)


    def isCollection(self):
        return True

    def createSimilarFile(self, path):
        # FIXME: this is necessary for 
        # twistedcaldav.test.test_mkcalendar.
        #     MKCALENDAR.test_make_calendar_no_parent - there should be a more
        # structured way to refuse creation with a non-existent parent.
        return NoParent(path)


    def provisionFile(self):
        """
        Create a calendar collection.
        """
        # FIXME: this should be done in the backend; provisionDefaultCalendars
        # should go away.
        return self.createCalendarCollection()


    def createCalendarCollection(self):
        """
        Override C{createCalendarCollection} to actually do the work.
        """
        d = succeed(CREATED)

        calendarName = self.fp.basename()
        self._newStoreParentHome.createCalendarWithName(calendarName)
        newStoreCalendar = self._newStoreParentHome.calendarWithName(
            calendarName
        )
        CalendarCollectionFile.transform(
            self, newStoreCalendar, self._newStoreParentHome
        )
        return d


    def exists(self):
        # FIXME: tests
        return False


    def provision(self):
        """
        This resource should do nothing if it's provisioned.
        """
        # FIXME: should be deleted, or raise an exception


    def quotaSize(self, request):
        # FIXME: tests, workingness
        return succeed(0)



class CalendarObjectFile(CalDAVFile):
    """
    A resource wrapping a calendar object.
    """

    def __init__(self, calendarObject, *args, **kw):
        """
        Construct a L{CalendarObjectFile} from an L{ICalendarObject}.

        @param calendarObject: The storage for the calendar object.
        @type calendarObject: L{txcaldav.icalendarstore.ICalendarObject}
        """
        super(CalendarObjectFile, self).__init__(*args, **kw)
        self._initializeWithObject(calendarObject)


    def isCollection(self):
        return False


    def inNewTransaction(self, request):
        """
        Implicit auto-replies need to span multiple transactions.  Clean out
        the given request's resource-lookup mapping, transaction, and re-look-
        up my calendar object in a new transaction.

        Return the new transaction so it can be committed.
        """
        # FIXME: private names from 'file' implementation; maybe there should
        # be a public way to do this?  or maybe we should just have a real
        # queue.
        objectName = self._newStoreObject.name()
        calendarName = self._newStoreObject._calendar.name()
        homeUID = self._newStoreObject._calendar._calendarHome.uid()
        store = self._newStoreObject._transaction._calendarStore
        txn = store.newTransaction()
        newObject = (txn.calendarHomeWithUID(homeUID)
                        .calendarWithName(calendarName)
                        .calendarObjectWithName(objectName))
        request._newStoreTransaction = txn
        request._resourcesByURL.clear()
        request._urlsByResource.clear()
        self._initializeWithObject(newObject)
        return txn


    def exists(self):
        # FIXME: Tests
        return True


    def etag(self):
        # FIXME: far too slow to be used for real, but I needed something to
        # placate the etag computation in the case where the file doesn't exist
        # yet (an uncommited transaction creating this calendar file)

        # FIXME: direct tests
        try:
            if self.hasDeadProperty(TwistedGETContentMD5):
                return ETag(str(self.readDeadProperty(TwistedGETContentMD5)))
            else:
                return ETag(
                    hashlib.new("md5", self.iCalendarText()).hexdigest(),
                    weak=False
                )
        except NoSuchCalendarObjectError:
            # FIXME: a workaround for the fact that DELETE still rudely vanishes
            # the calendar object out from underneath the store, and doesn't
            # call storeRemove.
            return None


    def newStoreProperties(self):
        return self._newStoreObject.properties()


    def quotaSize(self, request):
        # FIXME: tests
        return succeed(len(self._newStoreObject.iCalendarText()))


    def iCalendarText(self, ignored=None):
        assert ignored is None, "This is a calendar object, not a calendar"
        return self._newStoreObject.iCalendarText()


    @inlineCallbacks
    def storeStream(self, stream):
        # FIXME: direct tests
        component = vcomponent.VComponent.fromString(
            (yield allDataFromStream(stream))
        )
        self._newStoreObject.setComponent(component)
        returnValue(NO_CONTENT)


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
            matched = False
            if self.hasDeadProperty(ScheduleTag):
                scheduletag = self.readDeadProperty(ScheduleTag)
                matched = (scheduletag == header)
            if not matched:
                log.debug(
                    "If-Schedule-Tag-Match: header value '%s' does not match resource value '%s'" %
                    (header, scheduletag,))
                raise HTTPError(PRECONDITION_FAILED)


    @inlineCallbacks
    def storeRemove(self, request, implicitly, where):
        """
        Delete this calendar object and do implicit scheduling actions if
        required.

        @param request: Unused by this implementation; present for signature
            compatibility with L{CalendarCollectionFile.storeRemove}.

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

        # Do If-Schedule-Tag-Match behavior first
        self.validIfScheduleMatch(request)

        # Do quota checks before we start deleting things
        myquota = (yield self.quota(request))
        if myquota is not None:
            old_size = (yield self.quotaSize(request))
        else:
            old_size = 0

        scheduler = None
        lock = None
        if implicitly:
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

            storeCalendar = self._newStoreObject._calendar
            # Do delete

            # FIXME: public attribute please.  Should ICalendar maybe just have
            # a delete() method?
            storeCalendar.removeCalendarObjectWithName(
                self._newStoreObject.name()
            )

            # FIXME: clean this up with a 'transform' method
            self._newStoreParentCalendar = storeCalendar
            del self._newStoreObject
            self.__class__ = ProtoCalendarObjectFile

            # Adjust quota
            if myquota is not None:
                yield self.quotaSizeAdjust(request, -old_size)

            # Do scheduling
            if implicitly:
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


    def _initializeWithObject(self, calendarObject):
        self._newStoreObject = calendarObject
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreObject.properties()
        )


    @classmethod
    def transform(cls, self, calendarObject):
        self.__class__ = cls
        self._initializeWithObject(calendarObject)



class ProtoCalendarObjectFile(CalDAVFile):

    def __init__(self, parentCalendar, *a, **kw):
        super(ProtoCalendarObjectFile, self).__init__(*a, **kw)
        self._newStoreParentCalendar = parentCalendar


    @inlineCallbacks
    def storeStream(self, stream):
        # FIXME: direct tests 
        component = vcomponent.VComponent.fromString(
            (yield allDataFromStream(stream))
        )
        self._newStoreParentCalendar.createCalendarObjectWithName(
            self.fp.basename(), component
        )
        CalendarObjectFile.transform(self, self._newStoreParentCalendar.calendarObjectWithName(self.fp.basename()))
        returnValue(CREATED)


    def isCollection(self):
        return False

    def exists(self):
        # FIXME: tests
        return False


    def quotaSize(self, request):
        # FIXME: tests, workingness
        return succeed(0)

class _AddressBookChildHelper(object):
    """
    Methods for things which are like addressbooks.
    """

    def _initializeWithAddressBook(self, addressbook, home):
        """
        Initialize with a addressbook.

        @param addressbook: the wrapped addressbook.
        @type addressbook: L{txcarddav.iaddressbookstore.IAddressBook}

        @param home: the home through which the given addressbook was accessed.
        @type home: L{txcarddav.iaddressbookstore.IAddressBookHome}
        """
        self._newStoreAddressBook = addressbook
        self._newStoreParentHome = home
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreAddressBook.properties()
        )


    def index(self):
        """
        Retrieve the new-style index wrapper.
        """
        return self._newStoreAddressBook.retrieveOldIndex()


    def exists(self):
        # FIXME: tests
        return True


    @classmethod
    def transform(cls, self, addressbook, home):
        """
        Transform C{self} into a L{AddressBookCollectionFile}.
        """
        self.__class__ = cls
        self._initializeWithAddressBook(addressbook, home)


    def createSimilarFile(self, path):
        """
        Create a L{AddressBookObjectFile} or L{ProtoAddressBookObjectFile} based on a
        path object.
        """
        if not isinstance(path, FilePath):
            path = FilePath(path)

        newStoreObject = self._newStoreAddressBook.addressbookObjectWithName(
            path.basename()
        )

        if newStoreObject is not None:
            similar = AddressBookObjectFile(newStoreObject, path,
                principalCollections=self._principalCollections)
        else:
            # FIXME: creation in http_PUT should talk to a specific resource
            # type; this is the domain of StoreAddressBookObjectResource.
            # similar = ProtoAddressBookObjectFile(self._newStoreAddressBook, path)
            similar = ProtoAddressBookObjectFile(self._newStoreAddressBook, path,
                principalCollections=self._principalCollections)

        # FIXME: tests should be failing without this line.
        # Specifically, http_PUT won't be committing its transaction properly.
        self.propagateTransaction(similar)
        return similar


    def quotaSize(self, request):
        # FIXME: tests, workingness
        return succeed(0)



class AddressBookCollectionFile(_AddressBookChildHelper, CalDAVFile):
    """
    Wrapper around a L{txcarddav.iaddressbook.IAddressBook}.
    """

    def __init__(self, addressbook, home, *args, **kw):
        """
        Create a AddressBookCollectionFile from a L{txcarddav.iaddressbook.IAddressBook}
        and the arguments required for L{CalDAVFile}.
        """
        super(AddressBookCollectionFile, self).__init__(*args, **kw)
        self._initializeWithAddressBook(addressbook, home)


    def isCollection(self):
        return True

    def http_COPY(self, request):
        """
        Copying of addressbook collections isn't allowed.
        """
        # FIXME: no direct tests
        return FORBIDDEN


    @inlineCallbacks
    def http_MOVE(self, request):
        """
        Moving a addressbook collection is allowed for the purposes of changing
        that addressbook's name.
        """
        # FIXME: created to fix CDT test, no unit tests yet
        sourceURI = request.uri
        destinationURI = urlsplit(request.headers.getHeader("destination"))[2]
        if parentForURL(sourceURI) != parentForURL(destinationURI):
            returnValue(FORBIDDEN)
        destination = yield request.locateResource(destinationURI)
        # FIXME: should really use something other than 'fp' attribute.
        basename = destination.fp.basename()
        addressbook = self._newStoreAddressBook
        addressbook.rename(basename)
        AddressBookCollectionFile.transform(destination, addressbook,
                                         self._newStoreParentHome)
        del self._newStoreAddressBook
        self.__class__ = ProtoAddressBookCollectionFile
        returnValue(NO_CONTENT)


class ProtoAddressBookCollectionFile(CalDAVFile):
    """
    A resource representing an addressbook collection which hasn't yet been created.
    """

    def __init__(self, home, *args, **kw):
        """
        A placeholder resource for an addressbook collection which does not yet
        exist, but will become a L{AddressBookCollectionFile}.

        @param home: The addressbook home which will be this resource's parent,
            when it exists.

        @type home: L{txcarddav.iaddressbookstore.IAddressBookHome}
        """
        self._newStoreParentHome = home
        super(ProtoAddressBookCollectionFile, self).__init__(*args, **kw)


    def isCollection(self):
        return True

    def createSimilarFile(self, path):
        # FIXME: this is necessary for 
        # twistedcaldav.test.test_mkcol.
        #     MKCOL.test_make_addressbook_no_parent - there should be a more
        # structured way to refuse creation with a non-existent parent.
        return NoParent(path)


    def provisionFile(self):
        """
        Create an addressbook collection.
        """
        # FIXME: this should be done in the backend; provisionDefaultAddressBooks
        # should go away.
        return self.createAddressBookCollection()


    def createAddressBookCollection(self):
        """
        Override C{createAddressBookCollection} to actually do the work.
        """
        d = succeed(CREATED)

        Name = self.fp.basename()
        self._newStoreParentHome.createAddressBookWithName(Name)
        newStoreAddressBook = self._newStoreParentHome.addressbookWithName(
            Name
        )
        AddressBookCollectionFile.transform(
            self, newStoreAddressBook, self._newStoreParentHome
        )
        return d


    def exists(self):
        # FIXME: tests
        return False


    def provision(self):
        """
        This resource should do nothing if it's provisioned.
        """
        # FIXME: should be deleted, or raise an exception


    def quotaSize(self, request):
        # FIXME: tests, workingness
        return succeed(0)



class AddressBookObjectFile(CalDAVFile):
    """
    A resource wrapping a addressbook object.
    """

    def __init__(self, Object, *args, **kw):
        """
        Construct a L{AddressBookObjectFile} from an L{IAddressBookObject}.

        @param Object: The storage for the addressbook object.
        @type Object: L{txcarddav.iaddressbookstore.IAddressBookObject}
        """
        super(AddressBookObjectFile, self).__init__(*args, **kw)
        self._initializeWithObject(Object)


    def inNewTransaction(self, request):
        """
        Implicit auto-replies need to span multiple transactions.  Clean out the
        given request's resource-lookup mapping, transaction, and re-look-up my
        addressbook object in a new transaction.

        Return the new transaction so it can be committed.
        """
        # FIXME: private names from 'file' implementation; maybe there should be
        # a public way to do this?  or maybe we should just have a real queue.
        objectName = self._newStoreObject.name()
        Name = self._newStoreObject._addressbook.name()
        homeUID = self._newStoreObject._addressbook._Home.uid()
        store = self._newStoreObject._transaction._Store
        txn = store.newTransaction()
        newObject = (txn.HomeWithUID(homeUID)
                        .addressbookWithName(Name)
                        .addressbookObjectWithName(objectName))
        request._newStoreTransaction = txn
        request._resourcesByURL.clear()
        request._urlsByResource.clear()
        self._initializeWithObject(newObject)
        return txn


    def isCollection(self):
        return False

    def exists(self):
        # FIXME: Tests
        return True


    def etag(self):
        # FIXME: far too slow to be used for real, but I needed something to
        # placate the etag computation in the case where the file doesn't exist
        # yet (an uncommited transaction creating this addressbook file)

        # FIXME: direct tests
        try:
            if self.hasDeadProperty(TwistedGETContentMD5):
                return ETag(str(self.readDeadProperty(TwistedGETContentMD5)))
            else:
                return ETag(
                    hashlib.new("md5", self.vCardText()).hexdigest(),
                    weak=False
                )
        except NoSuchAddressBookObjectError:
            # FIXME: a workaround for the fact that DELETE still rudely vanishes
            # the addressbook object out from underneath the store, and doesn't
            # call storeRemove.
            return None


    def newStoreProperties(self):
        return self._newStoreObject.properties()


    def quotaSize(self, request):
        # FIXME: tests
        return succeed(len(self._newStoreObject.vCardText()))


    def vCardText(self, ignored=None):
        assert ignored is None, "This is a addressbook object, not a addressbook"
        return self._newStoreObject.vCardText()


    @inlineCallbacks
    def storeStream(self, stream):
        # FIXME: direct tests
        component = VCard.fromString(
            (yield allDataFromStream(stream))
        )
        self._newStoreObject.setComponent(component)
        returnValue(NO_CONTENT)


    def storeRemove(self):
        """
        Remove this addressbook object.
        """
        # FIXME: public attribute please
        self._newStoreObject._addressbook.removeAddressBookObjectWithName(self._newStoreObject.name())
        # FIXME: clean this up with a 'transform' method
        self._newStoreParentAddressBook = self._newStoreObject._addressbook
        del self._newStoreObject
        self.__class__ = ProtoAddressBookObjectFile


    def _initializeWithObject(self, Object):
        self._newStoreObject = Object
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreObject.properties()
        )


    @classmethod
    def transform(cls, self, Object):
        self.__class__ = cls
        self._initializeWithObject(Object)



class ProtoAddressBookObjectFile(CalDAVFile):

    def __init__(self, parentAddressBook, *a, **kw):
        super(ProtoAddressBookObjectFile, self).__init__(*a, **kw)
        self._newStoreParentAddressBook = parentAddressBook


    @inlineCallbacks
    def storeStream(self, stream):
        # FIXME: direct tests 
        component = VCard.fromString(
            (yield allDataFromStream(stream))
        )
        self._newStoreParentAddressBook.createAddressBookObjectWithName(
            self.fp.basename(), component
        )
        AddressBookObjectFile.transform(self, self._newStoreParentAddressBook.addressbookObjectWithName(self.fp.basename()))
        returnValue(CREATED)


    def isCollection(self):
        return False

    def exists(self):
        # FIXME: tests
        return False


    def quotaSize(self, request):
        # FIXME: tests, workingness
        return succeed(0)
