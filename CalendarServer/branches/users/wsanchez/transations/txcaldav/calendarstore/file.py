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
    "CalendarHome",
    "Calendar",
    "CalendarObject",
]

import errno

from zope.interface import implements

from twext.python.filepath import CachingFilePath as FilePath
from twisted.internet.defer import inlineCallbacks

from twext.python.log import LoggingMixIn
from twext.python.vcomponent import VComponent
from twext.python.vcomponent import InvalidICalendarDataError

from txdav.idav import AbortedTransactionError
from txdav.propertystore.xattr import PropertyStore

from txcaldav.icalendarstore import ICalendarStoreTransaction
from txcaldav.icalendarstore import ICalendarStore, ICalendarHome
from txcaldav.icalendarstore import ICalendar, ICalendarObject
from txcaldav.icalendarstore import CalendarNameNotAllowedError
from txcaldav.icalendarstore import CalendarObjectNameNotAllowedError
from txcaldav.icalendarstore import CalendarAlreadyExistsError
from txcaldav.icalendarstore import CalendarObjectNameAlreadyExistsError
from txcaldav.icalendarstore import NotFoundError
from txcaldav.icalendarstore import NoSuchCalendarError
from txcaldav.icalendarstore import NoSuchCalendarObjectError
from txcaldav.icalendarstore import InvalidCalendarComponentError
from txcaldav.icalendarstore import InternalDataStoreError

from twistedcaldav.index import Index as OldIndex
from twistedcaldav.memcachelock import MemcacheLock, MemcacheLockTimeoutError


class CalendarStore(LoggingMixIn):
    implements(ICalendarStore)

    def __init__(self, path):
        """
        @param path: a L{FilePath}
        """
        assert isinstance(path, FilePath)

        self.path = path

        if not path.isdir():
            # FIXME: Add CalendarStoreNotFoundError?
            raise NotFoundError("No such calendar store")

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self.path.path)

    def newTransaction(self):
        return Transaction(self)


class Transaction(LoggingMixIn):
    implements(ICalendarStoreTransaction)

    def __init__(self, calendarStore):
        self.calendarStore = calendarStore
        self.aborted = False
        self._operations = []
        self._calendarHomes = {}

    def addOperation(self, operation):
        self._operations.append(operation)

    def abort(self):
        self.aborted = True

    def commit(self):
        assert not self.aborted

        undos = []

        for operation in self._operations:
            try:
                undo = operation()
                if undo is not None:
                    undos.append(undo)
            except Exception, e:
                for undo in undos:
                    try:
                        undo()
                    except Exception, e:
                        self.log_error("Exception while undoing transaction: %s" % (e,))
                raise

    def calendarHomeWithUID(self, uid, create=False):
        if (uid, self) in self._calendarHomes:
            return self._calendarHomes[(uid, self)]

        if uid.startswith("."):
            return None

        assert len(uid) >= 4

        childPath1 = self.calendarStore.path.child(uid[0:2])
        childPath2 = childPath1.child(uid[2:4])
        childPath3 = childPath2.child(uid)

        def do():
            def createDirectory(path):
                try:
                    path.createDirectory()
                except (IOError, OSError), e:
                    if e.errno != errno.EEXIST:
                        # Ignore, in case someone else created the
                        # directory while we were trying to as well.
                        raise

            if not childPath3.isdir():
                if not childPath2.isdir():
                    if not childPath1.isdir():
                        createDirectory(childPath1)
                    createDirectory(childPath2)
                createDirectory(childPath3)

        if create:
            self.addOperation(do)
        elif not childPath3.isdir():
            return None

        calendarHome = CalendarHome(childPath3, self.calendarStore, self)
        self._calendarHomes[(uid, self)] = calendarHome
        return calendarHome


class CalendarHome(LoggingMixIn):
    implements(ICalendarHome)

    def __init__(self, path, calendarStore, transaction):
        self.path = path
        self.calendarStore = calendarStore
        self._transaction = transaction
        self._newCalendars = {}
        self._removedCalendars = set()

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self.path)

    def uid(self):
        return self.path.basename()

    def calendars(self):
        return set(self._newCalendars.itervalues()) | set(
            self.calendarWithName(name)
            for name in self.path.listdir()
            if not name.startswith(".")
        )

    def calendarWithName(self, name):
        calendar = self._newCalendars.get(name)
        if calendar is not None:
            return calendar
        if name in self._removedCalendars:
            return None

        if name.startswith("."):
            return None

        childPath = self.path.child(name)
        if childPath.isdir():
            return Calendar(childPath, self)
        else:
            return None

    def createCalendarWithName(self, name):
        if name.startswith("."):
            raise CalendarNameNotAllowedError(name)

        childPath = self.path.child(name)

        if name not in self._removedCalendars and childPath.isdir():
            raise CalendarAlreadyExistsError(name)

        def do():
            try:
                childPath.createDirectory()

                # Return undo
                return lambda: childPath.remove()
            except (IOError, OSError), e:
                if e.errno == errno.EEXIST and childPath.isdir():
                    raise CalendarAlreadyExistsError(name)
                raise

        self._transaction.addOperation(do)
        self._newCalendars[name] = Calendar(self.path.child(name), self)

    def removeCalendarWithName(self, name):
        if name.startswith(".") or name in self._removedCalendars:
            raise NoSuchCalendarError(name)

        self._removedCalendars.add(name)
        childPath = self.path.child(name)
        if name not in self._newCalendars and not childPath.isdir():
            raise NoSuchCalendarError(name)

        def do(transaction=self._transaction):
            for i in xrange(1000):
                trash = childPath.sibling("._del_%s_%d" % (childPath.basename(), i))
                if not trash.exists():
                    break
            else:
                raise InternalDataStoreError("Unable to create trash target for calendar at %s" % (childPath,))

            try:
                childPath.moveTo(trash)
            except (IOError, OSError), e:
                if e.errno == errno.ENOENT:
                    raise NoSuchCalendarError(name)
                raise

            def cleanup():
                try:
                    trash.remove()
                except Exception, e:
                    self.log_error("Unable to delete trashed calendar at %s: %s" % (trash.fp, e))

            transaction.addOperation(cleanup)

            def undo():
                trash.moveTo(childPath)

            return undo

    def properties(self):
        # raise NotImplementedError()
        if not hasattr(self, "_properties"):
            self._properties = PropertyStore(self.path)
        return self._properties


class Calendar(LoggingMixIn):
    implements(ICalendar)

    def __init__(self, path, calendarHome):
        self.path = path
        self.calendarHome = calendarHome
        self._transaction = calendarHome._transaction
        self._newCalendarObjects = {}
        self._removedCalendarObjects = set()

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self.path.path)

    def index(self):
        if not hasattr(self, "_index"):
            self._index = Index(self)
        return self._index

    def name(self):
        return self.path.basename()

    def ownerCalendarHome(self):
        return self.calendarHome

#    def _calendarObjects_index(self):
#        return self.index().calendarObjects()
#
#    def _calendarObjects_listdir(self):
#        return (
#            self.calendarObjectWithName(name)
#            for name in self.path.listdir()
#            if not name.startswith(".")
#        )

    def calendarObjects(self):
        return (
            self.calendarObjectWithName(name)
            for name in (
                set(self._newCalendarObjects.iterkeys()) |
                set(name for name in self.path.listdir() if not name.startswith("."))
            )
        )

    def calendarObjectWithName(self, name):
        if name in self._newCalendarObjects:
            return self._newCalendarObjects[name]

        calendarObjectPath = self.path.child(name)
        if calendarObjectPath.isfile():
            return CalendarObject(calendarObjectPath, self)
        else:
            return None

    def calendarObjectWithUID(self, uid):
        return None
        # for calendarObjectPath in self.path.children():
        #     obj = CalendarObject(calendarObjectPath, self)
        #     if obj.component().resourceUID() == uid:
        #         return obj

    def createCalendarObjectWithName(self, name, component):
        if name.startswith("."):
            raise CalendarObjectNameNotAllowedError(name)

        calendarObjectPath = self.path.child(name)
        if calendarObjectPath.exists():
            raise CalendarObjectNameAlreadyExistsError(name)

        calendarObject = CalendarObject(calendarObjectPath, self)
        calendarObject.setComponent(component)

    def removeCalendarObjectWithName(self, name):
        if name.startswith("."):
            raise NoSuchCalendarObjectError(name)

        calendarObjectPath = self.path.child(name)
        if calendarObjectPath.isfile():
            calendarObjectPath.remove()
        else:
            raise NoSuchCalendarObjectError(name)

    def removeCalendarObjectWithUID(self, uid):
        raise NotImplementedError()

    def syncToken(self):
        raise NotImplementedError()

    @inlineCallbacks
    def _updateSyncToken(self, reset=False):
        return

        lock = MemcacheLock("Calendar", self.fp.path, timeout=60.0)
        try:
            try:
                yield lock.acquire()
            except MemcacheLockTimeoutError:
                raise InternalDataStoreError("Timed out on calendar lock")

            def newToken():
                raise NotImplementedError()

            if reset:
                token = newToken()

            raise NotImplementedError(token)

        finally:
            yield lock.clean()


        raise NotImplementedError()

    def calendarObjectsInTimeRange(self, start, end, timeZone):
        raise NotImplementedError()

    def calendarObjectsSinceToken(self, token):
        raise NotImplementedError()

    def properties(self):
        raise NotImplementedError()
        if not hasattr(self, "_properties"):
            self._properties = PropertyStore(self.path)
        return self._properties


class CalendarObject(LoggingMixIn):
    implements(ICalendarObject)

    def __init__(self, path, calendar):
        self.path = path
        self.calendar = calendar

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self.path.path)

    def name(self):
        return self.path.basename()

    def setComponent(self, component):
        if not isinstance(component, VComponent):
            raise TypeError(VComponent)

        try:
            if component.resourceUID() != self.uid():
                raise InvalidCalendarComponentError(
                    "UID may not change (%s != %s)" % (
                        component.resourceUID(), self.uid()
                     )
                )
        except NoSuchCalendarObjectError:
            pass

        try:
            component.validateForCalDAV()
        except InvalidICalendarDataError, e:
            raise InvalidCalendarComponentError(e)

        self._component = component
        if hasattr(self, "_text"):
            del self._text

        fh = self.path.open("w")
        try:
            fh.write(str(component))
        finally:
            fh.close()

    def component(self):
        if not hasattr(self, "_component"):
            text = self.iCalendarText()

            try:
                component = VComponent.fromString(text)
            except InvalidICalendarDataError, e:
                raise InternalDataStoreError(
                    "File corruption detected (%s) in file: %s"
                    % (e, self.path.path)
                )

            del self._text
            self._component = component

        return self._component

    def iCalendarText(self):
        #
        # Note I'm making an assumption here that caching both is
        # redundant, so we're caching the text if it's asked for and
        # we don't have the component cached, then tossing it and
        # relying on the component if we have that cached. -wsv
        #
        if not hasattr(self, "_text"):
            if hasattr(self, "_component"):
                return str(self._component)

            try:
                fh = self.path.open()
            except IOError, e:
                if e[0] == errno.ENOENT:
                    raise NoSuchCalendarObjectError(self)

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
                    % (self.path.path,)
                )

            self._text = text

        return self._text

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

    def properties(self):
        raise NotImplementedError()
        if not hasattr(self, "_properties"):
            self._properties = PropertyStore(self.path)
        return self._properties


class Index (object):
    #
    # OK, here's where we get ugly.
    # The index code needs to be rewritten also, but in the meantime...
    #
    class StubResource(object):
        """
        Just enough resource to keep the Index class going.
        """
        def __init__(self, calendar):
            self.calendar = calendar
            self.fp = self.calendar.path

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
            return self.calendar._updateSyncToken(reset)


    def __init__(self, calendar):
        self.calendar = calendar
        self._oldIndex = OldIndex(Index.StubResource(calendar))

    def calendarObjects(self):
        calendar = self.calendar
        for name, uid, componentType in self._oldIndex.bruteForceSearch():
            calendarObject = calendar.calendarObjectWithName(name)

            # Precache what we found in the index
            calendarObject._uid = uid
            calendarObject._componentType = componentType

            yield calendarObject
