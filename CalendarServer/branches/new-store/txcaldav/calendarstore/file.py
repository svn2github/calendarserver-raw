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

from uuid import uuid4
from errno import EEXIST, ENOENT

from zope.interface import implements

from twisted.python.util import FancyEqMixin

from twisted.internet.defer import succeed

from twisted.python import log
from twext.python.log import LoggingMixIn
from twext.python.vcomponent import VComponent
from twext.python.vcomponent import InvalidICalendarDataError
from twext.web2.dav.element.rfc2518 import ResourceType

from txdav.propertystore.xattr import PropertyStore
from txdav.propertystore.base import PropertyName
PN = PropertyName.fromString

from txcaldav.icalendarstore import ICalendarStoreTransaction
from txcaldav.icalendarstore import ICalendarStore, ICalendarHome
from txcaldav.icalendarstore import ICalendar, ICalendarObject
from txcaldav.icalendarstore import CalendarNameNotAllowedError
from txcaldav.icalendarstore import CalendarObjectNameNotAllowedError
from txcaldav.icalendarstore import CalendarAlreadyExistsError
from txcaldav.icalendarstore import CalendarObjectNameAlreadyExistsError
from txcaldav.icalendarstore import NoSuchCalendarError
from txcaldav.icalendarstore import NoSuchCalendarObjectError
from txcaldav.icalendarstore import InvalidCalendarComponentError
from txcaldav.icalendarstore import InternalDataStoreError

from twistedcaldav.caldavxml import ScheduleCalendarTransp, Transparent, Opaque
from twistedcaldav.customxml import GETCTag

from twistedcaldav.index import Index as OldIndex, IndexSchedule as OldInboxIndex

def _isValidName(name):
    """
    Determine if the given string is a valid name.  i.e. does it conflict with
    any of the other entities which may be on the filesystem?

    @param name: a name which might be given to a calendar.
    """
    return not name.startswith(".")


def _hidden(path):
    return path.sibling('.' + path.basename())


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


    def __get__(self, oself, owner):
        def inner():
            cacheKey = "_" + self.thunk.__name__ + "_cached"
            cached = getattr(oself, cacheKey, _unset)
            if cached is _unset:
                value = self.thunk(oself)
                setattr(oself, cacheKey, value)
                return value
            else:
                return cached
        return inner



def _writeOperation(thunk):
    # FIXME: tests
    def inner(self, *a, **kw):
        if self._transaction._termination is not None:
            raise RuntimeError(
                "%s.%s is a write operation, but transaction already %s"
                % (self, thunk.__name__, self._transaction._termination))
        return thunk(self, *a, **kw)
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



class _CommitTracker(object):
    """
    Diagnostic tool to find transactions that were never committed.
    """

    def __init__(self):
        self.done = False
        self.info = []

    def __del__(self):
        if not self.done and self.info:
            print '**** UNCOMMITTED TRANSACTION BEING GARBAGE COLLECTED ****'
            for info in self.info:
                print '   ', info
            print '---- END OF OPERATIONS'



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
        self._calendarStore = calendarStore
        self._termination = None
        self._operations = []
        self._tracker = _CommitTracker()
        self._calendarHomes = {}


    def addOperation(self, operation, name):
        self._operations.append(operation)
        self._tracker.info.append(name)


    def _terminate(self, mode):
        """
        Check to see if this transaction has already been terminated somehow,
        either via committing or aborting, and if not, note that it has been
        terminated.

        @param mode: The manner of the termination of this transaction.
        
        @type mode: C{str}

        @raise RuntimeError: This transaction has already been terminated.
        """
        if self._termination is not None:
            raise RuntimeError("already %s" % (self._termination,))
        self._termination = mode
        self._tracker.done = True


    def abort(self):
        self._terminate("aborted")


    def commit(self):
        self._terminate("committed")

        self.committed = True
        undos = []

        for operation in self._operations:
            try:
                undo = operation()
                if undo is not None:
                    undos.append(undo)
            except:
                log.err()
                for undo in undos:
                    try:
                        undo()
                    except:
                        log.err()
                raise


    def calendarHomeWithUID(self, uid, create=False):
        if (uid, self) in self._calendarHomes:
            return self._calendarHomes[(uid, self)]

        if uid.startswith("."):
            return None

        assert len(uid) >= 4

        childPath1 = self._calendarStore._path.child(uid[0:2])
        childPath2 = childPath1.child(uid[2:4])
        childPath3 = childPath2.child(uid)
        def createDirectory(path):
            try:
                path.createDirectory()
            except (IOError, OSError), e:
                if e.errno != EEXIST:
                    # Ignore, in case someone else created the
                    # directory while we were trying to as well.
                    raise

        creating = False
        if create:
            if not childPath2.isdir():
                if not childPath1.isdir():
                    createDirectory(childPath1)
                createDirectory(childPath2)
            if childPath3.isdir():
                calendarPath = childPath3
            else:
                creating = True
                calendarPath = childPath3.temporarySibling()
                createDirectory(calendarPath)
                def do():
                    def lastly():
                        calendarPath.moveTo(childPath3)
                        # calendarHome._path = calendarPath
                        # do this _after_ all other file operations
                        calendarHome._path = childPath3
                        return lambda : None
                    self.addOperation(lastly, "create home finalize")
                    return lambda : None
                self.addOperation(do, "create home UID %r" % (uid,))

        elif not childPath3.isdir():
            return None
        else:
            calendarPath = childPath3

        calendarHome = CalendarHome(calendarPath, self._calendarStore, self)
        self._calendarHomes[(uid, self)] = calendarHome
        if creating:
            calendarHome.createCalendarWithName("calendar")
            defaultCal = calendarHome.calendarWithName("calendar")
            props = defaultCal.properties()
            props[PN(ScheduleCalendarTransp.sname())] = ScheduleCalendarTransp(
                Opaque())
            calendarHome.createCalendarWithName("inbox")
        return calendarHome



class CalendarHome(LoggingMixIn):
    implements(ICalendarHome)

    def __init__(self, path, calendarStore, transaction):
        self._calendarStore = calendarStore
        self._path = path
        self._transaction = transaction
        self._newCalendars = {}
        self._removedCalendars = set()
        self._cachedCalendars = {}


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path)


    def uid(self):
        return self._path.basename()


    def _updateSyncToken(self, reset=False):
        "Stub for updating sync token."
        # FIXME: actually update something


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
        if name in self._cachedCalendars:
            return self._cachedCalendars[name]

        if name.startswith("."):
            return None

        childPath = self._path.child(name)
        if childPath.isdir():
            existingCalendar = Calendar(name, self)
            self._cachedCalendars[name] = existingCalendar
            return existingCalendar
        else:
            return None


    @_writeOperation
    def createCalendarWithName(self, name):
        if name.startswith("."):
            raise CalendarNameNotAllowedError(name)

        childPath = self._path.child(name)

        if name not in self._removedCalendars and childPath.isdir():
            raise CalendarAlreadyExistsError(name)

        temporary = _hidden(childPath.temporarySibling())
        temporary.createDirectory()
        # In order for the index to work (which is doing real file ops on disk
        # via SQLite) we need to create a real directory _immediately_.

        # FIXME: some way to roll this back.

        c = self._newCalendars[name] = Calendar(temporary.basename(), self, name)
        c.retrieveOldIndex().create()
        def do():
            try:
                props = c.properties()
                temporary.moveTo(childPath)
                c._name = name
                # FIXME: _lots_ of duplication of work here.
                props.path = childPath
                props.flush()
            except (IOError, OSError), e:
                if e.errno == EEXIST and childPath.isdir():
                    raise CalendarAlreadyExistsError(name)
                raise
            # FIXME: direct tests, undo for index creation
            # Return undo
            return lambda: childPath.remove()

        self._transaction.addOperation(do, "create calendar %r" % (name,))
        props = c.properties()
        CalendarType = ResourceType.calendar #@UndefinedVariable
        props[PN(ResourceType.sname())] = CalendarType

        # Calendars are initially transparent to freebusy.  FIXME: freebusy
        # needs more structured support than this.
        props[PN(ScheduleCalendarTransp.sname())] = ScheduleCalendarTransp(
            Transparent())
        # FIXME: there's no need for 'flush' to be a public method of the
        # property store any more.  It should just be transactional like
        # everything else; the API here would better be expressed as
        # c.properties().participateInTxn(txn)
        # FIXME: return c # maybe ?


    @_writeOperation
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
                if e.errno == ENOENT:
                    raise NoSuchCalendarError(name)
                raise

            def cleanup():
                try:
                    trash.remove()
                except Exception, e:
                    self.log_error("Unable to delete trashed calendar at %s: %s" % (trash.fp, e))

            transaction.addOperation(cleanup, "remove calendar backup %r" % (name,))

            def undo():
                trash.moveTo(childPath)

            return undo
        # FIXME: direct tests
        self._transaction.addOperation(
            do, "prepare calendar remove %r" % (name,)
        )


    # @_cached
    def properties(self):
        # FIXME: needs tests for actual functionality
        # FIXME: needs to be cached
        # FIXME: transaction tests
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "flush home properties")
        return props



class Calendar(LoggingMixIn, FancyEqMixin):
    """
    File-based implementation of L{ICalendar}.
    """
    implements(ICalendar)

    compareAttributes = '_name _calendarHome _transaction'.split()

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
        self._name = name
        self._calendarHome = calendarHome
        self._transaction = calendarHome._transaction
        self._newCalendarObjects = {}
        self._cachedCalendarObjects = {}
        self._removedCalendarObjects = set()
        self._index = Index(self)
        self._renamedName = realName


    @property
    def _path(self):
        return self._calendarHome._path.child(self._name)


    def retrieveOldIndex(self):
        """
        Retrieve the old Index object.
        """
        return self._index._oldIndex


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    def name(self):
        if self._renamedName is not None:
            return self._renamedName
        return self._path.basename()


    _renamedName = None

    @_writeOperation
    def rename(self, name):
        self._updateSyncToken()
        oldName = self.name()
        self._renamedName = name
        self._calendarHome._newCalendars[name] = self
        self._calendarHome._removedCalendars.add(oldName)
        def doIt():
            self._path.moveTo(self._path.sibling(name))
            return lambda : None # FIXME: revert
        self._transaction.addOperation(doIt, "rename calendar %r -> %r" %
                                       (oldName, name))


    def ownerCalendarHome(self):
        return self._calendarHome


    def calendarObjects(self):
        return sorted((
            self.calendarObjectWithName(name)
            for name in (
                set(self._newCalendarObjects.iterkeys()) |
                set(name for name in self._path.listdir()
                    if not name.startswith(".")) -
                set(self._removedCalendarObjects)
            )),
            key=lambda calObj: calObj.name()
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
            obj = CalendarObject(name, self)
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
            obj = CalendarObject(calendarObjectPath.basename(), self)
            if obj.component().resourceUID() == uid:
                if obj.name() in self._removedCalendarObjects:
                    return None
                return obj


    @_writeOperation
    def createCalendarObjectWithName(self, name, component):
        if name.startswith("."):
            raise CalendarObjectNameNotAllowedError(name)

        calendarObjectPath = self._path.child(name)
        if calendarObjectPath.exists():
            raise CalendarObjectNameAlreadyExistsError(name)

        calendarObject = CalendarObject(name, self)
        calendarObject.setComponent(component)
        self._cachedCalendarObjects[name] = calendarObject


    @_writeOperation
    def removeCalendarObjectWithName(self, name):
        newRevision = self._updateSyncToken() # FIXME: Test
        self.retrieveOldIndex().deleteResource(name, newRevision)
        if name.startswith("."):
            raise NoSuchCalendarObjectError(name)

        calendarObjectPath = self._path.child(name)
        if calendarObjectPath.isfile():
            self._removedCalendarObjects.add(name)
            # FIXME: test for undo
            def do():
                calendarObjectPath.remove()
                return lambda: None
            self._transaction.addOperation(do, "remove calendar object %r" %
                                           (name,))
        else:
            raise NoSuchCalendarObjectError(name)


    @_writeOperation
    def removeCalendarObjectWithUID(self, uid):
        self.removeCalendarObjectWithName(
            self.calendarObjectWithUID(uid)._path.basename())


    def _updateSyncToken(self, reset=False):
        # FIXME: add locking a-la CalDAVFile.bumpSyncToken
        # FIXME: tests for desired concurrency properties
        ctag = PropertyName.fromString(GETCTag.sname())
        props = self.properties()
        token = props.get(ctag)
        if token is None or reset:
            caluuid = uuid4()
            revision = 1
        else:
            # FIXME: no direct tests for update
            token = str(token)
            caluuid, revision = token.split("#", 1)
            revision = int(revision) + 1
        token = "%s#%d" % (caluuid, revision)
        props[ctag] = GETCTag(token)
        # FIXME: no direct tests for commit
        succeed(token)


    def calendarObjectsInTimeRange(self, start, end, timeZone):
        raise NotImplementedError()

    def calendarObjectsSinceToken(self, token):
        raise NotImplementedError()


    # FIXME: property writes should be a write operation
    @_cached
    def properties(self):
        # FIXME: needs direct tests - only covered by calendar store tests
        # FIXME: transactions
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush,
                                       "flush calendar properties")
        return props


    def _doValidate(self, component):
        # FIXME: should be separate class, not separate case!
        if self.name() == 'inbox':
            component.validateComponentsForCalDAV(True)
        else:
            component.validateForCalDAV()



class CalendarObject(LoggingMixIn):
    """
    @ivar _path: The path of the .ics file on disk

    @type _path: L{FilePath}
    """
    implements(ICalendarObject)

    def __init__(self, name, calendar):
        self._name = name
        self._calendar = calendar
        self._transaction = calendar._transaction
        self._component = None


    @property
    def _path(self):
        return self._calendar._path.child(self._name)


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._path.path)


    def name(self):
        return self._path.basename()


    @_writeOperation
    def setComponent(self, component):

        newRevision = self._calendar._updateSyncToken() # FIXME: test
        self._calendar.retrieveOldIndex().addResource(
            self.name(), component, newRevision
        )

        if not isinstance(component, VComponent):
            raise TypeError(type(component))

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
            self._calendar._doValidate(component)
        except InvalidICalendarDataError, e:
            raise InvalidCalendarComponentError(e)

        self._component = component
        # FIXME: needs to clear text cache

        def do():
            backup = None
            if self._path.exists():
                backup = _hidden(self._path.temporarySibling())
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

    @_cached
    def properties(self):
        props = PropertyStore(self._path)
        self._transaction.addOperation(props.flush, "object properties flush")
        return props



class Index(object):
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
            # FIXME: needs direct tests
            return self.calendar._updateSyncToken(reset)


        def initSyncToken(self):
            # FIXME: needs direct tests
            self.bumpSyncToken(True)


    def __init__(self, calendar):
        self.calendar = calendar
        stubResource = Index.StubResource(calendar)
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
