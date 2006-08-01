##
# Copyright (c) 2005-2006 Apple Computer, Inc. All rights reserved.
#
# This file contains Original Code and/or Modifications of Original Code
# as defined in and that are subject to the Apple Public Source License
# Version 2.0 (the 'License'). You may not use this file except in
# compliance with the License. Please obtain a copy of the License at
# http://www.opensource.apple.com/apsl/ and read it before using this
# file.
# 
# The Original Code and all software distributed under the License are
# distributed on an 'AS IS' basis, WITHOUT WARRANTY OF ANY KIND, EITHER
# EXPRESS OR IMPLIED, AND APPLE HEREBY DISCLAIMS ALL SUCH WARRANTIES,
# INCLUDING WITHOUT LIMITATION, ANY WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE, QUIET ENJOYMENT OR NON-INFRINGEMENT.
# Please see the License for the specific language governing rights and
# limitations under the License.
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

"""
CalDAV interfaces.
"""

__all__ = [ "IDAVResource" ]

from twisted.web2.dav.idav import IDAVResource

class ICalDAVResource(IDAVResource):
    """
    CalDAV resource.
    """
    def isCalendarCollection():
        """
        (CalDAV-access-10, Section 4.2)
        @return: True if this resource is a calendar collection, False
            otherwise.
        """

    def isPseudoCalendarCollection():
        """
        @return: True if this resource is a calendar collection like (e.g.
            a regular calendar collection or schedule inbox/outbox), False
            otherwise.
        """

    def findCalendarCollections(depth):
        """
        Returns an iterable of child calendar collection resources for the given
        depth.
        Because resources do not know their request URIs, chidren are returned
        as tuples C{(resource, uri)}, where C{resource} is the child resource
        and C{uri} is a URL path relative to this resource.
        @param depth: the search depth (one of "0", "1", or "infinity")
        @return: an iterable of tuples C{(resource, uri)}.
        """

    def findCalendarCollectionsWithPrivileges(depth, privileges, request):
        """
        Returns an iterable of child calendar collection resources for the given
        depth.
        Because resources do not know their request URIs, chidren are returned
        as tuples C{(resource, uri)}, where C{resource} is the child resource
        and C{uri} is a URL path relative to this resource.
        @param depth: the search depth (one of "0", "1", or "infinity")
        @param privileges: the privileges that must exist on any returned calendar collections.
        @param request: L{Request} for the current request.
        @return: an iterable of tuples C{(resource, uri)}.
        """

    def createCalendar(request):
        """
        Create a calendar collection for this resource.
        """

    def iCalendar(name=None):
        """
        Instantiate an iCalendar component object representing this resource or
        its child with the given name.
        The behavior of this method is not specified if it is called on a
        resource that is not a calendar collection or a calendar resource within
        a calendar collection.
        @param name: the name of the desired child of this resource, or None
            if this resource is desired.  Must be None if this resource is
            not a calendar collection.
        @return: a L{twistedcaldav.ical.Component} of type C{"VCALENDAR"}.
        """

    def iCalendarText(name=None):
        """
        Obtains the iCalendar text representing this resource or its child with
        the given name.
        The behavior of this method is not specified if it is called on a
        resource that is not a calendar collection or a calendar resource within
        a calendar collection.
        @param name: the name of the desired child of this resource, or None
            if this resource is desired.  Must be None if this resource is
            not a calendar collection.
        @return: a string containing iCalendar text with a top-level component
            of type C{"VCALENDAR"}.
        """

    def iCalendarXML(name=None):
        """
        Constructs a CalDAV XML element representing this resource or its child
        with the given name.
        The behavior of this method is not specified if it is called on a
        resource that is not a calendar collection or a calendar resource within
        a calendar collection.
        @param name: the name of the desired child of this resource, or None
            if this resource is desired.  Must be None if this resource is
            not a calendar collection.
        @return: a L{twistedcaldav.caldavxml.CalendarData} containing the
            iCalendar data for the requested resource.
        """

class ICalendarSchedulingCollectionResource(ICalDAVResource):
    """
    CalDAV scheduling collection resource type, e.g. schedule INBOX.
    """
    def isScheduleInbox():
        """
        True if this is a schedule Inbox.
        """

    def isScheduleOutbox():
        """
        True if this is an schedule Outbox.
        """

class ICalendarPrincipalResource(IDAVResource):
    """
    CalDAV principle resource.
    """
    def principalUID():
        """
        Get the user id for this principal.
        """

    def calendarHomeSet():
        """
        Get list of calendar collections for this principal's calendar user.
        return L{CalendarHomeSet} element.
        """

    def calendarUserAddressSet():
        """
        Get list of calendar user addresses for this principal's calendar user.
        return: L{CalendarUserAddressSet} element.
        """

    def calendarFreeBusySet(request):
        """
        Get list of calendars that contribute to free-busy for this principal's calendar user.
        This property actually resides on the schedule Inbox.
        param request: L{Request} for the current request.
        return: L{CalendarFreeBusySet} element.
        """

    def scheduleInboxURL():
        """
        Get the schedule INBOX URL for this principal's calendar user.
        return: a string containing the URL from the schedule-inbox-URL property.
        """

    def scheduleOutboxURL():
        """
        Get the schedule OUTBOX URL for this principal's calendar user.
        return: a string containing the URL from the schedule-outbox-URL property.
        """
