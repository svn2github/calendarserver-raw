##
# Copyright (c) 2006-2009 Apple Inc. All rights reserved.
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
Implements a calendar user proxy principal.
"""

__all__ = [
    "CalendarUserProxyPrincipalResource",
    "CalendarUserProxyDatabase",
]

from twisted.internet.defer import succeed, inlineCallbacks, returnValue
from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, StatusResponse
from twisted.web2.dav import davxml
from twisted.web2.dav.element.base import dav_namespace
from twisted.web2.dav.util import joinURL
from twisted.web2.dav.noneprops import NonePropertyStore

from twistedcaldav.config import config
from twistedcaldav.extensions import DAVFile, DAVPrincipalResource
from twistedcaldav.extensions import ReadOnlyWritePropertiesResourceMixIn
from twistedcaldav.memcacher import Memcacher
from twistedcaldav.resource import CalDAVComplianceMixIn
from twistedcaldav.directory.util import NotFilePath
from twistedcaldav.sql import AbstractSQLDatabase, db_prefix
from twistedcaldav.log import LoggingMixIn

import itertools
import os
import time

class PermissionsMixIn (ReadOnlyWritePropertiesResourceMixIn):
    def defaultAccessControlList(self):
        aces = (
            # DAV:read access for authenticated users.
            davxml.ACE(
                davxml.Principal(davxml.Authenticated()),
                davxml.Grant(davxml.Privilege(davxml.Read())),
            ),
            # Inheritable DAV:all access for the resource's associated principal.
            davxml.ACE(
                davxml.Principal(davxml.HRef(self.parent.principalURL())),
                davxml.Grant(davxml.Privilege(davxml.WriteProperties())),
                davxml.Protected(),
            ),
        )

        # Add admins
        aces += tuple((
            davxml.ACE(
                davxml.Principal(davxml.HRef(principal)),
                davxml.Grant(davxml.Privilege(davxml.All())),
                davxml.Protected(),
            )
            for principal in config.AdminPrincipals
        ))

        return succeed(davxml.ACL(*aces))

    def accessControlList(self, request, inheritance=True, expanding=False, inherited_aces=None):
        # Permissions here are fixed, and are not subject to inheritance rules, etc.
        return self.defaultAccessControlList()

class CalendarUserProxyPrincipalResource (CalDAVComplianceMixIn, PermissionsMixIn, DAVPrincipalResource, DAVFile):
    """
    Calendar user proxy principal resource.
    """
    def __init__(self, parent, proxyType):
        """
        @param parent: the parent of this resource.
        @param proxyType: a C{str} containing the name of the resource.
        """
        if self.isCollection():
            slash = "/"
        else:
            slash = ""

        url = joinURL(parent.principalURL(), proxyType) + slash

        super(CalendarUserProxyPrincipalResource, self).__init__(NotFilePath(isdir=True), url)

        self.parent      = parent
        self.proxyType   = proxyType
        self.pcollection = self.parent.parent.parent # FIXME: if this is supposed to be public, it needs a better name
        self._url        = url

        # Not terribly useful at present because we don't have a way
        # to map a GUID back to the correct principal.
        #self.guid = uuidFromName(self.parent.principalUID(), proxyType)

        # Principal UID is parent's GUID plus the proxy type; this we
        # can easily map back to a principal.
        self.uid = "%s#%s" % (self.parent.principalUID(), proxyType)

        self._alternate_urls = tuple(
            joinURL(url, proxyType) + slash
            for url in parent.alternateURIs()
            if url.startswith("/")
        )

    def __str__(self):
        return "%s [%s]" % (self.parent, self.proxyType)

    def _index(self):
        """
        Return the SQL database for this group principal.

        @return: the L{CalendarUserProxyDatabase} for the principal collection.
        """

        # The db is located in the principal collection root
        if not hasattr(self.pcollection, "calendar_user_proxy_db"):
            setattr(self.pcollection, "calendar_user_proxy_db", CalendarUserProxyDatabase(config.DataRoot))
        return self.pcollection.calendar_user_proxy_db

    def resourceType(self):
        if self.proxyType == "calendar-proxy-read":
            return succeed(davxml.ResourceType.calendarproxyread)
        elif self.proxyType == "calendar-proxy-write":
            return succeed(davxml.ResourceType.calendarproxywrite)
        else:
            return super(CalendarUserProxyPrincipalResource, self).resourceType()

    def isProxyType(self, read_write):
        if (
            read_write and self.proxyType == "calendar-proxy-write" or
            not read_write and self.proxyType == "calendar-proxy-read"
        ):
            return True
        else:
            return False

    def isCollection(self):
        return True

    def etag(self):
        return succeed(None)

    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties

    # Deferred
    def writeProperty(self, property, request):
        assert isinstance(property, davxml.WebDAVElement)

        if property.qname() == (dav_namespace, "group-member-set"):
            return self.setGroupMemberSet(property, request)

        return super(CalendarUserProxyPrincipalResource, self).writeProperty(property, request)

    @inlineCallbacks
    def setGroupMemberSet(self, new_members, request):
        # FIXME: as defined right now it is not possible to specify a calendar-user-proxy group as
        # a member of any other group since the directory service does not know how to lookup
        # these special resource UIDs.
        #
        # Really, c-u-p principals should be treated the same way as any other principal, so
        # they should be allowed as members of groups.
        #
        # This implementation now raises an exception for any principal it cannot find.

        # Break out the list into a set of URIs.
        members = [str(h) for h in new_members.children]

        # Map the URIs to principals and a set of UIDs.
        principals = []
        newUIDs = set()
        for uri in members:
            principal = (yield self.pcollection._principalForURI(uri))
            # Invalid principals MUST result in an error.
            if principal is None or principal.principalURL() != uri:
                raise HTTPError(StatusResponse(
                    responsecode.BAD_REQUEST,
                    "Attempt to use a non-existent principal %s as a group member of %s." % (uri, self.principalURL(),)
                ))
            principals.append(principal)
            newUIDs.add(principal.principalUID())

        # Get the old set of UIDs
        oldUIDs = (yield self._index().getMembers(self.uid))
        
        # Change membership
        yield self.setGroupMemberSetPrincipals(principals)
        
        # Invalidate the primary principal's cache, and any principal's whose
        # membership status changed
        yield self.parent.cacheNotifier.changed()
        
        changedUIDs = newUIDs.symmetric_difference(oldUIDs)
        for uid in changedUIDs:
            principal = (yield self.pcollection.principalForUID(uid))
            if principal:
                yield principal.cacheNotifier.changed()
            
        returnValue(True)

    def setGroupMemberSetPrincipals(self, principals):
        # Map the principals to UIDs.
        return self._index().setGroupMembers(
            self.uid,
            [p.principalUID() for p in principals],
        )

    ##
    # HTTP
    ##

    def renderDirectoryBody(self, request):
        # FIXME: Too much code duplication here from principal.py
        from twistedcaldav.directory.principal import format_list, format_principals, format_link

        closure = {}

        d = super(CalendarUserProxyPrincipalResource, self).renderDirectoryBody(request)
        d.addCallback(lambda output: closure.setdefault("output", output))

        d.addCallback(lambda _: self.groupMembers())
        d.addCallback(lambda members: closure.setdefault("members", members))

        d.addCallback(lambda _: self.groupMemberships())
        d.addCallback(lambda memberships: closure.setdefault("memberships", memberships))
        
        d.addCallback(
            lambda _: "".join((
                """<div class="directory-listing">"""
                """<h1>Principal Details</h1>"""
                """<pre><blockquote>"""
                """Directory Information\n"""
                """---------------------\n"""
                """Directory GUID: %s\n"""         % (self.parent.record.service.guid,),
                """Realm: %s\n"""                  % (self.parent.record.service.realmName,),
                """\n"""
                """Parent Principal Information\n"""
                """---------------------\n"""
                """GUID: %s\n"""                   % (self.parent.record.guid,),
                """Record type: %s\n"""            % (self.parent.record.recordType,),
                """Short names: %s\n"""            % (",".join(self.parent.record.shortNames,)),
                """Full name: %s\n"""              % (self.parent.record.fullName,),
                """Principal UID: %s\n"""          % (self.parent.principalUID(),),
                """Principal URL: %s\n"""          % (format_link(self.parent.principalURL()),),
                """\n"""
                """Proxy Principal Information\n"""
                """---------------------\n"""
               #"""GUID: %s\n"""                   % (self.guid,),
                """Principal UID: %s\n"""          % (self.principalUID(),),
                """Principal URL: %s\n"""          % (format_link(self.principalURL()),),
                """\nAlternate URIs:\n"""          , format_list(format_link(u) for u in self.alternateURIs()),
                """\nGroup members:\n"""           , format_principals(closure["members"]),
                """\nGroup memberships:\n"""       , format_principals(closure["memberships"]),
                """</pre></blockquote></div>""",
                closure["output"]
            ))
        )

        return d

    ##
    # DAV
    ##

    def displayName(self):
        return succeed(self.proxyType)

    ##
    # ACL
    ##

    def alternateURIs(self):
        # FIXME: Add API to IDirectoryRecord for getting a record URI?
        return self._alternate_urls

    def principalURL(self):
        return self._url

    def principalUID(self):
        return self.uid

    def principalCollections(self):
        return self.parent.principalCollections()

    @inlineCallbacks
    def _expandMemberUIDs(self, uid=None, relatives=None, uids=None, infinity=False):
        if uid is None:
            uid = self.principalUID()
        if relatives is None:
            relatives = set()
        if uids is None:
            uids = set()

        if uid not in uids:
            from twistedcaldav.directory.principal import DirectoryPrincipalResource
            uids.add(uid)
            principal = (yield self.pcollection.principalForUID(uid))
            if isinstance(principal, CalendarUserProxyPrincipalResource):
                members = yield self._directGroupMembers()
                for member in members:
                    if member.principalUID() not in uids:
                        relatives.add(member)
                        if infinity:
                            yield self._expandMemberUIDs(member.principalUID(), relatives, uids, infinity=infinity)
            elif isinstance(principal, DirectoryPrincipalResource):
                if infinity:
                    members = yield principal.expandedGroupMembers()
                else:
                    members = yield principal.groupMembers()
                relatives.update(members)

        returnValue(relatives)

    @inlineCallbacks
    def _directGroupMembers(self):
        # Get member UIDs from database and map to principal resources
        members = yield self._index().getMembers(self.uid)
        found = []
        missing = []
        for uid in members:
            p = (yield self.pcollection.principalForUID(uid))
            if p:
                found.append(p)
                # Make sure any outstanding deletion timer entries for
                # existing principals are removed
                yield self._index()._memcacher.clearDeletionTimer(uid)
            else:
                missing.append(uid)

        # Clean-up ones that are missing
        for uid in missing:
            cacheTimeout = config.DirectoryService.params.get("cacheTimeout", 30) * 60 # in seconds

            yield self._index().removePrincipal(uid, delay=cacheTimeout*2)

        returnValue(found)

    def groupMembers(self):
        return self._expandMemberUIDs()

    def expandedGroupMembers(self):
        return self._expandMemberUIDs(infinity=True)

    @inlineCallbacks
    def groupMemberships(self):
        # Get membership UIDs and map to principal resources
        memberships = yield self._index().getMemberships(self.uid)
        returnValue([p for p in
                     [(yield self.pcollection.principalForUID(uid))
                      for uid in memberships]
                     if p])



class CalendarUserProxyDatabase(AbstractSQLDatabase, LoggingMixIn):
    """
    A database to maintain calendar user proxy group memberships.

    SCHEMA:

    Group Database:

    ROW: GROUPNAME, MEMBER

    """

    dbType = "CALENDARUSERPROXY"
    dbFilename = "calendaruserproxy.sqlite"
    dbOldFilename = db_prefix + "calendaruserproxy"
    dbFormatVersion = "4"

    class ProxyDBMemcacher(Memcacher):
        
        def setMembers(self, guid, members):
            return self.set("members:%s" % (str(guid),), str(",".join(members)))

        def setMemberships(self, guid, memberships):
            return self.set("memberships:%s" % (str(guid),), str(",".join(memberships)))

        def getMembers(self, guid):
            def _value(value):
                if value:
                    return set(value.split(","))
                elif value is None:
                    return None
                else:
                    return set()
            d = self.get("members:%s" % (str(guid),))
            d.addCallback(_value)
            return d

        def getMemberships(self, guid):
            def _value(value):
                if value:
                    return set(value.split(","))
                elif value is None:
                    return None
                else:
                    return set()
            d = self.get("memberships:%s" % (str(guid),))
            d.addCallback(_value)
            return d

        def deleteMember(self, guid):
            return self.delete("members:%s" % (str(guid),))

        def deleteMembership(self, guid):
            return self.delete("memberships:%s" % (str(guid),))

        def setDeletionTimer(self, guid, delay):
            return self.set("del:%s" % (str(guid),), str(self.getTime()+delay))

        def checkDeletionTimer(self, guid):
            # True means it's overdue, False means it's not, None means no timer
            def _value(value):
                if value:
                    if int(value) <= self.getTime():
                        return True
                    else:
                        return False
                else:
                    return None
            d = self.get("del:%s" % (str(guid),))
            d.addCallback(_value)
            return d

        def clearDeletionTimer(self, guid):
            return self.delete("del:%s" % (str(guid),))

        def getTime(self):
            if hasattr(self, 'theTime'):
                theTime = self.theTime
            else:
                theTime = int(time.time())
            return theTime

    def __init__(self, path):
        path = os.path.join(path, CalendarUserProxyDatabase.dbFilename)
        super(CalendarUserProxyDatabase, self).__init__(path, True)
        
        self._memcacher = CalendarUserProxyDatabase.ProxyDBMemcacher("proxyDB")

    @inlineCallbacks
    def setGroupMembers(self, principalUID, members):
        """
        Add a group membership record.

        @param principalUID: the UID of the group principal to add.
        @param members: a list UIDs of principals that are members of this group.
        """

        # Get current members before we change them
        current_members = yield self.getMembers(principalUID)
        if current_members is None:
            current_members = ()
        current_members = set(current_members)

        self.setGroupMembersInDatabase(principalUID, members)

        # Update cache
        update_members = set(members)
        
        remove_members = current_members.difference(update_members)
        add_members = update_members.difference(current_members)
        for member in itertools.chain(remove_members, add_members,):
            yield self._memcacher.deleteMembership(member)
        yield self._memcacher.deleteMember(principalUID)

    def setGroupMembersInDatabase(self, principalUID, members):
        """
        A blocking call to add a group membership record in the database.

        @param principalUID: the UID of the group principal to add.
        @param members: a list UIDs of principals that are members of this group.
        """
        # Remove what is there, then add it back.
        self._delete_from_db(principalUID)
        self._add_to_db(principalUID, members)
        self._db_commit()
        
    @inlineCallbacks
    def removeGroup(self, principalUID):
        """
        Remove a group membership record.

        @param principalUID: the UID of the group principal to remove.
        """

        # Need to get the members before we do the delete
        members = yield self.getMembers(principalUID)

        self._delete_from_db(principalUID)
        self._db_commit()
        
        # Update cache
        if members:
            for member in members:
                yield self._memcacher.deleteMembership(member)
            yield self._memcacher.deleteMember(principalUID)

    @inlineCallbacks
    def removePrincipal(self, principalUID, delay=None):
        """
        Remove a group membership record.

        @param principalUID: the UID of the principal to remove.
        """

        if delay:
            # We are going to remove the principal only after <delay> seconds
            # has passed since we first chose to remove it, to protect against
            # transient directory problems.
            # If <delay> is specified, first see if there was a timer set
            # previously.  If the timer is more than delay seconds old, we
            # go ahead and remove the principal.  Otherwise, do nothing.

            overdue = yield self._memcacher.checkDeletionTimer(principalUID)

            if overdue == False:
                # Do nothing
                returnValue(None)

            elif overdue is None:
                # No timer was previously set
                self.log_debug("Delaying removal of missing proxy principal '%s'"
                               % (principalUID,))
                self._memcacher.setDeletionTimer(principalUID, delay=delay)
                returnValue(None)

        self.log_warn("Removing missing proxy principal for '%s'"
                      % (principalUID,))

        for suffix in ("calendar-proxy-read", "calendar-proxy-write",):
            groupUID = "%s#%s" % (principalUID, suffix,)
            self._delete_from_db(groupUID)

            # Update cache
            members = yield self.getMembers(groupUID)
            if members:
                for member in members:
                    yield self._memcacher.deleteMembership(member)
                yield self._memcacher.deleteMember(groupUID)

        memberships = (yield self.getMemberships(principalUID))
        for groupUID in memberships:
            yield self._memcacher.deleteMember(groupUID)

        self._delete_from_db_member(principalUID)
        yield self._memcacher.deleteMembership(principalUID)
        self._db_commit()
        self._memcacher.clearDeletionTimer(principalUID)

    def getMembers(self, principalUID):
        """
        Return the list of group member UIDs for the specified principal.

        @return: a deferred returning a C{set} of members.
        """
        def gotCachedMembers(members):
            if members is not None:
                return members

            # Cache miss; compute members and update cache
            members = set([
                row[0] for row in
                self._db_execute("select MEMBER from GROUPS where GROUPNAME = :1", principalUID)
            ])
            d = self._memcacher.setMembers(principalUID, members)
            d.addCallback(lambda _: members)
            return d

        d = self._memcacher.getMembers(principalUID)
        d.addCallback(gotCachedMembers)
        return d

    def getMemberships(self, principalUID):
        """
        Return the list of group principal UIDs the specified principal is a member of.
        
        @return: a deferred returning a C{set} of memberships.
        """
        def gotCachedMemberships(memberships):
            if memberships is not None:
                return memberships

            # Cache miss; compute memberships and update cache
            memberships = set([
                row[0] for row in
                self._db_execute("select GROUPNAME from GROUPS where MEMBER = :1", principalUID)
            ])
            d = self._memcacher.setMemberships(principalUID, memberships)
            d.addCallback(lambda _: memberships)
            return d

        d = self._memcacher.getMemberships(principalUID)
        d.addCallback(gotCachedMemberships)
        return d

    def _add_to_db(self, principalUID, members):
        """
        Insert the specified entry into the database.

        @param principalUID: the UID of the group principal to add.
        @param members: a list of UIDs or principals that are members of this group.
        """
        for member in members:
            self._db_execute(
                """
                insert into GROUPS (GROUPNAME, MEMBER)
                values (:1, :2)
                """, principalUID, member
            )

    def _delete_from_db(self, principalUID):
        """
        Deletes the specified entry from the database.

        @param principalUID: the UID of the group principal to remove.
        """
        self._db_execute("delete from GROUPS where GROUPNAME = :1", principalUID)

    def _delete_from_db_member(self, principalUID):
        """
        Deletes the specified member entry from the database.

        @param principalUID: the UID of the member principal to remove.
        """
        self._db_execute("delete from GROUPS where MEMBER = :1", principalUID)

    def _db_version(self):
        """
        @return: the schema version assigned to this index.
        """
        return CalendarUserProxyDatabase.dbFormatVersion

    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return CalendarUserProxyDatabase.dbType

    def _db_init_data_tables(self, q):
        """
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """

        #
        # GROUPS table
        #
        q.execute(
            """
            create table GROUPS (
                GROUPNAME   text,
                MEMBER      text
            )
            """
        )
        q.execute(
            """
            create index GROUPNAMES on GROUPS (GROUPNAME)
            """
        )
        q.execute(
            """
            create index MEMBERS on GROUPS (MEMBER)
            """
        )

    def _db_upgrade_data_tables(self, q, old_version):
        """
        Upgrade the data from an older version of the DB.
        @param q: a database cursor to use.
        @param old_version: existing DB's version number
        @type old_version: str
        """

        # Add index if old version is less than "4"
        if int(old_version) < 4:
            q.execute(
                """
                create index GROUPNAMES on GROUPS (GROUPNAME)
                """
            )
            q.execute(
                """
                create index MEMBERS on GROUPS (MEMBER)
                """
            )



##
# Utilities
##

authReadACL = davxml.ACL(
    # Read access for authenticated users.
    davxml.ACE(
        davxml.Principal(davxml.Authenticated()),
        davxml.Grant(davxml.Privilege(davxml.Read())),
        davxml.Protected(),
    ),
)
