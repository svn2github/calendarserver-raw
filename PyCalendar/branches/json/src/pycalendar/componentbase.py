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

from cStringIO import StringIO
from pycalendar import xmldefinitions, xmlutils
from pycalendar.datetimevalue import DateTimeValue
from pycalendar.periodvalue import PeriodValue
from pycalendar.value import Value
import xml.etree.cElementTree as XML

class ComponentBase(object):

    # These are class attributes for sets of properties for testing cardinality constraints. The sets
    # must contain property names.
    propertyCardinality_1 = ()           # Must be present
    propertyCardinality_1_Fix_Empty = () # Must be present but can be fixed by adding an empty value
    propertyCardinality_0_1 = ()         # 0 or 1 only
    propertyCardinality_1_More = ()      # 1 or more

    propertyValueChecks = None  # Either iCalendar or vCard validation

    sortSubComponents = True

    sComponentType = None
    sPropertyType = None

    def __init__(self, parent=None):
        self.mParentComponent = parent
        self.mComponents = []
        self.mProperties = {}

        # This is the set of checks we do by default for components
        self.cardinalityChecks = (
            self.check_cardinality_1,
            self.check_cardinality_1_Fix_Empty,
            self.check_cardinality_0_1,
            self.check_cardinality_1_More,
        )


    def duplicate(self, **args):
        other = self.__class__(**args)

        for component in self.mComponents:
            other.addComponent(component.duplicate(parent=other))

        other.mProperties = {}
        for propname, props in self.mProperties.iteritems():
            other.mProperties[propname] = [i.duplicate() for i in props]
        return other


    def __str__(self):
        return self.getText()


    def __ne__(self, other):
        return not self.__eq__(other)


    def __eq__(self, other):
        if not isinstance(other, ComponentBase):
            return False
        return self.getType() == other.getType() and self.compareProperties(other) and self.compareComponents(other)


    def getType(self):
        raise NotImplementedError


    def getBeginDelimiter(self):
        return "BEGIN:" + self.getType()


    def getEndDelimiter(self):
        return "END:" + self.getType()


    def getSortKey(self):
        return ""


    def getParentComponent(self):
        return self.mParentComponent


    def setParentComponent(self, parent):
        self.mParentComponent = parent


    def compareComponents(self, other):
        mine = set(self.mComponents)
        theirs = set(other.mComponents)

        for item in mine:
            for another in theirs:
                if item == another:
                    theirs.remove(another)
                    break
            else:
                return False
        return len(theirs) == 0


    def getComponents(self, compname=None):
        compname = compname.upper() if compname else None
        return [component for component in self.mComponents if compname is None or component.getType().upper() == compname]


    def getComponentByKey(self, key):
        for component in self.mComponents:
            if component.getMapKey() == key:
                return component
        else:
            return None


    def removeComponentByKey(self, key):

        for component in self.mComponents:
            if component.getMapKey() == key:
                self.removeComponent(component)
                return


    def addComponent(self, component):
        self.mComponents.append(component)


    def hasComponent(self, compname):
        return self.countComponents(compname) != 0


    def countComponents(self, compname):
        return len(self.getComponents(compname))


    def removeComponent(self, component):
        self.mComponents.remove(component)


    def removeAllComponent(self, compname=None):
        if compname:
            compname = compname.upper()
            for component in tuple(self.mComponents):
                if component.getType().upper() == compname:
                    self.removeComponent(component)
        else:
            self.mComponents = []


    def sortedComponentNames(self):
        return ()


    def compareProperties(self, other):
        mine = set()
        for props in self.mProperties.values():
            mine.update(props)
        theirs = set()
        for props in other.mProperties.values():
            theirs.update(props)
        return mine == theirs


    def getProperties(self, propname=None):
        return self.mProperties.get(propname.upper(), []) if propname else self.mProperties


    def setProperties(self, props):
        self.mProperties = props


    def addProperty(self, prop):
        self.mProperties.setdefault(prop.getName().upper(), []).append(prop)


    def hasProperty(self, propname):
        return propname.upper() in self.mProperties


    def countProperty(self, propname):
        return len(self.mProperties.get(propname.upper(), []))


    def findFirstProperty(self, propname):
        return self.mProperties.get(propname.upper(), [None])[0]


    def removeProperty(self, prop):
        if prop.getName().upper() in self.mProperties:
            self.mProperties[prop.getName().upper()].remove(prop)
            if len(self.mProperties[prop.getName().upper()]) == 0:
                del self.mProperties[prop.getName().upper()]


    def removeProperties(self, propname):
        if propname.upper() in self.mProperties:
            del self.mProperties[propname.upper()]


    def getPropertyInteger(self, prop, type=None):
        return self.loadValueInteger(prop, type)


    def getPropertyString(self, prop):
        return self.loadValueString(prop)


    def getProperty(self, prop, value):
        return self.loadValue(prop, value)


    def finalise(self):
        raise NotImplemented


    def validate(self, doFix=False):
        """
        Validate the data in this component and optionally fix any problems. Return
        a tuple containing two lists: the first describes problems that were fixed, the
        second problems that were not fixed. Caller can then decide what to do with unfixed
        issues.
        """

        fixed = []
        unfixed = []

        # Cardinality tests
        for check in self.cardinalityChecks:
            check(fixed, unfixed, doFix)

        # Value constraints - these tests come from class specific attributes
        if self.propertyValueChecks is not None:
            for properties in self.mProperties.values():
                for property in properties:
                    propname = property.getName().upper()
                    if propname in self.propertyValueChecks:
                        if not self.propertyValueChecks[propname](property):
                            # Cannot fix a bad property value
                            logProblem = "[%s] Property value incorrect: %s" % (self.getType(), propname,)
                            unfixed.append(logProblem)

        # Validate all subcomponents
        for component in self.mComponents:
            morefixed, moreunfixed = component.validate(doFix)
            fixed.extend(morefixed)
            unfixed.extend(moreunfixed)

        return fixed, unfixed


    def check_cardinality_1(self, fixed, unfixed, doFix):
        for propname in self.propertyCardinality_1:
            if self.countProperty(propname) != 1: # Cannot fix a missing required property
                logProblem = "[%s] Missing or too many required property: %s" % (self.getType(), propname)
                unfixed.append(logProblem)


    def check_cardinality_1_Fix_Empty(self, fixed, unfixed, doFix):
        for propname in self.propertyCardinality_1_Fix_Empty:
            if self.countProperty(propname) > 1: # Cannot fix too many required property
                logProblem = "[%s] Too many required property: %s" % (self.getType(), propname)
                unfixed.append(logProblem)
            elif self.countProperty(propname) == 0: # Possibly fix by adding empty property
                logProblem = "[%s] Missing required property: %s" % (self.getType(), propname)
                if doFix:
                    self.addProperty(self.sPropertyType(propname, ""))
                    fixed.append(logProblem)
                else:
                    unfixed.append(logProblem)


    def check_cardinality_0_1(self, fixed, unfixed, doFix):
        for propname in self.propertyCardinality_0_1:
            if self.countProperty(propname) > 1: # Cannot be fixed - no idea which one to delete
                logProblem = "[%s] Too many properties present: %s" % (self.getType(), propname)
                unfixed.append(logProblem)


    def check_cardinality_1_More(self, fixed, unfixed, doFix):
        for propname in self.propertyCardinality_1_More:
            if not self.countProperty(propname) > 0: # Cannot fix a missing required property
                logProblem = "[%s] Missing required property: %s" % (self.getType(), propname)
                unfixed.append(logProblem)


    def getText(self):
        s = StringIO()
        self.generate(s)
        return s.getvalue()


    def generate(self, os):
        # Header
        os.write(self.getBeginDelimiter())
        os.write("\r\n")

        # Write each property
        self.writeProperties(os)

        # Write each embedded component based on specific order
        self.writeComponents(os)

        # Footer
        os.write(self.getEndDelimiter())
        os.write("\r\n")


    def generateFiltered(self, os, filter):
        # Header
        os.write(self.getBeginDelimiter())
        os.write("\r\n")

        # Write each property
        self.writePropertiesFiltered(os, filter)

        # Write each embedded component based on specific order
        self.writeComponentsFiltered(os, filter)

        # Footer
        os.write(self.getEndDelimiter())
        os.write("\r\n")


    def writeXML(self, node, namespace):

        # Component element
        comp = XML.SubElement(node, xmlutils.makeTag(namespace, self.getType()))

        # Each property
        self.writePropertiesXML(comp, namespace)

        # Each component
        self.writeComponentsXML(comp, namespace)


    def writeXMLFiltered(self, node, namespace, filter):
        # Component element
        comp = XML.SubElement(node, xmlutils.makeTag(namespace, self.getType()))

        # Each property
        self.writePropertiesFilteredXML(comp, namespace, filter)

        # Each component
        self.writeComponentsFilteredXML(comp, namespace, filter)


    @classmethod
    def parseJSON(cls, jobject, parent, comp=None):
        """
        Parse the JSON object which has the form:

        [name, properties, subcomponents]

        @param jobject: a JSON array
        @type jobject: C{list}
        """
        # [name, properties, subcomponents]

        if comp is None:
            comp = cls.sComponentType.makeComponent(jobject[0].upper(), parent)
        for prop in jobject[1]:
            comp.addProperty(cls.sPropertyType.parseJSON(prop))
        for subcomp in jobject[2]:
            comp.addComponent(cls.sComponentType.parseJSON(subcomp, comp))
        comp.finalise()
        return comp


    def writeJSON(self, jobject):

        # Component element
        comp = [self.getType().lower(), [], []]

        # Each property
        self.writePropertiesJSON(comp[1])

        # Each component
        self.writeComponentsJSON(comp[2])

        jobject.append(comp)


    def writeJSONFiltered(self, jobject, filter):
        # Component element
        comp = [self.getType().lower(), [], []]

        # Each property
        self.writePropertiesFilteredJSON(comp[1], filter)

        # Each component
        self.writeComponentsFilteredJSON(comp[2], filter)

        jobject.append(comp)


    def sortedComponents(self):

        components = self.mComponents[:]
        sortedcomponents = []

        # Write each component based on specific order
        orderedNames = self.sortedComponentNames()
        for name in orderedNames:

            # Group by name then sort by map key (UID/R-ID)
            namedcomponents = []
            for component in tuple(components):
                if component.getType().upper() == name:
                    namedcomponents.append(component)
                    components.remove(component)
            for component in sorted(namedcomponents, key=lambda x: x.getSortKey()):
                sortedcomponents.append(component)

        # Write out the remainder sorted by name, sortKey
        if self.sortSubComponents:
            remainder = sorted(components, key=lambda x: (x.getType().upper(), x.getSortKey(),))
        else:
            remainder = components
        for component in remainder:
            sortedcomponents.append(component)

        return sortedcomponents


    def writeComponents(self, os):

        # Write out the remainder
        for component in self.sortedComponents():
            component.generate(os)


    def writeComponentsFiltered(self, os, filter):
        # Shortcut for all sub-components
        if filter.isAllSubComponents():
            self.writeComponents(os)
        elif filter.hasSubComponentFilters():
            for subcomp in self.sortedcomponents():
                subfilter = filter.getSubComponentFilter(subcomp.getType())
                if subfilter is not None:
                    subcomp.generateFiltered(os, subfilter)


    def writeComponentsXML(self, node, namespace):

        if self.mComponents:
            comps = XML.SubElement(node, xmlutils.makeTag(namespace, xmldefinitions.components))

            # Write out the remainder
            for component in self.sortedComponents():
                component.writeXML(comps, namespace)


    def writeComponentsFilteredXML(self, node, namespace, filter):

        if self.mComponents:
            comps = XML.SubElement(node, xmlutils.makeTag(namespace, xmldefinitions.components))

            # Shortcut for all sub-components
            if filter.isAllSubComponents():
                self.writeXML(comps, namespace)
            elif filter.hasSubComponentFilters():
                for subcomp in self.sortedcomponents():
                    subfilter = filter.getSubComponentFilter(subcomp.getType())
                    if subfilter is not None:
                        subcomp.writeXMLFiltered(comps, namespace, subfilter)


    def writeComponentsJSON(self, jobject):

        if self.mComponents:
            # Write out the remainder
            for component in self.sortedComponents():
                component.writeJSON(jobject)


    def writeComponentsFilteredJSON(self, jobject, filter):

        if self.mComponents:
            # Shortcut for all sub-components
            if filter.isAllSubComponents():
                self.writeJSON(jobject)
            elif filter.hasSubComponentFilters():
                for subcomp in self.sortedcomponents():
                    subfilter = filter.getSubComponentFilter(subcomp.getType())
                    if subfilter is not None:
                        subcomp.writeJSONFiltered(jobject, subfilter)


    def loadValue(self, value_name):
        if self.hasProperty(value_name):
            return self.findFirstProperty(value_name)

        return None


    def loadValueInteger(self, value_name, type=None):
        if type:
            if self.hasProperty(value_name):
                if type == Value.VALUETYPE_INTEGER:
                    ivalue = self.findFirstProperty(value_name).getIntegerValue()
                    if ivalue is not None:
                        return ivalue.getValue()
                elif type == Value.VALUETYPE_UTC_OFFSET:
                    uvalue = self.findFirstProperty(value_name).getUTCOffsetValue()
                    if (uvalue is not None):
                        return uvalue.getValue()

            return None
        else:
            return self.loadValueInteger(value_name, Value.VALUETYPE_INTEGER)


    def loadValueString(self, value_name):
        if self.hasProperty(value_name):
            tvalue = self.findFirstProperty(value_name).getTextValue()
            if (tvalue is not None):
                return tvalue.getValue()

        return None


    def loadValueDateTime(self, value_name):
        if self.hasProperty(value_name):
            dtvalue = self.findFirstProperty(value_name).getDateTimeValue()
            if dtvalue is not None:
                return dtvalue.getValue()

        return None


    def loadValueDuration(self, value_name):
        if self.hasProperty(value_name):
            dvalue = self.findFirstProperty(value_name).getDurationValue()
            if (dvalue is not None):
                return dvalue.getValue()

        return None


    def loadValuePeriod(self, value_name):
        if self.hasProperty(value_name):
            pvalue = self.findFirstProperty(value_name).getPeriodValue()
            if (pvalue is not None):
                return pvalue.getValue()

        return None


    def loadValueRRULE(self, value_name, value, add):
        # Get RRULEs
        if self.hasProperty(value_name):
            items = self.getProperties()[value_name]
            for iter in items:
                rvalue = iter.getRecurrenceValue()
                if (rvalue is not None):
                    if add:
                        value.addRule(rvalue.getValue())
                    else:
                        value.subtractRule(rvalue.getValue())
            return True
        else:
            return False


    def loadValueRDATE(self, value_name, value, add):
        # Get RDATEs
        if self.hasProperty(value_name):
            for iter in self.getProperties(value_name):
                mvalue = iter.getMultiValue()
                if (mvalue is not None):
                    for obj in mvalue.getValues():
                        # cast to date-time
                        if isinstance(obj, DateTimeValue):
                            if add:
                                value.addDT(obj.getValue())
                            else:
                                value.subtractDT(obj.getValue())
                        elif isinstance(obj, PeriodValue):
                            if add:
                                value.addPeriod(obj.getValue().getStart())
                            else:
                                value.subtractPeriod(obj.getValue().getStart())

            return True
        else:
            return False


    def sortedPropertyKeys(self):
        keys = self.mProperties.keys()
        keys.sort()

        results = []
        for skey in self.sortedPropertyKeyOrder():
            if skey in keys:
                results.append(skey)
                keys.remove(skey)
        results.extend(keys)
        return results


    def sortedPropertyKeyOrder(self):
        return ()


    def writeProperties(self, os):
        # Sort properties by name
        keys = self.sortedPropertyKeys()
        for key in keys:
            props = self.mProperties[key]
            for prop in props:
                prop.generate(os)


    def writePropertiesFiltered(self, os, filter):

        # Sort properties by name
        keys = self.sortedPropertyKeys()

        # Shortcut for all properties
        if filter.isAllProperties():
            for key in keys:
                for prop in self.getProperties(key):
                    prop.generate(os)
        elif filter.hasPropertyFilters():
            for key in keys:
                for prop in self.getProperties(key):
                    prop.generateFiltered(os, filter)


    def writePropertiesXML(self, node, namespace):

        properties = XML.SubElement(node, xmlutils.makeTag(namespace, xmldefinitions.properties))

        # Sort properties by name
        keys = self.sortedPropertyKeys()
        for key in keys:
            props = self.mProperties[key]
            for prop in props:
                prop.writeXML(properties, namespace)


    def writePropertiesFilteredXML(self, node, namespace, filter):

        props = XML.SubElement(node, xmlutils.makeTag(namespace, xmldefinitions.properties))

        # Sort properties by name
        keys = self.sortedPropertyKeys()

        # Shortcut for all properties
        if filter.isAllProperties():
            for key in keys:
                for prop in self.getProperties(key):
                    prop.writeXML(props, namespace)
        elif filter.hasPropertyFilters():
            for key in keys:
                for prop in self.getProperties(key):
                    prop.writeXMLFiltered(props, namespace, filter)


    def writePropertiesJSON(self, jobject):

        # Sort properties by name
        keys = self.sortedPropertyKeys()
        for key in keys:
            props = self.mProperties[key]
            for prop in props:
                prop.writeJSON(jobject)


    def writePropertiesFilteredJSON(self, jobject, filter):

        # Sort properties by name
        keys = self.sortedPropertyKeys()

        # Shortcut for all properties
        if filter.isAllProperties():
            for key in keys:
                for prop in self.getProperties(key):
                    prop.writeJSON(jobject)
        elif filter.hasPropertyFilters():
            for key in keys:
                for prop in self.getProperties(key):
                    prop.writeJSONFiltered(jobject, filter)


    def loadPrivateValue(self, value_name):
        # Read it in from properties list and then delete the property from the
        # main list
        result = self.loadValueString(value_name)
        if (result is not None):
            self.removeProperties(value_name)
        return result


    def writePrivateProperty(self, os, key, value):
        prop = self.sPropertyType(name=key, value=value)
        prop.generate(os)


    def editProperty(self, propname, propvalue):

        # Remove existing items
        self.removeProperties(propname)

        # Now create properties
        if propvalue:
            self.addProperty(self.sPropertyType(name=propname, value=propvalue))
