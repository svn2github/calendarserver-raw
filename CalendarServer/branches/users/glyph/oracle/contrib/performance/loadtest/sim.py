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

from sys import argv
from random import Random
from plistlib import readPlist
from collections import namedtuple

from twisted.python.filepath import FilePath
from twisted.python.log import addObserver
from twisted.python.usage import UsageError, Options
from twisted.python.reflect import namedAny

from loadtest.ical import SnowLeopard
from loadtest.profiles import Eventer, Inviter, Accepter
from loadtest.population import (
    Populator, ClientType, PopulationParameters, SmoothRampUp,
    CalendarClientSimulator)


class SimOptions(Options):
    """
    Command line configuration options for the load simulator.
    """
    config = None

    def opt_config(self, path):
        """
        ini-syntax configuration file from which to read simulation
        parameters.
        """
        try:
            configFile = FilePath(path).open()
        except IOError, e:
            raise UsageError("--config %s: %s" % (path, e.strerror))
        try:
            self.config = readPlist(configFile)
        except Exception, e:
            raise UsageError(
                "--config %s: %s" % (path, str(e)))


    def opt_debug(self):
        """
        Enable Deferred and Failure debugging.
        """
        self.opt_debug_deferred()
        self.opt_debug_failure()


    def opt_debug_deferred(self):
        """
        Enable Deferred debugging.
        """
        from twisted.internet.defer import setDebugging
        setDebugging(True)


    def opt_debug_failure(self):
        """
        Enable Failure debugging.
        """
        from twisted.python.failure import startDebugMode
        startDebugMode()


    def postOptions(self):
        if self.config is None:
            raise UsageError("Specify a configuration file using --config <path>")


Server = namedtuple('Server', 'host port')
Arrival = namedtuple('Arrival', 'factory parameters')


class LoadSimulator(object):
    """
    A L{LoadSimulator} simulates some configuration of calendar
    clients.

    @type server: L{Server}
    @type arrival: L{Arrival}
    @type parameters: L{PopulationParameters}
    """
    def __init__(self, server, arrival, parameters, observers=None, reactor=None):
        if reactor is None:
            from twisted.internet import reactor
        self.server = server
        self.arrival = arrival
        self.parameters = parameters
        self.observers = observers
        self.reactor = reactor


    @classmethod
    def fromCommandLine(cls, args=None):
        if args is None:
            args = argv[1:]

        options = SimOptions()
        try:
            options.parseOptions(args)
        except UsageError, e:
            raise SystemExit(str(e))

        if 'server' in options.config:
            server = Server( 
                options.config['server']['host'],
                options.config['server']['port'])
        else:
            server = Server('127.0.0.1', 8008)

        if 'arrival' in options.config:
            params = options.config['arrival']
            factory = namedAny(params.pop('factory'))
            arrival = Arrival(factory, params)
        else:
            arrival = Arrival(
                SmoothRampUp, dict(groups=10, groupSize=1, interval=3))

        parameters = PopulationParameters()
        if 'clients' in options.config:
            for clientConfig in options.config['clients']:
                parameters.addClient(
                    clientConfig["weight"],
                    ClientType(
                        namedAny(clientConfig["software"]),
                        [namedAny(profile)
                         for profile in clientConfig["profiles"]]))
        if not parameters.clients:
            parameters.addClient(
                1, ClientType(SnowLeopard, [Eventer, Inviter, Accepter]))

        observers = []
        if 'observers' in options.config:
            for observerName in options.config['observers']:
                observers.append(namedAny(observerName)())

        return cls(server, arrival, parameters, observers)


    @classmethod
    def main(cls, args=None):
        simulator = cls.fromCommandLine(args)
        raise SystemExit(simulator.run())


    def createSimulator(self):
        host = self.server.host
        port = self.server.port
        populator = Populator(Random())
        return CalendarClientSimulator(
            populator, self.parameters, self.reactor, host, port)


    def createArrivalPolicy(self):
        return self.arrival.factory(self.reactor, **self.arrival.parameters)


    def run(self):
        for obs in self.observers:
            addObserver(obs.observe)
        sim = self.createSimulator()
        arrivalPolicy = self.createArrivalPolicy()
        arrivalPolicy.run(sim)
        self.reactor.run()
        for obs in self.observers:
            obs.report()

main = LoadSimulator.main
