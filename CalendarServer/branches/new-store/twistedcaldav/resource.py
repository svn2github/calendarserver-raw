# -*- test-case-name: twistedcaldav.test.test_resource -*-
##
# Copyright (c) 2005-2010 Apple Inc. All rights reserved.
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
CalDAV-aware resources.
"""

__all__ = [
    "CalDAVComplianceMixIn",
    "CalDAVResource",
    "CalendarPrincipalCollectionResource",
    "CalendarPrincipalResource",
    "isCalendarCollectionResource",
    "isPseudoCalendarCollectionResource",
    "isAddressBookCollectionResource",
]

import urllib
from urlparse import urlsplit
import uuid

from zope.interface import implements

from twext.python.log import LoggingMixIn
from twext.web2.dav.davxml import SyncCollection
from twext.web2.dav.http import ErrorResponse

from twisted.internet import reactor
from twisted.internet.defer import Deferred, succeed, maybeDeferred
from twisted.internet.defer import inlineCallbacks, returnValue
from twext.web2 import responsecode
from twext.web2.dav import davxml
from twext.web2.dav.auth import AuthenticationWrapper as SuperAuthenticationWrapper
from twext.web2.dav.davxml import dav_namespace
from twext.web2.dav.idav import IDAVPrincipalCollectionResource
from twext.web2.dav.resource import AccessDeniedError, DAVPrincipalCollectionResource
from twext.web2.dav.resource import TwistedACLInheritable
from twext.web2.dav.util import joinURL, parentForURL, unimplemented, normalizeURL
from twext.web2.http import HTTPError, RedirectResponse, StatusResponse, Response
from twext.web2.http_headers import MimeType
from twext.web2.stream import MemoryStream

from twistedcaldav import caldavxml, customxml
from twistedcaldav import carddavxml
from twistedcaldav.carddavxml import carddav_namespace
from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.config import config
from twistedcaldav.customxml import TwistedCalendarAccessProperty
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.datafilters.peruserdata import PerUserDataFilter
from twistedcaldav.extensions import DAVResource, DAVPrincipalResource,\
    PropertyNotFoundError
from twistedcaldav.ical import Component
from twistedcaldav.ical import Component as iComponent
from twistedcaldav.ical import allowedComponents
from twistedcaldav.icaldav import ICalDAVResource, ICalendarPrincipalResource
from twistedcaldav.notify import getPubSubConfiguration, getPubSubPath
from twistedcaldav.notify import getNodeCacher, NodeCreationException
from twistedcaldav.sharing import SharedCollectionMixin
from twistedcaldav.vcard import Component as vComponent

from txdav.common.icommondatastore import InternalDataStoreError

##
# Sharing Conts
##
SHARE_ACCEPT_STATE_NEEDS_ACTION = "0"
SHARE_ACCEPT_STATE_ACCEPTED = "1"
SHARE_ACCEPT_STATE_DECLINED = "2"
SHARE_ACCEPT_STATE_DELETED = "-1"

shareAccpetStates = {}
shareAccpetStates[SHARE_ACCEPT_STATE_NEEDS_ACTION] = "NEEDS-ACTION"
shareAccpetStates[SHARE_ACCEPT_STATE_ACCEPTED] = "ACCEPTED"
shareAccpetStates[SHARE_ACCEPT_STATE_DECLINED] = "DECLINED"
shareAccpetStates[SHARE_ACCEPT_STATE_DELETED] = "DELETED"

shareAcceptStatesByXML = {}
shareAcceptStatesByXML["NEEDS-ACTION"] = customxml.InviteStatusNoResponse()
shareAcceptStatesByXML["ACCEPTED"] = customxml.InviteStatusAccepted()
shareAcceptStatesByXML["DECLINED"] = customxml.InviteStatusDeclined()
shareAcceptStatesByXML["DELETED"] = customxml.InviteStatusDeleted()

class CalDAVComplianceMixIn(object):
    def davComplianceClasses(self):
        return (
            tuple(super(CalDAVComplianceMixIn, self).davComplianceClasses())
            + config.CalDAVComplianceClasses
        )


class CalDAVResource (CalDAVComplianceMixIn, SharedCollectionMixin, DAVResource, LoggingMixIn):
    """
    CalDAV resource.

    Extends L{DAVResource} to provide CalDAV functionality.
    """
    implements(ICalDAVResource)

    ##
    # HTTP
    ##

    def render(self, request):
        if config.EnableMonolithicCalendars:
            #
            # Send listing instead of iCalendar data to HTML agents
            # This is mostly useful for debugging...
            #
            # FIXME: Add a self-link to the dirlist with a query string so
            #     users can still download the actual iCalendar data?
            #
            # FIXME: Are there better ways to detect this than hacking in
            #     user agents?
            #
            # FIXME: In the meantime, make this a configurable regex list?
            #
            agent = request.headers.getHeader("user-agent")
            if agent is not None and (
                agent.startswith("Mozilla/") and agent.find("Gecko") != -1
            ):
                renderAsHTML = True
            else:
                renderAsHTML = False
        else:
            renderAsHTML = True

        if not renderAsHTML and self.isPseudoCalendarCollection():
            # Render a monolithic iCalendar file
            if request.path[-1] != "/":
                # Redirect to include trailing '/' in URI
                return RedirectResponse(request.unparseURL(path=urllib.quote(urllib.unquote(request.path), safe=':/')+'/'))

            def _defer(data):
                response = Response()
                response.stream = MemoryStream(str(data))
                response.headers.setHeader("content-type", MimeType.fromString("text/calendar"))
                return response

            d = self.iCalendarRolledup(request)
            d.addCallback(_defer)
            return d

        return super(CalDAVResource, self).render(request)


    _associatedTransaction = None
    _transactionError = False

    def associateWithTransaction(self, transaction):
        """
        Associate this resource with a L{txcaldav.idav.ITransaction}; when this
        resource (or any of its children) are rendered successfully, commit the
        transaction.  Otherwise, abort the transaction.

        @param transaction: the transaction to associate this resource and its
            children with.

        @type transaction: L{txcaldav.idav.ITransaction} 
        """
        # FIXME: needs to reject association with transaction if it's already
        # got one (resources associated with a transaction are not reusable)
        self._associatedTransaction = transaction


    def propagateTransaction(self, otherResource):
        """
        Propagate the transaction associated with this resource to another
        resource (which should ostensibly be a child resource).

        @param otherResource: Another L{CalDAVResource}, usually one being
            constructed as a child of this one.

        @type otherResource: L{CalDAVResource} (or a subclass thereof)
        """
        if not self._associatedTransaction:
            raise RuntimeError("No associated transaction to propagate")
        otherResource.associateWithTransaction(self._associatedTransaction)


    def transactionError(self):
        self._transactionError = True


    def renderHTTP(self, request):
        """
        Override C{renderHTTP} to commit the transaction when the resource is
        successfully rendered.

        @param request: the request to generate a response for.
        @type request: L{twext.web2.iweb.IRequest}
        """
        d = maybeDeferred(super(CalDAVResource, self).renderHTTP, request)
        def succeeded(result):
            if self._associatedTransaction is not None:
                if self._transactionError:
                    self._associatedTransaction.abort()
                else:
                    self._associatedTransaction.commit()
            return result
        return d.addCallback(succeeded)


    # Begin transitional new-store resource interface:

    def copyDeadPropertiesTo(self, other):
        """
        Copy this resource's dead properties to another resource.  This requires
        that the new resource have a back-end store.

        @param other: a resource to copy all properites to.
        @type other: subclass of L{CalDAVResource}
        """
        self.newStoreProperties().update(other.newStoreProperties())


    def newStoreProperties(self):
        """
        Return an L{IMapping} that represents properties.  Only available on
        new-storage objects.
        """
        raise NotImplementedError("%s does not implement newStoreProperties" %
                                  (self,))
        
    
    def storeRemove(self, *a, **kw):
        """
        Remove this resource from storage.
        """
        raise NotImplementedError("%s does not implement storeRemove" %
                                  (self,))


    def storeStream(self, stream):
        """
        Store the content of the stream in this resource, as it would via a PUT.

        @param stream: The stream containing the data to be stored.
        @type stream: L{IStream}
        
        @return: a L{Deferred} which fires with an HTTP response.
        @rtype: L{Deferred}
        """
        raise NotImplementedError("%s does not implement storeStream"  %
                                  (self,))

    # End transitional new-store interface 

    ##
    # WebDAV
    ##

    def liveProperties(self):
        baseProperties = (
            davxml.Owner.qname(),               # Private Events needs this but it is also OK to return empty
        )
        
        if self.isPseudoCalendarCollection():
            baseProperties += (
                caldavxml.SupportedCalendarComponentSet.qname(),
                caldavxml.SupportedCalendarData.qname(),
            )

        if self.isCalendarCollection():
            baseProperties += (
                davxml.ResourceID.qname(),
                customxml.PubSubXMPPPushKeyProperty.qname(),
            )

        if self.isAddressBookCollection():
            baseProperties += (
                davxml.ResourceID.qname(),
                carddavxml.SupportedAddressData.qname(),
                customxml.PubSubXMPPPushKeyProperty.qname(),
            )

        if config.EnableSyncReport and (self.isPseudoCalendarCollection() or self.isAddressBookCollection()):
            baseProperties += (davxml.SyncToken.qname(),)
            
        if config.EnableAddMember and (self.isCalendarCollection() or self.isAddressBookCollection()):
            baseProperties += (davxml.AddMember.qname(),)
            
        if config.Sharing.Enabled:
            if config.Sharing.Calendars.Enabled and self.isCalendarCollection():
                baseProperties += (
                    customxml.Invite.qname(),
                    customxml.AllowedSharingModes.qname(),
                    customxml.SharedURL.qname(),
                )

            elif config.Sharing.AddressBooks.Enabled and self.isAddressBookCollection():
                baseProperties += (
                    customxml.Invite.qname(),
                    customxml.AllowedSharingModes.qname(),
                )
                
        return super(CalDAVResource, self).liveProperties() + baseProperties

    supportedCalendarComponentSet = caldavxml.SupportedCalendarComponentSet(
        *[caldavxml.CalendarComponent(name=item) for item in allowedComponents]
    )

    def isShadowableProperty(self, qname):
        """
        Shadowable properties are ones on shared resources where a "default" exists until
        a user overrides with their own value.
        """
        return qname in (
            caldavxml.CalendarDescription.qname(),
            caldavxml.CalendarTimeZone.qname(),
            carddavxml.AddressBookDescription.qname(),
        )

    def isGlobalProperty(self, qname):
        """
        A global property is one that is the same for all users.
        """
        if qname in self.liveProperties():
            if qname in (
                davxml.DisplayName.qname(),
                customxml.Invite.qname(),
            ):
                return False
            else:
                return True
        elif qname in (
            customxml.GETCTag.qname(),
            caldavxml.MaxResourceSize.qname(),
            caldavxml.MaxAttendeesPerInstance.qname(),
        ):
            return True
        else:
            return False

    @inlineCallbacks
    def hasProperty(self, property, request):
        """
        Need to special case schedule-calendar-transp for backwards compatability.
        """
        
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        isvirt = (yield self.isVirtualShare(request))
        if isvirt:
            if self.isShadowableProperty(qname):
                ownerPrincipal = (yield self.resourceOwnerPrincipal(request))
                p = self.deadProperties().contains(qname, uid=ownerPrincipal.principalUID())
                if p:
                    returnValue(p)
                
            elif (not self.isGlobalProperty(qname)):
                ownerPrincipal = (yield self.resourceOwnerPrincipal(request))
                p = self.deadProperties().contains(qname, uid=ownerPrincipal.principalUID())
                returnValue(p)

        res = (yield self._hasGlobalProperty(property, request))
        returnValue(res)

    def _hasGlobalProperty(self, property, request):
        """
        Need to special case schedule-calendar-transp for backwards compatability.
        """
        
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        # Force calendar collections to always appear to have the property
        if qname == caldavxml.ScheduleCalendarTransp.qname() and self.isCalendarCollection():
            return succeed(True)
        else:
            return super(CalDAVResource, self).hasProperty(property, request)

    @inlineCallbacks
    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        isvirt = (yield self.isVirtualShare(request))

        if self.isCalendarCollection() or self.isAddressBookCollection():
            # Push notification DAV property "pushkey"
            if qname == customxml.PubSubXMPPPushKeyProperty.qname():
                pubSubConfiguration = getPubSubConfiguration(config)
                if (pubSubConfiguration['enabled'] and
                    getattr(self, "clientNotifier", None) is not None):
                    label = "collection" if isvirt else "default"
                    id = self.clientNotifier.getID(label=label)
                    nodeName = getPubSubPath(id, pubSubConfiguration)
                    propVal = customxml.PubSubXMPPPushKeyProperty(nodeName)
                    nodeCacher = getNodeCacher()
                    try:
                        (yield nodeCacher.waitForNode(self.clientNotifier, nodeName))
                    except NodeCreationException:
                        pass
                    returnValue(propVal)

                else:
                    returnValue(customxml.PubSubXMPPPushKeyProperty())


        if isvirt:
            if self.isShadowableProperty(qname):
                ownerPrincipal = (yield self.resourceOwnerPrincipal(request))
                try:
                    p = self.deadProperties().get(qname, uid=ownerPrincipal.principalUID())
                    returnValue(p)
                except PropertyNotFoundError:
                    pass
                
            elif (not self.isGlobalProperty(qname)):
                ownerPrincipal = (yield self.resourceOwnerPrincipal(request))
                p = self.deadProperties().get(qname, uid=ownerPrincipal.principalUID())
                returnValue(p)

        res = (yield self._readGlobalProperty(qname, property, request))
        returnValue(res)

    @inlineCallbacks
    def _readGlobalProperty(self, qname, property, request):

        if qname == davxml.Owner.qname():
            owner = (yield self.owner(request))
            returnValue(davxml.Owner(owner))

        elif qname == davxml.ResourceID.qname():
            returnValue(davxml.ResourceID(davxml.HRef.fromString(self.resourceID())))

        elif qname == davxml.SyncToken.qname() and config.EnableSyncReport and (
            self.isPseudoCalendarCollection() or self.isAddressBookCollection()
        ):
            returnValue(davxml.SyncToken.fromString(self.getSyncToken()))

        elif qname == davxml.AddMember.qname() and config.EnableAddMember and (
            self.isCalendarCollection() or self.isAddressBookCollection()
        ):
            url = (yield self.canonicalURL(request))
            returnValue(davxml.AddMember(davxml.HRef.fromString(url + "/;add-member")))

        elif qname == caldavxml.SupportedCalendarComponentSet.qname():
            # CalDAV-access-09, section 5.2.3
            if self.hasDeadProperty(qname):
                returnValue(self.readDeadProperty(qname))
            returnValue(self.supportedCalendarComponentSet)

        elif qname == caldavxml.SupportedCalendarData.qname():
            # CalDAV-access-09, section 5.2.4
            returnValue(caldavxml.SupportedCalendarData(
                caldavxml.CalendarData(**{
                    "content-type": "text/calendar",
                    "version"     : "2.0",
                }),
            ))

        elif qname == caldavxml.MaxResourceSize.qname():
            # CalDAV-access-15, section 5.2.5
            if config.MaximumAttachmentSize:
                returnValue(caldavxml.MaxResourceSize.fromString(
                    str(config.MaximumAttachmentSize)
                ))

        elif qname == caldavxml.MaxAttendeesPerInstance.qname():
            # CalDAV-access-15, section 5.2.9
            if config.MaxAttendeesPerInstance:
                returnValue(caldavxml.MaxAttendeesPerInstance.fromString(
                    str(config.MaxAttendeesPerInstance)
                ))

        elif qname == caldavxml.ScheduleCalendarTransp.qname():
            # For backwards compatibility, if the property does not exist we need to create
            # it and default to the old free-busy-set value.
            if self.isCalendarCollection() and not self.hasDeadProperty(property):
                # For backwards compatibility we need to sync this up with the calendar-free-busy-set on the inbox
                principal = (yield self.resourceOwnerPrincipal(request))
                fbset = (yield principal.calendarFreeBusyURIs(request))
                url = (yield self.canonicalURL(request))
                opaque = url in fbset
                self.writeDeadProperty(caldavxml.ScheduleCalendarTransp(caldavxml.Opaque() if opaque else caldavxml.Transparent()))

        elif qname == carddavxml.SupportedAddressData.qname():
            # CardDAV, section 6.2.2
            returnValue(carddavxml.SupportedAddressData(
                carddavxml.AddressDataType(**{
                    "content-type": "text/vcard",
                    "version"     : "3.0",
                }),
            ))

        elif qname == customxml.Invite.qname():
            if config.Sharing.Enabled and (
                config.Sharing.Calendars.Enabled and self.isCalendarCollection() or 
                config.Sharing.AddressBooks.Enabled and self.isAddressBookCollection()
            ):
                result = (yield self.inviteProperty(request))
                returnValue(result)

        elif qname == customxml.AllowedSharingModes.qname():
            if config.Sharing.Enabled and config.Sharing.Calendars.Enabled and self.isCalendarCollection():
                returnValue(customxml.AllowedSharingModes(customxml.CanBeShared()))
            elif config.Sharing.Enabled and config.Sharing.AddressBooks.Enabled and self.isAddressBookCollection():
                returnValue(customxml.AllowedSharingModes(customxml.CanBeShared()))

        elif qname == customxml.SharedURL.qname():
            isvirt = (yield self.isVirtualShare(request))
            
            if isvirt:
                returnValue(customxml.SharedURL(davxml.HRef.fromString(self._share.hosturl)))
            else:
                returnValue(None)

        result = (yield super(CalDAVResource, self).readProperty(property, request))
        returnValue(result)

    @inlineCallbacks
    def writeProperty(self, property, request):
        assert isinstance(property, davxml.WebDAVElement), (
            "%r is not a WebDAVElement instance" % (property,)
        )
        
        # Per-user Dav props currently only apply to a sharee's copy of a calendar
        isvirt = (yield self.isVirtualShare(request))
        if isvirt and (self.isShadowableProperty(property.qname()) or (not self.isGlobalProperty(property.qname()))):
            yield self._preProcessWriteProperty(property, request)
            ownerPrincipal = (yield self.resourceOwnerPrincipal(request))
            p = self.deadProperties().set(property, uid=ownerPrincipal.principalUID())
            returnValue(p)
 
        res = (yield self._writeGlobalProperty(property, request))
        returnValue(res)

    @inlineCallbacks
    def _preProcessWriteProperty(self, property, request):
        if property.qname() == caldavxml.SupportedCalendarComponentSet.qname():
            if not self.isPseudoCalendarCollection():
                raise HTTPError(StatusResponse(
                    responsecode.FORBIDDEN,
                    "Property %s may only be set on calendar collection." % (property,)
                ))
            for component in property.children:
                if component not in self.supportedCalendarComponentSet:
                    raise HTTPError(StatusResponse(
                        responsecode.NOT_IMPLEMENTED,
                        "Component %s is not supported by this server" % (component.toxml(),)
                    ))

        # Strictly speaking CalDAV:timezone is a live property in the sense that the
        # server enforces what can be stored, however it need not actually
        # exist so we cannot list it in liveProperties on this resource, since its
        # its presence there means that hasProperty will always return True for it.
        elif property.qname() == caldavxml.CalendarTimeZone.qname():
            if not self.isCalendarCollection():
                raise HTTPError(StatusResponse(
                    responsecode.FORBIDDEN,
                    "Property %s may only be set on calendar collection." % (property,)
                ))
            if not property.valid():
                raise HTTPError(ErrorResponse(
                    responsecode.CONFLICT,
                    (caldav_namespace, "valid-calendar-data"),
                    description="Invalid property"
                ))

        elif property.qname() == caldavxml.ScheduleCalendarTransp.qname():
            if not self.isCalendarCollection():
                raise HTTPError(StatusResponse(
                    responsecode.FORBIDDEN,
                    "Property %s may only be set on calendar collection." % (property,)
                ))

            # For backwards compatibility we need to sync this up with the calendar-free-busy-set on the inbox
            principal = (yield self.resourceOwnerPrincipal(request))
            
            # Map owner to their inbox
            inboxURL = principal.scheduleInboxURL()
            if inboxURL:
                inbox = (yield request.locateResource(inboxURL))
                myurl = (yield self.canonicalURL(request))
                inbox.processFreeBusyCalendar(myurl, property.children[0] == caldavxml.Opaque())

    @inlineCallbacks
    def _writeGlobalProperty(self, property, request):

        yield self._preProcessWriteProperty(property, request)

        if property.qname() == davxml.ResourceType.qname():
            if self.isCalendarCollection() or self.isAddressBookCollection():
                sawShare = [child for child in property.children if child.qname() == (calendarserver_namespace, "shared-owner")]
                if sawShare:
                    if self.isCalendarCollection() and not (config.Sharing.Enabled and config.Sharing.Calendars.Enabled):
                        raise HTTPError(StatusResponse(
                            responsecode.FORBIDDEN,
                            "Cannot create shared calendars on this server.",
                        ))
                    elif self.isAddressBookCollection() and not (config.Sharing.Enabled and config.Sharing.AddressBooks.Enabled):
                        raise HTTPError(StatusResponse(
                            responsecode.FORBIDDEN,
                            "Cannot create shared address books on this server.",
                        ))

                # Check if adding or removing share
                shared = (yield self.isShared(request))
                for child in property.children:
                    if child.qname() == davxml.Collection.qname():
                        break
                else:
                    raise HTTPError(StatusResponse(
                        responsecode.FORBIDDEN,
                        "Protected property %s may not be set." % (property.sname(),)
                    ))
                for child in property.children:
                    if self.isCalendarCollection and child.qname() == caldavxml.Calendar.qname() or \
                       self.isAddressBookCollection and child.qname() == carddavxml.AddressBook.qname():
                        break
                else:
                    raise HTTPError(StatusResponse(
                        responsecode.FORBIDDEN,
                        "Protected property %s may not be set." % (property.sname(),)
                    ))
                sawShare = [child for child in property.children if child.qname() == (calendarserver_namespace, "shared-owner")]
                if not shared and sawShare:
                    # Owner is trying to share a collection
                    yield self.upgradeToShare(request)
                elif shared and not sawShare:
                    # Remove share
                    yield self.downgradeFromShare(request)
                returnValue(None)
            else:
                # resourcetype cannot be changed but we will allow it to be set to the same value
                currentType = (yield self.resourceType(request))
                if currentType == property:
                    returnValue(None)

        result = (yield super(CalDAVResource, self).writeProperty(property, request))
        returnValue(result)

    ##
    # ACL
    ##

    # FIXME: Perhaps this is better done in authorize() instead.
    @inlineCallbacks
    def accessControlList(self, request, *args, **kwargs):

        acls = None
        isvirt = (yield self.isVirtualShare(request))
        if isvirt:
            acls = self.shareeAccessControlList()

        if acls is None:
            acls = (yield super(CalDAVResource, self).accessControlList(request, *args, **kwargs))

        # Look for private events access classification
        if self.hasDeadProperty(TwistedCalendarAccessProperty):
            access = self.readDeadProperty(TwistedCalendarAccessProperty)
            if access.getValue() in (Component.ACCESS_PRIVATE, Component.ACCESS_CONFIDENTIAL, Component.ACCESS_RESTRICTED,):
                # Need to insert ACE to prevent non-owner principals from seeing this resource
                owner = (yield self.owner(request))
                newacls = []
                if access.getValue() == Component.ACCESS_PRIVATE:
                    newacls.extend(config.AdminACEs)
                    newacls.extend(config.ReadACEs)
                    newacls.append(davxml.ACE(
                        davxml.Invert(
                            davxml.Principal(owner),
                        ),
                        davxml.Deny(
                            davxml.Privilege(
                                davxml.Read(),
                            ),
                            davxml.Privilege(
                                davxml.Write(),
                            ),
                        ),
                        davxml.Protected(),
                    ))
                else:
                    newacls.extend(config.AdminACEs)
                    newacls.extend(config.ReadACEs)
                    newacls.append(davxml.ACE(
                        davxml.Invert(
                            davxml.Principal(owner),
                        ),
                        davxml.Deny(
                            davxml.Privilege(
                                davxml.Write(),
                            ),
                        ),
                        davxml.Protected(),
                    ))
                newacls.extend(acls.children)

                acls = davxml.ACL(*newacls)
 
        returnValue(acls)

    @inlineCallbacks
    def owner(self, request):
        """
        Return the DAV:owner property value (MUST be a DAV:href or None).
        """
        
        isVirt = (yield self.isVirtualShare(request))
        if isVirt:
            parent = (yield self.locateParent(request, self._share.hosturl))
        else:
            parent = (yield self.locateParent(request, request.urlForResource(self)))
        if parent and isinstance(parent, CalDAVResource):
            result = (yield parent.owner(request))
            returnValue(result)
        else:
            returnValue(None)

    @inlineCallbacks
    def ownerPrincipal(self, request):
        """
        Return the DAV:owner property value (MUST be a DAV:href or None).
        """
        isVirt = (yield self.isVirtualShare(request))
        if isVirt:
            parent = (yield self.locateParent(request, self._share.hosturl))
        else:
            parent = (yield self.locateParent(request, request.urlForResource(self)))
        if parent and isinstance(parent, CalDAVResource):
            result = (yield parent.ownerPrincipal(request))
            returnValue(result)
        else:
            returnValue(None)

    @inlineCallbacks
    def resourceOwnerPrincipal(self, request):
        """
        This is the owner of the resource based on the URI used to access it. For a shared
        collection it will be the sharee, otherwise it will be the regular the ownerPrincipal.
        """

        isVirt = (yield self.isVirtualShare(request))
        if isVirt:
            returnValue(self._shareePrincipal)
        else:
            parent = (yield self.locateParent(request, request.urlForResource(self)))
        if parent and isinstance(parent, CalDAVResource):
            result = (yield parent.resourceOwnerPrincipal(request))
            returnValue(result)
        else:
            returnValue(None)

    def isOwner(self, request, adminprincipals=False, readprincipals=False):
        """
        Determine whether the DAV:owner of this resource matches the currently authorized principal
        in the request. Optionally test for admin or read principals and allow those.
        """

        def _gotOwner(owner):
            current = self.currentPrincipal(request)
            if davxml.Principal(owner) == current:
                return True
            
            if adminprincipals:
                for principal in config.AdminPrincipals:
                    if davxml.Principal(davxml.HRef(principal)) == current:
                        return True

            if readprincipals:
                for principal in config.AdminPrincipals:
                    if davxml.Principal(davxml.HRef(principal)) == current:
                        return True
                
            return False

        d = self.owner(request)
        d.addCallback(_gotOwner)
        return d

    ##
    # DAVResource
    ##

    def displayName(self):
        if self.isAddressBookCollection() and not self.hasDeadProperty((davxml.dav_namespace, "displayname")):
            return None
        
        if 'record' in dir(self):
            if self.record.fullName:
                return self.record.fullName
            elif self.record.shortNames:
                return self.record.shortNames[0]
            else:
                return super(DAVResource, self).displayName()
        else:
            return super(DAVResource, self).displayName()

    def resourceID(self):
        if not self.hasDeadProperty(davxml.ResourceID.qname()):
            uuidval = uuid.uuid4()
            self.writeDeadProperty(davxml.ResourceID(davxml.HRef.fromString(uuidval.urn)))
        return str(self.readDeadProperty(davxml.ResourceID.qname()).children[0])

    ##
    # CalDAV
    ##

    def isCalendarCollection(self):
        """
        See L{ICalDAVResource.isCalendarCollection}.
        """
        return self.isSpecialCollection(caldavxml.Calendar)

    def isAddressBookCollection(self):
        """
        See L{ICalDAVResource.isAddressBookCollection}.
        """
        return self.isSpecialCollection(carddavxml.AddressBook)

    def isDirectoryBackedAddressBookCollection(self):       # ATM - temporary fix? (this one worked)
        return False

    def isSpecialCollection(self, collectiontype):
        """
        See L{ICalDAVResource.isSpecialCollection}.
        """
        if not self.isCollection(): return False

        try:
            resourcetype = self.readDeadProperty((dav_namespace, "resourcetype"))
        except HTTPError, e:
            assert e.response.code == responsecode.NOT_FOUND, (
                "Unexpected response code: %s" % (e.response.code,)
            )
            return False
        return bool(resourcetype.childrenOfType(collectiontype))

    def isPseudoCalendarCollection(self):
        """
        See L{ICalDAVResource.isPseudoCalendarCollection}.
        """
        return self.isCalendarCollection()

    def findCalendarCollections(self, depth, request, callback, privileges=None):
        return self.findSpecialCollections(caldavxml.Calendar, depth, request, callback, privileges)

    def findAddressBookCollections(self, depth, request, callback, privileges=None):
        return self.findSpecialCollections(carddavxml.AddressBook, depth, request, callback, privileges)

    def findSpecialCollections(self, type, depth, request, callback, privileges=None):
        assert depth in ("0", "1", "infinity"), "Invalid depth: %s" % (depth,)

        def checkPrivilegesError(failure):
            failure.trap(AccessDeniedError)

            reactor.callLater(0, getChild)

        def checkPrivileges(child):
            if privileges is None:
                return child

            ca = child.checkPrivileges(request, privileges)
            ca.addCallback(lambda ign: child)
            return ca

        def gotChild(child, childpath):
            if child.isSpecialCollection(type):
                callback(child, childpath)
                
            # No more regular collections
            #elif child.isCollection():
            #    if depth == "infinity":
            #        fc = child.findSpecialCollections(type, depth, request, callback, privileges)
            #        fc.addCallback(lambda x: reactor.callLater(0, getChild))
            #        return fc

            reactor.callLater(0, getChild)

        def getChild():
            try:
                childname = children.pop()
            except IndexError:
                completionDeferred.callback(None)
            else:
                childpath = joinURL(basepath, childname)
                child = request.locateResource(childpath)
                child.addCallback(checkPrivileges)
                child.addCallbacks(gotChild, checkPrivilegesError, (childpath,))
                child.addErrback(completionDeferred.errback)

        completionDeferred = Deferred()

        if depth != "0" and self.isCollection():
            basepath = request.urlForResource(self)
            children = self.listChildren()
            getChild()
        else:
            completionDeferred.callback(None)

        return completionDeferred

    def createCalendar(self, request):
        """
        See L{ICalDAVResource.createCalendar}.
        This implementation raises L{NotImplementedError}; a subclass must
        override it.
        """
        unimplemented(self)

    @inlineCallbacks
    def deletedCalendar(self, request):
        """
        Calendar has been deleted. Need to do some extra clean-up.

        @param request:
        @type request:
        """
        
        # For backwards compatibility we need to sync this up with the calendar-free-busy-set on the inbox
        principal = (yield self.resourceOwnerPrincipal(request))
        inboxURL = principal.scheduleInboxURL()
        if inboxURL:
            inbox = (yield request.locateResource(inboxURL))
            inbox.processFreeBusyCalendar(request.path, False)

    @inlineCallbacks
    def movedCalendar(self, request, defaultCalendar, destination, destination_uri):
        """
        Calendar has been moved. Need to do some extra clean-up.
        """
        
        # For backwards compatibility we need to sync this up with the calendar-free-busy-set on the inbox
        principal = (yield self.resourceOwnerPrincipal(request))
        inboxURL = principal.scheduleInboxURL()
        if inboxURL:
            (_ignore_scheme, _ignore_host, destination_path, _ignore_query, _ignore_fragment) = urlsplit(normalizeURL(destination_uri))

            inbox = (yield request.locateResource(inboxURL))
            inbox.processFreeBusyCalendar(request.path, False)
            inbox.processFreeBusyCalendar(destination_uri, destination.isCalendarOpaque())
            
            # Adjust the default calendar setting if necessary
            if defaultCalendar:
                yield inbox.writeProperty(caldavxml.ScheduleDefaultCalendarURL(davxml.HRef(destination_path)), request)               

    def isCalendarOpaque(self):
        
        assert self.isCalendarCollection()
        
        if self.hasDeadProperty((caldav_namespace, "schedule-calendar-transp")):
            property = self.readDeadProperty((caldav_namespace, "schedule-calendar-transp"))
            return property.children[0] == caldavxml.Opaque()
        else:
            return False

    @inlineCallbacks
    def isDefaultCalendar(self, request):
        
        assert self.isCalendarCollection()
        
        # Not allowed to delete the default calendar
        principal = (yield self.resourceOwnerPrincipal(request))
        inboxURL = principal.scheduleInboxURL()
        if inboxURL:
            inbox = (yield request.locateResource(inboxURL))
            default = (yield inbox.readProperty((caldav_namespace, "schedule-default-calendar-URL"), request))
            if default and len(default.children) == 1:
                defaultURL = normalizeURL(str(default.children[0]))
                myURL = (yield self.canonicalURL(request))
                returnValue(defaultURL == myURL)

        returnValue(False)

    def iCalendar(self, name=None):
        """
        See L{ICalDAVResource.iCalendar}.

        This implementation returns the an object created from the data returned
        by L{iCalendarText} when given the same arguments.

        Note that L{iCalendarText} by default calls this method, which creates
        an infinite loop.  A subclass must override one of both of these
        methods.
        """
        
        try:
            calendar_data = self.iCalendarText(name)
        except InternalDataStoreError:
            return None

        if calendar_data is None: return None

        try:
            return iComponent.fromString(calendar_data)
        except ValueError:
            return None

    @inlineCallbacks
    def iCalendarForUser(self, request, name=None):
        
        caldata = self.iCalendar(name)
        
        accessUID = (yield self.resourceOwnerPrincipal(request))
        if accessUID is None:
            accessUID = ""
        else:
            accessUID = accessUID.principalUID()

        returnValue(PerUserDataFilter(accessUID).filter(caldata))

    def iCalendarRolledup(self, request):
        """
        See L{ICalDAVResource.iCalendarRolledup}.

        This implementation raises L{NotImplementedError}; a subclass must
        override it.
        """
        unimplemented(self)

    def iCalendarText(self, name=None):
        """
        See L{ICalDAVResource.iCalendarText}.

        This implementation returns the string representation (according to
        L{str}) of the object returned by L{iCalendar} when given the same
        arguments.

        Note that L{iCalendar} by default calls this method, which creates
        an infinite loop.  A subclass must override one of both of these
        methods.
        """
        return str(self.iCalendar(name))

    def iCalendarAddressDoNormalization(self, ical):
        """
        Normalize calendar user addresses in the supplied iCalendar object into their
        urn:uuid form where possible. Also reset CN= property and add EMAIL property.

        @param ical: calendar object to normalize.
        @type ical: L{Component}
        """

        def lookupFunction(cuaddr):
            principal = self.principalForCalendarUserAddress(cuaddr)
            if principal is None:
                return (None, None, None)
            else:
                return (principal.record.fullName.decode("utf-8"),
                    principal.record.guid,
                    principal.record.calendarUserAddresses)

        ical.normalizeCalendarUserAddresses(lookupFunction)


    def principalForCalendarUserAddress(self, address):
        for principalCollection in self.principalCollections():
            principal = principalCollection.principalForCalendarUserAddress(address)
            if principal is not None:
                return principal
        return None

    def createAddressBook(self, request):
        """
        See L{ICalDAVResource.createAddressBook}.
        This implementation raises L{NotImplementedError}; a subclass must
        override it.
        """
        unimplemented(self)

    def vCard(self, name=None):
        """
        See L{ICalDAVResource.vCard}.

        This implementation returns the an object created from the data returned
        by L{vCardText} when given the same arguments.

        Note that L{vCardText} by default calls this method, which creates
        an infinite loop.  A subclass must override one of both of these
        methods.
        """
        try:
            vcard_data = self.vCardText(name)
        except InternalDataStoreError:
            return None

        if vcard_data is None: return None

        try:
            return vComponent.fromString(vcard_data)
        except ValueError:
            return None

    def vCardRolledup(self, request):
        """
        See L{ICalDAVResource.vCardRolledup}.

        This implementation raises L{NotImplementedError}; a subclass must
        override it.
        """
        unimplemented(self)

    def vCardText(self, name=None):
        """
        See L{ICalDAVResource.vCardText}.

        This implementation returns the string representation (according to
        L{str}) of the object returned by L{vCard} when given the same
        arguments.

        Note that L{vCard} by default calls this method, which creates
        an infinite loop.  A subclass must override one of both of these
        methods.
        """
        return str(self.vCard(name))

    def vCardXML(self, name=None):
        """
        See L{ICalDAVResource.vCardXML}.
        This implementation returns an XML element constructed from the object
        returned by L{vCard} when given the same arguments.
        """
        return carddavxml.AddressData.fromAddress(self.vCard(name))

    def supportedReports(self):
        result = super(CalDAVResource, self).supportedReports()
        result.append(davxml.Report(caldavxml.CalendarQuery(),))
        result.append(davxml.Report(caldavxml.CalendarMultiGet(),))
        if self.isCollection():
            # Only allowed on collections
            result.append(davxml.Report(caldavxml.FreeBusyQuery(),))
        if config.EnableCardDAV:
            result.append(davxml.Report(carddavxml.AddressBookQuery(),))
            result.append(davxml.Report(carddavxml.AddressBookMultiGet(),))
        if (self.isPseudoCalendarCollection() or self.isAddressBookCollection()) and config.EnableSyncReport:
            # Only allowed on calendar/inbox/addressbook collections
            result.append(davxml.Report(SyncCollection(),))
        return result

    def writeNewACEs(self, newaces):
        """
        Write a new ACL to the resource's property store. We override this for calendar collections
        and force all the ACEs to be inheritable so that all calendar object resources within the
        calendar collection have the same privileges unless explicitly overridden. The same applies
        to drop box collections as we want all resources (attachments) to have the same privileges as
        the drop box collection.

        @param newaces: C{list} of L{ACE} for ACL being set.
        """

        # Do this only for regular calendar collections and Inbox/Outbox
        if self.isPseudoCalendarCollection() or self.isAddressBookCollection():
            edited_aces = []
            for ace in newaces:
                if TwistedACLInheritable() not in ace.children:
                    children = list(ace.children)
                    children.append(TwistedACLInheritable())
                    edited_aces.append(davxml.ACE(*children))
                else:
                    edited_aces.append(ace)
        else:
            edited_aces = newaces

        # Do inherited with possibly modified set of aces
        super(CalDAVResource, self).writeNewACEs(edited_aces)

    ##
    # Utilities
    ##

    def locateParent(self, request, uri):
        """
        Locates the parent resource of the resource with the given URI.
        @param request: an L{IRequest} object for the request being processed.
        @param uri: the URI whose parent resource is desired.
        """
        return request.locateResource(parentForURL(uri))

    @inlineCallbacks
    def canonicalURL(self, request):
        
        if not hasattr(self, "_canonical_url"):
    
            myurl = request.urlForResource(self)
            _ignore_scheme, _ignore_host, path, _ignore_query, _ignore_fragment = urlsplit(normalizeURL(myurl))
            lastpath = path.split("/")[-1]
            
            parent = (yield request.locateResource(parentForURL(myurl)))
            if parent:
                canonical_parent = (yield parent.canonicalURL(request))
                self._canonical_url = joinURL(canonical_parent, lastpath)
            else:
                self._canonical_url = myurl

        returnValue(self._canonical_url)

    ##
    # Quota
    ##

    def hasQuotaRoot(self, request):
        """
        Quota root only ever set on calendar homes.
        """
        return False
    
    def quotaRoot(self, request):
        """
        Quota root only ever set on calendar homes.
        """
        return None 

    @inlineCallbacks
    def quotaRootResource(self, request):
        """
        Return the quota root for this resource.
        
        @return: L{DAVResource} or C{None}
        """

        sharedParent = None
        isvirt = (yield self.isVirtualShare(request))
        if isvirt:
            # A virtual share's quota root is the resource owner's root
            sharedParent = (yield request.locateResource(parentForURL(self._share.hosturl)))
        else:
            parent = (yield self.locateParent(request, request.urlForResource(self)))
            if isCalendarCollectionResource(parent) or isAddressBookCollectionResource(parent):
                isvirt = (yield parent.isVirtualShare(request))
                if isvirt:
                    # A virtual share's quota root is the resource owner's root
                    sharedParent = (yield request.locateResource(parentForURL(parent._share.hosturl)))

        if sharedParent:
            result = (yield sharedParent.quotaRootResource(request))
        else:
            result = (yield super(CalDAVResource, self).quotaRootResource(request))

        returnValue(result)

class CalendarPrincipalCollectionResource (DAVPrincipalCollectionResource, CalDAVResource):
    """
    CalDAV principal collection.
    """
    implements(IDAVPrincipalCollectionResource)

    def isCollection(self):
        return True

    def isCalendarCollection(self):
        return False

    def isPseudoCalendarCollection(self):
        return False

    def isAddressBookCollection(self):
        return False

    def isDirectoryBackedAddressBookCollection(self):
        return False

    def principalForCalendarUserAddress(self, address):
        return None

    def supportedReports(self):
        """
        Principal collections are the only resources supporting the
        principal-search-property-set report.
        """
        result = super(CalendarPrincipalCollectionResource, self).supportedReports()
        result.append(davxml.Report(davxml.PrincipalSearchPropertySet(),))
        return result

    def principalSearchPropertySet(self):
        return davxml.PrincipalSearchPropertySet(
            davxml.PrincipalSearchProperty(
                davxml.PropertyContainer(
                    davxml.DisplayName()
                ),
                davxml.Description(
                    davxml.PCDATAElement("Display Name"),
                    **{"xml:lang":"en"}
                ),
            ),
            davxml.PrincipalSearchProperty(
                davxml.PropertyContainer(
                    caldavxml.CalendarUserAddressSet()
                ),
                davxml.Description(
                    davxml.PCDATAElement("Calendar User Addresses"),
                    **{"xml:lang":"en"}
                ),
            ),
        )

class CalendarPrincipalResource (CalDAVComplianceMixIn, DAVPrincipalResource):
    """
    CalDAV principal resource.

    Extends L{DAVPrincipalResource} to provide CalDAV functionality.
    """
    implements(ICalendarPrincipalResource)

    def liveProperties(self):
        
        baseProperties = ()
        
        if config.EnableCalDAV:
            baseProperties += (
                (caldav_namespace, "calendar-home-set"        ),
                (caldav_namespace, "calendar-user-address-set"),
                (caldav_namespace, "schedule-inbox-URL"       ),
                (caldav_namespace, "schedule-outbox-URL"      ),
                (caldav_namespace, "calendar-user-type"       ),
                (calendarserver_namespace, "calendar-proxy-read-for"  ),
                (calendarserver_namespace, "calendar-proxy-write-for" ),
                (calendarserver_namespace, "auto-schedule" ),
            )
        
        if config.EnableCardDAV:
            baseProperties += (carddavxml.AddressBookHomeSet.qname(),)
            if config.DirectoryAddressBook.Enabled:
                baseProperties += (carddavxml.DirectoryGateway.qname(),)

        if config.EnableDropBox:
            baseProperties += (customxml.DropBoxHomeURL.qname(),)

        if config.Sharing.Enabled:
            baseProperties += (customxml.NotificationURL.qname(),)

        return super(CalendarPrincipalResource, self).liveProperties() + baseProperties

    def isCollection(self):
        return True

    @inlineCallbacks
    def readProperty(self, property, request):
        if type(property) is tuple:
            qname = property
        else:
            qname = property.qname()

        namespace, name = qname

        if namespace == caldav_namespace:
            if name == "calendar-home-set":
                returnValue(caldavxml.CalendarHomeSet(
                    *[davxml.HRef(url) for url in self.calendarHomeURLs()]
                ))

            elif name == "calendar-user-address-set":
                returnValue(caldavxml.CalendarUserAddressSet(
                    *[davxml.HRef(uri) for uri in self.calendarUserAddresses()]
                ))

            elif name == "schedule-inbox-URL":
                url = self.scheduleInboxURL()
                if url is None:
                    returnValue(None)
                else:
                    returnValue(caldavxml.ScheduleInboxURL(davxml.HRef(url)))

            elif name == "schedule-outbox-URL":
                url = self.scheduleOutboxURL()
                if url is None:
                    returnValue(None)
                else:
                    returnValue(caldavxml.ScheduleOutboxURL(davxml.HRef(url)))

            elif name == "calendar-user-type":
                returnValue(caldavxml.CalendarUserType(self.record.getCUType()))

        elif namespace == calendarserver_namespace:
            if name == "dropbox-home-URL" and config.EnableDropBox:
                url = self.dropboxURL()
                if url is None:
                    returnValue(None)
                else:
                    returnValue(customxml.DropBoxHomeURL(davxml.HRef(url)))

            elif name == "notification-URL" and config.Sharing.Enabled:
                url = yield self.notificationURL()
                if url is None:
                    returnValue(None)
                else:
                    returnValue(customxml.NotificationURL(davxml.HRef(url)))

            elif name == "calendar-proxy-read-for":
                results = (yield self.proxyFor(False))
                returnValue(customxml.CalendarProxyReadFor(
                    *[davxml.HRef(principal.principalURL()) for principal in results]
                ))

            elif name == "calendar-proxy-write-for":
                results = (yield self.proxyFor(True))
                returnValue(customxml.CalendarProxyWriteFor(
                    *[davxml.HRef(principal.principalURL()) for principal in results]
                ))

            elif name == "auto-schedule":
                autoSchedule = self.getAutoSchedule()
                returnValue(customxml.AutoSchedule("true" if autoSchedule else "false"))

        elif config.EnableCardDAV and namespace == carddav_namespace:
            if name == "addressbook-home-set":
                returnValue(carddavxml.AddressBookHomeSet(
                    *[davxml.HRef(url) for url in self.addressBookHomeURLs()]
                 ))
            elif name == "directory-gateway":
                returnValue(carddavxml.DirectoryGateway(
                    davxml.HRef.fromString(joinURL("/", config.DirectoryAddressBook.name, "/"))
                ))

        result = (yield super(CalendarPrincipalResource, self).readProperty(property, request))
        returnValue(result)

    def calendarHomeURLs(self):
        if self.hasDeadProperty((caldav_namespace, "calendar-home-set")):
            home_set = self.readDeadProperty((caldav_namespace, "calendar-home-set"))
            return [str(h) for h in home_set.children]
        else:
            return ()

    def calendarUserAddresses(self):
        if self.hasDeadProperty((caldav_namespace, "calendar-user-address-set")):
            addresses = self.readDeadProperty((caldav_namespace, "calendar-user-address-set"))
            return [str(h) for h in addresses.children]
        else:
            # Must have a valid address of some kind so use the principal uri
            return (self.principalURL(),)

    def calendarFreeBusyURIs(self, request):
        def gotInbox(inbox):
            if inbox is None:
                return ()

            def getFreeBusy(has):
                if not has:
                    return ()

                def parseFreeBusy(freeBusySet):
                    return tuple(str(href) for href in freeBusySet.children)

                d = inbox.readProperty((caldav_namespace, "calendar-free-busy-set"), request)
                d.addCallback(parseFreeBusy)
                return d

            d = inbox.hasProperty((caldav_namespace, "calendar-free-busy-set"), request)
            d.addCallback(getFreeBusy)
            return d

        d = self.scheduleInbox(request)
        d.addCallback(gotInbox)
        return d

    def scheduleInbox(self, request):
        """
        @return: the deferred schedule inbox for this principal.
        """
        return request.locateResource(self.scheduleInboxURL())

    def scheduleInboxURL(self):
        if self.hasDeadProperty((caldav_namespace, "schedule-inbox-URL")):
            inbox = self.readDeadProperty((caldav_namespace, "schedule-inbox-URL"))
            return str(inbox.children[0])
        else:
            return None

    def scheduleOutboxURL(self):
        """
        @return: the schedule outbox URL for this principal.
        """
        if self.hasDeadProperty((caldav_namespace, "schedule-outbox-URL")):
            outbox = self.readDeadProperty((caldav_namespace, "schedule-outbox-URL"))
            return str(outbox.children[0])
        else:
            return None

    def dropboxURL(self):
        """
        @return: the drop box home collection URL for this principal.
        """
        if self.hasDeadProperty((calendarserver_namespace, "dropbox-home-URL")):
            inbox = self.readDeadProperty((caldav_namespace, "dropbox-home-URL"))
            return str(inbox.children[0])
        else:
            return None

    def notificationURL(self, request=None):
        if self.hasDeadProperty((calendarserver_namespace, "notification-URL")):
            notification = self.readDeadProperty((calendarserver_namespace, "notification-URL"))
            return succeed(str(notification.children[0]))
        else:
            return succeed(None)

    def addressBookHomeURLs(self):
        if self.hasDeadProperty((carddav_namespace, "addressbook-home-set")):
            home_set = self.readDeadProperty((carddav_namespace, "addressbook-home-set"))
            return [str(h) for h in home_set.children]
        else:
            return ()

    ##
    # Quota
    ##

    def hasQuotaRoot(self, request):
        """
        Quota root only ever set on calendar homes.
        """
        return False
    
    def quotaRoot(self, request):
        """
        Quota root only ever set on calendar homes.
        """
        return None


class AuthenticationWrapper(SuperAuthenticationWrapper):

    """ AuthenticationWrapper implementation which allows overriding
        credentialFactories on a per-resource-path basis """

    def __init__(self, resource, portal, credentialFactories, loginInterfaces,
        overrides=None):

        super(AuthenticationWrapper, self).__init__(resource, portal,
            credentialFactories, loginInterfaces)

        self.overrides = {}
        if overrides:
            for path, factories in overrides.iteritems():
                self.overrides[path] = dict([(factory.scheme, factory)
                    for factory in factories])

    def hook(self, req):
        """ Uses the default credentialFactories unless the request is for
            one of the overridden paths """

        super(AuthenticationWrapper, self).hook(req)

        factories = self.overrides.get(req.path.rstrip("/"),
            self.credentialFactories)
        req.credentialFactories = factories


##
# Utilities
##

def isCalendarCollectionResource(resource):
    try:
        resource = ICalDAVResource(resource)
    except TypeError:
        return False
    else:
        return resource.isCalendarCollection()

def isPseudoCalendarCollectionResource(resource):
    try:
        resource = ICalDAVResource(resource)
    except TypeError:
        return False
    else:
        return resource.isPseudoCalendarCollection()

def isAddressBookCollectionResource(resource):
    try:
        resource = ICalDAVResource(resource)
    except TypeError:
        return False
    else:
        return resource.isAddressBookCollection()

