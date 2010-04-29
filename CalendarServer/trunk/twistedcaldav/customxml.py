##
# Copyright (c) 2006-2010 Apple Inc. All rights reserved.
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
Custom CalDAV XML Support.

This module provides custom XML utilities for use with CalDAV.

This API is considered private to static.py and is therefore subject to
change.
"""

from twext.web2.dav.davxml import dav_namespace
from twext.web2.dav.davxml import twisted_dav_namespace
from twext.web2.dav.element.base import twisted_private_namespace
from twext.web2.dav import davxml

from twistedcaldav import caldavxml, carddavxml
from twistedcaldav.ical import Component as iComponent

from vobject.icalendar import utc
from vobject.icalendar import dateTimeToString

import datetime

calendarserver_namespace = "http://calendarserver.org/ns/"

calendarserver_proxy_compliance = (
    "calendar-proxy",
)

calendarserver_private_events_compliance = (
    "calendarserver-private-events",
)

calendarserver_private_comments_compliance = (
    "calendarserver-private-comments",
)

calendarserver_principal_property_search_compliance = (
    "calendarserver-principal-property-search",
)

calendarserver_sharing_compliance = (
    "calendarserver-sharing",
)

class TwistedCalendarAccessProperty (davxml.WebDAVTextElement):
    """
    Contains the calendar access level (private events) for the resource.
    """
    namespace = twisted_dav_namespace
    name = "calendar-access"
    hidden = True

    def getValue(self):
        return str(self)

class TwistedSchedulingObjectResource (davxml.WebDAVTextElement):
    """
    Indicates that the resource is a scheduling object resource.    
    """
    namespace = twisted_private_namespace
    name = "scheduling-object-resource"
    hidden = True

class TwistedScheduleMatchETags(davxml.WebDAVElement):
    """
    List of ETags that can be used for a "weak" If-Match comparison.    
    """
    namespace = twisted_private_namespace
    name = "scheduling-match-etags"
    hidden = True

    allowed_children = { (dav_namespace, "getetag"): (0, None) }

class TwistedCalendarHasPrivateCommentsProperty (davxml.WebDAVEmptyElement):
    """
    Indicates that a calendar resource has private comments.
    
    NB This MUST be a private property as we don't want to expose the presence of private comments
    in private events.
    
    """
    namespace = twisted_private_namespace
    name = "calendar-has-private-comments"
    hidden = True

class CalendarProxyRead (davxml.WebDAVEmptyElement):
    """
    A read-only calendar user proxy principal resource.
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "calendar-proxy-read"

class CalendarProxyWrite (davxml.WebDAVEmptyElement):
    """
    A read-write calendar user proxy principal resource.
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "calendar-proxy-write"

class CalendarProxyReadFor (davxml.WebDAVElement):
    """
    List of principals granting read-only proxy status.
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "calendar-proxy-read-for"
    hidden = True
    protected = True

    allowed_children = { (dav_namespace, "href"): (0, None) }

class CalendarProxyWriteFor (davxml.WebDAVElement):
    """
    List of principals granting read-write proxy status.
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "calendar-proxy-write-for"
    hidden = True
    protected = True

    allowed_children = { (dav_namespace, "href"): (0, None) }

class DropBoxHome (davxml.WebDAVEmptyElement):
    """
    Denotes a drop box home collection (a collection that will contain drop boxes).
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "dropbox-home"

class DropBox (davxml.WebDAVEmptyElement):
    """
    Denotes a drop box collection.
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "dropbox"

class DropBoxHomeURL (davxml.WebDAVElement):
    """
    A principal property to indicate the location of the drop box home.
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "dropbox-home-URL"
    hidden = True
    protected = True

    allowed_children = { (davxml.dav_namespace, "href"): (0, 1) }

class GETCTag (davxml.WebDAVTextElement):
    """
    Contains the calendar collection entity tag.
    """
    namespace = calendarserver_namespace
    name = "getctag"
    protected = True

class CalendarAvailability (davxml.WebDAVTextElement):
    """
    Contains the calendar availability property.
    """
    namespace = calendarserver_namespace
    name = "calendar-availability"
    hidden = True


    def calendar(self):
        """
        Returns a calendar component derived from this element.
        """
        return iComponent.fromString(str(self))

    def valid(self):
        """
        Determine whether the content of this element is a valid single VAVAILABILITY component,
        with zero or more VTIEMZONE components.

        @return: True if valid, False if not.
        """
        
        try:
            calendar = self.calendar()
            if calendar is None:
                return False
        except ValueError:
            return False

        found = False
        for subcomponent in calendar.subcomponents():
            if subcomponent.name() == "VAVAILABILITY":
                if found:
                    return False
                else:
                    found = True
            elif subcomponent.name() == "VTIMEZONE":
                continue
            else:
                return False

        return found

class Timezones (davxml.WebDAVEmptyElement):
    """
    Denotes a timezone service resource.
    (Apple Extension to CalDAV)
    """
    namespace = calendarserver_namespace
    name = "timezones"

class TZIDs (davxml.WebDAVElement):
    """
    Wraps a list of timezone ids.
    """
    namespace = calendarserver_namespace
    name = "tzids"
    allowed_children = { (calendarserver_namespace, "tzid" ): (0, None) }

class TZID (davxml.WebDAVTextElement):
    """
    A timezone id.
    """
    namespace = calendarserver_namespace
    name = "tzid"

class TZData (davxml.WebDAVElement):
    """
    Wraps a list of timezone observances.
    """
    namespace = calendarserver_namespace
    name = "tzdata"
    allowed_children = { (calendarserver_namespace, "observance" ): (0, None) }

class Observance (davxml.WebDAVElement):
    """
    A timezone observance.
    """
    namespace = calendarserver_namespace
    name = "observance"
    allowed_children = {
        (calendarserver_namespace, "onset" )     : (1, 1),
        (calendarserver_namespace, "utc-offset" ): (1, 1),
    }

class Onset (davxml.WebDAVTextElement):
    """
    The onset date-time for a DST transition.
    """
    namespace = calendarserver_namespace
    name = "onset"

class UTCOffset (davxml.WebDAVTextElement):
    """
    A UTC offset value for a timezone observance.
    """
    namespace = calendarserver_namespace
    name = "utc-offset"

class PubSubXMPPURIProperty (davxml.WebDAVTextElement):
    """
    A calendarhomefile property to indicate the pubsub XMPP URI to subscribe to
    for notifications.
    """
    namespace = calendarserver_namespace
    name = "xmpp-uri"
    protected = True
    hidden = True

class PubSubHeartbeatProperty (davxml.WebDAVElement):
    """
    A calendarhomefile property to indicate the pubsub XMPP URI to subscribe to
    for server heartbeats.
    """
    namespace = calendarserver_namespace
    name = "xmpp-heartbeat"
    protected = True
    hidden = True
    allowed_children = {
        (calendarserver_namespace, "xmpp-heartbeat-uri" )  : (1, 1),
        (calendarserver_namespace, "xmpp-heartbeat-minutes" ) : (1, 1),
    }

class PubSubHeartbeatURIProperty (davxml.WebDAVTextElement):
    namespace = calendarserver_namespace
    name = "xmpp-heartbeat-uri"
    protected = True
    hidden = True

class PubSubHeartbeatMinutesProperty (davxml.WebDAVTextElement):
    namespace = calendarserver_namespace
    name = "xmpp-heartbeat-minutes"
    protected = True
    hidden = True

class PubSubXMPPServerProperty (davxml.WebDAVTextElement):
    """
    A calendarhomefile property to indicate the pubsub XMPP hostname to
    contact for notifications.
    """
    namespace = calendarserver_namespace
    name = "xmpp-server"
    protected = True
    hidden = True

davxml.PrincipalPropertySearch.allowed_children[(calendarserver_namespace, "limit")] = (0, 1)
davxml.PrincipalPropertySearch.allowed_attributes["type"] = False
davxml.Match.allowed_attributes = {
    "caseless": False,
    "match-type": False,
}

class Limit (davxml.WebDAVElement):
    """
    Client supplied limit for reports.
    """
    namespace = calendarserver_namespace
    name = "limit"
    allowed_children = {
        (calendarserver_namespace, "nresults" )  : (1, 1),
    }

class NResults (davxml.WebDAVTextElement):
    """
    Number of results limit.
    """
    namespace = calendarserver_namespace
    name = "nresults"

class FirstNameProperty (davxml.WebDAVTextElement):
    """
    A property representing first name of a principal
    """
    namespace = calendarserver_namespace
    name = "first-name"
    protected = True
    hidden = True

class LastNameProperty (davxml.WebDAVTextElement):
    """
    A property representing last name of a principal
    """
    namespace = calendarserver_namespace
    name = "last-name"
    protected = True
    hidden = True

class EmailAddressProperty (davxml.WebDAVTextElement):
    """
    A property representing email address of a principal
    """
    namespace = calendarserver_namespace
    name = "email-address"
    protected = True
    hidden = True

class EmailAddressSet (davxml.WebDAVElement):
    """
    The list of email addresses of a principal
    """
    namespace = calendarserver_namespace
    name = "email-address-set"
    hidden = True

    allowed_children = { (calendarserver_namespace, "email-address"): (0, None) }

class ExpandedGroupMemberSet (davxml.WebDAVElement):
    """
    The expanded list of members of a (group) principal
    """
    namespace = calendarserver_namespace
    name = "expanded-group-member-set"
    protected = True
    hidden = True

    allowed_children = { (dav_namespace, "href"): (0, None) }

class ExpandedGroupMembership (davxml.WebDAVElement):
    """
    The expanded list of groups a principal is a member of
    """
    namespace = calendarserver_namespace
    name = "expanded-group-membership"
    protected = True
    hidden = True

    allowed_children = { (dav_namespace, "href"): (0, None) }

class IScheduleInbox (davxml.WebDAVEmptyElement):
    """
    Denotes the resourcetype of a iSchedule Inbox.
    (CalDAV-s2s-xx, section x.x.x)
    """
    namespace = calendarserver_namespace
    name = "ischedule-inbox"

class FreeBusyURL (davxml.WebDAVEmptyElement):
    """
    Denotes the resourcetype of a free-busy URL resource.
    (CalDAV-s2s-xx, section x.x.x)
    """
    namespace = calendarserver_namespace
    name = "free-busy-url"

class ScheduleChanges (davxml.WebDAVElement):
    """
    Change indicator for a scheduling message.
    """
    namespace = calendarserver_namespace
    name = "schedule-changes"
    protected = True
    hidden = True
    allowed_children = {
        (calendarserver_namespace, "dtstamp" )     : (0, 1), # Have to allow 0 as element is empty in PROPFIND requests
        (calendarserver_namespace, "action" )      : (0, 1), # Have to allow 0 as element is empty in PROPFIND requests
    }

class DTStamp (davxml.WebDAVTextElement):
    """
    A UTC timestamp in iCal format.
    """
    namespace = calendarserver_namespace
    name = "dtstamp"

    def __init__(self, *children):
        super(DTStamp, self).__init__(children)
        self.children = (davxml.PCDATAElement(dateTimeToString(datetime.datetime.now(tz=utc))),)

class Action (davxml.WebDAVElement):
    """
    A UTC timestamp in iCal format.
    """
    namespace = calendarserver_namespace
    name = "action"
    allowed_children = {
        (calendarserver_namespace, "create" ) : (0, 1),
        (calendarserver_namespace, "update" ) : (0, 1),
        (calendarserver_namespace, "cancel" ) : (0, 1),
        (calendarserver_namespace, "reply" )  : (0, 1),
    }

class Create (davxml.WebDAVEmptyElement):
    """
    Event created.
    """
    namespace = calendarserver_namespace
    name = "create"

class Update (davxml.WebDAVElement):
    """
    Event updated.
    """
    namespace = calendarserver_namespace
    name = "update"
    allowed_children = {
        (calendarserver_namespace, "recurrence" ) : (1, None),
    }

class Cancel (davxml.WebDAVElement):
    """
    Event cancelled.
    """
    namespace = calendarserver_namespace
    name = "cancel"
    allowed_children = {
        (calendarserver_namespace, "recurrence" ) : (0, 1),
    }

class Reply (davxml.WebDAVElement):
    """
    Event replied to.
    """
    namespace = calendarserver_namespace
    name = "reply"
    allowed_children = {
        (calendarserver_namespace, "attendee" )        : (1, 1),
        (calendarserver_namespace, "recurrence" )      : (1, None),
    }

class Recurrence (davxml.WebDAVElement):
    """
    Changes to an event.
    """
    namespace = calendarserver_namespace
    name = "recurrence"
    allowed_children = {
        (calendarserver_namespace, "master" )       : (0, 1),
        (calendarserver_namespace, "recurrenceid" ) : (0, None),
        (calendarserver_namespace, "changes" )      : (0, 1),
    }

class Master (davxml.WebDAVEmptyElement):
    """
    Master instance changed.
    """
    namespace = calendarserver_namespace
    name = "master"

class RecurrenceID (davxml.WebDAVTextElement):
    """
    A recurrence instance changed.
    """
    namespace = calendarserver_namespace
    name = "recurrenceid"

class Changes (davxml.WebDAVElement):
    """
    Changes to an event.
    """
    namespace = calendarserver_namespace
    name = "changes"
    allowed_children = {
        (calendarserver_namespace, "changed-property" )  : (0, None),
    }

class ChangedProperty (davxml.WebDAVElement):
    """
    Changes to a property.
    """
    namespace = calendarserver_namespace
    name = "changed-property"

    allowed_children = {
        (calendarserver_namespace, "changed-parameter" )  : (0, None),
    }

    allowed_attributes = {
        "name" : True,
    }

class ChangedParameter (davxml.WebDAVEmptyElement):
    """
    Changes to a parameter.
    """
    namespace = calendarserver_namespace
    name = "changed-parameter"

    allowed_attributes = {
        "name" : True,
    }

class Attendee (davxml.WebDAVTextElement):
    """
    An attendee calendar user address.
    """
    namespace = calendarserver_namespace
    name = "attendee"

class RecordType (davxml.WebDAVTextElement):
    """
    Exposes the type of a record
    """
    namespace = calendarserver_namespace
    name = "record-type"
    protected = True
    hidden = True

class AutoSchedule (davxml.WebDAVTextElement):
    """
    Whether the principal automatically accepts invitations
    """
    namespace = calendarserver_namespace
    name = "auto-schedule"

class ReadAccess (davxml.WebDAVEmptyElement):
    """
    Denotes read and update attendee partstat on a shared calendar.
    """
    namespace = calendarserver_namespace
    name = "read"

class ReadWriteAccess (davxml.WebDAVEmptyElement):
    """
    Denotes read and write access on a shared calendar.
    """
    namespace = calendarserver_namespace
    name = "read-write"

class ReadWriteScheduleAccess (davxml.WebDAVEmptyElement):
    """
    Denotes read and write and schedule access on a shared calendar.
    """
    namespace = calendarserver_namespace
    name = "read-write-schedule"

class UID (davxml.WebDAVTextElement):
    namespace = calendarserver_namespace
    name = "uid"

class InReplyTo (davxml.WebDAVTextElement):
    namespace = calendarserver_namespace
    name = "in-reply-to"

##
# Notifications
##

class SharedOwner (davxml.WebDAVEmptyElement):
    """
    Denotes a shared collection.
    """
    namespace = calendarserver_namespace
    name = "shared-owner"

class Shared (davxml.WebDAVEmptyElement):
    """
    Denotes a shared collection.
    """
    namespace = calendarserver_namespace
    name = "shared"

class Subscribed (davxml.WebDAVEmptyElement):
    """
    Denotes a subscribed calendar collection.
    """
    namespace = calendarserver_namespace
    name = "subscribed"

class SharedURL (davxml.WebDAVTextElement):
    """
    The source url for a shared calendar.
    """
    namespace = calendarserver_namespace
    name = "shared-url"

class SharedCalendar (davxml.WebDAVElement):
    """
    The url for a shared calendar.
    """
    namespace = calendarserver_namespace
    name = "shared-calendar"

    allowed_children = {
        davxml.HRef.qname()    : (1, 1),
    }

class SharedAcceptEmailNotification (davxml.WebDAVTextElement):
    """
    The accept email flag for a shared calendar.
    """
    namespace = calendarserver_namespace
    name = "shared-accept-email-notification"

class Birthday (davxml.WebDAVEmptyElement):
    """
    Denotes a birthday calendar collection.
    """
    namespace = calendarserver_namespace
    name = "birthday"

class AllowedSharingModes (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "allowed-sharing-modes"
    protected = True
    hidden = True

    allowed_children = {
        (calendarserver_namespace, "can-be-shared" )    : (0, 1),
        (calendarserver_namespace, "can-be-published" ) : (0, 1),
    }

class CanBeShared (davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "can-be-shared"

class CanBePublished (davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "can-be-published"

class InviteShare (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "share"

    allowed_children = {
        (calendarserver_namespace, "set" )    : (0, None),
        (calendarserver_namespace, "remove" ) : (0, None),
    }

class InviteSet (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "set"

    allowed_children = {
        (dav_namespace,            "href" )                : (1, 1),
        (calendarserver_namespace, "summary" )             : (0, 1),
        (calendarserver_namespace, "read" )                : (0, 1),
        (calendarserver_namespace, "read-write" )          : (0, 1),
        (calendarserver_namespace, "read-write-schedule" ) : (0, 1),
    }

class InviteRemove (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "remove"

    allowed_children = {
        (dav_namespace,            "href" )                : (1, 1),
        (calendarserver_namespace, "read" )                : (0, 1),
        (calendarserver_namespace, "read-write" )          : (0, 1),
        (calendarserver_namespace, "read-write-schedule" ) : (0, 1),
    }

class InviteUser (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "user"

    allowed_children = {
        (calendarserver_namespace, "href" )              : (1, 1),
        (calendarserver_namespace, "invite-noresponse" ) : (0, 1),
        (calendarserver_namespace, "invite-deleted" )    : (0, 1),
        (calendarserver_namespace, "invite-accepted" )   : (0, 1),
        (calendarserver_namespace, "invite-declined" )   : (0, 1),
        (calendarserver_namespace, "invite-invalid" )    : (0, 1),
        (calendarserver_namespace, "access" )            : (1, 1),
        (calendarserver_namespace, "summary" )           : (0, 1),
    }

class InviteAccess (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "access"

    allowed_children = {
        (calendarserver_namespace, "read" )               : (0, 1),
        (calendarserver_namespace, "read-write" )         : (0, 1),
        (calendarserver_namespace, "read-write-schedule" ): (0, 1),
    }

class Invite (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "invite"

    allowed_children = {
        (calendarserver_namespace, "user" )   : (0, None),
    }

class InviteSummary (davxml.WebDAVTextElement):
    namespace = calendarserver_namespace
    name = "summary"

class InviteStatusNoResponse (davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "invite-noresponse"

class InviteStatusDeleted (davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "invite-deleted"

class InviteStatusAccepted (davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "invite-accepted"

class InviteStatusDeclined (davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "invite-declined"

class InviteStatusInvalid (davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "invite-invalid"

class HostURL (davxml.WebDAVElement):
    """
    The source for a shared calendar
    """
    namespace = calendarserver_namespace
    name = "hosturl"

    allowed_children = { (dav_namespace, "href"): (0, None) }

class Organizer (davxml.WebDAVElement):
    """
    The organizer for a shared calendar
    """
    namespace = calendarserver_namespace
    name = "organizer"

    allowed_children = { (dav_namespace, "href"): (0, None) }

class InviteNotification (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "invite-notification"

    allowed_children = {
        UID.qname()                      : (0, 1),
        (dav_namespace, "href")          : (0, 1),
        InviteStatusNoResponse.qname()   : (0, 1),
        InviteStatusDeleted.qname()      : (0, 1),
        InviteStatusAccepted.qname()     : (0, 1),
        InviteStatusDeclined.qname()     : (0, 1),
        InviteAccess.qname()             : (0, 1),
        HostURL.qname()                  : (0, 1),
        Organizer.qname()                : (0, 1),
        InviteSummary.qname()            : (0, 1),
    }

    allowed_attributes = {
        "shared-type" : True,
    }

class InviteReply (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "invite-reply"

    allowed_children = {
        (dav_namespace, "href")          : (0, 1),
        InviteStatusAccepted.qname()     : (0, 1),
        InviteStatusDeclined.qname()     : (0, 1),
        HostURL.qname()                  : (0, 1),
        InReplyTo.qname()                : (0, 1),
        InviteSummary.qname()            : (0, 1),
    }

class ResourceUpdateAdded(davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "resource-added-notification"

class ResourceUpdateUpdated(davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "resource-updated-notification"

class ResourceDeletedUpdated(davxml.WebDAVEmptyElement):
    namespace = calendarserver_namespace
    name = "resource-deleted-notification"

class ResourceUpdateNotification (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "resource-update-notification"

    allowed_children = {
        (dav_namespace, "href")          : (0, 1),
        UID.qname()                      : (0, 1),
        ResourceUpdateAdded.qname()      : (0, 1),
        ResourceUpdateUpdated.qname()    : (0, 1),
        ResourceDeletedUpdated.qname()   : (0, 1),
    }

class SharedCalendarUpdateNotification (davxml.WebDAVElement):
    namespace = calendarserver_namespace
    name = "shared-update-notification"

    allowed_children = {
        HostURL.qname()                  : (0, 1), # The shared calendar url
        (dav_namespace, "href")          : (0, 1), # Email userid that was invited
        InviteStatusDeleted.qname()      : (0, 1), # What the user did...
        InviteStatusAccepted.qname()     : (0, 1),
        InviteStatusDeclined.qname()     : (0, 1),
    }

class Notification (davxml.WebDAVElement):
    """
    Denotes a notification collection, or a notification message.
    """
    namespace = calendarserver_namespace
    name = "notification"

    allowed_children = {
        DTStamp.qname()                            : (0, None),
        InviteNotification.qname()                 : (0, None),
        InviteReply.qname()                        : (0, None),
        ResourceUpdateNotification.qname()         : (0, None),
        SharedCalendarUpdateNotification.qname()   : (0, None),
    }

class NotificationURL (davxml.WebDAVElement):
    """
    A principal property to indicate the notification collection for the principal.
    """
    namespace = calendarserver_namespace
    name = "notification-URL"
    hidden = True
    protected = True

    allowed_children = { (davxml.dav_namespace, "href"): (0, 1) }

class NotificationType (davxml.WebDAVElement):
    """
    A property to indicate what type of notification the resource represents.
    """
    namespace = calendarserver_namespace
    name = "notificationtype"
    hidden = True
    protected = True

    allowed_children = {
        InviteNotification.qname()                 : (0, None),
        InviteReply.qname()                        : (0, None),
    }

##
# Extensions to davxml.ResourceType
##

davxml.ResourceType.dropboxhome = davxml.ResourceType(davxml.Collection(), DropBoxHome())
davxml.ResourceType.dropbox = davxml.ResourceType(davxml.Collection(), DropBox())
davxml.ResourceType.calendarproxyread = davxml.ResourceType(davxml.Principal(), davxml.Collection(), CalendarProxyRead())
davxml.ResourceType.calendarproxywrite = davxml.ResourceType(davxml.Principal(), davxml.Collection(), CalendarProxyWrite())
davxml.ResourceType.timezones = davxml.ResourceType(Timezones())
davxml.ResourceType.ischeduleinbox = davxml.ResourceType(IScheduleInbox())
davxml.ResourceType.freebusyurl = davxml.ResourceType(FreeBusyURL())
davxml.ResourceType.notification = davxml.ResourceType(davxml.Collection(), Notification())
davxml.ResourceType.sharedownercalendar = davxml.ResourceType(davxml.Collection(), caldavxml.Calendar(), SharedOwner())
davxml.ResourceType.sharedcalendar = davxml.ResourceType(davxml.Collection(), caldavxml.Calendar(), Shared())
davxml.ResourceType.sharedaddressbook = davxml.ResourceType(davxml.Collection(), carddavxml.AddressBook(), Shared())
