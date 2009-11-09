##
# Copyright (c) 2006-2009 Apple Inc. All rights reserved.
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

__all__ = [
    "applyToCalendarCollections",
    "responseForHref",
    "allPropertiesForResource",
    "propertyNamesForResource",
    "propertyListForResource",
    "validPropertyListCalendarDataTypeVersion",
    "generateFreeBusyInfo",
    "processEventFreeBusy",
    "processFreeBusyFreeBusy",
    "processAvailabilityFreeBusy",
    "buildFreeBusyResult",
]

import datetime
import time

try:
    from hashlib import md5
except ImportError:
    from md5 import new as md5

from vobject.icalendar import utc

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python.failure import Failure
from twisted.web2 import responsecode
from twisted.web2.dav import davxml
from twisted.web2.dav.element.base import WebDAVElement
from twisted.web2.dav.http import statusForFailure
from twisted.web2.dav.method.propfind import propertyName
from twisted.web2.dav.method.report import NumberOfMatchesWithinLimits
from twisted.web2.dav.method.report import max_number_of_matches
from twisted.web2.dav.resource import AccessDeniedError
from twisted.web2.http import HTTPError

from twistedcaldav import caldavxml
from twistedcaldav.caldavxml import caldav_namespace, CalendarData
from twistedcaldav.customxml import TwistedCalendarAccessProperty
from twistedcaldav.datafilters.calendardata import CalendarDataFilter
from twistedcaldav.datafilters.privateevents import PrivateEventFilter
from twistedcaldav.dateops import clipPeriod, normalizePeriodList, timeRangesOverlap
from twistedcaldav.ical import Component, Property, iCalendarProductID
from twistedcaldav.instance import InstanceList
from twistedcaldav.index import IndexedSearchException
from twistedcaldav.log import Logger
from twistedcaldav.query import queryfilter

log = Logger()

@inlineCallbacks
def applyToCalendarCollections(resource, request, request_uri, depth, apply, privileges):
    """
    Run an operation on all calendar collections, starting at the specified
    root, to the specified depth. This involves scanning the URI hierarchy
    down from the root. Return a MultiStatus element of all responses.
    
    @param request: the L{IRequest} for the current request.
    @param resource: the L{CalDAVFile} representing the root to start scanning
        for calendar collections.
    @param depth: the depth to do the scan.
    @param apply: the function to apply to each calendar collection located
        during the scan.
    @param privileges: the privileges that must exist on the calendar collection.
    """

    # First check the privilege on this resource
    if privileges:
        try:
            yield resource.checkPrivileges(request, privileges)
        except AccessDeniedError:
            return

    # When scanning we only go down as far as a calendar collection - not into one
    if resource.isPseudoCalendarCollection():
        resources = [(resource, request_uri)]
    elif not resource.isCollection():
        resources = [(resource, request_uri)]
    else:
        resources = []
        yield resource.findCalendarCollections(depth, request, lambda x, y: resources.append((x, y)), privileges = privileges)
         
    for calresource, uri in resources:
        result = (yield apply(calresource, uri))
        if not result:
            break

def responseForHref(request, responses, href, resource, calendar, timezone, propertiesForResource, propertyreq, isowner=True):
    """
    Create an appropriate property status response for the given resource.

    @param request: the L{IRequest} for the current request.
    @param responses: the list of responses to append the result of this method to.
    @param href: the L{HRef} element of the resource being targeted.
    @param resource: the L{CalDAVFile} for the targeted resource.
    @param calendar: the L{Component} for the calendar for the resource. This may be None
        if the calendar has not already been read in, in which case the resource
        will be used to get the calendar if needed.
    @param propertiesForResource: the method to use to get the list of properties to return.
    @param propertyreq: the L{PropertyContainer} element for the properties of interest.
    @param isowner: C{True} if the authorized principal making the request is the DAV:owner,
        C{False} otherwise.
    """

    def _defer(properties_by_status):
        propstats = []

        for status in properties_by_status:
            properties = properties_by_status[status]
            if properties:
                xml_status = davxml.Status.fromResponseCode(status)
                xml_container = davxml.PropertyContainer(*properties)
                xml_propstat = davxml.PropertyStatus(xml_container, xml_status)

                propstats.append(xml_propstat)

        if propstats:
            responses.append(davxml.PropertyStatusResponse(href, *propstats))

    d = propertiesForResource(request, propertyreq, resource, calendar, timezone, isowner)
    d.addCallback(_defer)
    return d

def allPropertiesForResource(request, prop, resource, calendar=None, timezone=None, isowner=True):
    """
    Return all (non-hidden) properties for the specified resource.
    @param request: the L{IRequest} for the current request.
    @param prop: the L{PropertyContainer} element for the properties of interest.
    @param resource: the L{CalDAVFile} for the targeted resource.
    @param calendar: the L{Component} for the calendar for the resource. This may be None
        if the calendar has not already been read in, in which case the resource
        will be used to get the calendar if needed.
    @param timezone: the L{Component} the VTIMEZONE to use for floating/all-day.
    @param isowner: C{True} if the authorized principal making the request is the DAV:owner,
        C{False} otherwise.
    @return: a map of OK and NOT FOUND property values.
    """

    def _defer(props):
        return _namedPropertiesForResource(request, props, resource, calendar, timezone, isowner)

    d = resource.listAllprop(request)
    d.addCallback(_defer)
    return d

def propertyNamesForResource(request, prop, resource, calendar=None, timezone=None, isowner=True): #@UnusedVariable
    """
    Return property names for all properties on the specified resource.
    @param request: the L{IRequest} for the current request.
    @param prop: the L{PropertyContainer} element for the properties of interest.
    @param resource: the L{CalDAVFile} for the targeted resource.
    @param calendar: the L{Component} for the calendar for the resource. This may be None
        if the calendar has not already been read in, in which case the resource
        will be used to get the calendar if needed.
    @param timezone: the L{Component} the VTIMEZONE to use for floating/all-day.
    @param isowner: C{True} if the authorized principal making the request is the DAV:owner,
        C{False} otherwise.
    @return: a map of OK and NOT FOUND property values.
    """

    def _defer(props):
        properties_by_status = {
            responsecode.OK: [propertyName(p) for p in props]
        }
        return properties_by_status
    
    d = resource.listProperties(request)
    d.addCallback(_defer)
    return d

def propertyListForResource(request, prop, resource, calendar=None, timezone=None, isowner=True):
    """
    Return the specified properties on the specified resource.
    @param request: the L{IRequest} for the current request.
    @param prop: the L{PropertyContainer} element for the properties of interest.
    @param resource: the L{CalDAVFile} for the targeted resource.
    @param calendar: the L{Component} for the calendar for the resource. This may be None
        if the calendar has not already been read in, in which case the resource
        will be used to get the calendar if needed.
    @param timezone: the L{Component} the VTIMEZONE to use for floating/all-day.
    @param isowner: C{True} if the authorized principal making the request is the DAV:owner,
        C{False} otherwise.
    @return: a map of OK and NOT FOUND property values.
    """
    
    return _namedPropertiesForResource(request, prop.children, resource, calendar, timezone, isowner)

def validPropertyListCalendarDataTypeVersion(prop):
    """
    If the supplied prop element includes a calendar-data element, verify that
    the type/version on that matches what we can handle..

    @param prop: the L{PropertyContainer} element for the properties of interest.
    @return:     a tuple: (True/False if the calendar-data element is one we can handle or not present,
                           error message).
    """
    
    result = True
    message = ""
    generate_calendar_data = False
    for property in prop.children:
        if isinstance(property, caldavxml.CalendarData):
            if not property.verifyTypeVersion([("text/calendar", "2.0")]):
                result = False
                message = "Calendar-data element type/version not supported: content-type: %s, version: %s" % (property.content_type,property.version)
            generate_calendar_data = True
            break

    return result, message, generate_calendar_data

@inlineCallbacks
def _namedPropertiesForResource(request, props, resource, calendar=None, timezone=None, isowner=True):
    """
    Return the specified properties on the specified resource.
    @param request: the L{IRequest} for the current request.
    @param props: a list of property elements or qname tuples for the properties of interest.
    @param resource: the L{CalDAVFile} for the targeted resource.
    @param calendar: the L{Component} for the calendar for the resource. This may be None
        if the calendar has not already been read in, in which case the resource
        will be used to get the calendar if needed.
    @param timezone: the L{Component} the VTIMEZONE to use for floating/all-day.
    @param isowner: C{True} if the authorized principal making the request is the DAV:owner,
        C{False} otherwise.
    @return: a map of OK and NOT FOUND property values.
    """
    properties_by_status = {
        responsecode.OK        : [],
        responsecode.NOT_FOUND : [],
    }
    
    for property in props:
        if isinstance(property, caldavxml.CalendarData):
            # Handle private events access restrictions
            try:
                access = resource.readDeadProperty(TwistedCalendarAccessProperty)
            except HTTPError:
                access = None

            if calendar is None:
                calendar = resource.iCalendarText()
            filtered = PrivateEventFilter(access, isowner).filter(calendar)
            filtered = CalendarDataFilter(property, timezone).filter(filtered)
            propvalue = CalendarData().fromCalendar(filtered)
            properties_by_status[responsecode.OK].append(propvalue)
            continue
    
        if isinstance(property, WebDAVElement):
            qname = property.qname()
        else:
            qname = property
    
        has = (yield resource.hasProperty(property, request))

        if has:
            try:
                prop = (yield resource.readProperty(qname, request))
                properties_by_status[responsecode.OK].append(prop)
            except HTTPError:
                f = Failure()
    
                status = statusForFailure(f, "getting property: %s" % (qname,))
                if status != responsecode.NOT_FOUND:
                    log.err("Error reading property %r for resource %s: %s" % (qname, request.uri, f.value))
                if status not in properties_by_status: properties_by_status[status] = []
                properties_by_status[status].append(propertyName(qname))
        else:
            properties_by_status[responsecode.NOT_FOUND].append(propertyName(qname))
    
    returnValue(properties_by_status)

fbtype_mapper = {"BUSY": 0, "BUSY-TENTATIVE": 1, "BUSY-UNAVAILABLE": 2}
fbtype_index_mapper = {'B': 0, 'T': 1, 'U': 2}

@inlineCallbacks
def generateFreeBusyInfo(request, calresource, fbinfo, timerange, matchtotal,
                         excludeuid=None, organizer=None, same_calendar_user=False,
                         servertoserver=False):
    """
    Run a free busy report on the specified calendar collection
    accumulating the free busy info for later processing.
    @param request:     the L{IRequest} for the current request.
    @param calresource: the L{CalDAVFile} for a calendar collection.
    @param fbinfo:      the array of busy periods to update.
    @param timerange:   the L{TimeRange} for the query.
    @param matchtotal:  the running total for the number of matches.
    @param excludeuid:  a C{str} containing a UID value to exclude any components with that
        UID from contributing to free-busy.
    @param organizer:   a C{str} containing the value of the ORGANIZER property in the VFREEBUSY request.
        This is used in conjunction with the UID value to process exclusions.
    @param same_calendar_user:   a C{bool} indicating whether the calendar user requesting the free-busy information
        is the same as the calendar user being targeted.
    @param servertoserver:       a C{bool} indicating whether we are doing a local or remote lookup request.
    """
    
    # First check the privilege on this collection
    # TODO: for server-to-server we bypass this right now as we have no way to authorize external users.
    if not servertoserver:
        try:
            yield calresource.checkPrivileges(request, (caldavxml.ReadFreeBusy(),))
        except AccessDeniedError:
            returnValue(matchtotal)

    # May need organizer principal
    organizer_principal = calresource.principalForCalendarUserAddress(organizer) if organizer else None
    organizer_uid = organizer_principal.principalUID() if organizer_principal else ""

    #
    # What we do is a fake calendar-query for VEVENT/VFREEBUSYs in the specified time-range.
    # We then take those results and merge them into one VFREEBUSY component
    # with appropriate FREEBUSY properties, and return that single item as iCal data.
    #

    # Create fake filter element to match time-range
    filter =  caldavxml.Filter(
                  caldavxml.ComponentFilter(
                      caldavxml.ComponentFilter(
                          timerange,
                          name=("VEVENT", "VFREEBUSY", "VAVAILABILITY"),
                      ),
                      name="VCALENDAR",
                   )
              )
    filter = queryfilter.Filter(filter)

    # Get the timezone property from the collection, and store in the query filter
    # for use during the query itself.
    has_prop = (yield calresource.hasProperty((caldav_namespace, "calendar-timezone"), request))
    if has_prop:
        tz = (yield calresource.readProperty((caldav_namespace, "calendar-timezone"), request))
    else:
        tz = None
    tzinfo = filter.settimezone(tz)

    # Do some optimization of access control calculation by determining any inherited ACLs outside of
    # the child resource loop and supply those to the checkPrivileges on each child.
    filteredaces = (yield calresource.inheritedACEsforChildren(request))

    try:
        resources = calresource.index().indexedSearch(filter, fbtype=True)
    except IndexedSearchException:
        resources = calresource.index().bruteForceSearch()

    # We care about separate instances for VEVENTs only
    aggregated_resources = {}
    for name, uid, type, test_organizer, float, start, end, fbtype in resources:
        aggregated_resources.setdefault((name, uid, type, test_organizer,), []).append((float, start, end, fbtype,))

    for key in aggregated_resources.iterkeys():

        name, uid, type, test_organizer = key

        # Check privileges - must have at least CalDAV:read-free-busy
        child = (yield request.locateChildResource(calresource, name))

        # TODO: for server-to-server we bypass this right now as we have no way to authorize external users.
        if not servertoserver:
            try:
                yield child.checkPrivileges(request, (caldavxml.ReadFreeBusy(),), inherited_aces=filteredaces)
            except AccessDeniedError:
                continue

        # Short-cut - if an fbtype exists we can use that
        if type == "VEVENT" and aggregated_resources[key][0][3] != '?':
            
            # Look at each instance
            for float, start, end, fbtype in aggregated_resources[key]:
                # Ignore free time or unknown
                if fbtype in ('F', '?'):
                    continue
                
                # Ignore ones of this UID
                if excludeuid:
                    # See if we have a UID match
                    if (excludeuid == uid):
                        test_principal = calresource.principalForCalendarUserAddress(test_organizer) if test_organizer else None
                        test_uid = test_principal.principalUID() if test_principal else ""

                        # Check that ORGANIZER's match (security requirement)
                        if (organizer is None) or (organizer_uid == test_uid):
                            continue
                        # Check for no ORGANIZER and check by same calendar user
                        elif (test_uid == "") and same_calendar_user:
                            continue
                        
                # Apply a timezone to any floating times
                fbstart = datetime.datetime.strptime(start[:-6], "%Y-%m-%d %H:%M:%S")
                if float == 'Y':
                    fbstart = fbstart.replace(tzinfo=tzinfo)
                else:
                    fbstart = fbstart.replace(tzinfo=utc)
                fbend =datetime.datetime.strptime(end[:-6], "%Y-%m-%d %H:%M:%S")
                if float == 'Y':
                    fbend = fbend.replace(tzinfo=tzinfo)
                else:
                    fbend = fbend.replace(tzinfo=utc)
                
                # Click instance to time range
                clipped = clipPeriod((fbstart, fbend - fbstart), (timerange.start, timerange.end))

                # Double check for overlap
                if clipped:
                    fbinfo[fbtype_index_mapper.get(fbtype, 0)].append(clipped)
                
        else:
            calendar = calresource.iCalendar(name)
            
            # The calendar may come back as None if the resource is being changed, or was deleted
            # between our initial index query and getting here. For now we will ignore this error, but in
            # the longer term we need to implement some form of locking, perhaps.
            if calendar is None:
                log.err("Calendar %s is missing from calendar collection %r" % (name, calresource))
                continue
            
            # Ignore ones of this UID
            if excludeuid:
                # See if we have a UID match
                if (excludeuid == uid):
                    test_organizer = calendar.getOrganizer()
                    test_principal = calresource.principalForCalendarUserAddress(test_organizer) if test_organizer else None
                    test_uid = test_principal.principalUID() if test_principal else ""
    
                    # Check that ORGANIZER's match (security requirement)
                    if (organizer is None) or (organizer_uid == test_uid):
                        continue
                    # Check for no ORGANIZER and check by same calendar user
                    elif (test_organizer is None) and same_calendar_user:
                        continue
    
            if filter.match(calendar, None):
                # Check size of results is within limit
                matchtotal += 1
                if matchtotal > max_number_of_matches:
                    raise NumberOfMatchesWithinLimits(max_number_of_matches)
    
                if calendar.mainType() == "VEVENT":
                    processEventFreeBusy(calendar, fbinfo, timerange, tzinfo)
                    
                    # Lets also force an index rebuild for this resource so that next time we have the fbtype set
                    calresource.index().addResource(name, calendar, reCreate=True)

                elif calendar.mainType() == "VFREEBUSY":
                    processFreeBusyFreeBusy(calendar, fbinfo, timerange)
                elif calendar.mainType() == "VAVAILABILITY":
                    processAvailabilityFreeBusy(calendar, fbinfo, timerange)
                else:
                    assert "Free-busy query returned unwanted component: %s in %r", (name, calresource,)
    
    returnValue(matchtotal)

def processEventFreeBusy(calendar, fbinfo, timerange, tzinfo):
    """
    Extract free busy data from a VEVENT component.
    @param calendar: the L{Component} that is the VCALENDAR containing the VEVENT's.
    @param fbinfo: the tuple used to store the three types of fb data.
    @param timerange: the time range to restrict free busy data to.
    @param tzinfo: the L{datetime.tzinfo} for the timezone to use for floating/all-day events.
    """
    
    # Expand out the set of instances for the event with in the required range
    instances = calendar.expandTimeRanges(timerange.end, ignoreInvalidInstances=True)
    
    # Can only do timed events
    for key in instances:
        instance = instances[key]
        if not isinstance(instance.start, datetime.datetime):
            return
        break
    else:
        return
        
    for key in instances:
        instance = instances[key]

        # Apply a timezone to any floating times
        fbstart = instance.start
        if fbstart.tzinfo is None:
            fbstart = fbstart.replace(tzinfo=tzinfo)
        fbend = instance.end
        if fbend.tzinfo is None:
            fbend = fbend.replace(tzinfo=tzinfo)
        
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
            period = (fbstart, fbend - fbstart)
        else:
            period = (fbstart, fbend)
        clipped = clipPeriod(period, (timerange.start, timerange.end))
        
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
            if "FBTYPE" in fb.params():
                fbtype = fb.params()["FBTYPE"][0]
            else:
                fbtype = "BUSY"
            if fbtype == "FREE":
                continue
            
            # Look at each period in the property
            assert isinstance(fb.value(), list), "FREEBUSY property does not contain a list of values: %r" % (fb,)
            for period in fb.value():
                # Clip period for this instance
                clipped = clipPeriod(period, (timerange.start, timerange.end))
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
            start = datetime.datetime(1900, 1, 1, 0, 0, 0, tzinfo=utc)
        end = vav.getEndDateUTC()
        if end is None:
            end = datetime.datetime(3000, 1, 1, 0, 0, 0, tzinfo=utc)
        period = (start, end)
        overall = clipPeriod(period, (timerange.start, timerange.end))
        if overall is None:
            continue
        
        # Now get periods for each instance of AVAILABLE sub-components
        periods = processAvailablePeriods(vav, timerange)
        
        # Now invert the periods and store in accumulator
        busyperiods = []
        last_end = timerange.start
        for period in periods:
            if last_end < period[0]:
                busyperiods.append((last_end, period[0]))
            last_end = period[1]
        if last_end < timerange.end:
            busyperiods.append((last_end, timerange.end))

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
            if start.tzinfo is None:
                continue
            end = instance.end
            if end.tzinfo is None:
                continue

            # Clip period for this instance - use duration for period end if that
            # is what original component used
            if instance.component.hasProperty("DURATION"):
                period = (start, end - start)
            else:
                period = (start, end)
            clipped = clipPeriod(period, (timerange.start, timerange.end))
            if clipped:
                periods.append(clipped)
            
    normalizePeriodList(periods)
    return periods

def buildFreeBusyResult(fbinfo, timerange, organizer=None, attendee=None, uid=None, method=None):
    """
    Generate a VCALENDAR object containing a single VFREEBUSY that is the
    aggregate of the free busy info passed in.
    @param fbinfo:    the array of busy periods to use.
    @param timerange: the L{TimeRange} for the query.
    @param organizer: the L{Property} for the Organizer of the free busy request, or None.
    @param attendee:  the L{Property} for the Attendee responding to the free busy request, or None.
    @param uid:       the UID value from the free busy request.
    @param method:    the METHOD property value to insert.
    @return:          the L{Component} containing the calendar data.
    """
    
    # Merge overlapping time ranges in each fb info section
    normalizePeriodList(fbinfo[0])
    normalizePeriodList(fbinfo[1])
    normalizePeriodList(fbinfo[2])
    
    # Now build a new calendar object with the free busy info we have
    fbcalendar = Component("VCALENDAR")
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
    fb.addProperty(Property("DTSTAMP", datetime.datetime.now(tz=utc)))
    if len(fbinfo[0]) != 0:
        fb.addProperty(Property("FREEBUSY", fbinfo[0], {"FBTYPE": ["BUSY"]}))
    if len(fbinfo[1]) != 0:
        fb.addProperty(Property("FREEBUSY", fbinfo[1], {"FBTYPE": ["BUSY-TENTATIVE"]}))
    if len(fbinfo[2]) != 0:
        fb.addProperty(Property("FREEBUSY", fbinfo[2], {"FBTYPE": ["BUSY-UNAVAILABLE"]}))
    if uid is not None:
        fb.addProperty(Property("UID", uid))
    else:
        uid = md5(str(fbcalendar) + str(time.time())).hexdigest()
        fb.addProperty(Property("UID", uid))

    return fbcalendar
