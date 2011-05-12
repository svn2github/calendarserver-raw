##
#    Copyright (c) 2007-2011 Cyrus Daboo. All rights reserved.
#    
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#    
#        http://www.apache.org/licenses/LICENSE-2.0
#    
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
##

from pycalendar import definitions
from pycalendar.attribute import PyCalendarAttribute
from pycalendar.component import PyCalendarComponent
from pycalendar.datetime import PyCalendarDateTime
from pycalendar.duration import PyCalendarDuration
from pycalendar.property import PyCalendarProperty
from pycalendar.value import PyCalendarValue

class PyCalendarVAlarm(PyCalendarComponent):

    # Classes for each action encapsulating action-specific data
    class PyCalendarVAlarmAction(object):

        def __init__(self, type):
            self.mType = type

        def duplicate(self):
            return PyCalendarVAlarm.PyCalendarVAlarmAction(self.mType)

        def load(self, valarm):
            pass

        def add(self, valarm):
            pass

        def remove(self, valarm):
            pass

        def getType(self):
            return self.mType

    class PyCalendarVAlarmAudio(PyCalendarVAlarmAction):

        def __init__(self, speak=None):
            super(PyCalendarVAlarm.PyCalendarVAlarmAudio, self).__init__(type=definitions.eAction_VAlarm_Audio)
            self.mSpeakText = speak

        def duplicate(self):
            return PyCalendarVAlarm.PyCalendarVAlarmAudio(self.mSpeakText)

        def load(self, valarm):
            # Get properties
            self.mSpeakText = valarm.loadValueString(definitions.cICalProperty_ACTION_X_SPEAKTEXT)

        def add(self, valarm):
            # Delete existing then add
            self.remove(valarm)

            prop = PyCalendarProperty(definitions.cICalProperty_ACTION_X_SPEAKTEXT, self.mSpeakText)
            valarm.addProperty(prop)

        def remove(self, valarm):
            valarm.removeProperties(definitions.cICalProperty_ACTION_X_SPEAKTEXT)

        def isSpeakText(self):
            return len(self.mSpeakText) != 0

        def getSpeakText(self):
            return self.mSpeakText

    class PyCalendarVAlarmDisplay(PyCalendarVAlarmAction):

        def __init__(self, description=None):
            super(PyCalendarVAlarm.PyCalendarVAlarmDisplay, self).__init__(type=definitions.eAction_VAlarm_Display)
            self.mDescription = description

        def duplicate(self):
            return PyCalendarVAlarm.PyCalendarVAlarmDisplay(self.mDescription)

        def load(self, valarm):
            # Get properties
            self.mDescription = valarm.loadValueString(definitions.cICalProperty_DESCRIPTION)

        def add(self, valarm):
            # Delete existing then add
            self.remove(valarm)

            prop = PyCalendarProperty(definitions.cICalProperty_DESCRIPTION, self.mDescription)
            valarm.addProperty(prop)

        def remove(self, valarm):
            valarm.removeProperties(definitions.cICalProperty_DESCRIPTION)

        def getDescription(self):
            return self.mDescription

    class PyCalendarVAlarmEmail(PyCalendarVAlarmAction):

        def __init__(self, description=None, summary=None, attendees=None):
            super(PyCalendarVAlarm.PyCalendarVAlarmEmail, self).__init__(type=definitions.eAction_VAlarm_Display)
            self.mDescription = description
            self.mSummary = summary
            self.mAttendees = attendees

        def duplicate(self):
            return PyCalendarVAlarm.PyCalendarVAlarmEmail(self.mDescription, self.mSummary, self.mAttendees)

        def load(self, valarm):
            # Get properties
            self.mDescription = valarm.loadValueString(definitions.cICalProperty_DESCRIPTION)
            self.mSummary = valarm.loadValueString(definitions.cICalProperty_SUMMARY)

            self.mAttendees = []
            if valarm.hasProperty(definitions.cICalProperty_ATTENDEE):
                # Get each attendee
                range = valarm.getProperties().get(definitions.cICalProperty_ATTENDEE, ())
                for iter in range:
                    # Get the attendee value
                    attendee = iter.getCalAddressValue()
                    if attendee is not None:
                        self.mAttendees.append(attendee.getValue())

        def add(self, valarm):
            # Delete existing then add
            self.remove(valarm)

            prop = PyCalendarProperty(definitions.cICalProperty_DESCRIPTION, self.mDescription)
            valarm.addProperty(prop)

            prop = PyCalendarProperty(definitions.cICalProperty_SUMMARY, self.mSummary)
            valarm.addProperty(prop)

            for iter in self.mAttendees:
                prop = PyCalendarProperty(definitions.cICalProperty_ATTENDEE, iter, PyCalendarValue.VALUETYPE_CALADDRESS)
                valarm.addProperty(prop)

        def remove(self, valarm):
            valarm.removeProperties(definitions.cICalProperty_DESCRIPTION)
            valarm.removeProperties(definitions.cICalProperty_SUMMARY)
            valarm.removeProperties(definitions.cICalProperty_ATTENDEE)

        def getDescription(self):
            return self.mDescription

        def getSummary(self):
            return self.mSummary

        def getAttendees(self):
            return self.mAttendees

    class PyCalendarVAlarmUnknown(PyCalendarVAlarmAction):

        def __init__(self):
            super(PyCalendarVAlarm.PyCalendarVAlarmUnknown, self).__init__(type=definitions.eAction_VAlarm_Unknown)

        def duplicate(self):
            return PyCalendarVAlarm.PyCalendarVAlarmUnknown()

        def load(self, valarm):
            pass

        def add(self, valarm):
            pass

        def remove(self, valarm):
            pass

    def getMimeComponentName(self):
        # Cannot be sent as a separate MIME object
        return None

    def __init__(self, parent=None):
        
        super(PyCalendarVAlarm, self).__init__(parent=parent)

        self.mAction = definitions.eAction_VAlarm_Display
        self.mTriggerAbsolute = False
        self.mTriggerOnStart = True
        self.mTriggerOn = PyCalendarDateTime()

        # Set duration default to 1 hour
        self.mTriggerBy = PyCalendarDuration()
        self.mTriggerBy.setDuration(60 * 60)

        # Does not repeat by default
        self.mRepeats = 0
        self.mRepeatInterval = PyCalendarDuration()
        self.mRepeatInterval.setDuration(5 * 60) # Five minutes

        # Status
        self.mStatusInit = False
        self.mAlarmStatus = definitions.eAlarm_Status_Pending
        self.mLastTrigger = PyCalendarDateTime()
        self.mNextTrigger = PyCalendarDateTime()
        self.mDoneCount = 0

        # Create action data
        self.mActionData = PyCalendarVAlarm.PyCalendarVAlarmDisplay("")

    def duplicate(self, parent=None):
        other = super(PyCalendarVAlarm, self).duplicate(parent=parent)
        other.mAction = self.mAction
        other.mTriggerAbsolute = self.mTriggerAbsolute
        other.mTriggerOn = self.mTriggerOn.duplicate()
        other.mTriggerBy = self.mTriggerBy.duplicate()
        other.mTriggerOnStart = self.mTriggerOnStart

        other.mRepeats = self.mRepeats
        other.mRepeatInterval = self.mRepeatInterval.duplicate()

        other.mAlarmStatus = self.mAlarmStatus
        if self.mLastTrigger is not None:
            other.mLastTrigger = self.mLastTrigger.duplicate()
        if self.mNextTrigger is not None:
            other.mNextTrigger = self.mNextTrigger.duplicate()
        other.mDoneCount = self.mDoneCount

        other.mActionData = self.mActionData.duplicate()
        return other

    def getType(self):
        return definitions.cICalComponent_VALARM

    def getAction(self):
        return self.mAction

    def getActionData(self):
        return self.mActionData

    def isTriggerAbsolute(self):
        return self.mTriggerAbsolute

    def getTriggerOn(self):
        return self.mTriggerOn

    def getTriggerDuration(self):
        return self.mTriggerBy
  
    def isTriggerOnStart(self):
        return self.mTriggerOnStart

    def getRepeats(self):
        return self.mRepeats

    def getInterval(self):
        return self.mRepeatInterval

    def added(self):
        # Added to calendar so add to calendar notifier
        # calstore::CCalendarNotifier::sCalendarNotifier.AddAlarm(this)

        # Do inherited
        super(PyCalendarVAlarm, self).added()

    def removed(self):
        # Removed from calendar so add to calendar notifier
        # calstore::CCalendarNotifier::sCalendarNotifier.RemoveAlarm(this)

        # Do inherited
        super(PyCalendarVAlarm, self).removed()

    def changed(self):
        # Always force recalc of trigger status
        self.mStatusInit = False

        # Changed in calendar so change in calendar notifier
        # calstore::CCalendarNotifier::sCalendarNotifier.ChangedAlarm(this)

        # Do not do inherited as this is always a sub-component and we do not
        # do top-level component changes
        # super.changed()

    def finalise(self):
        # Do inherited
        super(PyCalendarVAlarm, self).finalise()

        # Get the ACTION
        temp = self.loadValueString(definitions.cICalProperty_ACTION)
        if temp is not None:
            if temp == definitions.cICalProperty_ACTION_AUDIO:
                self.mAction = definitions.eAction_VAlarm_Audio
            elif temp == definitions.cICalProperty_ACTION_DISPLAY:
                self.mAction = definitions.eAction_VAlarm_Display
            elif temp == definitions.cICalProperty_ACTION_EMAIL:
                self.mAction = definitions.eAction_VAlarm_Email
            elif temp == definitions.cICalProperty_ACTION_PROCEDURE:
                self.mAction = definitions.eAction_VAlarm_Procedure
            else:
                self.mAction = definitions.eAction_VAlarm_Unknown

            self.loadAction()

        # Get the trigger
        if self.hasProperty(definitions.cICalProperty_TRIGGER):
            # Determine the type of the value
            temp1 = self.loadValueDateTime(definitions.cICalProperty_TRIGGER)
            temp2 = self.loadValueDuration(definitions.cICalProperty_TRIGGER)
            if temp1 is not None:
                self.mTriggerAbsolute = True
                self.mTriggerOn = temp1
            elif temp2 is not None:
                self.mTriggerAbsolute = False
                self.mTriggerBy = temp2

                # Get the property
                prop = self.findFirstProperty(definitions.cICalProperty_TRIGGER)

                # Look for RELATED attribute
                if prop.hasAttribute(definitions.cICalAttribute_RELATED):
                    temp = prop.getAttributeValue(definitions.cICalAttribute_RELATED)
                    if temp == definitions.cICalAttribute_RELATED_START:
                        self.mTriggerOnStart = True
                    elif temp == definitions.cICalAttribute_RELATED_END:
                        self.mTriggerOnStart = False
                else:
                    self.mTriggerOnStart = True

        # Get repeat & interval
        temp = self.loadValueInteger(definitions.cICalProperty_REPEAT)
        if temp is not None:
            self.mRepeats = temp
        temp = self.loadValueDuration(definitions.cICalProperty_DURATION)
        if temp is not None:
            self.mRepeatInterval = temp

        # Alarm status - private to Mulberry
        status = self.loadValueString(definitions.cICalProperty_ALARM_X_ALARMSTATUS)
        if status is not None:
            if status == definitions.cICalProperty_ALARM_X_ALARMSTATUS_PENDING:
                self.mAlarmStatus = definitions.eAlarm_Status_Pending
            elif status == definitions.cICalProperty_ALARM_X_ALARMSTATUS_COMPLETED:
                self.mAlarmStatus = definitions.eAlarm_Status_Completed
            elif status == definitions.cICalProperty_ALARM_X_ALARMSTATUS_DISABLED:
                self.mAlarmStatus = definitions.eAlarm_Status_Disabled
            else:
                self.mAlarmStatus = definitions.eAlarm_Status_Pending

        # Last trigger time - private to Mulberry
        temp = self.loadValueDateTime(definitions.cICalProperty_ALARM_X_LASTTRIGGER)
        if temp is not None:
            self.mLastTrigger = temp

    def editStatus(self, status):
        # Remove existing
        self.removeProperties(definitions.cICalProperty_ALARM_X_ALARMSTATUS)

        # Updated cached values
        self.mAlarmStatus = status

        # Add new
        status_txt = ""
        if self.mAlarmStatus == definitions.eAlarm_Status_Pending:
            status_txt = definitions.cICalProperty_ALARM_X_ALARMSTATUS_PENDING
        elif self.mAlarmStatus == definitions.eAlarm_Status_Completed:
            status_txt = definitions.cICalProperty_ALARM_X_ALARMSTATUS_COMPLETED
        elif self.mAlarmStatus == definitions.eAlarm_Status_Disabled:
            status_txt = definitions.cICalProperty_ALARM_X_ALARMSTATUS_DISABLED
        self.addProperty(PyCalendarProperty(definitions.cICalProperty_ALARM_X_ALARMSTATUS, status_txt))

    def editAction(self, action, data):
        # Remove existing
        self.removeProperties(definitions.cICalProperty_ACTION)
        self.mActionData.remove(self)
        self.mActionData = None

        # Updated cached values
        self.mAction = action
        self.mActionData = data

        # Add new properties to alarm
        action_txt = ""
        if self.mAction == definitions.eAction_VAlarm_Audio:
            action_txt = definitions.cICalProperty_ACTION_AUDIO
        elif self.mAction == definitions.eAction_VAlarm_Display:
            action_txt = definitions.cICalProperty_ACTION_DISPLAY
        elif self.mAction == definitions.eAction_VAlarm_Email:
            action_txt = definitions.cICalProperty_ACTION_EMAIL
        else:
            action_txt = definitions.cICalProperty_ACTION_PROCEDURE

        prop = PyCalendarProperty(definitions.cICalProperty_ACTION, action_txt)
        self.addProperty(prop)

        self.mActionData.add(self)

    def editTriggerOn(self, dt):
        # Remove existing
        self.removeProperties(definitions.cICalProperty_TRIGGER)

        # Updated cached values
        self.mTriggerAbsolute = True
        self.mTriggerOn = dt

        # Add new
        prop = PyCalendarProperty(definitions.cICalProperty_TRIGGER, dt)
        self.addProperty(prop)

    def editTriggerBy(self, duration, trigger_start):
        # Remove existing
        self.removeProperties(definitions.cICalProperty_TRIGGER)

        # Updated cached values
        self.mTriggerAbsolute = False
        self.mTriggerBy = duration
        self.mTriggerOnStart = trigger_start

        # Add new (with attribute)
        prop = PyCalendarProperty(definitions.cICalProperty_TRIGGER, duration)
        attr = PyCalendarAttribute(definitions.cICalAttribute_RELATED,
                 (definitions.cICalAttribute_RELATED_START,
                  definitions.cICalAttribute_RELATED_END)[not trigger_start])
        prop.addAttribute(attr)
        self.addProperty(prop)

    def editRepeats(self, repeat, interval):
        # Remove existing
        self.removeProperties(definitions.cICalProperty_REPEAT)
        self.removeProperties(definitions.cICalProperty_DURATION)

        # Updated cached values
        self.mRepeats = repeat
        self.mRepeatInterval = interval

        # Add new
        if self.mRepeats > 0:
            self.addProperty(PyCalendarProperty(definitions.cICalProperty_REPEAT, repeat))
            self.addProperty(PyCalendarProperty(definitions.cICalProperty_DURATION, interval))

    def getAlarmStatus(self):
        return self.mAlarmStatus

    def getNextTrigger(self, dt):
        if not self.mStatusInit:
            self.initNextTrigger()
        dt.copy(self.mNextTrigger)

    def alarmTriggered(self, dt):
        # Remove existing
        self.removeProperties(definitions.cICalProperty_ALARM_X_LASTTRIGGER)
        self.removeProperties(definitions.cICalProperty_ALARM_X_ALARMSTATUS)

        # Updated cached values
        self.mLastTrigger.copy(dt)

        if self.mDoneCount < self.mRepeats:
            self.mNextTrigger = self.mLastTrigger + self.mRepeatInterval
            dt.copy(self.mNextTrigger)
            self.mDoneCount += 1
            self.mAlarmStatus = definitions.eAlarm_Status_Pending
        else:
            self.mAlarmStatus = definitions.eAlarm_Status_Completed

        # Add new
        self.addProperty(PyCalendarProperty(definitions.cICalProperty_ALARM_X_LASTTRIGGER, dt))
        status = ""
        if self.mAlarmStatus == definitions.eAlarm_Status_Pending:
            status = definitions.cICalProperty_ALARM_X_ALARMSTATUS_PENDING
        elif self.mAlarmStatus == definitions.eAlarm_Status_Completed:
            status = definitions.cICalProperty_ALARM_X_ALARMSTATUS_COMPLETED
        elif self.mAlarmStatus == definitions.eAlarm_Status_Disabled:
            status = definitions.cICalProperty_ALARM_X_ALARMSTATUS_DISABLED
        self.addProperty(PyCalendarProperty(definitions.cICalProperty_ALARM_X_ALARMSTATUS, status))

        # Now update dt to the next alarm time
        return self.mAlarmStatus == definitions.eAlarm_Status_Pending

    def loadAction(self):
        # Delete current one
        self.mActionData = None
        if self.mAction == definitions.eAction_VAlarm_Audio:
            self.mActionData = PyCalendarVAlarm.PyCalendarVAlarmAudio()
            self.mActionData.load(self)
        elif self.mAction == definitions.eAction_VAlarm_Display:
            self.mActionData = PyCalendarVAlarm.PyCalendarVAlarmDisplay()
            self.mActionData.load(self)
        elif self.mAction == definitions.eAction_VAlarm_Email:
            self.mActionData = PyCalendarVAlarm.PyCalendarVAlarmEmail()
            self.mActionData.load(self)
        else:
            self.mActionData = PyCalendarVAlarm.PyCalendarVAlarmUnknown()
            self.mActionData.load(self)

    def initNextTrigger(self):
        # Do not bother if its completed
        if self.mAlarmStatus == definitions.eAlarm_Status_Completed:
            return
        self.mStatusInit = True

        # Look for trigger immediately preceeding or equal to utc now
        nowutc = PyCalendarDateTime.getNowUTC()

        # Init done counter
        self.mDoneCount = 0

        # Determine the first trigger
        trigger = PyCalendarDateTime()
        self.getFirstTrigger(trigger)

        while self.mDoneCount < self.mRepeats:
            # See if next trigger is later than now
            next_trigger = trigger + self.mRepeatInterval
            if next_trigger > nowutc:
                break
            self.mDoneCount += 1
            trigger = next_trigger

        # Check for completion
        if trigger == self.mLastTrigger or (nowutc - trigger).getTotalSeconds() > 24 * 60 * 60:
            if self.mDoneCount == self.mRepeats:
                self.mAlarmStatus = definitions.eAlarm_Status_Completed
                return
            else:
                trigger = trigger + self.mRepeatInterval
                self.mDoneCount += 1

        self.mNextTrigger = trigger

    def getFirstTrigger(self, dt):
        # If absolute trigger, use that
        if self.isTriggerAbsolute():
            # Get the trigger on
            dt.copy(self.getTriggerOn())
        else:
            # Get the parent embedder class (must be CICalendarComponentRecur type)
            owner = self.getEmbedder()
            if owner is not None:
                # Determine time at which alarm will trigger
                trigger = (owner.getStart(), owner.getEnd())[not self.isTriggerOnStart()]

                # Offset by duration
                dt.copy(trigger + self.getTriggerDuration())
