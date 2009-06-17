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

"""
iTIP (RFC2446) processing.
"""

#
# This is currently used for handling auto-replies to schedule requests arriving
# in an inbox. It is called in a delayed fashion via reactor.callLater.
#
# We assume that all the components/calendars we deal with have been determined
# as being 'valid for CalDAV/iTIP', i.e. they contain UIDs, single component
# types, etc.
#
# The logic for component matching needs a lot more work as it currently does not
# know how to deal with overridden instances.
#

import datetime

from twistedcaldav.config import config
from twistedcaldav.dateops import normalizeToUTC, toString
from twistedcaldav.log import Logger
from twistedcaldav.ical import Property, iCalendarProductID, Component

from vobject.icalendar import utc
from vobject.icalendar import dateTimeToString

log = Logger()

__version__ = "0.0"

__all__ = [
    "iTipProcessing",
    "iTipGenerator",
]

class iTipProcessing(object):

    @staticmethod
    def processNewRequest(itip_message, recipient=None, autoprocessing=False):
        """
        Process a METHOD=REQUEST for a brand new calendar object.
        
        @param itip_message: the iTIP message calendar object to process.
        @type itip_message:
        
        @return: calendar object ready to save
        """
        assert itip_message.propertyValue("METHOD") == "REQUEST", "iTIP message must have METHOD:REQUEST"

        calendar = itip_message.duplicate()
        method = calendar.getProperty("METHOD")
        if method:
            calendar.removeProperty(method)
        
        if recipient and not autoprocessing:
            iTipProcessing.fixForiCal3(calendar.subcomponents(), recipient, config.Scheduling.CalDAV.OldDraftCompatibility)

        return calendar
        
    @staticmethod
    def processRequest(itip_message, calendar, recipient, autoprocessing=False):
        """
        Process a METHOD=REQUEST.
        
        @param itip_message: the iTIP message calendar object to process.
        @type itip_message:
        @param calendar: the calendar object to apply the REQUEST to
        @type calendar:
        
        @return: a C{tuple} of:
            calendar object ready to save, or C{None} (request should be ignored)
            a C{set} of iCalendar properties that changed, or C{None},
            a C{set} of recurrences that changed, or C{None}
        """
        
        # Merge Organizer data with Attendee's own changes (VALARMs, Comment only for now).
        from twistedcaldav.scheduling.icaldiff import iCalDiff
        rids = iCalDiff(calendar, itip_message, False).whatIsDifferent()

        # Different behavior depending on whether a master component is present or not
        current_master = calendar.masterComponent()
        if current_master:
            master_valarms = [comp for comp in current_master.subcomponents() if comp.name() == "VALARM"]
            private_comments = current_master.properties("X-CALENDARSERVER-PRIVATE-COMMENT")
            transps = current_master.properties("TRANSP")
        else:
            master_valarms = ()
            private_comments = ()
            transps = ()

        if itip_message.masterComponent() is not None:
            
            # Get a new calendar object first
            new_calendar = iTipProcessing.processNewRequest(itip_message, recipient, autoprocessing)
            
            # Copy over master alarms, comments
            master_component = new_calendar.masterComponent()
            for alarm in master_valarms:
                master_component.addComponent(alarm)
            for comment in private_comments:
                master_component.addProperty(comment)
            for transp in transps:
                master_component.replaceProperty(transp)
                
            # Now try to match recurrences
            for component in new_calendar.subcomponents():
                if component.name() != "VTIMEZONE" and component.getRecurrenceIDUTC() is not None:
                    iTipProcessing.transferItems(calendar, master_valarms, private_comments, transps, component)
            
            # Now try to match recurrences
            for component in calendar.subcomponents():
                if component.name() != "VTIMEZONE" and component.getRecurrenceIDUTC() is not None:
                    rid = component.getRecurrenceIDUTC()
                    if new_calendar.overriddenComponent(rid) is None:
                        allowCancelled = component.propertyValue("STATUS") == "CANCELLED"
                        new_component = new_calendar.deriveInstance(rid, allowCancelled=allowCancelled)
                        if new_component:
                            new_calendar.addComponent(new_component)
                            iTipProcessing.transferItems(calendar, master_valarms, private_comments, transps, new_component)
            
            # Replace the entire object
            return new_calendar, rids

        else:
            # Need existing tzids
            tzids = calendar.timezones()

            # Update existing instances
            for component in itip_message.subcomponents():
                if component.name() == "VTIMEZONE":
                    # May need to add a new VTIMEZONE
                    if component.propertyValue("TZID") not in tzids:
                        calendar.addComponent(component)
                else:
                    component = component.duplicate()
                    iTipProcessing.transferItems(calendar, master_valarms, private_comments, transps, component, remove_matched=True)
                    calendar.addComponent(component)
                    if recipient and not autoprocessing:
                        iTipProcessing.fixForiCal3((component,), recipient, config.Scheduling.CalDAV.OldDraftCompatibility)

            # Write back the modified object
            return calendar, rids

    @staticmethod
    def processCancel(itip_message, calendar, autoprocessing=False):
        """
        Process a METHOD=CANCEL.
        
        TODO: Yes, I am going to ignore RANGE= on RECURRENCE-ID for now...
        
        @param itip_message: the iTIP message calendar object to process.
        @type itip_message:
        @param calendar: the calendar object to apply the CANCEL to
        @type calendar:
        
        @return: C{tuple} of:
            C{bool} : C{True} if processed, C{False} if scheduling message should be ignored
            C{bool} : C{True} if calendar object should be deleted, C{False} otherwise
            C{set}  : set of Recurrence-IDs for cancelled instances, or C{None} if all cancelled
        """
        
        assert itip_message.propertyValue("METHOD") == "CANCEL", "iTIP message must have METHOD:CANCEL"
        assert itip_message.resourceUID() == calendar.resourceUID(), "UIDs must be the same to process iTIP message"

        # Check to see if this is a cancel of the entire event
        if itip_message.masterComponent() is not None:
            if autoprocessing:
                # Delete the entire event off the auto-processed calendar
                return True, True, None
            else:
                # Cancel every instance in the existing event
                calendar.replacePropertyInAllComponents(Property("STATUS", "CANCELLED"))
                return True, False, None

        # iTIP CANCEL can contain multiple components being cancelled in the RECURRENCE-ID case.
        # So we need to iterate over each iTIP component.

        # Get the existing calendar master object if it exists
        calendar_master = calendar.masterComponent()
        exdates = []
        rids = set()

        # Look at each component in the iTIP message
        for component in itip_message.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
        
            # Extract RECURRENCE-ID value from component
            rid = component.getRecurrenceIDUTC()
            rids.add(rid)
            
            # Get the one that matches in the calendar
            overridden = calendar.overriddenComponent(rid)
            
            if overridden:
                # We are cancelling an overridden component.

                if autoprocessing:
                    # Exclude the cancelled instance
                    exdates.append(component.getRecurrenceIDUTC())
                
                    # Remove the existing component.
                    calendar.removeComponent(overridden)
                else:
                    # Existing component is cancelled.
                    overridden.replaceProperty(Property("STATUS", "CANCELLED"))

            elif calendar_master:
                # We are trying to CANCEL a non-overridden instance.
                
                if autoprocessing:
                    # Exclude the cancelled instance
                    exdates.append(component.getRecurrenceIDUTC())
                else:
                    # Derive a new component and cancel it.
                    overridden = calendar.deriveInstance(rid)
                    if overridden:
                        overridden.replaceProperty(Property("STATUS", "CANCELLED"))
                        calendar.addComponent(overridden)

        # If we have any EXDATEs lets add them to the existing calendar object.
        if exdates and calendar_master:
            calendar_master.addProperty(Property("EXDATE", exdates))

        # See if there are still components in the calendar - we might have deleted the last overridden instance
        # in which case the calendar object is empty (except for VTIMEZONEs).
        if calendar.mainType() is None:
            # Delete the now empty calendar object
            return True, True, None
        else:
            return True, False, rids
    
    @staticmethod
    def processReply(itip_message, calendar):
        """
        Process a METHOD=REPLY.
        
        TODO: Yes, I am going to ignore RANGE= on RECURRENCE-ID for now...
        
        @param itip_message: the iTIP message calendar object to process.
        @type itip_message:
        @param calendar: the calendar object to apply the REPLY to
        @type calendar:
        
        @return: a C{tuple} of:
            C{True} if processed, C{False} if scheduling message should be ignored
            C{tuple} of change info
        """
        
        assert itip_message.propertyValue("METHOD") == "REPLY", "iTIP message must have METHOD:REPLY"
        assert itip_message.resourceUID() == calendar.resourceUID(), "UIDs must be the same to process iTIP message"

        # Take each component in the reply and update the corresponding component
        # in the organizer's copy (possibly generating new ones) so that the ATTENDEE
        # PARTSTATs match up.

        # Do the master first
        old_master = calendar.masterComponent()
        new_master = itip_message.masterComponent()
        attendees = set()
        rids = set()
        if new_master:
            attendee, partstat, private_comment = iTipProcessing.updateAttendeeData(new_master, old_master)
            attendees.add(attendee)
            if partstat or private_comment:
                rids.add(("", partstat, private_comment,))

        # Now do all overridden ones (sort by RECURRENCE-ID)
        sortedComponents = []
        for itip_component in itip_message.subcomponents():
            
            # Make sure we have an appropriate component
            if itip_component.name() == "VTIMEZONE":
                continue
            rid = itip_component.getRecurrenceIDUTC()
            if rid is None:
                continue
            sortedComponents.append((rid, itip_component,))
            
        sortedComponents.sort(key=lambda x:x[0])
        
        for rid, itip_component in sortedComponents:
            # Find matching component in organizer's copy
            match_component = calendar.overriddenComponent(rid)
            if match_component is None:
                # Attendee is overriding an instance themselves - we need to create a derived one
                # for the Organizer
                match_component = calendar.deriveInstance(rid)
                if match_component:
                    calendar.addComponent(match_component)
                else:
                    log.error("Ignoring instance: %s in iTIP REPLY for: %s" % (rid, itip_message.resourceUID()))
                    continue

            attendee, partstat, private_comment = iTipProcessing.updateAttendeeData(itip_component, match_component)
            attendees.add(attendee)
            if rids is not None and (partstat or private_comment):
                rids.add((toString(rid), partstat, private_comment,))

        assert len(attendees) == 1, "ATTENDEE property in a REPLY must be the same in all components\n%s" % (str(itip_message),)
        return True, (attendees.pop(), rids)

    @staticmethod
    def updateAttendeeData(from_component, to_component):
        """
        Copy the PARTSTAT of the Attendee in the from_component to the matching ATTENDEE
        in the to_component. Ignore if no match found. Also update the private comments.

        @param from_component:
        @type from_component:
        @param to_component:
        @type to_component:
        """
        
        # Track what changed
        partstat_changed = False
        private_comment_changed = False

        # Get REQUEST-STATUS as we need to write that into the saved ATTENDEE property
        reqstatus = tuple(from_component.properties("REQUEST-STATUS"))
        assert len(reqstatus) <= 1, "There must be zero or REQUEST-STATUS properties in a REPLY\n%s" % (str(from_component),)
        if reqstatus:
            reqstatus = ";".join(reqstatus[0].value()[0:2])
        else:
            reqstatus = iTIPRequestStatus.SUCCESS

        # Get attendee in from_component - there MUST be only one
        attendees = tuple(from_component.properties("ATTENDEE"))
        assert len(attendees) == 1, "There must be one and only one ATTENDEE property in a REPLY\n%s" % (str(from_component),)
        attendee = attendees[0]
        partstat = attendee.params().get("PARTSTAT", ("NEEDS-ACTION",))[0]
        
        # Now find matching ATTENDEE in to_component
        existing_attendee = to_component.getAttendeeProperty((attendee.value(),))
        if existing_attendee:
            oldpartstat = existing_attendee.params().get("PARTSTAT", ("NEEDS-ACTION",))[0]
            existing_attendee.params()["PARTSTAT"] = [partstat]
            existing_attendee.params()["SCHEDULE-STATUS"] = [reqstatus]
            partstat_changed = (oldpartstat != partstat)
            
            if partstat == "NEEDS-ACTION":
                existing_attendee.params()["RSVP"] = ["TRUE"]
            else:
                try:
                    del existing_attendee.params()["RSVP"]
                except KeyError:
                    pass

            # Handle attendee comments
            if config.Scheduling.CalDAV.get("EnablePrivateComments", True):
                # Look for X-CALENDARSERVER-PRIVATE-COMMENT property in iTIP component (State 1 in spec)
                attendee_comment = tuple(from_component.properties("X-CALENDARSERVER-PRIVATE-COMMENT"))
                attendee_comment = attendee_comment[0] if len(attendee_comment) else None
                
                # Look for matching X-CALENDARSERVER-ATTENDEE-COMMENT property in existing data (State 2 in spec)
                private_comments = tuple(to_component.properties("X-CALENDARSERVER-ATTENDEE-COMMENT"))
                for comment in private_comments:
                    params = comment.params()["X-CALENDARSERVER-ATTENDEE-REF"]
                    assert len(params) == 1, "Must be one and only one X-CALENDARSERVER-ATTENDEE-REF parameter in X-CALENDARSERVER-ATTENDEE-COMMENT"
                    param = params[0]
                    if param == attendee.value():
                        private_comment = comment
                        break
                else:
                    private_comment = None
            else:
                attendee_comment = None
                private_comment = None
                
            # Now do update logic
            if attendee_comment is None and private_comment is None:
                # Nothing to do
                pass
 
            elif attendee_comment is None and private_comment is not None:
                # Remove all property parameters
                private_comment.params().clear()
                
                # Add default parameters
                private_comment.params()["X-CALENDARSERVER-ATTENDEE-REF"] = [attendee.value()]
                private_comment.params()["X-CALENDARSERVER-DTSTAMP"] = [dateTimeToString(datetime.datetime.now(tz=utc))]
                
                # Set value empty
                private_comment.setValue("")
                
                private_comment_changed = True
                
            elif attendee_comment is not None and private_comment is None:
                
                # Add new property
                private_comment = Property(
                    "X-CALENDARSERVER-ATTENDEE-COMMENT",
                    attendee_comment.value(),
                    params = {
                        "X-CALENDARSERVER-ATTENDEE-REF":     [attendee.value()],
                        "X-CALENDARSERVER-DTSTAMP": [dateTimeToString(datetime.datetime.now(tz=utc))],
                    }
                )
                to_component.addProperty(private_comment)
                
                private_comment_changed = True
            
            else:
                # Only change if different
                if private_comment.value() != attendee_comment.value():
                    # Remove all property parameters
                    private_comment.params().clear()
                    
                    # Add default parameters
                    private_comment.params()["X-CALENDARSERVER-ATTENDEE-REF"] = [attendee.value()]
                    private_comment.params()["X-CALENDARSERVER-DTSTAMP"] = [dateTimeToString(datetime.datetime.now(tz=utc))]
                    
                    # Set new value
                    private_comment.setValue(attendee_comment.value())
    
                    private_comment_changed = True

        return attendee.value(), partstat_changed, private_comment_changed

    @staticmethod
    def transferItems(from_calendar, master_valarms, private_comments, transps, to_component, remove_matched=False):

        rid = to_component.getRecurrenceIDUTC()

        # Is there a matching component
        matched = from_calendar.overriddenComponent(rid)
        if matched:
            # Copy over VALARMs from existing component
            [to_component.addComponent(comp) for comp in matched.subcomponents() if comp.name() == "VALARM"]
            [to_component.addProperty(prop) for prop in matched.properties("X-CALENDARSERVER-ATTENDEE-COMMENT")]
            [to_component.replaceProperty(prop) for prop in matched.properties("TRANSP")]

            # Remove the old one
            if remove_matched:
                from_calendar.removeComponent(matched)
                
        else:
            # It is a new override - copy any valarms on the existing master component
            # into the new one.
            [to_component.addComponent(alarm) for alarm in master_valarms]
            [to_component.addProperty(comment) for comment in private_comments]
            [to_component.replaceProperty(transp) for transp in transps]
    
    @staticmethod
    def fixForiCal3(components, recipient, compatibilityMode):
        # For each component where the ATTENDEE property of the recipient has PARTSTAT
        # NEEDS-ACTION we need to add X-APPLE-NEEDS-REPLY:TRUE
        # We also add TRANSP:TRANSPARENT
        for component in components:
            if component.name() == "VTIMEZONE":
                continue
            attendee = component.getAttendeeProperty((recipient,))
            if attendee:
                partstat = attendee.params().get("PARTSTAT", ("NEEDS-ACTION",))[0]
                if partstat == "NEEDS-ACTION":
                    if compatibilityMode:
                        component.addProperty(Property("X-APPLE-NEEDS-REPLY", "TRUE"))
                    component.replaceProperty(Property("TRANSP", "TRANSPARENT"))

class iTipGenerator(object):
    
    @staticmethod
    def generateCancel(original, attendees, instances=None, full_cancel=False):
        
        itip = Component("VCALENDAR")
        itip.addProperty(Property("VERSION", "2.0"))
        itip.addProperty(Property("PRODID", iCalendarProductID))
        itip.addProperty(Property("METHOD", "CANCEL"))

        if instances is None:
            instances = (None,)

        tzids = set()
        for instance_rid in instances:
            
            # Create a new component matching the type of the original
            comp = Component(original.mainType())
            itip.addComponent(comp)

            # Use the master component when the instance is None
            if not instance_rid:
                instance = original.masterComponent()
            else:
                instance = original.overriddenComponent(instance_rid)
                if instance is None:
                    instance = original.deriveInstance(instance_rid)
            assert instance is not None, "Need a master component"

            # Add some required properties extracted from the original
            comp.addProperty(Property("DTSTAMP", datetime.datetime.now(tz=utc)))
            comp.addProperty(Property("UID", instance.propertyValue("UID")))
            seq = instance.propertyValue("SEQUENCE")
            seq = str(int(seq) + 1) if seq else "1"
            comp.addProperty(Property("SEQUENCE", seq))
            comp.addProperty(instance.getOrganizerProperty())
            if instance_rid:
                comp.addProperty(Property("RECURRENCE-ID", normalizeToUTC(instance_rid)))
            
            def addProperties(propname):
                for property in instance.properties(propname):
                    comp.addProperty(property)
                    
            addProperties("SUMMARY")
            addProperties("DTSTART")
            addProperties("DTEND")
            addProperties("DURATION")
            if not instance_rid:
                addProperties("RRULE")
                addProperties("RDATE")
                addProperties("EXDATE")

            # Extract the matching attendee property
            for attendee in attendees:
                if full_cancel:
                    attendeeProp = original.getAttendeeProperty((attendee,))
                else:
                    attendeeProp = instance.getAttendeeProperty((attendee,))
                assert attendeeProp is not None, "Must have matching ATTENDEE property"
                comp.addProperty(attendeeProp)

            tzids.update(comp.timezoneIDs())
            
        # Now include any referenced tzids
        for comp in original.subcomponents():
            if comp.name() == "VTIMEZONE":
                tzid = comp.propertyValue("TZID")
                if tzid in tzids:
                    itip.addComponent(comp)

        # Strip out unwanted bits
        iTipGenerator.prepareSchedulingMessage(itip)

        return itip

    @staticmethod
    def generateAttendeeRequest(original, attendees, filter_rids):

        # Start with a copy of the original as we may have to modify bits of it
        itip = original.duplicate()
        itip.replaceProperty(Property("PRODID", iCalendarProductID))
        itip.addProperty(Property("METHOD", "REQUEST"))
        
        # Force update to DTSTAMP everywhere
        itip.replacePropertyInAllComponents(Property("DTSTAMP", datetime.datetime.now(tz=utc)))

        # Now filter out components that do not contain every attendee
        itip.attendeesView(attendees, onlyScheduleAgentServer=True)
        
        # Now filter out components except the ones specified
        if itip.filterComponents(filter_rids):

            # Strip out unwanted bits
            iTipGenerator.prepareSchedulingMessage(itip)
    
            return itip
        
        else:
            return None

    @staticmethod
    def generateAttendeeReply(original, attendee, changedRids=None, force_decline=False):

        # Start with a copy of the original as we may have to modify bits of it
        itip = original.duplicate()
        itip.replaceProperty(Property("PRODID", iCalendarProductID))
        itip.addProperty(Property("METHOD", "REPLY"))

        # Now filter out components except the ones specified
        itip.filterComponents(changedRids)

        # Force update to DTSTAMP everywhere
        itip.replacePropertyInAllComponents(Property("DTSTAMP", datetime.datetime.now(tz=utc)))

        # Remove all attendees except the one we want
        itip.removeAllButOneAttendee(attendee)
        
        # No alarms
        itip.removeAlarms()

        # Remove all but essential properties
        itip.filterProperties(keep=(
            "UID",
            "RECURRENCE-ID",
            "SEQUENCE",
            "STATUS",
            "DTSTAMP",
            "DTSTART",
            "DTEND",
            "DURATION",
            "RRULE",
            "RDATE",
            "EXDATE",
            "ORGANIZER",
            "ATTENDEE",
            "X-CALENDARSERVER-PRIVATE-COMMENT",
        ))
        
        # Now set each ATTENDEE's PARTSTAT to DECLINED
        if force_decline:
            attendeeProps = itip.getAttendeeProperties((attendee,))
            assert attendeeProps, "Must have some matching ATTENDEEs"
            for attendeeProp in attendeeProps:
                attendeeProp.params().setdefault("PARTSTAT", ["DECLINED",])[0] = "DECLINED"
        
        # Add REQUEST-STATUS to each top-level component
        itip.addPropertyToAllComponents(Property("REQUEST-STATUS", ["2.0", "Success",]))
        
        # Strip out unwanted bits
        iTipGenerator.prepareSchedulingMessage(itip, reply=True)

        return itip

    @staticmethod
    def prepareSchedulingMessage(itip, reply=False):
        """
        Remove properties and parameters that should not be sent in an iTIP message
        """

        # Alarms
        itip.removeAlarms()

        # Top-level properties - remove all X-
        itip.removeXProperties(do_subcomponents=False)
                
        # Component properties - remove all X- except for those specified
        if not reply:
            # Organizer properties that need to go to the Attendees
            keep_properties = ("X-APPLE-DROPBOX",)
        else:
            # Attendee properties that need to go to the Organizer
            keep_properties = ("X-CALENDARSERVER-PRIVATE-COMMENT",)
        itip.removeXProperties(keep_properties=keep_properties)
        
        # Property Parameters
        itip.removePropertyParameters("ATTENDEE", ("SCHEDULE-AGENT", "SCHEDULE-STATUS",))
        itip.removePropertyParameters("ORGANIZER", ("SCHEDULE-AGENT", "SCHEDULE-STATUS",))

class iTIPRequestStatus(object):
    """
    String constants for various iTIP status codes we use.
    """
    
    MESSAGE_PENDING         = "1.0;Scheduling message send is pending"
    MESSAGE_SENT            = "1.1;Scheduling message has been sent"
    MESSAGE_DELIVERED       = "1.2;Scheduling message has been delivered"
    
    SUCCESS                 = "2.0;Success"

    INVALID_CALENDAR_USER   = "3.7;Invalid Calendar User"
    NO_AUTHORITY            = "3.8;No authority"

    BAD_REQUEST             = "5.0;Service cannot handle request"
    SERVICE_UNAVAILABLE     = "5.1;Service unavailable"
    INVALID_SERVICE         = "5.2;Invalid calendar service"
    NO_USER_SUPPORT         = "5.3;No scheduling support for user"
