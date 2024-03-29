##
# Copyright (c) 2006-2012 Apple Inc. All rights reserved.
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
Class that encapsulates the server information for a CalDAV test run.
"""

import datetime
import src.xmlDefs

class serverinfo(object):
    """
    Maintains information about the server being targetted.
    """

    def __init__(self):
        self.host = ""
        self.nonsslport = 80
        self.sslport = 443
        self.authtype = "basic"
        self.features = set()
        self.user = ""
        self.pswd = ""
        self.subsdict = {}
        self.extrasubsdict = {}

        # Insert a bunch of date values
        dtnow = datetime.date.today()
        for dayoffset in xrange(-21, 21):
            dtoffset = dtnow + datetime.timedelta(days=dayoffset)
            key = "$now.%d:" % (dayoffset,)
            value = "%d%02d%02d" % (dtoffset.year, dtoffset.month, dtoffset.day,)
            self.subsdict[key] = value
        for yearoffset in xrange(-5, 20):
            key = "$now.year.%d:" % (yearoffset,)
            value = "%d" % (dtnow.year + yearoffset,)
            self.subsdict[key] = value


    def subs(self, str, db=None):
        if db is None:
            db = self.subsdict
        count = 0
        while count < 10:
            do_again = False
            for key, value in db.iteritems():
                newstr = str.replace(key, value)
                do_again = do_again or (newstr != str)
                str = newstr
            if not do_again:
                break
            count += 1
        return str


    def addsubs(self, items, db=None):
        if db is None:
            db_actual = self.subsdict
        else:
            db_actual = db
        for key, value in items.iteritems():
            db_actual[key] = value

        if db is None:
            self.updateParams()


    def hasextrasubs(self):
        return len(self.extrasubsdict) > 0


    def extrasubs(self, str):
        return self.subs(str, self.extrasubsdict)


    def addextrasubs(self, items):
        self.addsubs(items, self.extrasubsdict)


    def parseXML(self, node):
        for child in node.getchildren():
            if child.tag == src.xmlDefs.ELEMENT_HOST:
                try:
                    self.host = child.text.encode("utf-8")
                except:
                    self.host = "localhost"
            elif child.tag == src.xmlDefs.ELEMENT_NONSSLPORT:
                self.nonsslport = int(child.text)
            elif child.tag == src.xmlDefs.ELEMENT_SSLPORT:
                self.sslport = int(child.text)
            elif child.tag == src.xmlDefs.ELEMENT_AUTHTYPE:
                self.authtype = child.text.encode("utf-8")
            elif child.tag == src.xmlDefs.ELEMENT_FEATURES:
                self.parseFeatures(child)
            elif child.tag == src.xmlDefs.ELEMENT_SUBSTITUTIONS:
                self.parseSubstitutionsXML(child)

        self.updateParams()


    def parseFeatures(self, node):
        for child in node.getchildren():
            if child.tag == src.xmlDefs.ELEMENT_FEATURE:
                self.features.add(child.text.encode("utf-8"))


    def updateParams(self):
        # Now cache some useful substitutions
        if "$userid1:" not in self.subsdict:
            raise ValueError("Must have $userid1: substitution")
        self.user = self.subsdict["$userid1:"]
        if "$pswd1:" not in self.subsdict:
            raise ValueError("Must have $pswd1: substitution")
        self.pswd = self.subsdict["$pswd1:"]


    def parseRepeatXML(self, node):
        # Look for count
        count = node.get(src.xmlDefs.ATTR_COUNT)

        for child in node.getchildren():
            self.parseSubstitutionXML(child, count)


    def parseSubstitutionsXML(self, node):
        for child in node.getchildren():
            if child.tag == src.xmlDefs.ELEMENT_SUBSTITUTION:
                self.parseSubstitutionXML(child)
            elif child.tag == src.xmlDefs.ELEMENT_REPEAT:
                self.parseRepeatXML(child)


    def parseSubstitutionXML(self, node, repeat=None):
        if node.tag == src.xmlDefs.ELEMENT_SUBSTITUTION:
            key = None
            value = None
            for schild in node.getchildren():
                if schild.tag == src.xmlDefs.ELEMENT_KEY:
                    key = schild.text.encode("utf-8")
                elif schild.tag == src.xmlDefs.ELEMENT_VALUE:
                    value = schild.text.encode("utf-8") if schild.text else ""

            if key and value:
                if repeat:
                    for count in range(1, int(repeat) + 1):
                        self.subsdict[key % (count,)] = (value % (count,)) if "%" in value else value
                else:
                    self.subsdict[key] = value
