# -*- test-case-name: txcaldav.calendarstore.test.test_file -*-
##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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
File calendar store.
"""

__all__ = [
    "CalendarStore",
    "CalendarStoreTransaction",
    "CalendarHome",
    "Calendar",
    "CalendarObject",
]

from errno import ENOENT

from twext.python.vcomponent import InvalidICalendarDataError
from twext.python.vcomponent import VComponent
from twext.web2.dav.element.rfc2518 import ResourceType

from twistedcaldav.caldavxml import ScheduleCalendarTransp, Opaque
from twistedcaldav.index import Index as OldIndex, IndexSchedule as OldInboxIndex
from twistedcaldav.sharing import InvitesDatabase

from txcaldav.icalendarstore import ICalendar, ICalendarObject
from txcaldav.icalendarstore import ICalendarHome

from txdav.common.datastore.file import CommonDataStore, CommonStoreTransaction,\
    CommonHome, CommonHomeChild, CommonObjectResource
from txdav.common.icommondatastore import InvalidObjectResourceError,\
    NoSuchObjectResourceError, InternalDataStoreError
from txdav.datastore.file import writeOperation, hidden
from txdav.propertystore.base import PropertyName

from zope.interface import implements

CalendarStore = CommonDataStore

CalendarStoreTransaction = CommonStoreTransaction

class CalendarHome(CommonHome):
    implements(ICalendarHome)

    def __init__(self, uid, path, calendarStore, transaction):
        super(CalendarHome, self).__init__(uid, path, calendarStore, transaction)

        self._childClass = Calendar

    calendars = CommonHome.children
    calendarWithName = CommonHome.childWithName
    createCalendarWithName = CommonHome.createChildWithName
    removeCalendarWithName = CommonHome.removeChildWithName

    @property
    def _calendarStore(self):
        return self._dataStore

    def created(self):
        self.createCalendarWithName("calendar")
        defaultCal = self.calendarWithName("calendar")
        props = defaultCal.properties()
        props[PropertyName(*ScheduleCalendarTransp.qname())] = ScheduleCalendarTransp(
            Opaque())
        self.createCalendarWithName("inbox")

class Calendar(CommonHomeChild):
    """
    File-based implementation of L{ICalendar}.
    """
    implements(ICalendar)

    def __init__(self, name, calendarHome, realName=None):
        """
        Initialize a calendar pointing at a path on disk.

        @param name: the subdirectory of calendarHome where this calendar
            resides.
        @type name: C{str}

        @param calendarHome: the home containing this calendar.
        @type calendarHome: L{CalendarHome}

        @param realName: If this calendar was just created, the name which it
        will eventually have on disk.
        @type realName: C{str}
        """

        super(Calendar, self).__init__(name, calendarHome, realName)

        self._index = Index(self)
        self._invites = Invites(self)
        self._objectResourceClass = CalendarObject

    @property
    def _calendarHome(self):
        return self._home

    def resourceType(self):
        return ResourceType.calendar

    ownerCalendarHome = CommonHomeChild.ownerHome
    calendarObjects = CommonHomeChild.objectResources
    calendarObjectWithName = CommonHomeChild.objectResourceWithName
    calendarObjectWithUID = CommonHomeChild.objectResourceWithUID
    createCalendarObjectWithName = CommonHomeChild.createObjectResourceWithName
    removeCalendarObjectWithName = CommonHomeChild.removeObjectResourceWithName
    removeCalendarObjectWithUID = CommonHomeChild.removeObjectResourceWithUID
    calendarObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken


    def calendarObjectsInTimeRange(self, start, end, timeZone):
        raise NotImplementedError()


    def _doValidate(self, component):
        # FIXME: should be separate class, not separate case!
        if self.name() == 'inbox':
            component.validateComponentsForCalDAV(True)
        else:
            component.validateForCalDAV()


class CalendarObject(CommonObjectResource):
    """
    @ivar _path: The path of the .ics file on disk

    @type _path: L{FilePath}
    """
    implements(ICalendarObject)

    def __init__(self, name, calendar):
        super(CalendarObject, self).__init__(name, calendar)

    @property
    def _calendar(self):
        return self._parentCollection

    @writeOperation
    def setComponent(self, component):
        if not isinstance(component, VComponent):
            raise TypeError(type(component))

        try:
            if component.resourceUID() != self.uid():
                raise InvalidObjectResourceError(
                    "UID may not change (%s != %s)" % (
                        component.resourceUID(), self.uid()
                     )
                )
        except NoSuchObjectResourceError:
            pass

        try:
            self._calendar._doValidate(component)
        except InvalidICalendarDataError, e:
            raise InvalidObjectResourceError(e)

        newRevision = self._calendar._updateSyncToken() # FIXME: test
        self._calendar.retrieveOldIndex().addResource(
            self.name(), component, newRevision
        )

        self._component = component
        # FIXME: needs to clear text cache

        def do():
            backup = None
            if self._path.exists():
                backup = hidden(self._path.temporarySibling())
                self._path.moveTo(backup)
            fh = self._path.open("w")
            try:
                # FIXME: concurrency problem; if this write is interrupted
                # halfway through, the underlying file will be corrupt.
                fh.write(str(component))
            finally:
                fh.close()
            def undo():
                if backup:
                    backup.moveTo(self._path)
                else:
                    self._path.remove()
            return undo
        self._transaction.addOperation(do, "set calendar component %r" % (self.name(),))

        # Mark all properties as dirty, so they will be re-added to the
        # temporary file when the main file is deleted. NOTE: if there were a
        # temporary file and a rename() as there should be, this should really
        # happen after the write but before the rename.
        self.properties().update(self.properties())

        # FIXME: the property store's flush() method may already have been
        # added to the transaction, but we need to add it again to make sure it
        # happens _after_ the new file has been written.  we may end up doing
        # the work multiple times, and external callers to property-
        # manipulation methods won't work.
        self._transaction.addOperation(self.properties().flush, "post-update property flush")


    def component(self):
        if self._component is not None:
            return self._component
        text = self.iCalendarText()

        try:
            component = VComponent.fromString(text)
        except InvalidICalendarDataError, e:
            raise InternalDataStoreError(
                "File corruption detected (%s) in file: %s"
                % (e, self._path.path)
            )
        return component


    def iCalendarText(self):
        if self._component is not None:
            return str(self._component)
        try:
            fh = self._path.open()
        except IOError, e:
            if e[0] == ENOENT:
                raise NoSuchObjectResourceError(self)
            else:
                raise

        try:
            text = fh.read()
        finally:
            fh.close()

        if not (
            text.startswith("BEGIN:VCALENDAR\r\n") or
            text.endswith("\r\nEND:VCALENDAR\r\n")
        ):
            raise InternalDataStoreError(
                "File corruption detected (improper start) in file: %s"
                % (self._path.path,)
            )
        return text


    def uid(self):
        if not hasattr(self, "_uid"):
            self._uid = self.component().resourceUID()
        return self._uid

    def componentType(self):
        if not hasattr(self, "_componentType"):
            self._componentType = self.component().mainType()
        return self._componentType

    def organizer(self):
        return self.component().getOrganizer()


class CalendarStubResource(object):
    """
    Just enough resource to keep the calendar's sql DB classes going.
    """
    def __init__(self, calendar):
        self.calendar = calendar
        self.fp = self.calendar._path

    def isCalendarCollection(self):
        return True

    def getChild(self, name):
        calendarObject = self.calendar.calendarObjectWithName(name)
        if calendarObject:
            class ChildResource(object):
                def __init__(self, calendarObject):
                    self.calendarObject = calendarObject

                def iCalendar(self):
                    return self.calendarObject.component()

            return ChildResource(calendarObject)
        else:
            return None

    def bumpSyncToken(self, reset=False):
        # FIXME: needs direct tests
        return self.calendar._updateSyncToken(reset)


    def initSyncToken(self):
        # FIXME: needs direct tests
        self.bumpSyncToken(True)

class Index(object):
    #
    # OK, here's where we get ugly.
    # The index code needs to be rewritten also, but in the meantime...
    #
    def __init__(self, calendar):
        self.calendar = calendar
        stubResource = CalendarStubResource(calendar)
        if self.calendar.name() == 'inbox':
            indexClass = OldInboxIndex
        else:
            indexClass = OldIndex
        self._oldIndex = indexClass(stubResource)


    def calendarObjects(self):
        calendar = self.calendar
        for name, uid, componentType in self._oldIndex.bruteForceSearch():
            calendarObject = calendar.calendarObjectWithName(name)

            # Precache what we found in the index
            calendarObject._uid = uid
            calendarObject._componentType = componentType

            yield calendarObject


class Invites(object):
    #
    # OK, here's where we get ugly.
    # The index code needs to be rewritten also, but in the meantime...
    #
    def __init__(self, calendar):
        self.calendar = calendar
        stubResource = CalendarStubResource(calendar)
        self._oldInvites = InvitesDatabase(stubResource)
