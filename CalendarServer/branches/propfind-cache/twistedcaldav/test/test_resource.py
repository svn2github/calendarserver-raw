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


class StubLocatingRequest(object):
    """
    A stub request that only knows how to locate resources for uris.
    """

    def __init__(self, resources=None):
        self.resources = resources or {}

    def locateResource(self, uri):
        return self.resources.get(uri)



class StubParentResource(object):
    """
    A stub resource that records it's arguments to the changed method.
    """
    changedArgs = None
    changedKwArgs = None

    def changed(self, *args, **kwargs):
        self.changedArgs = args
        self.changedKwArgs = kwargs


class ChangedNotificationTestCase(TestCase):
    """
    Test that CalDAVResource's changed implementation propogates change
    notifications to it's parent.
    """
    def setUp(self):
        self.calendarHome = StubParentResource()
        self.request = StubLocatingRequest({
                '/calendars/users/dreid/': self.calendarHome})
        self.myCalendar = CalDAVResource()
        self.myCalendarURI = '/calendars/users/dreid/calendar'

    def _test(self, changedArgs, changedKwargs,
              expectedArgs, expectedKwargs):
        self.myCalendar.changed(*changedArgs, **changedKwargs)

        self.assertEquals(self.calendarHome.changedArgs,
                          expectedArgs)
        self.assertEquals(self.calendarHome.changedKwArgs,
                          expectedKwargs)


    def test_notifiesParentResource(self):
        self._test((self.request, self.myCalendarURI),
                   {},
                   (self.request, self.myCalendarURI),
                   {'properties': False, 'data': False})

    def test_propertiesChanged(self):
        self._test((self.request, self.myCalendarURI),
                   {'properties': True},
                   (self.request, self.myCalendarURI),
                   {'properties': True, 'data': False})

    def test_dataChanged(self):
        self._test((self.request, self.myCalendarURI),
                   {'data': True},
                   (self.request, self.myCalendarURI),
                   {'properties': False, 'data': True})
