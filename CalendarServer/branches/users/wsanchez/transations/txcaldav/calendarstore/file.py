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

from twisted.internet.defer import inlineCallbacks

from twext.python.log import LoggingMixIn
from twext.python.vcomponent import VComponent
from twext.python.vcomponent import InvalidICalendarDataError

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

def _isValidName(name):
    """
    Determine if the given string is a valid name.  i.e. does it conflict with
    any of the other entities which may be on the filesystem?

    @param name: a name which might be given to a calendar.
    """
    return not name.startswith(".")


_unset = object()

class _cached(object):
    """
    This object is a decorator for a 0-argument method which should be called
    only once, and its result cached so that future invocations just return the
    same result without calling the underlying method again.

    @ivar thunk: the function to call to generate a cached value.
    """

    def __init__(self, thunk):
        self.thunk = thunk


    def __get__(self, oself, name):
        def inner():
            cacheKey = "_"+name+"_cached"
            cached = getattr(oself, cacheKey, _unset)
            if cached is _unset:
                value = self.thunk(oself)
                setattr(oself, cacheKey, value)
                return value
            else:
                return cached
        return inner



class CalendarStore(LoggingMixIn):
    """
    An implementation of L{ICalendarObject} backed by a
    L{twext.python.filepath.CachingFilePath}.

    @ivar _path: A L{CachingFilePath} referencing a directory on disk that
        stores all calendar data for a group of uids.
    """
    implements(ICalendarStore)

    def __init__(self, path):
        """
        Create a calendar store.

        @param path: a L{FilePath} pointing at a directory on disk.
        """
        self._path = path

#        if not path.isdir():
            # FIXME: Add CalendarStoreNotFoundError?
#            raise NotFoundError("No such calendar store")

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)

    def newTransaction(self):
        """
        Create a new filesystem-based transaction.

        @see Transaction
        """
        return Transaction(self)



class Transaction(LoggingMixIn):
    """
    In-memory implementation of

    Note that this provides basic 'undo' support, but not truly transactional
    operations.
    """

    implements(ICalendarStoreTransaction)

    def __init__(self, calendarStore):
        """
        Initialize a transaction; do not call this directly, instead call
        L{CalendarStore.newTransaction}.

        @param calendarStore: The store that created this transaction.

        @type calendarStore: L{CalendarStore}
        """
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

        childPath1 = self.calendarStore._path.child(uid[0:2])
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
        self._path = path
        self.calendarStore = calendarStore
        self._transaction = transaction
        self._newCalendars = {}
        self._removedCalendars = set()

    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path)

    def uid(self):
        return self._path.basename()

    def calendars(self):
        return set(self._newCalendars.itervalues()) | set(
            self.calendarWithName(name)
            for name in self._path.listdir()
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

        childPath = self._path.child(name)
        if childPath.isdir():
            return Calendar(childPath, self)
        else:
            return None

    def createCalendarWithName(self, name):
        if name.startswith("."):
            raise CalendarNameNotAllowedError(name)

        childPath = self._path.child(name)

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
        self._newCalendars[name] = Calendar(self._path.child(name), self)

    def removeCalendarWithName(self, name):
        if name.startswith(".") or name in self._removedCalendars:
            raise NoSuchCalendarError(name)

        self._removedCalendars.add(name)
        childPath = self._path.child(name)
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
        # FIXME: needs tests for actual functionality
        # FIXME: needs to be cached
        return PropertyStore(self._path)



class Calendar(LoggingMixIn):
    """
    File-based implementation of L{ICalendar}.
    """
    implements(ICalendar)

    def __init__(self, path, calendarHome):
        self._path = path
        self.calendarHome = calendarHome
        self._transaction = calendarHome._transaction
        self._newCalendarObjects = {}
        self._cachedCalendarObjects = {}
        self._removedCalendarObjects = set()


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    def name(self):
        return self._path.basename()


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
                set(name for name in self._path.listdir() if not name.startswith("."))
            )
        )


    def calendarObjectWithName(self, name):
        if name in self._removedCalendarObjects:
            return None
        if name in self._newCalendarObjects:
            return self._newCalendarObjects[name]
        if name in self._cachedCalendarObjects:
            return self._cachedCalendarObjects[name]


        calendarObjectPath = self._path.child(name)
        if calendarObjectPath.isfile():
            obj = CalendarObject(calendarObjectPath, self)
            self._cachedCalendarObjects[name] = obj
            return obj
        else:
            return None


    def calendarObjectWithUID(self, uid):
        # FIXME: This _really_ needs to be inspecting an index, not parsing
        # every resource.
        for calendarObjectPath in self._path.children():
            if not _isValidName(calendarObjectPath.basename()):
                continue
            obj = CalendarObject(calendarObjectPath, self)
            if obj.component().resourceUID() == uid:
                return obj


    def createCalendarObjectWithName(self, name, component):
        if name.startswith("."):
            raise CalendarObjectNameNotAllowedError(name)

        calendarObjectPath = self._path.child(name)
        if calendarObjectPath.exists():
            raise CalendarObjectNameAlreadyExistsError(name)

        calendarObject = CalendarObject(calendarObjectPath, self)
        calendarObject.setComponent(component)
        self._cachedCalendarObjects[name] = calendarObject


    def removeCalendarObjectWithName(self, name):
        if name.startswith("."):
            raise NoSuchCalendarObjectError(name)

        calendarObjectPath = self._path.child(name)
        if calendarObjectPath.isfile():
            self._removedCalendarObjects.add(name)
            # FIXME: test for undo
            calendarObjectPath.remove()
        else:
            raise NoSuchCalendarObjectError(name)

    def removeCalendarObjectWithUID(self, uid):
        self.removeCalendarObjectWithName(self.calendarObjectWithUID(uid)._path.basename())

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
        # FIXME: needs tests
        # FIXME: needs implementation
        raise NotImplementedError()



class CalendarObject(LoggingMixIn):
    """
    @ivar path: The path of the .ics file on disk

    @type path: L{FilePath}
    """
    implements(ICalendarObject)

    def __init__(self, path, calendar):
        self._path = path
        self.calendar = calendar
        self._component = None


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    def name(self):
        return self._path.basename()


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
        # FIXME: needs to clear text cache

        def do():
            backup = None
            if self._path.exists():
                backup = self._path.temporarySibling()
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
        self.calendar._transaction.addOperation(do)


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
            if e[0] == errno.ENOENT:
                raise NoSuchCalendarObjectError(self)
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

    def properties(self):
        raise NotImplementedError()
        if not hasattr(self, "_properties"):
            self._properties = PropertyStore(self._path)
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
