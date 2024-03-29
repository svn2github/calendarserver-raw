# -*- test-case-name: twistedcaldav.directory.test.test_principal -*-
##
# Copyright (c) 2006-2012 Apple Inc. All rights reserved.
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
Implements a directory-backed principal hierarchy.
"""

__all__ = [
    "DirectoryProvisioningResource",
    "DirectoryPrincipalProvisioningResource",
    "DirectoryPrincipalTypeProvisioningResource",
    "DirectoryPrincipalUIDProvisioningResource",
    "DirectoryPrincipalResource",
    "DirectoryCalendarPrincipalResource",
]

from urllib import unquote
from urlparse import urlparse

from twisted.cred.credentials import UsernamePassword
from twisted.python.failure import Failure
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.defer import succeed
from twisted.web.template import XMLFile, Element, renderer, tags

from twext.web2.auth.digest import DigestedCredentials
from twext.web2 import responsecode
from twext.web2.http import HTTPError
from twext.web2.dav import davxml
from twext.web2.dav.util import joinURL
from twext.web2.dav.noneprops import NonePropertyStore

from twext.python.log import Logger

try:
    from twistedcaldav.authkerb import NegotiateCredentials
    NegotiateCredentials # sigh, pyflakes
except ImportError:
    NegotiateCredentials = None
from twisted.python.modules import getModule

from twistedcaldav import caldavxml, customxml
from twistedcaldav.cache import DisabledCacheNotifier, PropfindCacheMixin
from twistedcaldav.config import config
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.directory.augment import allowedAutoScheduleModes
from twistedcaldav.directory.common import uidsResourceName
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.idirectory import IDirectoryService
from twistedcaldav.directory.wiki import getWikiACL
from twistedcaldav.extensions import DirectoryElement
from twistedcaldav.extensions import ReadOnlyResourceMixIn, DAVPrincipalResource,\
    DAVResourceWithChildrenMixin
from twistedcaldav.resource import CalendarPrincipalCollectionResource, CalendarPrincipalResource
from twistedcaldav.scheduling.cuaddress import normalizeCUAddr

thisModule = getModule(__name__)
log = Logger()


class PermissionsMixIn (ReadOnlyResourceMixIn):
    def defaultAccessControlList(self):
        return authReadACL


    @inlineCallbacks
    def accessControlList(self, request, inheritance=True, expanding=False, inherited_aces=None):

        try:
            wikiACL = (yield getWikiACL(self, request))
        except HTTPError:
            wikiACL = None

        if wikiACL is not None:
            # ACL depends on wiki server...
            log.debug("Wiki ACL: %s" % (wikiACL.toxml(),))
            returnValue(wikiACL)
        else:
            # ...otherwise permissions are fixed, and are not subject to
            # inheritance rules, etc.
            returnValue(self.defaultAccessControlList())


# Converter methods for recordsMatchingFields()
#
# A DAV property can be associated with one of these converter methods,
# which take the string being matched and return the appropriate record
# field name to match against, as well as a new match string which has been
# converted to the appropriate form.

def cuTypeConverter(cuType):
    """ Converts calendar user types to OD type names """

    return "recordType", DirectoryRecord.fromCUType(cuType)

def cuAddressConverter(origCUAddr):
    """ Converts calendar user addresses to OD-compatible form """

    cua = normalizeCUAddr(origCUAddr)

    if cua.startswith("urn:uuid:"):
        return "guid", cua[9:]

    elif cua.startswith("mailto:"):
        return "emailAddresses", cua[7:]

    elif cua.startswith("/") or cua.startswith("http"):
        ignored, collection, id = cua.rsplit("/", 2)
        if collection == "__uids__":
            return "guid", id
        else:
            return "recordName", id

    else:
        raise ValueError("Invalid calendar user address format: %s" %
            (origCUAddr,))


class DirectoryProvisioningResource (
    PermissionsMixIn,
    CalendarPrincipalCollectionResource,
):
    def __init__(self, url, directory):
        """
        @param url: the canonical URL for the resource.
        @param directory: an L{IDirectoryService} to provision principals from.
        """
        assert url.endswith("/"), "Collection URL must end in '/'"

        CalendarPrincipalCollectionResource.__init__(self, url)
        DAVResourceWithChildrenMixin.__init__(self)

        self.directory = IDirectoryService(directory)

    def __repr__(self):
        return "<%s: %s %s>" % (self.__class__.__name__, self.directory, self._url)

    def locateChild(self, req, segments):
        child = self.getChild(segments[0])
        if child is not None:
            return (child, segments[1:])
        return (None, ())

    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties

    def etag(self):
        return succeed(None)

    def principalForShortName(self, recordType, name):
        return self.principalForRecord(self.directory.recordWithShortName(recordType, name))

    def principalForUser(self, user):
        return self.principalForShortName(DirectoryService.recordType_users, user)

    def principalForAuthID(self, user):
        # Basic/Digest creds -> just lookup user name
        if isinstance(user, UsernamePassword) or isinstance(user, DigestedCredentials):
            return self.principalForUser(user.username)
        elif NegotiateCredentials is not None and isinstance(user, NegotiateCredentials):
            authID = "Kerberos:%s" % (user.principal,)
            principal = self.principalForRecord(self.directory.recordWithAuthID(authID))
            if principal:
                return principal
            elif user.username:
                return self.principalForUser(user.username)
        
        return None

    def principalForUID(self, uid):
        raise NotImplementedError("Subclass must implement principalForUID()")

    def principalForCalendarUserAddress(self, address):
        raise NotImplementedError("Subclass must implement principalForCalendarUserAddress()")

    def principalForRecord(self, record):
        if record is None or not record.enabled:
            return None
        return self.principalForUID(record.uid)

    ##
    # DAV-property-to-record-field mapping
    ##

    _cs_ns = "http://calendarserver.org/ns/"
    _fieldMap = {
        ("DAV:" , "displayname") :
            ("fullName", None, "Display Name", davxml.DisplayName),
        ("urn:ietf:params:xml:ns:caldav" , "calendar-user-type") :
            ("", cuTypeConverter, "Calendar User Type",
            caldavxml.CalendarUserType),
        ("urn:ietf:params:xml:ns:caldav" , "calendar-user-address-set") :
            ("", cuAddressConverter, "Calendar User Address Set",
            caldavxml.CalendarUserAddressSet),
        (_cs_ns, "first-name") :
            ("firstName", None, "First Name", customxml.FirstNameProperty),
        (_cs_ns, "last-name") :
            ("lastName", None, "Last Name", customxml.LastNameProperty),
        (_cs_ns, "email-address-set") :
            ("emailAddresses", None, "Email Addresses",
            customxml.EmailAddressSet),
    }

    def propertyToField(self, property, match):
        """
        If property is a DAV property that maps to a directory field, return
        that field's name, otherwise return None
        """
        field, converter, _ignore_description, _ignore_xmlClass = self._fieldMap.get(
            property.qname(), (None, None, None, None))
        if field is None:
            return (None, None)
        elif converter is not None:
            field, match = converter(match)
        return (field, match)

    def principalSearchPropertySet(self):
        props = []
        for _ignore_field, _ignore_converter, description, xmlClass in self._fieldMap.itervalues():
            props.append(
                davxml.PrincipalSearchProperty(
                    davxml.PropertyContainer(
                        xmlClass()
                    ),
                    davxml.Description(
                        davxml.PCDATAElement(description),
                        **{"xml:lang":"en"}
                    ),
                )
            )

        return davxml.PrincipalSearchPropertySet(*props)


class DirectoryPrincipalProvisioningResource (DirectoryProvisioningResource):
    """
    Collection resource which provisions directory principals as its children.
    """
    def __init__(self, url, directory):
        DirectoryProvisioningResource.__init__(self, url, directory)

        # FIXME: Smells like a hack
        self.directory.principalCollection = self

        #
        # Create children
        #
        for recordType in self.directory.recordTypes():
            self.putChild(recordType, DirectoryPrincipalTypeProvisioningResource(self, recordType))

        self.putChild(uidsResourceName, DirectoryPrincipalUIDProvisioningResource(self))

    def principalForUID(self, uid):
        return self.getChild(uidsResourceName).getChild(uid)

    def _principalForURI(self, uri):
        scheme, netloc, path, _ignore_params, _ignore_query, _ignore_fragment = urlparse(uri)

        if scheme == "":
            pass

        elif scheme in ("http", "https"):
            # Get rid of possible user/password nonsense
            netloc = netloc.split("@", 1)[-1]

            # Get host/port
            netloc = netloc.split(":", 1)

            host = netloc[0]
            if len(netloc) == 1 or netloc[1] == "":
                port = 80
            else:
                port = int(netloc[1])

            if (host != config.ServerHostName and
                host not in config.Scheduling.Options.PrincipalHostAliases):
                return None

            if port != {
                "http" : config.HTTPPort,
                "https": config.SSLPort,
            }[scheme]:
                return None

        elif scheme == "urn":
            if path.startswith("uuid:"):
                return self.principalForUID(path[5:])
            else:
                return None
        else:
            return None

        if not path.startswith(self._url):
            return None

        path = path[len(self._url) - 1:]

        segments = [unquote(s) for s in path.rstrip("/").split("/")]
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
            if isinstance(principal, DirectoryCalendarPrincipalResource) and principal.record.enabledForCalendaring:
                return principal
        else:
            # Next try looking it up in the directory
            record = self.directory.recordWithCalendarUserAddress(address)
            if record is not None and record.enabled and record.enabledForCalendaring:
                return self.principalForRecord(record)

        log.debug("No principal for calendar user address: %r" % (address,))
        return None

    def principalForRecord(self, record):
        return self.getChild(uidsResourceName).principalForRecord(record)

    ##
    # Static
    ##

    def createSimilarFile(self, path):
        log.err("Attempt to create clone %r of resource %r" % (path, self))
        raise HTTPError(responsecode.NOT_FOUND)

    def getChild(self, name):
        if name == "":
            return self
        else:
            return self.putChildren.get(name, None)

    def listChildren(self):
        return self.directory.recordTypes()

    ##
    # ACL
    ##

    def principalCollections(self):
        return (self,)


class DirectoryPrincipalTypeProvisioningResource (DirectoryProvisioningResource):
    """
    Collection resource which provisions directory principals of a
    specific type as its children, indexed by short name.
    """
    def __init__(self, parent, recordType):
        """
        @param parent: the parent L{DirectoryPrincipalProvisioningResource}.
        @param recordType: the directory record type to provision.
        """
        DirectoryProvisioningResource.__init__(
            self,
            joinURL(parent.principalCollectionURL(), recordType) + "/",
            parent.directory
        )

        self.recordType = recordType
        self.parent = parent

    def principalForUID(self, uid):
        return self.parent.principalForUID(uid)

    def principalForCalendarUserAddress(self, address):
        return self.parent.principalForCalendarUserAddress(address)

    def principalForRecord(self, record):
        return self.parent.principalForRecord(record)

    ##
    # Static
    ##

    def createSimilarFile(self, path):
        log.err("Attempt to create clone %r of resource %r" % (path, self))
        raise HTTPError(responsecode.NOT_FOUND)

    def getChild(self, name):
        if name == "":
            return self
        else:
            return self.principalForShortName(self.recordType, name)

    def listChildren(self):
        if config.EnablePrincipalListings:

            def _recordShortnameExpand():
                for record in self.directory.listRecords(self.recordType):
                    if record.enabled:
                        for shortName in record.shortNames:
                            yield shortName

            return _recordShortnameExpand()
        else:
            # Not a listable collection
            raise HTTPError(responsecode.FORBIDDEN)

    ##
    # ACL
    ##

    def principalCollections(self):
        return self.parent.principalCollections()


class DirectoryPrincipalUIDProvisioningResource (DirectoryProvisioningResource):
    """
    Collection resource which provisions directory principals indexed
    by UID.
    """
    def __init__(self, parent):
        """
        @param directory: an L{IDirectoryService} to provision calendars from.
        @param recordType: the directory record type to provision.
        """
        DirectoryProvisioningResource.__init__(
            self,
            joinURL(parent.principalCollectionURL(), uidsResourceName) + "/",
            parent.directory
        )

        self.parent = parent

    def principalForUID(self, uid):
        return self.parent.principalForUID(uid)

    def principalForCalendarUserAddress(self, address):
        return self.parent.principalForCalendarUserAddress(address)

    def principalForRecord(self, record):
        if record is None or not record.enabled:
            return None

        if record.enabledForCalendaring or record.enabledForAddressBooks:
            # XXX these are different features and one should not automatically
            # imply the other...
            principal = DirectoryCalendarPrincipalResource(self, record)
        else:
            principal = DirectoryPrincipalResource(self, record)
        return principal

    ##
    # Static
    ##

    def createSimilarFile(self, path):
        log.err("Attempt to create clone %r of resource %r" % (path, self))
        raise HTTPError(responsecode.NOT_FOUND)

    def getChild(self, name):
        if name == "":
            return self

        if "#" in name:
            # This UID belongs to a sub-principal
            primaryUID, subType = name.split("#")
        else:
            primaryUID = name
            subType = None

        record = self.directory.recordWithUID(primaryUID)
        primaryPrincipal = self.principalForRecord(record)
        if primaryPrincipal is None:
            log.info("No principal found for UID: %s" % (name,))
            return None

        if subType is None:
            return primaryPrincipal
        else:
            return primaryPrincipal.getChild(subType)

    def listChildren(self):
        # Not a listable collection
        raise HTTPError(responsecode.FORBIDDEN)

    ##
    # ACL
    ##

    def principalCollections(self):
        return self.parent.principalCollections()



class DirectoryPrincipalDetailElement(Element):
    """
    Element that can render the details of a
    L{CalendarUserDirectoryPrincipalResource}.
    """

    loader = XMLFile(thisModule.filePath.sibling(
        "directory-principal-resource.html").open()
    )

    def __init__(self, resource):
        super(DirectoryPrincipalDetailElement, self).__init__()
        self.resource = resource


    @renderer
    def serversEnabled(self, request, tag):
        """
        Renderer for when servers are enabled.
        """
        if not config.Servers.Enabled:
            return ""
        record = self.resource.record
        return tag.fillSlots(
            hostedAt=str(record.serverURI()),
            partition=str(record.effectivePartitionID()),
        )


    @renderer
    def principal(self, request, tag):
        """
        Top-level renderer in the template.
        """
        record = self.resource.record
        return tag.fillSlots(
            directoryGUID=str(record.service.guid),
            realm=str(record.service.realmName),
            principalGUID=str(record.guid),
            recordType=str(record.recordType),
            shortNames=",".join(record.shortNames),
            securityIDs=",".join(record.authIDs),
            fullName=str(record.fullName),
            firstName=str(record.firstName),
            lastName=str(record.lastName),
            emailAddresses=formatList(record.emailAddresses),
            principalUID=str(self.resource.principalUID()),
            principalURL=formatLink(self.resource.principalURL()),
            alternateURIs=formatLinks(self.resource.alternateURIs()),
            groupMembers=self.resource.groupMembers().addCallback(
                formatPrincipals
            ),
            groupMemberships=self.resource.groupMemberships().addCallback(
                formatPrincipals
            ),
            readWriteProxyFor=self.resource.proxyFor(True).addCallback(
                formatPrincipals
            ),
            readOnlyProxyFor=self.resource.proxyFor(False).addCallback(
                formatPrincipals
            ),
        )


    @renderer
    def extra(self, request, tag):
        """
        No-op; implemented in subclass.
        """
        return ''


    @renderer
    def enabledForCalendaring(self, request, tag):
        """
        No-op; implemented in subclass.
        """
        return ''


    @renderer
    def enabledForAddressBooks(self, request, tag):
        """
        No-op; implemented in subclass.
        """
        return ''



class DirectoryPrincipalElement(DirectoryElement):
    """
    L{DirectoryPrincipalElement} is a renderer for directory details.
    """

    @renderer
    def resourceDetail(self, request, tag):
        """
        Render the directory principal's details.
        """
        return DirectoryPrincipalDetailElement(self.resource)


class DirectoryCalendarPrincipalDetailElement(DirectoryPrincipalDetailElement):

    @renderer
    def extra(self, request, tag):
        """
        Renderer for extra directory body items for calendar/addressbook
        principals.
        """
        return tag


    @renderer
    def enabledForCalendaring(self, request, tag):
        """
        Renderer which returns its tag when the wrapped record is enabled for
        calendaring.
        """
        resource = self.resource
        record = resource.record
        if record.enabledForCalendaring:
            return tag.fillSlots(
                calendarUserAddresses=formatLinks(
                    resource.calendarUserAddresses()
                ),
                calendarHomes=formatLinks(resource.calendarHomeURLs())
            )
        return ''


    @renderer
    def enabledForAddressBooks(self, request, tag):
        """
        Renderer which returnst its tag when the wrapped record is enabled for
        addressbooks.
        """
        resource = self.resource
        record = resource.record
        if record.enabledForAddressBooks:
            return tag.fillSlots(
                addressBookHomes=formatLinks(resource.addressBookHomeURLs())
            )
        return ''



class DirectoryCalendarPrincipalElement(DirectoryPrincipalElement):
    """
    L{DirectoryPrincipalElement} is a renderer for directory details, with
    calendaring additions.
    """

    @renderer
    def resourceDetail(self, request, tag):
        """
        Render the directory calendar principal's details.
        """
        return DirectoryCalendarPrincipalDetailElement(self.resource)


class DirectoryPrincipalResource (
        PropfindCacheMixin, PermissionsMixIn, DAVPrincipalResource):
    """
    Directory principal resource.
    """

    def liveProperties(self):
        
        return super(DirectoryPrincipalResource, self).liveProperties() + (
            (calendarserver_namespace, "first-name"       ),
            (calendarserver_namespace, "last-name"        ),
            (calendarserver_namespace, "email-address-set"),
            davxml.ResourceID.qname(),
        )

    cacheNotifierFactory = DisabledCacheNotifier

    def __init__(self, parent, record):
        """
        @param parent: the parent of this resource.
        @param record: the L{IDirectoryRecord} that this resource represents.
        """
        super(DirectoryPrincipalResource, self).__init__()

        self.cacheNotifier = self.cacheNotifierFactory(self, cacheHandle="PrincipalToken")

        if self.isCollection():
            slash = "/"
        else:
            slash = ""

        assert record is not None, "Principal must have a directory record"


        self.record = record
        self.parent = parent

        url = joinURL(parent.principalCollectionURL(), self.principalUID()) + slash
        self._url   = url

        self._alternate_urls = tuple([
            joinURL(parent.parent.principalCollectionURL(), record.recordType, shortName) + slash for shortName in record.shortNames
        ])

    def __str__(self):
        return "(%s)%s" % (self.record.recordType, self.record.shortNames[0])

    def __eq__(self, other):
        """
        Principals are the same if their principalURLs are the same.
        """
        return (self.principalURL() == other.principalURL()) if isinstance(other, DirectoryPrincipalResource) else False

    def __ne__(self, other):
        return not self.__eq__(other)

    @inlineCallbacks
    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        namespace, name = qname

        if qname == davxml.ResourceID.qname():
            returnValue(davxml.ResourceID(davxml.HRef.fromString("urn:uuid:%s" % (self.record.guid,))))
        elif namespace == calendarserver_namespace:
            if name == "first-name":
                firstName = self.record.firstName
                if firstName is not None:
                    returnValue(customxml.FirstNameProperty(firstName))
                else:
                    returnValue(None)

            elif name == "last-name":
                lastName = self.record.lastName
                if lastName is not None:
                    returnValue(customxml.LastNameProperty(lastName))
                else:
                    returnValue(None)

            elif name == "email-address-set":
                returnValue(customxml.EmailAddressSet(
                    *[customxml.EmailAddressProperty(addr) for addr in self.record.emailAddresses]
                ))

        result = (yield super(DirectoryPrincipalResource, self).readProperty(property, request))
        returnValue(result)

    def deadProperties(self):
        if not hasattr(self, "_dead_properties"):
            self._dead_properties = NonePropertyStore(self)
        return self._dead_properties

    def etag(self):
        return succeed(None)

    ##
    # HTTP
    ##

    def htmlElement(self):
        """
        Customize HTML rendering for directory principals.
        """
        return DirectoryPrincipalElement(self)

    ##
    # DAV
    ##

    def isCollection(self):
        return True

    def displayName(self):
        if self.record.fullName:
            return self.record.fullName
        else:
            return self.record.shortNames[0]

    ##
    # ACL
    ##

    def _calendar_user_proxy_index(self):
        """
        Return the SQL database for calendar user proxies.

        @return: the L{ProxyDB} for the principal collection.
        """

        # The db is located in the principal collection root
        from twistedcaldav.directory.calendaruserproxy import ProxyDBService
        return ProxyDBService


    def alternateURIs(self):
        # FIXME: Add API to IDirectoryRecord for getting a record URI?
        return self._alternate_urls

    def principalURL(self):
        return self._url

    def url(self):
        return self.principalURL()

    @inlineCallbacks
    def proxyFor(self, read_write, resolve_memberships=True):

        proxyFors = set()

        if resolve_memberships:
            cache = getattr(self.record.service, "groupMembershipCache", None)
            if cache:
                log.debug("proxyFor is using groupMembershipCache")
                guids = (yield self.record.cachedGroups())
                memberships = set()
                for guid in guids:
                    principal = self.parent.principalForUID(guid)
                    if principal:
                        memberships.add(principal)
            else:
                memberships = self._getRelatives("groups", infinity=True)

            for membership in memberships:
                results = (yield membership.proxyFor(read_write, False))
                proxyFors.update(results)

        if config.EnableProxyPrincipals:
            # Get proxy group UIDs and map to principal resources
            proxies = []
            memberships = (yield self._calendar_user_proxy_index().getMemberships(self.principalUID()))
            for uid in memberships:
                subprincipal = self.parent.principalForUID(uid)
                if subprincipal:
                    if subprincipal.isProxyType(read_write):
                        proxies.append(subprincipal.parent)
                else:
                    yield self._calendar_user_proxy_index().removeGroup(uid)

            proxyFors.update(proxies)

        uids = set()
        for principal in tuple(proxyFors):
            if principal.principalUID() in uids:
                proxyFors.remove(principal)
            else:
                uids.add(principal.principalUID())

        returnValue(proxyFors)

    def _getRelatives(self, method, record=None, relatives=None, records=None, proxy=None, infinity=False):
        if record is None:
            record = self.record
        if relatives is None:
            relatives = set()
        if records is None:
            records = set()

        if record not in records:
            records.add(record)
            for relative in getattr(record, method)():
                if relative not in records:
                    found = self.parent.principalForRecord(relative)
                    if found is None:
                        log.err("No principal found for directory record: %r" % (relative,))
                    else:
                        if proxy:
                            if proxy == "read-write":
                                found = found.getChild("calendar-proxy-write")
                            else:
                                found = found.getChild("calendar-proxy-read")
                        relatives.add(found)

                    if infinity:
                        self._getRelatives(method, relative, relatives, records,
                            infinity=infinity)

        return relatives

    def groupMembers(self):
        return succeed(self._getRelatives("members"))

    def expandedGroupMembers(self):
        return succeed(self._getRelatives("members", infinity=True))

    @inlineCallbacks
    def groupMemberships(self, infinity=False):

        cache = getattr(self.record.service, "groupMembershipCache", None)
        if cache:
            log.debug("groupMemberships is using groupMembershipCache")
            guids = (yield self.record.cachedGroups())
            groups = set()
            for guid in guids:
                principal = self.parent.principalForUID(guid)
                if principal:
                    groups.add(principal)
        else:
            groups = self._getRelatives("groups", infinity=infinity)

        if config.EnableProxyPrincipals:
            # Get proxy group UIDs and map to principal resources
            proxies = []
            memberships = (yield self._calendar_user_proxy_index().getMemberships(self.principalUID()))
            for uid in memberships:
                subprincipal = self.parent.principalForUID(uid)
                if subprincipal:
                    proxies.append(subprincipal)
                else:
                    yield self._calendar_user_proxy_index().removeGroup(uid)

            groups.update(proxies)

        returnValue(groups)

    def expandedGroupMemberships(self):
        return self.groupMemberships(infinity=True)

    def principalCollections(self):
        return self.parent.principalCollections()

    def principalUID(self):
        return self.record.uid

    def serverURI(self):
        return self.record.serverURI()

    def server(self):
        return self.record.server()

    def partitionURI(self):
        return self.record.partitionURI()

    def locallyHosted(self):
        return self.record.locallyHosted()
    
    def thisServer(self):
        return self.record.thisServer()
    
    ##
    # Extra resource info
    ##

    @inlineCallbacks
    def setAutoSchedule(self, autoSchedule):
        self.record.autoSchedule = autoSchedule
        augmentRecord = (yield self.record.service.augmentService.getAugmentRecord(self.record.guid, self.record.recordType))
        augmentRecord.autoSchedule = autoSchedule
        (yield self.record.service.augmentService.addAugmentRecords([augmentRecord]))

    def getAutoSchedule(self):
        return self.record.autoSchedule

    def canAutoSchedule(self):
        """
        Determine the auto-schedule state based on record state, type and config settings.
        """
        
        if config.Scheduling.Options.AutoSchedule.Enabled:
            if config.Scheduling.Options.AutoSchedule.Always or self.getAutoSchedule():
                if self.getCUType() != "INDIVIDUAL" or config.Scheduling.Options.AutoSchedule.AllowUsers:
                    return True
        return False

    @inlineCallbacks
    def setAutoScheduleMode(self, autoScheduleMode):
        self.record.autoScheduleMode = autoScheduleMode if autoScheduleMode in allowedAutoScheduleModes else "default"
        augmentRecord = (yield self.record.service.augmentService.getAugmentRecord(self.record.guid, self.record.recordType))
        augmentRecord.autoScheduleMode = autoScheduleMode
        (yield self.record.service.augmentService.addAugmentRecords([augmentRecord]))

    def getAutoScheduleMode(self):
        return self.record.autoScheduleMode

    def getCUType(self):
        return self.record.getCUType()

    ##
    # Static
    ##

    def createSimilarFile(self, path):
        log.err("Attempt to create clone %r of resource %r" % (path, self))
        raise HTTPError(responsecode.NOT_FOUND)

    def locateChild(self, req, segments):
        child = self.getChild(segments[0])
        if child is not None:
            return (child, segments[1:])
        return (None, ())

    def getChild(self, name):
        if name == "":
            return self

        return None

    def listChildren(self):
        return ()


class DirectoryCalendarPrincipalResource(DirectoryPrincipalResource,
                                         CalendarPrincipalResource):
    """
    Directory calendar principal resource.
    """

    def liveProperties(self):
        return DirectoryPrincipalResource.liveProperties(self) + CalendarPrincipalResource.liveProperties(self)

    def calendarsEnabled(self):
        return config.EnableCalDAV and self.record.enabledForCalendaring
    
    def addressBooksEnabled(self):
        return config.EnableCardDAV and self.record.enabledForAddressBooks
    
    @inlineCallbacks
    def readProperty(self, property, request):
        # Ouch, multiple inheritance.
        result = (yield DirectoryPrincipalResource.readProperty(self, property, request))
        if not result:
            result = (yield CalendarPrincipalResource.readProperty(self, property, request))
        returnValue(result)


    ##
    # CalDAV
    ##

    def calendarUserAddresses(self):

        # No CUAs if not enabledForCalendaring.
        if not self.record.enabledForCalendaring:
            return set()

        # Get any CUAs defined by the directory implementation.
        addresses = set(self.record.calendarUserAddresses)

        # Add the principal URL and alternate URIs to the list.
        for uri in ((self.principalURL(),) + tuple(self.alternateURIs())):
            addresses.add(uri)

        return addresses


    def htmlElement(self):
        """
        Customize HTML generation for calendar principals.
        """
        return DirectoryCalendarPrincipalElement(self)


    def canonicalCalendarUserAddress(self):
        """
        Return a CUA for this principal, preferring in this order:
            urn:uuid: form
            mailto: form
            first in calendarUserAddresses( ) list
        """

        cua = ""
        for candidate in self.calendarUserAddresses():
            # Pick the first one, but urn:uuid: and mailto: can override
            if not cua:
                cua = candidate
            # But always immediately choose the urn:uuid: form
            if candidate.startswith("urn:uuid:"):
                cua = candidate
                break
            # Prefer mailto: if no urn:uuid:
            elif candidate.startswith("mailto:"):
                cua = candidate
        return cua


    def enabledAsOrganizer(self):
        if self.record.recordType == DirectoryService.recordType_users:
            return True
        elif self.record.recordType == DirectoryService.recordType_groups:
            return config.Scheduling.Options.AllowGroupAsOrganizer
        elif self.record.recordType == DirectoryService.recordType_locations:
            return config.Scheduling.Options.AllowLocationAsOrganizer
        elif self.record.recordType == DirectoryService.recordType_resources:
            return config.Scheduling.Options.AllowResourceAsOrganizer
        else:
            return False

    @inlineCallbacks
    def scheduleInbox(self, request):
        home = yield self.calendarHome(request)
        if home is None:
            returnValue(None)

        inbox = yield home.getChild("inbox")
        if inbox is None:
            returnValue(None)

        returnValue(inbox)

    @inlineCallbacks
    def notificationCollection(self, request):

        notification = None
        if config.Sharing.Enabled:
            home = yield self.calendarHome(request)
            if home is not None:    
                notification = yield home.getChild("notification")
        returnValue(notification)

    def calendarHomeURLs(self):
        if self.record.enabledForCalendaring:
            homeURL = self._homeChildURL(None)
        else:
            homeURL = ""
        return (homeURL,) if homeURL else ()

    def scheduleInboxURL(self):
        return self._homeChildURL("inbox/")

    def scheduleOutboxURL(self):
        return self._homeChildURL("outbox/")

    def dropboxURL(self):
        if config.EnableDropBox:
            return self._homeChildURL("dropbox/")
        else:
            return None

    def notificationURL(self):
        if config.Sharing.Enabled:
            return self._homeChildURL("notification/")
        else:
            return None

    def addressBookHomeURLs(self):
        if self.record.enabledForAddressBooks:
            homeURL = self._addressBookHomeChildURL(None)
        else:
            homeURL = ""
        return (homeURL,) if homeURL else ()

    def _homeChildURL(self, name):
        if not hasattr(self, "calendarHomeURL"):
            if not hasattr(self.record.service, "calendarHomesCollection"):
                return None
            self.calendarHomeURL = joinURL(
                self.record.service.calendarHomesCollection.url(),
                uidsResourceName,
                self.record.uid
            ) + "/"
            
            # Prefix with other server if needed
            if not self.thisServer():
                self.calendarHomeURL = joinURL(self.serverURI(), self.calendarHomeURL)

        url = self.calendarHomeURL
        if url is None:
            return None
        else:
            return joinURL(url, name) if name else url


    def calendarHome(self, request):
        # FIXME: self.record.service.calendarHomesCollection smells like a hack
        service = self.record.service
        if hasattr(service, "calendarHomesCollection"):
            return service.calendarHomesCollection.homeForDirectoryRecord(self.record, request)
        else:
            return succeed(None)


    def _addressBookHomeChildURL(self, name):
        if not hasattr(self, "addressBookHomeURL"):
            if not hasattr(self.record.service, "addressBookHomesCollection"):
                return None
            self.addressBookHomeURL = joinURL(
                self.record.service.addressBookHomesCollection.url(),
                uidsResourceName,
                self.record.uid
            ) + "/"
            
            # Prefix with other server if needed
            if not self.thisServer():
                self.addressBookHomeURL = joinURL(self.serverURI(), self.addressBookHomeURL)

        url = self.addressBookHomeURL
        if url is None:
            return None
        else:
            return joinURL(url, name) if name else url

    def addressBookHome(self, request):
        # FIXME: self.record.service.addressBookHomesCollection smells like a hack
        service = self.record.service
        if hasattr(service, "addressBookHomesCollection"):
            return service.addressBookHomesCollection.homeForDirectoryRecord(self.record, request)
        else:
            return succeed(None)

    ##
    # Static
    ##

    def getChild(self, name):
        if name == "":
            return self

        if config.EnableProxyPrincipals and name in ("calendar-proxy-read",
                                                     "calendar-proxy-write"):
            # name is required to be str
            from twistedcaldav.directory.calendaruserproxy import (
                CalendarUserProxyPrincipalResource
            )
            return CalendarUserProxyPrincipalResource(self, str(name))
        else:
            return None

    def listChildren(self):
        if config.EnableProxyPrincipals:
            return ("calendar-proxy-read", "calendar-proxy-write")
        else:
            return ()

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


def formatPrincipals(principals):
    """
    Format a list of principals into some twisted.web.template DOM objects.
    """
    def recordKey(principal):
        try:
            record = principal.record
        except AttributeError:
            try:
                record = principal.parent.record
            except:
                return None
        return (record.recordType, record.shortNames[0])

    def describe(principal):
        if hasattr(principal, "record"):
            return " - %s" % (principal.record.fullName,)
        else:
            return ""

    return formatList(
        tags.a(href=principal.principalURL())(
            str(principal), describe(principal)
        )
        for principal in sorted(principals, key=recordKey)
    )


def formatList(iterable):
    """
    Format a list of stuff as an interable.
    """
    thereAreAny = False
    try:
        item = None
        for item in iterable:
            thereAreAny = True
            yield " -> "
            if item is None:
                yield "None"
            else:
                yield item
            yield "\n"
    except Exception, e:
        log.err("Exception while rendering: %s" % (e,))
        Failure().printTraceback()
        yield "  ** %s **: %s\n" % (e.__class__.__name__, e)
    if not thereAreAny:
        yield " '()\n"



def formatLink(url):
    """
    Convert a URL string into some twisted.web.template DOM objects for
    rendering as a link to itself.
    """
    return tags.a(href=url)(url)


def formatLinks(urls):
    """
    Format a list of URL strings as a list of twisted.web.template DOM links.
    """
    return formatList(formatLink(link) for link in urls)

