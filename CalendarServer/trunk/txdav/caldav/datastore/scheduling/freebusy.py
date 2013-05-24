##
# Copyright (c) 2005-2013 Apple Inc. All rights reserved.
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
from pycalendar.duration import PyCalendarDuration
from pycalendar.period import PyCalendarPeriod
from pycalendar.timezone import PyCalendarTimezone

from twext.python.log import Logger

from twisted.internet.defer import inlineCallbacks, returnValue

from twistedcaldav import caldavxml
from twistedcaldav.caldavxml import caldav_namespace, TimeRange
from twistedcaldav.config import config
from twistedcaldav.dateops import compareDateTime, normalizeToUTC, \
    parseSQLTimestampToPyCalendar, clipPeriod, timeRangesOverlap, \
    normalizePeriodList
from twistedcaldav.ical import Component, Property, iCalendarProductID
from twistedcaldav.instance import InstanceList
from twistedcaldav.memcacher import Memcacher
from twistedcaldav.query import calendarqueryfilter

from txdav.caldav.icalendarstore import QueryMaxResources
from txdav.common.icommondatastore import IndexedSearchException

import uuid
from txdav.base.propertystore.base import PropertyName

log = Logger()

fbtype_mapper = {"BUSY": 0, "BUSY-TENTATIVE": 1, "BUSY-UNAVAILABLE": 2}
fbtype_index_mapper = {'B': 0, 'T': 1, 'U': 2}

fbcacher = Memcacher("FBCache", pickle=True)

class FBCacheEntry(object):

    CACHE_DAYS_FLOATING_ADJUST = 1

    def __init__(self, key, token, timerange, fbresults):
        self.key = key
        self.token = token
        self.timerange = timerange
        self.fbresults = fbresults


    @classmethod
    @inlineCallbacks
    def getCacheEntry(cls, calresource, useruid, timerange):

        key = str(calresource.id()) + "/" + useruid
        token = (yield calresource.syncToken())
        entry = (yield fbcacher.get(key))

        if entry:

            # Offset one day at either end to account for floating
            cached_start = entry.timerange.start + PyCalendarDuration(days=FBCacheEntry.CACHE_DAYS_FLOATING_ADJUST)
            cached_end = entry.timerange.end - PyCalendarDuration(days=FBCacheEntry.CACHE_DAYS_FLOATING_ADJUST)

            # Verify that the requested time range lies within the cache time range
            if compareDateTime(timerange.end, cached_end) <= 0 and compareDateTime(timerange.start, cached_start) >= 0:

                # Verify that cached entry is still valid
                if token == entry.token:
                    returnValue(entry.fbresults)

        returnValue(None)


    @classmethod
    @inlineCallbacks
    def makeCacheEntry(cls, calresource, useruid, timerange, fbresults):

        key = str(calresource.id()) + "/" + useruid
        token = (yield calresource.syncToken())
        entry = cls(key, token, timerange, fbresults)
        yield fbcacher.set(key, entry)



@inlineCallbacks
def generateFreeBusyInfo(
    calresource,
    fbinfo,
    timerange,
    matchtotal,
    excludeuid=None,
    organizer=None,
    organizerPrincipal=None,
    same_calendar_user=False,
    servertoserver=False,
    event_details=None,
    logItems=None,
):
    """
    Run a free busy report on the specified calendar collection
    accumulating the free busy info for later processing.
    @param calresource: the L{Calendar} for a calendar collection.
    @param fbinfo:      the array of busy periods to update.
    @param timerange:   the L{TimeRange} for the query.
    @param matchtotal:  the running total for the number of matches.
    @param excludeuid:  a C{str} containing a UID value to exclude any
        components with that UID from contributing to free-busy.
    @param organizer:   a C{str} containing the value of the ORGANIZER property
        in the VFREEBUSY request.  This is used in conjunction with the UID
        value to process exclusions.
    @param same_calendar_user: a C{bool} indicating whether the calendar user
        requesting the free-busy information is the same as the calendar user
        being targeted.
    @param servertoserver: a C{bool} indicating whether we are doing a local or
        remote lookup request.
    @param event_details: a C{list} into which to store extended VEVENT details if not C{None}
    @param logItems: a C{dict} to store logging info to
    """

    # First check the privilege on this collection
    # TODO: for server-to-server we bypass this right now as we have no way to authorize external users.
    # TODO: actually we by pass altogether by assuming anyone can check anyone else's freebusy

    # May need organizer principal
    organizer_record = calresource.directoryService().recordWithCalendarUserAddress(organizer) if organizer else None
    organizer_uid = organizer_record.uid if organizer_record else ""

    # Free busy is per-user
    useruid = calresource.viewerHome().uid()
    user_record = calresource.directoryService().recordWithUID(useruid)

    # Get the timezone property from the collection.
    tz = calresource.properties().get(PropertyName(caldav_namespace, "calendar-timezone"))

    # Look for possible extended free busy information
    rich_options = {
        "organizer": False,
        "delegate": False,
        "resource": False,
    }
    do_event_details = False
    if event_details is not None and organizer_record is not None and user_record is not None:

        # Check if organizer is attendee
        if organizer_uid == useruid:
            do_event_details = True
            rich_options["organizer"] = True

        # Check if organizer is a delegate of attendee
        proxy = (yield organizer_record.isProxyFor(user_record))
        if config.Scheduling.Options.DelegeteRichFreeBusy and proxy:
            do_event_details = True
            rich_options["delegate"] = True

        # Check if attendee is room or resource
        if config.Scheduling.Options.RoomResourceRichFreeBusy and user_record.getCUType() in ("RESOURCE", "ROOM",):
            do_event_details = True
            rich_options["resource"] = True

    # Try cache
    resources = (yield FBCacheEntry.getCacheEntry(calresource, useruid, timerange)) if config.EnableFreeBusyCache else None

    if resources is None:

        caching = False
        if config.EnableFreeBusyCache:
            # Log extended item
            if logItems is not None:
                logItems["fb-uncached"] = logItems.get("fb-uncached", 0) + 1

            # We want to cache a large range of time based on the current date
            cache_start = normalizeToUTC(PyCalendarDateTime.getToday() + PyCalendarDuration(days=0 - config.FreeBusyCacheDaysBack))
            cache_end = normalizeToUTC(PyCalendarDateTime.getToday() + PyCalendarDuration(days=config.FreeBusyCacheDaysForward))

            # If the requested time range would fit in our allowed cache range, trigger the cache creation
            if compareDateTime(timerange.start, cache_start) >= 0 and compareDateTime(timerange.end, cache_end) <= 0:
                cache_timerange = TimeRange(start=cache_start.getText(), end=cache_end.getText())
                caching = True

        #
        # What we do is a fake calendar-query for VEVENT/VFREEBUSYs in the specified time-range.
        # We then take those results and merge them into one VFREEBUSY component
        # with appropriate FREEBUSY properties, and return that single item as iCal data.
        #

        # Create fake filter element to match time-range
        filter = caldavxml.Filter(
                      caldavxml.ComponentFilter(
                          caldavxml.ComponentFilter(
                              cache_timerange if caching else timerange,
                              name=("VEVENT", "VFREEBUSY", "VAVAILABILITY"),
                          ),
                          name="VCALENDAR",
                       )
                  )
        filter = calendarqueryfilter.Filter(filter)
        tzinfo = filter.settimezone(tz)

        try:
            resources = yield calresource._index.indexedSearch(filter, useruid=useruid, fbtype=True)
            if caching:
                yield FBCacheEntry.makeCacheEntry(calresource, useruid, cache_timerange, resources)
        except IndexedSearchException:
            resources = yield calresource._index.bruteForceSearch()

    else:
        # Log extended item
        if logItems is not None:
            logItems["fb-cached"] = logItems.get("fb-cached", 0) + 1

        # Determine appropriate timezone (UTC is the default)
        tzinfo = tz.gettimezone() if tz is not None else PyCalendarTimezone(utc=True)

    # We care about separate instances for VEVENTs only
    aggregated_resources = {}
    for name, uid, type, test_organizer, float, start, end, fbtype, transp in resources:
        if transp == 'T' and fbtype != '?':
            fbtype = 'F'
        aggregated_resources.setdefault((name, uid, type, test_organizer,), []).append((float, start, end, fbtype,))

    for key in aggregated_resources.iterkeys():

        name, uid, type, test_organizer = key

        # Short-cut - if an fbtype exists we can use that
        if type == "VEVENT" and aggregated_resources[key][0][3] != '?':

            matchedResource = False

            # Look at each instance
            for float, start, end, fbtype in aggregated_resources[key]:
                # Ignore free time or unknown
                if fbtype in ('F', '?'):
                    continue

                # Ignore ones of this UID
                if excludeuid:
                    # See if we have a UID match
                    if (excludeuid == uid):
                        test_record = calresource.directoryService().recordWithCalendarUserAddress(test_organizer) if test_organizer else None
                        test_uid = test_record.uid if test_record else ""

                        # Check that ORGANIZER's match (security requirement)
                        if (organizer is None) or (organizer_uid == test_uid):
                            continue
                        # Check for no ORGANIZER and check by same calendar user
                        elif (test_uid == "") and same_calendar_user:
                            continue

                # Apply a timezone to any floating times
                fbstart = parseSQLTimestampToPyCalendar(start)
                if float == 'Y':
                    fbstart.setTimezone(tzinfo)
                else:
                    fbstart.setTimezone(PyCalendarTimezone(utc=True))
                fbend = parseSQLTimestampToPyCalendar(end)
                if float == 'Y':
                    fbend.setTimezone(tzinfo)
                else:
                    fbend.setTimezone(PyCalendarTimezone(utc=True))

                # Clip instance to time range
                clipped = clipPeriod(PyCalendarPeriod(fbstart, duration=fbend - fbstart), PyCalendarPeriod(timerange.start, timerange.end))

                # Double check for overlap
                if clipped:
                    matchedResource = True
                    fbinfo[fbtype_index_mapper.get(fbtype, 0)].append(clipped)

            if matchedResource:
                # Check size of results is within limit
                matchtotal += 1
                if matchtotal > config.MaxQueryWithDataResults:
                    raise QueryMaxResources(config.MaxQueryWithDataResults, matchtotal)

                # Add extended details
                if do_event_details:
                    child = (yield calresource.calendarObjectWithName(name))
                    calendar = (yield child.componentForUser())
                    _addEventDetails(calendar, event_details, rich_options, timerange, tzinfo)

        else:
            child = (yield calresource.calendarObjectWithName(name))
            calendar = (yield child.componentForUser())

            # The calendar may come back as None if the resource is being changed, or was deleted
            # between our initial index query and getting here. For now we will ignore this error, but in
            # the longer term we need to implement some form of locking, perhaps.
            if calendar is None:
                log.error("Calendar %s is missing from calendar collection %r" % (name, calresource))
                continue

            # Ignore ones of this UID
            if excludeuid:
                # See if we have a UID match
                if (excludeuid == uid):
                    test_organizer = calendar.getOrganizer()
                    test_record = calresource.principalForCalendarUserAddress(test_organizer) if test_organizer else None
                    test_uid = test_record.principalUID() if test_record else ""

                    # Check that ORGANIZER's match (security requirement)
                    if (organizer is None) or (organizer_uid == test_uid):
                        continue
                    # Check for no ORGANIZER and check by same calendar user
                    elif (test_organizer is None) and same_calendar_user:
                        continue

            if filter.match(calendar, None):
                # Check size of results is within limit
                matchtotal += 1
                if matchtotal > config.MaxQueryWithDataResults:
                    raise QueryMaxResources(config.MaxQueryWithDataResults, matchtotal)

                if calendar.mainType() == "VEVENT":
                    processEventFreeBusy(calendar, fbinfo, timerange, tzinfo)
                elif calendar.mainType() == "VFREEBUSY":
                    processFreeBusyFreeBusy(calendar, fbinfo, timerange)
                elif calendar.mainType() == "VAVAILABILITY":
                    processAvailabilityFreeBusy(calendar, fbinfo, timerange)
                else:
                    assert "Free-busy query returned unwanted component: %s in %r", (name, calresource,)

                # Add extended details
                if calendar.mainType() == "VEVENT" and do_event_details:
                    child = (yield calresource.calendarObjectWithName(name))
                    calendar = (yield child.componentForUser())
                    _addEventDetails(calendar, event_details, rich_options, timerange, tzinfo)

    returnValue(matchtotal)



def _addEventDetails(calendar, event_details, rich_options, timerange, tzinfo):
    """
    Expand events within the specified time range and limit the set of properties to those allowed for
    delegate extended free busy.

    @param calendar: the calendar object to expand
    @type calendar: L{Component}
    @param event_details: list to append VEVENT components to
    @type event_details: C{list}
    @param timerange: the time-range in which to expand
    @type timerange: L{TimeRange}
    @param tzinfo: timezone for floating time calculations
    @type tzinfo: L{PyCalendarTimezone}
    """

    # First expand the component
    expanded = calendar.expand(timerange.start, timerange.end, timezone=tzinfo)

    keep_props = (
        "UID",
        "RECURRENCE-ID",
        "DTSTAMP",
        "DTSTART",
        "DTEND",
        "DURATION",
    )

    if rich_options["organizer"] or rich_options["delegate"]:
        keep_props += ("SUMMARY",)

    if rich_options["organizer"] or rich_options["resource"]:
        keep_props += ("ORGANIZER",)

    # Remove all but essential properties
    expanded.filterProperties(keep=keep_props)

    # Need to remove all child components of VEVENT
    for subcomponent in expanded.subcomponents():
        if subcomponent.name() == "VEVENT":
            for sub in tuple(subcomponent.subcomponents()):
                subcomponent.removeComponent(sub)

    event_details.extend([subcomponent for subcomponent in expanded.subcomponents() if subcomponent.name() == "VEVENT"])



def processEventFreeBusy(calendar, fbinfo, timerange, tzinfo):
    """
    Extract free busy data from a VEVENT component.
    @param calendar: the L{Component} that is the VCALENDAR containing the VEVENT's.
    @param fbinfo: the tuple used to store the three types of fb data.
    @param timerange: the time range to restrict free busy data to.
    @param tzinfo: the L{PyCalendarTimezone} for the timezone to use for floating/all-day events.
    """

    # Expand out the set of instances for the event with in the required range
    instances = calendar.expandTimeRanges(timerange.end, lowerLimit=timerange.start, ignoreInvalidInstances=True)

    # Can only do timed events
    for key in instances:
        instance = instances[key]
        if instance.start.isDateOnly():
            return
        break
    else:
        return

    for key in instances:
        instance = instances[key]

        # Apply a timezone to any floating times
        fbstart = instance.start
        if fbstart.floating():
            fbstart.setTimezone(tzinfo)
        fbend = instance.end
        if fbend.floating():
            fbend.setTimezone(tzinfo)

        # Check TRANSP property of underlying component
        if instance.component.hasProperty("TRANSP"):
            # If its TRANSPARENT we always ignore it
            if instance.component.propertyValue("TRANSP") == "TRANSPARENT":
                continue

        # Determine status
        if instance.component.hasProperty("STATUS"):
            status = instance.component.propertyValue("STATUS")
        else:
            status = "CONFIRMED"

        # Ignore cancelled
        if status == "CANCELLED":
            continue

        # Clip period for this instance - use duration for period end if that
        # is what original component used
        if instance.component.hasProperty("DURATION"):
            period = PyCalendarPeriod(fbstart, duration=fbend - fbstart)
        else:
            period = PyCalendarPeriod(fbstart, fbend)
        clipped = clipPeriod(period, PyCalendarPeriod(timerange.start, timerange.end))

        # Double check for overlap
        if clipped:
            if status == "TENTATIVE":
                fbinfo[1].append(clipped)
            else:
                fbinfo[0].append(clipped)



def processFreeBusyFreeBusy(calendar, fbinfo, timerange):
    """
    Extract FREEBUSY data from a VFREEBUSY component.
    @param calendar: the L{Component} that is the VCALENDAR containing the VFREEBUSY's.
    @param fbinfo: the tuple used to store the three types of fb data.
    @param timerange: the time range to restrict free busy data to.
    """

    for vfb in [x for x in calendar.subcomponents() if x.name() == "VFREEBUSY"]:
        # First check any start/end in the actual component
        start = vfb.getStartDateUTC()
        end = vfb.getEndDateUTC()
        if start and end:
            if not timeRangesOverlap(start, end, timerange.start, timerange.end):
                continue

        # Now look at each FREEBUSY property
        for fb in vfb.properties("FREEBUSY"):
            # Check the type
            fbtype = fb.parameterValue("FBTYPE", default="BUSY")
            if fbtype == "FREE":
                continue

            # Look at each period in the property
            assert isinstance(fb.value(), list), "FREEBUSY property does not contain a list of values: %r" % (fb,)
            for period in fb.value():
                # Clip period for this instance
                clipped = clipPeriod(period.getValue(), PyCalendarPeriod(timerange.start, timerange.end))
                if clipped:
                    fbinfo[fbtype_mapper.get(fbtype, 0)].append(clipped)



def processAvailabilityFreeBusy(calendar, fbinfo, timerange):
    """
    Extract free-busy data from a VAVAILABILITY component.
    @param calendar: the L{Component} that is the VCALENDAR containing the VAVAILABILITY's.
    @param fbinfo: the tuple used to store the three types of fb data.
    @param timerange: the time range to restrict free busy data to.
    """

    for vav in [x for x in calendar.subcomponents() if x.name() == "VAVAILABILITY"]:

        # Get overall start/end
        start = vav.getStartDateUTC()
        if start is None:
            start = PyCalendarDateTime(1900, 1, 1, 0, 0, 0, tzid=PyCalendarTimezone(utc=True))
        end = vav.getEndDateUTC()
        if end is None:
            end = PyCalendarDateTime(2100, 1, 1, 0, 0, 0, tzid=PyCalendarTimezone(utc=True))
        period = PyCalendarPeriod(start, end)
        overall = clipPeriod(period, PyCalendarPeriod(timerange.start, timerange.end))
        if overall is None:
            continue

        # Now get periods for each instance of AVAILABLE sub-components
        periods = processAvailablePeriods(vav, timerange)

        # Now invert the periods and store in accumulator
        busyperiods = []
        last_end = timerange.start
        for period in periods:
            if last_end < period.getStart():
                busyperiods.append(PyCalendarPeriod(last_end, period.getStart()))
            last_end = period.getEnd()
        if last_end < timerange.end:
            busyperiods.append(PyCalendarPeriod(last_end, timerange.end))

        # Add to actual results mapped by busy type
        fbtype = vav.propertyValue("BUSYTYPE")
        if fbtype is None:
            fbtype = "BUSY-UNAVAILABLE"

        fbinfo[fbtype_mapper.get(fbtype, 2)].extend(busyperiods)



def processAvailablePeriods(calendar, timerange):
    """
    Extract instance period data from an AVAILABLE component.
    @param calendar: the L{Component} that is the VAVAILABILITY containing the AVAILABLE's.
    @param timerange: the time range to restrict free busy data to.
    """

    periods = []

    # First we need to group all AVAILABLE sub-components by UID
    uidmap = {}
    for component in calendar.subcomponents():
        if component.name() == "AVAILABLE":
            uid = component.propertyValue("UID")
            uidmap.setdefault(uid, []).append(component)

    # Then we expand each uid set separately
    for componentSet in uidmap.itervalues():
        instances = InstanceList(ignoreInvalidInstances=True)
        instances.expandTimeRanges(componentSet, timerange.end)

        # Now convert instances into period list
        for key in instances:
            instance = instances[key]
            # Ignore any with floating times (which should not happen as the spec requires UTC or local
            # but we will try and be safe here).
            start = instance.start
            if start.floating():
                continue
            end = instance.end
            if end.floating():
                continue

            # Clip period for this instance - use duration for period end if that
            # is what original component used
            if instance.component.hasProperty("DURATION"):
                period = PyCalendarPeriod(start, duration=end - start)
            else:
                period = PyCalendarPeriod(start, end)
            clipped = clipPeriod(period, PyCalendarPeriod(timerange.start, timerange.end))
            if clipped:
                periods.append(clipped)

    normalizePeriodList(periods)
    return periods



def buildFreeBusyResult(fbinfo, timerange, organizer=None, attendee=None, uid=None, method=None, event_details=None):
    """
    Generate a VCALENDAR object containing a single VFREEBUSY that is the
    aggregate of the free busy info passed in.

    @param fbinfo:        the array of busy periods to use.
    @param timerange:     the L{TimeRange} for the query.
    @param organizer:     the L{Property} for the Organizer of the free busy request, or None.
    @param attendee:      the L{Property} for the Attendee responding to the free busy request, or None.
    @param uid:           the UID value from the free busy request.
    @param method:        the METHOD property value to insert.
    @param event_details: VEVENT components to add.
    @return:              the L{Component} containing the calendar data.
    """

    # Merge overlapping time ranges in each fb info section
    normalizePeriodList(fbinfo[0])
    normalizePeriodList(fbinfo[1])
    normalizePeriodList(fbinfo[2])

    # Now build a new calendar object with the free busy info we have
    fbcalendar = Component("VCALENDAR")
    fbcalendar.addProperty(Property("VERSION", "2.0"))
    fbcalendar.addProperty(Property("PRODID", iCalendarProductID))
    if method:
        fbcalendar.addProperty(Property("METHOD", method))
    fb = Component("VFREEBUSY")
    fbcalendar.addComponent(fb)
    if organizer is not None:
        fb.addProperty(organizer)
    if attendee is not None:
        fb.addProperty(attendee)
    fb.addProperty(Property("DTSTART", timerange.start))
    fb.addProperty(Property("DTEND", timerange.end))
    fb.addProperty(Property("DTSTAMP", PyCalendarDateTime.getNowUTC()))
    if len(fbinfo[0]) != 0:
        fb.addProperty(Property("FREEBUSY", fbinfo[0], {"FBTYPE": "BUSY"}))
    if len(fbinfo[1]) != 0:
        fb.addProperty(Property("FREEBUSY", fbinfo[1], {"FBTYPE": "BUSY-TENTATIVE"}))
    if len(fbinfo[2]) != 0:
        fb.addProperty(Property("FREEBUSY", fbinfo[2], {"FBTYPE": "BUSY-UNAVAILABLE"}))
    if uid is not None:
        fb.addProperty(Property("UID", uid))
    else:
        uid = str(uuid.uuid4())
        fb.addProperty(Property("UID", uid))

    if event_details:
        for vevent in event_details:
            fbcalendar.addComponent(vevent)

    return fbcalendar
