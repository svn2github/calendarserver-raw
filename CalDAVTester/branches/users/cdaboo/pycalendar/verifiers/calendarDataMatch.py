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

from difflib import unified_diff
from pycalendar.calendar import PyCalendar
from pycalendar.attribute import PyCalendarAttribute

"""
Verifier that checks the response body for an exact match to data in a file.
"""

class Verifier(object):
    
    def verify(self, manager, uri, response, respdata, args): #@UnusedVariable
        # Get arguments
        files = args.get("filepath", [])
        caldata = args.get("data", [])
        filters = args.get("filter", [])
        
        if "EMAIL parameter" not in manager.server_info.features:
            filters.append("ATTENDEE:EMAIL") 
            filters.append("ORGANIZER:EMAIL")
        filters.append("CALSCALE")
        filters.append("PRODID")
        filters.append("CREATED")
        filters.append("LAST-MODIFIED")
 
        # status code must be 200, 207
        if response.status not in (200,207):
            return False, "        HTTP Status Code Wrong: %d" % (response.status,)
        
        # look for response data
        if not respdata:
            return False, "        No response body"
        
        # look for one file
        if len(files) != 1 and len(caldata) != 1:
            return False, "        No file to compare response to"
        
        # read in all data from specified file or use provided data
        if len(files):
            fd = open( files[0], "r" )
            try:
                try:
                    data = fd.read()
                finally:
                    fd.close()
            except:
                data = None
        else:
            data = caldata[0] if len(caldata) else None

        if data is None:
            return False, "        Could not read data file"

        data = manager.server_info.subs(data)
        
        def removePropertiesParameters(component):
            
            for subcomponent in component.getComponents():
                removePropertiesParameters(subcomponent)

            allProps = []
            for properties in component.getProperties().itervalues():
                allProps.extend(properties)
            for property in allProps:                    
                # Always remove DTSTAMP
                if property.getName() == "DTSTAMP":
                    component.removeProperty(property)
                elif property.getName() == "PRODID":
                    component.removeProperty(property)
                elif property.getName() == "X-CALENDARSERVER-ATTENDEE-COMMENT":
                    if property.hasAttribute("X-CALENDARSERVER-DTSTAMP"):
                        property.replaceAttribute(PyCalendarAttribute("X-CALENDARSERVER-DTSTAMP", "20080101T000000Z"))
                        
                for filter in filters:
                    if ":" in filter:
                        propname, parameter = filter.split(":")
                        if property.getName() == propname:
                            if property.hasAttribute(parameter):
                                property.removeAttributes(parameter)
                    else:
                        if property.getName() == filter:
                            component.removeProperty(property)

        try:
            resp_calendar = PyCalendar.parseText(respdata)
            removePropertiesParameters(resp_calendar)
            respdata = resp_calendar.getText()
            
            data_calendar = PyCalendar.parseText(data)
            removePropertiesParameters(data_calendar)
            data = data_calendar.getText()
            
            result = respdata == data
                    
            if result:
                return True, ""
            else:
                error_diff = "\n".join([line for line in unified_diff(data.split("\n"), respdata.split("\n"))])
                return False, "        Response data does not exactly match file data%s" % (error_diff,)
        except Exception, e:
            return False, "        Response data is not calendar data data: %s" % (e,)
            