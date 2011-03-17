##
# Copyright (c) 2011 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

"""
Benchmark a server's handling of events with a bounded recurrence.
"""

from uuid import uuid4
from itertools import count
from datetime import datetime, timedelta

from _event_create import (
    makeAttendees, makeVCalendar, formatDate, measure as _measure)

def makeEvent(i, organizerSequence, attendeeCount):
    """
    Create a new half-hour long event that starts soon and recurs
    daily for the next five days.
    """
    now = datetime.now()
    start = now.replace(minute=15, second=0, microsecond=0) + timedelta(hours=i)
    end = start + timedelta(minutes=30)
    until = start + timedelta(days=5)
    rrule = "RRULE:FREQ=DAILY;INTERVAL=1;UNTIL=" + formatDate(until)
    attendees = makeAttendees(attendeeCount)
    attendees.append(
        'ATTENDEE;CN="Resource 01";CUTYPE=INDIVIDUAL;PARTSTAT=NEEDS-ACTION;RSVP=T\n'
        ' RUE;SCHEDULE-STATUS="1.2":urn:uuid:resource01\n')
    return makeVCalendar(
        uuid4(), start, end, rrule, organizerSequence, attendees)


def measure(host, port, dtrace, attendeeCount, samples):
    calendar = "bounded-recurrence-autoaccept"
    organizerSequence = 1

    # An infinite stream of recurring VEVENTS to PUT to the server.
    events = ((i, makeEvent(i, organizerSequence, attendeeCount)) for i in count(2))

    return _measure(
        calendar, organizerSequence, events,
        host, port, dtrace, samples)
