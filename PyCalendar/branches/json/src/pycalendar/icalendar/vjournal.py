##
#    Copyright (c) 2007-2012 Cyrus Daboo. All rights reserved.
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

from pycalendar.icalendar import definitions
from pycalendar.icalendar import itipdefinitions
from pycalendar.icalendar.component import Component
from pycalendar.icalendar.componentrecur import ComponentRecur
from pycalendar.icalendar.validation import ICALENDAR_VALUE_CHECKS

class VJournal(ComponentRecur):

    propertyCardinality_1 = (
        definitions.cICalProperty_DTSTAMP,
        definitions.cICalProperty_UID,
    )

    propertyCardinality_0_1 = (
        definitions.cICalProperty_CLASS,
        definitions.cICalProperty_CREATED,
        definitions.cICalProperty_DTSTART,
        definitions.cICalProperty_LAST_MODIFIED,
        definitions.cICalProperty_ORGANIZER,
        definitions.cICalProperty_RECURRENCE_ID,
        definitions.cICalProperty_SEQUENCE,
        # definitions.cICalProperty_STATUS, # Special fix done for multiple STATUS
        definitions.cICalProperty_SUMMARY,
        definitions.cICalProperty_URL,
        definitions.cICalProperty_RRULE,
    )

    propertyValueChecks = ICALENDAR_VALUE_CHECKS

    def __init__(self, parent=None):
        super(VJournal, self).__init__(parent=parent)


    def duplicate(self, parent=None):
        return super(VJournal, self).duplicate(parent=parent)


    def getType(self):
        return definitions.cICalComponent_VJOURNAL


    def getMimeComponentName(self):
        return itipdefinitions.cICalMIMEComponent_VJOURNAL


    def finalise(self):
        super(VJournal, self).finalise()


    def sortedPropertyKeyOrder(self):
        return (
            definitions.cICalProperty_UID,
            definitions.cICalProperty_RECURRENCE_ID,
            definitions.cICalProperty_DTSTART,
        )

Component.registerComponent(definitions.cICalComponent_VJOURNAL, VJournal)
