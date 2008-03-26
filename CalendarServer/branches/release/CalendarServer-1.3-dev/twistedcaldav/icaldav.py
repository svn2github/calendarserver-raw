##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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

    def isSpecialCollection(collectiontype):
        """
        (CalDAV-access-10, Section 4.2)
        @param collectiontype: L{WebDAVElement} for the collection type to test for.
        @return: True if this resource is a collection that also has the specified type,
            False otherwise.
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

class ICalendarPrincipalResource(IDAVResource):
    """
    CalDAV principle resource.
    """
    def principalUID():
        """
        @return: the user id for this principal.
        """

    def calendarHomeURLs():
        """
        @return: a list of calendar home URLs for this principal's calendar user.
        """

    def calendarUserAddresses():
        """
        @return: a list of calendar user addresses for this principal's calendar
            user.
        """

    def calendarFreeBusyURIs(self, request):
        """
        @param request: the request being processed.
        @return: a L{Deferred} list of URIs for calendars that contribute to
            free-busy for this principal's calendar user.
        """

    def scheduleInboxURL():
        """
        Get the schedule INBOX URL for this principal's calendar user.
        @return: a string containing the URL from the schedule-inbox-URL property.
        """

    def scheduleOutboxURL():
        """
        Get the schedule OUTBOX URL for this principal's calendar user.
        @return: a string containing the URL from the schedule-outbox-URL property.
        """
