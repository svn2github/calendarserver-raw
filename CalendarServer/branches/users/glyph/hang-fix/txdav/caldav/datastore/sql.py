# -*- test-case-name: txdav.caldav.datastore.test.test_sql -*-
##
# Copyright (c) 2010-2013 Apple Inc. All rights reserved.
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
SQL backend for CalDAV storage.
"""

__all__ = [
    "CalendarHome",
    "Calendar",
    "CalendarObject",
]

from twext.enterprise.dal.record import fromTable
from twext.enterprise.dal.syntax import Delete
from twext.enterprise.dal.syntax import Insert
from twext.enterprise.dal.syntax import Len
from twext.enterprise.dal.syntax import Parameter
from twext.enterprise.dal.syntax import Select, Count, ColumnSyntax
from twext.enterprise.dal.syntax import Update
from twext.enterprise.dal.syntax import utcNowSQL
from twext.enterprise.locking import NamedLock
from twext.enterprise.queue import WorkItem
from twext.enterprise.util import parseSQLTimestamp
from twext.python.clsprop import classproperty
from twext.python.filepath import CachingFilePath
from twext.python.log import Logger
from twext.python.vcomponent import VComponent
from twext.web2.http_headers import MimeType, generateContentType
from twext.web2.stream import readStream

from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twisted.python import hashlib

from twistedcaldav import caldavxml, customxml
from twistedcaldav.config import config
from twistedcaldav.datafilters.peruserdata import PerUserDataFilter
from twistedcaldav.dateops import normalizeForIndex, datetimeMktime, \
    pyCalendarTodatetime, parseSQLDateToPyCalendar
from twistedcaldav.ical import Component, InvalidICalendarDataError, Property
from twistedcaldav.instance import InvalidOverriddenInstanceError
from twistedcaldav.memcacher import Memcacher

from txdav.base.propertystore.base import PropertyName
from txdav.caldav.datastore.scheduling.icalsplitter import iCalSplitter
from txdav.caldav.datastore.scheduling.implicit import ImplicitScheduler
from txdav.caldav.datastore.util import AttachmentRetrievalTransport, \
    normalizationLookup
from txdav.caldav.datastore.util import CalendarObjectBase
from txdav.caldav.datastore.util import StorageTransportBase
from txdav.caldav.datastore.util import dropboxIDFromCalendarObject
from txdav.caldav.icalendarstore import ICalendarHome, ICalendar, ICalendarObject, \
    IAttachment, AttachmentStoreFailed, AttachmentStoreValidManagedID, \
    AttachmentMigrationFailed, AttachmentDropboxNotAllowed, \
    TooManyAttendeesError, InvalidComponentTypeError, InvalidCalendarAccessError, \
    ResourceDeletedError, \
    AttendeeAllowedError, InvalidPerUserDataMerge, ComponentUpdateState, \
    ValidOrganizerError, ShareeAllowedError, ComponentRemoveState, \
    InvalidDefaultCalendar, \
    InvalidAttachmentOperation
from txdav.caldav.icalendarstore import QuotaExceeded
from txdav.common.datastore.sql import CommonHome, CommonHomeChild, \
    CommonObjectResource, ECALENDARTYPE
from txdav.common.datastore.sql_legacy import PostgresLegacyIndexEmulator, \
    PostgresLegacyInboxIndexEmulator
from txdav.common.datastore.sql_tables import _ATTACHMENTS_MODE_NONE, \
    _ATTACHMENTS_MODE_WRITE, schema, _BIND_MODE_OWN, \
    _ATTACHMENTS_MODE_READ, _TRANSP_OPAQUE, _TRANSP_TRANSPARENT
from txdav.common.icommondatastore import IndexedSearchException, \
    InternalDataStoreError, HomeChildNameAlreadyExistsError, \
    HomeChildNameNotAllowedError, ObjectResourceTooBigError, \
    InvalidObjectResourceError, ObjectResourceNameAlreadyExistsError, \
    ObjectResourceNameNotAllowedError, TooManyObjectResourcesError, \
    InvalidUIDError, UIDExistsError, UIDExistsElsewhereError, \
    InvalidResourceMove, InvalidComponentForStoreError

from pycalendar.datetime import PyCalendarDateTime
from pycalendar.duration import PyCalendarDuration
from pycalendar.timezone import PyCalendarTimezone
from pycalendar.value import PyCalendarValue

from zope.interface.declarations import implements

from urlparse import urlparse, urlunparse
import collections
import datetime
import os
import tempfile
import urllib
import uuid

log = Logger()

class CalendarStoreFeatures(object):
    """
    Manages store-wide operations specific to calendars.
    """

    def __init__(self, store):
        """
        @param store: the underlying store object to use.
        @type store: L{twext.common.datastore.sql.CommonDataStore}
        """
        self._store = store


    @inlineCallbacks
    def hasDropboxAttachments(self, txn):
        """
        Determine whether any dropbox attachments are present.

        @param txn: the transaction to run under
        @type txn: L{txdav.common.datastore.sql.CommonStoreTransaction}
        """

        at = schema.ATTACHMENT
        rows = (yield Select(
            (at.DROPBOX_ID,),
            From=at,
            Where=at.DROPBOX_ID != ".",
            Limit=1,
        ).on(txn))
        returnValue(len(rows) != 0)


    @inlineCallbacks
    def upgradeToManagedAttachments(self, batchSize=10):
        """
        Upgrade the calendar server from old-style dropbox attachments to the new
        managed attachments. This is a one-way, one-time migration step. This method
        creates its own transactions as needed (possibly multiple when batching).

        Things to do:

        1. For any CALENDAR_OBJECT rows with a DROPBOX_ID not matching an existing DROPBOX_ID
        in the ATTACHMENT table, null out CALENDAR_OBJECT.DROPBOX_ID. Do not rewrite calendar
        data to remove X-APPLE-DROPBOX.

        2. For each item in the ATTACHMENT table, convert into a managed attachment and re-write
        all calendar data referring to that attachment.

        TODO: parallelize this as much as possible as it will have to touch a lot of data.
        """

        txn = self._store.newTransaction("CalendarStoreFeatures.upgradeToManagedAttachments - preliminary work")
        try:
            # Clear out unused CALENDAR_OBJECT.DROPBOX_IDs
            co = schema.CALENDAR_OBJECT
            at = schema.ATTACHMENT
            yield Update(
                {co.DROPBOX_ID: None},
                Where=co.RESOURCE_ID.In(Select(
                    (co.RESOURCE_ID,),
                    From=co.join(at, co.DROPBOX_ID == at.DROPBOX_ID, "left outer"),
                    Where=(co.DROPBOX_ID != None).And(at.DROPBOX_ID == None),
                )),
            ).on(txn)

            # Count number to process so we can display progress
            rows = (yield Select(
                (at.DROPBOX_ID,),
                From=at,
                Where=at.DROPBOX_ID != ".",
                Distinct=True,
            ).on(txn))
            total = len(rows)
            count = 0
            log.warn("%d dropbox ids to migrate" % (total,))
        except RuntimeError, e:
            log.error("Dropbox migration failed when cleaning out dropbox ids: %s" % (e,))
            yield txn.abort()
            raise
        else:
            yield txn.commit()

        # For each remaining attachment
        rows = -1
        while rows:
            txn = self._store.newTransaction("CalendarStoreFeatures.upgradeToManagedAttachments - attachment loop count: %d" % (count,))
            try:
                dropbox_id = "Batched select"
                rows = (yield Select(
                    (at.DROPBOX_ID,),
                    From=at,
                    Where=at.DROPBOX_ID != ".",
                    Limit=batchSize,
                    Distinct=True,
                ).on(txn))
                if len(rows) > 0:
                    for dropbox_id in rows:
                        (yield self._upgradeDropbox(txn, dropbox_id))
                    count += len(rows)
                    log.warn("%d of %d dropbox ids migrated" % (count, total,))
            except RuntimeError, e:
                log.error("Dropbox migration failed for '%s': %s" % (dropbox_id, e,))
                yield txn.abort()
                raise
            else:
                yield txn.commit()


    @inlineCallbacks
    def _upgradeDropbox(self, txn, dropbox_id):
        """
        Upgrade attachments for the corresponding dropbox box to managed attachments. This is tricky
        in that we have to spot the case of a dropbox attachment being used by more than one event
        in the owner's home (e.g., the case of a recurrence split). We have to give each owned event
        its own managed attachment reference (though they point to the same actual attachment data).
        So we need to detect owned attachments and group by UID.

        @param dropbox_id: the dropbox id to upgrade
        @type dropbox_id: C{str}
        """

        log.debug("Processing dropbox id: %s" % (dropbox_id,))

        # Get all affected calendar objects
        cobjs = (yield self._loadCalendarObjectsForDropboxID(txn, dropbox_id))
        log.debug("  %d affected calendar objects" % (len(cobjs),))

        # Get names of each matching attachment
        at = schema.ATTACHMENT
        names = (yield Select(
            (at.PATH,),
            From=at,
            Where=at.DROPBOX_ID == dropbox_id,
        ).on(txn))
        log.debug("  %d associated attachment objects" % (len(names),))

        # For each attachment, update each calendar object
        for name in names:
            name = name[0]
            log.debug("  processing attachment object: %s" % (name,))
            attachment = (yield DropBoxAttachment.load(txn, dropbox_id, name))

            # Check for orphans
            if len(cobjs) == 0:
                # Just remove the attachment
                log.warn("Orphaned dropbox id removed: %s" % (attachment._path,))
                yield attachment.remove()
                continue

            # Find owner objects and group all by UID
            owners = []
            cobj_by_UID = collections.defaultdict(list)
            for cobj in cobjs:
                if cobj._parentCollection.ownerHome()._resourceID == attachment._ownerHomeID:
                    owners.append(cobj)
                cobj_by_UID[cobj.uid()].append(cobj)
            log.debug("    %d owner calendar objects" % (len(owners),))
            log.debug("    %d UIDs" % (len(cobj_by_UID),))
            log.debug("    %d total calendar objects" % (sum([len(items) for items in cobj_by_UID.values()]),))

            if owners:
                # Create the managed attachment without references to calendar objects.
                managed = (yield attachment.convertToManaged())
                log.debug("    converted attachment: %r" % (attachment,))

                # Do conversion for each owner object
                for owner_obj in owners:

                    # Add a reference to the managed attachment
                    mattachment = (yield managed.newReference(owner_obj._resourceID))
                    log.debug("    added reference for: %r" % (owner_obj,))

                    # Rewrite calendar data
                    for cobj in cobj_by_UID[owner_obj.uid()]:
                        (yield cobj.convertAttachments(attachment, mattachment))
                        log.debug("    re-wrote calendar object: %r" % (cobj,))
            else:
                # TODO: look for cobjs that were not changed and remove their ATTACH properties.
                # These could happen if the owner object no longer exists.
                # For now just remove the attachment
                log.warn("Unowned dropbox id removed: %s" % (attachment._path,))
                yield attachment.remove()
                continue

        log.debug("  finished dropbox id: %s" % (dropbox_id,))


    @inlineCallbacks
    def _loadCalendarObjectsForDropboxID(self, txn, dropbox_id):
        """
        Load all calendar objects (and associated calendars and homes) that match the
        specified dropbox id.

        @param dropbox_id: the dropbox id to match.
        @type dropbox_id: C{str}
        """

        co = schema.CALENDAR_OBJECT
        cb = schema.CALENDAR_BIND
        rows = (yield Select(
            (cb.CALENDAR_HOME_RESOURCE_ID, co.CALENDAR_RESOURCE_ID, co.RESOURCE_ID,),
            From=co.join(cb, co.CALENDAR_RESOURCE_ID == cb.CALENDAR_RESOURCE_ID),
            Where=(co.DROPBOX_ID == dropbox_id).And(cb.BIND_MODE == _BIND_MODE_OWN)
        ).on(txn))

        results = []
        for home_rid, calendar_rid, cobj_rid in rows:
            home = (yield txn.calendarHomeWithResourceID(home_rid))
            calendar = (yield home.childWithID(calendar_rid))
            cobj = (yield calendar.objectResourceWithID(cobj_rid))
            results.append(cobj)

        returnValue(results)


    @inlineCallbacks
    def calendarObjectsWithUID(self, txn, uid):
        """
        Return all child object resources with the specified UID. Only "owned" resources are returned,
        no shared resources.

        @param txn: transaction to use
        @type txn: L{CommonStoreTransaction}
        @param uid: UID to query
        @type uid: C{str}

        @return: list of matching L{CalendarObject}s
        @rtype: C{list}
        """

        # First find the resource-ids of the (home, parent, object) for each object matching the UID.
        obj = CalendarHome._objectSchema
        bind = CalendarHome._bindSchema
        rows = (yield Select(
            [bind.HOME_RESOURCE_ID, obj.PARENT_RESOURCE_ID, obj.RESOURCE_ID],
            From=obj.join(bind, obj.PARENT_RESOURCE_ID == bind.RESOURCE_ID),
            Where=(obj.UID == Parameter("uid")).And(bind.BIND_MODE == _BIND_MODE_OWN),
        ).on(txn, uid=uid))

        results = []
        for homeID, parentID, childID in rows:
            home = (yield txn.calendarHomeWithResourceID(homeID))
            parent = (yield home.childWithID(parentID))
            child = (yield parent.objectResourceWithID(childID))
            results.append(child)

        returnValue(results)


    @inlineCallbacks
    def calendarObjectWithID(self, txn, rid):
        """
        Return all child object resources with the specified UID. Only "owned" resources are returned,
        no shared resources.

        @param txn: transaction to use
        @type txn: L{CommonStoreTransaction}
        @param rid: resource-id to query
        @type rid: C{int}

        @return: matching calendar object, or None if not found
        @rtype: L{CalendarObject} or C{None}
        """

        # First find the resource-ids of the (home, parent, object) for each object matching the UID.
        obj = CalendarHome._objectSchema
        bind = CalendarHome._bindSchema
        rows = (yield Select(
            [bind.HOME_RESOURCE_ID, obj.PARENT_RESOURCE_ID, obj.RESOURCE_ID],
            From=obj.join(bind, obj.PARENT_RESOURCE_ID == bind.RESOURCE_ID),
            Where=(obj.RESOURCE_ID == Parameter("rid")).And(bind.BIND_MODE == _BIND_MODE_OWN),
        ).on(txn, rid=rid))

        results = []
        for homeID, parentID, childID in rows:
            home = (yield txn.calendarHomeWithResourceID(homeID))
            parent = (yield home.childWithID(parentID))
            child = (yield parent.objectResourceWithID(childID))
            results.append(child)

        returnValue(results[0] if results else None)



class CalendarHome(CommonHome):

    implements(ICalendarHome)

    # structured tables.  (new, preferred)
    _homeSchema = schema.CALENDAR_HOME
    _bindSchema = schema.CALENDAR_BIND
    _homeMetaDataSchema = schema.CALENDAR_HOME_METADATA
    _revisionsSchema = schema.CALENDAR_OBJECT_REVISIONS
    _objectSchema = schema.CALENDAR_OBJECT

    _notifierPrefix = "CalDAV"
    _dataVersionKey = "CALENDAR-DATAVERSION"

    _cacher = Memcacher("SQL.calhome", pickle=True, key_normalization=False)

    def __init__(self, transaction, ownerUID):

        self._childClass = Calendar
        super(CalendarHome, self).__init__(transaction, ownerUID)


    @classmethod
    def metadataColumns(cls):
        """
        Return a list of column name for retrieval of metadata. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """

        # Common behavior is to have created and modified

        return (
            cls._homeMetaDataSchema.DEFAULT_EVENTS,
            cls._homeMetaDataSchema.DEFAULT_TASKS,
            cls._homeMetaDataSchema.ALARM_VEVENT_TIMED,
            cls._homeMetaDataSchema.ALARM_VEVENT_ALLDAY,
            cls._homeMetaDataSchema.ALARM_VTODO_TIMED,
            cls._homeMetaDataSchema.ALARM_VTODO_ALLDAY,
            cls._homeMetaDataSchema.AVAILABILITY,
            cls._homeMetaDataSchema.CREATED,
            cls._homeMetaDataSchema.MODIFIED,
        )


    @classmethod
    def metadataAttributes(cls):
        """
        Return a list of attribute names for retrieval of metadata. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """

        # Common behavior is to have created and modified

        return (
            "_default_events",
            "_default_tasks",
            "_alarm_vevent_timed",
            "_alarm_vevent_allday",
            "_alarm_vtodo_timed",
            "_alarm_vtodo_allday",
            "_availability",
            "_created",
            "_modified",
        )

    createCalendarWithName = CommonHome.createChildWithName
    removeCalendarWithName = CommonHome.removeChildWithName
    calendarWithName = CommonHome.childWithName
    calendars = CommonHome.children
    listCalendars = CommonHome.listChildren
    loadCalendars = CommonHome.loadChildren

    @inlineCallbacks
    def remove(self):
        ch = schema.CALENDAR_HOME
        cb = schema.CALENDAR_BIND
        cor = schema.CALENDAR_OBJECT_REVISIONS
        rp = schema.RESOURCE_PROPERTY

        # delete attachments corresponding to this home, also removing from disk
        yield Attachment.removedHome(self._txn, self._resourceID)

        yield Delete(
            From=cb,
            Where=cb.CALENDAR_HOME_RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield Delete(
            From=cor,
            Where=cor.CALENDAR_HOME_RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield Delete(
            From=ch,
            Where=ch.RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield Delete(
            From=rp,
            Where=rp.RESOURCE_ID == self._resourceID
        ).on(self._txn)

        yield self._cacher.delete(str(self._ownerUID))


    @inlineCallbacks
    def hasCalendarResourceUIDSomewhereElse(self, uid, ok_object, mode):
        """
        Determine if this calendar home contains any calendar objects which
        would potentially conflict with the given UID for scheduling purposes.

        @param uid: The UID to search for.
        @type uid: C{str}

        @param ok_object: a calendar object with the given UID, that doesn't
            count as a potential conflict (since, for example, it is the one
            being updated).  May be C{None} if all objects potentially count.
        @type ok_object: L{CalendarObject} or C{NoneType}

        @param mode: a string, indicating the mode to check for conflicts.  If
            this is the string "schedule", then we are checking for potential
            conflicts with a new scheduled calendar object, which will conflict
            with any calendar object matching the given C{uid} in the home.
            Otherwise, (if this is the string "calendar") we are checking for
            conflicts with a new unscheduled calendar object, which will
            conflict only with other scheduled objects.
        @type mode: C{str}

        @return: a L{Deferred} which fires with C{True} if there is a conflict
            and C{False} if not.
        """
        # FIXME: this should be documented on the interface; it should also
        # refer to calendar *object* UIDs, since calendar *resources* are an
        # HTTP protocol layer thing, not a data store thing.  (See also
        # objectResourcesWithUID.)
        objectResources = (
            yield self.objectResourcesWithUID(uid, ["inbox"], False)
        )
        for objectResource in objectResources:
            if ok_object and objectResource._resourceID == ok_object._resourceID:
                continue
            matched_mode = ("schedule" if objectResource.isScheduleObject else "calendar")
            if mode == "schedule" or matched_mode == "schedule":
                returnValue(objectResource)

        returnValue(None)


    @inlineCallbacks
    def getCalendarResourcesForUID(self, uid, allow_shared=False):

        results = []
        objectResources = (yield self.objectResourcesWithUID(uid, ["inbox"]))
        for objectResource in objectResources:
            if allow_shared or objectResource._parentCollection.owned():
                results.append(objectResource)

        returnValue(results)


    @inlineCallbacks
    def calendarObjectWithDropboxID(self, dropboxID):
        """
        Implement lookup via queries.
        """
        co = schema.CALENDAR_OBJECT
        cb = schema.CALENDAR_BIND
        rows = (yield Select(
            [co.PARENT_RESOURCE_ID,
             co.RESOURCE_ID],
            From=co.join(cb, co.PARENT_RESOURCE_ID == cb.RESOURCE_ID,
                         'left outer'),
            Where=(co.DROPBOX_ID == dropboxID).And(
                cb.HOME_RESOURCE_ID == self._resourceID)
        ).on(self._txn))

        if rows:
            calendarID, objectID = rows[0]
            calendar = (yield self.childWithID(calendarID))
            if calendar:
                calendarObject = (yield calendar.objectResourceWithID(objectID))
                returnValue(calendarObject)
        returnValue(None)


    @inlineCallbacks
    def getAllDropboxIDs(self):
        co = schema.CALENDAR_OBJECT
        cb = schema.CALENDAR_BIND
        rows = (yield Select(
            [co.DROPBOX_ID],
            From=co.join(cb, co.PARENT_RESOURCE_ID == cb.RESOURCE_ID),
            Where=(co.DROPBOX_ID != None).And(
                cb.HOME_RESOURCE_ID == self._resourceID),
            OrderBy=co.DROPBOX_ID
        ).on(self._txn))
        returnValue([row[0] for row in rows])


    @inlineCallbacks
    def getAllAttachmentNames(self):
        att = schema.ATTACHMENT
        rows = (yield Select(
            [att.DROPBOX_ID],
            From=att,
            Where=(att.CALENDAR_HOME_RESOURCE_ID == self._resourceID),
            OrderBy=att.DROPBOX_ID
        ).on(self._txn))
        returnValue([row[0] for row in rows])


    @inlineCallbacks
    def getAllManagedIDs(self):
        at = schema.ATTACHMENT
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        rows = (yield Select(
            [attco.MANAGED_ID, ],
            From=attco.join(at, attco.ATTACHMENT_ID == at.ATTACHMENT_ID),
            Where=at.CALENDAR_HOME_RESOURCE_ID == self._resourceID,
            OrderBy=attco.MANAGED_ID
        ).on(self._txn))
        returnValue([row[0] for row in rows])


    @inlineCallbacks
    def createdHome(self):

        # Default calendar
        defaultCal = yield self.createCalendarWithName("calendar")

        # Check whether components type must be separate
        if config.RestrictCalendarsToOneComponentType:
            yield defaultCal.setSupportedComponents("VEVENT")
            yield self.setDefaultCalendar(defaultCal, False)

            # Default tasks
            defaultTasks = yield self.createCalendarWithName("tasks")
            yield defaultTasks.setSupportedComponents("VTODO")
            yield defaultTasks.setUsedForFreeBusy(False)
            yield self.setDefaultCalendar(defaultTasks, True)
        else:
            yield self.setDefaultCalendar(defaultCal, False)
            yield self.setDefaultCalendar(defaultCal, True)

        inbox = yield self.createCalendarWithName("inbox")
        yield inbox.setUsedForFreeBusy(False)


    @inlineCallbacks
    def splitCalendars(self):
        """
        Split all regular calendars by component type
        """

        # Make sure the loop does not operate on any new calendars created during the loop
        self.log.warn("Splitting calendars for user %s" % (self._ownerUID,))
        calendars = yield self.calendars()
        for calendar in calendars:

            # Ignore inbox - also shared calendars are not part of .calendars()
            if calendar.isInbox():
                continue
            split_count = yield calendar.splitCollectionByComponentTypes()
            self.log.warn("  Calendar: '%s', split into %d" % (calendar.name(), split_count + 1,))

        yield self.ensureDefaultCalendarsExist()


    @inlineCallbacks
    def ensureDefaultCalendarsExist(self):
        """
        Double check that we have calendars supporting at least VEVENT and VTODO,
        and create if missing.
        """

        # Double check that we have calendars supporting at least VEVENT and VTODO
        if config.RestrictCalendarsToOneComponentType:
            supported_components = set()
            names = set()
            calendars = yield self.calendars()
            for calendar in calendars:
                if calendar.isInbox():
                    continue
                names.add(calendar.name())
                result = yield calendar.getSupportedComponents()
                supported_components.update(result.split(","))

            @inlineCallbacks
            def _requireCalendarWithType(support_component, tryname):
                if support_component not in supported_components:
                    newname = tryname
                    if newname in names:
                        newname = str(uuid.uuid4())
                    newcal = yield self.createCalendarWithName(newname)
                    yield newcal.setSupportedComponents(support_component)

            yield _requireCalendarWithType("VEVENT", "calendar")
            yield _requireCalendarWithType("VTODO", "tasks")


    @inlineCallbacks
    def pickNewDefaultCalendar(self, tasks=False):
        """
        First see if default provisioned calendar exists in the calendar home and pick that. Otherwise
        pick another from the calendar home.
        """

        componentType = "VTODO" if tasks else "VEVENT"
        test_name = "tasks" if tasks else "calendar"

        defaultCalendar = (yield self.calendarWithName(test_name))
        if defaultCalendar is None or not defaultCalendar.owned():

            @inlineCallbacks
            def _findDefault():
                for calendarName in (yield self.listCalendars()):
                    calendar = (yield self.calendarWithName(calendarName))
                    if calendar.isInbox():
                        continue
                    if not calendar.owned():
                        continue
                    if not calendar.isSupportedComponent(componentType):
                        continue
                    break
                else:
                    calendar = None
                returnValue(calendar)

            defaultCalendar = yield _findDefault()
            if defaultCalendar is None:
                # Create a default and try and get its name again
                yield self.ensureDefaultCalendarsExist()
                defaultCalendar = yield _findDefault()
                if defaultCalendar is None:
                    # Failed to even create a default - bad news...
                    raise RuntimeError("No valid calendars to use as a default %s calendar." % (componentType,))

        yield self.setDefaultCalendar(defaultCalendar, tasks)

        returnValue(defaultCalendar)


    @inlineCallbacks
    def setDefaultCalendar(self, calendar, tasks=False):
        """
        Set the default calendar for a particular type of component.

        @param calendar: the calendar being set as the default
        @type calendar: L{CalendarObject}
        @param tasks: C{True} for VTODO, C{False} for VEVENT
        @type componentType: C{bool}
        """
        chm = self._homeMetaDataSchema
        componentType = "VTODO" if tasks else "VEVENT"
        attribute_to_test = "_default_tasks" if tasks else "_default_events"
        column_to_set = chm.DEFAULT_TASKS if tasks else chm.DEFAULT_EVENTS

        # Check validity of the default
        if calendar.isInbox():
            raise InvalidDefaultCalendar("Cannot set inbox as a default calendar")
        elif not calendar.owned():
            raise InvalidDefaultCalendar("Cannot set shared calendar as a default calendar")
        elif not calendar.isSupportedComponent(componentType):
            raise InvalidDefaultCalendar("Cannot set default calendar to unsupported component type")
        elif calendar.ownerHome().uid() != self.uid():
            raise InvalidDefaultCalendar("Cannot set default calendar to someone else's calendar")

        setattr(self, attribute_to_test, calendar._resourceID)
        yield Update(
            {column_to_set: calendar._resourceID},
            Where=chm.RESOURCE_ID == self._resourceID,
        ).on(self._txn)
        yield self.invalidateQueryCache()
        yield self.notifyChanged()

        # CalDAV stores the default calendar properties on the inbox so we also need to send a changed notification on that
        inbox = (yield self.calendarWithName("inbox"))
        if inbox is not None:
            yield inbox.notifyPropertyChanged()


    @inlineCallbacks
    def defaultCalendar(self, componentType, create=True):
        """
        Find the default calendar for the supplied iCalendar component type. If one does
        not exist, automatically provision it.

        @param componentType: the name of the iCalendar component for the default, i.e. "VEVENT" or "VTODO"
        @type componentType: C{str}
        @param create: if C{True} and no default is found, create a calendar and make it the default, if C{False}
            and there is no default, then return C{None}
        @type create: C{bool}

        @return: the default calendar or C{None} if none found and creation not requested
        @rtype: L{Calendar} or C{None}
        """

        # Check any default calendar property first - this will create if none exists
        attribute_to_test = "_default_tasks" if componentType == "VTODO" else "_default_events"
        defaultID = getattr(self, attribute_to_test)
        if defaultID:
            default = (yield self.childWithID(defaultID))
        else:
            default = None

        # Check that default meets requirements
        if default is not None:
            if default.isInbox():
                default = None
            elif not default.owned():
                default = None
            elif not default.isSupportedComponent(componentType):
                default = None

        # Must have a default - provision one if not
        if default is None:

            # Try to find a calendar supporting the required component type. If there are multiple, pick
            # the one with the oldest created timestamp as that will likely be the initial provision.
            for calendarName in (yield self.listCalendars()):
                calendar = (yield self.calendarWithName(calendarName))
                if calendar.isInbox():
                    continue
                elif not calendar.owned():
                    continue
                elif not calendar.isSupportedComponent(componentType):
                    continue
                if default is None or calendar.created() < default.created():
                    default = calendar

            # If none can be found, provision one
            if default is None:
                if not create:
                    returnValue(None)
                else:
                    new_name = "%ss" % (componentType.lower()[1:],)
                    default = yield self.createCalendarWithName(new_name)
                    yield default.setSupportedComponents(componentType.upper())

            # Update the metadata
            yield self.setDefaultCalendar(default, componentType == "VTODO")

        returnValue(default)


    def isDefaultCalendar(self, calendar):
        """
        Is the supplied calendar one of the possible default calendars.
        """
        # Not allowed to delete the default calendar
        return calendar._resourceID in (self._default_events, self._default_tasks)

    ALARM_DETAILS = {
        (True, True): (_homeMetaDataSchema.ALARM_VEVENT_TIMED, "_alarm_vevent_timed"),
        (True, False): (_homeMetaDataSchema.ALARM_VEVENT_ALLDAY, "_alarm_vevent_allday"),
        (False, True): (_homeMetaDataSchema.ALARM_VTODO_TIMED, "_alarm_vtodo_timed"),
        (False, False): (_homeMetaDataSchema.ALARM_VTODO_ALLDAY, "_alarm_vtodo_allday"),
    }

    def getDefaultAlarm(self, vevent, timed):
        """
        Return the default alarm (text) for the specified alarm type.

        @param vevent: used for a vevent (C{True}) or vtodo (C{False})
        @type vevent: C{bool}
        @param timed: timed ({C{True}) or all-day ({C{False})
        @type timed: C{bool}

        @return: the alarm (text)
        @rtype: C{str}
        """

        return getattr(self, self.ALARM_DETAILS[(vevent, timed)][1])


    @inlineCallbacks
    def setDefaultAlarm(self, alarm, vevent, timed):
        """
        Set default alarm of the specified type.

        @param alarm: the alarm text
        @type alarm: C{str}
        @param vevent: used for a vevent (C{True}) or vtodo (C{False})
        @type vevent: C{bool}
        @param timed: timed ({C{True}) or all-day ({C{False})
        @type timed: C{bool}
        """

        colname, attr_alarm = self.ALARM_DETAILS[(vevent, timed)]

        setattr(self, attr_alarm, alarm)

        chm = self._homeMetaDataSchema
        yield Update(
            {colname: alarm},
            Where=chm.RESOURCE_ID == self._resourceID,
        ).on(self._txn)
        yield self.invalidateQueryCache()
        yield self.notifyChanged()


    def getAvailability(self):
        """
        Return the VAVAILABILITY data.

        @return: the component (text)
        @rtype: L{Component}
        """

        return Component.fromString(self._availability) if self._availability else None


    @inlineCallbacks
    def setAvailability(self, availability):
        """
        Set VAVAILABILITY data.

        @param availability: the component
        @type availability: L{Component}
        """

        self._availability = str(availability) if availability else None

        chm = self._homeMetaDataSchema
        yield Update(
            {chm.AVAILABILITY: self._availability},
            Where=chm.RESOURCE_ID == self._resourceID,
        ).on(self._txn)
        yield self.invalidateQueryCache()
        yield self.notifyChanged()

        # CalDAV stores the availability properties on the inbox so we also need to send a changed notification on that
        inbox = (yield self.calendarWithName("inbox"))
        if inbox is not None:
            yield inbox.notifyPropertyChanged()


CalendarHome._register(ECALENDARTYPE)



class Calendar(CommonHomeChild):
    """
    SQL-based implementation of L{ICalendar}.
    """
    implements(ICalendar)

    # structured tables.  (new, preferred)
    _homeSchema = schema.CALENDAR_HOME
    _bindSchema = schema.CALENDAR_BIND
    _homeChildSchema = schema.CALENDAR
    _homeChildMetaDataSchema = schema.CALENDAR_METADATA
    _revisionsSchema = schema.CALENDAR_OBJECT_REVISIONS
    _objectSchema = schema.CALENDAR_OBJECT
    _timeRangeSchema = schema.TIME_RANGE

    _supportedComponents = None

    def __init__(self, *args, **kw):
        """
        Initialize a calendar pointing at a record in a database.
        """
        super(Calendar, self).__init__(*args, **kw)
        if self.isInbox():
            self._index = PostgresLegacyInboxIndexEmulator(self)
        else:
            self._index = PostgresLegacyIndexEmulator(self)
        self._transp = _TRANSP_OPAQUE


    @classmethod
    def metadataColumns(cls):
        """
        Return a list of column name for retrieval of metadata. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """

        # Common behavior is to have created and modified

        return (
            cls._homeChildMetaDataSchema.SUPPORTED_COMPONENTS,
            cls._homeChildMetaDataSchema.CREATED,
            cls._homeChildMetaDataSchema.MODIFIED,
        )


    @classmethod
    def metadataAttributes(cls):
        """
        Return a list of attribute names for retrieval of metadata. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """

        # Common behavior is to have created and modified

        return (
            "_supportedComponents",
            "_created",
            "_modified",
        )


    @classmethod
    def additionalBindColumns(cls):
        """
        Return a list of column names for retrieval during creation. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """

        return (
            cls._bindSchema.TRANSP,
            cls._bindSchema.ALARM_VEVENT_TIMED,
            cls._bindSchema.ALARM_VEVENT_ALLDAY,
            cls._bindSchema.ALARM_VTODO_TIMED,
            cls._bindSchema.ALARM_VTODO_ALLDAY,
            cls._bindSchema.TIMEZONE,
        )


    @classmethod
    def additionalBindAttributes(cls):
        """
        Return a list of attribute names for retrieval of during creation. This allows
        different child classes to have their own type specific data, but still make use of the
        common base logic.
        """

        return (
            "_transp",
            "_alarm_vevent_timed",
            "_alarm_vevent_allday",
            "_alarm_vtodo_timed",
            "_alarm_vtodo_allday",
            "_timezone",
        )


    @property
    def _calendarHome(self):
        return self._home

    ownerCalendarHome = CommonHomeChild.ownerHome
    viewerCalendarHome = CommonHomeChild.viewerHome
    calendarObjects = CommonHomeChild.objectResources
    listCalendarObjects = CommonHomeChild.listObjectResources
    calendarObjectWithName = CommonHomeChild.objectResourceWithName
    calendarObjectWithUID = CommonHomeChild.objectResourceWithUID
    createCalendarObjectWithName = CommonHomeChild.createObjectResourceWithName
    calendarObjectsSinceToken = CommonHomeChild.objectResourcesSinceToken


    @inlineCallbacks
    def _createCalendarObjectWithNameInternal(self, name, component, internal_state, options=None):

        # Create => a new resource name
        if name in self._objects and self._objects[name]:
            raise ObjectResourceNameAlreadyExistsError()

        # Apply check to the size of the collection
        if config.MaxResourcesPerCollection:
            child_count = (yield self.countObjectResources())
            if child_count >= config.MaxResourcesPerCollection:
                raise TooManyObjectResourcesError()

        objectResource = (
            yield self._objectResourceClass._createInternal(self, name, component, internal_state, options)
        )
        self._objects[objectResource.name()] = objectResource
        self._objects[objectResource.uid()] = objectResource

        # Note: create triggers a notification when the component is set, so we
        # don't need to call notify() here like we do for object removal.
        returnValue(objectResource)


    def calendarObjectsInTimeRange(self, start, end, timeZone):
        raise NotImplementedError()


    def objectResourcesHaveProperties(self):
        """
        inbox resources need to store Originator, Recipient etc properties.
        Other calendars do not have object resources with properties.
        """
        return self.isInbox()


    def isSupportedComponent(self, componentType):
        if self._supportedComponents:
            return componentType.upper() in self._supportedComponents.split(",")
        else:
            return True


    def getSupportedComponents(self):
        return self._supportedComponents


    @inlineCallbacks
    def setSupportedComponents(self, supported_components):
        """
        Update the database column with the supported components. Technically this should only happen once
        on collection creation, but for migration we may need to change after the fact - hence a separate api.
        """

        cal = self._homeChildMetaDataSchema
        yield Update(
            {
                cal.SUPPORTED_COMPONENTS : supported_components
            },
            Where=(cal.RESOURCE_ID == self._resourceID)
        ).on(self._txn)
        self._supportedComponents = supported_components
        yield self.invalidateQueryCache()
        yield self.notifyPropertyChanged()


    def getTimezone(self):
        """
        Return the VTIMEZONE data.

        @return: the component (text)
        @rtype: L{Component}
        """

        return Component.fromString(self._timezone) if self._timezone else None


    @inlineCallbacks
    def setTimezone(self, timezone):
        """
        Set VTIMEZONE data.

        @param timezone: the component
        @type timezone: L{Component}
        """

        self._timezone = str(timezone) if timezone else None

        cal = self._bindSchema
        yield Update(
            {
                cal.TIMEZONE : self._timezone
            },
            Where=(cal.CALENDAR_HOME_RESOURCE_ID == self.viewerHome()._resourceID).And(cal.CALENDAR_RESOURCE_ID == self._resourceID)
        ).on(self._txn)
        yield self.invalidateQueryCache()
        yield self.notifyPropertyChanged()

    ALARM_DETAILS = {
        (True, True): (_bindSchema.ALARM_VEVENT_TIMED, "_alarm_vevent_timed"),
        (True, False): (_bindSchema.ALARM_VEVENT_ALLDAY, "_alarm_vevent_allday"),
        (False, True): (_bindSchema.ALARM_VTODO_TIMED, "_alarm_vtodo_timed"),
        (False, False): (_bindSchema.ALARM_VTODO_ALLDAY, "_alarm_vtodo_allday"),
    }

    def getDefaultAlarm(self, vevent, timed):
        """
        Return the default alarm (text) for the specified alarm type.

        @param vevent: used for a vevent (C{True}) or vtodo (C{False})
        @type vevent: C{bool}
        @param timed: timed ({C{True}) or all-day ({C{False})
        @type timed: C{bool}
        @return: the alarm (text)
        @rtype: C{str}
        """

        return getattr(self, self.ALARM_DETAILS[(vevent, timed)][1])


    @inlineCallbacks
    def setDefaultAlarm(self, alarm, vevent, timed):
        """
        Set default alarm of the specified type.

        @param alarm: the alarm text
        @type alarm: C{str}
        @param vevent: used for a vevent (C{True}) or vtodo (C{False})
        @type vevent: C{bool}
        @param timed: timed ({C{True}) or all-day ({C{False})
        @type timed: C{bool}
        """

        colname, attr_alarm = self.ALARM_DETAILS[(vevent, timed)]

        setattr(self, attr_alarm, alarm)

        cal = self._bindSchema
        yield Update(
            {colname: alarm},
            Where=(cal.CALENDAR_HOME_RESOURCE_ID == self.viewerHome()._resourceID).And(cal.CALENDAR_RESOURCE_ID == self._resourceID)
        ).on(self._txn)
        yield self.invalidateQueryCache()
        yield self.notifyPropertyChanged()


    def isInbox(self):
        """
        Indicates whether this calendar is an "inbox".

        @return: C{True} if it is an "inbox, C{False} otherwise
        @rtype: C{bool}
        """
        return self.name() == "inbox"


    def isUsedForFreeBusy(self):
        """
        Indicates whether the contents of this calendar contributes to free busy.

        @return: C{True} if it does, C{False} otherwise
        @rtype: C{bool}
        """
        return self._transp == _TRANSP_OPAQUE


    @inlineCallbacks
    def setUsedForFreeBusy(self, use_it):
        """
        Mark this calendar as being used for free busy request. Note this is a per-user setting so we are setting
        this on the bind table entry which is related to the user viewing the calendar.

        @param use_it: C{True} if used for free busy, C{False} otherwise
        @type use_it: C{bool}
        """

        self._transp = _TRANSP_OPAQUE if use_it else _TRANSP_TRANSPARENT
        cal = self._bindSchema
        yield Update(
            {cal.TRANSP : self._transp},
            Where=(cal.CALENDAR_HOME_RESOURCE_ID == self.viewerHome()._resourceID).And(cal.CALENDAR_RESOURCE_ID == self._resourceID)
        ).on(self._txn)
        yield self.invalidateQueryCache()
        yield self.notifyPropertyChanged()


    def initPropertyStore(self, props):
        # Setup peruser special properties
        props.setSpecialProperties(
            # Shadowable
            (
                PropertyName.fromElement(caldavxml.CalendarDescription),
                PropertyName.fromElement(caldavxml.CalendarTimeZone),
            ),

            # Global
            (
                PropertyName.fromElement(customxml.GETCTag),
                PropertyName.fromElement(caldavxml.SupportedCalendarComponentSet),
            ),
        )


    # FIXME: this is DAV-ish.  Data store calendar objects don't have
    # mime types.  -wsv
    def contentType(self):
        """
        The content type of Calendar objects is text/calendar.
        """
        return MimeType.fromString("text/calendar; charset=utf-8")


    @inlineCallbacks
    def splitCollectionByComponentTypes(self):
        """
        If the calendar contains iCalendar data with different component types, then split it into separate collections
        each containing only one component type. When doing this make sure properties and sharing state are preserved
        on any new calendars created. Also restrict the new calendars to only the one appropriate component type. Return
        the number of splits done.
        """

        # First see how many different component types there are
        split_count = 0
        components = yield self._countComponentTypes()
        if len(components) <= 1:

            # Restrict calendar to single component type
            component = components[0][0] if components else "VEVENT"
            yield self.setSupportedComponents(component.upper())

            returnValue(split_count)

        # We will leave the component type with the highest count in the current calendar and create new calendars
        # for the others which will be moved over
        maxComponent = max(components, key=lambda x: x[1])[0]

        for component, _ignore_count in components:
            if component == maxComponent:
                continue
            split_count += 1
            yield self._splitComponentType(component)

        # Restrict calendar to single component type
        yield self.setSupportedComponents(maxComponent.upper())

        returnValue(split_count)


    @inlineCallbacks
    def _countComponentTypes(self):
        """
        Count each component type in this calendar.

        @return: a C{tuple} of C{tuple} containing the component type name and count.
        """

        ob = self._objectSchema
        _componentsQuery = Select(
            [ob.ICALENDAR_TYPE, Count(ob.ICALENDAR_TYPE)],
            From=ob,
            Where=ob.CALENDAR_RESOURCE_ID == Parameter('calID'),
            GroupBy=ob.ICALENDAR_TYPE
        )

        rows = yield _componentsQuery.on(self._txn, calID=self._resourceID)
        result = tuple([(componentType, componentCount) for componentType, componentCount in sorted(rows, key=lambda x:x[0])])
        returnValue(result)


    @inlineCallbacks
    def _splitComponentType(self, component):
        """
        Create a new calendar and move all components of the specified component type into the new one.
        Make sure properties and sharing state is preserved on the new calendar.

        @param component: Component type to split out
        @type component: C{str}
        """

        # Create the new calendar
        try:
            newcalendar = yield self._home.createCalendarWithName("%s-%s" % (self._name, component.lower(),))
        except HomeChildNameAlreadyExistsError:
            # If the name we want exists, try repeating with up to ten more
            for ctr in range(10):
                try:
                    newcalendar = yield self._home.createCalendarWithName("%s-%s-%d" % (self._name, component.lower(), ctr + 1,))
                except HomeChildNameAlreadyExistsError:
                    continue
            else:
                # At this point we are stuck
                raise HomeChildNameNotAllowedError

        # Restrict calendar to single component type
        yield newcalendar.setSupportedComponents(component.upper())

        # Transfer properties over
        yield newcalendar._properties.copyAllProperties(self._properties)

        # Transfer sharing
        yield self._transferSharingDetails(newcalendar, component)

        # Now move calendar data over
        yield self._transferCalendarObjects(newcalendar, component)


    @inlineCallbacks
    def _transferSharingDetails(self, newcalendar, component):
        """
        If the current calendar is shared, make the new calendar shared in the same way, but tweak the name.
        """

        cb = self._bindSchema
        columns = [ColumnSyntax(item) for item in self._bindSchema.model.columns]
        _bindQuery = Select(
            columns,
            From=cb,
            Where=(cb.CALENDAR_RESOURCE_ID == Parameter('calID')).And(
                cb.CALENDAR_HOME_RESOURCE_ID != Parameter('homeID'))
        )

        rows = yield _bindQuery.on(
            self._txn,
            calID=self._resourceID,
            homeID=self._home._resourceID,
        )

        if len(rows) == 0:
            returnValue(None)

        for row in rows:
            columnMap = dict(zip(columns, row))
            columnMap[cb.CALENDAR_RESOURCE_ID] = newcalendar._resourceID
            columnMap[cb.CALENDAR_RESOURCE_NAME] = "%s-%s" % (columnMap[cb.CALENDAR_RESOURCE_NAME], component.lower(),)
            yield Insert(columnMap).on(self._txn)


    @inlineCallbacks
    def _transferCalendarObjects(self, newcalendar, component):
        """
        Move all calendar components of the specified type to the specified calendar.
        """

        # Find resource-ids for all matching components
        ob = self._objectSchema
        _componentsQuery = Select(
            [ob.RESOURCE_ID],
            From=ob,
            Where=(ob.CALENDAR_RESOURCE_ID == Parameter('calID')).And(
                ob.ICALENDAR_TYPE == Parameter('componentType'))
        )

        rows = yield _componentsQuery.on(
            self._txn,
            calID=self._resourceID,
            componentType=component,
        )

        if len(rows) == 0:
            returnValue(None)

        for row in rows:
            resourceID = row[0]
            child = yield self.objectResourceWithID(resourceID)
            yield self.moveObjectResource(child, newcalendar)


    @classproperty
    def _moveTimeRangeUpdateQuery(cls):  # @NoSelf
        """
        DAL query to update a child to be in a new parent.
        """
        tr = cls._timeRangeSchema
        return Update(
            {tr.CALENDAR_RESOURCE_ID: Parameter("newParentID")},
            Where=tr.CALENDAR_OBJECT_RESOURCE_ID == Parameter("resourceID")
        )


    @inlineCallbacks
    def _movedObjectResource(self, child, newparent):
        """
        Make sure time range entries have the new parent resource id.
        """
        yield self._moveTimeRangeUpdateQuery.on(
            self._txn,
            newParentID=newparent._resourceID,
            resourceID=child._resourceID
        )


icalfbtype_to_indexfbtype = {
    "UNKNOWN"         : 0,
    "FREE"            : 1,
    "BUSY"            : 2,
    "BUSY-UNAVAILABLE": 3,
    "BUSY-TENTATIVE"  : 4,
}

indexfbtype_to_icalfbtype = {
    0: '?',
    1: 'F',
    2: 'B',
    3: 'U',
    4: 'T',
}

accessMode_to_type = {
    ""                           : 0,
    Component.ACCESS_PUBLIC      : 1,
    Component.ACCESS_PRIVATE     : 2,
    Component.ACCESS_CONFIDENTIAL: 3,
    Component.ACCESS_RESTRICTED  : 4,
}
accesstype_to_accessMode = dict([(v, k) for k, v in accessMode_to_type.items()])

def _pathToName(path):
    return path.rsplit(".", 1)[0]



class CalendarObject(CommonObjectResource, CalendarObjectBase):
    implements(ICalendarObject)

    _objectSchema = schema.CALENDAR_OBJECT

    def __init__(self, calendar, name, uid, resourceID=None, options=None):

        super(CalendarObject, self).__init__(calendar, name, uid, resourceID)

        if options is None:
            options = {}
        self.accessMode = options.get("accessMode", "")
        self.isScheduleObject = options.get("isScheduleObject", False)
        self.scheduleTag = options.get("scheduleTag", "")
        self.scheduleEtags = options.get("scheduleEtags", "")
        self.hasPrivateComment = options.get("hasPrivateComment", False)
        self._dropboxID = None

        # Component caching
        self._cachedComponent = None
        self._cachedCommponentPerUser = {}

    _allColumns = [
        _objectSchema.RESOURCE_ID,
        _objectSchema.RESOURCE_NAME,
        _objectSchema.UID,
        _objectSchema.MD5,
        Len(_objectSchema.TEXT),
        _objectSchema.ATTACHMENTS_MODE,
        _objectSchema.DROPBOX_ID,
        _objectSchema.ACCESS,
        _objectSchema.SCHEDULE_OBJECT,
        _objectSchema.SCHEDULE_TAG,
        _objectSchema.SCHEDULE_ETAGS,
        _objectSchema.PRIVATE_COMMENTS,
        _objectSchema.CREATED,
        _objectSchema.MODIFIED
    ]


    @classmethod
    @inlineCallbacks
    def _createInternal(cls, parent, name, component, internal_state, options=None):

        child = (yield cls.objectWithName(parent, name, None))
        if child:
            raise ObjectResourceNameAlreadyExistsError(name)

        if name.startswith("."):
            raise ObjectResourceNameNotAllowedError(name)

        objectResource = cls(parent, name, None, None, options=options)
        yield objectResource._setComponentInternal(component, inserting=True, internal_state=internal_state)
        yield objectResource._loadPropertyStore(created=True)

        # Note: setComponent triggers a notification, so we don't need to
        # call notify( ) here like we do for object removal.

        returnValue(objectResource)


    def _initFromRow(self, row):
        """
        Given a select result using the columns from L{_allColumns}, initialize
        the calendar object resource state.
        """
        (self._resourceID,
         self._name,
         self._uid,
         self._md5,
         self._size,
         self._attachment,
         self._dropboxID,
         self._access,
         self._schedule_object,
         self._schedule_tag,
         self._schedule_etags,
         self._private_comments,
         self._created,
         self._modified,) = tuple(row)


    @property
    def _calendar(self):
        return self._parentCollection


    def calendar(self):
        return self._calendar


    # Stuff from put_common
    @inlineCallbacks
    def fullValidation(self, component, inserting, internal_state):
        """
        Do full validation of source and destination calendar data.
        """

        # Basic validation

        # Do validation on external requests
        if internal_state == ComponentUpdateState.NORMAL:

            # Valid data sizes
            if config.MaxResourceSize:
                calsize = len(str(component))
                if calsize > config.MaxResourceSize:
                    raise ObjectResourceTooBigError()

        # Possible timezone stripping
        if config.EnableTimezonesByReference:
            component.stripKnownTimezones()

        # Do validation on external requests
        if internal_state == ComponentUpdateState.NORMAL:

            # Valid calendar data checks
            self.validCalendarDataCheck(component, inserting)

            # Valid calendar component for check
            if not self.calendar().isSupportedComponent(component.mainType()):
                raise InvalidComponentTypeError("Invalid component type %s for calendar: %s" % (component.mainType(), self.calendar(),))

            # Valid attendee list size check
            yield self.validAttendeeListSizeCheck(component, inserting)

            # Normalize the calendar user addresses once we know we have valid
            # calendar data
            component.normalizeCalendarUserAddresses(normalizationLookup, self.directoryService().recordWithCalendarUserAddress)

        # Check location/resource organizer requirement
        self.validLocationResourceOrganizer(component, inserting, internal_state)

        # Check access
        if config.EnablePrivateEvents:
            self.validAccess(component, inserting, internal_state)


    def validCalendarDataCheck(self, component, inserting):
        """
        Check that the calendar data is valid iCalendar.
        @return:         tuple: (True/False if the calendar data is valid,
                                 log message string).
        """

        # Valid calendar data checks
        if not isinstance(component, VComponent):
            raise InvalidObjectResourceError("Wrong type of object: %s" % (type(component),))

        try:
            component.validCalendarData(validateRecurrences=self._txn._migrating)
        except InvalidICalendarDataError, e:
            raise InvalidObjectResourceError(str(e))
        try:
            component.validCalendarForCalDAV(methodAllowed=self.calendar().isInbox())
        except InvalidICalendarDataError, e:
            raise InvalidComponentForStoreError(str(e))
        try:
            if self._txn._migrating:
                component.validOrganizerForScheduling(doFix=True)
        except InvalidICalendarDataError, e:
            raise ValidOrganizerError(str(e))


    @inlineCallbacks
    def validAttendeeListSizeCheck(self, component, inserting):
        """
        Make sure that the Attendee list length is within bounds. We don't do this check for inbox because we
        will assume that the limit has been applied on the PUT causing the iTIP message to be created.

        FIXME: The inbox check might not take into account iSchedule stuff from outside. That needs to have
        the max attendees check applied at the time of delivery.
        """

        if config.MaxAttendeesPerInstance and not self.calendar().isInbox():
            uniqueAttendees = set()
            for attendee in component.getAllAttendeeProperties():
                uniqueAttendees.add(attendee.value())
            attendeeListLength = len(uniqueAttendees)
            if attendeeListLength > config.MaxAttendeesPerInstance:

                # Check to see whether we are increasing the count on an existing resource
                if not inserting:
                    oldcalendar = (yield self.componentForUser())
                    uniqueAttendees = set()
                    for attendee in oldcalendar.getAllAttendeeProperties():
                        uniqueAttendees.add(attendee.value())
                    oldAttendeeListLength = len(uniqueAttendees)
                else:
                    oldAttendeeListLength = 0

                if attendeeListLength > oldAttendeeListLength:
                    raise TooManyAttendeesError("Attendee list size %d is larger than allowed limit %d" % (attendeeListLength, config.MaxAttendeesPerInstance))


    def validLocationResourceOrganizer(self, component, inserting, internal_state):
        """
        If the calendar owner is a location or resource, check whether an ORGANIZER property is required.
        """

        if internal_state == ComponentUpdateState.NORMAL:
            originatorPrincipal = self.calendar().ownerHome().directoryRecord()
            cutype = originatorPrincipal.getCUType() if originatorPrincipal is not None else "INDIVIDUAL"
            organizer = component.getOrganizer()

            # Check for an allowed change
            if organizer is None and (
                cutype == "ROOM" and not config.Scheduling.Options.AllowLocationWithoutOrganizer or
                cutype == "RESOURCE" and not config.Scheduling.Options.AllowResourceWithoutOrganizer):
                raise ValidOrganizerError("Organizer required in calendar data for a %s" % (cutype.lower(),))

            # Check for tracking the modifier
            if organizer is None and (
                cutype == "ROOM" and config.Scheduling.Options.TrackUnscheduledLocationData or
                cutype == "RESOURCE" and config.Scheduling.Options.TrackUnscheduledResourceData):

                # Find current principal and update modified by details
                if hasattr(self._txn, "_authz_uid"):
                    authz = self.directoryService().recordWithUID(self._txn._authz_uid)
                    prop = Property("X-CALENDARSERVER-MODIFIED-BY", authz.canonicalCalendarUserAddress())
                    prop.setParameter("CN", authz.displayName())
                    for candidate in authz.calendarUserAddresses:
                        if candidate.startswith("mailto:"):
                            prop.setParameter("EMAIL", candidate[7:])
                            break
                    component.replacePropertyInAllComponents(prop)
                else:
                    component.removeAllPropertiesWithName("X-CALENDARSERVER-MODIFIED-BY")
                self._componentChanged = True


    def validAccess(self, component, inserting, internal_state):
        """
        Make sure that the X-CALENDARSERVER-ACCESS property is properly dealt with.
        """

        if component.hasProperty(Component.ACCESS_PROPERTY):

            # Must be a value we know about
            access = component.accessLevel(default=None)
            if access is None:
                raise InvalidCalendarAccessError("Private event access level not allowed")

            # Only DAV:owner is able to set the property to other than PUBLIC
            if internal_state == ComponentUpdateState.NORMAL:
                if self.calendar().viewerHome().uid() != self._txn._authz_uid and access != Component.ACCESS_PUBLIC:
                    raise InvalidCalendarAccessError("Private event access level change not allowed")

            self.accessMode = access
        else:
            # Check whether an access property was present before and write that into the calendar data
            if not inserting and self.accessMode:
                old_access = self.accessMode
                component.addProperty(Property(name=Component.ACCESS_PROPERTY, value=old_access))


    @inlineCallbacks
    def preservePrivateComments(self, component, inserting):
        """
        Check for private comments on the old resource and the new resource and re-insert
        ones that are lost.

        NB Do this before implicit scheduling as we don't want old clients to trigger scheduling when
        the X- property is missing.
        """
        if config.Scheduling.CalDAV.get("EnablePrivateComments", True):
            old_has_private_comments = not inserting and self.hasPrivateComment
            new_has_private_comments = component.hasPropertyInAnyComponent((
                "X-CALENDARSERVER-PRIVATE-COMMENT",
                "X-CALENDARSERVER-ATTENDEE-COMMENT",
            ))

            if old_has_private_comments and not new_has_private_comments:
                # Transfer old comments to new calendar
                log.debug("Private Comments properties were entirely removed by the client. Restoring existing properties.")
                old_calendar = (yield self.componentForUser())
                component.transferProperties(old_calendar, (
                    "X-CALENDARSERVER-PRIVATE-COMMENT",
                    "X-CALENDARSERVER-ATTENDEE-COMMENT",
                ))

            self.hasPrivateComment = new_has_private_comments


    @inlineCallbacks
    def replaceMissingToDoProperties(self, calendar, inserting, internal_state):
        """
        Recover any lost ORGANIZER or ATTENDEE properties in non-recurring VTODOs.
        """

        if not inserting and calendar.resourceType() == "VTODO" and not calendar.isRecurring():

            old_calendar = (yield self.componentForUser())

            new_organizer = calendar.getOrganizer()
            old_organizer = old_calendar.getOrganizerProperty()
            new_attendees = calendar.getAttendees()
            old_attendees = tuple(old_calendar.getAllAttendeeProperties())

            new_completed = calendar.masterComponent().hasProperty("COMPLETED")
            old_completed = old_calendar.masterComponent().hasProperty("COMPLETED")

            if old_organizer and not new_organizer and len(old_attendees) > 0 and len(new_attendees) == 0:
                # Transfer old organizer and attendees to new calendar
                log.debug("Organizer and attendee properties were entirely removed by the client. Restoring existing properties.")

                # Get the originator who is the owner of the calendar resource being modified
                originatorPrincipal = self.calendar().ownerHome().directoryRecord()
                originatorAddresses = originatorPrincipal.calendarUserAddresses

                for component in calendar.subcomponents():
                    if component.name() != "VTODO":
                        continue

                    if not component.hasProperty("DTSTART"):
                        # Need to put DTSTART back in or we get a date mismatch failure later
                        for old_component in old_calendar.subcomponents():
                            if old_component.name() != "VTODO":
                                continue
                            if old_component.hasProperty("DTSTART"):
                                component.addProperty(old_component.getProperty("DTSTART").duplicate())
                                break

                    # Add organizer back in from previous resource
                    component.addProperty(old_organizer.duplicate())

                    # Add attendees back in from previous resource
                    for anAttendee in old_attendees:
                        anAttendee = anAttendee.duplicate()
                        if component.hasProperty("COMPLETED") and anAttendee.value() in originatorAddresses:
                            anAttendee.setParameter("PARTSTAT", "COMPLETED")
                        component.addProperty(anAttendee)

            elif new_completed ^ old_completed and internal_state == ComponentUpdateState.NORMAL:
                # COMPLETED changed - sync up attendee state
                # We need this because many VTODO clients are not aware of scheduling,
                # i.e. they do not adjust any ATTENDEE PARTSTATs. We are going to impose
                # our own requirement that PARTSTAT is set to COMPLETED when the COMPLETED
                # property is added.

                # Transfer old organizer and attendees to new calendar
                log.debug("Sync COMPLETED property change.")

                # Get the originator who is the owner of the calendar resource being modified
                originatorPrincipal = self.calendar().ownerHome().directoryRecord()
                originatorAddresses = originatorPrincipal.calendarUserAddresses

                for component in calendar.subcomponents():
                    if component.name() != "VTODO":
                        continue

                    # Change owner partstat
                    for anAttendee in component.properties("ATTENDEE"):
                        if anAttendee.value() in originatorAddresses:
                            oldpartstat = anAttendee.parameterValue("PARTSTAT", "NEEDS-ACTION")
                            newpartstat = "COMPLETED" if component.hasProperty("COMPLETED") else "IN-PROCESS"
                            if newpartstat != oldpartstat:
                                anAttendee.setParameter("PARTSTAT", newpartstat)


    @inlineCallbacks
    def dropboxPathNormalization(self, component):
        """
        Make sure sharees only use dropbox paths of the sharer.
        """

        # Only relevant if calendar is sharee collection
        if not self.calendar().owned():

            # Get all X-APPLE-DROPBOX's and ATTACH's that are http URIs
            xdropboxes = component.getAllPropertiesInAnyComponent(
                "X-APPLE-DROPBOX",
                depth=1,
            )
            attachments = component.getAllPropertiesInAnyComponent(
                "ATTACH",
                depth=1,
            )
            attachments = [
                attachment for attachment in attachments
                if attachment.parameterValue("VALUE", "URI") == "URI" and attachment.value().startswith("http")
            ]

            if len(xdropboxes) or len(attachments):

                # Determine owner GUID
                owner = (yield self.calendar().ownerHome()).uid()

                def uriNormalize(uri):
                    urichanged = False
                    scheme, netloc, path, params, query, fragment = urlparse(uri)
                    pathbits = path.split("/")
                    if len(pathbits) >= 6 and pathbits[4] == "dropbox":
                        if pathbits[1] != "calendars":
                            pathbits[1] = "calendars"
                            urichanged = True
                        if pathbits[2] != "__uids__":
                            pathbits[2] = "__uids__"
                            urichanged = True
                        if pathbits[3] != owner:
                            pathbits[3] = owner
                            urichanged = True
                        if urichanged:
                            return urlunparse((scheme, netloc, "/".join(pathbits), params, query, fragment,))
                    return None

                for xdropbox in xdropboxes:
                    uri = uriNormalize(xdropbox.value())
                    if uri:
                        xdropbox.setValue(uri)
                        self._componentChanged = True
                for attachment in attachments:
                    uri = uriNormalize(attachment.value())
                    if uri:
                        attachment.setValue(uri)
                        self._componentChanged = True


    def processAlarms(self, component, inserting):
        """
        Remove duplicate alarms. Add a default alarm if required.

        @return: indicate whether a change was made
        @rtype: C{bool}
        """

        # Remove duplicate alarms
        if config.RemoveDuplicateAlarms and component.hasDuplicateAlarms(doFix=True):
            self._componentChanged = True

        # Only if feature enabled
        if not config.EnableDefaultAlarms:
            return

        # Check that we are creating and this is not the inbox
        if not inserting or self.calendar().isInbox():
            return

        # Never add default alarms to calendar data in shared calendars
        if not self.calendar().owned():
            return

        # Add default alarm for VEVENT and VTODO only
        mtype = component.mainType().upper()
        if component.mainType().upper() not in ("VEVENT", "VTODO"):
            return
        vevent = mtype == "VEVENT"

        # Check timed or all-day
        start, _ignore_end = component.mainComponent().getEffectiveStartEnd()
        if start is None:
            # Yes VTODOs might have no DTSTART or DUE - in this case we do not add a default
            return
        timed = not start.isDateOnly()

        # See if default exists and add using appropriate logic
        alarm = self.calendar().getDefaultAlarm(vevent, timed)
        if alarm is None:
            alarm = self.calendar().viewerHome().getDefaultAlarm(vevent, timed)
        if alarm and alarm != "empty" and component.addAlarms(alarm):
            self._componentChanged = True


    @inlineCallbacks
    def doImplicitScheduling(self, component, inserting, internal_state):

        new_component = None
        did_implicit_action = False
        is_scheduling_resource = False
        schedule_state = None

        is_internal = internal_state not in (ComponentUpdateState.NORMAL, ComponentUpdateState.ATTACHMENT_UPDATE,)

        # Do scheduling
        if not self.calendar().isInbox():
            scheduler = ImplicitScheduler()

            # PUT
            do_implicit_action, is_scheduling_resource = (yield scheduler.testImplicitSchedulingPUT(
                self.calendar(),
                None if inserting else self,
                component,
                internal_request=is_internal,
            ))

            if do_implicit_action and not is_internal:

                # Cannot do implicit in sharee's shared calendar
                if not self.calendar().owned():
                    scheduler.setSchedulingNotAllowed(
                        ShareeAllowedError,
                        "Sharee's cannot schedule",
                    )

                new_calendar = (yield scheduler.doImplicitScheduling(self.schedule_tag_match))
                if new_calendar:
                    if isinstance(new_calendar, int):
                        returnValue(new_calendar)
                    else:
                        new_component = new_calendar
                did_implicit_action = True
                schedule_state = scheduler.state

        returnValue((is_scheduling_resource, new_component, did_implicit_action, schedule_state,))


    @inlineCallbacks
    def mergePerUserData(self, component, inserting):
        """
        Merge incoming calendar data with other user's per-user data in existing calendar data.

        @param component: incoming calendar data
        @type component: L{twistedcaldav.ical.Component}
        """
        accessUID = self.calendar().viewerHome().uid()
        oldCal = (yield self.component()) if not inserting else None

        # Duplicate before we do the merge because someone else may "own" the calendar object
        # and we should not change it. This is not ideal as we may duplicate it unnecessarily
        # but we currently have no api to let the caller tell us whether it cares about the
        # whether the calendar data is changed or not.
        try:
            component = PerUserDataFilter(accessUID).merge(component.duplicate(), oldCal)
        except ValueError:
            log.error("Invalid per-user data merge")
            raise InvalidPerUserDataMerge("Cannot merge per-user data")

        returnValue(component)


    def processScheduleTags(self, component, inserting, internal_state):

        # Check for scheduling object resource and write property
        if self.isScheduleObject:
            # Need to figure out when to change the schedule tag:
            #
            # 1. If this is not an internal request then the resource is being explicitly changed
            # 2. If it is an internal request for the Organizer, schedule tag never changes
            # 3. If it is an internal request for an Attendee and the message being processed came
            #    from the Organizer then the schedule tag changes.

            # Check what kind of processing is going on
            change_scheduletag = not (
                (internal_state == ComponentUpdateState.ORGANIZER_ITIP_UPDATE) or
                (internal_state == ComponentUpdateState.ATTENDEE_ITIP_UPDATE) and hasattr(self._txn, "doing_attendee_refresh")
            )

            if change_scheduletag or not self.scheduleTag:
                self.scheduleTag = str(uuid.uuid4())

            # Handle weak etag compatibility
            if config.Scheduling.CalDAV.ScheduleTagCompatibility:
                if change_scheduletag:
                    # Schedule-Tag change => weak ETag behavior must not happen
                    etags = ()
                else:
                    # Schedule-Tag did not change => add current ETag to list of those that can
                    # be used in a weak precondition test
                    etags = self.scheduleEtags
                    if etags is None:
                        etags = ()
                etags += (self._generateEtag(str(component)),)
                self.scheduleEtags = etags
            else:
                self.scheduleEtags = ()
        else:
            self.scheduleTag = ""
            self.scheduleEtags = ()


    @inlineCallbacks
    def _lockUID(self, component, inserting, internal_state):
        """
        Create a lock on the component's UID and verify, after getting the lock, that the incoming UID
        meets the requirements of the store.
        """

        new_uid = component.resourceUID()
        if internal_state == ComponentUpdateState.NORMAL:
            yield NamedLock.acquire(self._txn, "ImplicitUIDLock:%s" % (hashlib.md5(new_uid).hexdigest(),))

        # UID conflict check - note we do this after reserving the UID to avoid a race condition where two requests
        # try to write the same calendar data to two different resource URIs.
        if not self.calendar().isInbox():
            # Cannot overwrite a resource with different UID
            if not inserting:
                if self._uid != new_uid:
                    raise InvalidUIDError("Cannot change the UID in an existing resource.")
            else:
                # New UID must be unique for the owner - no need to do this on an overwrite as we can assume
                # the store is already consistent in this regard
                elsewhere = (yield self.calendar().ownerHome().hasCalendarResourceUIDSomewhereElse(new_uid, self, "schedule"))
                if elsewhere is not None:
                    if elsewhere.calendar().id() == self.calendar().id():
                        raise UIDExistsError("UID already exists in same calendar.")
                    else:
                        raise UIDExistsElsewhereError("UID already exists in different calendar: %s." % (elsewhere.calendar().name(),))


    def setComponent(self, component, inserting=False, smart_merge=False):
        """
        Public api for storing a component. This will do full data validation checks on the specified component.
        Scheduling will be done automatically.
        """

        return self._setComponentInternal(component, inserting, ComponentUpdateState.NORMAL, smart_merge)


    @inlineCallbacks
    def _setComponentInternal(self, component, inserting=False, internal_state=ComponentUpdateState.NORMAL, smart_merge=False):
        """
        Setting the component internally to the store itself. This will bypass a whole bunch of data consistency checks
        on the assumption that those have been done prior to the component data being provided, provided the flag is set.
        This should always be treated as an api private to the store.
        """

        self._componentChanged = False
        self.schedule_tag_match = not self.calendar().isInbox() and internal_state == ComponentUpdateState.NORMAL and smart_merge
        schedule_state = None

        if internal_state == ComponentUpdateState.SPLIT:
            # When splitting, some state from the previous resource needs to be properly
            # preserved in thus new one when storing the component. Since we don't do the "full"
            # store here, we need to add the explicit pieces we need for state preservation.

            # Check access
            if config.EnablePrivateEvents:
                self.validAccess(component, inserting, internal_state)

            # Preserve private comments
            yield self.preservePrivateComments(component, inserting)

            managed_copied, managed_removed = (yield self.resourceCheckAttachments(component, inserting))

            self.isScheduleObject = True
            self.processScheduleTags(component, inserting, internal_state)

        elif internal_state != ComponentUpdateState.RAW:
            # Handle all validation operations here.
            yield self.fullValidation(component, inserting, internal_state)

            # UID lock - this will remain active until the end of the current txn
            yield self._lockUID(component, inserting, internal_state)

            # Preserve private comments
            yield self.preservePrivateComments(component, inserting)

            # Fix broken VTODOs
            yield self.replaceMissingToDoProperties(component, inserting, internal_state)

            # Handle sharing dropbox normalization
            yield self.dropboxPathNormalization(component)

            # Pre-process managed attachments
            if internal_state == ComponentUpdateState.NORMAL:
                managed_copied, managed_removed = (yield self.resourceCheckAttachments(component, inserting))

            # Default/duplicate alarms
            self.processAlarms(component, inserting)

            # Do scheduling
            implicit_result = (yield self.doImplicitScheduling(component, inserting, internal_state))
            if isinstance(implicit_result, int):
                if implicit_result == ImplicitScheduler.STATUS_ORPHANED_CANCELLED_EVENT:
                    raise ResourceDeletedError("Resource created but immediately deleted by the server.")

                elif implicit_result == ImplicitScheduler.STATUS_ORPHANED_EVENT:

                    # Now forcibly delete the event
                    if not inserting:
                        yield self._removeInternal(internal_state=ComponentRemoveState.INTERNAL)
                        raise ResourceDeletedError("Resource modified but immediately deleted by the server.")
                    else:
                        raise AttendeeAllowedError("Attendee cannot create event for Organizer: %s" % (implicit_result,))

                else:
                    msg = "Invalid return status code from ImplicitScheduler: %s" % (implicit_result,)
                    log.error(msg)
                    raise InvalidObjectResourceError(msg)
            else:
                self.isScheduleObject, new_component, did_implicit_action, schedule_state = implicit_result
                if new_component is not None:
                    component = new_component
                if did_implicit_action:
                    self._componentChanged = True

            # Always do the per-user data merge right before we store
            component = (yield self.mergePerUserData(component, inserting))

            self.processScheduleTags(component, inserting, internal_state)

        # When migrating we always do validity check to fix issues
        elif self._txn._migrating:
            self.validCalendarDataCheck(component, inserting)

        yield self.updateDatabase(component, inserting=inserting)

        # Post process managed attachments
        if internal_state in (ComponentUpdateState.NORMAL, ComponentUpdateState.SPLIT):
            if managed_copied:
                yield self.copyResourceAttachments(managed_copied)
            if managed_removed:
                yield self.removeResourceAttachments(managed_removed)

        if inserting:
            yield self._calendar._insertRevision(self._name)
        else:
            yield self._calendar._updateRevision(self._name)

        yield self._calendar.notifyChanged()

        # Finally check if a split is needed
        if internal_state != ComponentUpdateState.SPLIT and schedule_state == "organizer":
            yield self.checkSplit()

        returnValue(self._componentChanged)


    def _generateEtag(self, componentText):
        return hashlib.md5(componentText + (self.scheduleTag if self.scheduleTag else "")).hexdigest()


    @inlineCallbacks
    def updateDatabase(self, component, expand_until=None, reCreate=False,
                       inserting=False, txn=None):
        """
        Update the database tables for the new data being written. Occasionally we might need to do an update to
        time-range data via a separate transaction, so we allow that to be passed in. Note that in that case
        access to the parent resources will not occur in this method, so the queries on the new txn won't depend
        on any parent objects having the same txn set.

        @param component: calendar data to store
        @type component: L{Component}
        """

        # Setup appropriate txn
        txn = txn if txn is not None else self._txn

        # inbox does things slightly differently
        isInboxItem = self.calendar().isInbox()

        # In some cases there is no need to remove/rebuild the instance index because we know no time or
        # freebusy related properties have changed (e.g. an attendee reply and refresh). In those cases
        # the component will have a special attribute present to let us know to suppress the instance indexing.
        instanceIndexingRequired = not getattr(component, "noInstanceIndexing", False) or inserting or reCreate

        if instanceIndexingRequired:

            # Decide how far to expand based on the component. doInstanceIndexing will indicate whether we
            # store expanded instance data immediately, or wait until a re-expand is triggered by some later
            # operation.
            doInstanceIndexing = False
            master = component.masterComponent()
            if (master is None or not component.isRecurring()):
                # When there is no master we have a set of overridden components -
                #   index them all.
                # When there is one instance - index it.
                expand = PyCalendarDateTime(2100, 1, 1, 0, 0, 0, tzid=PyCalendarTimezone(utc=True))
                doInstanceIndexing = True
            else:

                # If migrating or re-creating or config option for delayed indexing is off, always index
                if reCreate or txn._migrating or (not config.FreeBusyIndexDelayedExpand and not isInboxItem):
                    doInstanceIndexing = True

                # Duration into the future through which recurrences are expanded in the index
                # by default.  This is a caching parameter which affects the size of the index;
                # it does not affect search results beyond this period, but it may affect
                # performance of such a search.
                expand = (PyCalendarDateTime.getToday() +
                          PyCalendarDuration(days=config.FreeBusyIndexExpandAheadDays))

                if expand_until and expand_until > expand:
                    expand = expand_until

                # Maximum duration into the future through which recurrences are expanded in the
                # index.  This is a caching parameter which affects the size of the index; it
                # does not affect search results beyond this period, but it may affect
                # performance of such a search.
                #
                # When a search is performed on a time span that goes beyond that which is
                # expanded in the index, we have to open each resource which may have data in
                # that time period.  In order to avoid doing that multiple times, we want to
                # cache those results.  However, we don't necessarily want to cache all
                # occurrences into some obscenely far-in-the-future date, so we cap the caching
                # period.  Searches beyond this period will always be relatively expensive for
                # resources with occurrences beyond this period.
                if expand > (PyCalendarDateTime.getToday() +
                             PyCalendarDuration(days=config.FreeBusyIndexExpandMaxDays)):
                    raise IndexedSearchException

            if config.FreeBusyIndexLowerLimitDays:
                truncateLowerLimit = PyCalendarDateTime.getToday()
                truncateLowerLimit.offsetDay(-config.FreeBusyIndexLowerLimitDays)
            else:
                truncateLowerLimit = None

            # Always do recurrence expansion even if we do not intend to index - we need this to double-check the
            # validity of the iCalendar recurrence data.
            try:
                instances = component.expandTimeRanges(expand, lowerLimit=truncateLowerLimit, ignoreInvalidInstances=reCreate)
                recurrenceLimit = instances.limit
                recurrenceLowerLimit = instances.lowerLimit
            except InvalidOverriddenInstanceError, e:
                self.log.error("Invalid instance %s when indexing %s in %s" %
                               (e.rid, self._name, self._calendar,))

                if txn._migrating:
                    # TODO: fix the data here by re-writing component then re-index
                    instances = component.expandTimeRanges(expand, lowerLimit=truncateLowerLimit, ignoreInvalidInstances=True)
                    recurrenceLimit = instances.limit
                    recurrenceLowerLimit = instances.lowerLimit
                else:
                    raise

            # Now coerce indexing to off if needed
            if not doInstanceIndexing:
                instances = None
                recurrenceLowerLimit = None
                recurrenceLimit = PyCalendarDateTime(1900, 1, 1, 0, 0, 0, tzid=PyCalendarTimezone(utc=True))

        co = schema.CALENDAR_OBJECT
        tr = schema.TIME_RANGE

        # Do not update if reCreate (re-indexing - we don't want to re-write data
        # or cause modified to change)
        if not reCreate:
            componentText = str(component)
            self._objectText = componentText
            self._cachedComponent = component
            self._cachedCommponentPerUser = {}

            organizer = component.getOrganizer()
            if not organizer:
                organizer = ""

            # CALENDAR_OBJECT table update
            self._uid = component.resourceUID()
            self._md5 = self._generateEtag(componentText)
            self._size = len(componentText)

            # Special - if migrating we need to preserve the original md5
            if txn._migrating and hasattr(component, "md5"):
                self._md5 = component.md5

            # Determine attachment mode (ignore inbox's) - NB we have to do this
            # after setting up other properties as UID at least is needed
            self._attachment = _ATTACHMENTS_MODE_NONE
            if not self._dropboxID:
                if not isInboxItem:
                    if component.hasPropertyInAnyComponent("X-APPLE-DROPBOX"):
                        self._attachment = _ATTACHMENTS_MODE_WRITE
                        self._dropboxID = (yield self.dropboxID())
                    else:
                        # Only include a dropbox id if dropbox attachments exist
                        attachments = component.getAllPropertiesInAnyComponent("ATTACH")
                        has_dropbox = any([attachment.value().find("/dropbox/") != -1 for attachment in attachments])
                        if has_dropbox:
                            self._attachment = _ATTACHMENTS_MODE_READ
                            self._dropboxID = (yield self.dropboxID())

            values = {
                co.CALENDAR_RESOURCE_ID            : self._calendar._resourceID,
                co.RESOURCE_NAME                   : self._name,
                co.ICALENDAR_TEXT                  : componentText,
                co.ICALENDAR_UID                   : self._uid,
                co.ICALENDAR_TYPE                  : component.resourceType(),
                co.ATTACHMENTS_MODE                : self._attachment,
                co.DROPBOX_ID                      : self._dropboxID,
                co.ORGANIZER                       : organizer,
                co.ACCESS                          : self._access,
                co.SCHEDULE_OBJECT                 : self._schedule_object,
                co.SCHEDULE_TAG                    : self._schedule_tag,
                co.SCHEDULE_ETAGS                  : self._schedule_etags,
                co.PRIVATE_COMMENTS                : self._private_comments,
                co.MD5                             : self._md5
            }

            # Only needed if indexing being changed
            if instanceIndexingRequired:
                values[co.RECURRANCE_MIN] = pyCalendarTodatetime(normalizeForIndex(recurrenceLowerLimit)) if recurrenceLowerLimit else None
                values[co.RECURRANCE_MAX] = pyCalendarTodatetime(normalizeForIndex(recurrenceLimit)) if recurrenceLimit else None

            if inserting:
                self._resourceID, self._created, self._modified = (
                    yield Insert(
                        values,
                        Return=(co.RESOURCE_ID, co.CREATED, co.MODIFIED)
                    ).on(txn)
                )[0]
            else:
                values[co.MODIFIED] = utcNowSQL
                self._modified = (
                    yield Update(
                        values, Return=co.MODIFIED,
                        Where=co.RESOURCE_ID == self._resourceID
                    ).on(txn)
                )[0][0]

                # Need to wipe the existing time-range for this and rebuild if required
                if instanceIndexingRequired:
                    yield Delete(
                        From=tr,
                        Where=tr.CALENDAR_OBJECT_RESOURCE_ID == self._resourceID
                    ).on(txn)
        else:
            # Keep MODIFIED the same when doing an index-only update
            values = {
                co.RECURRANCE_MIN : pyCalendarTodatetime(normalizeForIndex(recurrenceLowerLimit)) if recurrenceLowerLimit else None,
                co.RECURRANCE_MAX : pyCalendarTodatetime(normalizeForIndex(recurrenceLimit)) if recurrenceLimit else None,
                co.MODIFIED : self._modified,
            }

            yield Update(
                values,
                Where=co.RESOURCE_ID == self._resourceID
            ).on(txn)

            # Need to wipe the existing time-range for this and rebuild
            yield Delete(
                From=tr,
                Where=tr.CALENDAR_OBJECT_RESOURCE_ID == self._resourceID
            ).on(txn)

        if instanceIndexingRequired and doInstanceIndexing:
            yield self._addInstances(component, instances, truncateLowerLimit, isInboxItem, txn)


    @inlineCallbacks
    def _addInstances(self, component, instances, truncateLowerLimit, isInboxItem, txn):
        """
        Add the set of supplied instances to the store.

        @param component: the component whose instances are being added
        @type component: L{Component}
        @param instances: the set of instances to add
        @type instances: L{InstanceList}
        @param truncateLowerLimit: the lower limit for instances
        @type truncateLowerLimit: L{PyCalendarDateTime}
        @param isInboxItem: indicates if an inbox item
        @type isInboxItem: C{bool}
        @param txn: transaction to use
        @type txn: L{Transaction}
        """

        # TIME_RANGE table update
        lowerLimitApplied = False
        for key in instances:
            instance = instances[key]
            start = instance.start
            end = instance.end
            floating = instance.start.floating()
            transp = instance.component.propertyValue("TRANSP") == "TRANSPARENT"
            fbtype = instance.component.getFBType()
            start.setTimezoneUTC(True)
            end.setTimezoneUTC(True)

            # Ignore if below the lower limit
            if truncateLowerLimit and end < truncateLowerLimit:
                lowerLimitApplied = True
                continue

            yield self._addInstanceDetails(component, instance.rid, start, end, floating, transp, fbtype, isInboxItem, txn)

        # For truncated items we insert a tomb stone lower bound so that a time-range
        # query with just an end bound will match
        if lowerLimitApplied or instances.lowerLimit and len(instances.instances) == 0:
            start = PyCalendarDateTime(1901, 1, 1, 0, 0, 0, tzid=PyCalendarTimezone(utc=True))
            end = PyCalendarDateTime(1901, 1, 1, 1, 0, 0, tzid=PyCalendarTimezone(utc=True))
            yield self._addInstanceDetails(component, None, start, end, False, True, "UNKNOWN", isInboxItem, txn)

        # Special - for unbounded recurrence we insert a value for "infinity"
        # that will allow an open-ended time-range to always match it.
        # We also need to add the "infinity" value if the event was bounded but
        # starts after the future expansion cut-off limit.
        if component.isRecurringUnbounded() or instances.limit and len(instances.instances) == 0:
            start = PyCalendarDateTime(2100, 1, 1, 0, 0, 0, tzid=PyCalendarTimezone(utc=True))
            end = PyCalendarDateTime(2100, 1, 1, 1, 0, 0, tzid=PyCalendarTimezone(utc=True))
            yield self._addInstanceDetails(component, None, start, end, False, True, "UNKNOWN", isInboxItem, txn)


    @inlineCallbacks
    def _addInstanceDetails(self, component, rid, start, end, floating, transp, fbtype, isInboxItem, txn):

        tr = schema.TIME_RANGE
        tpy = schema.TRANSPARENCY

        instanceid = (yield Insert({
            tr.CALENDAR_RESOURCE_ID        : self._calendar._resourceID,
            tr.CALENDAR_OBJECT_RESOURCE_ID : self._resourceID,
            tr.FLOATING                    : floating,
            tr.START_DATE                  : pyCalendarTodatetime(start),
            tr.END_DATE                    : pyCalendarTodatetime(end),
            tr.FBTYPE                      : icalfbtype_to_indexfbtype.get(fbtype, icalfbtype_to_indexfbtype["FREE"]),
            tr.TRANSPARENT                 : transp,
        }, Return=tr.INSTANCE_ID).on(txn))[0][0]

        # Don't do transparency for inbox items - we never do freebusy on inbox
        if not isInboxItem:
            peruserdata = component.perUserTransparency(rid)
            for useruid, usertransp in peruserdata:
                if usertransp != transp:
                    (yield Insert({
                        tpy.TIME_RANGE_INSTANCE_ID : instanceid,
                        tpy.USER_ID                : useruid,
                        tpy.TRANSPARENT            : usertransp,
                    }).on(txn))


    @inlineCallbacks
    def component(self):
        """
        Read calendar data and validate/fix it. Do not raise a store error here
        if there are unfixable errors as that could prevent the overall request
        to fail. Instead we will hand bad data off to the caller - that is not
        ideal but in theory we should have checked everything on the way in and
        only allowed in good data.
        """

        if self._cachedComponent is None:

            text = yield self._text()

            try:
                component = VComponent.fromString(text)
            except InvalidICalendarDataError, e:
                # This is a really bad situation, so do raise
                raise InternalDataStoreError(
                    "Data corruption detected (%s) in id: %s"
                    % (e, self._resourceID)
                )

            # Fix any bogus data we can
            fixed, unfixed = component.validCalendarData(doFix=True, doRaise=False)

            if unfixed:
                self.log.error("Calendar data id=%s had unfixable problems:\n  %s" %
                               (self._resourceID, "\n  ".join(unfixed),))

            if fixed:
                self.log.error("Calendar data id=%s had fixable problems:\n  %s" %
                               (self._resourceID, "\n  ".join(fixed),))

            self._cachedComponent = component
            self._cachedCommponentPerUser = {}

        returnValue(self._cachedComponent)


    @inlineCallbacks
    def componentForUser(self, user_uuid=None):
        """
        Return the iCalendar component filtered for the specified user's per-user data.

        @param user_uuid: the user UUID to filter on
        @type user_uuid: C{str}

        @return: the filtered calendar component
        @rtype: L{twistedcaldav.ical.Component}
        """

        if user_uuid is None:
            user_uuid = self._parentCollection.viewerHome().uid()

        if user_uuid not in self._cachedCommponentPerUser:
            caldata = yield self.component()
            filtered = PerUserDataFilter(user_uuid).filter(caldata.duplicate())
            self._cachedCommponentPerUser[user_uuid] = filtered
        returnValue(self._cachedCommponentPerUser[user_uuid])


    def moveValidation(self, destination, name):
        """
        Validate whether a move to the specified collection is allowed.

        @param destination: destination calendar collection
        @type destination: L{CalendarCollection}
        @param name: name of new resource
        @type name: C{str}
        """

        # Calendar to calendar moves are OK if the resource (viewer) owner is the same.
        # Use resourceOwnerPrincipal for this as that takes into account sharing such that the
        # returned principal relates to the URI path used to access the resource rather than the
        # underlying resource owner (sharee).
        sourceowner = self.calendar().viewerHome().uid()
        destowner = destination.viewerHome().uid()

        if sourceowner != destowner:
            msg = "Calendar-to-calendar moves with different homes are not supported."
            log.debug(msg)
            raise InvalidResourceMove(msg)

        # Calendar to calendar moves where Organizer is present are not OK if the owners are different.
        sourceowner = self.calendar().ownerHome().uid()
        destowner = destination.ownerHome().uid()

        if sourceowner != destowner and self._schedule_object:
            msg = "Calendar-to-calendar moves with an organizer property present and different owners are not supported."
            log.debug(msg)
            raise InvalidResourceMove(msg)

        # NB there is no need to do a UID lock and test here as we are moving an existing resource
        # with the already imposed constraint of unique UIDs.

        return succeed(None)


    def remove(self, implicitly=True):
        return self._removeInternal(
            internal_state=ComponentRemoveState.NORMAL if implicitly else ComponentRemoveState.NORMAL_NO_IMPLICIT
        )


    @inlineCallbacks
    def _removeInternal(self, internal_state=ComponentRemoveState.NORMAL):

        isinbox = self._calendar.isInbox()

        # Pre-flight scheduling operation
        scheduler = None
        if not isinbox and internal_state == ComponentRemoveState.NORMAL:
            # Get data we need for implicit scheduling
            calendar = (yield self.componentForUser())
            scheduler = ImplicitScheduler()
            do_implicit_action, _ignore = (yield scheduler.testImplicitSchedulingDELETE(
                self.calendar(),
                self,
                calendar,
                internal_request=(internal_state != ComponentUpdateState.NORMAL),
            ))
            if do_implicit_action:
                yield NamedLock.acquire(self._txn, "ImplicitUIDLock:%s" % (hashlib.md5(calendar.resourceUID()).hexdigest(),))

        # Need to also remove attachments
        if self._dropboxID:
            yield DropBoxAttachment.resourceRemoved(self._txn, self._resourceID, self._dropboxID)
        yield ManagedAttachment.resourceRemoved(self._txn, self._resourceID)
        yield super(CalendarObject, self).remove()

        # Do scheduling
        if scheduler is not None:
            yield scheduler.doImplicitScheduling()


    @classproperty
    def _recurrenceMinMaxByIDQuery(cls):  # @NoSelf
        """
        DAL query to load RECURRANCE_MIN, RECURRANCE_MAX via an object's resource ID.
        """
        co = schema.CALENDAR_OBJECT
        return Select(
            [co.RECURRANCE_MIN, co.RECURRANCE_MAX, ],
            From=co,
            Where=co.RESOURCE_ID == Parameter("resourceID"),
        )


    @inlineCallbacks
    def recurrenceMinMax(self, txn=None):
        """
        Get the RECURRANCE_MIN, RECURRANCE_MAX value from the database. Occasionally we might need to do an
        update to time-range data via a separate transaction, so we allow that to be passed in.

        @return: L{PyCalendarDateTime} result
        """
        # Setup appropriate txn
        txn = txn if txn is not None else self._txn

        rMin, rMax = (
            yield self._recurrenceMinMaxByIDQuery.on(txn,
                                         resourceID=self._resourceID)
        )[0]
        returnValue((
            parseSQLDateToPyCalendar(rMin) if rMin is not None else None,
            parseSQLDateToPyCalendar(rMax) if rMax is not None else None,
        ))


    @classproperty
    def _instanceQuery(cls):  # @NoSelf
        """
        DAL query to load TIME_RANGE data via an object's resource ID.
        """
        tr = schema.TIME_RANGE
        return Select(
            [
                tr.INSTANCE_ID,
                tr.START_DATE,
                tr.END_DATE,
            ],
            From=tr,
            Where=tr.CALENDAR_OBJECT_RESOURCE_ID == Parameter("resourceID"),
        )


    @inlineCallbacks
    def instances(self, txn=None):
        """
        Get the set of instances from the database.

        @return: C{list} result
        """
        # Setup appropriate txn
        txn = txn if txn is not None else self._txn

        instances = (
            yield self._instanceQuery.on(txn,
                                         resourceID=self._resourceID)
        )
        returnValue(tuple(instances))


    @inlineCallbacks
    def organizer(self):
        returnValue((yield self.component()).getOrganizer())


    def getMetadata(self):
        metadata = {}
        metadata["accessMode"] = self.accessMode
        metadata["isScheduleObject"] = self.isScheduleObject
        metadata["scheduleTag"] = self.scheduleTag
        metadata["scheduleEtags"] = self.scheduleEtags
        metadata["hasPrivateComment"] = self.hasPrivateComment
        return metadata


    def _get_accessMode(self):
        return accesstype_to_accessMode[self._access]


    def _set_accessMode(self, value):
        self._access = accessMode_to_type[value]

    accessMode = property(_get_accessMode, _set_accessMode)

    def _get_isScheduleObject(self):
        return self._schedule_object


    def _set_isScheduleObject(self, value):
        self._schedule_object = value

    isScheduleObject = property(_get_isScheduleObject, _set_isScheduleObject)

    def _get_scheduleTag(self):
        return self._schedule_tag


    def _set_scheduleTag(self, value):
        self._schedule_tag = value

    scheduleTag = property(_get_scheduleTag, _set_scheduleTag)

    def _get_scheduleEtags(self):
        return tuple(self._schedule_etags.split(",")) if self._schedule_etags else ()


    def _set_scheduleEtags(self, value):
        self._schedule_etags = ",".join(value) if value else ""

    scheduleEtags = property(_get_scheduleEtags, _set_scheduleEtags)

    def _get_hasPrivateComment(self):
        return self._private_comments


    def _set_hasPrivateComment(self, value):
        self._private_comments = value

    hasPrivateComment = property(_get_hasPrivateComment, _set_hasPrivateComment)

    @inlineCallbacks
    def resourceCheckAttachments(self, component, inserting=False):
        """
        A component is being changed or added. Check any ATTACH properties that may be present
        to verify they are owned by the organizer/owner of the resource. Strip any that are invalid.

        @param component: calendar component about to be stored
        @type component: L{Component}
        """

        # Retrieve all ATTACH properties with a MANAGED-ID in new data
        newattached = collections.defaultdict(list)
        newattachments = component.getAllPropertiesInAnyComponent("ATTACH", depth=1,)
        for attachment in newattachments:
            managed_id = attachment.parameterValue("MANAGED-ID")
            if managed_id is not None:
                newattached[managed_id].append(attachment)

        # Retrieve all ATTACH properties with a MANAGED-ID in old data
        if not inserting:
            oldcomponent = (yield self.component())
            oldattached = collections.defaultdict(list)
            oldattachments = oldcomponent.getAllPropertiesInAnyComponent("ATTACH", depth=1,)
            for attachment in oldattachments:
                managed_id = attachment.parameterValue("MANAGED-ID")
                if managed_id is not None:
                    oldattached[managed_id].append(attachment)
        else:
            oldattached = collections.defaultdict(list)

        # Punt if no managed attachments
        if len(newattached) + len(oldattached) == 0:
            returnValue((None, None,))

        newattached_keys = set(newattached.keys())
        oldattached_keys = set(oldattached.keys())

        # Determine what was removed
        removed = set(oldattached_keys) - set(newattached_keys)

        # Determine what was added
        added = set(newattached_keys) - set(oldattached_keys)
        changed = {}
        for managed_id in added:
            changed[managed_id] = newattached[managed_id]

        if not self._dropboxID:
            self._dropboxID = str(uuid.uuid4())
        changes = yield self._addingManagedIDs(self._txn, self._parentCollection, self._dropboxID, changed, component.resourceUID())

        # Make sure existing data is not changed
        same = oldattached_keys & newattached_keys
        for managed_id in same:
            newattachment = newattached[managed_id]
            oldattachment = oldattached[managed_id][0]
            for newattachment in newattached[managed_id]:
                if newattachment != oldattachment:
                    newattachment.setParameter("FMTTYPE", oldattachment.parameterValue("FMTTYPE"))
                    newattachment.setParameter("FILENAME", oldattachment.parameterValue("FILENAME"))
                    newattachment.setParameter("SIZE", oldattachment.parameterValue("SIZE"))
                    newattachment.setValue(oldattachment.value())

        returnValue((changes, removed,))


    @inlineCallbacks
    def _addingManagedIDs(self, txn, parent, dropbox_id, attached, newuid):
        # Now check each managed-id
        changes = []
        for managed_id, attachments in attached.items():

            # Must be in the same home as this resource
            details = (yield ManagedAttachment.usedManagedID(txn, managed_id))
            if len(details) == 0:
                raise AttachmentStoreValidManagedID
            homes = set()
            uids = set()
            for home_id, _ignore_resource_id, uid in details:
                homes.add(home_id)
                uids.add(uid)
            if len(homes) != 1:
                # This is a bad store error - there should be only one home associated with a managed-id
                raise InternalDataStoreError

            # Policy:
            #
            # 1. If Managed-ID is re-used in a resource with the same UID - it is fine - just rewrite the details
            # 2. If Managed-ID is re-used in a different resource but owned by the same user - just rewrite the details
            # 3. Otherwise, strip off the managed-id property and treat as unmanaged.

            # 1. UID check
            if newuid in uids:
                yield self._syncAttachmentProperty(txn, managed_id, dropbox_id, attachments)

            # 2. Same home
            elif home_id == parent.ownerHome()._resourceID:

                # Record the change as we will need to add a new reference to this attachment
                yield self._syncAttachmentProperty(txn, managed_id, dropbox_id, attachments)
                changes.append(managed_id)

            else:
                self._stripAttachmentProperty(attachments)

        returnValue(changes)


    @inlineCallbacks
    def _syncAttachmentProperty(self, txn, managed_id, dropbox_id, attachments):
        """
        Make sure the supplied set of attach properties are all sync'd with the current value of the
        matching managed-id attachment.

        @param managed_id: Managed-Id to sync with
        @type managed_id: C{str}
        @param attachments: list of attachment properties
        @type attachments: C{list} of L{twistedcaldav.ical.Property}
        """

        # Try to load any attachment directly referenced by this object, otherwise load one referenced by some
        # any other - they are all the same.
        new_attachment = (yield ManagedAttachment.load(txn, self._resourceID, managed_id))
        if new_attachment is None:
            new_attachment = (yield ManagedAttachment.load(txn, None, managed_id))
        new_attachment._objectDropboxID = dropbox_id
        for attachment in attachments:
            yield new_attachment.updateProperty(attachment)


    def _stripAttachmentProperty(self, attachments):
        """
        Strip off managed-id related properties from an attachment.
        """
        for attachment in attachments:
            attachment.removeParameter("MANAGED-ID")
            attachment.removeParameter("MTAG")


    @inlineCallbacks
    def copyResourceAttachments(self, attached):
        """
        Copy an attachment reference for some other resource and link it to this resource.

        @param attached: managed id for the attachments to copy
        @type attached: C{str}
        """
        for managed_id in attached:
            yield ManagedAttachment.copyManagedID(self._txn, managed_id, self._resourceID)


    @inlineCallbacks
    def removeResourceAttachments(self, attached):
        """
        Remove an attachment reference for this resource.

        @param attached: managed-ids to remove
        @type attached: C{tuple}
        """
        for managed_id in attached:
            yield self.removeManagedAttachmentWithID(managed_id)


    @inlineCallbacks
    def _checkValidManagedAttachmentChange(self):
        """
        Make sure a managed attachment add, update or remover operation is valid.
        """

        # Only allow organizers to manipulate managed attachments for now
        calendar = (yield self.componentForUser())
        scheduler = ImplicitScheduler()
        is_attendee = (yield scheduler.testAttendeeEvent(self.calendar(), self, calendar,))
        if is_attendee:
            raise InvalidAttachmentOperation("Attendees are not allowed to manipulate managed attachments")


    @inlineCallbacks
    def addAttachment(self, rids, content_type, filename, stream):
        """
        Add a new managed attachment to this calendar object.

        @param rids: list of recurrence-ids for components to add to, or C{None} to add to all.
        @type rids: C{str} or C{None}
        @param content_type: the MIME media type/subtype of the attachment
        @type content_type: L{MimeType}
        @param filename: the name for the attachment
        @type filename: C{str}
        @param stream: the stream to read attachment data from
        @type stream: L{IStream}
        """

        # Check validity of request
        yield self._checkValidManagedAttachmentChange()

        # First write the data stream

        # We need to know the resource_ID of the home collection of the owner
        # (not sharee) of this event
        try:
            attachment = (yield self.createManagedAttachment())
            t = attachment.store(content_type, filename)
            yield readStream(stream, t.write)
        except Exception, e:
            self.log.error("Unable to store attachment: %s" % (e,))
            raise AttachmentStoreFailed
        yield t.loseConnection()

        if not self._dropboxID:
            self._dropboxID = str(uuid.uuid4())
        attachment._objectDropboxID = self._dropboxID

        # Now try and adjust the actual calendar data.
        # NB We need a copy of the original calendar data as implicit scheduling will need to compare that to
        # the original in order to detect changes that would case scheduling.
        calendar = (yield self.componentForUser())
        calendar = calendar.duplicate()

        attach, location = (yield attachment.attachProperty())
        if rids is None:
            calendar.addPropertyToAllComponents(attach)
        else:
            # TODO - per-recurrence attachments
            pass

        # Here is where we want to store data implicitly
        yield self._setComponentInternal(calendar, internal_state=ComponentUpdateState.ATTACHMENT_UPDATE)

        returnValue((attachment, location,))


    @inlineCallbacks
    def updateAttachment(self, managed_id, content_type, filename, stream):
        """
        Update a managed attachment in this calendar object.

        @param managed_id: the attachment's managed-id
        @type managed_id: C{str}
        @param content_type: the new MIME media type/subtype of the attachment
        @type content_type: L{MIMEType}
        @param filename: the new name for the attachment
        @type filename: C{str}
        @param stream: the stream to read new attachment data from
        @type stream: L{IStream}
        """

        # Check validity of request
        yield self._checkValidManagedAttachmentChange()

        # First check the supplied managed-id is associated with this resource
        cobjs = (yield ManagedAttachment.referencesTo(self._txn, managed_id))
        if self._resourceID not in cobjs:
            raise AttachmentStoreValidManagedID

        # Next write the data stream to existing attachment

        # We need to know the resource_ID of the home collection of the owner
        # (not sharee) of this event
        try:
            # Check that this is a proper update
            oldattachment = (yield self.attachmentWithManagedID(managed_id))
            if oldattachment is None:
                self.log.error("Missing managed attachment even though ATTACHMENT_CALENDAR_OBJECT indicates it is present: %s" % (managed_id,))
                raise AttachmentStoreFailed

            # We actually create a brand new attachment object for the update, but with the same managed-id. That way, other resources
            # referencing the old attachment data will still see that.
            attachment = (yield self.updateManagedAttachment(managed_id, oldattachment))
            t = attachment.store(content_type, filename)
            yield readStream(stream, t.write)
        except Exception, e:
            self.log.error("Unable to store attachment: %s" % (e,))
            raise AttachmentStoreFailed
        yield t.loseConnection()

        # Now try and adjust the actual calendar data.
        # NB We need a copy of the original calendar data as implicit scheduling will need to compare that to
        # the original in order to detect changes that would case scheduling.
        calendar = (yield self.componentForUser())
        calendar = calendar.duplicate()

        attach, location = (yield attachment.attachProperty())
        calendar.replaceAllPropertiesWithParameterMatch(attach, "MANAGED-ID", managed_id)

        # Here is where we want to store data implicitly
        yield self._setComponentInternal(calendar, internal_state=ComponentUpdateState.ATTACHMENT_UPDATE)

        returnValue((attachment, location,))


    @inlineCallbacks
    def removeAttachment(self, rids, managed_id):
        """
        Remove a managed attachment from this calendar object.

        @param rids: list of recurrence-ids for components to add to, or C{None} to add to all.
        @type rids: C{str} or C{None}
        @param managed_id: the attachment's managed-id
        @type managed_id: C{str}
        """

        # Check validity of request
        yield self._checkValidManagedAttachmentChange()

        # First check the supplied managed-id is associated with this resource
        cobjs = (yield ManagedAttachment.referencesTo(self._txn, managed_id))
        if self._resourceID not in cobjs:
            raise AttachmentStoreValidManagedID

        # Now try and adjust the actual calendar data.
        # NB We need a copy of the original calendar data as implicit scheduling will need to compare that to
        # the original in order to detect changes that would case scheduling.
        all_removed = False
        calendar = (yield self.componentForUser())
        calendar = calendar.duplicate()

        if rids is None:
            calendar.removeAllPropertiesWithParameterMatch("ATTACH", "MANAGED-ID", managed_id)
            all_removed = True
        else:
            # TODO: per-recurrence removal
            pass

        # Here is where we want to store data implicitly
        yield self._setComponentInternal(calendar, internal_state=ComponentUpdateState.ATTACHMENT_UPDATE)

        # Remove it - this will take care of actually removing it from the store if there are
        # no more references to the attachment
        if all_removed:
            yield self.removeManagedAttachmentWithID(managed_id)


    @inlineCallbacks
    def convertAttachments(self, oldattachment, newattachment):
        """
        Convert ATTACH properties in the calendar data from a dropbox attachment to a managed attachment.
        This is only used when migrating from dropbox to managed attachments. The ATTACH/ATTACH_CALENDAR_OBJECT
        DB tables have already been updated to reflect the new managed attachment entry, however the CALENDAR_OBJECT.
        DROPBOX_ID column has not.

        @param oldattachment: the old dropbox attachment being converted
        @type oldattachment: L{DropBoxAttachment}
        @param newattachment: the new managed attachment
        @type newattachment: L{ManagedAttachment}
        """

        # Scan each component looking for an ATTACH matching the old dropbox, remove
        # that and add a new managed ATTACH property
        cal = (yield self.component())
        for component in cal.subcomponents():
            attachments = component.properties("ATTACH")
            removed = False
            for attachment in tuple(attachments):
                if attachment.value().endswith("/dropbox/%s/%s" % (
                    urllib.quote(oldattachment.dropboxID()),
                    urllib.quote(oldattachment.name()),
                )):
                    component.removeProperty(attachment)
                    removed = True
            if removed:
                attach, _ignore_location = (yield newattachment.attachProperty())
                component.addProperty(attach)
            component.removeAllPropertiesWithName("X-APPLE-DROPBOX")

        # Write the component back (and no need to re-index as we have not
        # changed any timing properties in the calendar data).
        cal.noInstanceIndexing = True
        yield self._setComponentInternal(cal, internal_state=ComponentUpdateState.RAW)


    @inlineCallbacks
    def createManagedAttachment(self):

        # We need to know the resource_ID of the home collection of the owner
        # (not sharee) of this event
        ownerHomeID = (yield self._parentCollection.ownerHome().id())
        managedID = str(uuid.uuid4())
        returnValue((
            yield ManagedAttachment.create(
                self._txn, managedID, ownerHomeID, self._resourceID,
            )
        ))


    @inlineCallbacks
    def updateManagedAttachment(self, managedID, oldattachment):

        # We need to know the resource_ID of the home collection of the owner
        # (not sharee) of this event
        ownerHomeID = (yield self._parentCollection.ownerHome().id())
        returnValue((
            yield ManagedAttachment.update(
                self._txn, managedID, ownerHomeID, self._resourceID, oldattachment._attachmentID,
            )
        ))


    def attachmentWithManagedID(self, managed_id):
        return ManagedAttachment.load(self._txn, self._resourceID, managed_id)


    @inlineCallbacks
    def removeManagedAttachmentWithID(self, managed_id):
        attachment = (yield self.attachmentWithManagedID(managed_id))
        if attachment is not None:
            yield attachment.removeFromResource(self._resourceID)


    @inlineCallbacks
    def managedAttachmentList(self):
        """
        Get a list of managed attachments where the names returned are for the last path segment
        of the attachment URI.
        """
        at = schema.ATTACHMENT
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        rows = (yield Select(
            [attco.MANAGED_ID, at.PATH, ],
            From=attco.join(at, attco.ATTACHMENT_ID == at.ATTACHMENT_ID),
            Where=attco.CALENDAR_OBJECT_RESOURCE_ID == Parameter("resourceID")
        ).on(self._txn, resourceID=self._resourceID))
        returnValue([ManagedAttachment.lastSegmentOfUriPath(row[0], row[1]) for row in rows])


    @inlineCallbacks
    def managedAttachmentRetrieval(self, name):
        """
        Return a managed attachment specified by the last path segment of the attachment URI.
        """

        # Scan all the associated attachments for the one that matches
        at = schema.ATTACHMENT
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        rows = (yield Select(
            [attco.MANAGED_ID, at.PATH, ],
            From=attco.join(at, attco.ATTACHMENT_ID == at.ATTACHMENT_ID),
            Where=attco.CALENDAR_OBJECT_RESOURCE_ID == Parameter("resourceID")
        ).on(self._txn, resourceID=self._resourceID))

        for att_managed_id, att_name in rows:
            if ManagedAttachment.lastSegmentOfUriPath(att_managed_id, att_name) == name:
                attachment = (yield self.attachmentWithManagedID(att_managed_id))
                returnValue(attachment)
        returnValue(None)


    @inlineCallbacks
    def createAttachmentWithName(self, name):

        # We need to know the resource_ID of the home collection of the owner
        # (not sharee) of this event
        ownerHomeID = (yield self._parentCollection.ownerHome().id())
        dropboxID = (yield self.dropboxID())
        returnValue((
            yield DropBoxAttachment.create(
                self._txn, dropboxID, name, ownerHomeID,
            )
        ))


    @inlineCallbacks
    def removeAttachmentWithName(self, name):
        attachment = (yield self.attachmentWithName(name))
        yield attachment.remove()


    def attachmentWithName(self, name):
        return DropBoxAttachment.load(self._txn, self._dropboxID, name)


    def attendeesCanManageAttachments(self):
        return self._attachment == _ATTACHMENTS_MODE_WRITE

    dropboxID = dropboxIDFromCalendarObject

    _attachmentsQuery = Select(
        [schema.ATTACHMENT.PATH],
        From=schema.ATTACHMENT,
        Where=schema.ATTACHMENT.DROPBOX_ID == Parameter('dropboxID')
    )


    @inlineCallbacks
    def attachments(self):
        if self._dropboxID:
            rows = yield self._attachmentsQuery.on(self._txn,
                                                   dropboxID=self._dropboxID)
            result = []
            for row in rows:
                result.append((yield self.attachmentWithName(row[0])))
            returnValue(result)
        else:
            returnValue(())


    def initPropertyStore(self, props):
        # Setup peruser special properties
        props.setSpecialProperties(
            (
            ),
            (
                PropertyName.fromElement(customxml.ScheduleChanges),
            ),
        )


    # IDataStoreObject
    def contentType(self):
        """
        The content type of Calendar objects is text/calendar.
        """
        return MimeType.fromString("text/calendar; charset=utf-8")


    @inlineCallbacks
    def checkSplit(self):
        """
        Determine if the calendar data needs to be split, and enqueue a split work item if needed.
        """

        if config.Scheduling.Options.Splitting.Enabled:
            will = (yield self.willSplit())
            if will:
                notBefore = datetime.datetime.utcnow() + datetime.timedelta(seconds=config.Scheduling.Options.Splitting.Delay)
                work = (yield self._txn.enqueue(CalendarObject.CalendarObjectSplitterWork, resourceID=self._resourceID, notBefore=notBefore))
                if not hasattr(self, "_workItems"):
                    self._workItems = []
                self._workItems.append(work)


    @inlineCallbacks
    def willSplit(self):
        """
        Determine if the calendar data needs to be split as per L{iCalSplitter}.
        """

        splitter = iCalSplitter(config.Scheduling.Options.Splitting.Size, config.Scheduling.Options.Splitting.PastDays)
        ical = (yield self.component())
        will_split = splitter.willSplit(ical)
        returnValue(will_split)


    @inlineCallbacks
    def split(self):
        """
        Split this and all matching UID calendar objects as per L{iCalSplitter}.
        """

        # First job is to grab a UID lock on this entire series of events
        yield NamedLock.acquire(self._txn, "ImplicitUIDLock:%s" % (hashlib.md5(self._uid).hexdigest(),))

        # Find all other calendar objects on this server with the same UID
        resources = (yield CalendarStoreFeatures(self._txn._store).calendarObjectsWithUID(self._txn, self._uid))

        splitter = iCalSplitter(config.Scheduling.Options.Splitting.Size, config.Scheduling.Options.Splitting.PastDays)

        # Determine the recurrence-id of the split and create a new UID for it
        calendar = (yield self.component())
        rid = splitter.whereSplit(calendar)
        newUID = str(uuid.uuid4())

        # Now process this resource, but do implicit scheduling for attendees not hosted on this server.
        # We need to do this before processing attendee copies.
        calendar_old = splitter.split(calendar, rid=rid, newUID=newUID)

        # Store changed data
        if calendar.mainType() is not None:
            yield self._setComponentInternal(calendar, internal_state=ComponentUpdateState.SPLIT)
        else:
            yield self._removeInternal(internal_state=ComponentUpdateState.SPLIT)
        if calendar_old.mainType() is not None:
            yield self.calendar()._createCalendarObjectWithNameInternal("%s.ics" % (newUID,), calendar_old, ComponentUpdateState.SPLIT)

        # Split each one - but not this resource
        for resource in resources:
            if resource._resourceID == self._resourceID:
                continue
            ical = (yield resource.component())
            ical_old = splitter.split(ical, rid=rid, newUID=newUID)

            # Store changed data
            if ical.mainType() is not None:
                yield resource._setComponentInternal(ical, internal_state=ComponentUpdateState.SPLIT)
            else:
                # The split removed all components from this object - remove it
                yield resource._removeInternal(internal_state=ComponentUpdateState.SPLIT)

            # Create a new resource and store its data (but not if the parent is "inbox", or if it is empty)
            if not resource.calendar().isInbox() and ical_old.mainType() is not None:
                yield resource.calendar()._createCalendarObjectWithNameInternal("%s.ics" % (newUID,), ical_old, ComponentUpdateState.SPLIT)

        # TODO: scheduling currently turned off until we figure out how to properly do that

        returnValue(newUID)


    class CalendarObjectSplitterWork(WorkItem, fromTable(schema.CALENDAR_OBJECT_SPLITTER_WORK)):

        group = property(lambda self: "CalendarObjectSplitterWork:%s" % (self.resourceID,))

        @inlineCallbacks
        def doWork(self):

            log.debug("Splitting calendar object with resource-id: {rid}", rid=self.resourceID)

            # Delete all other work items with the same resourceID
            yield Delete(From=self.table,
                         Where=self.table.RESOURCE_ID == self.resourceID
                        ).on(self.transaction)

            # Get the actual owned calendar object with this ID
            cobj = (yield CalendarStoreFeatures(self.transaction._store).calendarObjectWithID(self.transaction, self.resourceID))
            if cobj is None:
                returnValue(None)

            # Check it is still split-able
            will = (yield  cobj.willSplit())

            if will:
                # Now do the spitting
                yield cobj.split()



class AttachmentStorageTransport(StorageTransportBase):

    _TEMPORARY_UPLOADS_DIRECTORY = "Temporary"

    def __init__(self, attachment, contentType, dispositionName, creating=False):
        super(AttachmentStorageTransport, self).__init__(
            attachment, contentType, dispositionName)

        fileDescriptor, fileName = self._temporaryFile()
        # Wrap the file descriptor in a file object we can write to
        self._file = os.fdopen(fileDescriptor, "w")
        self._path = CachingFilePath(fileName)
        self._hash = hashlib.md5()
        self._creating = creating

        self._txn.postAbort(self.aborted)


    def _temporaryFile(self):
        """
        Returns a (file descriptor, absolute path) tuple for a temporary file within
        the Attachments/Temporary directory (creating the Temporary subdirectory
        if it doesn't exist).  It is the caller's responsibility to remove the
        file.
        """
        attachmentRoot = self._txn._store.attachmentsPath
        tempUploadsPath = attachmentRoot.child(self._TEMPORARY_UPLOADS_DIRECTORY)
        if not tempUploadsPath.exists():
            tempUploadsPath.createDirectory()
        return tempfile.mkstemp(dir=tempUploadsPath.path)


    @property
    def _txn(self):
        return self._attachment._txn


    def aborted(self):
        """
        Transaction aborted - clean up temp files.
        """
        if self._path.exists():
            self._path.remove()


    def write(self, data):
        if isinstance(data, buffer):
            data = str(data)
        self._file.write(data)
        self._hash.update(data)


    @inlineCallbacks
    def loseConnection(self):

        # FIXME: this should be synchronously accessible; IAttachment should
        # have a method for getting its parent just as CalendarObject/Calendar
        # do.

        # FIXME: If this method isn't called, the transaction should be
        # prevented from committing successfully.  It's not valid to have an
        # attachment that doesn't point to a real file.

        home = (yield self._txn.calendarHomeWithResourceID(
                    self._attachment._ownerHomeID))

        oldSize = self._attachment.size()
        newSize = self._file.tell()
        self._file.close()
        allowed = home.quotaAllowedBytes()
        if allowed is not None and allowed < ((yield home.quotaUsedBytes())
                                              + (newSize - oldSize)):
            self._path.remove()
            if self._creating:
                yield self._attachment._internalRemove()
            raise QuotaExceeded()

        self._path.moveTo(self._attachment._path)

        yield self._attachment.changed(
            self._contentType,
            self._dispositionName,
            self._hash.hexdigest(),
            newSize
        )

        if home:
            # Adjust quota
            yield home.adjustQuotaUsedBytes(self._attachment.size() - oldSize)

            # Send change notification to home
            yield home.notifyChanged()



def sqltime(value):
    return datetimeMktime(parseSQLTimestamp(value))



class Attachment(object):

    implements(IAttachment)

    def __init__(self, txn, a_id, dropboxID, name, ownerHomeID=None, justCreated=False):
        self._txn = txn
        self._attachmentID = a_id
        self._ownerHomeID = ownerHomeID
        self._dropboxID = dropboxID
        self._contentType = None
        self._size = 0
        self._md5 = None
        self._created = None
        self._modified = None
        self._name = name
        self._justCreated = justCreated


    def __repr__(self):
        return "<%s: %s>" % (self.__class__.__name__, self._attachmentID)


    def _attachmentPathRoot(self):
        return self._txn._store.attachmentsPath


    @inlineCallbacks
    def initFromStore(self):
        """
        Execute necessary SQL queries to retrieve attributes.

        @return: C{True} if this attachment exists, C{False} otherwise.
        """
        att = schema.ATTACHMENT
        if self._dropboxID:
            where = (att.DROPBOX_ID == self._dropboxID).And(
                   att.PATH == self._name)
        else:
            where = (att.ATTACHMENT_ID == self._attachmentID)
        rows = (yield Select(
            [
                att.ATTACHMENT_ID,
                att.DROPBOX_ID,
                att.CALENDAR_HOME_RESOURCE_ID,
                att.CONTENT_TYPE,
                att.SIZE,
                att.MD5,
                att.CREATED,
                att.MODIFIED,
                att.PATH,
            ],
            From=att,
            Where=where
        ).on(self._txn))

        if not rows:
            returnValue(None)

        row_iter = iter(rows[0])
        self._attachmentID = row_iter.next()
        self._dropboxID = row_iter.next()
        self._ownerHomeID = row_iter.next()
        self._contentType = MimeType.fromString(row_iter.next())
        self._size = row_iter.next()
        self._md5 = row_iter.next()
        self._created = sqltime(row_iter.next())
        self._modified = sqltime(row_iter.next())
        self._name = row_iter.next()

        returnValue(self)


    def dropboxID(self):
        return self._dropboxID


    def isManaged(self):
        return self._dropboxID == "."


    def name(self):
        return self._name


    def properties(self):
        pass  # stub


    def store(self, contentType, dispositionName=None):
        if not self._name:
            self._name = dispositionName
        return AttachmentStorageTransport(self, contentType, dispositionName, self._justCreated)


    def retrieve(self, protocol):
        return AttachmentRetrievalTransport(self._path).start(protocol)


    def changed(self, contentType, dispositionName, md5, size):
        raise NotImplementedError

    _removeStatement = Delete(
        From=schema.ATTACHMENT,
        Where=(schema.ATTACHMENT.ATTACHMENT_ID == Parameter("attachmentID"))
    )


    @inlineCallbacks
    def remove(self):
        oldSize = self._size
        self._txn.postCommit(self.removePaths)
        yield self._internalRemove()
        # Adjust quota
        home = (yield self._txn.calendarHomeWithResourceID(self._ownerHomeID))
        if home:
            yield home.adjustQuotaUsedBytes(-oldSize)

            # Send change notification to home
            yield home.notifyChanged()


    def removePaths(self):
        """
        Remove the actual file and up to attachment parent directory if empty.
        """
        self._path.remove()
        parent = self._path.parent()
        toppath = self._attachmentPathRoot().path
        while parent.path != toppath:
            if len(parent.listdir()) == 0:
                parent.remove()
                parent = parent.parent()
            else:
                break


    def _internalRemove(self):
        """
        Just delete the row; don't do any accounting / bookkeeping.  (This is
        for attachments that have failed to be created due to errors during
        storage.)
        """
        return self._removeStatement.on(self._txn, attachmentID=self._attachmentID)


    @classmethod
    @inlineCallbacks
    def removedHome(cls, txn, homeID):
        """
        A calendar home is being removed so all of its attachments must go too. When removing,
        we don't care about quota adjustment as there will be no quota once the home is removed.

        TODO: this needs to be transactional wrt the actual file deletes.
        """
        att = schema.ATTACHMENT
        attco = schema.ATTACHMENT_CALENDAR_OBJECT

        rows = (yield Select(
            [att.ATTACHMENT_ID, att.DROPBOX_ID, ],
            From=att,
            Where=(
                att.CALENDAR_HOME_RESOURCE_ID == homeID
            ),
        ).on(txn))

        for attachmentID, dropboxID in rows:
            if dropboxID:
                attachment = DropBoxAttachment(txn, attachmentID, None, None)
            else:
                attachment = ManagedAttachment(txn, attachmentID, None, None)
            attachment = (yield attachment.initFromStore())
            if attachment._path.exists():
                attachment.removePaths()

        yield Delete(
            From=attco,
            Where=(
                attco.ATTACHMENT_ID.In(Select(
                    [att.ATTACHMENT_ID, ],
                    From=att,
                    Where=(
                        att.CALENDAR_HOME_RESOURCE_ID == homeID
                    ),
                ))
            ),
        ).on(txn)

        yield Delete(
            From=att,
            Where=(
                att.CALENDAR_HOME_RESOURCE_ID == homeID
            ),
        ).on(txn)


    # IDataStoreObject
    def contentType(self):
        return self._contentType


    def md5(self):
        return self._md5


    def size(self):
        return self._size


    def created(self):
        return self._created


    def modified(self):
        return self._modified



class DropBoxAttachment(Attachment):

    @classmethod
    @inlineCallbacks
    def create(cls, txn, dropboxID, name, ownerHomeID):
        """
        Create a new Attachment object.

        @param txn: The transaction to use
        @type txn: L{CommonStoreTransaction}
        @param dropboxID: the identifier for the attachment (dropbox id or managed id)
        @type dropboxID: C{str}
        @param name: the name of the attachment
        @type name: C{str}
        @param ownerHomeID: the resource-id of the home collection of the attachment owner
        @type ownerHomeID: C{int}
        """

        # If store has already migrated to managed attachments we will prevent creation of dropbox attachments
        dropbox = (yield txn.store().dropboxAllowed(txn))
        if not dropbox:
            raise AttachmentDropboxNotAllowed

        # Now create the DB entry
        att = schema.ATTACHMENT
        rows = (yield Insert({
            att.CALENDAR_HOME_RESOURCE_ID : ownerHomeID,
            att.DROPBOX_ID                : dropboxID,
            att.CONTENT_TYPE              : "",
            att.SIZE                      : 0,
            att.MD5                       : "",
            att.PATH                      : name,
        }, Return=(att.ATTACHMENT_ID, att.CREATED, att.MODIFIED)).on(txn))

        row_iter = iter(rows[0])
        a_id = row_iter.next()
        created = sqltime(row_iter.next())
        modified = sqltime(row_iter.next())

        attachment = cls(txn, a_id, dropboxID, name, ownerHomeID, True)
        attachment._created = created
        attachment._modified = modified

        # File system paths need to exist
        try:
            attachment._path.parent().makedirs()
        except:
            pass

        returnValue(attachment)


    @classmethod
    @inlineCallbacks
    def load(cls, txn, dropboxID, name):
        attachment = cls(txn, None, dropboxID, name)
        attachment = (yield attachment.initFromStore())
        returnValue(attachment)


    @property
    def _path(self):
        # Use directory hashing scheme based on MD5 of dropboxID
        hasheduid = hashlib.md5(self._dropboxID).hexdigest()
        attachmentRoot = self._attachmentPathRoot().child(hasheduid[0:2]).child(hasheduid[2:4]).child(hasheduid)
        return attachmentRoot.child(self.name())


    @classmethod
    @inlineCallbacks
    def resourceRemoved(cls, txn, resourceID, dropboxID):
        """
        Remove all attachments referencing the specified resource.
        """

        # See if any other resources still reference this dropbox ID
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [co.RESOURCE_ID, ],
            From=co,
            Where=(co.DROPBOX_ID == dropboxID).And(
                co.RESOURCE_ID != resourceID)
        ).on(txn))

        if not rows:
            # Find each attachment with matching dropbox ID
            att = schema.ATTACHMENT
            rows = (yield Select(
                [att.PATH],
                From=att,
                Where=(att.DROPBOX_ID == dropboxID)
            ).on(txn))
            for name in rows:
                name = name[0]
                attachment = yield cls.load(txn, dropboxID, name)
                yield attachment.remove()


    @inlineCallbacks
    def changed(self, contentType, dispositionName, md5, size):
        """
        Dropbox attachments never change their path - ignore dispositionName.
        """

        self._contentType = contentType
        self._md5 = md5
        self._size = size

        att = schema.ATTACHMENT
        self._created, self._modified = map(
            sqltime,
            (yield Update(
                {
                    att.CONTENT_TYPE    : generateContentType(self._contentType),
                    att.SIZE            : self._size,
                    att.MD5             : self._md5,
                    att.MODIFIED        : utcNowSQL,
                },
                Where=(att.ATTACHMENT_ID == self._attachmentID),
                Return=(att.CREATED, att.MODIFIED)).on(self._txn))[0]
        )


    @inlineCallbacks
    def convertToManaged(self):
        """
        Convert this dropbox attachment into a managed attachment by updating the
        database and returning a new ManagedAttachment object that does not reference
        any calendar object. Referencing will be added later.

        @return: the managed attachment object
        @rtype: L{ManagedAttachment}
        """

        # Change the DROPBOX_ID to a single "." to indicate a managed attachment.
        att = schema.ATTACHMENT
        (yield Update(
            {att.DROPBOX_ID    : ".", },
            Where=(att.ATTACHMENT_ID == self._attachmentID),
        ).on(self._txn))

        # Create an "orphaned" ManagedAttachment that points to the updated data but without
        # an actual managed-id (which only exists when there is a reference to a calendar object).
        mattach = (yield ManagedAttachment.load(self._txn, None, None, attachmentID=self._attachmentID))
        mattach._managedID = str(uuid.uuid4())
        if mattach is None:
            raise AttachmentMigrationFailed

        # Then move the file on disk from the old path to the new one
        mattach._path.parent().makedirs()
        oldpath = self._path
        newpath = mattach._path
        oldpath.moveTo(newpath)

        returnValue(mattach)



class ManagedAttachment(Attachment):
    """
    Managed attachments are ones that the server is in total control of. Clients do POSTs on calendar objects
    to store the attachment data and have ATTACH properties added, updated or remove from the calendar objects.
    Each ATTACH property in a calendar object has a MANAGED-ID iCalendar parameter that is used in the POST requests
    to target a specific attachment. The MANAGED-ID values are unique to each calendar object resource, though
    multiple calendar object resources can point to the same underlying attachment as there is a separate database
    table that maps calendar objects/managed-ids to actual attachments.
    """

    @classmethod
    @inlineCallbacks
    def _create(cls, txn, managedID, ownerHomeID):
        """
        Create a new managed Attachment object.

        @param txn: The transaction to use
        @type txn: L{CommonStoreTransaction}
        @param managedID: the identifier for the attachment
        @type managedID: C{str}
        @param ownerHomeID: the resource-id of the home collection of the attachment owner
        @type ownerHomeID: C{int}
        """

        # Now create the DB entry
        att = schema.ATTACHMENT
        rows = (yield Insert({
            att.CALENDAR_HOME_RESOURCE_ID : ownerHomeID,
            att.DROPBOX_ID                : ".",
            att.CONTENT_TYPE              : "",
            att.SIZE                      : 0,
            att.MD5                       : "",
            att.PATH                      : "",
        }, Return=(att.ATTACHMENT_ID, att.CREATED, att.MODIFIED)).on(txn))

        row_iter = iter(rows[0])
        a_id = row_iter.next()
        created = sqltime(row_iter.next())
        modified = sqltime(row_iter.next())

        attachment = cls(txn, a_id, ".", None, ownerHomeID, True)
        attachment._managedID = managedID
        attachment._created = created
        attachment._modified = modified

        # File system paths need to exist
        try:
            attachment._path.parent().makedirs()
        except:
            pass

        returnValue(attachment)


    @classmethod
    @inlineCallbacks
    def create(cls, txn, managedID, ownerHomeID, referencedBy):
        """
        Create a new Attachment object.

        @param txn: The transaction to use
        @type txn: L{CommonStoreTransaction}
        @param managedID: the identifier for the attachment
        @type managedID: C{str}
        @param ownerHomeID: the resource-id of the home collection of the attachment owner
        @type ownerHomeID: C{int}
        @param referencedBy: the resource-id of the calendar object referencing the attachment
        @type referencedBy: C{int}
        """

        # Now create the DB entry
        attachment = (yield cls._create(txn, managedID, ownerHomeID))
        attachment._objectResourceID = referencedBy

        # Create the attachment<->calendar object relationship for managed attachments
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        yield Insert({
            attco.ATTACHMENT_ID               : attachment._attachmentID,
            attco.MANAGED_ID                  : attachment._managedID,
            attco.CALENDAR_OBJECT_RESOURCE_ID : attachment._objectResourceID,
        }).on(txn)

        returnValue(attachment)


    @classmethod
    @inlineCallbacks
    def update(cls, txn, oldManagedID, ownerHomeID, referencedBy, oldAttachmentID):
        """
        Create a new Attachment object.

        @param txn: The transaction to use
        @type txn: L{CommonStoreTransaction}
        @param oldManagedID: the identifier for the original attachment
        @type oldManagedID: C{str}
        @param ownerHomeID: the resource-id of the home collection of the attachment owner
        @type ownerHomeID: C{int}
        @param referencedBy: the resource-id of the calendar object referencing the attachment
        @type referencedBy: C{int}
        @param oldAttachmentID: the attachment-id of the existing attachment being updated
        @type oldAttachmentID: C{int}
        """

        # Now create the DB entry with a new managed-ID
        managed_id = str(uuid.uuid4())
        attachment = (yield cls._create(txn, managed_id, ownerHomeID))
        attachment._objectResourceID = referencedBy

        # Update the attachment<->calendar object relationship for managed attachments
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        yield Update(
            {
                attco.ATTACHMENT_ID    : attachment._attachmentID,
                attco.MANAGED_ID       : attachment._managedID,
            },
            Where=(attco.MANAGED_ID == oldManagedID).And(
                attco.CALENDAR_OBJECT_RESOURCE_ID == attachment._objectResourceID
            ),
        ).on(txn)

        # Now check whether old attachmentID is still referenced - if not delete it
        rows = (yield Select(
            [attco.ATTACHMENT_ID, ],
            From=attco,
            Where=(attco.ATTACHMENT_ID == oldAttachmentID),
        ).on(txn))
        aids = [row[0] for row in rows] if rows is not None else ()
        if len(aids) == 0:
            oldattachment = ManagedAttachment(txn, oldAttachmentID, None, None)
            oldattachment = (yield oldattachment.initFromStore())
            yield oldattachment.remove()

        returnValue(attachment)


    @classmethod
    @inlineCallbacks
    def load(cls, txn, referencedID, managedID, attachmentID=None):
        """
        Load a ManagedAttachment via either its managedID or attachmentID.
        """

        if managedID:
            attco = schema.ATTACHMENT_CALENDAR_OBJECT
            where = (attco.MANAGED_ID == managedID)
            if referencedID is not None:
                where = where.And(attco.CALENDAR_OBJECT_RESOURCE_ID == referencedID)
            rows = (yield Select(
                [attco.ATTACHMENT_ID, ],
                From=attco,
                Where=where,
            ).on(txn))
            if len(rows) == 0:
                returnValue(None)
            elif referencedID is not None and len(rows) != 1:
                raise AttachmentStoreValidManagedID
            attachmentID = rows[0][0]

        attachment = cls(txn, attachmentID, None, None)
        attachment = (yield attachment.initFromStore())
        attachment._managedID = managedID
        attachment._objectResourceID = referencedID
        returnValue(attachment)


    @classmethod
    @inlineCallbacks
    def referencesTo(cls, txn, managedID):
        """
        Find all the calendar object resourceIds referenced by this supplied managed-id.
        """
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        rows = (yield Select(
            [attco.CALENDAR_OBJECT_RESOURCE_ID, ],
            From=attco,
            Where=(attco.MANAGED_ID == managedID),
        ).on(txn))
        cobjs = set([row[0] for row in rows]) if rows is not None else set()
        returnValue(cobjs)


    @classmethod
    @inlineCallbacks
    def usedManagedID(cls, txn, managedID):
        """
        Return the "owner" home and referencing resource is, and UID for a managed-id.
        """
        att = schema.ATTACHMENT
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        co = schema.CALENDAR_OBJECT
        rows = (yield Select(
            [
                att.CALENDAR_HOME_RESOURCE_ID,
                attco.CALENDAR_OBJECT_RESOURCE_ID,
                co.ICALENDAR_UID,
            ],
            From=att.join(
                attco, att.ATTACHMENT_ID == attco.ATTACHMENT_ID, "left outer"
            ).join(co, co.RESOURCE_ID == attco.CALENDAR_OBJECT_RESOURCE_ID),
            Where=(attco.MANAGED_ID == managedID),
        ).on(txn))
        returnValue(rows)


    @classmethod
    @inlineCallbacks
    def resourceRemoved(cls, txn, resourceID):
        """
        Remove all attachments referencing the specified resource.
        """

        # Find all reference attachment-ids and dereference
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        rows = (yield Select(
            [attco.MANAGED_ID, ],
            From=attco,
            Where=(attco.CALENDAR_OBJECT_RESOURCE_ID == resourceID),
        ).on(txn))
        mids = set([row[0] for row in rows]) if rows is not None else set()
        for managedID in mids:
            attachment = (yield ManagedAttachment.load(txn, resourceID, managedID))
            (yield attachment.removeFromResource(resourceID))


    @classmethod
    @inlineCallbacks
    def copyManagedID(cls, txn, managedID, referencedBy):
        """
        Associate an existing attachment with the new resource.
        """

        # Find the associated attachment-id and insert new reference
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        aid = (yield Select(
            [attco.ATTACHMENT_ID, ],
            From=attco,
            Where=(attco.MANAGED_ID == managedID),
        ).on(txn))[0][0]

        yield Insert({
            attco.ATTACHMENT_ID               : aid,
            attco.MANAGED_ID                  : managedID,
            attco.CALENDAR_OBJECT_RESOURCE_ID : referencedBy,
        }).on(txn)


    def managedID(self):
        return self._managedID


    @inlineCallbacks
    def objectResource(self):
        """
        Return the calendar object resource associated with this attachment.
        """

        home = (yield self._txn.calendarHomeWithResourceID(self._ownerHomeID))
        obj = (yield home.objectResourceWithID(self._objectResourceID))
        returnValue(obj)


    @property
    def _path(self):
        # Use directory hashing scheme based on MD5 of attachmentID
        hasheduid = hashlib.md5(str(self._attachmentID)).hexdigest()
        return self._attachmentPathRoot().child(hasheduid[0:2]).child(hasheduid[2:4]).child(hasheduid)


    @inlineCallbacks
    def location(self):
        """
        Return the URI location of the attachment.
        """
        if not hasattr(self, "_ownerName"):
            home = (yield self._txn.calendarHomeWithResourceID(self._ownerHomeID))
            self._ownerName = home.name()
        if not hasattr(self, "_objectDropboxID"):
            if not hasattr(self, "_objectResource"):
                self._objectResource = (yield self.objectResource())
            self._objectDropboxID = self._objectResource._dropboxID

        fname = self.lastSegmentOfUriPath(self._managedID, self._name)
        location = self._txn._store.attachmentsURIPattern % {
            "home": self._ownerName,
            "dropbox_id": urllib.quote(self._objectDropboxID),
            "name": urllib.quote(fname),
        }
        returnValue(location)


    @classmethod
    def lastSegmentOfUriPath(cls, managed_id, name):
        splits = name.rsplit(".", 1)
        fname = splits[0]
        suffix = splits[1] if len(splits) == 2 else "unknown"
        return "%s-%s.%s" % (fname, managed_id[:8], suffix)


    @inlineCallbacks
    def changed(self, contentType, dispositionName, md5, size):
        """
        Always update name to current disposition name.
        """

        self._contentType = contentType
        self._name = dispositionName
        self._md5 = md5
        self._size = size
        att = schema.ATTACHMENT
        self._created, self._modified = map(
            sqltime,
            (yield Update(
                {
                    att.CONTENT_TYPE    : generateContentType(self._contentType),
                    att.SIZE            : self._size,
                    att.MD5             : self._md5,
                    att.MODIFIED        : utcNowSQL,
                    att.PATH            : self._name,
                },
                Where=(att.ATTACHMENT_ID == self._attachmentID),
                Return=(att.CREATED, att.MODIFIED)).on(self._txn))[0]
        )


    @inlineCallbacks
    def newReference(self, resourceID):
        """
        Create a new reference of this attachment to the supplied calendar object resource id, and
        return a ManagedAttachment for the new reference.

        @param resourceID: the resource id to reference
        @type resourceID: C{int}

        @return: the new managed attachment
        @rtype: L{ManagedAttachment}
        """

        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        yield Insert({
            attco.ATTACHMENT_ID               : self._attachmentID,
            attco.MANAGED_ID                  : self._managedID,
            attco.CALENDAR_OBJECT_RESOURCE_ID : resourceID,
        }).on(self._txn)

        mattach = (yield ManagedAttachment.load(self._txn, resourceID, self._managedID))
        returnValue(mattach)


    @inlineCallbacks
    def removeFromResource(self, resourceID):

        # Delete the reference
        attco = schema.ATTACHMENT_CALENDAR_OBJECT
        yield Delete(
            From=attco,
            Where=(attco.ATTACHMENT_ID == self._attachmentID).And(
                   attco.CALENDAR_OBJECT_RESOURCE_ID == resourceID),
        ).on(self._txn)

        # References still exist - if not remove actual attachment
        rows = (yield Select(
            [attco.CALENDAR_OBJECT_RESOURCE_ID, ],
            From=attco,
            Where=(attco.ATTACHMENT_ID == self._attachmentID),
        ).on(self._txn))
        if len(rows) == 0:
            yield self.remove()


    @inlineCallbacks
    def attachProperty(self):
        """
        Return an iCalendar ATTACH property for this attachment.
        """
        attach = Property("ATTACH", "", valuetype=PyCalendarValue.VALUETYPE_URI)
        location = (yield self.updateProperty(attach))
        returnValue((attach, location,))


    @inlineCallbacks
    def updateProperty(self, attach):
        """
        Update an iCalendar ATTACH property for this attachment.
        """

        location = (yield self.location())

        attach.setParameter("MANAGED-ID", self.managedID())
        attach.setParameter("FMTTYPE", "%s/%s" % (self.contentType().mediaType, self.contentType().mediaSubtype))
        attach.setParameter("FILENAME", self.name())
        attach.setParameter("SIZE", str(self.size()))
        attach.setValue(location)

        returnValue(location)

Calendar._objectResourceClass = CalendarObject
