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
Wrappers to translate between the APIs in L{txcaldav.icalendarstore} and those
in L{twistedcaldav}.
"""

import hashlib

from urlparse import urlsplit

from twisted.internet.defer import succeed, inlineCallbacks, returnValue

from twext.python.filepath import CachingFilePath as FilePath
from twext.python import vcomponent

from twext.web2.http_headers import ETag
from twext.web2.responsecode import FORBIDDEN, NO_CONTENT, NOT_FOUND, CREATED, \
    CONFLICT
from twext.web2.dav.util import parentForURL, allDataFromStream
from twext.web2.http import HTTPError, StatusResponse

from twistedcaldav.static import CalDAVFile, ScheduleInboxFile

from txdav.propertystore.base import PropertyName
from txcaldav.icalendarstore import NoSuchCalendarObjectError



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


    def inNewTransaction(self, request):
        """
        Implicit auto-replies need to span multiple transactions.  Clean out the
        given request's resource-lookup mapping, transaction, and re-look-up my
        calendar object in a new transaction.

        Return the new transaction so it can be committed.
        """
        # FIXME: private names from 'file' implementation; maybe there should be
        # a public way to do this?  or maybe we should just have a real queue.
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
            return ETag(
                hashlib.new("sha1", self.iCalendarText()).hexdigest(),
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


    def storeRemove(self):
        """
        Remove this calendar object.
        """
        # FIXME: public attribute please
        self._newStoreObject._calendar.removeCalendarObjectWithName(self._newStoreObject.name())
        # FIXME: clean this up with a 'transform' method
        self._newStoreParentCalendar = self._newStoreObject._calendar
        del self._newStoreObject
        self.__class__ = ProtoCalendarObjectFile


    def _initializeWithObject(self, calendarObject):
        self._newStoreObject = calendarObject


    @classmethod
    def transform(cls, self, calendarObject):
        self.__class__ = cls
        self._initializeWithObject(calendarObject)
        self._dead_properties = _NewStorePropertiesWrapper(
            self._newStoreObject.properties()
        )



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


    def exists(self):
        # FIXME: tests
        return False


    def quotaSize(self, request):
        # FIXME: tests, workingness
        return succeed(0)
