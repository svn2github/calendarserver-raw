##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
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
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

"""
Implements a directory-backed principal hierarchy.
"""

__all__ = [
    "DirectoryPrincipalProvisioningResource",
    "DirectoryPrincipalTypeResource",
    "DirectoryPrincipalResource",
]

from urllib import unquote

from twisted.python import log
from twisted.python.failure import Failure
from twisted.internet.defer import succeed
from twisted.web2 import responsecode
from twisted.web2.http import Response, HTTPError
from twisted.web2.http_headers import MimeType
from twisted.web2.dav import davxml
from twisted.web2.dav.util import joinURL

from twistedcaldav.extensions import ReadOnlyResourceMixIn, DAVFile
from twistedcaldav.resource import CalendarPrincipalCollectionResource, CalendarPrincipalResource
from twistedcaldav.static import provisionFile
from twistedcaldav.dropbox import DropBox
from twistedcaldav.directory.idirectory import IDirectoryService

# FIXME: These should not be tied to DAVFile
# The reason that they is that web2.dav only implements DAV methods on
# DAVFile instead of DAVResource.  That should change.

class PermissionsMixIn (ReadOnlyResourceMixIn):
    def defaultAccessControlList(self):
        return authReadACL

    def accessControlList(self, request, inheritance=True, expanding=False, inherited_aces=None):
        # Permissions here are fixed, and are not subject to inherritance rules, etc.
        return succeed(self.defaultAccessControlList())

class DirectoryPrincipalProvisioningResource (PermissionsMixIn, CalendarPrincipalCollectionResource, DAVFile):
    """
    Collection resource which provisions directory principals as its children.
    """
    def __init__(self, path, url, directory):
        """
        @param path: the path to the file which will back the resource.
        @param url: the canonical URL for the resource.
        @param directory: an L{IDirectoryService} to provision principals from.
        """
        assert url.endswith("/"), "Collection URL must end in '/'"

        CalendarPrincipalCollectionResource.__init__(self, url)
        DAVFile.__init__(self, path)

        self.directory = IDirectoryService(directory)

        # FIXME: Smells like a hack
        directory.principalCollection = self

        self.provision()

        # Create children
        for recordType in self.directory.recordTypes():
            self.putChild(recordType, DirectoryPrincipalTypeResource(self.fp.child(recordType).path, self, recordType))

    def provision(self):
        provisionFile(self)

    def principalForUser(self, user):
        return self.getChild("user").getChild(user)

    def principalForRecord(self, record):
        typeResource = self.getChild(record.recordType)
        if typeResource is None:
            return None
        return typeResource.getChild(record.shortName)

    def _principalForURI(self, uri):
        if uri.startswith(self._url):
            path = uri[len(self._url) - 1:]
        else:
            #TODO: figure out absolute URIs
            #absuri = request.unparseURL(path=self._url)
            #if uri.startswith(absuri):
            #    path = uri[len(absuri) - 1:]
            #else:
            #    path = None
            path = None
        
        if path:
            segments = [unquote(s) for s in path.split("/")]
            if segments[0] == "" and len(segments) == 3:
                typeResource = self.getChild(segments[1])
                if typeResource is not None:
                    principalResource = typeResource.getChild(segments[2])
                    if principalResource:
                        return principalResource
            
        return None

    def principalForCalendarUserAddress(self, address):
        # First see if the address is a principal URI
        principal = self._principalForURI(address)
        if principal:
            return principal

        record = self.directory.recordWithCalendarUserAddress(address)
        if record is None:
            return None
        else:
            return self.principalForRecord(record)

    ##
    # Static
    ##

    def createSimilarFile(self, path):
        raise HTTPError(responsecode.NOT_FOUND)

    def getChild(self, name):
        self.provision()
        return self.putChildren.get(name, None)

    def listChildren(self):
        return self.putChildren.keys()

    ##
    # ACL
    ##

    def principalCollections(self):
        return (self,)

class DirectoryPrincipalTypeResource (PermissionsMixIn, CalendarPrincipalCollectionResource, DAVFile):
    """
    Collection resource which provisions directory principals of a specific type as its children.
    """
    def __init__(self, path, parent, recordType):
        """
        @param path: the path to the file which will back the resource.
        @param directory: an L{IDirectoryService} to provision calendars from.
        @param recordType: the directory record type to provision.
        """
        CalendarPrincipalCollectionResource.__init__(self, joinURL(parent.principalCollectionURL(), recordType) + "/")
        DAVFile.__init__(self, path)

        self.directory = parent.directory
        self.recordType = recordType
        self._parent = parent

        self.provision()

    def provision(self):
        provisionFile(self, self._parent)

    def principalForUser(self, user):
        return self._parent.principalForUser(user)

    def principalForRecord(self, record):
        return self._parent.principalForRecord(record)

    def principalForCalendarUserAddress(self, address):
        return self._parent.principalForCalendarUserAddress(address)

    ##
    # Static
    ##

    def createSimilarFile(self, path):
        raise HTTPError(responsecode.NOT_FOUND)

    def getChild(self, name, record=None):
        self.provision()

        if name == "":
            return self

        if record is None:
            record = self.directory.recordWithShortName(self.recordType, name)
            if record is None:
                return None
        else:
            assert name is None
            name = record.shortName

        return DirectoryPrincipalResource(self.fp.child(name).path, self, record)

    def listChildren(self):
        return (record.shortName for record in self.directory.listRecords(self.recordType))

    ##
    # ACL
    ##

    def principalCollections(self):
        return self._parent.principalCollections()

class DirectoryPrincipalResource (PermissionsMixIn, CalendarPrincipalResource, DAVFile):
    """
    Directory principal resource.
    """
    def __init__(self, path, parent, record):
        """
        @param path: them path to the file which will back this resource.
        @param parent: the parent of this resource.
        @param record: the L{IDirectoryRecord} that this resource represents.
        """
        super(DirectoryPrincipalResource, self).__init__(path, joinURL(parent.principalCollectionURL(), record.shortName))

        self.record = record
        self._parent = parent
        self._url = joinURL(parent.principalCollectionURL(), record.shortName)

        self.provision()

    def provision(self):
        provisionFile(self, self._parent, True)

    ##
    # HTTP
    ##

    def render(self, request):
        def format_list(method, *args):
            def genlist():
                try:
                    item = None
                    for item in method(*args):
                        yield " -> %s\n" % (item,)
                    if item is None:
                        yield " '()\n"
                except Exception, e:
                    log.err("Exception while rendering: %s" % (e,))
                    Failure().printTraceback()
                    yield "  ** %s **: %s\n" % (e.__class__.__name__, e)
            return "".join(genlist())

        output = ("".join((
            "Principal resource\n"
            "------------------\n"
            "\n"
           #"Directory service: %s\n"      % (self.record.service,),
            "Record type: %s\n"            % (self.record.recordType,),
            "GUID: %s\n"                   % (self.record.guid,),
            "Short name: %s\n"             % (self.record.shortName,),
            "Full name: %s\n"              % (self.record.fullName,),
            "Principal UID: %s\n"          % (self.principalUID(),),
            "Principal URL: %s\n"          % (self.principalURL(),),
            "\nAlternate URIs:\n"          , format_list(self.alternateURIs),
            "\nGroup members:\n"           , format_list(self.groupMembers),
            "\nGroup memberships:\n"       , format_list(self.groupMemberships),
            "\nCalendar homes:\n"          , format_list(self.calendarHomeURLs),
            "\nCalendar user addresses:\n" , format_list(self.calendarUserAddresses),
        )))

        if type(output) == unicode:
            output = output.encode("utf-8")
            mime_params = {"charset": "utf-8"}
        else:
            mime_params = {}

        response = Response(code=responsecode.OK, stream=output)
        response.headers.setHeader("content-type", MimeType("text", "plain", mime_params))

        return response

    ##
    # DAV
    ##

    def displayName(self):
        if self.record.fullName:
            return self.record.fullName
        else:
            return self.record.shortName

    ##
    # ACL
    ##

    def alternateURIs(self):
        # FIXME: Add API to IDirectoryRecord for getting a record URI?
        return ()

    def principalURL(self):
        return self._url

    def _getRelatives(self, method, record=None, relatives=None, records=None):
        if record is None:
            record = self.record
        if relatives is None:
            relatives = set()
        if records is None:
            records = set()

        if record not in records:
            records.add(record)
            myRecordType = self.record.recordType
            for relative in getattr(record, method)():
                if relative not in records:
                    if relative.recordType == myRecordType: 
                        relatives.add(self._parent.getChild(None, record=relative))
                    else:
                        relatives.add(self._parent._parent.getChild(relative.recordType).getChild(None, record=relative))
                    self._getRelatives(method, relative, relatives, records)

        return relatives

    def groupMembers(self):
        return self._getRelatives("members")

    def groupMemberships(self):
        return self._getRelatives("groups")

    def principalCollections(self):
        return self._parent.principalCollections()

    ##
    # CalDAV
    ##

    def principalUID(self):
        return self.record.shortName
        
    def calendarUserAddresses(self):
        # Add the principal URL to whatever calendar user addresses
        # the directory record provides.
        return (self.principalURL(),) + tuple(self.record.calendarUserAddresses)

    def scheduleInbox(self, request):
        home = self._calendarHome()
        if home is None:
            return succeed(None)

        inbox = home.getChild("inbox")
        if inbox is None:
            return succeed(None)

        return succeed(inbox)

    def calendarHomeURLs(self):
        home = self._calendarHome()
        if home is None:
            return ()
        else:
            return (home.url(),)

    def scheduleInboxURL(self):
        return self._homeChildURL("inbox/")

    def scheduleOutboxURL(self):
        return self._homeChildURL("outbox/")

    def dropboxURL(self):
        return self._homeChildURL(DropBox.dropboxName + "/")

    def notificationsURL(self):
        return self._homeChildURL(DropBox.notificationName + "/")

    def _homeChildURL(self, name):
        home = self._calendarHome()
        if home is None:
            return None
        else:
            return joinURL(home.url(), name)

    def _calendarHome(self):
        # FIXME: self.record.service.calendarHomesCollection smells like a hack
        # See CalendarHomeProvisioningFile.__init__()
        service = self.record.service
        if hasattr(service, "calendarHomesCollection"):
            return service.calendarHomesCollection.homeForDirectoryRecord(self.record)
        else:
            return None

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
