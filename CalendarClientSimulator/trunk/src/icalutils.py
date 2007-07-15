##
# Copyright (c) 2007 Apple Inc. All rights reserved.
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
# DRI: Cyrus Daboo, cdaboo@apple.com
##

from pycalendar import definitions
from pycalendar import itipdefinitions
from pycalendar.calendar import PyCalendar
from pycalendar.datetime import PyCalendarDateTime
from pycalendar.property import PyCalendarProperty
from pycalendar.value import PyCalendarValue
from pycalendar.vfreebusy import PyCalendarVFreeBusy
import cStringIO as StringIO
from pycalendar.vevent import PyCalendarVEvent
from pycalendar.attribute import PyCalendarAttribute
import datetime

def parseCalendar(caldata):

    cal = PyCalendar()
    if cal.parse(StringIO.StringIO(caldata)):
        return cal
    else:
        return None

def parseUID(caldata):

    calendar = parseCalendar(caldata)
    return getUID(calendar)

def getUID(calendar):

    for item in calendar.getVEventsDB():
        return item.getUID()
    else:
        return None

def getORGANZIER(calendar):

    for item in calendar.getVEventsDB():
        return item.getPropertyString(definitions.cICalProperty_ORGANIZER)

    return None

def getATTENDEE(calendar):

    for item in calendar.getVEventsDB():
        return item.getPropertyString(definitions.cICalProperty_ATTENDEE)

    return None

def generateVEVENT(organizer=None, attendees=None):
    
    now = datetime.datetime.now()

    cal = PyCalendar()
    
    vev = PyCalendarVEvent(calendar=cal.getRef())
    
    start = PyCalendarDateTime(year=now.year, month=now.month, day=now.day, hours=now.hour, minutes=now.minute, seconds=0)
    end = PyCalendarDateTime(copyit=start)
    end.offsetHours(1)
    vev.editTimingStartEnd(start, end)

    if organizer:
        prop = PyCalendarProperty(definitions.cICalProperty_ORGANIZER, organizer, PyCalendarValue.VALUETYPE_CALADDRESS)
        vev.addProperty(prop)
        prop = PyCalendarProperty(definitions.cICalProperty_ATTENDEE, organizer, PyCalendarValue.VALUETYPE_CALADDRESS)
        prop.addAttribute(PyCalendarAttribute(definitions.cICalAttribute_PARTSTAT, definitions.cICalAttribute_PARTSTAT_ACCEPTED))
        vev.addProperty(prop)

    if attendees:
        for attendee in attendees:
            prop = PyCalendarProperty(definitions.cICalProperty_ATTENDEE, attendee, PyCalendarValue.VALUETYPE_CALADDRESS)
            prop.addAttribute(PyCalendarAttribute(definitions.cICalAttribute_PARTSTAT, definitions.cICalAttribute_PARTSTAT_NEEDSACTION))
            prop.addAttribute(PyCalendarAttribute(definitions.cICalAttribute_RSVP, definitions.cICalAttribute_RSVP_TRUE))
            vev.addProperty(prop)

    vev.setUID("")

    vev.initDTSTAMP()
    
    cal.addComponent(vev)

    os = StringIO.StringIO()
    cal.generate(os)
    return os.getvalue().replace("\n", "\r\n")

def generateVFREEBUSY(organizer, attendees):
    
    now = datetime.datetime.now()

    cal = PyCalendar()
    cal.addProperty(PyCalendarProperty(definitions.cICalProperty_METHOD, itipdefinitions.cICalMethod_REQUEST))
    
    vfb = PyCalendarVFreeBusy(calendar=cal.getRef())
    
    start = PyCalendarDateTime(year=now.year, month=now.month, day=now.day, hours=0, minutes=0, seconds=0)
    end = PyCalendarDateTime(copyit=start)
    end.offsetDay(1)
    vfb.editTimingStartEnd(start, end)
    

    prop = PyCalendarProperty(definitions.cICalProperty_ORGANIZER, organizer, PyCalendarValue.VALUETYPE_CALADDRESS)
    vfb.addProperty(prop)
    prop = PyCalendarProperty(definitions.cICalProperty_ATTENDEE, organizer, PyCalendarValue.VALUETYPE_CALADDRESS)
    vfb.addProperty(prop)

    for attendee in attendees:
        prop = PyCalendarProperty(definitions.cICalProperty_ATTENDEE, attendee, PyCalendarValue.VALUETYPE_CALADDRESS)
        vfb.addProperty(prop)

    vfb.setUID("")

    vfb.initDTSTAMP()
    
    cal.addComponent(vfb)

    os = StringIO.StringIO()
    cal.generate(os)
    return os.getvalue().replace("\n", "\r\n")

def generateiTIPSave(calendar, me):
    # Generate accepted data and write to main calendar
    calendar.removeProperties(definitions.cICalProperty_METHOD)
    for vev in calendar.getComponents(PyCalendar.VEVENT).mItems.itervalues():
        break
    else:
        return
    attendees = vev.getProperties()[definitions.cICalProperty_ATTENDEE]
    for attendee in attendees:
        if attendee.getTextValue().getValue() == me:
            attendee.removeAttributes(definitions.cICalAttribute_PARTSTAT)
            attendee.removeAttributes(definitions.cICalAttribute_RSVP)
            attendee.addAttribute(PyCalendarAttribute(definitions.cICalAttribute_PARTSTAT, definitions.cICalAttribute_PARTSTAT_ACCEPTED))

    os = StringIO.StringIO()
    calendar.generate(os)
    return os.getvalue().replace("\n", "\r\n")

def generateiTIPReply(calendar, me):
    # Generate accepted data and write to main calendar
    calendar.removeProperties(definitions.cICalProperty_METHOD)
    calendar.addProperty(PyCalendarProperty(definitions.cICalProperty_METHOD, itipdefinitions.cICalMethod_REPLY))

    for vev in calendar.getComponents(PyCalendar.VEVENT).mItems.itervalues():
        break
    else:
        return
    attendees = vev.getProperties()[definitions.cICalProperty_ATTENDEE]
    for attendee in attendees:
        if attendee.getTextValue().getValue() == me:
            break
    vev.removeProperties(definitions.cICalProperty_ATTENDEE)
    vev.addProperty(attendee)
    organizers = vev.getProperties()[definitions.cICalProperty_ORGANIZER]
    organizer = organizers[0].getTextValue().getValue()

    os = StringIO.StringIO()
    calendar.generate(os)
    return os.getvalue().replace("\n", "\r\n"), organizer

def updateAttendeeStatus(caldata, update):

    calendar = parseCalendar(caldata)

    for vev in calendar.getComponents(PyCalendar.VEVENT).mItems.itervalues():
        break
    else:
        return
    attendees = vev.getProperties()[definitions.cICalProperty_ATTENDEE]
    for attendee in attendees:
        if attendee.getTextValue().getValue() == update:
            attendee.removeAttributes(definitions.cICalAttribute_PARTSTAT)
            attendee.removeAttributes(definitions.cICalAttribute_RSVP)
            attendee.addAttribute(PyCalendarAttribute(definitions.cICalAttribute_PARTSTAT, definitions.cICalAttribute_PARTSTAT_ACCEPTED))
            break

    os = StringIO.StringIO()
    calendar.generate(os)
    return os.getvalue().replace("\n", "\r\n")
