# -*- test-case-name: txcaldav.calendarstore.test.test_postgres -*-
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
PostgreSQL data store.
"""

__all__ = [
    "PostgresStore",
    "PostgresCalendarHome",
    "PostgresCalendar",
    "PostgresCalendarObject",
    "PostgresAddressBookHome",
    "PostgresAddressBook",
    "PostgresAddressBookObject",
]

import StringIO

from twisted.python import hashlib
from twistedcaldav.sharing import SharedCollectionRecord #@UnusedImport

from inspect import getargspec
from zope.interface.declarations import implements

from twisted.python.modules import getModule
from twisted.application.service import Service
from twisted.internet.interfaces import ITransport
from twisted.internet.error import ConnectionLost
from twisted.python.failure import Failure

from twext.web2.dav.element.rfc2518 import ResourceType

from txdav.idav import IDataStore, AlreadyFinishedError
from txdav.common.inotifications import (INotificationCollection,
    INotificationObject)

from txdav.common.icommondatastore import (
    ObjectResourceNameAlreadyExistsError, HomeChildNameAlreadyExistsError,
    NoSuchHomeChildError, NoSuchObjectResourceError)
from txcaldav.calendarstore.util import (validateCalendarComponent,
    validateAddressBookComponent, dropboxIDFromCalendarObject, SyncTokenHelper)


from txcaldav.icalendarstore import (ICalendarTransaction, ICalendarHome,
    ICalendar, ICalendarObject, IAttachment)
from txcarddav.iaddressbookstore import (IAddressBookTransaction,
    IAddressBookHome, IAddressBook, IAddressBookObject)
from txdav.propertystore.base import AbstractPropertyStore, PropertyName
from txdav.propertystore.none import PropertyStore

from twext.web2.http_headers import MimeType, generateContentType
from twext.web2.dav.element.parser import WebDAVDocument

from twext.python.vcomponent import VComponent
from twistedcaldav.vcard import Component as VCard
from twistedcaldav.sharing import Invite
from twistedcaldav.notifications import NotificationRecord
from twistedcaldav.query.sqlgenerator import sqlgenerator
from twistedcaldav.index import IndexedSearchException


v1_schema = getModule(__name__).filePath.sibling(
    "postgres_schema_v1.sql").getContent()


# FIXME: these constants are in the schema, and should probably be discovered
# from there somehow.

_BIND_STATUS_INVITED = 0
_BIND_STATUS_ACCEPTED = 1
_BIND_STATUS_DECLINED = 2
_BIND_STATUS_INVALID = 3

_ATTACHMENTS_MODE_WRITE = 1

_BIND_MODE_OWN = 0
_BIND_MODE_READ = 1
_BIND_MODE_WRITE = 2



def _getarg(argname, argspec, args, kw):
    """
    Get an argument from some arguments.

    @param argname: The name of the argument to retrieve.

    @param argspec: The result of L{inspect.getargspec}.

    @param args: positional arguments passed to the function specified by
        argspec.

    @param kw: keyword arguments passed to the function specified by
        argspec.

    @return: The value of the argument named by 'argname'.
    """
    argnames = argspec[0]
    try:
        argpos = argnames.index(argname)
    except ValueError:
        argpos = None
    if argpos is not None:
        if len(args) > argpos:
            return args[argpos]
    if argname in kw:
        return kw[argname]
    else:
        raise TypeError("could not find key argument %r in %r/%r (%r)" %
            (argname, args, kw, argpos)
        )



def memoized(keyArgument, memoAttribute):
    """
    Decorator which memoizes the result of a method on that method's instance.

    @param keyArgument: The name of the 'key' argument.

    @type keyArgument: C{str}

    @param memoAttribute: The name of the attribute on the instance which
        should be used for memoizing the result of this method; the attribute
        itself must be a dictionary.

    @type memoAttribute: C{str}
    """
    def decorate(thunk):
        spec = getargspec(thunk)
        def outer(*a, **kw):
            self = a[0]
            memo = getattr(self, memoAttribute)
            key = _getarg(keyArgument, spec, a, kw)
            if key in memo:
                return memo[key]
            result = thunk(*a, **kw)
            if result is not None:
                memo[key] = result
            return result
        return outer
    return decorate



class PropertyStore(AbstractPropertyStore):

    def __init__(self, peruser, defaultuser, txn, resourceID):
        super(PropertyStore, self).__init__(peruser, defaultuser)
        self._txn = txn
        self._resourceID = resourceID


    def _getitem_uid(self, key, uid):
        rows = self._txn.execSQL(
            "select VALUE from RESOURCE_PROPERTY where "
            "RESOURCE_ID = %s and NAME = %s and VIEWER_UID = %s",
            [self._resourceID, key.toString(), uid])
        if not rows:
            raise KeyError(key)
        return WebDAVDocument.fromString(rows[0][0]).root_element


    def _setitem_uid(self, key, value, uid):
        self._delitem_uid(key, uid)
        self._txn.execSQL(
            "insert into RESOURCE_PROPERTY "
            "(RESOURCE_ID, NAME, VALUE, VIEWER_UID) values (%s, %s, %s, %s)",
            [self._resourceID, key.toString(), value.toxml(), uid])


    def _delitem_uid(self, key, uid):
        self._txn.execSQL(
            "delete from RESOURCE_PROPERTY where VIEWER_UID = %s"
            "and RESOURCE_ID = %s AND NAME = %s",
            [uid, self._resourceID, key.toString()])


    def _keys_uid(self, uid):
        rows = self._txn.execSQL(
            "select NAME from RESOURCE_PROPERTY where "
            "VIEWER_UID = %s and RESOURCE_ID = %s",
            [uid, self._resourceID]
        )
        for row in rows:
            yield PropertyName.fromString(row[0])



class PostgresCalendarObject(object):
    implements(ICalendarObject)

    def __init__(self, calendar, name, resid):
        self._calendar = calendar
        self._name = name
        self._resourceID = resid
        self._calendarText = None


    @property
    def _txn(self):
        return self._calendar._txn


    def uid(self):
        return self.component().resourceUID()


    def organizer(self):
        return self.component().getOrganizer()


    def dropboxID(self):
        return dropboxIDFromCalendarObject(self)


    def name(self):
        return self._name


    def calendar(self):
        return self._calendar


    def iCalendarText(self):
        if self._calendarText is None:
            text = self._txn.execSQL(
                "select ICALENDAR_TEXT from CALENDAR_OBJECT where "
                "RESOURCE_ID = %s", [self._resourceID]
            )[0][0]
            self._calendarText = text
            return text
        else:
            return self._calendarText


    def component(self):
        return VComponent.fromString(self.iCalendarText())


    def componentType(self):
        return self.component().mainType()


    def properties(self):
        return PropertyStore(
            self.uid(),
            self.uid(),
            self._txn,
            self._resourceID
        )


    def setComponent(self, component):
        validateCalendarComponent(self, self._calendar, component)
        calendarText = str(component)
        self._txn.execSQL(
            "update CALENDAR_OBJECT set ICALENDAR_TEXT = %s "
            "where RESOURCE_ID = %s", [calendarText, self._resourceID]
        )
        self._calendarText = calendarText
        self._calendar._updateSyncToken()

        if self._calendar._notifier:
            self._calendar._home._txn.postCommit(self._calendar._notifier.notify)


    def _attachmentPath(self, name):
        attachmentRoot = self._calendar._home._txn._store.attachmentsPath
        try:
            attachmentRoot.createDirectory()
        except:
            pass
        return attachmentRoot.child(
            "%s-%s-%s-%s.attachment" % (
                self._calendar._home.uid(), self._calendar.name(),
                self.name(), name
            )
        )


    def createAttachmentWithName(self, name, contentType):
        path = self._attachmentPath(name)
        attachment = PostgresAttachment(self, path)
        self._txn.execSQL("""
            insert into ATTACHMENT (CALENDAR_OBJECT_RESOURCE_ID, CONTENT_TYPE,
            SIZE, MD5, PATH)
            values (%s, %s, %s, %s, %s)
            """,
            [
                self._resourceID, generateContentType(contentType), 0, "",
                attachment._pathValue()
            ]
        )
        return attachment.store(contentType)


    def attachments(self):
        rows = self._txn.execSQL("""
        select PATH from ATTACHMENT where CALENDAR_OBJECT_RESOURCE_ID = %s 
        """, [self._resourceID])
        for row in rows:
            demangledName = _pathToName(row[0])
            yield self.attachmentWithName(demangledName)


    def attachmentWithName(self, name):
        attachment = PostgresAttachment(self, self._attachmentPath(name))
        if attachment._populate():
            return attachment
        else:
            return None


    def removeAttachmentWithName(self, name):
        attachment = PostgresAttachment(self, self._attachmentPath(name))
        self._calendar._home._txn.postCommit(attachment._path.remove)
        self._txn.execSQL("""
        delete from ATTACHMENT where CALENDAR_OBJECT_RESOURCE_ID = %s AND
        PATH = %s
        """, [self._resourceID, attachment._pathValue()])


    # IDataStoreResource
    def contentType(self):
        """
        The content type of Calendar objects is text/calendar.
        """
        return MimeType.fromString("text/calendar")


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None


    def attendeesCanManageAttachments(self):
        return self.component().hasPropertyInAnyComponent("X-APPLE-DROPBOX")



def _pathToName(path):
    return path.rsplit(".", 1)[0].split("-", 3)[-1]



class PostgresAttachment(object):

    implements(IAttachment)

    def __init__(self, calendarObject, path):
        self._calendarObject = calendarObject
        self._path = path


    @property
    def _txn(self):
        return self._calendarObject._txn


    def _populate(self):
        """
        Execute necessary SQL queries to retrieve attributes.

        @return: C{True} if this attachment exists, C{False} otherwise.
        """
        rows = self._txn.execSQL(
            """
            select CONTENT_TYPE, MD5 from ATTACHMENT where PATH = %s
            """, [self._pathValue()])
        if not rows:
            return False
        self._contentType = MimeType.fromString(rows[0][0])
        self._md5 = rows[0][1]
        return True


    def store(self, contentType):
        return PostgresAttachmentStorageTransport(self, contentType)


    def retrieve(self, protocol):
        protocol.dataReceived(self._path.getContent())
        protocol.connectionLost(Failure(ConnectionLost()))


    def properties(self):
        pass # stub


    # IDataStoreResource
    def contentType(self):
        return self._contentType


    def md5(self):
        return self._md5


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None


    def name(self):
        return _pathToName(self._pathValue())


    def _pathValue(self):
        """
        Compute the value which should go into the 'path' column for this
        attachment.
        """
        root = self._calendarObject._calendar._home._txn._store.attachmentsPath
        return '/'.join(self._path.segmentsFrom(root))



class PostgresAttachmentStorageTransport(object):

    implements(ITransport)

    def __init__(self, attachment, contentType):
        self.attachment = attachment
        self.contentType = contentType
        self.buf = ''
        self.hash = hashlib.md5()


    @property
    def _txn(self):
        return self.attachment._txn


    def write(self, data):
        self.buf += data
        self.hash.update(data)


    def loseConnection(self):
        self.attachment._path.setContent(self.buf)
        pathValue = self.attachment._pathValue()
        contentTypeString = generateContentType(self.contentType)
        self._txn.execSQL(
            "update ATTACHMENT set CONTENT_TYPE = %s, MD5 = %s "
            "WHERE PATH = %s",
            [contentTypeString, self.hash.hexdigest(), pathValue]
        )



class PostgresLegacyInvitesEmulator(object):
    """
    Emulator for the implicit interface specified by
    L{twistedcaldav.sharing.InvitesDatabase}.
    """


    def __init__(self, calendar):
        self._calendar = calendar


    @property
    def _txn(self):
        return self._calendar._txn


    def create(self):
        "No-op, because the index implicitly always exists in the database."


    def remove(self):
        "No-op, because the index implicitly always exists in the database."


    def allRecords(self):
        for row in self._txn.execSQL(
                """
                select
                    INVITE.INVITE_UID, INVITE.NAME, INVITE.SENDER_ADDRESS,
                    CALENDAR_HOME.OWNER_UID, CALENDAR_BIND.BIND_MODE,
                    CALENDAR_BIND.BIND_STATUS, CALENDAR_BIND.MESSAGE
                from
                    INVITE, CALENDAR_HOME, CALENDAR_BIND
                where
                    INVITE.RESOURCE_ID = %s and
                    INVITE.HOME_RESOURCE_ID = 
                        CALENDAR_HOME.RESOURCE_ID and
                    CALENDAR_BIND.CALENDAR_RESOURCE_ID =
                        INVITE.RESOURCE_ID and
                    CALENDAR_BIND.CALENDAR_HOME_RESOURCE_ID =
                        INVITE.HOME_RESOURCE_ID
                order by
                    INVITE.NAME asc
                """, [self._calendar._resourceID]):
            [inviteuid, common_name, userid, ownerUID,
                bindMode, bindStatus, summary] = row
            # FIXME: this is really the responsibility of the protocol layer.
            state = {
                _BIND_STATUS_INVITED: "NEEDS-ACTION",
                _BIND_STATUS_ACCEPTED: "ACCEPTED",
                _BIND_STATUS_DECLINED: "DECLINED",
                _BIND_STATUS_INVALID: "INVALID",
            }[bindStatus]
            access = {
                _BIND_MODE_READ: "read-only",
                _BIND_MODE_WRITE: "read-write"
            }[bindMode]
            principalURL = "/principals/__uids__/" + ownerUID
            yield Invite(
                inviteuid, userid, principalURL, common_name,
                access, state, summary
            )


    def recordForUserID(self, userid):
        for record in self.allRecords():
            if record.userid == userid:
                return record


    def recordForPrincipalURL(self, principalURL):
        for record in self.allRecords():
            if record.principalURL == principalURL:
                return record


    def recordForInviteUID(self, inviteUID):
        for record in self.allRecords():
            if record.inviteuid == inviteUID:
                return record


    def addOrUpdateRecord(self, record):
        bindMode = {'read-only': _BIND_MODE_READ,
                    'read-write': _BIND_MODE_WRITE}[record.access]
        bindStatus = {
            "NEEDS-ACTION": _BIND_STATUS_INVITED,
            "ACCEPTED": _BIND_STATUS_ACCEPTED,
            "DECLINED": _BIND_STATUS_DECLINED,
            "INVALID": _BIND_STATUS_INVALID,
        }[record.state]
        # principalURL is derived from a directory record's principalURL() so
        # it will always contain the UID.
        principalUID = record.principalURL.split("/")[-1]
        shareeHome = self._txn.calendarHomeWithUID(principalUID, create=True)
        rows = self._txn.execSQL(
            "select RESOURCE_ID, HOME_RESOURCE_ID from INVITE where INVITE_UID = %s",
            [record.inviteuid]
        )
        if rows:
            [[resourceID, homeResourceID]] = rows
            # Invite(inviteuid, userid, principalURL, common_name, access, state, summary)
            self._txn.execSQL("""
                update CALENDAR_BIND set BIND_MODE = %s,
                BIND_STATUS = %s, MESSAGE = %s
                where
                    CALENDAR_RESOURCE_ID = %s and
                    CALENDAR_HOME_RESOURCE_ID = %s
            """, [bindMode, bindStatus, record.summary,
                resourceID, homeResourceID])
            self._txn.execSQL("""
                update INVITE set NAME = %s, SENDER_ADDRESS = %s
                where INVITE_UID = %s
                """,
                [record.name, record.userid, record.inviteuid]
            )
        else:
            self._txn.execSQL(
                """
                insert into CALENDAR_BIND
                (
                    CALENDAR_HOME_RESOURCE_ID, CALENDAR_RESOURCE_ID, 
                    CALENDAR_RESOURCE_NAME, BIND_MODE, BIND_STATUS,
                    SEEN_BY_OWNER, SEEN_BY_SHAREE, MESSAGE
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    shareeHome._resourceID,
                    self._calendar._resourceID,
                    None, # this is NULL because it is not bound yet, let's be
                          # explicit about that.
                    bindMode,
                    bindStatus,
                    False,
                    False,
                    record.summary
                ])
            self._txn.execSQL(
                """
                insert into INVITE (
                    INVITE_UID, NAME,
                    HOME_RESOURCE_ID, RESOURCE_ID,
                    SENDER_ADDRESS
                )
                values (%s, %s, %s, %s, %s)
                """,
                [
                    record.inviteuid, record.name,
                    shareeHome._resourceID, self._calendar._resourceID,
                    record.userid
                ])


    def removeRecordForUserID(self, userid):
        rec = self.recordForUserID(userid)
        self.removeRecordForInviteUID(rec.inviteuid)


    def removeRecordForPrincipalURL(self, principalURL):
        raise NotImplementedError("removeRecordForPrincipalURL")


    def removeRecordForInviteUID(self, inviteUID):
        rows = self._txn.execSQL("""
                select HOME_RESOURCE_ID, RESOURCE_ID from INVITE where
                INVITE_UID = %s
            """, [inviteUID])
        if rows:
            [[homeID, resourceID]] = rows
            self._txn.execSQL(
                "delete from CALENDAR_BIND where "
                "CALENDAR_HOME_RESOURCE_ID = %s and CALENDAR_RESOURCE_ID = %s",
                [homeID, resourceID])
            self._txn.execSQL("delete from INVITE where INVITE_UID = %s",
                [inviteUID])



class PostgresLegacySharesEmulator(object):

    def __init__(self, home):
        self._home = home


    @property
    def _txn(self):
        return self._home._txn


    def create(self):
        pass


    def remove(self):
        pass


    def allRecords(self):
        return []
#        c = self._home._txn._cursor
#        c.execute(
#            "select CALENDAR_RESOURCE_ID, CALENDAR_HOME_RESOURCE_ID from "
#            "CALENDAR_BIND where CALENDAR_BIND"
#            "",
#            [self._home.uid()])
#        ownedShares = c.fetchall()
#        for row in rows:
#            [calendarResourceID] = row
#            shareuid = 
#            yield SharedCollectionRecord(
#                shareuid, sharetype, hosturl, localname, summary
#            )


    def recordForLocalName(self, localname):
        return None
#        c = self._home._txn.cursor()
#        return SharedCollectionRecord(shareuid, sharetype, hosturl, localname, summary)


    def recordForShareUID(self, shareUID):
        pass


    def addOrUpdateRecord(self, record):
        print record

#        self._db_execute("""insert or replace into SHARES (SHAREUID, SHARETYPE, HOSTURL, LOCALNAME, SUMMARY)
#            values (:1, :2, :3, :4, :5)
#            """, record.shareuid, record.sharetype, record.hosturl, record.localname, record.summary,
#        )

    def removeRecordForLocalName(self, localname):
        self._txn.execSQL(
            "delete from CALENDAR_BIND where CALENDAR_RESOURCE_NAME = %s "
            "and CALENDAR_HOME_RESOURCE_ID = %s",
            [localname, self._home._resourceID]
        )


    def removeRecordForShareUID(self, shareUID):
        pass
#        c = self._home._cursor()
#        c.execute(
#            "delete from CALENDAR_BIND where CALENDAR_RESOURCE_NAME = %s "
#            "and CALENDAR_HOME_RESOURCE_ID = %s",
#            [self._home._resourceID]
#        )



class postgresqlgenerator(sqlgenerator):
    """
    Query generator for postgreSQL indexed searches.  (Currently unused: work
    in progress.)
    """

    def __init__(self, expr, calendar):
        self.TIMESPANDB = "TIME_RANGE"
        self.TIMESPANTEST = "((TIME_RANGE.FLOAT == 'N' AND TIME_RANGE.START_DATE < %s AND TIME_RANGE.END_DATE > %s) OR (TIME_RANGE.FLOAT == 'Y' AND TIME_RANGE.START_DATE < %s AND TIME_RANGE.END_DATE > %s))"
        self.TIMESPANTEST_NOEND = "((TIME_RANGE.FLOAT == 'N' AND TIME_RANGE.END_DATE > %s) OR (TIME_RANGE.FLOAT == 'Y' AND TIME_RANGE.END_DATE > %s))"
        self.TIMESPANTEST_NOSTART = "((TIME_RANGE.FLOAT == 'N' AND TIME_RANGE.START_DATE < %s) OR (TIME_RANGE.FLOAT == 'Y' AND TIME_RANGE.START_DATE < %s))"
        self.TIMESPANTEST_TAIL_PIECE = " AND TIME_RANGE.CALENDAR_OBJECT_RESOURCE_ID == CALENDAR_OBJECT.RESOURCE_ID"

        super(postgresqlgenerator, self).__init__(expr)
        self.calendar = calendar


    def generate(self):
        """
        Generate the actual SQL 'where ...' expression from the passed in
        expression tree.
        
        @return: a C{tuple} of (C{str}, C{list}), where the C{str} is the
            partial SQL statement, and the C{list} is the list of argument
            substitutions to use with the SQL API execute method.
        """

        # Init state
        self.sout = StringIO.StringIO()
        self.arguments = []
        self.usedtimespan = False

        # Generate ' where ...' partial statement
        self.sout.write(self.WHERE)
        self.generateExpression(self.expression)

        # Prefix with ' from ...' partial statement
        select = self.FROM + self.RESOURCEDB
        if self.usedtimespan:
            select += ", %s, %s" % (
                self.TIMESPANDB)
        select += self.sout.getvalue()
        return select, self.arguments


    def addArgument(self, arg):
        self.arguments.append(arg)
        self.sout.write("%s")


    def setArgument(self, arg):
        self.arguments.append(arg)
        return "%s"



class PostgresLegacyIndexEmulator(object):
    """
    Emulator for L{twistedcaldv.index.Index} and
    L{twistedcaldv.index.IndexSchedule}.
    """

    def __init__(self, calendar):
        self.calendar = calendar


    @property
    def _txn(self):
        return self.calendar._txn


    def reserveUID(self, uid):
        pass


    def isAllowedUID(self, uid, *names):
        """
        @see: L{twistedcaldav.index.Index.isAllowedUID}
        """
        return True


    def resourceUIDForName(self, name):
        obj = self.calendar.calendarObjectWithName(name)
        if obj is None:
            return None
        return obj.uid()


    def resourceNameForUID(self, uid):
        obj = self.calendar.calendarObjectWithUID(uid)
        if obj is None:
            return None
        return obj.name()


    def unreserveUID(self, uid):
        pass


    def notExpandedBeyond(self, minDate):
        """
        Gives all resources which have not been expanded beyond a given date
        in the database.  (Unused; see above L{postgresqlgenerator}.
        """
        return self._txn.execSQL(
            "select RESOURCE_NAME from CALENDAR_OBJECT "
            "where RECURRANCE_MAX < %s and CALENDAR_RESOURCE_ID = %s",
            [minDate, self.calendar._resourceID]
        )


    def indexedSearch(self, filter, useruid='', fbtype=False):
        """
        Always raise L{IndexedSearchException}, since these indexes are not
        fully implemented yet.
        """
        raise IndexedSearchException()


    def bruteForceSearch(self):
        return self._txn.execSQL(
            "select RESOURCE_NAME, ICALENDAR_UID, ICALENDAR_TYPE from "
            "CALENDAR_OBJECT where CALENDAR_RESOURCE_ID = %s",
            [self.calendar._resourceID]
        )


    def resourcesExist(self, names):
        return list(set(names).intersection(
            set(self.calendar.listCalendarObjects())))


    def resourceExists(self, name):
        return bool(
            self._txn.execSQL(
                "select RESOURCE_NAME from CALENDAR_OBJECT where "
                "RESOURCE_NAME = %s and CALENDAR_RESOURCE_ID = %s",
                [name, self.calendar._resourceID]
            )
        )



class PostgresCalendar(SyncTokenHelper):

    implements(ICalendar)

    def __init__(self, home, name, resourceID, notifier):
        self._home = home
        self._name = name
        self._resourceID = resourceID
        self._objects = {}
        self._notifier = notifier


    @property
    def _txn(self):
        return self._home._txn


    def retrieveOldInvites(self):
        return PostgresLegacyInvitesEmulator(self)

    def retrieveOldIndex(self):
        return PostgresLegacyIndexEmulator(self)


    def notifierID(self, label="default"):
        return None


    def name(self):
        return self._name


    def rename(self, name):
        oldName = self._name
        self._txn.execSQL(
            "update CALENDAR_BIND set CALENDAR_RESOURCE_NAME = %s "
            "where CALENDAR_RESOURCE_ID = %s AND "
            "CALENDAR_HOME_RESOURCE_ID = %s",
            [name, self._resourceID, self._home._resourceID]
        )
        self._name = name
        # update memos
        del self._home._calendars[oldName]
        self._home._calendars[name] = self


    def ownerCalendarHome(self):
        return self._home


    def listCalendarObjects(self):
        # FIXME: see listChildren
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from "
            "CALENDAR_OBJECT where "
            "CALENDAR_RESOURCE_ID = %s",
            [self._resourceID])
        return [row[0] for row in rows]


    def calendarObjects(self):
        for name in self.listCalendarObjects():
            yield self.calendarObjectWithName(name)


    @memoized('name', '_objects')
    def calendarObjectWithName(self, name):
        rows = self._txn.execSQL(
            "select RESOURCE_ID from CALENDAR_OBJECT where "
            "RESOURCE_NAME = %s and CALENDAR_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if not rows:
            return None
        resid = rows[0][0]
        return PostgresCalendarObject(self, name, resid)


    def calendarObjectWithUID(self, uid):
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from CALENDAR_OBJECT where "
            "ICALENDAR_UID = %s",
            [uid]
        )
        if not rows:
            return None
        name = rows[0][0]
        return self.calendarObjectWithName(name)


    def createCalendarObjectWithName(self, name, component):
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from CALENDAR_OBJECT where "
            " RESOURCE_NAME = %s AND CALENDAR_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if rows:
            raise ObjectResourceNameAlreadyExistsError()

        self._updateSyncToken()

        calendarObject = PostgresCalendarObject(self, name, None)
        calendarObject.component = lambda : component

        validateCalendarComponent(calendarObject, self, component)

        componentText = str(component)
        self._txn.execSQL(
            """
            insert into CALENDAR_OBJECT
            (CALENDAR_RESOURCE_ID, RESOURCE_NAME, ICALENDAR_TEXT,
             ICALENDAR_UID, ICALENDAR_TYPE, ATTACHMENTS_MODE)
             values
            (%s, %s, %s, %s, %s, %s)
            """,
            # should really be filling out more fields: ORGANIZER,
            # ORGANIZER_OBJECT, a correct ATTACHMENTS_MODE based on X-APPLE-
            # DROPBOX
            [self._resourceID, name, componentText, component.resourceUID(),
            component.resourceType(), _ATTACHMENTS_MODE_WRITE]
        )
        if self._notifier:
            self._home._txn.postCommit(self._notifier.notify)


    def removeCalendarObjectWithName(self, name):
        self._txn.execSQL(
            "delete from CALENDAR_OBJECT where RESOURCE_NAME = %s and "
            "CALENDAR_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if self._txn._cursor.rowcount == 0:
            raise NoSuchObjectResourceError()
        self._objects.pop(name, None)

        self._updateSyncToken()

        if self._notifier:
            self._txn.postCommit(self._notifier.notify)


    def removeCalendarObjectWithUID(self, uid):
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from CALENDAR_OBJECT where "
            "ICALENDAR_UID = %s AND CALENDAR_RESOURCE_ID = %s",
            [uid, self._resourceID]
        )
        if not rows:
            raise NoSuchObjectResourceError()
        name = rows[0][0]
        self._txn.execSQL(
            "delete from CALENDAR_OBJECT where ICALENDAR_UID = %s and "
            "CALENDAR_RESOURCE_ID = %s",
            [uid, self._resourceID]
        )
        self._objects.pop(name, None)
        self._updateSyncToken()

        if self._notifier:
            self._home._txn.postCommit(self._notifier.notify)


    def syncToken(self):
        return self._txn.execSQL(
            "select SYNC_TOKEN from CALENDAR where RESOURCE_ID = %s",
            [self._resourceID])[0][0]


    def calendarObjectsInTimeRange(self, start, end, timeZone):
        raise NotImplementedError()


    def calendarObjectsSinceToken(self, token):
        raise NotImplementedError()


    def properties(self):
        ownerUID = self.ownerCalendarHome().uid()
        return PropertyStore(
            ownerUID,
            ownerUID,
            self._txn,
            self._resourceID
        )


    # IDataStoreResource
    def contentType(self):
        """
        The content type of Calendar objects is text/calendar.
        """
        return MimeType.fromString("text/calendar")


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None



class PostgresCalendarHome(object):

    implements(ICalendarHome)

    def __init__(self, transaction, ownerUID, resourceID, notifier):
        self._txn = transaction
        self._ownerUID = ownerUID
        self._resourceID = resourceID
        self._calendars = {}
        self._notifier = notifier


    def retrieveOldShares(self):
        return PostgresLegacySharesEmulator(self)


    def uid(self):
        """
        Retrieve the unique identifier for this calendar home.

        @return: a string.
        """
        return self._ownerUID


    def name(self):
        """
        Implement L{IDataStoreResource.name} to return the uid.
        """
        return self.uid()


    def transaction(self):
        return self._txn


    def listChildren(self):
        """
        Retrieve the names of the children in this calendar home.

        @return: an iterable of C{str}s.
        """
        # FIXME: not specified on the interface or exercised by the tests, but
        # required by clients of the implementation!
        rows = self._txn.execSQL(
            "select CALENDAR_RESOURCE_NAME from CALENDAR_BIND where "
            "CALENDAR_HOME_RESOURCE_ID = %s "
            "AND BIND_STATUS != %s",
            [self._resourceID, _BIND_STATUS_DECLINED]
        )
        names = [row[0] for row in rows]
        return names


    def calendars(self):
        """
        Retrieve calendars contained in this calendar home.

        @return: an iterable of L{ICalendar}s.
        """
        names = self.listChildren()
        for name in names:
            yield self.calendarWithName(name)


    @memoized('name', '_calendars')
    def calendarWithName(self, name):
        """
        Retrieve the calendar with the given C{name} contained in this
        calendar home.

        @param name: a string.
        @return: an L{ICalendar} or C{None} if no such calendar
            exists.
        """
        data = self._txn.execSQL(
            "select CALENDAR_RESOURCE_ID from CALENDAR_BIND where "
            "CALENDAR_RESOURCE_NAME = %s",
            [name]
        )
        if not data:
            return None
        resourceID = data[0][0]
        if self._notifier:
            childID = "%s/%s" % (self.uid(), name)
            notifier = self._notifier.clone(label="collection", id=childID)
        else:
            notifier = None
        return PostgresCalendar(self, name, resourceID, notifier)


    def calendarObjectWithDropboxID(self, dropboxID):
        """
        Implement lookup with brute-force scanning.
        """
        for calendar in self.calendars():
            for calendarObject in calendar.calendarObjects():
                if dropboxID == calendarObject.dropboxID():
                    return calendarObject


    def createCalendarWithName(self, name):
        rows = self._txn.execSQL(
            "select CALENDAR_RESOURCE_NAME from CALENDAR_BIND where "
            "CALENDAR_RESOURCE_NAME = %s AND "
            "CALENDAR_HOME_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if rows:
            raise HomeChildNameAlreadyExistsError()
        rows = self._txn.execSQL("select nextval('RESOURCE_ID_SEQ')")
        resourceID = rows[0][0]
        self._txn.execSQL(
            "insert into CALENDAR (SYNC_TOKEN, RESOURCE_ID) values "
            "(%s, %s)",
            ['uninitialized', resourceID])

        self._txn.execSQL("""
            insert into CALENDAR_BIND (
                CALENDAR_HOME_RESOURCE_ID,
                CALENDAR_RESOURCE_ID, CALENDAR_RESOURCE_NAME, BIND_MODE,
                SEEN_BY_OWNER, SEEN_BY_SHAREE, BIND_STATUS) values (
            %s, %s, %s, %s, %s, %s, %s)
            """,
            [self._resourceID, resourceID, name, _BIND_MODE_OWN, True, True,
             _BIND_STATUS_ACCEPTED]
        )

        calendarType = ResourceType.calendar #@UndefinedVariable
        newCalendar = self.calendarWithName(name)
        newCalendar.properties()[
            PropertyName.fromElement(ResourceType)] = calendarType
        newCalendar._updateSyncToken(True)

        if self._notifier:
            self._txn.postCommit(self._notifier.notify)


    def removeCalendarWithName(self, name):
        self._txn.execSQL(
            "delete from CALENDAR_BIND where CALENDAR_RESOURCE_NAME = %s and "
            "CALENDAR_HOME_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        self._calendars.pop(name, None)
        if self._txn._cursor.rowcount == 0:
            raise NoSuchHomeChildError()
        # FIXME: the schema should probably cascade the calendar delete when
        # the last bind is deleted.
        if self._notifier:
            self._txn.postCommit(self._notifier.notify)


    def properties(self):
        return PropertyStore(
            self.uid(),
            self.uid(),
            self._txn,
            self._resourceID
        )


    # IDataStoreResource
    def contentType(self):
        """
        The content type of Calendar objects is text/calendar.
        """
        return MimeType.fromString("text/calendar")


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None


    def notifierID(self, label="default"):
        return None



class PostgresNotificationObject(object):
    implements(INotificationObject)

    def __init__(self, home, resourceID):
        self._home = home
        self._resourceID = resourceID


    def name(self):
        return self.uid() + ".xml"


    @property
    def _txn(self):
        return self._home._txn


    def setData(self, uid, xmltype, xmldata):
        return self._txn.execSQL(
            """
            update NOTIFICATION set NOTIFICATION_UID = %s, XML_TYPE = %s,
            XML_DATA = %s where RESOURCE_ID = %s
            """,
            [uid, xmltype, xmldata, self._resourceID]
        )


    def _fieldQuery(self, field):
        [[data]] = self._txn.execSQL(
            "select " + field + " from NOTIFICATION where "
            "RESOURCE_ID = %s",
            [self._resourceID])
        return data


    def xmldata(self):
        return self._fieldQuery("XML_DATA")


    def uid(self):
        return self._fieldQuery("NOTIFICATION_UID")


    def properties(self):
        return PropertyStore(self._home.uid(),
                             self._home.uid(),
                             self._txn,
                             self._resourceID)


    def md5(self):
        return None


    def modified(self):
        return None


    def created(self):
        return None



class PostgresLegacyNotificationsEmulator(object):
    def __init__(self, notificationsCollection):
        self._collection = notificationsCollection


    def _recordForObject(self, notificationObject):
        return NotificationRecord(
            notificationObject.uid(),
            notificationObject.name(),
            notificationObject._fieldQuery("XML_TYPE"))


    def recordForName(self, name):
        return self._recordForObject(
            self._collection.notificationObjectWithName(name)
        )


    def recordForUID(self, uid):
        return self._recordForObject(
            self._collection.notificationObjectWithUID(uid)
        )


    def removeRecordForUID(self, uid):
        self._collection.removeNotificationObjectWithUID(uid)


    def removeRecordForName(self, name):
        self._collection.removeNotificationObjectWithName(name)



class PostgresNotificationsCollection(object):

    implements(INotificationCollection)

    def __init__(self, txn, uid, resourceID):
        self._txn = txn
        self._uid = uid
        self._resourceID = resourceID


    def retrieveOldIndex(self):
        return PostgresLegacyNotificationsEmulator(self)


    def name(self):
        return 'notification'


    def uid(self):
        return self._uid


    def notificationObjects(self):
        for [uid] in self._txn.execSQL(
                "select (NOTIFICATION_UID) "
                "from NOTIFICATION "
                "where NOTIFICATION_HOME_RESOURCE_ID = %s",
                [self._resourceID]):
            yield self.notificationObjectWithUID(uid)


    def _nameToUID(self, name):
        """
        Based on the file-backed implementation, the 'name' is just uid +
        ".xml".
        """
        return name.rsplit(".", 1)[0]


    def notificationObjectWithName(self, name):
        return self.notificationObjectWithUID(self._nameToUID(name))


    def notificationObjectWithUID(self, uid):
        rows = self._txn.execSQL(
            "select RESOURCE_ID from NOTIFICATION where NOTIFICATION_UID = %s"
            " and NOTIFICATION_HOME_RESOURCE_ID = %s",
            [uid, self._resourceID])
        if rows:
            [[resourceID]] = rows
            return PostgresNotificationObject(self, resourceID)
        else:
            return None


    def writeNotificationObject(self, uid, xmltype, xmldata):
        xmltype = PropertyName.fromElement(xmltype).toString()
        self._txn.execSQL(
            "insert into NOTIFICATION (NOTIFICATION_HOME_RESOURCE_ID, NOTIFICATION_UID, XML_TYPE, XML_DATA) "
            "values (%s, %s, %s, %s)", [self._resourceID, uid, xmltype, xmldata])


    def removeNotificationObjectWithName(self, name):
        self.removeNotificationObjectWithUID(self._nameToUID(name))


    def removeNotificationObjectWithUID(self, uid):
        self._txn.execSQL(
            "delete from NOTIFICATION where NOTIFICATION_UID = %s and "
            "NOTIFICATION_HOME_RESOURCE_ID = %s",
            [uid, self._resourceID])


    def syncToken(self):
        return 'dummy-sync-token'


    def notificationObjectsSinceToken(self, token):
        changed = []
        removed = []
        token = self.syncToken()
        return (changed, removed, token)


    def properties(self):
        return PropertyStore(
            self._uid, self._uid, self._txn, self._resourceID
        )



class PostgresTransaction(object):
    """
    Transaction implementation for postgres database.
    """
    implements(ICalendarTransaction, IAddressBookTransaction)

    def __init__(self, store, connection, notifierFactory, label):
        # print 'STARTING', label
        self._store = store
        self._connection = connection
        self._cursor = connection.cursor()
        self._completed = False
        self._homes = {}
        self._postCommitOperations = []
        self._notifierFactory = notifierFactory
        self._label = label


    def store(self):
        return self._store


    def __repr__(self):
        return 'PG-TXN<%s>' % (self._label,)


    def execSQL(self, sql, args=[]):
        # print 'EXECUTE %s: %s' % (self._label, sql)
        self._cursor.execute(sql, args)
        if self._cursor.description:
            return self._cursor.fetchall()
        else:
            return None


    def __del__(self):
        if not self._completed:
            self._connection.rollback()
            self._connection.close()


    @memoized('uid', '_homes')
    def calendarHomeWithUID(self, uid, create=False):
        data = self.execSQL(
            "select RESOURCE_ID from CALENDAR_HOME where OWNER_UID = %s",
            [uid]
        )
        if not data:
            if not create:
                return None
            self.execSQL(
                "insert into CALENDAR_HOME (OWNER_UID) values (%s)",
                [uid]
            )
            home = self.calendarHomeWithUID(uid)
            home.createCalendarWithName("calendar")
            return home
        resid = data[0][0]

        if self._notifierFactory:
            notifier = self._notifierFactory.newNotifier(id=uid)
        else:
            notifier = None

        return PostgresCalendarHome(self, uid, resid, notifier)


    @memoized('uid', '_homes')
    def addressbookHomeWithUID(self, uid, create=False):
        data = self.execSQL(
            "select RESOURCE_ID from ADDRESSBOOK_HOME where OWNER_UID = %s",
            [uid]
        )
        if not data:
            if not create:
                return None
            self.execSQL(
                "insert into ADDRESSBOOK_HOME (OWNER_UID) values (%s)",
                [uid]
            )
            home = self.addressbookHomeWithUID(uid)
            home.createAddressBookWithName("addressbook")
            return home
        resid = data[0][0]

        if self._notifierFactory:
            notifier = self._notifierFactory.newNotifier(id=uid)
        else:
            notifier = None

        return PostgresAddressBookHome(self, uid, resid, notifier)


    def notificationsWithUID(self, uid):
        """
        Implement notificationsWithUID.
        """
        rows = self.execSQL(
            """
            select RESOURCE_ID from NOTIFICATION_HOME where
            OWNER_UID = %s
            """, [uid])
        if rows:
            [[resourceID]] = rows
        else:
            [[resourceID]] = self.execSQL("select nextval('RESOURCE_ID_SEQ')")
            resourceID = str(resourceID)
            self.execSQL(
                "insert into NOTIFICATION_HOME (RESOURCE_ID, OWNER_UID) "
                "values (%s, %s)", [resourceID, uid])
        return PostgresNotificationsCollection(self, uid, resourceID)


    def abort(self):
        if not self._completed:
            # print 'ABORTING', self._label
            self._completed = True
            self._connection.rollback()
            self._connection.close()
        else:
            raise AlreadyFinishedError()


    def commit(self):
        if not self._completed:
            # print 'COMPLETING', self._label
            self._completed = True
            self._connection.commit()
            self._connection.close()
            for operation in self._postCommitOperations:
                operation()
        else:
            raise AlreadyFinishedError()


    def postCommit(self, operation):
        """
        Run things after 'commit.'
        """
        self._postCommitOperations.append(operation)
        # FIXME: implement.

# CARDDAV

class PostgresAddressBookObject(object):

    implements(IAddressBookObject)

    def __init__(self, addressbook, name, resid):
        self._addressbook = addressbook
        self._name = name
        self._resourceID = resid
        self._vCardText = None


    @property
    def _txn(self):
        return self._addressbook._txn


    def uid(self):
        return self.component().resourceUID()


    def name(self):
        return self._name


    def addressbook(self):
        return self._addressbook


    def vCardText(self):
        if self._vCardText is None:
            text = self._txn.execSQL(
                "select VCARD_TEXT from ADDRESSBOOK_OBJECT where "
                "RESOURCE_ID = %s", [self._resourceID]
            )[0][0]
            self._vCardText = text
            return text
        else:
            return self._vCardText


    def component(self):
        return VCard.fromString(self.vCardText())


    def componentType(self):
        return self.component().mainType()


    def properties(self):
        return PropertyStore(
            self.uid(),
            self.uid(),
            self._txn,
            self._resourceID
        )


    def setComponent(self, component):
        validateAddressBookComponent(self, self._addressbook, component)

        vCardText = str(component)
        self._txn.execSQL(
            "update ADDRESSBOOK_OBJECT set VCARD_TEXT = %s "
            "where RESOURCE_ID = %s", [vCardText, self._resourceID]
        )
        self._vCardText = vCardText
        if self._addressbook._notifier:
            self._addressbook._home._txn.postCommit(self._addressbook._notifier.notify)



    # IDataStoreResource
    def contentType(self):
        """
        The content type of Addressbook objects is text/x-vcard.
        """
        return MimeType.fromString("text/x-vcard")


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None



class PostgresAddressBook(object):

    implements(IAddressBook)

    def __init__(self, home, name, resourceID, notifier):
        self._home = home
        self._name = name
        self._resourceID = resourceID
        self._objects = {}
        self._notifier = notifier


    @property
    def _txn(self):
        return self._home._txn


    def retrieveOldInvites(self):
        return PostgresLegacyInvitesEmulator(self)

    def retrieveOldIndex(self):
        return PostgresLegacyIndexEmulator(self)


    def notifierID(self, label="default"):
        return None


    def name(self):
        return self._name


    def rename(self, name):
        oldName = self._name
        self._txn.execSQL(
            "update ADDRESSBOOK_BIND set ADDRESSBOOK_RESOURCE_NAME = %s "
            "where ADDRESSBOOK_RESOURCE_ID = %s AND "
            "ADDRESSBOOK_HOME_RESOURCE_ID = %s",
            [name, self._resourceID, self._home._resourceID]
        )
        self._name = name
        # update memos
        del self._home._addressbooks[oldName]
        self._home._addressbooks[name] = self


    def ownerAddressBookHome(self):
        return self._home


    def listAddressbookObjects(self):
        # FIXME: see listChildren
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from "
            "ADDRESSBOOK_OBJECT where "
            "ADDRESSBOOK_RESOURCE_ID = %s",
            [self._resourceID])
        return [row[0] for row in rows]


    def addressbookObjects(self):
        for name in self.listAddressbookObjects():
            yield self.addressbookObjectWithName(name)


    @memoized('name', '_objects')
    def addressbookObjectWithName(self, name):
        rows = self._txn.execSQL(
            "select RESOURCE_ID from ADDRESSBOOK_OBJECT where "
            "RESOURCE_NAME = %s and ADDRESSBOOK_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if not rows:
            return None
        resid = rows[0][0]
        return PostgresAddressBookObject(self, name, resid)


    def addressbookObjectWithUID(self, uid):
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from ADDRESSBOOK_OBJECT where "
            "VCARD_UID = %s",
            [uid]
        )
        if not rows:
            return None
        name = rows[0][0]
        return self.addressbookObjectWithName(name)


    def createAddressBookObjectWithName(self, name, component):
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from ADDRESSBOOK_OBJECT where "
            " RESOURCE_NAME = %s AND ADDRESSBOOK_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if rows:
            raise ObjectResourceNameAlreadyExistsError()

        addressbookObject = PostgresAddressBookObject(self, name, None)
        addressbookObject.component = lambda : component

        validateAddressBookComponent(addressbookObject, self, component)

        componentText = str(component)
        self._txn.execSQL(
            """
            insert into ADDRESSBOOK_OBJECT
            (ADDRESSBOOK_RESOURCE_ID, RESOURCE_NAME, VCARD_TEXT,
             VCARD_UID, VCARD_TYPE)
             values
            (%s, %s, %s, %s, %s)
            """,
            [self._resourceID, name, componentText, component.resourceUID(),
            "VCARD"] # component.resourceType()]  FIXME: what value(s) here?
        )
        if self._notifier:
            self._home._txn.postCommit(self._notifier.notify)


    def removeAddressBookObjectWithName(self, name):
        self._txn.execSQL(
            "delete from ADDRESSBOOK_OBJECT where RESOURCE_NAME = %s and "
            "ADDRESSBOOK_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if self._txn._cursor.rowcount == 0:
            raise NoSuchObjectResourceError()
        self._objects.pop(name, None)
        if self._notifier:
            self._txn.postCommit(self._notifier.notify)


    def removeAddressBookObjectWithUID(self, uid):
        rows = self._txn.execSQL(
            "select RESOURCE_NAME from ADDRESSBOOK_OBJECT where "
            "VCARD_UID = %s AND ADDRESSBOOK_RESOURCE_ID = %s",
            [uid, self._resourceID]
        )
        if not rows:
            raise NoSuchObjectResourceError()
        name = rows[0][0]
        self._txn.execSQL(
            "delete from ADDRESSBOOK_OBJECT where VCARD_UID = %s and "
            "ADDRESSBOOK_RESOURCE_ID = %s",
            [uid, self._resourceID]
        )
        self._objects.pop(name, None)
        if self._notifier:
            self._home._txn.postCommit(self._notifier.notify)


    def syncToken(self):
        return self._txn.execSQL(
            "select SYNC_TOKEN from ADDRESSBOOK where RESOURCE_ID = %s",
            [self._resourceID])[0][0]


    def addressbookObjectsSinceToken(self, token):
        raise NotImplementedError()


    def properties(self):
        ownerUID = self.ownerAddressBookHome().uid()
        return PropertyStore(
            ownerUID,
            ownerUID,
            self._txn,
            self._resourceID
        )


    # IDataStoreResource
    def contentType(self):
        """
        The content type of Addressbook objects is ???
        """
        return None # FIXME: verify


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None




class PostgresAddressBookHome(object):

    implements(IAddressBookHome)

    def __init__(self, transaction, ownerUID, resourceID, notifier):
        self._txn = transaction
        self._ownerUID = ownerUID
        self._resourceID = resourceID
        self._addressbooks = {}
        self._notifier = notifier


    def retrieveOldShares(self):
        return PostgresLegacySharesEmulator(self)


    def uid(self):
        """
        Retrieve the unique identifier for this calendar home.

        @return: a string.
        """
        return self._ownerUID


    def name(self):
        """
        Implement L{IDataStoreResource.name} to return the uid.
        """
        return self.uid()


    def listChildren(self):
        """
        Retrieve the names of the children in this addressbook home.

        @return: an iterable of C{str}s.
        """
        # FIXME: not specified on the interface or exercised by the tests, but
        # required by clients of the implementation!
        rows = self._txn.execSQL(
            "select ADDRESSBOOK_RESOURCE_NAME from ADDRESSBOOK_BIND where "
            "ADDRESSBOOK_HOME_RESOURCE_ID = %s "
            "AND BIND_STATUS != %s",
            [self._resourceID, _BIND_STATUS_DECLINED]
        )
        names = [row[0] for row in rows]
        return names


    def addressbooks(self):
        """
        Retrieve addressbooks contained in this addressbook home.

        @return: an iterable of L{IAddressBook}s.
        """
        names = self.listChildren()
        for name in names:
            yield self.addressbookWithName(name)


    @memoized('name', '_addressbooks')
    def addressbookWithName(self, name):
        """
        Retrieve the addressbook with the given C{name} contained in this
        addressbook home.

        @param name: a string.
        @return: an L{IAddressBook} or C{None} if no such addressbook
            exists.
        """
        data = self._txn.execSQL(
            "select ADDRESSBOOK_RESOURCE_ID from ADDRESSBOOK_BIND where "
            "ADDRESSBOOK_RESOURCE_NAME = %s",
            [name]
        )
        if not data:
            return None
        resourceID = data[0][0]
        if self._notifier:
            childID = "%s/%s" % (self.uid(), name)
            notifier = self._notifier.clone(label="collection", id=childID)
        else:
            notifier = None
        return PostgresAddressBook(self, name, resourceID, notifier)


    def createAddressBookWithName(self, name):
        rows = self._txn.execSQL(
            "select ADDRESSBOOK_RESOURCE_NAME from ADDRESSBOOK_BIND where "
            "ADDRESSBOOK_RESOURCE_NAME = %s AND "
            "ADDRESSBOOK_HOME_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        if rows:
            raise HomeChildNameAlreadyExistsError()
        rows = self._txn.execSQL("select nextval('RESOURCE_ID_SEQ')")
        resourceID = rows[0][0]
        self._txn.execSQL(
            "insert into ADDRESSBOOK (SYNC_TOKEN, RESOURCE_ID) values "
            "(%s, %s)",
            ['uninitialized', resourceID])

        self._txn.execSQL("""
            insert into ADDRESSBOOK_BIND (
                ADDRESSBOOK_HOME_RESOURCE_ID,
                ADDRESSBOOK_RESOURCE_ID, ADDRESSBOOK_RESOURCE_NAME, BIND_MODE,
                SEEN_BY_OWNER, SEEN_BY_SHAREE, BIND_STATUS) values (
            %s, %s, %s, %s, %s, %s, %s)
            """,
            [self._resourceID, resourceID, name, _BIND_MODE_OWN, True, True,
             _BIND_STATUS_ACCEPTED]
        )

        addressbookType = ResourceType.addressbook #@UndefinedVariable
        self.addressbookWithName(name).properties()[
            PropertyName.fromElement(ResourceType)] = addressbookType
        if self._notifier:
            self._txn.postCommit(self._notifier.notify)


    def removeAddressBookWithName(self, name):
        self._txn.execSQL(
            "delete from ADDRESSBOOK_BIND where ADDRESSBOOK_RESOURCE_NAME = %s and "
            "ADDRESSBOOK_HOME_RESOURCE_ID = %s",
            [name, self._resourceID]
        )
        self._addressbooks.pop(name, None)
        if self._txn._cursor.rowcount == 0:
            raise NoSuchHomeChildError()
        # FIXME: the schema should probably cascade the addressbook delete when
        # the last bind is deleted.
        if self._notifier:
            self._txn.postCommit(self._notifier.notify)


    def properties(self):
        return PropertyStore(
            self.uid(),
            self.uid(),
            self._txn,
            self._resourceID
        )


    # IDataStoreResource
    def contentType(self):
        """
        The content type of Addressbook home objects is ???
        """
        return None # FIXME: verify


    def md5(self):
        return None


    def size(self):
        return 0


    def created(self):
        return None


    def modified(self):
        return None


    def notifierID(self, label="default"):
        return None


#


class PostgresStore(Service, object):

    implements(IDataStore)

    def __init__(self, connectionFactory, notifierFactory, attachmentsPath):
        self.connectionFactory = connectionFactory
        self.notifierFactory = notifierFactory
        self.attachmentsPath = attachmentsPath


    def newTransaction(self, label="unlabeled"):
        return PostgresTransaction(
            self,
            self.connectionFactory(),
            self.notifierFactory,
            label
        )

