##
# Copyright (c) 2008 Apple Inc. All rights reserved.
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
Unittests for CalDAV-aware resources found in twistedcaldav.resources
"""

from twisted.trial.unittest import TestCase
from twistedcaldav.resource import CalDAVResource

from twisted.internet.defer import succeed

class StubLocatingRequest(object):
    """
    A stub request that only knows how to locate resources for uris.
    """

    def __init__(self, resources=None):
        self.resources = resources or {}
        self.urls = {}
        self._loadUrls()

    def _loadUrls(self):
        for k, v in self.resources.iteritems():
            self.urls[v] = k

    def locateResource(self, url):
        return succeed(self.resources.get(url))

    def urlForResource(self, resrc):
        return self.urls.get(resrc)


class StubParentResource(object):
    """
    A stub resource that records it's arguments to the changed method.
    """
    changedArgs = None
    changedKwArgs = None

    def changed(self, *args, **kwargs):
        self.changedArgs = args
        self.changedKwArgs = kwargs
        return succeed(None)


class ChangedNotificationTestCase(TestCase):
    """
    Test that CalDAVResource's changed implementation propogates change
    notifications to it's parent.
    """
    def setUp(self):
        self.calendarHome = StubParentResource()
        self.myCalendar = CalDAVResource()
        self.myCalendarURI = '/calendars/users/dreid/calendar'

        self.request = StubLocatingRequest({
                '/calendars/users/dreid/': self.calendarHome,
                self.myCalendarURI: self.myCalendar
                })


    def _test(self, changedArgs, changedKwargs,
              expectedArgs, expectedKwargs):
        def _ranTest(result):
            self.assertEquals(self.calendarHome.changedArgs,
                              expectedArgs)
            self.assertEquals(self.calendarHome.changedKwArgs,
                              expectedKwargs)

        d = self.myCalendar.changed(*changedArgs, **changedKwargs)
        d.addCallback(_ranTest)
        return d

    def test_notifiesParentResource(self):
        return self._test((self.request, self.myCalendarURI),
                          {},
                          (self.request, self.myCalendarURI),
                          {'properties': False, 'data': False})

    def test_propertiesChanged(self):
        return self._test((self.request, self.myCalendarURI),
                          {'properties': True},
                          (self.request, self.myCalendarURI),
                          {'properties': True, 'data': False})

    def test_dataChanged(self):
        return self._test((self.request, self.myCalendarURI),
                          {'data': True},
                          (self.request, self.myCalendarURI),
                          {'properties': False, 'data': True})

    def test_changedOnRootResource(self):
        self.request.resources = {'/': self.myCalendar}
        self.request._loadUrls()
        d = self.myCalendar.changed(self.request, '/', data=True)
        return d

    def test_deepChanged(self):
        self.request.resources['/calendars/users/dreid/'] = CalDAVResource()
        self.request.resources['/calendars/users/'] = CalDAVResource()
        self.request.resources['/calendars/'] = CalDAVResource()
        self.request.resources['/'] = CalDAVResource()
        self.request._loadUrls()
        d = self.myCalendar.changed(self.request, self.myCalendarURI, data=True)
        return d
