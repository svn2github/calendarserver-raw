##
# Copyright (c) 2011 Apple Inc. All rights reserved.
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
Unit tests for L{calendarsever.tools.export}.
"""


import sys
from cStringIO import StringIO

from twisted.trial.unittest import TestCase

#from calendarserver.tools import export
from twisted.internet.defer import inlineCallbacks
from twisted.python.modules import getModule

from twext.enterprise.ienterprise import AlreadyFinishedError

from twistedcaldav.ical import Component
from calendarserver.tools.export import ExportOptions
from calendarserver.tools.export import HomeExporter
from twistedcaldav.datafilters.test.test_peruserdata import dataForTwoUsers
from twistedcaldav.datafilters.test.test_peruserdata import resultForUser2
from txdav.common.datastore.test.util import buildStore
from txdav.common.datastore.test.util import populateCalendarsFrom

from calendarserver.tools.export import usage, exportToFile

def holiday(uid):
    return (
        getModule("twistedcaldav.test").filePath
            .sibling("data").child("Holidays").child(uid + ".ics")
            .getContent()
    )

def sample(name):
    return (
        getModule("twistedcaldav.test").filePath
        .sibling("data").child(name + ".ics").getContent()
    )

valentines = holiday("C31854DA-1ED0-11D9-A5E0-000A958A3252")
newYears = holiday("C3184A66-1ED0-11D9-A5E0-000A958A3252")
payday = sample("PayDay")

one = sample("OneEvent")
another = sample("AnotherEvent")
third = sample("ThirdEvent")


class CommandLine(TestCase):
    """
    Simple tests for command-line parsing.
    """

    def test_usageMessage(self):
        """
        The 'usage' message should print something to standard output (and
        nothing to standard error) and exit.
        """
        orig = sys.stdout
        orige = sys.stderr
        try:
            out = sys.stdout = StringIO()
            err = sys.stderr = StringIO()
            self.assertRaises(SystemExit, usage)
        finally:
            sys.stdout = orig
            sys.stderr = orige
        self.assertEquals(len(out.getvalue()) > 0, True, "No output.")
        self.assertEquals(len(err.getvalue()), 0)


    def test_oneHome(self):
        """
        One '--record' option will result in a single HomeExporter object with
        no calendars in its list.
        """
        eo = ExportOptions()
        eo.parseOptions(["--record", "users:bob"])
        self.assertEquals(len(eo.exporters), 1)
        exp = eo.exporters[0]
        self.assertIsInstance(exp, HomeExporter)
        self.assertEquals(exp.recordType, "users")
        self.assertEquals(exp.shortName, "bob")
        self.assertEquals(exp.collections, [])


    def test_homeAndCollections(self):
        """
        The --collection option adds calendars to the last calendar that was
        exported.
        """
        eo = ExportOptions()
        eo.parseOptions(["--record", "users:bob",
                         "--collection", "work stuff",
                         "--record", "users:jethro",
                         "--collection=fun stuff"])
        self.assertEquals(len(eo.exporters), 2)
        exp = eo.exporters[0]
        self.assertEquals(exp.recordType, "users")
        self.assertEquals(exp.shortName, "bob")
        self.assertEquals(exp.collections, ["work stuff"])
        exp = eo.exporters[1]
        self.assertEquals(exp.recordType, "users")
        self.assertEquals(exp.shortName, "jethro")
        self.assertEquals(exp.collections, ["fun stuff"])


    def test_outputFileSelection(self):
        """
        The --output option selects the file to write to, '-' or no parameter
        meaning stdout; the L{ExportOptions.openOutput} method returns that
        file.
        """
        eo = ExportOptions()
        eo.parseOptions([])
        self.assertIdentical(eo.openOutput(), sys.stdout)
        eo = ExportOptions()
        eo.parseOptions(["--output", "-"])
        self.assertIdentical(eo.openOutput(), sys.stdout)
        eo = ExportOptions()
        tmpnam = self.mktemp()
        eo.parseOptions(["--output", tmpnam])
        self.assertEquals(eo.openOutput().name, tmpnam)



class IntegrationTests(TestCase):
    """
    Tests for exporting data from a live store.
    """

    fakeConfigFile = 'not-a-real-config-file.plist'

    @inlineCallbacks
    def setUp(self):
        """
        Set up a store and fix the imported C{utilityMain} function (normally
        from L{calendarserver.tools.cmdline.utilityMain}) to point to a
        temporary method of this class.  Also, patch the imported C{reactor},
        since the SUT needs to call C{reactor.stop()} in order to work with
        L{utilityMain}.
        """
        self.mainCalled = False
        #self.patch(export, "utilityMain", self.fakeUtilityMain)
        self.store = yield buildStore(self, None)


    def fakeUtilityMain(self, configFileName, serviceClass, reactor=None):
        """
        Verify a few basic things.
        """
        if self.mainCalled:
            raise RuntimeError(
                "Main called twice this test; duplicate reactor run.")
        self.mainCalled = True
        self.assertEquals(configFileName, self.fakeConfigFile)
        theService = serviceClass(self.store)
        theService.startService()


    @inlineCallbacks
    def test_emptyCalendar(self):
        """
        Exporting an empty calendar results in an empty calendar.
        """
        io = StringIO()
        value = yield exportToFile([], "nobody", io)
        # it doesn't return anything, it writes to the file.
        self.assertEquals(value, None)
        # but it should write a valid component to the file.
        self.assertEquals(Component.fromString(io.getvalue()),
                          Component.newCalendar())


    def txn(self):
        """
        Create a new transaction and automatically clean it up when the test
        completes.
        """
        aTransaction = self.store.newTransaction()
        @inlineCallbacks
        def maybeAbort():
            try:
                yield aTransaction.abort()
            except AlreadyFinishedError:
                pass
        self.addCleanup(maybeAbort)
        return aTransaction


    @inlineCallbacks
    def test_oneEventCalendar(self):
        """
        Exporting an calendar with one event in it will result in just that
        event.
        """
        yield populateCalendarsFrom(
            {
                "home1": {
                    "calendar1": {
                        "valentines-day.ics": (valentines, {})
                    }
                }
            }, self.store
        )

        expected = Component.newCalendar()
        [theComponent] = Component.fromString(valentines).subcomponents()
        expected.addComponent(theComponent)

        io = StringIO()
        yield exportToFile(
            [(yield self.txn().calendarHomeWithUID("home1"))
              .calendarWithName("calendar1")],
            "nobody", io
        )
        self.assertEquals(Component.fromString(io.getvalue()),
                          expected)


    @inlineCallbacks
    def test_twoSimpleEvents(self):
        """
        Exporting a calendar with two events in it will result in a VCALENDAR
        component with both VEVENTs in it.
        """
        yield populateCalendarsFrom(
            {
                "home1": {
                    "calendar1": {
                        "valentines-day.ics": (valentines, {}),
                        "new-years-day.ics": (newYears, {})
                    }
                }
            }, self.store
        )

        expected = Component.newCalendar()
        a = Component.fromString(valentines)
        b = Component.fromString(newYears)
        for comp in a, b:
            for sub in comp.subcomponents():
                expected.addComponent(sub)

        io = StringIO()
        yield exportToFile(
            [(yield self.txn().calendarHomeWithUID("home1"))
              .calendarWithName("calendar1")],
            "nobody", io
        )
        self.assertEquals(Component.fromString(io.getvalue()),
                          expected)


    @inlineCallbacks
    def test_onlyOneVTIMEZONE(self):
        """
        C{VTIMEZONE} subcomponents with matching TZIDs in multiple event
        calendar objects should only be rendered in the resulting output once.

        (Note that the code to suppor this is actually in PyCalendar, not the
        export tool itself.)
        """
        yield populateCalendarsFrom(
            {
                "home1": {
                    "calendar1": {
                        "1.ics": (one, {}), # EST
                        "2.ics": (another, {}), # EST
                        "3.ics": (third, {}) # PST
                    }
                }
            }, self.store
        )

        io = StringIO()
        yield exportToFile(
            [(yield self.txn().calendarHomeWithUID("home1"))
              .calendarWithName("calendar1")],
            "nobody", io
        )
        result = Component.fromString(io.getvalue())

        def filtered(name):
            for c in result.subcomponents():
                if c.name() == name:
                    yield c

        timezones = list(filtered("VTIMEZONE"))
        events = list(filtered("VEVENT"))

        # Sanity check to make sure we picked up all three events:
        self.assertEquals(len(events), 3)

        self.assertEquals(len(timezones), 2)
        self.assertEquals(set([tz.propertyValue("TZID") for tz in timezones]),

                          # Use an intentionally wrong TZID in order to make
                          # sure we don't depend on caching effects elsewhere.
                          set(["America/New_Yrok", "US/Pacific"]))


    @inlineCallbacks
    def test_perUserFiltering(self):
        """
        L{exportToFile} performs per-user component filtering based on its
        C{exporterUID} parameter.
        """
        yield populateCalendarsFrom(
            {
                "home1": {
                    "calendar1": {
                        "peruser.ics": (dataForTwoUsers, {}), # EST
                    }
                }
            }, self.store
        )
        io = StringIO()
        yield exportToFile(
            [(yield self.txn().calendarHomeWithUID("home1"))
              .calendarWithName("calendar1")], "user02", io
        )
        self.assertEquals(
            Component.fromString(resultForUser2),
            Component.fromString(io.getvalue())
        )



