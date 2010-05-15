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

"""
Wrappers to translate between the APIs in L{txcaldav.icalendarstore} and those
in L{twistedcaldav}.
"""

from twisted.internet.defer import inlineCallbacks, returnValue, succeed

from twext.python.filepath import CachingFilePath as FilePath

from twext.web2.http import HTTPError, StatusResponse
from twext.web2 import responsecode

from twistedcaldav.static import CalDAVFile

from txdav.propertystore.base import PropertyName



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
                    responsecode.NOT_FOUND,
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



class CalendarCollectionFile(CalDAVFile):
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
            # FIXME: what about creation in http_PUT?
            similar = CalendarObjectFile(newStoreObject, path)
        else:
            # similar = ProtoCalendarObjectFile(self._newStoreCalendar, path)
            similar = CalDAVFile(path)

        # FIXME: tests should fail without this:
        # self.propagateTransaction(similar)

        # Short-circuit stat with information we know to be true at this point
        if isinstance(path, FilePath) and hasattr(self, "knownChildren"):
            if path.basename() in self.knownChildren:
                path.existsCached = True
                path.isDirCached = False

        #
        # Override the dead property store
        #
        superDeadProperties = similar.deadProperties

        def deadProperties():
            if not hasattr(similar, "_dead_properties"):
                similar._dead_properties = self.propertyCollection().propertyStoreForChild(
                    similar,
                    superDeadProperties(caching=False)
                )
            return similar._dead_properties

        similar.deadProperties = deadProperties

        #
        # Override DELETE, MOVE
        #
        for method in ("DELETE", "MOVE"):
            method = "http_" + method
            original = getattr(similar, method)

            @inlineCallbacks
            def override(request, original=original):

                # Call original method (which is deferred)
                response = (yield original(request))

                # Wipe the cache
                similar.deadProperties().flushCache()

                returnValue(response)

            setattr(similar, method, override)
        self.propagateTransaction(similar)
        return similar



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
        return CalDAVFile(path)


    def createCalendarCollection(self):
        """
        Override C{createCalendarCollection} to actually do the work.
        """
        d = succeed(responsecode.CREATED)

        calendarName = self.fp.basename()
        self._newStoreParentHome.createCalendarWithName(calendarName)
        newStoreCalendar = self._newStoreParentHome.calendarWithName(
            calendarName
        )
        CalendarCollectionFile.transform(
            self, newStoreCalendar, self._newStoreParentHome
        )
        return d



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
        self._newStoreObject = calendarObject
        super(CalendarObjectFile, self).__init__(*args, **kw)



