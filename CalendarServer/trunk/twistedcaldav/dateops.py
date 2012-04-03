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
from pycalendar.datetime import PyCalendarDateTime
from pycalendar.timezone import PyCalendarTimezone
from pycalendar.period import PyCalendarPeriod
import datetime
import dateutil.tz

"""
Date/time Utilities
"""

__all__ = [
    "normalizeForIndex",
    "floatoffset",
    "compareDateTime",
    "differenceDateTime",
    "timeRangesOverlap",
    "normalizePeriodList",
    "clipPeriod"
]

import calendar

def normalizeForIndex(dt):
    """
    Normalize a L{PyCalendarDateTime} object for use in the Index.
    Convert to date-time in UTC.
    @param dt: a L{PyCalendarDateTime} object to normalize
    @return: the normalized PyCalendarDateTime
    """
    if not isinstance(dt, PyCalendarDateTime):
        raise TypeError("%r is not a PyCalendarDateTime instance" % (dt,))
    
    dt = dt.duplicate()
    if dt.isDateOnly():
        dt.setDateOnly(False)
        dt.setHHMMSS(0, 0, 0)
        dt.setTimezoneID(None)  # Keep it floating
        return dt
    elif dt.floating():
        return dt
    else:
        dt.adjustToUTC()
        return dt

def normalizeToUTC(dt):
    """
    Normalize a L{PyCalendarDateTime} object to UTC.
    """
    if not isinstance(dt, PyCalendarDateTime):
        raise TypeError("%r is not a PyCalendarDateTime instance" % (dt,))
    
    dt = dt.duplicate()
    if dt.isDateOnly():
        dt.setDateOnly(False)
        dt.setHHMMSS(0, 0, 0)
        dt.setTimezoneUTC(True)
        return dt
    elif dt.floating():
        dt.setTimezoneUTC(True)
        return dt
    else:
        dt.adjustToUTC()
        return dt

def floatoffset(dt, pytz):
    """
    Apply the timezone offset to the supplied time, then force tz to utc. This gives the local
    date-time as if the local tz were UTC. It can be used in floating time comparisons with UTC date-times.
    
    @param dt: a L{PyCalendarDateTime} object to normalize
    @param pytz: a L{PyCalendarTimezone} object to apply offset from
    @return: the normalized PyCalendarDateTime
    """
    
    if pytz is None:
        pytz = PyCalendarTimezone(utc=True)
    
    dt = dt.duplicate()
    dt.adjustTimezone(pytz)
    dt.setTimezoneUTC(True)
    return dt

def adjustFloatingToTimezone(dtadjust, dtcopyfrom, pytz=None):
    
    dtadjust = dtadjust.duplicate()
    dtadjust.setTimezone(pytz if pytz else dtcopyfrom.getTimezone())
    return dtadjust

def compareDateTime(dt1, dt2, defaulttz=None):
    
    if dt1.floating() and not dt2.floating():
        dt1 = adjustFloatingToTimezone(dt1, dt2, defaulttz)
    elif dt2.floating() and not dt1.floating():
        dt2 = adjustFloatingToTimezone(dt2, dt1, defaulttz)
    
    return dt1.compareDateTime(dt2)

def differenceDateTime(start, end, defaulttz = None):

    if start.floating() and not end.floating():
        start = adjustFloatingToTimezone(start, end, defaulttz)
    elif end.floating() and not start.floating():
        end = adjustFloatingToTimezone(end, start, defaulttz)

    return end - start

def timeRangesOverlap(start1, end1, start2, end2, defaulttz = None):
    # Can't compare date-time and date only, so normalize
    # to date only if they are mixed.
    if (start1 is not None) and not start1.isDateOnly() and (start2 is not None) and start2.isDateOnly(): start1 = start1.setDateOnly(True)
    if (start2 is not None) and not start2.isDateOnly() and (start1 is not None) and start1.isDateOnly(): start2 = start2.setDateOnly(True)
    if (end1 is not None) and not end1.isDateOnly() and (end2 is not None) and end2.isDateOnly(): end1 = end1.setDateOnly(True)
    if (end2 is not None) and not end2.isDateOnly() and (end1 is not None) and end1.isDateOnly(): end2 = end2.setDateOnly(True)

    # Note that start times are inclusive and end times are not.
    if start1 is not None and start2 is not None:
        if end1 is not None and end2 is not None:
            return compareDateTime(start1, end2, defaulttz) < 0 and compareDateTime(end1, start2, defaulttz) > 0
        elif end1 is None:
            return compareDateTime(start1, start2, defaulttz) >= 0 and compareDateTime(start1, end2, defaulttz) < 0
        elif end2 is None:
            return compareDateTime(start2, end1, defaulttz) < 0
        else:
            return False
    elif start1 is not None:
        return compareDateTime(start1, end2, defaulttz) < 0
    elif start2 is not None:
        return compareDateTime(end1, end2, defaulttz) < 0 and compareDateTime(end1, start2, defaulttz) > 0
    else:
        return False

def normalizePeriodList(periods):
    """
    Normalize the list of periods by merging overlapping or consecutive ranges
    and sorting the list by each periods start.
    @param list: a list of tuples of L{PyCalendarPeriod}. The list is changed in place.
    """
    
    # First sort the list
    def sortPeriods(p1, p2):
        """
        Compare two periods. Sort by their start and then end times.
        A period is a L{PyCalendarPeriod}.
        @param p1: first period
        @param p2: second period
        @return: 1 if p1>p2, 0 if p1==p2, -1 if p1<p2
        """

        assert isinstance(p1, PyCalendarPeriod), "Period is not a PyCalendarPeriod: %r" % (p1,)
        assert isinstance(p2, PyCalendarPeriod), "Period is not a PyCalendarPeriod: %r" % (p2,)
        
        
        if p1.getStart() == p2.getStart():
            cmp1 = p1.getEnd()
            cmp2 = p2.getEnd()
        else:
            cmp1 = p1.getStart()
            cmp2 = p2.getStart()
        
        return compareDateTime(cmp1, cmp2)

    for period in periods:
        period.adjustToUTC()
    periods.sort(cmp=sortPeriods)
    
    # Now merge overlaps and consecutive periods
    index = None
    p = None
    pe = None
    for i in xrange(len(periods)):
        if p is None:
            index = i
            p = periods[i]
            pe = p.getEnd()
            continue
        ie = periods[i].getEnd()
        if (pe >= periods[i].getStart()):
            if ie > pe:
                periods[index] = PyCalendarPeriod(periods[index].getStart(), ie)
                pe = ie
            periods[i] = None
        else:
            index = i
            p = periods[i]
            pe =p.getEnd()
    periods[:] = [x for x in periods if x]

def clipPeriod(period, clipPeriod):
    """
    Clip the start/end period so that it lies entirely within the clip period.
    @param period: the (start, end) tuple for the period to be clipped.
    @param clipPeriod: the (start, end) tuple for the period to clip to.
    @return: the (start, end) tuple for the clipped period, or
             None if the period is outside the clip period
    """
    start = period.getStart()
    end = period.getEnd()
    clipStart = clipPeriod.getStart()
    clipEnd = clipPeriod.getEnd()

    if start < clipStart:
        start = clipStart
    
    if end > clipEnd:
        end = clipEnd
    
    if start >= end:
        return None
    else:
        # Try to preserve use of duration in period
        result = PyCalendarPeriod(start, end)
        result.setUseDuration(period.getUseDuration())
        return result

def pyCalendarTodatetime(pydt):
    
    if pydt.isDateOnly():
        return datetime.date(year=pydt.getYear(), month=pydt.getMonth(), day=pydt.getDay())
    else:
        return datetime.datetime(
            year=pydt.getYear(),
            month=pydt.getMonth(),
            day=pydt.getDay(),
            hour=pydt.getHours(),
            minute=pydt.getMinutes(),
            second=pydt.getSeconds(),
            tzinfo=dateutil.tz.tzutc()
        )

SQL_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"

def parseSQLTimestamp(ts):
    # Handle case where fraction seconds may not be present
    if len(ts) < 20:
        ts += ".0"
    return datetime.datetime.strptime(ts, SQL_TIMESTAMP_FORMAT)

def parseSQLTimestampToPyCalendar(ts):
    """
    Parse an SQL formated timestamp into a PyCalendarDateTime
    @param ts: the SQL timestamp
    @type ts: C{str}
    
    @return: L{PyCalendarDateTime} result
    """
    
    dt = datetime.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    return PyCalendarDateTime(year=dt.year, month=dt.month, day=dt.day, hours=dt.hour, minutes=dt.minute, seconds=dt.second)

def parseSQLDateToPyCalendar(ts):
    """
    Parse an SQL formated date into a PyCalendarDateTime
    @param ts: the SQL date
    @type ts: C{str}
    
    @return: L{PyCalendarDateTime} result
    """
    
    dt = datetime.datetime.strptime(ts[:10], "%Y-%m-%d")
    return PyCalendarDateTime(year=dt.year, month=dt.month, day=dt.day)

def datetimeMktime(dt):

    assert isinstance(dt, datetime.date)

    if dt.tzinfo is None:
        dt.replace(tzinfo=dateutil.tz.tzutc())
    return calendar.timegm(dt.utctimetuple())

