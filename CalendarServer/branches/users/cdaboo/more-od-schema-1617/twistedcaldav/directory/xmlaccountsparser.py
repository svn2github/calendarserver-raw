##
# Copyright (c) 2006-2007 Apple Inc. All rights reserved.
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
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##


"""
XML based user/group/resource configuration file handling.
"""

__all__ = [
    "XMLAccountsParser",
]

import xml.dom.minidom

from twisted.python.filepath import FilePath

from twistedcaldav.directory.directory import DirectoryService

ELEMENT_ACCOUNTS     = "accounts"
ELEMENT_USER         = "user"
ELEMENT_GROUP        = "group"
ELEMENT_LOCATION     = "location"
ELEMENT_RESOURCE     = "resource"

ELEMENT_SHORTNAME    = "uid"
ELEMENT_GUID         = "guid"
ELEMENT_PASSWORD     = "password"
ELEMENT_NAME         = "name"
ELEMENT_MEMBERS      = "members"
ELEMENT_MEMBER       = "member"
ELEMENT_CUADDR       = "cuaddr"
ELEMENT_AUTOSCHEDULE = "auto-schedule"

ATTRIBUTE_REALM      = "realm"
ATTRIBUTE_REPEAT     = "repeat"
ATTRIBUTE_RECORDTYPE = "type"

RECORD_TYPES = {
    ELEMENT_USER     : DirectoryService.recordType_users,
    ELEMENT_GROUP    : DirectoryService.recordType_groups,
    ELEMENT_LOCATION : DirectoryService.recordType_locations,
    ELEMENT_RESOURCE : DirectoryService.recordType_resources,
}

class XMLAccountsParser(object):
    """
    XML account configuration file parser.
    """
    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.xmlFile)

    def __init__(self, xmlFile):
        if type(xmlFile) is str:
            xmlFile = FilePath(xmlFile)

        self.xmlFile = xmlFile
        self.realm = None
        self.items = {}
        
        for recordType in RECORD_TYPES.values():
            self.items[recordType] = {}

        # Read in XML
        fd = open(self.xmlFile.path, "r")
        doc = xml.dom.minidom.parse(fd)
        fd.close()

        # Verify that top-level element is correct
        accounts_node = doc._get_documentElement()
        if accounts_node._get_localName() != ELEMENT_ACCOUNTS:
            self.log("Ignoring file %r because it is not a repository builder file" % (self.xmlFile,))
            return
        self._parseXML(accounts_node)
        
    def _parseXML(self, node):
        """
        Parse the XML root node from the accounts configuration document.
        @param node: the L{Node} to parse.
        """
        if node.hasAttribute(ATTRIBUTE_REALM):
            self.realm = node.getAttribute(ATTRIBUTE_REALM).encode("utf-8")

        def updateMembership(group):
            # Update group membership
            for recordType, shortName in group.members:
                item = self.items[recordType].get(shortName, None)
                if item is not None:
                    item.groups.add(group.shortName)

        for child in node._get_childNodes():
            child_name = child._get_localName()
            if child_name is None:
                continue

            try:
                recordType = RECORD_TYPES[child_name]
            except KeyError:
                raise RuntimeError("Unknown account type: %s" % (child_name,))

            if child.hasAttribute(ATTRIBUTE_REPEAT):
                repeat = int(child.getAttribute(ATTRIBUTE_REPEAT))
            else:
                repeat = 1

            principal = XMLAccountRecord(recordType)
            principal.parseXML(child)
            if repeat > 1:
                for i in xrange(1, repeat+1):
                    newprincipal = principal.repeat(i)
                    self.items[recordType][newprincipal.shortName] = newprincipal
                    updateMembership(newprincipal)
            else:
                self.items[recordType][principal.shortName] = principal
                updateMembership(principal)

class XMLAccountRecord (object):
    """
    Contains provision information for one user.
    """
    def __init__(self, recordType):
        """
        @param recordType: record type for directory entry.
        """
        self.recordType = recordType
        self.shortName = None
        self.guid = None
        self.password = None
        self.name = None
        self.members = set()
        self.groups = set()
        self.calendarUserAddresses = set()
        self.autoSchedule = False

    def repeat(self, ctr):
        """
        Create another object like this but with all text items having % substitution
        done on them with the numeric value provided.
        @param ctr: an integer to substitute into text.
        """
        if self.shortName.find("%") != -1:
            shortName = self.shortName % ctr
        else:
            shortName = self.shortName
        if self.password.find("%") != -1:
            password = self.password % ctr
        else:
            password = self.password
        if self.name.find("%") != -1:
            name = self.name % ctr
        else:
            name = self.name
        calendarUserAddresses = set()
        for cuaddr in self.calendarUserAddresses:
            if cuaddr.find("%") != -1:
                calendarUserAddresses.add(cuaddr % ctr)
            else:
                calendarUserAddresses.add(cuaddr)
        
        result = XMLAccountRecord(self.recordType)
        result.shortName = shortName
        result.password = password
        result.name = name
        result.members = self.members
        result.calendarUserAddresses = calendarUserAddresses
        result.autoSchedule = self.autoSchedule
        return result

    def parseXML(self, node):
        for child in node._get_childNodes():
            child_name = child._get_localName()
            if child_name is None:
                continue
            elif child_name == ELEMENT_SHORTNAME:
                if child.firstChild is not None:
                    self.shortName = child.firstChild.data.encode("utf-8")
            elif child_name == ELEMENT_GUID:
                if child.firstChild is not None:
                    self.guid = child.firstChild.data.encode("utf-8")
            elif child_name == ELEMENT_PASSWORD:
                if child.firstChild is not None:
                    self.password = child.firstChild.data.encode("utf-8")
            elif child_name == ELEMENT_NAME:
                if child.firstChild is not None:
                    self.name = child.firstChild.data.encode("utf-8")
            elif child_name == ELEMENT_MEMBERS:
                self._parseMembers(child)
            elif child_name == ELEMENT_CUADDR:
                if child.firstChild is not None:
                    self.calendarUserAddresses.add(child.firstChild.data.encode("utf-8"))
            elif child_name == ELEMENT_AUTOSCHEDULE:
                # Only Resources & Locations
                if self.recordType not in (DirectoryService.recordType_resources, DirectoryService.recordType_locations):
                    raise ValueError("<auto-schedule> element only allowed for Resources and Locations: %s" % (child_name,))
                self.autoSchedule = True
            else:
                raise RuntimeError("Unknown account attribute: %s" % (child_name,))

    def _parseMembers(self, node):
        for child in node._get_childNodes():
            if child._get_localName() == ELEMENT_MEMBER:
                if child.hasAttribute(ATTRIBUTE_RECORDTYPE):
                    recordType = child.getAttribute(ATTRIBUTE_RECORDTYPE).encode("utf-8")
                else:
                    recordType = DirectoryService.recordType_users
                if child.firstChild is not None:
                    self.members.add((recordType, child.firstChild.data.encode("utf-8")))
