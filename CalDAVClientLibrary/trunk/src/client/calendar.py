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
##

from protocol.webdav.definitions import davxml
from protocol.url import URL
from protocol.caldav.definitions import caldavxml

class Calendar(object):
    
    def __init__(self, path=None, session=None):
        self.path = path
        if not path.endswith("/"):
            self.path += "/"
        self.session = session
        self.displayname = None
        self.description = None
        self.timezone = None

    def __str__(self):
        return "Calendar: %s" % (self.path,)

    def __repr__(self):
        return "Calendar: %s" % (self.path,)

    def exists(self):
        return self.session.testResource(URL(url=self.path))

    def readCalendar(self):
        pass
    def writeCalendar(self, calendar):
        pass

    def readComponent(self, name=None, uid=None):
        pass
    def writeComponent(self, component, name=None):
        pass

    def getDisplayName(self):
        if self.displayname is None and self.session:
            self._getProperties()
        return self.displayname

    def getDescription(self):
        if self.description is None and self.session:
            self._getProperties()
        return self.description
    
    def _getProperties(self):
        assert(self.session is not None)
        
        results, _ignore_bad = self.session.getProperties(URL(url=self.path), (davxml.displayname, caldavxml.calendar_description, caldavxml.calendar_timezone,))
        self.displayname = results.get(davxml.displayname, "")
        self.description = results.get(caldavxml.calendar_description, "")
        self.timezone = results.get(caldavxml.calendar_timezone, None)
