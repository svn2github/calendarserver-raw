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
XML based augment configuration file handling.
"""

__all__ = [
    "XMLAugmentsParser",
]

from xml.etree.ElementTree import ElementTree
from xml.parsers.expat import ExpatError
import types

from twext.log import Logger

log = Logger()

ELEMENT_AUGMENTS          = "augments"
ELEMENT_RECORD            = "record"

ELEMENT_UID               = "uid"
ELEMENT_ENABLE            = "enable"
ELEMENT_HOSTEDAT          = "hosted-at"
ELEMENT_ENABLEADDRESSBOOK = "enable-addressbook"
ELEMENT_ENABLECALENDAR    = "enable-calendar"
ELEMENT_AUTOSCHEDULE      = "auto-schedule"

ATTRIBUTE_REPEAT          = "repeat"

VALUE_TRUE                = "true"
VALUE_FALSE               = "false"

ELEMENT_AUGMENTRECORD_MAP = {
    ELEMENT_UID:               "uid",
    ELEMENT_ENABLE:            "enabled",
    ELEMENT_HOSTEDAT:          "hostedAt",
    ELEMENT_ENABLECALENDAR:    "enabledForCalendaring",
    ELEMENT_ENABLEADDRESSBOOK: "enabledForAddressBooks",
    ELEMENT_AUTOSCHEDULE:      "autoSchedule",
}

class XMLAugmentsParser(object):
    """
    XML augments configuration file parser.
    """
    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.xmlFile)

    def __init__(self, xmlFile, items):

        self.items = items
        self.xmlFile = xmlFile

        # Read in XML
        try:
            tree = ElementTree(file=self.xmlFile)
        except ExpatError, e:
            log.error("Unable to parse file '%s' because: %s" % (self.xmlFile, e,), raiseException=RuntimeError)

        # Verify that top-level element is correct
        augments_node = tree.getroot()
        if augments_node.tag != ELEMENT_AUGMENTS:
            log.error("Ignoring file '%s' because it is not a augments file" % (self.xmlFile,), raiseException=RuntimeError)

        self._parseXML(augments_node)

    def _parseXML(self, rootnode):
        """
        Parse the XML root node from the augments configuration document.
        @param rootnode: the L{Element} to parse.
        """
        for child in rootnode.getchildren():
            
            if child.tag != ELEMENT_RECORD:
                log.error("Unknown augment type: '%s' in augment file: '%s'" % (child.tag, self.xmlFile,), raiseException=RuntimeError)

            repeat = int(child.get(ATTRIBUTE_REPEAT, "1"))

            fields = {}
            for node in child.getchildren():
                
                if node.tag in (
                    ELEMENT_UID,
                    ELEMENT_HOSTEDAT,
                ):
                    fields[node.tag] = node.text
                elif node.tag in (
                    ELEMENT_ENABLE,
                    ELEMENT_ENABLECALENDAR,
                    ELEMENT_AUTOSCHEDULE,
                    ELEMENT_ENABLEADDRESSBOOK
                ):
                    fields[node.tag] = node.text == VALUE_TRUE
                else:
                    log.error("Invalid element '%s' in augment file: '%s'" % (node.tag, self.xmlFile,), raiseException=RuntimeError)
                    
            # Must have at least a uid
            if ELEMENT_UID not in fields:
                log.error("Invalid record '%s' without a uid in augment file: '%s'" % (child, self.xmlFile,), raiseException=RuntimeError)
                
            if repeat > 1:
                for i in xrange(1, repeat+1):
                    self.buildRecord(fields, i)
            else:
                self.buildRecord(fields)
    
    def buildRecord(self, fields, count=None):
        
        from twistedcaldav.directory.augment import AugmentRecord

        def expandCount(value, count):
            
            if type(value) in types.StringTypes:
                return value % (count,) if count and "%" in value else value
            elif type(value) == set:
                return set([item % (count,) if count and "%" in item else item for item in value])
            else:
                return value
        
        actualFields = {}
        for k,v in fields.iteritems():
            actualFields[ELEMENT_AUGMENTRECORD_MAP[k]] = expandCount(v, count)

        record = AugmentRecord(**actualFields)
        self.items[record.uid] = record
