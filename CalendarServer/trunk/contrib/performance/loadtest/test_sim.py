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
#
##

from operator import setitem
from plistlib import writePlistToString

from zope.interface.verify import verifyClass

from twisted.python.log import LogPublisher, theLogPublisher, msg
from twisted.python.usage import UsageError
from twisted.python.filepath import FilePath
from twisted.trial.unittest import TestCase
from twisted.internet.defer import succeed
from twisted.internet.task import Clock

from twistedcaldav.directory.idirectory import IDirectoryService
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord

from loadtest.ical import SnowLeopard
from loadtest.profiles import Eventer, Inviter, Accepter
from loadtest.population import (
    SmoothRampUp, ClientType, PopulationParameters, Populator, CalendarClientSimulator,
    ProfileType, SimpleStatistics)
from loadtest.sim import (
    Server, Arrival, SimOptions, LoadSimulator, LagTrackingReactor, main)

VALID_CONFIG = {
    'server': {
        'host': '127.0.0.1',
        'port': 8008,
        },
    'arrival': {
        'factory': 'loadtest.population.SmoothRampUp',
        'groups': 10,
        'groupSize': 1,
        'interval': 3,
        },
    }

VALID_CONFIG_PLIST = writePlistToString(VALID_CONFIG)


class SimOptionsTests(TestCase):
    def test_missingConfig(self):
        """
        If the I{config} option is not specified,
        L{SimOptions.parseOptions} raises a L{UsageError} indicating
        it is required.
        """
        options = SimOptions()
        exc = self.assertRaises(UsageError, options.parseOptions, [])
        self.assertEquals(
            str(exc), "Specify a configuration file using --config <path>")


    def test_configFileNotFound(self):
        """
        If the filename given to the I{config} option is not found,
        L{SimOptions.parseOptions} raises a L{UsageError} indicating
        this.
        """
        name = self.mktemp()
        options = SimOptions()
        exc = self.assertRaises(
            UsageError, options.parseOptions, ['--config', name])
        self.assertEquals(
            str(exc), "--config %s: No such file or directory" % (name,))


    def test_configFileNotParseable(self):
        """
        If the contents of the file given to the I{config} option
        cannot be parsed by L{ConfigParser},
        L{SimOptions.parseOptions} raises a L{UsageError} indicating
        this.
        """
        config = self.mktemp()
        FilePath(config).setContent("some random junk")
        options = SimOptions()
        exc = self.assertRaises(
            UsageError, options.parseOptions, ['--config', config])
        self.assertEquals(
            str(exc),
            "--config %s: syntax error: line 1, column 0" % (config,))



class CalendarClientSimulatorTests(TestCase):
    """
    Tests for L{CalendarClientSimulator} which adds running clients to
    a simulation.
    """
    realmName = 'stub'

    def _user(self, name):
        record = DirectoryRecord(self, 'user', name, (name,))
        record.password = 'password-' + name
        return record


    def test_createUser(self):
        """
        Subsequent calls to L{CalendarClientSimulator._createUser}
        with different user numbers return user details from different
        directory records.
        """
        calsim = CalendarClientSimulator(
            [self._user('alice'), self._user('bob'), self._user('carol')],
            Populator(None), None, None, 'example.org', 1234)
        users = sorted([
                calsim._createUser(0)[0],
                calsim._createUser(1)[0],
                calsim._createUser(2)[0],
                ])
        self.assertEqual(['alice', 'bob', 'carol'], users)


    def test_createUserAuthInfo(self):
        """
        The auth handler returned by L{CalendarClientSimulator._createUser}
        includes the password taken from user's directory record.
        """
        calsim = CalendarClientSimulator(
            [self._user('alice')],
            Populator(None), None, None, 'example.org', 1234)
        user, auth = calsim._createUser(0)
        self.assertEqual(
            auth.passwd.find_user_password('Test Realm', 'http://example.org:1234/')[1],
            'password-' + user)


class Reactor(object):
    message = "some event to be observed"

    def run(self):
        msg(self.message)


    def addSystemEventTrigger(self, *args):
        pass


class Observer(object):
    def __init__(self):
        self.reported = False
        self.events = []


    def observe(self, event):
        self.events.append(event)


    def report(self):
        self.reported = True



class NullArrival(object):
    def run(self, sim):
        pass



class StubSimulator(LoadSimulator):
    def run(self):
        return 3



class LoadSimulatorTests(TestCase):
    def test_main(self):
        """
        L{LoadSimulator.main} raises L{SystemExit} with the result of
        L{LoadSimulator.run}.
        """
        config = FilePath(self.mktemp())
        config.setContent(VALID_CONFIG_PLIST)

        exc = self.assertRaises(
            SystemExit, StubSimulator.main, ['--config', config.path])
        self.assertEquals(
            exc.args, (StubSimulator(None, None, None).run(),))


    def test_createSimulator(self):
        """
        L{LoadSimulator.createSimulator} creates a L{CalendarClientSimulator}
        with its own reactor and host and port information from the
        configuration file.
        """
        host = '127.0.0.7'
        port = 1243
        reactor = object()
        sim = LoadSimulator(Server(host, port), None, None, reactor=reactor)
        calsim = sim.createSimulator()
        self.assertIsInstance(calsim, CalendarClientSimulator)
        self.assertIsInstance(calsim.reactor, LagTrackingReactor)
        self.assertIdentical(calsim.reactor._reactor, reactor)
        self.assertEquals(calsim.host, host)
        self.assertEquals(calsim.port, port)


    def test_loadAccountsFromFile(self):
        """
        L{LoadSimulator.
        """
        accounts = FilePath(self.mktemp())
        accounts.setContent("foo bar\nbaz quux\n")
        config = VALID_CONFIG.copy()
        config["accounts"] = {
            "loader": "loadtest.sim.recordsFromTextFile",
            "params": {
                "path": accounts.path},
            }
        configpath = FilePath(self.mktemp())
        configpath.setContent(writePlistToString(config))
        sim = LoadSimulator.fromCommandLine(['--config', configpath.path])
        self.assertEqual(2, len(sim.records))
        self.assertEqual(sim.records[0].uid, 'foo')
        self.assertEqual(sim.records[0].password, 'bar')
        self.assertEqual(sim.records[1].uid, 'baz')
        self.assertEqual(sim.records[1].password, 'quux')


    def test_loadServerConfig(self):
        """
        The Calendar Server host and port are loaded from the [server]
        section of the configuration file specified.
        """
        config = FilePath(self.mktemp())
        config.setContent(writePlistToString({
                    "server": {
                        "host": "127.0.0.1",
                        "port": 1234,
                        },
                    }))
        sim = LoadSimulator.fromCommandLine(['--config', config.path])
        self.assertEquals(sim.server, Server("127.0.0.1", 1234))


    def test_loadArrivalConfig(self):
        """
        The arrival policy type and arguments are loaded from the
        [arrival] section of the configuration file specified.
        """
        config = FilePath(self.mktemp())
        config.setContent(writePlistToString({
                    "arrival": {
                        "factory": "loadtest.population.SmoothRampUp",
                        "groups": 10,
                        "groupSize": 1,
                        "interval": 3,
                        },
                    }))
        sim = LoadSimulator.fromCommandLine(['--config', config.path])
        self.assertEquals(
            sim.arrival,
            Arrival(SmoothRampUp, dict(groups=10, groupSize=1, interval=3)))


    def test_createArrivalPolicy(self):
        """
        L{LoadSimulator.createArrivalPolicy} creates an arrival
        policy based on the L{Arrival} passed to its initializer.
        """
        class FakeArrival(object):
            def __init__(self, reactor, x, y):
                self.reactor = reactor
                self.x = x
                self.y = y

        reactor = object()
        sim = LoadSimulator(
            None, Arrival(FakeArrival, {'x': 3, 'y': 2}), None, reactor=reactor)
        arrival = sim.createArrivalPolicy()
        self.assertIsInstance(arrival, FakeArrival)
        self.assertIdentical(arrival.reactor, sim.reactor)
        self.assertEquals(arrival.x, 3)
        self.assertEquals(arrival.y, 2)


    def test_loadPopulationParameters(self):
        """
        Client weights and profiles are loaded from the [clients]
        section of the configuration file specified.
        """
        config = FilePath(self.mktemp())
        config.setContent(writePlistToString({
                    "clients": [{
                            "software": "loadtest.ical.SnowLeopard",
                            "profiles": [{"class": "loadtest.profiles.Eventer", "params": {"interval": 25}}],
                            "weight": 3,
                            }]}))
        sim = LoadSimulator.fromCommandLine(['--config', config.path])
        expectedParameters = PopulationParameters()
        expectedParameters.addClient(
            3, ClientType(SnowLeopard, [ProfileType(Eventer, {"interval": 25})]))
        self.assertEquals(sim.parameters, expectedParameters)

        
    def test_requireClient(self):
        """
        At least one client is required, so if a configuration with an
        empty clients array is specified, a single default client type
        is used.
        """
        config = FilePath(self.mktemp())
        config.setContent(writePlistToString({"clients": []}))
        sim = LoadSimulator.fromCommandLine(['--config', config.path])
        expectedParameters = PopulationParameters()
        expectedParameters.addClient(
            1, ClientType(SnowLeopard, [Eventer, Inviter, Accepter]))
        self.assertEquals(sim.parameters, expectedParameters)


    def test_loadLogObservers(self):
        """
        Log observers specified in the [observers] section of the
        configuration file are added to the logging system.
        """
        config = FilePath(self.mktemp())
        config.setContent(writePlistToString({
                    "observers": ["loadtest.population.SimpleStatistics"]}))
        sim = LoadSimulator.fromCommandLine(['--config', config.path])
        self.assertEquals(len(sim.observers), 1)
        self.assertIsInstance(sim.observers[0], SimpleStatistics)

    def test_observeRunReport(self):
        """
        Each log observer is added to the log publisher before the
        simulation run is started and has its C{report} method called
        after the simulation run completes.
        """
        observers = [Observer()]
        sim = LoadSimulator(
            Server('example.com', 123), 
            Arrival(lambda reactor: NullArrival(), {}),
            None, observers, reactor=Reactor())
        sim.run()
        self.assertTrue(observers[0].reported)
        self.assertEquals(
            observers[0].events[0]['message'], (Reactor.message,))
