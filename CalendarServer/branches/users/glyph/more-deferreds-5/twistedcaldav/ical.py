##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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

"""
iCalendar Utilities
"""

__all__ = [
    "iCalendarProductID",
    "allowedComponents",
    "Property",
    "Component",
    "FixedOffset",
    "parse_date",
    "parse_time",
    "parse_datetime",
    "parse_date_or_datetime",
    "parse_duration",
    "tzexpand",
]

from twisted.web2.dav.util import allDataFromStream
from twisted.web2.stream import IStream
from twisted.internet.defer import inlineCallbacks

from twistedcaldav.dateops import compareDateTime, normalizeToUTC, timeRangesOverlap,\
    normalizeStartEndDuration, toString, normalizeForIndex, differenceDateTime
from twistedcaldav.instance import InstanceList
from twistedcaldav.log import Logger
from twistedcaldav.scheduling.cuaddress import normalizeCUAddr

from vobject import newFromBehavior, readComponents
from vobject.base import Component as vComponent, ContentLine as vContentLine, ParseError as vParseError
from vobject.icalendar import TimezoneComponent, dateTimeToString, deltaToOffset, getTransition, stringToDate, stringToDateTime, stringToDurations, utc

import cStringIO as StringIO
import datetime
import heapq
import itertools

log = Logger()

iCalendarProductID = "-//CALENDARSERVER.ORG//NONSGML Version 1//EN"

allowedComponents = (
    "VEVENT",
    "VTODO",
    "VTIMEZONE",
    "VJOURNAL",
    "VFREEBUSY",
    #"VAVAILABILITY",
)

# 2445 default values and parameters
# Structure: propname: (<default value>, <parameter defaults dict>)

normalizeProps = {
    "CALSCALE":     ("GREGORIAN", {"VALUE": "TEXT"}),
    "METHOD":       (None, {"VALUE": "TEXT"}),
    "PRODID":       (None, {"VALUE": "TEXT"}),
    "VERSION":      (None, {"VALUE": "TEXT"}),
    "ATTACH":       (None, {"VALUE": "URI"}),
    "CATEGORIES":   (None, {"VALUE": "TEXT"}),
    "CLASS":        (None, {"VALUE": "TEXT"}),
    "COMMENT":      (None, {"VALUE": "TEXT"}),
    "DESCRIPTION":  (None, {"VALUE": "TEXT"}),
    "GEO":          (None, {"VALUE": "FLOAT"}),
    "LOCATION":     (None, {"VALUE": "TEXT"}),
    "PERCENT-COMPLETE": (None, {"VALUE": "INTEGER"}),
    "PRIORITY":     ("0", {"VALUE": "INTEGER"}),
    "RESOURCES":    (None, {"VALUE": "TEXT"}),
    "STATUS":       (None, {"VALUE": "TEXT"}),
    "SUMMARY":      (None, {"VALUE": "TEXT"}),
    "COMPLETED":    (None, {"VALUE": "DATE-TIME"}),
    "DTEND":        (None, {"VALUE": "DATE-TIME"}),
    "DUE":          (None, {"VALUE": "DATE-TIME"}),
    "DTSTART":      (None, {"VALUE": "DATE-TIME"}),
    "DURATION":     (None, {"VALUE": "DURATION"}),
    "FREEBUSY":     (None, {"VALUE": "PERIOD"}),
    "TRANSP":       ("OPAQUE", {"VALUE": "TEXT"}),
    "TZID":         (None, {"VALUE": "TEXT"}),
    "TZNAME":       (None, {"VALUE": "TEXT"}),
    "TZOFFSETFROM": (None, {"VALUE": "UTC-OFFSET"}),
    "TZOFFSETTO":   (None, {"VALUE": "UTC-OFFSET"}),
    "TZURL":        (None, {"VALUE": "URI"}),
    "ATTENDEE":     (None, {
        "VALUE":          "CAL-ADDRESS",
        "CUTYPE":         "INDIVIDUAL",
        "ROLE":           "REQ-PARTICIPANT",
        "PARTSTAT":       "NEEDS-ACTION",
        "RSVP":           "FALSE",
        "SCHEDULE-AGENT": "SERVER",
    }),
    "CONTACT":      (None, {"VALUE": "TEXT"}),
    "ORGANIZER":    (None, {"VALUE": "CAL-ADDRESS"}),
    "RECURRENCE-ID": (None, {"VALUE": "DATE-TIME"}),
    "RELATED-TO":   (None, {"VALUE": "TEXT"}),
    "URL":          (None, {"VALUE": "URI"}),
    "UID":          (None, {"VALUE": "TEXT"}),
    "EXDATE":       (None, {"VALUE": "DATE-TIME"}),
    "EXRULE":       (None, {"VALUE": "RECUR"}),
    "RDATE":        (None, {"VALUE": "DATE-TIME"}),
    "RRULE":        (None, {"VALUE": "RECUR"}),
    "ACTION":       (None, {"VALUE": "TEXT"}),
    "REPEAT":       ("0", {"VALUE": "INTEGER"}),
    "TRIGGER":      (None, {"VALUE": "DURATION"}),
    "CREATED":      (None, {"VALUE": "DATE-TIME"}),
    "DTSTAMP":      (None, {"VALUE": "DATE-TIME"}),
    "LAST-MODIFIED": (None, {"VALUE": "DATE-TIME"}),
    "SEQUENCE":     ("0", {"VALUE": "INTEGER"}),
    "REQUEST-STATUS": (None, {"VALUE": "TEXT"}),
}

# transformations to apply to property values
normalizePropsValue = {
    "ATTENDEE":     normalizeCUAddr,
    "ORGANIZER":    normalizeCUAddr,
}

class Property (object):
    """
    iCalendar Property
    """
    def __init__(self, name, value, params={}, **kwargs):
        """
        @param name: the property's name
        @param value: the property's value
        @param params: a dictionary of parameters, where keys are parameter names and
            values are (possibly empty) lists of parameter values.
        """
        if name is None:
            assert value  is None
            assert params is None

            vobj = kwargs["vobject"]

            if not isinstance(vobj, vContentLine):
                raise TypeError("Not a vContentLine: %r" % (property,))

            self._vobject = vobj
        else:
            # Convert params dictionary to list of lists format used by vobject
            lparams = [[key] + lvalue for key, lvalue in params.items()]
            self._vobject = vContentLine(name, lparams, value, isNative=True)

    def __str__ (self): return self._vobject.serialize()
    def __repr__(self): return "<%s: %r: %r>" % (self.__class__.__name__, self.name(), self.value())

    def __hash__(self):
        return hash(str(self))

    def __ne__(self, other): return not self.__eq__(other)
    def __eq__(self, other):
        if not isinstance(other, Property): return False
        return self.name() == other.name() and self.value() == other.value() and self.params() == other.params()

    def __gt__(self, other): return not (self.__eq__(other) or self.__lt__(other))
    def __lt__(self, other):
        my_name = self.name()
        other_name = other.name()

        if my_name < other_name: return True
        if my_name > other_name: return False

        return self.value() < other.value()

    def __ge__(self, other): return self.__eq__(other) or self.__gt__(other)
    def __le__(self, other): return self.__eq__(other) or self.__lt__(other)

    def name  (self): return self._vobject.name

    def value (self): return self._vobject.value
    def setValue(self, value):
        self._vobject.value = value

    def params(self):
        """
        Returns a mapping object containing parameters for this property.

        Keys are parameter names, values are sequences containing
        values for the named parameter.
        """
        return self._vobject.params

    def paramValue(self, name):
        """
        Returns a single value for the given parameter.  Raises
        ValueError if the parameter has more than one value.
        """
        values = self._vobject.params.get(name, [None,])
        assert type(values) is list, "vobject returned non-list value for parameter %r in property %r" % (name, self)
        if len(values) != 1:
            raise ValueError("Not exactly one %s value in property %r" % (name, self))
        return values[0]

    def containsTimeRange(self, start, end, tzinfo=None):
        """
        Determines whether this property contains a date/date-time within the specified
        start/end period.
        The only properties allowed for this query are: COMPLETED, CREATED, DTSTAMP and
        LAST-MODIFIED (caldav -09).
        @param start: a L{datetime.datetime} or L{datetime.date} specifying the
            beginning of the given time span.
        @param end: a L{datetime.datetime} or L{datetime.date} specifying the
            end of the given time span.  C{end} may be None, indicating that
            there is no end date.
        @param tzinfo: the default L{datetime.tzinfo} to use in datetime comparisons.
        @return: True if the property's date/date-time value is within the given time range,
                 False if not, or the property is not an appropriate date/date-time value.
        """

        # Verify that property name matches the ones allowed
        allowedNames = ["COMPLETED", "CREATED", "DTSTAMP", "LAST-MODIFIED"]
        if self.name() not in allowedNames:
            return False
        
        # get date/date-time value
        dt = self.value()
        assert isinstance(dt, datetime.date), "Not a date/date-time value: %r" % (self,)
        
        return timeRangesOverlap(dt, None, start, end, tzinfo)

    def transformAllFromNative(self):
        transformed = self._vobject.isNative
        if transformed:
            self._vobject = self._vobject.transformFromNative()
            self._vobject.transformChildrenFromNative()
        return transformed
        
    def transformAllToNative(self):
        transformed = not self._vobject.isNative
        if transformed:
            self._vobject = self._vobject.transformToNative()
            self._vobject.transformChildrenToNative()
        return transformed

class Component (object):
    """
    X{iCalendar} component.
    """

    # Private Event access levels.
    ACCESS_PROPERTY     = "X-CALENDARSERVER-ACCESS"
    ACCESS_PUBLIC       = "PUBLIC"
    ACCESS_PRIVATE      = "PRIVATE"
    ACCESS_CONFIDENTIAL = "CONFIDENTIAL"
    ACCESS_RESTRICTED   = "RESTRICTED"

    accessMap = {
        "PUBLIC"       : ACCESS_PUBLIC,
        "PRIVATE"      : ACCESS_PRIVATE,
        "CONFIDENTIAL" : ACCESS_CONFIDENTIAL,
        "RESTRICTED"   : ACCESS_RESTRICTED,
    }

    confidentialPropertiesMap = {
        "VCALENDAR": ("PRODID", "VERSION", "CALSCALE", ACCESS_PROPERTY),
        "VEVENT":    ("UID", "RECURRENCE-ID", "SEQUENCE", "DTSTAMP", "STATUS", "TRANSP", "DTSTART", "DTEND", "DURATION", "RRULE", "RDATE", "EXRULE", "EXDATE", ),
        "VTODO":     ("UID", "RECURRENCE-ID", "SEQUENCE", "DTSTAMP", "STATUS", "DTSTART", "COMPLETED", "DUE", "DURATION", "RRULE", "RDATE", "EXRULE", "EXDATE", ),
        "VJOURNAL":  ("UID", "RECURRENCE-ID", "SEQUENCE", "DTSTAMP", "STATUS", "DTSTART", "RRULE", "RDATE", "EXRULE", "EXDATE", ),
        "VFREEBUSY": ("UID", "DTSTAMP", "DTSTART", "DTEND", "DURATION", "FREEBUSY", ),
        "VTIMEZONE": None,
    }
    extraRestrictedProperties = ("SUMMARY", "LOCATION",)

    @classmethod
    def fromString(clazz, string):
        """
        Construct a L{Component} from a string.
        @param string: a string containing iCalendar data.
        @return: a L{Component} representing the first component described by
            C{string}.
        """
        if type(string) is unicode:
            string = string.encode("utf-8")
        return clazz.fromStream(StringIO.StringIO(string))

    @classmethod
    def fromStream(clazz, stream):
        """
        Construct a L{Component} from a stream.
        @param stream: a C{read()}able stream containing iCalendar data.
        @return: a L{Component} representing the first component described by
            C{stream}.
        """
        try:
            return clazz(None, vobject=readComponents(stream, findBegin=False).next())
        except UnicodeDecodeError, e:
            stream.seek(0)
            raise ValueError("%s: %s" % (e, stream.read()))
        except vParseError, e:
            raise ValueError(e)
        except StopIteration, e:
            raise ValueError(e)

    @classmethod
    def fromIStream(clazz, stream):
        """
        Construct a L{Component} from a stream.
        @param stream: an L{IStream} containing iCalendar data.
        @return: a deferred returning a L{Component} representing the first
            component described by C{stream}.
        """
        #
        # FIXME:
        #   This reads the request body into a string and then parses it.
        #   A better solution would parse directly and incrementally from the
        #   request stream.
        #
        def parse(data): return clazz.fromString(data)
        return allDataFromStream(IStream(stream), parse)

    def __init__(self, name, **kwargs):
        """
        Use this constructor to initialize an empty L{Component}.
        To create a new L{Component} from X{iCalendar} data, don't use this
        constructor directly.  Use one of the factory methods instead.
        @param name: the name (L{str}) of the X{iCalendar} component type for the
            component.
        """
        if name is None:
            if "vobject" in kwargs:
                vobj = kwargs["vobject"]

                if vobj is not None:
                    if not isinstance(vobj, vComponent):
                        raise TypeError("Not a vComponent: %r" % (vobj,))

                self._vobject = vobj
            else:
                raise AssertionError("name may not be None")

            # FIXME: _parent is not use internally, and appears to be used elsewhere,
            # even though it's names as a private variable.
            if "parent" in kwargs:
                parent = kwargs["parent"]
                
                if parent is not None:
                    if not isinstance(parent, Component):
                        raise TypeError("Not a Component: %r" % (parent,))
                    
                self._parent = parent
            else:
                self._parent = None
        else:
            self._vobject = newFromBehavior(name)
            self._parent = None

    def __str__ (self): return self._vobject.serialize()
    def __repr__(self): return "<%s: %r>" % (self.__class__.__name__, str(self._vobject))

    def __hash__(self):
        return hash(str(self))

    def __ne__(self, other): return not self.__eq__(other)
    def __eq__(self, other):
        if not isinstance(other, Component):
            return False

        my_properties = set(self.properties())
        for property in other.properties():
            if property in my_properties:
                my_properties.remove(property)
            else:
                return False
        if my_properties:
            return False

        my_subcomponents = set(self.subcomponents())
        for subcomponent in other.subcomponents():
            for testcomponent in my_subcomponents:
                if subcomponent == testcomponent:
                    my_subcomponents.remove(testcomponent)
                    break
            else:
                return False
        if my_subcomponents:
            return False

        return True

    # FIXME: Should this not be in __eq__?
    def same(self, other):
        return self._vobject == other._vobject
    
    def name(self):
        """
        @return: the name of the iCalendar type of this component.
        """
        return self._vobject.name

    def setBehavior(self, behavior):
        """
        Set the behavior of the underlying iCal object.
        @param behavior: the behavior type to set.
        """
        self._vobject.setBehavior(behavior)

    def mainType(self):
        """
        Determine the primary type of iCal component in this calendar.
        @return: the name of the primary type.
        @raise: L{ValueError} if there is more than one primary type.
        """
        assert self.name() == "VCALENDAR", "Must be a VCALENDAR: %r" % (self,)
        
        mtype = None
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            elif mtype and (mtype != component.name()):
                raise ValueError("Component contains more than one type of primary type: %r" % (self,))
            else:
                mtype = component.name()
        
        return mtype
    
    def mainComponent(self, allow_multiple=False):
        """
        Return the primary iCal component in this calendar.
        @return: the L{Component} of the primary type.
        @raise: L{ValueError} if there is more than one primary type.
        """
        assert self.name() == "VCALENDAR", "Must be a VCALENDAR: %r" % (self,)
        
        result = None
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            elif not allow_multiple and (result is not None):
                raise ValueError("Calendar contains more than one primary component: %r" % (self,))
            else:
                result = component
                if allow_multiple:
                    break
        
        return result
    
    def masterComponent(self):
        """
        Return the master iCal component in this calendar.
        @return: the L{Component} for the master component,
            or C{None} if there isn't one.
        """
        assert self.name() == "VCALENDAR", "Must be a VCALENDAR: %r" % (self,)
        
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            if not component.hasProperty("RECURRENCE-ID"):
                return component
        
        return None
    
    def overriddenComponent(self, recurrence_id):
        """
        Return the overridden iCal component in this calendar matching the supplied RECURRENCE-ID property.

        @param recurrence_id: The RECURRENCE-ID property value to match.
        @type recurrence_id: L{datetime.datetime} or L{datetime.date}
        @return: the L{Component} for the overridden component,
            or C{None} if there isn't one.
        """
        assert self.name() == "VCALENDAR", "Must be a VCALENDAR: %r" % (self,)
        
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            rid = component.getRecurrenceIDUTC()
            if rid and recurrence_id and compareDateTime(rid, recurrence_id) == 0:
                return component
            elif rid is None and recurrence_id is None:
                return component
        
        return None
    
    def accessLevel(self, default=ACCESS_PUBLIC):
        """
        Return the access level for this component.
        @return: the access level for the calendar data.
        """
        assert self.name() == "VCALENDAR", "Must be a VCALENDAR: %r" % (self,)
        
        access = self.propertyValue(Component.ACCESS_PROPERTY)
        if access:
            access = access.upper()
        return Component.accessMap.get(access, default)
    
    def duplicate(self):
        """
        Duplicate this object and all its contents.
        @return: the duplicated calendar.
        """
        return Component(None, vobject=vComponent.duplicate(self._vobject))
        
    def subcomponents(self):
        """
        @return: an iterable of L{Component} objects, one for each subcomponent
            of this component.
        """
        return (
            Component(None, vobject=c, parent=self)
            for c in self._vobject.getChildren()
            if isinstance(c, vComponent)
        )

    def addComponent(self, component):
        """
        Adds a subcomponent to this component.
        @param component: the L{Component} to add as a subcomponent of this
            component.
        """
        self._vobject.add(component._vobject)
        component._parent = self

    def removeComponent(self, component):
        """
        Removes a subcomponent from this component.
        @param component: the L{Component} to remove.
        """
        self._vobject.remove(component._vobject)

    def hasProperty(self, name):
        """
        @param name: the name of the property whose existence is being tested.
        @return: True if the named property exists, False otherwise.
        """
        try:
            return len(self._vobject.contents[name.lower()]) > 0
        except KeyError:
            return False

    def getProperty(self, name):
        """
        Get one property from the property list.
        @param name: the name of the property to get.
        @return: the L{Property} found or None.
        @raise: L{ValueError} if there is more than one property of the given name.
        """
        properties = tuple(self.properties(name))
        if len(properties) == 1: return properties[0]
        if len(properties) > 1: raise ValueError("More than one %s property in component %r" % (name, self))
        return None
        
    def properties(self, name=None):
        """
        @param name: if given and not C{None}, restricts the returned properties
            to those with the given C{name}.
        @return: an iterable of L{Property} objects, one for each property of
            this component.
        """
        if name is None:
            properties = self._vobject.getChildren()
        else:
            try:
                properties = self._vobject.contents[name.lower()]
            except KeyError:
                return ()

        return (
            Property(None, None, None, vobject=p)
            for p in properties
            if isinstance(p, vContentLine)
        )

    def propertyValue(self, name):
        properties = tuple(self.properties(name))
        if len(properties) == 1:
            return properties[0].value()
        if len(properties) > 1:
            raise ValueError("More than one %s property in component %r" % (name, self))
        return None


    def propertyNativeValue(self, name):
        """
        Return the native property value for the named property in the supplied component.
        NB Assumes a single property exists in the component.
        @param name: the name of the property whose value is required
        @return: the native property value
        """
        properties = tuple(self.properties(name))

        if len(properties) == 1:
            transormed = properties[0].transformAllToNative()
    
            result = properties[0].value()
    
            if transormed:
                properties[0].transformAllFromNative()
                
            return result

        elif len(properties) > 1:
            raise ValueError("More than one %s property in component %r" % (name, self))
        else:
            return None

    def getStartDateUTC(self):
        """
        Return the start date or date-time for the specified component
        converted to UTC.
        @param component: the Component whose start should be returned.
        @return: the datetime.date or datetime.datetime for the start.
        """
        dtstart = self.propertyNativeValue("DTSTART")
        if dtstart is not None:
            return normalizeToUTC(dtstart)
        else:
            return None
 
    def getEndDateUTC(self):
        """
        Return the end date or date-time for the specified component,
        taking into account the presence or absence of DTEND/DURATION properties.
        The returned date-time is converted to UTC.
        @param component: the Component whose end should be returned.
        @return: the datetime.date or datetime.datetime for the end.
        """
        dtend = self.propertyNativeValue("DTEND")
        if dtend is None:
            dtstart = self.propertyNativeValue("DTSTART")
            duration = self.propertyNativeValue("DURATION")
            if duration is not None:
                dtend = dtstart + duration

        if dtend is not None:
            return normalizeToUTC(dtend)
        else:
            return None

    def getDueDateUTC(self):
        """
        Return the due date or date-time for the specified component
        converted to UTC. Use DTSTART/DURATION if no DUE property.
        @param component: the Component whose start should be returned.
        @return: the datetime.date or datetime.datetime for the start.
        """
        due = self.propertyNativeValue("DUE")
        if due is None:
            dtstart = self.propertyNativeValue("DTSTART")
            duration = self.propertyNativeValue("DURATION")
            if dtstart is not None and duration is not None:
                due = dtstart + duration

        if due is not None:
            return normalizeToUTC(due)
        else:
            return None
 
    def getRecurrenceIDUTC(self):
        """
        Return the recurrence-id for the specified component.
        @param component: the Component whose r-id should be returned.
        @return: the datetime.date or datetime.datetime for the r-id.
        """
        rid = self.propertyNativeValue("RECURRENCE-ID")

        if rid is not None:
            return normalizeToUTC(rid)
        else:
            return None
 
    def getRange(self):
        """
        Determine whether a RANGE=THISANDFUTURE parameter is present
        on any RECURRENCE-ID property.
        @return: True if the parameter is present, False otherwise.
        """
        ridprop = self.getProperty("RECURRENCE-ID")
        if ridprop is not None:
            range = ridprop.paramValue("RANGE")
            if range is not None:
                return (range == "THISANDFUTURE")

        return False
            
    def getTriggerDetails(self):
        """
        Return the trigger information for the specified alarm component.
        @param component: the Component whose start should be returned.
        @return: ta tuple consisting of:
            trigger : the 'native' trigger value (either datetime.date or datetime.timedelta)
            related : either True (for START) or False (for END)
            repeat : an integer for the REPEAT count
            duration: the repeat duration if present, otherwise None
        """
        assert self.name() == "VALARM", "Component is not a VAlARM: %r" % (self,)
        
        # The trigger value
        trigger = self.propertyNativeValue("TRIGGER")
        if trigger is None:
            raise ValueError("VALARM has no TRIGGER property: %r" % (self,))
        
        # The related parameter
        related = self.getProperty("TRIGGER").paramValue("RELATED")
        if related is None:
            related = True
        else:
            related = (related == "START")
        
        # Repeat property
        repeat = self.propertyNativeValue("REPEAT")
        if repeat is None: repeat = 0
        else: repeat = int(repeat)
        
        # Duration property
        duration = self.propertyNativeValue("DURATION")

        if repeat > 0 and duration is None:
            raise ValueError("VALARM has invalid REPEAT/DURATIOn properties: %r" % (self,))

        return (trigger, related, repeat, duration)
 
    def getRRuleSet(self, addRDate = False):
        self.transformAllToNative()
        return self._vobject.getrruleset(addRDate)

    def setRRuleSet(self, rruleset):
        #self.transformAllToNative()
        return self._vobject.setrruleset(rruleset)

    def getEffectiveStartEnd(self):
        # Get the start/end range needed for instance comparisons

        if self.name() in ("VEVENT", "VJOURNAL",):
            return self.getStartDateUTC(), self.getEndDateUTC()
        elif self.name() == "VTODO":
            start = self.getStartDateUTC()
            due = self.getDueDateUTC()
            if start is None and due is not None:
                return due, due
            else:
                return start, due
        else:
            return None, None

    def getFBType(self):
        
        # Only VEVENTs block time
        if self.name() not in ("VEVENT", ):
            return "FREE"
        
        # If it is TRANSPARENT we always ignore it
        if self.propertyValue("TRANSP") == "TRANSPARENT":
            return "FREE"
        
        # Handle status
        status = self.propertyValue("STATUS")
        if status == "CANCELLED":
            return "FREE"
        elif status == "TENTATIVE":
            return "BUSY-TENTATIVE"
        else:
            return "BUSY"

    def addProperty(self, property):
        """
        Adds a property to this component.
        @param property: the L{Property} to add to this component.
        """
        self._vobject.add(property._vobject)

    def removeProperty(self, property):
        """
        Remove a property from this component.
        @param property: the L{Property} to remove from this component.
        """
        self._vobject.remove(property._vobject)

    def replaceProperty(self, property):
        """
        Add or replace a property in this component.
        @param property: the L{Property} to add or replace in this component.
        """
        
        # Remove all existing ones first
        for removeit in tuple(self.properties(property.name())):
            self.removeProperty(removeit)
        self.addProperty(property)

    def timezoneIDs(self):
        """
        Returns the set of TZID parameter values appearing in any property in
        this component.
        @return: a set of strings, one for each unique TZID value.
        """
        result = set()

        for property in self.properties():
            for propertyname in ("TZID", "X-VOBJ-ORIGINAL-TZID"):
                tzid = property.paramValue(propertyname)
                if tzid is not None:
                    result.add(tzid)
                    break
            else:
                items = property.value()
                if type(items) is not list:
                    items = [items]
                for item in items:
                    tzinfo = getattr(item, 'tzinfo', None)
                    tzid = TimezoneComponent.pickTzid(tzinfo)
                    if tzid is not None:
                        result.add(tzid)
        
        return result
    
    def timezones(self):
        """
        Returns the set of TZID's for each VTIMEZONE component.

        @return: a set of strings, one for each unique TZID value.
        """
        
        assert self.name() == "VCALENDAR", "Not a calendar: %r" % (self,)

        results = set()
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                results.add(component.propertyValue("TZID"))
        
        return results
    
    def truncateRecurrence(self, maximumCount):
        """
        Truncate RRULEs etc to make sure there are no more than the given number
        of instances.
 
        @param maximumCount: the maximum number of instances to allow
        @type maximumCount: C{int}
        @return: a C{bool} indicating whether a change was made or not
        """

        changed = False
        master = self.masterComponent()
        if master and master.isRecurring():
            rrules = master.getRRuleSet()
            if rrules:
                for rrule in rrules._rrule:
                    if rrule._count is not None:
                        # Make sure COUNT is less than the limit
                        if rrule._count > maximumCount:
                            rrule._count = maximumCount
                            changed = True
                    elif rrule._until is not None:
                        # Need to figure out how to determine number of instances
                        # with this UNTIL and truncate if needed
                        start = master.getStartDateUTC()
                        diff = differenceDateTime(start, rrule._until)
                        diff = diff.days * 24 * 60 * 60 + diff.seconds
                        
                        period = {
                            0: 365 * 24 * 60 * 60,
                            1:  30 * 24 * 60 * 60,
                            2:   7 * 24 * 60 * 60,
                            3:   1 * 24 * 60 * 60,
                            4:   60 * 60,
                            5:   60,
                            6:   1
                        }[rrule._freq] * rrule._interval
                        
                        if diff / period > maximumCount:
                            rrule._until = None
                            rrule._count = maximumCount
                            changed = True
                    else:
                        # For frequencies other than yearly we will truncate at our limit
                        if rrule._freq != 0:
                            rrule._count = maximumCount
                            changed = True
                
                if changed:
                    master.setRRuleSet(rrules)

        return changed

    def expand(self, start, end, timezone=None):
        """
        Expand the components into a set of new components, one for each
        instance in the specified range. Date-times are converted to UTC. A
        new calendar object is returned.
        @param start: the L{datetime.datetime} for the start of the range.
        @param end: the L{datetime.datetime} for the end of the range.
        @param timezone: the L{Component} the VTIMEZONE to use for floating/all-day.
        @return: the L{Component} for the new calendar with expanded instances.
        """
        
        tzinfo = timezone.gettzinfo() if timezone else None

        # Create new calendar object with same properties as the original, but
        # none of the originals sub-components
        calendar = Component("VCALENDAR")
        for property in calendar.properties():
            calendar.removeProperty(property)
        for property in self.properties():
            calendar.addProperty(property)
        
        # Expand the instances and add each one
        instances = self.expandTimeRanges(end)
        first = True
        for key in instances:
            instance = instances[key]
            if timeRangesOverlap(instance.start, instance.end, start, end, tzinfo):
                calendar.addComponent(self.expandComponent(instance, first))
            first = False
        
        return calendar
    
    def expandComponent(self, instance, first):
        """
        Create an expanded component based on the instance provided.
        NB Expansion also requires UTC conversions.
        @param instance: an L{Instance} for the instance being expanded.
        @return: a new L{Component} for the expanded instance.
        """
        
        # Duplicate the component from the instance
        newcomp = instance.component.duplicate()
 
        # Strip out unwanted recurrence properties
        for property in tuple(newcomp.properties()):
            if property.name() in ["RRULE", "RDATE", "EXRULE", "EXDATE", "RECURRENCE-ID"]:
                newcomp.removeProperty(property)
        
        # Convert all datetime properties to UTC unless they are floating
        for property in newcomp.properties():
            value = property.value()
            if isinstance(value, datetime.datetime) and value.tzinfo is not None:
                property.setValue(value.astimezone(utc))
        
        # Now reset DTSTART, DTEND/DURATION
        for property in newcomp.properties("DTSTART"):
            property.setValue(instance.start)
        for property in newcomp.properties("DTEND"):
            property.setValue(instance.end)
        for property in newcomp.properties("DURATION"):
            property.setValue(instance.end - instance.start)
        
        # Add RECURRENCE-ID if not first instance
        if not first:
            newcomp.addProperty(Property("RECURRENCE-ID", instance.rid))
            newcomp.transformAllToNative()

        return newcomp

    def cacheExpandedTimeRanges(self, limit):
        """
        Expand instances up to the specified limit and cache the results in this object
        so we can return cached results in the future.
 
        @param limit: the max datetime to cache up to.
        @type limit: L{datetime.datetime} or L{datetime.date}
        """
        
        # Checked for cached values first
        if hasattr(self, "cachedInstances"):
            cachedLimit = self.cachedInstances.limit
            if cachedLimit is None or cachedLimit >= limit:
                # We have already fully expanded, or cached up to the requested time,
                # so return cached instances
                return self.cachedInstances
        
        self.cachedInstances = self.expandTimeRanges(limit)
        return self.cachedInstances

    def expandTimeRanges(self, limit, ignoreInvalidInstances=False):
        """
        Expand the set of recurrence instances for the components
        contained within this VCALENDAR component. We will assume
        that this component has already been validated as a CalDAV resource
        (i.e. only one type of component, all with the same UID)
        @param limit: datetime.date value representing the end of the expansion.
        @param ignoreInvalidInstances: C{bool} whether to ignore instance errors.
        @return: a set of Instances for each recurrence in the set.
        """
        
        componentSet = self.subcomponents()
        return self.expandSetTimeRanges(componentSet, limit, ignoreInvalidInstances)
    
    def expandSetTimeRanges(self, componentSet, limit, ignoreInvalidInstances=False):
        """
        Expand the set of recurrence instances up to the specified date limit.
        What we do is first expand the master instance into the set of generate
        instances. Then we merge the overridden instances, taking into account
        THISANDFUTURE and THISANDPRIOR.
        @param limit: datetime.date value representing the end of the expansion.
        @param componentSet: the set of components that are to make up the
                recurrence set. These MUST all be components with the same UID
                and type, forming a proper recurring set.
        @return: a set of Instances for each recurrence in the set.
        """
        
        # Set of instances to return
        instances = InstanceList(ignoreInvalidInstances=ignoreInvalidInstances)
        instances.expandTimeRanges(componentSet, limit)
        return instances

    def getComponentInstances(self):
        """
        Get the R-ID value for each component.
        
        @return: a tuple of recurrence-ids
        """
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            result = ()
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    result += component.getComponentInstances()
            return result
        else:
            rid = self.getRecurrenceIDUTC()
            return (rid,)

    def isRecurring(self):
        """
        Check whether any recurrence properties are present in any component.
        """

        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE" and component.isRecurring():
                    return True
        else:
            for propname in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID",):
                if self.hasProperty(propname):
                    return True
        return False
        
    def isRecurringUnbounded(self):
        """
        Check for unbounded recurrence.
        """

        master = self.masterComponent()
        if master:
            rrules = master.properties("RRULE")
            for rrule in rrules:
                s = str(rrule)
                if "COUNT" not in s and "UNTIL" not in s:
                    return True
        return False
        
    def deriveInstance(self, rid, allowCancelled=False):
        """
        Derive an instance from the master component that has the provided RECURRENCE-ID, but
        with all other properties, components etc from the master. If the requested override is
        currently marked as an EXDATE in the existing master, allow an option whereby the override
        is added as STATUS:CANCELLED and the EXDATE removed.

        @param rid: recurrence-id value
        @type rid: L{datetime.datetime}
        @param allowCancelled: whether to allow a STATUS:CANCELLED override
        @type allowCancelled: C{bool}
        
        @return: L{Component} for newly derived instance, or None if not valid override
        """
        
        # Must have a master component
        master = self.masterComponent()
        if master is None:
            return None

        # TODO: Check that the recurrence-id is a valid instance
        # For now we just check that there is no matching EXDATE
        didCancel = False
        for exdate in tuple(master.properties("EXDATE")):
            for exdateValue in exdate.value():
                if exdateValue == rid:
                    if allowCancelled:
                        exdate.value().remove(exdateValue)
                        if len(exdate.value()) == 0:
                            master.removeProperty(exdate)
                        didCancel = True

                        # We changed the instance set so remove any instance cache
                        if hasattr(self, "cachedInstances"):
                            delattr(self, "cachedInstances")
                        break
                    else:
                        # Cannot derive from an existing EXDATE
                        return None
        
        # Check whether recurrence-id matches an RDATE - if so it is OK
        rdates = set()
        for rdate in master.properties("RDATE"):
            rdates.update([normalizeToUTC(item) for item in rdate.value()])
        if rid not in rdates:
            # Check whether we have a truncated RRULE
            rrules = master.properties("RRULE")
            if len(tuple(rrules)):
                limit = rid
                limit += datetime.timedelta(days=365)
                instances = self.cacheExpandedTimeRanges(limit)
                rids = set([instances[key].rid for key in instances])
                instance_rid = normalizeForIndex(rid)
                if instance_rid not in rids:
                    # No match to a valid RRULE instance
                    return None
            else:
                # No RRULE and no match to an RDATE => error
                return None
        
        # Create the derived instance
        newcomp = master.duplicate()

        # Strip out unwanted recurrence properties
        for property in tuple(newcomp.properties()):
            if property.name() in ("RRULE", "RDATE", "EXRULE", "EXDATE", "RECURRENCE-ID",):
                newcomp.removeProperty(property)
        
        # New DTSTART is the RECURRENCE-ID we are deriving but adjusted to the
        # original DTSTART's localtime
        dtstart = newcomp.getProperty("DTSTART")
        if newcomp.hasProperty("DTEND"):
            dtend = newcomp.getProperty("DTEND")
            oldduration = dtend.value() - dtstart.value()
            
        if isinstance(dtstart.value(), datetime.datetime):
            if dtstart.value().tzinfo:
                newdtstartValue = rid.astimezone(dtstart.value().tzinfo)
            else:
                newdtstartValue = rid
        else:
            newdtstartValue = datetime.date.fromordinal(rid.toordinal())
            
        dtstart.setValue(newdtstartValue)
        if newcomp.hasProperty("DTEND"):
            dtend.setValue(newdtstartValue + oldduration)

        try:
            rid_params = {"X-VOBJ-ORIGINAL-TZID":dtstart.params()["X-VOBJ-ORIGINAL-TZID"]}
        except KeyError:
            rid_params = {}
        newcomp.addProperty(Property("RECURRENCE-ID", dtstart.value(), params=rid_params))
        
        if didCancel:
            newcomp.addProperty(Property("STATUS", "CANCELLED"))

        return newcomp
        
    def resourceUID(self):
        """
        @return: the UID of the subcomponents in this component.
        """
        assert self.name() == "VCALENDAR", "Not a calendar: %r" % (self,)

        if not hasattr(self, "_resource_uid"):
            for subcomponent in self.subcomponents():
                if subcomponent.name() != "VTIMEZONE":
                    self._resource_uid = subcomponent.propertyValue("UID")
                    break
            else:
                self._resource_uid = None

        return self._resource_uid

    def resourceType(self):
        """
        @return: the name of the iCalendar type of the subcomponents in this
            component.
        """
        assert self.name() == "VCALENDAR", "Not a calendar: %r" % (self,)

        if not hasattr(self, "_resource_type"):
            has_timezone = False

            for subcomponent in self.subcomponents():
                name = subcomponent.name()
                if name == "VTIMEZONE":
                    has_timezone = True
                else:
                    self._resource_type = name
                    break
            else:
                if has_timezone:
                    self._resource_type = "VTIMEZONE"
                else:
                    raise ValueError("No component type found for calendar component: %r" % (self,))

        return self._resource_type

    def validCalendarForCalDAV(self):
        """
        @raise ValueError: if the given calendar data is not valid.
        """
        if self.name() != "VCALENDAR":
            log.debug("Not a calendar: %s" % (self,))
            raise ValueError("Not a calendar")
        if not self.resourceType():
            log.debug("Unknown resource type: %s" % (self,))
            raise ValueError("Unknown resource type")

        version = self.propertyValue("VERSION")
        if version != "2.0":
            msg = "Not a version 2.0 iCalendar (version=%s)" % (version,)
            log.debug(msg)
            raise ValueError(msg)

    def validateForCalDAV(self):
        """
        @raise ValueError: if the given calendar component is not valid for
            use as a X{CalDAV} resource.
        """
        self.validCalendarForCalDAV()

        # Disallowed in CalDAV-Access-08, section 4.1
        if self.hasProperty("METHOD"):
            msg = "METHOD property is not allowed in CalDAV iCalendar data"
            log.debug(msg)
            raise ValueError(msg)

        self.validateComponentsForCalDAV(False)

    def validateComponentsForCalDAV(self, method):
        """
        @param method:     True if METHOD property is allowed, False otherwise.
        @raise ValueError: if the given calendar component is not valid for
            use as a X{CalDAV} resource.
        """
        #
        # Must not contain more than one type of iCalendar component, except for
        # the required timezone components, and component UIDs must match
        #
        ctype            = None
        component_id     = None
        component_rids   = set()
        timezone_refs    = set()
        timezones        = set()
        got_master       = False
        got_override     = False
        master_recurring = False
        
        for subcomponent in self.subcomponents():
            # Disallowed in CalDAV-Access-08, section 4.1
            if not method and subcomponent.hasProperty("METHOD"):
                msg = "METHOD property is not allowed in CalDAV iCalendar data"
                log.debug(msg)
                raise ValueError(msg)
        
            if subcomponent.name() == "VTIMEZONE":
                timezones.add(subcomponent.propertyValue("TZID"))
            else:
                if ctype is None:
                    ctype = subcomponent.name()
                else:
                    if ctype != subcomponent.name():
                        msg = "Calendar resources may not contain more than one type of calendar component (%s and %s found)" % (ctype, subcomponent.name())
                        log.debug(msg)
                        raise ValueError(msg)
        
                if ctype not in allowedComponents:
                    msg = "Component type: %s not allowed" % (ctype,)
                    log.debug(msg)
                    raise ValueError(msg)
                    
                uid = subcomponent.propertyValue("UID")
                if uid is None:
                    msg = "All components must have UIDs"
                    log.debug(msg)
                    raise ValueError(msg)
                rid = subcomponent.getRecurrenceIDUTC()
                
                # Verify that UIDs are the same
                if component_id is None:
                    component_id = uid
                elif component_id != uid:
                    msg = "Calendar resources may not contain components with different UIDs (%s and %s found)" % (component_id, subcomponent.propertyValue("UID"))
                    log.debug(msg)
                    raise ValueError(msg)

                # Verify that there is only one master component
                if rid is None:
                    if got_master:
                        msg = "Calendar resources may not contain components with the same UIDs and no Recurrence-IDs (%s and %s found)" % (component_id, subcomponent.propertyValue("UID"))
                        log.debug(msg)
                        raise ValueError(msg)
                    else:
                        got_master = True
                        master_recurring = subcomponent.hasProperty("RRULE") or subcomponent.hasProperty("RDATE")
                else:
                    got_override = True
                            
                # Check that if an override is present then the master is recurring
                # Leopard iCal sometimes does this for overridden instances that an Attendee receives and
                # it creates a "fake" (invalid) master. We are going to skip this test here. Instead implicit
                # scheduling with verify the validity of the components and raise if they don't make sense.
                # If no scheduling is happening then we allow this - that may cause other clients to choke.
                # If it does we will have to reinstate this check but only after we have checked for implicit.
#                if got_override and got_master and not master_recurring:
#                    msg = "Calendar resources must have a recurring master component if there is an overridden one (%s)" % (subcomponent.propertyValue("UID"),)
#                    log.debug(msg)
#                    raise ValueError(msg)
                
                # Check for duplicate RECURRENCE-IDs        
                if rid in component_rids:
                    msg = "Calendar resources may not contain components with the same Recurrence-IDs (%s)" % (rid,)
                    log.debug(msg)
                    raise ValueError(msg)
                else:
                    component_rids.add(rid)

                timezone_refs.update(subcomponent.timezoneIDs())
        
        #
        # Make sure required timezone components are present
        #
        for timezone_ref in timezone_refs:
            if timezone_ref not in timezones:
                msg = "Timezone ID %s is referenced but not defined: %s" % (timezone_ref, self,)
                log.debug(msg)
                raise ValueError(msg)
        
        #
        # FIXME:
        #   This test is not part of the spec; it appears to be legal (but
        #   goofy?) to have extra timezone components.
        #
        for timezone in timezones:
            if timezone not in timezone_refs:
                log.debug(
                    "Timezone %s is not referenced by any non-timezone component" % (timezone,)
                )

    def validOrganizerForScheduling(self):
        """
        Check that the ORGANIZER property is valid for scheduling 
        """
        
        organizers = self.getOrganizersByInstance()
        foundOrganizer = None
        foundRid = None
        missingRids = set()
        for organizer, rid in organizers:
            if organizer:
                if foundOrganizer:
                    if organizer != foundOrganizer:
                        # We have different ORGANIZERs in the same iCalendar object - this is an error
                        msg = "Only one ORGANIZER is allowed in an iCalendar object:\n%s" % (self,)
                        log.debug(msg)
                        raise ValueError(msg)
                else:
                    foundOrganizer = organizer
                    foundRid = rid
            else:
                missingRids.add(rid)
        
        # If there are some components without an ORGANIZER we will fix the data
        if foundOrganizer and missingRids:
            log.debug("Fixing missing ORGANIZER properties")
            organizerProperty = self.overriddenComponent(foundRid).getProperty("ORGANIZER")
            for rid in missingRids:
                self.overriddenComponent(rid).addProperty(organizerProperty)

        return foundOrganizer

    def transformAllFromNative(self):
        self._vobject = self._vobject.transformFromNative()
        self._vobject.transformChildrenFromNative(False)
        
    def transformAllToNative(self):
        self._vobject = self._vobject.transformToNative()
        self._vobject.transformChildrenToNative()

    def gettzinfo(self):
        """
        Get the tzinfo for a Timezone component.

        @return: L{datetime.tzinfo} if this is a VTIMEZONE, otherwise None.
        """
        if self.name() == "VTIMEZONE":
            return self._vobject.gettzinfo()
        elif self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() == "VTIMEZONE":
                    return component.gettzinfo()
            else:
                return None
        else:
            return None

    ##
    # iTIP stuff
    ##
    
    def isValidMethod(self):
        """
        Verify that this calendar component has a valid iTIP METHOD property.
        
        @return: True if valid, False if not
        """
        
        try:
            method = self.propertyValue("METHOD")
            if method not in ("PUBLISH", "REQUEST", "REPLY", "ADD", "CANCEL", "REFRESH", "COUNTER", "DECLINECOUNTER"):
                return False
        except ValueError:
            return False
        
        return True

    def isValidITIP(self):
        """
        Verify that this calendar component is valid according to iTIP.
        
        @return: True if valid, False if not
        """
        
        try:
            method = self.propertyValue("METHOD")
            if method not in ("PUBLISH", "REQUEST", "REPLY", "ADD", "CANCEL", "REFRESH", "COUNTER", "DECLINECOUNTER"):
                return False
            
            # First make sure components are all of the same time (excluding VTIMEZONE)
            self.validateComponentsForCalDAV(True)
            
            # Next we could check the iTIP status for each type of method/component pair, however
            # we can also leave that up to the server except for the REQUEST/VFREEBUSY case which
            # the server will handle itself.
            
            if (method == "REQUEST") and (self.mainType() == "VFREEBUSY"):
                # TODO: verify REQUEST/VFREEBUSY as being OK
                
                # Only one VFREEBUSY (actually multiple X-'s are allowed but we will reject)
                if len([c for c in self.subcomponents()]) != 1:
                    return False

        except ValueError:
            return False
        
        return True
    
    def getOrganizer(self):
        """
        Get the organizer value. Works on either a VCALENDAR or on a component.
        
        @return: the string value of the Organizer property, or None
        """
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    return component.getOrganizer()
        else:
            try:
                # Find the primary subcomponent
                return self.propertyValue("ORGANIZER")
            except ValueError:
                pass

        return None

    def getOrganizersByInstance(self):
        """
        Get the organizer value for each instance.
        
        @return: a list of tuples of (organizer value, recurrence-id)
        """
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            result = ()
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    result += component.getOrganizersByInstance()
            return result
        else:
            try:
                # Should be just one ORGANIZER
                org = self.propertyValue("ORGANIZER")
                rid = self.getRecurrenceIDUTC()
                return ((org, rid),)
            except ValueError:
                pass

        return ()

    def getOrganizerProperty(self):
        """
        Get the organizer value. Works on either a VCALENDAR or on a component.
        
        @return: the string value of the Organizer property, or None
        """
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    return component.getOrganizerProperty()
        else:
            try:
                # Find the primary subcomponent
                return self.getProperty("ORGANIZER")
            except ValueError:
                pass

        return None

    def getAttendees(self):
        """
        Get the attendee value. Works on either a VCALENDAR or on a component.
        
        @param match: a C{list} of calendar user address strings to try and match.
        @return: a C{list} of the string values of the Attendee property, or None
        """
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    return component.getAttendees()
        else:
            # Find the property values
            return [p.value() for p in self.properties("ATTENDEE")]

        return None

    def getAttendeesByInstance(self, makeUnique=False, onlyScheduleAgentServer=False):
        """
        Get the attendee values for each instance. Optionally remove duplicates.
        
        @param makeUnique: if C{True} remove duplicate ATTENDEEs in each component
        @type makeUnique: C{bool}
        @param onlyScheduleAgentServer: if C{True} only return ATETNDEEs with SCHEDULE-AGENT=SERVER set
        @type onlyScheduleAgentServer: C{bool}
        @return: a list of tuples of (organizer value, recurrence-id)
        """
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            result = ()
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    result += component.getAttendeesByInstance(makeUnique, onlyScheduleAgentServer)
            return result
        else:
            result = ()
            attendees = set()
            rid = self.getRecurrenceIDUTC()
            for attendee in tuple(self.properties("ATTENDEE")):
                
                if onlyScheduleAgentServer:
                    if "SCHEDULE-AGENT" in attendee.params():
                        if attendee.paramValue("SCHEDULE-AGENT") != "SERVER":
                            continue

                cuaddr = attendee.value()
                if makeUnique and cuaddr in attendees:
                    self.removeProperty(attendee)
                else:
                    result += ((cuaddr, rid),)
                    attendees.add(cuaddr)
            return result

    def getAttendeeProperty(self, match):
        """
        Get the attendees matching a value. Works on either a VCALENDAR or on a component.
        
        @param match: a C{list} of calendar user address strings to try and match.
        @return: the matching Attendee property, or None
        """
        
        # Need to normalize http/https cu addresses
        test = set()
        for item in match:
            test.add(normalizeCUAddr(item))
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    attendee = component.getAttendeeProperty(match)
                    if attendee is not None:
                        return attendee
        else:
            # Find the primary subcomponent
            for attendee in self.properties("ATTENDEE"):
                if normalizeCUAddr(attendee.value()) in test:
                    return attendee

        return None

    def getAttendeeProperties(self, match):
        """
        Get all the attendees matching a value in each component. Works on a VCALENDAR component only.
        
        @param match: a C{list} of calendar user address strings to try and match.
        @return: the string value of the Organizer property, or None
        """
        
        assert self.name() == "VCALENDAR", "Not a calendar: %r" % (self,)

        # Extract appropriate sub-component if this is a VCALENDAR
        results = []
        for component in self.subcomponents():
            if component.name() != "VTIMEZONE":
                attendee = component.getAttendeeProperty(match)
                if attendee:
                    results.append(attendee)

        return results

    def getAllAttendeeProperties(self):
        """
        Yield all attendees as Property objects.  Works on either a VCALENDAR or
        on a component.
        @return: a generator yielding Property objects
        """

        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    for attendee in component.getAllAttendeeProperties():
                        yield attendee
        else:
            # Find the primary subcomponent
            for attendee in self.properties("ATTENDEE"):
                yield attendee


    def getMaskUID(self):
        """
        Get the X-CALENDARSEREVR-MASK-UID value. Works on either a VCALENDAR or on a component.
        
        @return: the string value of the X-CALENDARSEREVR-MASK-UID property, or None
        """
        
        # Extract appropriate sub-component if this is a VCALENDAR
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() != "VTIMEZONE":
                    return component.getMaskUID()
        else:
            try:
                # Find the primary subcomponent
                return self.propertyValue("X-CALENDARSERVER-MASK-UID")
            except ValueError:
                pass

        return None

    def setParameterToValueForPropertyWithValue(self, paramname, paramvalue, propname, propvalue):
        """
        Add or change the parameter to the specified value on the property having the specified value.
        
        @param paramname: the parameter name
        @type paramname: C{str}
        @param paramvalue: the parameter value to set
        @type paramvalue: C{str}
        @param propname: the property name
        @type propname: C{str}
        @param propvalue: the property value to test
        @type propvalue: C{str} or C{None}
        """
        
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            for property in component.properties(propname):
                if propvalue is None or property.value() == propvalue:
                    property.params()[paramname] = [paramvalue]
    
    def hasPropertyInAnyComponent(self, properties):
        """
        Test for the existence of one or more properties in any component.
        
        @param properties: property names to test for
        @type properties: C{list} or C{tuple}
        """

        for property in properties:
            if self.hasProperty(property):
                return True

        for component in self.subcomponents():
            if component.hasPropertyInAnyComponent(properties):
                return True

        return False

    def hasPropertyValueInAllComponents(self, property):
        """
        Test for the existence of a property with a specific value in any sub-component.
        
        @param property: property to test for
        @type property: L{Property}
        """

        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            found = component.getProperty(property.name())
            if not found or found.value() != property.value():
                return False

        return True

    def addPropertyToAllComponents(self, property):
        """
        Add a property to all top-level components except VTIMEZONE.

        @param property: the property to add
        @type property: L{Property}
        """
        
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            component.addProperty(property)

    def replacePropertyInAllComponents(self, property):
        """
        Replace a property in all components.
        @param property: the L{Property} to replace in this component.
        """
        
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            component.replaceProperty(property)
    
    def transferProperties(self, from_calendar, properties):
        """
        Transfer specified properties from old calendar into all components
        of this calendar, synthesizing any for new overridden instances.
 
        @param from_calendar: the old calendar to copy from
        @type from_calendar: L{Component}
        @param properties: the property names to copy over
        @type properties: C{typle} or C{list}
        """

        assert from_calendar.name() == "VCALENDAR", "Not a calendar: %r" % (self,)
        
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() == "VTIMEZONE":
                    continue
                component.transferProperties(from_calendar, properties)
        else:
            # Is there a matching component
            rid = self.getRecurrenceIDUTC()
            matched = from_calendar.overriddenComponent(rid)
            
            # If no match found, we are processing a new overridden instance so copy from the original master
            if not matched:
                matched = from_calendar.masterComponent()

            if matched:
                for propname in properties:
                    for prop in matched.properties(propname):
                        self.addProperty(prop)
            
    def attendeesView(self, attendees, onlyScheduleAgentServer=False):
        """
        Filter out any components that all attendees are not present in. Use EXDATEs
        on the master to account for changes.
        """

        assert self.name() == "VCALENDAR", "Not a calendar: %r" % (self,)
            
        # Modify any components that reference the attendee, make note of the ones that don't
        remove_components = []
        master_component = None
        removed_master = False
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            found_all_attendees = True
            for attendee in attendees:
                foundAttendee = component.getAttendeeProperty((attendee,))
                if foundAttendee is None:
                    found_all_attendees = False
                    break
                if onlyScheduleAgentServer:
                    if "SCHEDULE-AGENT" in foundAttendee.params():
                        if foundAttendee.paramValue("SCHEDULE-AGENT") != "SERVER":
                            found_all_attendees = False
                            break
            if not found_all_attendees:
                remove_components.append(component)
            if component.getRecurrenceIDUTC() is None:
                master_component = component
                if not found_all_attendees:
                    removed_master = True
                
        # Now remove the unwanted components - but we may need to exdate the master
        exdates = []
        for component in remove_components:
            rid = component.getRecurrenceIDUTC()
            if rid is not None:
                exdates.append(rid)
            self.removeComponent(component)
            
        if not removed_master and master_component is not None:
            for exdate in exdates:
                master_component.addProperty(Property("EXDATE", [exdate,]))
    
    def filterComponents(self, rids):
        
        # If master is in rids do nothing
        if not rids or "" in rids:
            return True
        
        assert self.name() == "VCALENDAR", "Not a calendar: %r" % (self,)
            
        # Remove components not in the list
        components = tuple(self.subcomponents())
        remaining = len(components)
        for component in components:
            if component.name() == "VTIMEZONE":
                remaining -= 1
                continue
            rid = component.getRecurrenceIDUTC()
            if (toString(rid) if rid else "") not in rids:
                self.removeComponent(component)
                remaining -= 1
                
        return remaining != 0
        
    def removeAllButOneAttendee(self, attendee):
        """
        Remove all ATTENDEE properties except for the one specified.
        """

        assert self.name() == "VCALENDAR", "Not a calendar: %r" % (self,)

        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            [component.removeProperty(p) for p in tuple(component.properties("ATTENDEE")) if p.value().lower() != attendee.lower()]
            
    def removeAlarms(self):
        """
        Remove all Alarms components
        """

        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() == "VTIMEZONE":
                    continue
                component.removeAlarms()
        else:
            for component in tuple(self.subcomponents()):
                if component.name() == "VALARM":
                    self.removeComponent(component)
                
    def filterProperties(self, remove=None, keep=None, do_subcomponents=True):
        """
        Remove all properties that do not match the provided set.
        """

        if do_subcomponents:
            for component in self.subcomponents():
                component.filterProperties(remove, keep, do_subcomponents=False)
        else:
            if self.name() == "VTIMEZONE":
                return
            
            for p in tuple(self.properties()):
                if (keep and p.name() not in keep) or (remove and p.name() in remove):
                    self.removeProperty(p)
                
    def removeXProperties(self, keep_properties=(), remove_x_parameters=True, do_subcomponents=True):
        """
        Remove all X- properties except the specified ones
        """

        if do_subcomponents and self.name() == "VCALENDAR":
            for component in self.subcomponents():
                component.removeXProperties(keep_properties, remove_x_parameters, do_subcomponents=False)
        else:
            if self.name() == "VTIMEZONE":
                return
            for p in tuple(self.properties()):
                xpname = p.name().startswith("X-")
                if xpname and p.name() not in keep_properties:
                    self.removeProperty(p)
                elif not xpname and remove_x_parameters:
                    for paramname in tuple(p.params()):
                        if paramname.startswith("X-"):
                            del p.params()[paramname]
            
    def removePropertyParameters(self, property, params):
        """
        Remove all specified property parameters
        """

        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() == "VTIMEZONE":
                    continue
                component.removePropertyParameters(property, params)
        else:
            props = self.properties(property)
            for prop in props:
                for param in params:
                    try:
                        del prop.params()[param]
                    except KeyError:
                        pass

    def removePropertyParametersByValue(self, property, paramvalues):
        """
        Remove all specified property parameters
        """

        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() == "VTIMEZONE":
                    continue
                component.removePropertyParametersByValue(property, paramvalues)
        else:
            props = self.properties(property)
            for prop in props:
                for param, value in paramvalues:
                    try:
                        prop.params()[param].remove(value)
                        if len(prop.params()[param]) == 0:
                            del prop.params()[param]
                    except KeyError:
                        pass
                    except ValueError:
                        pass

    def normalizeAll(self):
        
        # Normalize all properties
        for prop in tuple(self.properties()):
            result = normalizeProps.get(prop.name())
            if result:
                default_value, default_params = result
            else:
                # Assume default VALUE is TEXT
                default_value = None
                default_params = {"VALUE": "TEXT"}
            
            # Remove any default parameters
            for name, value in prop.params().items():
                if value == [default_params.get(name),]:
                    del prop.params()[name]
            
            # If there are no parameters, remove the property if it has the default value
            if len(prop.params()) == 0:
                if prop.value() == default_value:
                    self.removeProperty(prop)
                    continue

            # Otherwise look for value normalization
            normalize_function = normalizePropsValue.get(prop.name())
            if normalize_function:
                prop.setValue(normalize_function(prop.value()))

        # Do datetime/rrule normalization
        self.normalizeDateTimes()

        # Do to all sub-components too
        for component in self.subcomponents():
            component.normalizeAll()

    def normalizeDateTimes(self):
        """
        Normalize various datetime properties into UTC and handle DTEND/DURATION variants in such
        a way that we can compare objects with slight differences.
        
        Also normalize the RRULE value parts.
        
        Strictly speaking we should not need to do this as clients should not be messing with
        these properties - i.e. they should round trip them. Unfortunately some do...
        """
        
        # TODO: what about VJOURNAL and VTODO?
        if self.name() == "VEVENT":
            
            # Basic time properties
            dtstart = self.getProperty("DTSTART")
            dtend = self.getProperty("DTEND")
            duration = self.getProperty("DURATION")
            
            newdtstart, newdtend = normalizeStartEndDuration(
                dtstart.value(),
                dtend.value() if dtend is not None else None,
                duration.value() if duration is not None else None,
            )
            
            dtstart.setValue(newdtstart)
            if "X-VOBJ-ORIGINAL-TZID" in dtstart.params():
                dtstart.params()["ORIGINAL-TZID"] = dtstart.params()["X-VOBJ-ORIGINAL-TZID"]
                del dtstart.params()["X-VOBJ-ORIGINAL-TZID"]
            if dtend is not None:
                dtend.setValue(newdtend)
                if "X-VOBJ-ORIGINAL-TZID" in dtend.params():
                    dtend.params()["ORIGINAL-TZID"] = dtend.params()["X-VOBJ-ORIGINAL-TZID"]
                    del dtend.params()["X-VOBJ-ORIGINAL-TZID"]
            elif duration is not None:
                self.removeProperty(duration)
                self.addProperty(Property("DTEND", newdtend))

            exdates = self.properties("EXDATE")
            for exdate in exdates:
                exdate.setValue([normalizeToUTC(value) for value in exdate.value()])
                try:
                    del exdate.params()["TZID"]
                except KeyError:
                    pass

            rid = self.getProperty("RECURRENCE-ID")
            if rid is not None:
                rid.setValue(normalizeToUTC(rid.value()))
                try:
                    del rid.params()["TZID"]
                except KeyError:
                    pass

            # Recurrence rules - we need to normalize the order of the value parts
            rrules = self.properties("RRULE")
            for rrule in rrules:
                indexedTokens = {}
                indexedTokens.update([valuePart.split("=") for valuePart in rrule.value().split(";")])
                sortedValue = ";".join(["%s=%s" % (key, value,) for key, value in sorted(indexedTokens.iteritems(), key=lambda x:x[0])])
                rrule.setValue(sortedValue)

    def normalizePropertyValueLists(self, propname):
        """
        Convert properties that have a list of values into single properties, to make it easier
        to do comparisons between two ical objects.
        """
        
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() == "VTIMEZONE":
                    continue
                component.normalizePropertyValueLists(propname)
        else:
            for prop in tuple(self.properties(propname)):
                if type(prop.value()) is list and len(prop.value()) > 1:
                    self.removeProperty(prop)
                    for value in prop.value():
                        self.addProperty(Property(propname, [value,]))

    def normalizeAttachments(self):
        """
        Remove any ATTACH properties that relate to a dropbox.
        """
        
        if self.name() == "VCALENDAR":
            for component in self.subcomponents():
                if component.name() == "VTIMEZONE":
                    continue
                component.normalizeAttachments()
        else:
            dropboxPrefix = self.propertyValue("X-APPLE-DROPBOX")
            if dropboxPrefix is None:
                return
            for attachment in tuple(self.properties("ATTACH")):
                valueType = attachment.paramValue("VALUE")
                if valueType in (None, "URI"):
                    dataValue = attachment.value()
                    if dataValue.find(dropboxPrefix) != -1:
                        self.removeProperty(attachment)

    @inlineCallbacks
    def normalizeCalendarUserAddresses(self, lookupFunction):
        """
        Do the ORGANIZER/ATTENDEE property normalization.

        @param lookupFunction: function returning full name, guid, CUAs for a given CUA (Deferred)
        @type lookupFunction: L{Function}
        """
        for component in self.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            for prop in itertools.chain(
                component.properties("ORGANIZER"),
                component.properties("ATTENDEE")
            ):

                # Check that we can lookup this calendar user address - if not
                # we cannot do anything with it
                cuaddr = normalizeCUAddr(prop.value())
                name, guid, cuaddrs = (yield lookupFunction(cuaddr))
                if guid is None:
                    continue

                # Always re-write value to urn:uuid
                prop.setValue("urn:uuid:%s" % (guid,))

                # Always re-write the CN parameter
                if name:
                    prop.params()["CN"] = [name,]
                else:
                    try:
                        del prop.params()["CN"]
                    except KeyError:
                        pass

                # Re-write the EMAIL if its value no longer
                # matches
                oldemail = prop.params().get("EMAIL", (None,))[0]
                if oldemail:
                    oldemail = "mailto:%s" % (oldemail,)
                if oldemail is None or oldemail not in cuaddrs:
                    if cuaddr.startswith("mailto:") and cuaddr in cuaddrs:
                        email = cuaddr[7:]
                    else:
                        for addr in cuaddrs:
                            if addr.startswith("mailto:"):
                                email = addr[7:]
                                break
                        else:
                            email = None

                    if email:
                        prop.params()["EMAIL"] = [email,]
                    else:
                        try:
                            del prop.params()["EMAIL"]
                        except KeyError:
                            pass

        
##
# Dates and date-times
##

class FixedOffset (datetime.tzinfo):
    """
    Fixed offset in minutes east from UTC.
    """
    def __init__(self, offset, name=None):
        self._offset = datetime.timedelta(minutes=offset)
        self._name   = name

    def utcoffset(self, dt): return self._offset
    def tzname   (self, dt): return self._name
    def dst      (self, dt): return datetime.timedelta(0)

def parse_date(date_string):
    """
    Parse an iCalendar-format DATE string.  (RFC 2445, section 4.3.4)
    @param date_string: an iCalendar-format DATE string.
    @return: a L{datetime.date} object for the given C{date_string}.
    """
    try:
        return stringToDate(date_string)
    except (vParseError, ValueError):
        raise ValueError("Invalid iCalendar DATE: %r" % (date_string,))

def parse_time(time_string):
    """
    Parse iCalendar-format TIME string.  (RFC 2445, section 4.3.12)
    @param time_string: an iCalendar-format TIME string.
    @return: a L{datetime.time} object for the given C{time_string}.
    """
    try:
        # Parse this as a fake date-time string by prepending date
        with_date = "20000101T" + time_string
        return stringToDateTime(with_date).time()
    except (vParseError, ValueError):
        raise ValueError("Invalid iCalendar TIME: %r" % (time_string,))

def parse_datetime(datetime_string):
    """
    Parse iCalendar-format DATE-TIME string.  (RFC 2445, section 4.3.5)
    @param datetime_string: an iCalendar-format DATE-TIME string.
    @return: a L{datetime.datetime} object for the given C{datetime_string}.
    """
    try:
        return stringToDateTime(datetime_string)
    except (vParseError, ValueError):
        raise ValueError("Invalid iCalendar DATE-TIME: %r" % (datetime_string,))

def parse_date_or_datetime(date_string):
    """
    Parse iCalendar-format DATE or DATE-TIME string.  (RFC 2445, sections 4.3.4
    and 4.3.5)
    @param date_string: an iCalendar-format DATE or DATE-TIME string.
    @return: a L{datetime.date} or L{datetime.datetime} object for the given
        C{date_string}.
    """
    try:
        if len(date_string) == 8:
            return parse_date(date_string)
        else:
            return parse_datetime(date_string)
    except ValueError:
        raise ValueError("Invalid iCalendar DATE or DATE-TIME: %r" % (date_string,))

def parse_duration(duration_string):
    """
    Parse iCalendar-format DURATION string.  (RFC 2445, sections 4.3.6)
    @param duration_string: an iCalendar-format DURATION string.
    @return: a L{datetime.timedelta} object for the given C{duration_string}.
    """
    try:
        return stringToDurations(duration_string)[0]
    except (vParseError, ValueError):
        raise ValueError("Invalid iCalendar DURATION: %r" % (duration_string,))

_regex_duration = None

##
# Timezones
##

def tzexpand(tzdata, start, end):
    """
    Expand a timezone to get onset/utc-offset observance tuples within the specified
    time range.

    @param tzdata: the iCalendar data containing a VTIMEZONE.
    @type tzdata: C{str}
    @param start: date for the start of the expansion.
    @type start: C{date}
    @param end: date for the end of the expansion.
    @type end: C{date}
    
    @return: a C{list} of tuples of (C{datetime}, C{str})
    """
    
    start = datetime.datetime.fromordinal(start.toordinal())
    end = datetime.datetime.fromordinal(end.toordinal())
    icalobj = Component.fromString(tzdata)
    tzcomp = None
    for comp in icalobj.subcomponents():
        if comp.name() == "VTIMEZONE":
            tzcomp = comp
            break
    else:
        raise ValueError("No VTIMEZONE component in %s" % (tzdata,))

    tzinfo = tzcomp.gettzinfo()
    
    results = []
    
    # Get the start utc-offset - that is our first value
    results.append((dateTimeToString(start), deltaToOffset(tzinfo.utcoffset(start)),))
    last_dt = start
    
    while last_dt < end:
        # Get the transitions for the current year
        standard = getTransition("standard", last_dt.year, tzinfo)
        daylight = getTransition("daylight", last_dt.year, tzinfo)
        
        # Order the transitions
        if standard and daylight:
            if standard < daylight:
                first = standard
                second = daylight
            else:
                first = daylight
                second = standard
        elif standard:
            first = standard
            second = None
        else:
            first = daylight
            second = None
        
        for transition in (first, second):
            # Terminate if the next transition is outside the time range
            if transition and transition > end:
                break
            
            # If the next transition is after the last one, then add its info if
            # the utc-offset actually changed.
            if transition and transition > last_dt:
                utcoffset = deltaToOffset(tzinfo.utcoffset(transition + datetime.timedelta(days=1)))
                if utcoffset != results[-1][1]:
                    results.append((dateTimeToString(transition), utcoffset,))
                last_dt = transition
            
        # Bump last transition up to the start of the next year
        last_dt = datetime.datetime(last_dt.year + 1, 1, 1, 0, 0, 0)
        if last_dt >= end:
            break
    
    return results

##
# Utilities
##

#
# This function is from "Python Cookbook, 2d Ed., by Alex Martelli, Anna
# Martelli Ravenscroft, and David Ascher (O'Reilly Media, 2005) 0-596-00797-3."
#
def merge(*iterables):
    """
    Merge sorted iterables into one sorted iterable.
    @param iterables: arguments are iterables which yield items in sorted order.
    @return: an iterable of all items generated by every iterable in
    C{iterables} in sorted order.
    """
    heap = []
    for iterable in iterables:
        iterator = iter(iterable)
        for value in iterator:
            heap.append((value, iterator))
            break
    heapq.heapify(heap)
    while heap:
        value, iterator = heap[0]
        yield value
        for value in iterator:
            heapq.heapreplace(heap, (value, iterator))
            break
        else:
            heapq.heappop(heap)
