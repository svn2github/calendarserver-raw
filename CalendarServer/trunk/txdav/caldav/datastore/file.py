# -*- test-case-name: txdav.caldav.datastore.test.test_file -*-
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

import hashlib

from errno import ENOENT

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.interfaces import ITransport
from twisted.python.failure import Failure

from txdav.base.propertystore.xattr import PropertyStore

from twext.python.vcomponent import InvalidICalendarDataError
from twext.python.vcomponent import VComponent
from twext.web2.dav import davxml
from twext.web2.dav.element.rfc2518 import ResourceType, GETContentType
from twext.web2.dav.resource import TwistedGETContentMD5
from twext.web2.http_headers import generateContentType

from twistedcaldav import caldavxml, customxml
from twistedcaldav.caldavxml import ScheduleCalendarTransp, Opaque
from twistedcaldav.sharing import InvitesDatabase

from txdav.caldav.icalendarstore import IAttachment
from txdav.caldav.icalendarstore import ICalendar, ICalendarObject
from txdav.caldav.icalendarstore import ICalendarHome

from txdav.caldav.datastore.index_file import Index as OldIndex,\
    IndexSchedule as OldInboxIndex
from txdav.caldav.datastore.util import (
    validateCalendarComponent, dropboxIDFromCalendarObject
)

from txdav.common.datastore.file import (
    CommonDataStore, CommonStoreTransaction, CommonHome, CommonHomeChild,
    CommonObjectResource
, CommonStubResource)

from txdav.common.icommondatastore import (NoSuchObjectResourceError,
    InternalDataStoreError)
from txdav.base.datastore.file import writeOperation, hidden, FileMetaDataMixin
from txdav.base.propertystore.base import PropertyName

from zope.interface import implements

contentTypeKey = PropertyName.fromElement(GETContentType)
md5key = PropertyName.fromElement(TwistedGETContentMD5)

CalendarStore = CommonDataStore

CalendarStoreTransaction = CommonStoreTransaction

IGNORE_NAMES = ('dropbox', 'notification', 'freebusy')

class CalendarHome(CommonHome):
    implements(ICalendarHome)

    _topPath = "calendars"
    _notifierPrefix = "CalDAV"

    def __init__(self, uid, path, calendarStore, transaction, notifiers):
        super(CalendarHome, self).__init__(uid, path, calendarStore, transaction, notifiers)

        self._childClass = Calendar


    createCalendarWithName = CommonHome.createChildWithName
    removeCalendarWithName = CommonHome.removeChildWithName


    def childWithName(self, name):
        if name in IGNORE_NAMES:
            # "dropbox" is a file storage area, not a calendar.
            return None
        else:
            return super(CalendarHome, self).childWithName(name)

    calendarWithName = childWithName


    def children(self):
        """
        Return a generator of the child resource objects.
        """
        for child in self.listCalendars():
            yield self.calendarWithName(child)

    calendars = children

    def listChildren(self):
        """
        Return a generator of the child resource names.
        """
        for name in super(CalendarHome, self).listChildren():
            if name in IGNORE_NAMES:
                continue
            yield name

    listCalendars = listChildren
    loadCalendars = CommonHome.loadChildren


    @inlineCallbacks
    def calendarObjectWithDropboxID(self, dropboxID):
        """
        Implement lookup with brute-force scanning.
        """
        for calendar in self.calendars():
            for calendarObject in calendar.calendarObjects():
                if dropboxID == (yield calendarObject.dropboxID()):
                    returnValue(calendarObject)


    @property
    def _calendarStore(self):
        return self._dataStore


    def createdHome(self):
        defaultCal = self.createCalendarWithName("calendar")
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
        super(Calendar, self).__init__(name, calendarHome, realName=realName)

        self._index = Index(self)
        self._invites = Invites(self)
        self._objectResourceClass = CalendarObject


    @property
    def _calendarHome(self):
        return self._home


    def resourceType(self):
        return ResourceType.calendar #@UndefinedVariable


    ownerCalendarHome = CommonHomeChild.ownerHome
    calendarObjects = CommonHomeChild.objectResources
    listCalendarObjects = CommonHomeChild.listObjectResources
    calendarObjectWithName = CommonHomeChild.objectResourceWithName
    calendarObjectWithUID = CommonHomeChild.objectResourceWithUID
    createCalendarObjectWithName = CommonHomeChild.createObjectResourceWithName
    removeCalendarObjectWithName = CommonHomeChild.removeObjectResourceWithName
    removeCalendarObjectWithUID = CommonHomeChild.removeObjectResourceWithUID
    calendarObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken


    def calendarObjectsInTimeRange(self, start, end, timeZone):
        raise NotImplementedError()


    def initPropertyStore(self, props):
        # Setup peruser special properties
        props.setSpecialProperties(
            (
                PropertyName.fromElement(caldavxml.CalendarDescription),
                PropertyName.fromElement(caldavxml.CalendarTimeZone),
            ),
            (
                PropertyName.fromElement(customxml.GETCTag),
                PropertyName.fromElement(caldavxml.SupportedCalendarComponentSet),
            ),
        )



class CalendarObject(CommonObjectResource):
    """
    @ivar _path: The path of the .ics file on disk

    @type _path: L{FilePath}
    """
    implements(ICalendarObject)

    def __init__(self, name, calendar, metadata=None):
        super(CalendarObject, self).__init__(name, calendar)
        self._attachments = {}
        
        if metadata is None:
            metadata = {}
        self.accessMode = metadata.get("accessMode", "")
        self.isScheduleObject = metadata.get("isScheduleObject", False)
        self.scheduleTag = metadata.get("scheduleTag", "")
        self.scheduleEtags = metadata.get("scheduleEtags", "")
        self.hasPrivateComment = metadata.get("hasPrivateComment", False)


    @property
    def _calendar(self):
        return self._parentCollection


    def calendar(self):
        return self._calendar


    @writeOperation
    def setComponent(self, component, inserting=False):

        old_size = 0 if inserting else self.size()

        validateCalendarComponent(self, self._calendar, component, inserting)

        self._calendar.retrieveOldIndex().addResource(
            self.name(), component
        )

        self._component = component
        # FIXME: needs to clear text cache

        def do():
            # Mark all properties as dirty, so they can be added back
            # to the newly updated file.
            self.properties().update(self.properties())

            backup = None
            if self._path.exists():
                backup = hidden(self._path.temporarySibling())
                self._path.moveTo(backup)
            
            componentText = str(component)
            fh = self._path.open("w")
            try:
                # FIXME: concurrency problem; if this write is interrupted
                # halfway through, the underlying file will be corrupt.
                fh.write(componentText)
            finally:
                fh.close()

            md5 = hashlib.md5(componentText).hexdigest()
            self.properties()[md5key] = TwistedGETContentMD5.fromString(md5)

            # Now re-write the original properties on the updated file
            self.properties().flush()

            # Adjust quota
            quota_adjustment = self.size() - old_size
            self._calendar._home.adjustQuotaUsedBytes(quota_adjustment)

            def undo():
                if backup:
                    backup.moveTo(self._path)
                else:
                    self._path.remove()
                self._calendar._home.adjustQuotaUsedBytes(-quota_adjustment)
            return undo
        self._transaction.addOperation(do, "set calendar component %r" % (self.name(),))

        self._calendar.notifyChanged()


    def component(self):
        if self._component is not None:
            return self._component
        text = self.text()

        try:
            component = VComponent.fromString(text)
            # Fix any bogus data we can
            component.validateComponentsForCalDAV(False, fix=True)
        except InvalidICalendarDataError, e:
            raise InternalDataStoreError(
                "File corruption detected (%s) in file: %s"
                % (e, self._path.path)
            )
        return component


    def text(self):
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
            # Handle case of old wiki data written using \n instead of \r\n
            if (
                text.startswith("BEGIN:VCALENDAR\n") and
                text.endswith("\nEND:VCALENDAR\n")
            ):
                text = text.replace("\n", "\r\n")
            else:
                # Cannot deal with this data
                raise InternalDataStoreError(
                    "File corruption detected (improper start) in file: %s"
                    % (self._path.path,)
                )
        return text

    iCalendarText = text

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

    def _get_accessMode(self):
        return str(self.properties().get(PropertyName.fromElement(customxml.TwistedCalendarAccessProperty), ""))

    def _set_accessMode(self, value):
        pname = PropertyName.fromElement(customxml.TwistedCalendarAccessProperty)
        if value:
            self.properties()[pname] = customxml.TwistedCalendarAccessProperty(value)
        elif pname in self.properties():
            del self.properties()[pname]

    accessMode = property(_get_accessMode, _set_accessMode)

    def _get_isScheduleObject(self):
        return str(self.properties().get(PropertyName.fromElement(customxml.TwistedSchedulingObjectResource), "false")) == "true"

    def _set_isScheduleObject(self, value):
        pname = PropertyName.fromElement(customxml.TwistedSchedulingObjectResource)
        if value:
            self.properties()[pname] = customxml.TwistedSchedulingObjectResource.fromString("true" if value else "false")
        elif pname in self.properties():
            del self.properties()[pname]

    isScheduleObject = property(_get_isScheduleObject, _set_isScheduleObject)

    def _get_scheduleTag(self):
        return str(self.properties().get(PropertyName.fromElement(caldavxml.ScheduleTag), ""))

    def _set_scheduleTag(self, value):
        pname = PropertyName.fromElement(caldavxml.ScheduleTag)
        if value:
            self.properties()[pname] = caldavxml.ScheduleTag.fromString(value)
        elif pname in self.properties():
            del self.properties()[pname]

    scheduleTag = property(_get_scheduleTag, _set_scheduleTag)

    def _get_scheduleEtags(self):
        return tuple([str(etag) for etag in self.properties().get(PropertyName.fromElement(customxml.TwistedScheduleMatchETags), customxml.TwistedScheduleMatchETags()).children])

    def _set_scheduleEtags(self, value):
        if value:
            etags = [davxml.GETETag.fromString(etag) for etag in value]
            self.properties()[PropertyName.fromElement(customxml.TwistedScheduleMatchETags)] = customxml.TwistedScheduleMatchETags(*etags)
        else:
            try:
                del self.properties()[PropertyName.fromElement(customxml.TwistedScheduleMatchETags)]
            except KeyError:
                pass

    scheduleEtags = property(_get_scheduleEtags, _set_scheduleEtags)

    def _get_hasPrivateComment(self):
        return PropertyName.fromElement(customxml.TwistedCalendarHasPrivateCommentsProperty) in self.properties()

    def _set_hasPrivateComment(self, value):
        pname = PropertyName.fromElement(customxml.TwistedCalendarHasPrivateCommentsProperty)
        if value:
            self.properties()[pname] = customxml.TwistedCalendarHasPrivateCommentsProperty()
        elif pname in self.properties():
            del self.properties()[pname]

    hasPrivateComment = property(_get_hasPrivateComment, _set_hasPrivateComment)

    @inlineCallbacks
    def createAttachmentWithName(self, name, contentType):
        """
        Implement L{ICalendarObject.removeAttachmentWithName}.
        """
        # Make a (FIXME: temp, remember rollbacks) file in dropbox-land
        dropboxPath = yield self._dropboxPath()
        attachment = Attachment(self, name, dropboxPath)
        self._attachments[name] = attachment
        returnValue(attachment.store(contentType))


    @inlineCallbacks
    def removeAttachmentWithName(self, name):
        """
        Implement L{ICalendarObject.removeAttachmentWithName}.
        """
        # FIXME: rollback, tests for rollback

        attachment = (yield self.attachmentWithName(name))
        old_size = attachment.size()

        (yield self._dropboxPath()).child(name).remove()
        if name in self._attachments:
            del self._attachments[name]

        # Adjust quota
        self._calendar._home.adjustQuotaUsedBytes(-old_size)


    @inlineCallbacks
    def attachmentWithName(self, name):
        # Attachments can be local or remote, but right now we only care about
        # local.  So we're going to base this on the listing of files in the
        # dropbox and not on the calendar data.  However, we COULD examine the
        # 'attach' properties.

        if name in self._attachments:
            returnValue(self._attachments[name])
        # FIXME: cache consistently (put it in self._attachments)
        dbp = yield self._dropboxPath()
        if dbp.child(name).exists():
            returnValue(Attachment(self, name, dbp))
        else:
            # FIXME: test for non-existent attachment.
            returnValue(None)


    @inlineCallbacks
    def attendeesCanManageAttachments(self):
        returnValue((yield self.component()).hasPropertyInAnyComponent("X-APPLE-DROPBOX"))


    def dropboxID(self):
        # NB: Deferred
        return dropboxIDFromCalendarObject(self)


    @inlineCallbacks
    def _dropboxPath(self):
        dropboxPath = self._parentCollection._home._path.child(
            "dropbox"
        ).child((yield self.dropboxID()))
        if not dropboxPath.isdir():
            dropboxPath.makedirs()
        returnValue(dropboxPath)


    @inlineCallbacks
    def attachments(self):
        # See comment on attachmentWithName.
        dropboxPath = (yield self._dropboxPath())
        returnValue(
            [Attachment(self, name, dropboxPath)
            for name in dropboxPath.listdir()]
        )


    def initPropertyStore(self, props):
        # Setup peruser special properties
        props.setSpecialProperties(
            (
            ),
            (
                PropertyName.fromElement(customxml.TwistedCalendarAccessProperty),
                PropertyName.fromElement(customxml.TwistedSchedulingObjectResource),
                PropertyName.fromElement(caldavxml.ScheduleTag),
                PropertyName.fromElement(customxml.TwistedScheduleMatchETags),
                PropertyName.fromElement(customxml.TwistedCalendarHasPrivateCommentsProperty),
                PropertyName.fromElement(caldavxml.Originator),
                PropertyName.fromElement(caldavxml.Recipient),
                PropertyName.fromElement(customxml.ScheduleChanges),
            ),
        )



class AttachmentStorageTransport(object):

    implements(ITransport)

    def __init__(self, attachment, contentType):
        """
        Initialize this L{AttachmentStorageTransport} and open its file for
        writing.

        @param attachment: The attachment whose data is being filled out.
        @type attachment: L{Attachment}
        """
        self._attachment = attachment
        self._contentType = contentType
        self._file = self._attachment._path.open("w")


    def write(self, data):
        # FIXME: multiple chunks
        self._file.write(data)


    def loseConnection(self):
        
        old_size = self._attachment.size()

        # FIXME: do anything
        self._file.close()

        md5 = hashlib.md5(self._attachment._path.getContent()).hexdigest()
        props = self._attachment.properties()
        props[contentTypeKey] = GETContentType(generateContentType(self._contentType))
        props[md5key] = TwistedGETContentMD5.fromString(md5)

        # Adjust quota
        self._attachment._calendarObject._calendar._home.adjustQuotaUsedBytes(self._attachment.size() - old_size)
        props.flush()



class Attachment(FileMetaDataMixin):
    """
    An L{Attachment} is a container for the data associated with a I{locally-
    stored} calendar attachment.  That is to say, there will only be
    L{Attachment} objects present on the I{organizer's} copy of and event, and
    only for C{ATTACH} properties where this server is storing the resource.
    (For example, the organizer may specify an C{ATTACH} property that
    references an URI on a remote server.)
    """

    implements(IAttachment)

    def __init__(self, calendarObject, name, dropboxPath):
        self._calendarObject = calendarObject
        self._name = name
        self._dropboxPath = dropboxPath


    def name(self):
        return self._name


    def properties(self):
        uid = self._calendarObject._parentCollection._home.uid()
        return PropertyStore(uid, lambda :self._path)


    def store(self, contentType):
        return AttachmentStorageTransport(self, contentType)


    def retrieve(self, protocol):
        # FIXME: makeConnection
        # FIXME: actually stream
        # FIMXE: connectionLost
        protocol.dataReceived(self._path.getContent())
        # FIXME: ConnectionDone, not NotImplementedError
        protocol.connectionLost(Failure(NotImplementedError()))


    @property
    def _path(self):
        return self._dropboxPath.child(self.name())



class CalendarStubResource(CommonStubResource):
    """
    Just enough resource to keep the calendar's sql DB classes going.
    """

    def isCalendarCollection(self):
        return True


    def getChild(self, name):
        calendarObject = self.resource.calendarObjectWithName(name)
        if calendarObject:
            class ChildResource(object):
                def __init__(self, calendarObject):
                    self.calendarObject = calendarObject

                def iCalendar(self):
                    return self.calendarObject.component()

            return ChildResource(calendarObject)
        else:
            return None



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
