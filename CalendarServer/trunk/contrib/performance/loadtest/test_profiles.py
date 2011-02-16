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
Tests for loadtest.profiles.
"""

from vobject import readComponents

from protocol.caldav.definitions import caldavxml

from twisted.trial.unittest import TestCase
from twisted.internet.task import Clock
from twisted.internet.defer import succeed

from loadtest.profiles import Eventer, Inviter, Accepter
from loadtest.ical import Calendar, Event, BaseClient

SIMPLE_EVENT = """\
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
"""

INVITED_EVENT = """\
BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:EST
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:EDT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:882C3D50-0DAE-45CB-A2E7-DA75DA9BE452
DTSTART;TZID=America/New_York:20110131T130000
DTEND;TZID=America/New_York:20110131T140000
ATTENDEE;CN=User 01;CUTYPE=INDIVIDUAL;EMAIL=user01@example.com;PARTSTAT=AC
 CEPTED:urn:uuid:user01
ATTENDEE;CN=User 02;CUTYPE=INDIVIDUAL;EMAIL=user02@example.com;PARTSTAT=NE
 EDS-ACTION;ROLE=REQ-PARTICIPANT;RSVP=TRUE:urn:uuid:user02
CREATED:20110124T170357Z
DTSTAMP:20110124T170425Z
ORGANIZER;CN=User 01;EMAIL=user01@example.com:urn:uuid:user01
SEQUENCE:3
SUMMARY:Some Event For You
TRANSP:TRANSPARENT
X-APPLE-NEEDS-REPLY:TRUE
END:VEVENT
END:VCALENDAR
"""

ACCEPTED_EVENT = """\
BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:-//CALENDARSERVER.ORG//NONSGML Version 1//EN
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:STANDARD
DTSTART:20071104T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
TZNAME:EST
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:20070311T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
TZNAME:EDT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:882C3D50-0DAE-45CB-A2E7-DA75DA9BE452
DTSTART;TZID=America/New_York:20110131T130000
DTEND;TZID=America/New_York:20110131T140000
ATTENDEE;CN=User 01;CUTYPE=INDIVIDUAL;EMAIL=user01@example.com;PARTSTAT=AC
 CEPTED:urn:uuid:user01
ATTENDEE;CN=User 02;CUTYPE=INDIVIDUAL;EMAIL=user02@example.com;PARTSTAT=AC
 CEPTED:urn:uuid:user02
CREATED:20110124T170357Z
DTSTAMP:20110124T170425Z
ORGANIZER;CN=User 01;EMAIL=user01@example.com:urn:uuid:user01
SEQUENCE:3
SUMMARY:Some Event For You
TRANSP:TRANSPARENT
X-APPLE-NEEDS-REPLY:TRUE
END:VEVENT
END:VCALENDAR
"""



class Deterministic(object):
    def gauss(self, mean, stddev):
        """
        Pretend to return a value from a gaussian distribution with mu
        parameter C{mean} and sigma parameter C{stddev}.  But actually
        always return C{mean + 1}.
        """
        return mean + 1


    def choice(self, sequence):
        return sequence[0]



class StubClient(BaseClient):
    def __init__(self, number):
        self._events = {}
        self._calendars = {}
        self.user = u"user%02d" % (number,)
        self.email = u"mailto:user%02d@example.com" % (number,)


    def addEvent(self, href, vevent):
        self._events[href] = Event(href, None, vevent)


    def deleteEvent(self, href):
        event = self._events.pop(href)
        calendar, uid = href.rsplit('/', 1)
        del self._calendars[calendar + '/'].events[uid]


    def addEventAttendee(self, href, attendee):
        vevent = self._events[href].vevent
        attendees = vevent.contents[u'vevent'][0].contents.setdefault(u'attendee', [])
        attendees.append(attendee)


    def changeEventAttendee(self, href, old, new):
        vevent = self._events[href].vevent
        attendees = vevent.contents[u'vevent'][0].contents.setdefault(u'attendee', [])
        attendees.remove(old)
        attendees.append(new)
        return succeed(None)


class InviterTests(TestCase):
    """
    Tests for loadtest.profiles.Inviter.
    """
    def _simpleAccount(self, userNumber, eventText):
        vevent = list(readComponents(eventText))[0]
        calendar = Calendar(
            caldavxml.calendar, u'calendar', u'/cal/', None)
        event = Event(calendar.url + u'1234.ics', None, vevent)
        calendar.events = {u'1234.ics': event}
        client = StubClient(userNumber)
        client._events.update({event.url: event})
        client._calendars.update({calendar.url: calendar})

        return vevent, event, calendar, client


    def test_doNotAddAttendeeToInbox(self):
        """
        When the only calendar with any events is a schedule inbox, no
        attempt is made to add attendees to an event on that calendar.
        """
        userNumber = 10
        vevent, event, calendar, client = self._simpleAccount(
            userNumber, SIMPLE_EVENT)
        calendar.resourceType = caldavxml.schedule_inbox
        inviter = Inviter(None, client, userNumber)
        inviter._invite()
        self.assertNotIn(u'attendee', vevent.contents[u'vevent'][0].contents)


    def test_doNotAddAttendeeToNoCalendars(self):
        """
        When there are no calendars and no events at all, the inviter
        does nothing.
        """
        userNumber = 13
        client = StubClient(userNumber)
        inviter = Inviter(None, client, userNumber)
        inviter._invite()
        self.assertEquals(client._events, {})
        self.assertEquals(client._calendars, {})


    def test_doNotAddAttendeeToUninitializedEvent(self):
        """
        When there is an L{Event} on a calendar but the details of the
        event have not yet been retrieved, no attempt is made to add
        invitees to that event.
        """
        userNumber = 19
        vevent, event, calendar, client = self._simpleAccount(
            userNumber, SIMPLE_EVENT)
        event.vevent = event.etag = event.scheduleTag = None
        inviter = Inviter(None, client, userNumber)
        inviter._invite()
        self.assertEquals(client._events, {event.url: event})
        self.assertEquals(client._calendars, {calendar.url: calendar})


    def test_addAttendeeToEvent(self):
        """
        When there is a normal calendar with an event, inviter adds an
        attendee to it.
        """
        userNumber = 16
        vevent, event, calendar, client = self._simpleAccount(
            userNumber, SIMPLE_EVENT)
        inviter = Inviter(None, client, userNumber)
        inviter.random = Deterministic()
        inviter._invite()
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        self.assertEquals(len(attendees), 1)
        self.assertEquals(attendees[0].params, {
                u'CN': [u'User %d' % (userNumber + 1,)],
                u'CUTYPE': [u'INDIVIDUAL'],
                u'EMAIL': [u'user%d@example.com' % (userNumber + 1,)],
                u'PARTSTAT': [u'NEEDS-ACTION'],
                u'ROLE': [u'REQ-PARTICIPANT'],
                u'RSVP': [u'TRUE']})



    def test_doNotAddSelfToEvent(self):
        """
        If the inviter randomly selects its own user to be added to
        the attendee list, a different user is added instead.
        """
        selfNumber = 12
        vevent, event, calendar, client = self._simpleAccount(
            selfNumber, SIMPLE_EVENT)

        otherNumber = 20
        values = [selfNumber, otherNumber]

        inviter = Inviter(None, client, selfNumber)
        inviter.random = Deterministic()
        inviter.random.gauss = lambda mu, sigma: values.pop(0)
        inviter._invite()
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        self.assertEquals(len(attendees), 1)
        self.assertEquals(attendees[0].params, {
                u'CN': [u'User %d' % (otherNumber,)],
                u'CUTYPE': [u'INDIVIDUAL'],
                u'EMAIL': [u'user%d@example.com' % (otherNumber,)],
                u'PARTSTAT': [u'NEEDS-ACTION'],
                u'ROLE': [u'REQ-PARTICIPANT'],
                u'RSVP': [u'TRUE']})



    def test_doNotAddExistingToEvent(self):
        """
        If the inviter randomly selects a user which is already an
        invitee on the event, a different user is added instead.
        """
        selfNumber = 13
        vevent, event, calendar, client = self._simpleAccount(
            selfNumber, INVITED_EVENT)

        invitee = vevent.contents[u'vevent'][0].contents[u'attendee'][0]
        inviteeNumber = int(invitee.params[u'CN'][0].split()[1])
        anotherNumber = inviteeNumber + 5
        values = [inviteeNumber, anotherNumber]

        inviter = Inviter(None, client, selfNumber)
        inviter.random = Deterministic()
        inviter.random.gauss = lambda mu, sigma: values.pop(0)
        inviter._invite()
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        self.assertEquals(len(attendees), 3)
        self.assertEquals(attendees[2].params, {
                u'CN': [u'User %02d' % (anotherNumber,)],
                u'CUTYPE': [u'INDIVIDUAL'],
                u'EMAIL': [u'user%02d@example.com' % (anotherNumber,)],
                u'PARTSTAT': [u'NEEDS-ACTION'],
                u'ROLE': [u'REQ-PARTICIPANT'],
                u'RSVP': [u'TRUE']})



class AccepterTests(TestCase):
    """
    Tests for loadtest.profiles.Accepter.
    """
    def test_ignoreEventOnUnknownCalendar(self):
        """
        If an event on an unknown calendar changes, it is ignored.
        """
        userNumber = 13
        client = StubClient(userNumber)
        accepter = Accepter(None, client, userNumber)
        accepter.eventChanged('/some/calendar/1234.ics')


    def test_ignoreNonCalendar(self):
        """
        If an event is on a calendar which is not of type
        {CALDAV:}calendar, it is ignored.
        """
        userNumber = 14
        calendarURL = '/some/calendar/'
        calendar = Calendar(
            caldavxml.schedule_inbox, u'inbox', calendarURL, None)
        client = StubClient(userNumber)
        client._calendars[calendarURL] = calendar
        accepter = Accepter(None, client, userNumber)
        accepter.eventChanged(calendarURL + '1234.ics')


    def test_ignoreAccepted(self):
        """
        If the client is an attendee on an event but the PARTSTAT is
        not NEEDS-ACTION, the event is ignored.
        """
        vevent = list(readComponents(ACCEPTED_EVENT))[0]
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        userNumber = int(attendees[1].params[u'CN'][0].split(None, 1)[1])
        calendarURL = '/some/calendar/'
        calendar = Calendar(
            caldavxml.calendar, u'calendar', calendarURL, None)
        client = StubClient(userNumber)
        client._calendars[calendarURL] = calendar
        event = Event(calendarURL + u'1234.ics', None, vevent)
        client._events[event.url] = event
        accepter = Accepter(None, client, userNumber)
        accepter.eventChanged(event.url)


    def test_ignoreAlreadyAccepting(self):
        """
        If the client sees an event change a second time before
        responding to an invitation found on it during the first
        change notification, the second change notification does not
        generate another accept attempt.
        """
        clock = Clock()
        randomDelay = 7
        vevent = list(readComponents(INVITED_EVENT))[0]
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        userNumber = int(attendees[1].params[u'CN'][0].split(None, 1)[1])
        calendarURL = '/some/calendar/'
        calendar = Calendar(
            caldavxml.calendar, u'calendar', calendarURL, None)
        client = StubClient(userNumber)
        client._calendars[calendarURL] = calendar
        event = Event(calendarURL + u'1234.ics', None, vevent)
        client._events[event.url] = event
        accepter = Accepter(clock, client, userNumber)
        accepter.random = Deterministic()
        accepter.random.gauss = lambda mu, sigma: randomDelay
        accepter.eventChanged(event.url)
        accepter.eventChanged(event.url)
        clock.advance(randomDelay)


    def test_acceptInvitation(self):
        """
        If the client is an attendee on an event and the PARTSTAT is
        NEEDS-ACTION, a response is generated which accepts the
        invitation and the corresponding event in the
        I{schedule-inbox} is deleted.
        """
        clock = Clock()
        randomDelay = 7
        vevent = list(readComponents(INVITED_EVENT))[0]
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        userNumber = int(attendees[1].params[u'CN'][0].split(None, 1)[1])
        client = StubClient(userNumber)
        
        calendarURL = '/some/calendar/'
        calendar = Calendar(
            caldavxml.calendar, u'calendar', calendarURL, None)
        client._calendars[calendarURL] = calendar

        inboxURL = '/some/inbox/'
        inbox = Calendar(
            caldavxml.schedule_inbox, u'the inbox', inboxURL, None)
        client._calendars[inboxURL] = inbox

        event = Event(calendarURL + u'1234.ics', None, vevent)
        client._setEvent(event.url, event)

        inboxEvent = Event(inboxURL + u'4321.ics', None, vevent)
        client._setEvent(inboxEvent.url, inboxEvent)

        accepter = Accepter(clock, client, userNumber)
        accepter.random = Deterministic()
        accepter.random.gauss = lambda mu, sigma: randomDelay
        accepter.eventChanged(event.url)
        clock.advance(randomDelay)

        vevent = client._events[event.url].vevent
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        self.assertEquals(len(attendees), 2)
        self.assertEquals(
            attendees[1].params[u'CN'], [u'User %02d' % (userNumber,)])
        self.assertEquals(
            attendees[1].params[u'PARTSTAT'], [u'ACCEPTED'])
        self.assertNotIn(u'RSVP', attendees[1].params)

        self.assertNotIn(inboxEvent.url, client._events)
        self.assertNotIn('4321.ics', inbox.events)


    def test_reacceptInvitation(self):
        """
        If a client accepts an invitation on an event and then is
        later re-invited to the same event, the invitation is again
        accepted.
        """
        clock = Clock()
        randomDelay = 7
        vevent = list(readComponents(INVITED_EVENT))[0]
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        userNumber = int(attendees[1].params[u'CN'][0].split(None, 1)[1])
        calendarURL = '/some/calendar/'
        calendar = Calendar(
            caldavxml.calendar, u'calendar', calendarURL, None)
        client = StubClient(userNumber)
        client._calendars[calendarURL] = calendar
        event = Event(calendarURL + u'1234.ics', None, vevent)
        client._events[event.url] = event
        accepter = Accepter(clock, client, userNumber)
        accepter.random = Deterministic()
        accepter.random.gauss = lambda mu, sigma: randomDelay
        accepter.eventChanged(event.url)
        clock.advance(randomDelay)

        # Now re-set the event so it has to be accepted again
        event.vevent = list(readComponents(INVITED_EVENT))[0]

        # And now re-deliver it
        accepter.eventChanged(event.url)
        clock.advance(randomDelay)

        # And ensure that it was accepted again
        vevent = client._events[event.url].vevent
        attendees = vevent.contents[u'vevent'][0].contents[u'attendee']
        self.assertEquals(len(attendees), 2)
        self.assertEquals(
            attendees[1].params[u'CN'], [u'User %02d' % (userNumber,)])
        self.assertEquals(
            attendees[1].params[u'PARTSTAT'], [u'ACCEPTED'])
        self.assertNotIn(u'RSVP', attendees[1].params)



class EventerTests(TestCase):
    """
    Tests for loadtest.profiles.Eventer, a profile which adds new
    events on calendars.
    """
    def test_doNotAddEventOnInbox(self):
        """
        When the only calendar is a schedule inbox, no attempt is made
        to add events on it.
        """
        calendar = Calendar(
            caldavxml.schedule_inbox, u'inbox', u'/sched/inbox', None)
        client = StubClient(21)
        client._calendars.update({calendar.url: calendar})

        eventer = Eventer(None, client, None)
        eventer._addEvent()

        self.assertEquals(client._events, {})


    def test_addEvent(self):
        """
        When there is a normal calendar to add events to,
        L{Eventer._addEvent} adds an event to it.
        """
        calendar = Calendar(
            caldavxml.calendar, u'personal stuff', u'/cals/personal', None)
        client = StubClient(31)
        client._calendars.update({calendar.url: calendar})

        eventer = Eventer(None, client, None)
        eventer._addEvent()

        self.assertEquals(len(client._events), 1)

        # XXX Vary the event period/interval and the uid

