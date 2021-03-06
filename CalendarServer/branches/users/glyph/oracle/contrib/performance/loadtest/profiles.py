##
# Copyright (c) 2011 Apple Inc. All rights reserved.
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
##

"""
Implementation of specific end-user behaviors.
"""

import random
from uuid import uuid4

from datetime import datetime, timedelta

from vobject import readComponents
from vobject.base import Component, ContentLine
from vobject.icalendar import VEvent

from protocol.caldav.definitions import caldavxml

from twisted.python.log import msg
from twisted.internet.defer import succeed, fail
from twisted.internet.task import LoopingCall


class ProfileBase(object):
    """
    Base class which provides some conveniences for profile
    implementations.
    """
    random = random

    def __init__(self, reactor, client, userNumber):
        self._reactor = reactor
        self._client = client
        self._number = userNumber


    def _calendarsOfType(self, calendarType):
        return [
            cal 
            for cal 
            in self._client._calendars.itervalues() 
            if cal.resourceType == calendarType]


class CannotAddAttendee(Exception):
    """
    Indicates no new attendees can be invited to a particular event.
    """



class Inviter(ProfileBase):
    """
    A Calendar user who invites and de-invites other users to events.
    """
    def run(self):
        self._call = LoopingCall(self._invite)
        self._call.clock = self._reactor
        # XXX Base this on something real
        self._call.start(20)


    def _addAttendee(self, event, attendees):
        """
        Create a new attendee to add to the list of attendees for the
        given event.
        """
        invitees = set([u'urn:uuid:user%02d' % (self._number,)])
        for att in attendees:
            invitees.add(att.value)

        for i in range(10):
            invitee = max(1, int(self.random.gauss(self._number, 3)))
            uuid = u'urn:uuid:user%02d' % (invitee,)
            if uuid not in invitees:
                break
        else:
            return fail(CannotAddAttendee("Can't find uninvited user to invite."))

        user = u'User %02d' % (invitee,)
        email = u'user%02d@example.com' % (invitee,)

        attendee = ContentLine(
            name=u'ATTENDEE', params=[
                [u'CN', user],
                [u'CUTYPE', u'INDIVIDUAL'],
                [u'EMAIL', email],
                [u'PARTSTAT', u'NEEDS-ACTION'],
                [u'ROLE', u'REQ-PARTICIPANT'],
                [u'RSVP', u'TRUE'],
                # [u'SCHEDULE-STATUS', u'1.2'],
                ],
            value=uuid,
            encoded=True)
        attendee.parentBehavior = VEvent

        return succeed(attendee)


    def _invite(self):
        """
        Try to add a new attendee to an event, or perhaps remove an
        existing attendee from an event.

        @return: C{None} if there are no events to play with,
            otherwise a L{Deferred} which fires when the attendee
            change has been made.
        """
        # Find calendars which are eligible for invites
        calendars = self._calendarsOfType(caldavxml.calendar)

        while calendars:
            # Pick one at random from which to try to select an event
            # to modify.
            calendar = self.random.choice(calendars)
            calendars.remove(calendar)

            if not calendar.events:
                continue

            events = calendar.events.keys()
            while events:
                uuid = self.random.choice(events)
                events.remove(uuid)
                event = calendar.events[uuid].vevent
                if event is None:
                    continue

                href = calendar.url + uuid

                # Find out who might attend
                attendees = event.contents['vevent'][0].contents.get('attendee', [])

                d = self._addAttendee(event, attendees)
                d.addCallbacks(
                    lambda attendee:
                        self._client.addEventAttendee(
                            href, attendee),
                    lambda reason: reason.trap(CannotAddAttendee))
                return d



class Accepter(ProfileBase):
    """
    A Calendar user who accepts invitations to events.
    """

    def __init__(self, reactor, client, userNumber):
        ProfileBase.__init__(self, reactor, client, userNumber)
        self._accepting = set()


    def run(self):
        self._subscription = self._client.catalog["eventChanged"].subscribe(self.eventChanged)


    def eventChanged(self, href):
        # Just respond to normal calendar events
        calendar = href.rsplit('/', 1)[0] + '/'
        try:
            calendar = self._client._calendars[calendar]
        except KeyError:
            return
        if calendar.resourceType != caldavxml.calendar:
            return
        if href in self._accepting:
            return

        vevent = self._client._events[href].vevent
        # Check to see if this user is in the attendee list in the
        # NEEDS-ACTION PARTSTAT.
        attendees = vevent.contents['vevent'][0].contents.get('attendee', [])
        for attendee in attendees:
            if attendee.params[u'EMAIL'][0] == self._client.email[len('mailto:'):]:
                if attendee.params[u'PARTSTAT'][0] == 'NEEDS-ACTION':
                    # XXX Base this on something real
                    delay = self.random.gauss(10, 2)
                    self._accepting.add(href)
                    self._reactor.callLater(
                        delay, self._acceptInvitation, href, attendee)
                    return


    def _acceptInvitation(self, href, attendee):
        self._accepting.remove(href)
        accepted = self._makeAcceptedAttendee(attendee)
        d = self._client.changeEventAttendee(href, attendee, accepted)
        def accepted(ignored):
            # Find the corresponding event in the inbox and delete it.
            uid = self._client._events[href].getUID()
            for cal in self._client._calendars.itervalues():
                if cal.resourceType == caldavxml.schedule_inbox:
                    for event in cal.events.itervalues():
                        if uid == event.getUID():
                            return self._client.deleteEvent(event.url)
        d.addCallback(accepted)
        return d


    def _makeAcceptedAttendee(self, attendee):
        accepted = ContentLine.duplicate(attendee)
        accepted.params[u'PARTSTAT'] = [u'ACCEPTED']
        try:
            del accepted.params[u'RSVP']
        except KeyError:
            msg("Duplicated an attendee with no RSVP: %r" % (attendee,))
        return accepted



class Eventer(ProfileBase):
    """
    A Calendar user who creates new events.
    """
    _eventTemplate = list(readComponents("""\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.3//EN
CALSCALE:GREGORIAN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
TZOFFSETFROM:-0500
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
DTSTART:20070311T020000
TZNAME:EDT
TZOFFSETTO:-0400
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0400
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
DTSTART:20071104T020000
TZNAME:EST
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
CREATED:20101018T155431Z
UID:C98AD237-55AD-4F7D-9009-0D355D835822
DTEND;TZID=America/New_York:20101021T130000
TRANSP:OPAQUE
SUMMARY:Simple event
DTSTART;TZID=America/New_York:20101021T120000
DTSTAMP:20101018T155438Z
SEQUENCE:2
END:VEVENT
END:VCALENDAR
"""))[0]

    def run(self):
        self._call = LoopingCall(self._addEvent)
        self._call.clock = self._reactor
        # XXX Base this on something real
        self._call.start(25)


    def _addEvent(self):
        calendars = self._calendarsOfType(caldavxml.calendar)

        while calendars:
            calendar = self.random.choice(calendars)
            calendars.remove(calendar)

            # Copy the template event and fill in some of its fields
            # to make a new event to create on the calendar.
            vcalendar = Component.duplicate(self._eventTemplate)
            vevent = vcalendar.contents[u'vevent'][0]
            tz = vevent.contents[u'created'][0].value.tzinfo
            dtstamp = datetime.now(tz)
            vevent.contents[u'created'][0].value = dtstamp
            vevent.contents[u'dtstamp'][0].value = dtstamp
            vevent.contents[u'dtstart'][0].value = dtstamp
            vevent.contents[u'dtend'][0].value = dtstamp + timedelta(hours=1)
            vevent.contents[u'uid'][0].value = unicode(uuid4())

            href = '%s%s.ics' % (
                calendar.url, vevent.contents[u'uid'][0].value)
            return self._client.addEvent(href, vcalendar)
