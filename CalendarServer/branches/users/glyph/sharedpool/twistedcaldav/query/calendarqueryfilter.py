##
# Copyright (c) 2009-2010 Apple Inc. All rights reserved.
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
Object model of CALDAV:filter element used in a calendar-query.
"""

__all__ = [
    "Filter",
]

from twext.python.log import Logger

from twistedcaldav.caldavxml import caldav_namespace, CalDAVTimeZoneElement
from twistedcaldav.dateops import timeRangesOverlap
from twistedcaldav.ical import Component, Property, parse_date_or_datetime
from vobject.icalendar import utc
import datetime

log = Logger()

class FilterBase(object):
    """
    Determines which matching components are returned.
    """

    def __init__(self, xml_element):
        self.xmlelement = xml_element

    def match(self, item, access=None):
        raise NotImplementedError

    def valid(self, level=0):
        raise NotImplementedError

class Filter(FilterBase):
    """
    Determines which matching components are returned.
    """

    def __init__(self, xml_element):

        super(Filter, self).__init__(xml_element)

        # One comp-filter element must be present
        if len(xml_element.children) != 1 or xml_element.children[0].qname() != (caldav_namespace, "comp-filter"):
            raise ValueError("Invalid CALDAV:filter element: %s" % (xml_element,))
        
        self.child = ComponentFilter(xml_element.children[0])

    def match(self, component, access=None):
        """
        Returns True if the given calendar component matches this filter, False
        otherwise.
        """
        
        # We only care about certain access restrictions.
        if access not in (Component.ACCESS_CONFIDENTIAL, Component.ACCESS_RESTRICTED):
            access = None

        # We need to prepare ourselves for a time-range query by pre-calculating
        # the set of instances up to the latest time-range limit. That way we can
        # avoid having to do some form of recurrence expansion for each query sub-part.
        maxend, isStartTime = self.getmaxtimerange()
        if maxend:
            if isStartTime:
                if component.isRecurringUnbounded():
                    # Unbounded recurrence is always within a start-only time-range
                    instances = None
                else:
                    # Expand the instances up to infinity
                    instances = component.expandTimeRanges(datetime.datetime(2100, 1, 1, 0, 0, 0, tzinfo=utc), ignoreInvalidInstances=True)
            else:
                instances = component.expandTimeRanges(maxend, ignoreInvalidInstances=True)
        else:
            instances = None
        self.child.setInstances(instances)

        # <filter> contains exactly one <comp-filter>
        return self.child.match(component, access)

    def valid(self):
        """
        Indicate whether this filter element's structure is valid wrt iCalendar
        data object model.
        
        @return: True if valid, False otherwise
        """
        
        # Must have one child element for VCALENDAR
        return self.child.valid(0)
        
    def settimezone(self, tzelement):
        """
        Set the default timezone to use with this query.
        @param calendar: a L{Component} for the VCALENDAR containing the one
            VTIMEZONE that we want
        @return: the L{datetime.tzinfo} derived from the VTIMEZONE or utc.
        """
        assert tzelement is None or isinstance(tzelement, CalDAVTimeZoneElement)

        if tzelement is not None:
            calendar = tzelement.calendar()
            if calendar is not None:
                for subcomponent in calendar.subcomponents():
                    if subcomponent.name() == "VTIMEZONE":
                        # <filter> contains exactly one <comp-filter>
                        tzinfo = subcomponent.gettzinfo()
                        self.child.settzinfo(tzinfo)
                        return tzinfo

        # Default to using utc tzinfo
        self.child.settzinfo(utc)
        return utc

    def getmaxtimerange(self):
        """
        Get the date farthest into the future in any time-range elements
        """
        
        return self.child.getmaxtimerange(None, False)

class FilterChildBase(FilterBase):
    """
    CalDAV filter element.
    """

    def __init__(self, xml_element):

        super(FilterChildBase, self).__init__(xml_element)

        qualifier = None
        filters = []

        for child in xml_element.children:
            qname = child.qname()
            
            if qname in (
                (caldav_namespace, "is-not-defined"),
                (caldav_namespace, "time-range"),
                (caldav_namespace, "text-match"),
            ):
                if qualifier is not None:
                    raise ValueError("Only one of CalDAV:time-range, CalDAV:text-match allowed")
                
                if qname == (caldav_namespace, "is-not-defined"):
                    qualifier = IsNotDefined(child)
                elif qname == (caldav_namespace, "time-range"):
                    qualifier = TimeRange(child)
                elif qname == (caldav_namespace, "text-match"):
                    qualifier = TextMatch(child)

            elif qname == (caldav_namespace, "comp-filter"):
                filters.append(ComponentFilter(child))
            elif qname == (caldav_namespace, "prop-filter"):
                filters.append(PropertyFilter(child))
            elif qname == (caldav_namespace, "param-filter"):
                filters.append(ParameterFilter(child))
            else:
                raise ValueError("Unknown child element: %s" % (qname,))

        if qualifier and isinstance(qualifier, IsNotDefined) and (len(filters) != 0):
            raise ValueError("No other tests allowed when CalDAV:is-not-defined is present")
            
        self.qualifier = qualifier
        self.filters = filters
        self.filter_name = xml_element.attributes["name"]
        if isinstance(self.filter_name, unicode):
            self.filter_name = self.filter_name.encode("utf-8")
        self.defined = not self.qualifier or not isinstance(qualifier, IsNotDefined)

    def match(self, item, access=None):
        """
        Returns True if the given calendar item (either a component, property or parameter value)
        matches this filter, False otherwise.
        """
        
        # Always return True for the is-not-defined case as the result of this will
        # be negated by the caller
        if not self.defined: return True

        if self.qualifier and not self.qualifier.match(item, access): return False

        if len(self.filters) > 0:
            for filter in self.filters:
                if filter._match(item, access):
                    return True
            return False
        else:
            return True

class ComponentFilter (FilterChildBase):
    """
    Limits a search to only the chosen component types.
    """

    def match(self, item, access):
        """
        Returns True if the given calendar item (which is a component)
        matches this filter, False otherwise.
        This specialization uses the instance matching option of the time-range filter
        to minimize instance expansion.
        """

        # Always return True for the is-not-defined case as the result of this will
        # be negated by the caller
        if not self.defined: return True

        if self.qualifier and not self.qualifier.matchinstance(item, self.instances): return False

        if len(self.filters) > 0:
            for filter in self.filters:
                if filter._match(item, access):
                    return True
            return False
        else:
            return True

    def _match(self, component, access):
        # At least one subcomponent must match (or is-not-defined is set)
        for subcomponent in component.subcomponents():
            # If access restrictions are in force, restrict matching to specific components only.
            # In particular do not match VALARM.
            if access and subcomponent.name() not in ("VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY", "VTIMEZONE",):
                continue
            
            # Try to match the component name
            if isinstance(self.filter_name, str):
                if subcomponent.name() != self.filter_name: continue
            else:
                if subcomponent.name() not in self.filter_name: continue
            if self.match(subcomponent, access): break
        else:
            return not self.defined
        return self.defined
        
    def setInstances(self, instances):
        """
        Give the list of instances to each comp-filter element.
        @param instances: the list of instances.
        """
        self.instances = instances
        for compfilter in [x for x in self.filters if isinstance(x, ComponentFilter)]:
            compfilter.setInstances(instances)
        
    def valid(self, level):
        """
        Indicate whether this filter element's structure is valid wrt iCalendar
        data object model.
        
        @param level: the nesting level of this filter element, 0 being the top comp-filter.
        @return:      True if valid, False otherwise
        """
        
        # Check for time-range
        timerange = self.qualifier and isinstance(self.qualifier, TimeRange)

        if level == 0:
            # Must have VCALENDAR at the top
            if (self.filter_name != "VCALENDAR") or timerange:
                log.msg("Top-level comp-filter must be VCALENDAR, instead: %s" % (self.filter_name,))
                return False
        elif level == 1:
            # Disallow VCALENDAR, VALARM, STANDARD, DAYLIGHT, AVAILABLE at the top, everything else is OK
            if self.filter_name in ("VCALENDAR", "VALARM", "STANDARD", "DAYLIGHT", "AVAILABLE"):
                log.msg("comp-filter wrong component type: %s" % (self.filter_name,))
                return False
            
            # time-range only on VEVENT, VTODO, VJOURNAL, VFREEBUSY, VAVAILABILITY
            if timerange and self.filter_name not in ("VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY", "VAVAILABILITY"):
                log.msg("time-range cannot be used with component %s" % (self.filter_name,))
                return False
        elif level == 2:
            # Disallow VCALENDAR, VTIMEZONE, VEVENT, VTODO, VJOURNAL, VFREEBUSY, VAVAILABILITY at the top, everything else is OK
            if (self.filter_name in ("VCALENDAR", "VTIMEZONE", "VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY", "VAVAILABILITY")):
                log.msg("comp-filter wrong sub-component type: %s" % (self.filter_name,))
                return False
            
            # time-range only on VALARM, AVAILABLE
            if timerange and self.filter_name not in ("VALARM", "AVAILABLE",):
                log.msg("time-range cannot be used with sub-component %s" % (self.filter_name,))
                return False
        else:
            # Disallow all standard iCal components anywhere else
            if (self.filter_name in ("VCALENDAR", "VTIMEZONE", "VEVENT", "VTODO", "VJOURNAL", "VFREEBUSY", "VALARM", "STANDARD", "DAYLIGHT", "AVAILABLE")) or timerange:
                log.msg("comp-filter wrong standard component type: %s" % (self.filter_name,))
                return False
        
        # Test each property
        for propfilter in [x for x in self.filters if isinstance(x, PropertyFilter)]:
            if not propfilter.valid():
                return False

        # Test each component
        for compfilter in [x for x in self.filters if isinstance(x, ComponentFilter)]:
            if not compfilter.valid(level + 1):
                return False

        # Test the time-range
        if timerange:
            if not self.qualifier.valid():
                return False

        return True

    def settzinfo(self, tzinfo):
        """
        Set the default timezone to use with this query.
        @param tzinfo: a L{datetime.tzinfo} to use.
        """
        
        # Give tzinfo to any TimeRange we have
        if isinstance(self.qualifier, TimeRange):
            self.qualifier.settzinfo(tzinfo)
        
        # Pass down to sub components/properties
        for x in self.filters:
            x.settzinfo(tzinfo)

    def getmaxtimerange(self, currentMaximum, currentIsStartTime):
        """
        Get the date furthest into the future in any time-range elements
        
        @param currentMaximum: current future value to compare with
        @type currentMaximum: L{datetime.datetime}
        """
        
        # Give tzinfo to any TimeRange we have
        isStartTime = False
        if isinstance(self.qualifier, TimeRange):
            isStartTime = self.qualifier.end is None
            compareWith = self.qualifier.start if isStartTime else self.qualifier.end
            if currentMaximum is None or currentMaximum < compareWith:
                currentMaximum = compareWith
                currentIsStartTime = isStartTime
        
        # Pass down to sub components/properties
        for x in self.filters:
            currentMaximum, currentIsStartTime = x.getmaxtimerange(currentMaximum, currentIsStartTime)

        return currentMaximum, currentIsStartTime

class PropertyFilter (FilterChildBase):
    """
    Limits a search to specific properties.
    """

    def _match(self, component, access):
        # When access restriction is in force, we need to only allow matches against the properties
        # allowed by the access restriction level.
        if access:
            allowedProperties = Component.confidentialPropertiesMap.get(component.name(), None)
            if allowedProperties and access == Component.ACCESS_RESTRICTED:
                allowedProperties += Component.extraRestrictedProperties
        else:
            allowedProperties = None

        # At least one property must match (or is-not-defined is set)
        for property in component.properties():
            # Apply access restrictions, if any.
            if allowedProperties is not None and property.name() not in allowedProperties:
                continue
            if property.name() == self.filter_name and self.match(property, access): break
        else:
            return not self.defined
        return self.defined

    def valid(self):
        """
        Indicate whether this filter element's structure is valid wrt iCalendar
        data object model.
        
        @return:      True if valid, False otherwise
        """
        
        # Check for time-range
        timerange = self.qualifier and isinstance(self.qualifier, TimeRange)
        
        # time-range only on COMPLETED, CREATED, DTSTAMP, LAST-MODIFIED
        if timerange and self.filter_name not in ("COMPLETED", "CREATED", "DTSTAMP", "LAST-MODIFIED"):
            log.msg("time-range cannot be used with property %s" % (self.filter_name,))
            return False

        # Test the time-range
        if timerange:
            if not self.qualifier.valid():
                return False

        # No other tests
        return True

    def settzinfo(self, tzinfo):
        """
        Set the default timezone to use with this query.
        @param tzinfo: a L{datetime.tzinfo} to use.
        """
        
        # Give tzinfo to any TimeRange we have
        if isinstance(self.qualifier, TimeRange):
            self.qualifier.settzinfo(tzinfo)

    def getmaxtimerange(self, currentMaximum, currentIsStartTime):
        """
        Get the date furthest into the future in any time-range elements
        
        @param currentMaximum: current future value to compare with
        @type currentMaximum: L{datetime.datetime}
        """
        
        # Give tzinfo to any TimeRange we have
        isStartTime = False
        if isinstance(self.qualifier, TimeRange):
            isStartTime = self.qualifier.end is None
            compareWith = self.qualifier.start if isStartTime else self.qualifier.end
            if currentMaximum is None or currentMaximum < compareWith:
                currentMaximum = compareWith
                currentIsStartTime = isStartTime

        return currentMaximum, currentIsStartTime

class ParameterFilter (FilterChildBase):
    """
    Limits a search to specific parameters.
    """

    def _match(self, property, access):

        # We have to deal with the problem that the 'Native' form of a property
        # will be missing the TZID parameter due to the conversion performed. Converting
        # to non-native for the entire calendar object causes problems elsewhere, so its
        # best to do it here for this one special case.
        if self.filter_name == "TZID":
            transformed = property.transformAllFromNative()
        else:
            transformed = False

        # At least one property must match (or is-not-defined is set)
        result = not self.defined
        for parameterName in property.params().keys():
            if parameterName == self.filter_name and self.match(property.params()[parameterName], access):
                result = self.defined
                break

        if transformed:
            property.transformAllToNative()
        return result

class IsNotDefined (FilterBase):
    """
    Specifies that the named iCalendar item does not exist.
    """

    def match(self, component, access=None):
        # Oddly, this needs always to return True so that it appears there is
        # a match - but we then "negate" the result if is-not-defined is set.
        # Actually this method should never be called as we special case the
        # is-not-defined option.
        return True

class TextMatch (FilterBase):
    """
    Specifies a substring match on a property or parameter value.
    (CalDAV-access-09, section 9.6.4)
    """
    def __init__(self, xml_element):

        super(TextMatch, self).__init__(xml_element)

        self.text = str(xml_element)
        if "caseless" in xml_element.attributes:
            caseless = xml_element.attributes["caseless"]
            if caseless == "yes":
                self.caseless = True
            elif caseless == "no":
                self.caseless = False
        else:
            self.caseless = True

        if "negate-condition" in xml_element.attributes:
            negate = xml_element.attributes["negate-condition"]
            if negate == "yes":
                self.negate = True
            elif negate == "no":
                self.negate = False
        else:
            self.negate = False

    def match(self, item, access):
        """
        Match the text for the item.
        If the item is a property, then match the property value,
        otherwise it may be a list of parameter values - try to match anyone of those
        """
        if item is None: return False

        if isinstance(item, Property):
            values = [item.value()]
        else:
            values = item

        test = unicode(self.text, "utf-8")
        if self.caseless:
            test = test.lower()

        def _textCompare(s):
            if self.caseless:
                if s.lower().find(test) != -1:
                    return True, not self.negate
            else:
                if s.find(test) != -1:
                    return True, not self.negate
            return False, False

        for value in values:
            # NB Its possible that we have a text list value which appears as a Python list,
            # so we need to check for that an iterate over the list.
            if isinstance(value, list):
                for subvalue in value:
                    matched, result = _textCompare(subvalue)
                    if matched:
                        return result
            else:
                matched, result = _textCompare(value)
                if matched:
                    return result
        
        return self.negate

class TimeRange (FilterBase):
    """
    Specifies a time for testing components against.
    """

    def __init__(self, xml_element):

        super(TimeRange, self).__init__(xml_element)

        # One of start or end must be present
        if "start" not in xml_element.attributes and "end" not in xml_element.attributes:
            raise ValueError("One of 'start' or 'end' must be present in CALDAV:time-range")
        
        self.start = parse_date_or_datetime(xml_element.attributes["start"]) if "start" in xml_element.attributes else None
        self.end = parse_date_or_datetime(xml_element.attributes["end"]) if "end" in xml_element.attributes else None
        self.tzinfo = None

    def settzinfo(self, tzinfo):
        """
        Set the default timezone to use with this query.
        @param tzinfo: a L{datetime.tzinfo} to use.
        """
        
        # Give tzinfo to any TimeRange we have
        self.tzinfo = tzinfo

    def valid(self, level=0):
        """
        Indicate whether the time-range is valid (must be date-time in UTC).
        
        @return:      True if valid, False otherwise
        """
        
        if self.start is not None and not isinstance(self.start, datetime.datetime):
            log.msg("start attribute in <time-range> is not a date-time: %s" % (self.start,))
            return False
        if self.end is not None and not isinstance(self.end, datetime.datetime):
            log.msg("end attribute in <time-range> is not a date-time: %s" % (self.end,))
            return False
        if self.start is not None and self.start.tzinfo != utc:
            log.msg("start attribute in <time-range> is not UTC: %s" % (self.start,))
            return False
        if self.end is not None and self.end.tzinfo != utc:
            log.msg("end attribute in <time-range> is not UTC: %s" % (self.end,))
            return False

        # No other tests
        return True

    def match(self, property, access=None):
        """
        NB This is only called when doing a time-range match on a property.
        """
        if property is None:
            return False
        else:
            return property.containsTimeRange(self.start, self.end, self.tzinfo)

    def matchinstance(self, component, instances):
        """
        Test whether this time-range element causes a match to the specified component
        using the specified set of instances to determine the expanded time ranges.
        @param component: the L{Component} to test.
        @param instances: the list of expanded instances.
        @return: True if the time-range query matches, False otherwise.
        """
        if component is None:
            return False
        
        assert instances is not None or self.end is None, "Failure to expand instance for time-range filter: %r" % (self,)
        
        # Special case open-ended unbounded
        if instances is None:
            if component.getRecurrenceIDUTC() is None:
                return True
            else:
                # See if the overridden component's start is past the start
                start, _ignore_end = component.getEffectiveStartEnd()
                if start is None:
                    return True
                else:
                    return start >= self.start

        # Handle alarms as a special case
        alarms = (component.name() == "VALARM")
        if alarms:
            testcomponent = component._parent
        else:
            testcomponent = component
            
        for key in instances:
            instance = instances[key]
            
            # First make sure components match
            if not testcomponent.same(instance.component):
                continue

            if alarms:
                # Get all the alarm triggers for this instance and test each one
                triggers = instance.getAlarmTriggers()
                for trigger in triggers:
                    if timeRangesOverlap(trigger, None, self.start, self.end, self.tzinfo):
                        return True
            else:
                # Regular instance overlap test
                if timeRangesOverlap(instance.start, instance.end, self.start, self.end, self.tzinfo):
                    return True

        return False
