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

from __future__ import division

import sys, random
from uuid import uuid4

from caldavclientlibrary.protocol.caldav.definitions import caldavxml

from twisted.python import context
from twisted.python.log import msg
from twisted.python.failure import Failure
from twisted.internet.defer import Deferred, succeed, fail
from twisted.internet.task import LoopingCall
from twisted.web.http import PRECONDITION_FAILED

from twistedcaldav.ical import Property, Component

from contrib.performance.stats import NearFutureDistribution, NormalDistribution, UniformDiscreteDistribution, mean, median
from contrib.performance.loadtest.logger import SummarizingMixin
from contrib.performance.loadtest.ical import IncorrectResponseCode

from pycalendar.datetime import PyCalendarDateTime
from pycalendar.duration import PyCalendarDuration

class ProfileBase(object):
    """
    Base class which provides some conveniences for profile
    implementations.
    """
    random = random

    def __init__(self, reactor, simulator, client, userNumber, **params):
        self._reactor = reactor
        self._sim = simulator
        self._client = client
        self._number = userNumber
        self.setParameters(**params)


    def setParameters(self):
        pass


    def _calendarsOfType(self, calendarType):
        return [
            cal 
            for cal 
            in self._client._calendars.itervalues() 
            if cal.resourceType == calendarType]


    def _isSelfAttendee(self, attendee):
        """
        Try to match one of the attendee's identifiers against one of
        C{self._client}'s identifiers.  Return C{True} if something matches,
        C{False} otherwise.
        """
        return attendee.parameterValue('EMAIL') == self._client.email[len('mailto:'):]


    def _newOperation(self, label, deferred):
        """
        Helper to emit a log event when a new operation is started and
        another one when it completes.
        """
        # If this is a scheduled request, record the lag in the
        # scheduling now so it can be reported when the response is
        # received.
        lag = context.get('lag', None)

        before = self._reactor.seconds()
        msg(type="operation", phase="start",
            user=self._client.record.uid, label=label, lag=lag)

        def finished(passthrough):
            success = not isinstance(passthrough, Failure)
            if not success:
                passthrough.trap(IncorrectResponseCode)
                passthrough = passthrough.value.response
            after = self._reactor.seconds()
            msg(type="operation", phase="end", duration=after - before,
                user=self._client.record.uid, label=label, success=success)
            return passthrough
        deferred.addBoth(finished)
        return deferred
        



class CannotAddAttendee(Exception):
    """
    Indicates no new attendees can be invited to a particular event.
    """


def loopWithDistribution(reactor, distribution, function):
    result = Deferred()

    def repeat(ignored):
        reactor.callLater(distribution.sample(), iterate)

    def iterate():
        d = function()
        d.addCallbacks(repeat, result.errback)

    repeat(None)
    return result



class Inviter(ProfileBase):
    """
    A Calendar user who invites and de-invites other users to events.
    """
    def setParameters(
        self,
        enabled=True,
        sendInvitationDistribution=NormalDistribution(600, 60),
        inviteeDistanceDistribution=UniformDiscreteDistribution(range(-10, 11))
    ):
        self.enabled = enabled
        self._sendInvitationDistribution = sendInvitationDistribution
        self._inviteeDistanceDistribution = inviteeDistanceDistribution


    def run(self):
        return loopWithDistribution(
            self._reactor, self._sendInvitationDistribution, self._invite)


    def _addAttendee(self, event, attendees):
        """
        Create a new attendee to add to the list of attendees for the
        given event.
        """
        selfRecord = self._sim.getUserRecord(self._number)
        invitees = set([u'urn:uuid:%s' % (selfRecord.uid,)])
        for att in attendees:
            invitees.add(att.value())

        for _ignore_i in range(10):
            invitee = max(
                0, self._number + self._inviteeDistanceDistribution.sample())
            try:
                record = self._sim.getUserRecord(invitee)
            except IndexError:
                continue
            uuid = u'urn:uuid:%s' % (record.uid,)
            if uuid not in invitees:
                break
        else:
            return fail(CannotAddAttendee("Can't find uninvited user to invite."))

        attendee = Property(
            name=u'ATTENDEE',
            value=uuid,
            params={
            'CN': record.commonName,
            'CUTYPE': 'INDIVIDUAL',
            'EMAIL': record.email,
            'PARTSTAT': 'NEEDS-ACTION',
            'ROLE': 'REQ-PARTICIPANT',
            'RSVP': 'TRUE',
            #'SCHEDULE-STATUS': '1.2',
            },
        )

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

                vevent = event.mainComponent()
                organizer = vevent.getOrganizerProperty()
                if organizer is not None and not self._isSelfAttendee(organizer):
                    # This event was organized by someone else, don't try to invite someone to it.
                    continue

                href = calendar.url + uuid

                # Find out who might attend
                attendees = tuple(vevent.properties('ATTENDEE'))

                d = self._addAttendee(event, attendees)
                d.addCallbacks(
                    lambda attendee:
                        self._client.addEventAttendee(
                            href, attendee),
                    lambda reason: reason.trap(CannotAddAttendee))
                return self._newOperation("invite", d)

        # Oops, either no events or no calendars to play with.
        return succeed(None)



class Accepter(ProfileBase):
    """
    A Calendar user who accepts invitations to events. As well as accepting requests, this
    will also remove cancels and replies.
    """
    def setParameters(
        self,
        enabled=True,
        acceptDelayDistribution=NormalDistribution(1200, 60)
    ):
        self.enabled = enabled
        self._accepting = set()
        self._acceptDelayDistribution = acceptDelayDistribution


    def run(self):
        self._subscription = self._client.catalog["eventChanged"].subscribe(self.eventChanged)
        # TODO: Propagate errors from eventChanged and _acceptInvitation to this Deferred
        return Deferred()


    def eventChanged(self, href):
        # Just respond to normal calendar events
        calendar = href.rsplit('/', 1)[0] + '/'
        try:
            calendar = self._client._calendars[calendar]
        except KeyError:
            return

        if calendar.resourceType == caldavxml.schedule_inbox:
            # Handle inbox differently
            self.inboxEventChanged(calendar, href)
        elif calendar.resourceType == caldavxml.calendar:
            self.calendarEventChanged(calendar, href)
        else:
            return

    def calendarEventChanged(self, calendar, href):
        if href in self._accepting:
            return

        vevent = self._client._events[href].vevent
        # Check to see if this user is in the attendee list in the
        # NEEDS-ACTION PARTSTAT.
        attendees = tuple(vevent.mainComponent().properties('ATTENDEE'))
        for attendee in attendees:
            if self._isSelfAttendee(attendee):
                if attendee.parameterValue('PARTSTAT') == 'NEEDS-ACTION':
                    delay = self._acceptDelayDistribution.sample()
                    self._accepting.add(href)
                    self._reactor.callLater(
                        delay, self._acceptInvitation, href, attendee)


    def inboxEventChanged(self, calendar, href):
        if href in self._accepting:
            return

        vevent = self._client._events[href].vevent
        method = vevent.propertyValue('METHOD')
        if method == "REPLY":
            # Replies are immediately deleted
            self._accepting.add(href)
            self._reactor.callLater(
                0, self._handleReply, href)

        elif method == "CANCEL":
            # Cancels are handled after a user delay
            delay = self._acceptDelayDistribution.sample()
            self._accepting.add(href)
            self._reactor.callLater(
                delay, self._handleCancel, href)


    def _acceptInvitation(self, href, attendee):
        def change():
            accepted = self._makeAcceptedAttendee(attendee)
            return self._client.changeEventAttendee(href, attendee, accepted)
        d = change()

        def scheduleError(reason):
            reason.trap(IncorrectResponseCode)
            if reason.value.response.code != PRECONDITION_FAILED:
                return reason.value.response.code

            # Download the event again and attempt to make the change
            # to the attendee list again.
            d = self._client.updateEvent(href)
            def cbUpdated(ignored):
                d = change()
                d.addErrback(scheduleError)
                return d
            d.addCallback(cbUpdated)
            return d
        d.addErrback(scheduleError)

        def accepted(ignored):
            # Find the corresponding event in the inbox and delete it.
            uid = self._client._events[href].getUID()
            for cal in self._client._calendars.itervalues():
                if cal.resourceType == caldavxml.schedule_inbox:
                    for event in cal.events.itervalues():
                        if uid == event.getUID():
                            return self._client.deleteEvent(event.url)
        d.addCallback(accepted)
        def finished(passthrough):
            self._accepting.remove(href)
            return passthrough
        d.addBoth(finished)
        return self._newOperation("accept", d)


    def _handleReply(self, href):
        d = self._client.deleteEvent(href)
        def finished(passthrough):
            self._accepting.remove(href)
            if isinstance(passthrough, Failure):
                passthrough.trap(IncorrectResponseCode)
                passthrough = passthrough.response
            return passthrough
        d.addBoth(finished)
        return self._newOperation("reply done", d)


    def _handleCancel(self, href):

        uid = self._client._events[href].getUID()
        d = self._client.deleteEvent(href)

        def removed(ignored):
            # Find the corresponding event in any calendar and delete it.
            for cal in self._client._calendars.itervalues():
                if cal.resourceType == caldavxml.calendar:
                    for event in cal.events.itervalues():
                        if uid == event.getUID():
                            return self._client.deleteEvent(event.url)
        d.addCallback(removed)
        def finished(passthrough):
            self._accepting.remove(href)
            if isinstance(passthrough, Failure):
                passthrough.trap(IncorrectResponseCode)
                passthrough = passthrough.response
            return passthrough
        d.addBoth(finished)
        return self._newOperation("cancelled", d)


    def _makeAcceptedAttendee(self, attendee):
        accepted = attendee.duplicate()
        accepted.setParameter('PARTSTAT', 'ACCEPTED')
        accepted.removeParameter('RSVP')
        return accepted



class Eventer(ProfileBase):
    """
    A Calendar user who creates new events.
    """
    _eventTemplate = Component.fromString("""\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Apple Inc.//iCal 4.0.3//EN
CALSCALE:GREGORIAN
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
""".replace("\n", "\r\n"))

    def setParameters(
        self,
        enabled=True,
        interval=25,
        eventStartDistribution=NearFutureDistribution(),
        eventDurationDistribution=UniformDiscreteDistribution([
            15 * 60, 30 * 60,
            45 * 60, 60 * 60,
            120 * 60
        ])
    ):
        self.enabled = enabled
        self._interval = interval
        self._eventStartDistribution = eventStartDistribution
        self._eventDurationDistribution = eventDurationDistribution


    def run(self):
        self._call = LoopingCall(self._addEvent)
        self._call.clock = self._reactor
        return self._call.start(self._interval)


    def _addEvent(self):
        calendars = self._calendarsOfType(caldavxml.calendar)

        while calendars:
            calendar = self.random.choice(calendars)
            calendars.remove(calendar)

            # Copy the template event and fill in some of its fields
            # to make a new event to create on the calendar.
            vcalendar = self._eventTemplate.duplicate()
            vevent = vcalendar.mainComponent()
            uid = str(uuid4())
            dtstart = self._eventStartDistribution.sample()
            dtend = dtstart + PyCalendarDuration(seconds=self._eventDurationDistribution.sample())
            vevent.replaceProperty(Property("CREATED", PyCalendarDateTime.getNowUTC()))
            vevent.replaceProperty(Property("DTSTAMP", PyCalendarDateTime.getNowUTC()))
            vevent.replaceProperty(Property("DTSTART", dtstart))
            vevent.replaceProperty(Property("DTEND", dtend))
            vevent.replaceProperty(Property("UID", uid))

            href = '%s%s.ics' % (calendar.url, uid)
            d = self._client.addEvent(href, vcalendar)
            return self._newOperation("create", d)


class OperationLogger(SummarizingMixin):
    """
    Profiles will initiate operations which may span multiple requests.  Start
    and stop log messages are emitted for these operations and logged by this
    logger.
    """
    formats = {
        u"start": u"%(user)s - - - - - - - - - - - %(label)8s BEGIN %(lag)s",
        u"end"  : u"%(user)s - - - - - - - - - - - %(label)8s END [%(duration)5.2f s]",
        }

    lagFormat = u'{lag %5.2f ms}'

    _fields = [
        ('operation', 10, '%10s'),
        ('count', 8, '%8s'),
        ('failed', 8, '%8s'),
        ('>3sec', 8, '%8s'),
        ('mean', 8, '%8.4f'),
        ('median', 8, '%8.4f'),
        ('avglag (ms)', 8, '%8.4f'),
        ]

    def __init__(self, outfile=None):
        self._perOperationTimes = {}
        self._perOperationLags = {}
        if outfile is None:
            outfile = sys.stdout
        self._outfile = outfile


    def observe(self, event):
        if event.get("type") == "operation":
            event = event.copy()
            lag = event.get('lag')
            if lag is None:
                event['lag'] = ''
            else:
                event['lag'] = self.lagFormat % (lag * 1000.0,)

            self._outfile.write(
                (self.formats[event[u'phase']] % event).encode('utf-8') + '\n')

            if event[u'phase'] == u'end':
                dataset = self._perOperationTimes.setdefault(event[u'label'], [])
                dataset.append((event[u'success'], event[u'duration']))
            elif lag is not None:
                dataset = self._perOperationLags.setdefault(event[u'label'], [])
                dataset.append(lag)

    def _summarizeData(self, operation, data):
        avglag = mean(self._perOperationLags.get(operation, [0.0])) * 1000.0
        return SummarizingMixin._summarizeData(self, operation, data) + (avglag,)


    def report(self):
        print
        self.printHeader([
                (label, width)
                for (label, width, _ignore_fmt)
                in self._fields])
        self.printData(
            [fmt for (label, width, fmt) in self._fields],
            sorted(self._perOperationTimes.items()))

    _LATENCY_REASON = "Median %(operation)s scheduling lag greater than %(cutoff)sms"
    _FAILED_REASON = "Greater than %(cutoff).0f%% %(operation)s failed"

    def failures(self):
        reasons = []

        # Maximum allowed median scheduling latency, seconds
        lagCutoff = 1.0

        # Maximum allowed ratio of failed operations
        failCutoff = 0.01

        for operation, lags in self._perOperationLags.iteritems():
            if median(lags) > lagCutoff:
                reasons.append(self._LATENCY_REASON % dict(
                        operation=operation.upper(), cutoff=lagCutoff * 1000))

        for operation, times in self._perOperationTimes.iteritems():
            failures = len([success for (success, _ignore_duration) in times if not success])
            if failures / len(times) > failCutoff:
                reasons.append(self._FAILED_REASON % dict(
                        operation=operation.upper(), cutoff=failCutoff * 100))

        return reasons
