# -*- test-case-name: contrib.performance.loadtest.test_sim -*-
##
# Copyright (c) 2011-2013 Apple Inc. All rights reserved.
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

from collections import namedtuple
from os import environ, mkdir
from os.path import isdir
from plistlib import readPlist
from random import Random
from sys import argv, stdout
from xml.parsers.expat import ExpatError

from twisted.python import context
from twisted.python.filepath import FilePath
from twisted.python.log import startLogging, addObserver, removeObserver
from twisted.python.usage import UsageError, Options
from twisted.python.reflect import namedAny

from twisted.application.service import Service
from twisted.application.service import MultiService

from twisted.internet.defer import Deferred
from twisted.internet.defer import gatherResults
from twisted.internet.defer import inlineCallbacks
from twisted.internet.protocol import ProcessProtocol

from contrib.performance.loadtest.ical import OS_X_10_6
from contrib.performance.loadtest.profiles import Eventer, Inviter, Accepter
from contrib.performance.loadtest.population import (
    Populator, ProfileType, ClientType, PopulationParameters, SmoothRampUp,
    CalendarClientSimulator)
from contrib.performance.loadtest.webadmin import LoadSimAdminResource


class _DirectoryRecord(object):
    def __init__(self, uid, password, commonName, email):
        self.uid = uid
        self.password = password
        self.commonName = commonName
        self.email = email


def generateRecords(count, uidPattern="user%d", passwordPattern="user%d",
    namePattern="User %d", emailPattern="user%d@example.com"):
    for i in xrange(count):
        i += 1
        uid = uidPattern % (i,)
        password = passwordPattern % (i,)
        name = namePattern % (i,)
        email = emailPattern % (i,)
        yield _DirectoryRecord(uid, password, name, email)


def recordsFromCSVFile(path):
    if path:
        pathObj = FilePath(path)
    else:
        pathObj = FilePath(__file__).sibling("accounts.csv")
    return [
        _DirectoryRecord(*line.decode('utf-8').split(u','))
        for line
        in pathObj.getContent().splitlines()]



def recordsFromCount(count, uid=u"user%02d", password=u"user%02d",
                     commonName=u"User %02d", email=u"user%02d@example.com"):
    for i in range(1, count + 1):
        yield _DirectoryRecord(uid % i, password % i,
                               commonName % i, email % i)



class LagTrackingReactor(object):
    """
    This reactor wraps another reactor and proxies all attribute
    access (including method calls).  It only changes the behavior of
    L{IReactorTime.callLater} to insert a C{"lag"} key into the
    context which delayed function calls are invoked with.  This key
    has a float value which gives the difference in time between when
    the call was original scheduled and when the call actually took
    place.
    """
    def __init__(self, reactor):
        self._reactor = reactor

    def __getattr__(self, name):
        return getattr(self._reactor, name)

    def callLater(self, delay, function, *args, **kwargs):
        expected = self._reactor.seconds() + delay
        def modifyContext():
            now = self._reactor.seconds()
            context.call({'lag': now - expected}, function, *args, **kwargs)
        return self._reactor.callLater(delay, modifyContext)


class SimOptions(Options):
    """
    Command line configuration options for the load simulator.
    """
    config = None
    _defaultConfig = FilePath(__file__).sibling("config.plist")

    optParameters = [
        ("runtime", "t", None,
         "Specify the limit (seconds) on the time to run the simulation.",
         int),
        ("config", None, _defaultConfig,
         "Configuration plist file name from which to read simulation parameters.",
         FilePath),
        ]


    def opt_logfile(self, filename):
        """
        Enable normal logging to some file.  - for stdout.
        """
        if filename == "-":
            fObj = stdout
        else:
            fObj = file(filename, "a")
        startLogging(fObj, setStdout=False)


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
        try:
            configFile = self['config'].open()
        except IOError, e:
            raise UsageError("--config %s: %s" % (
                    self['config'].path, e.strerror))
        try:
            try:
                self.config = readPlist(configFile)
            except ExpatError, e:
                raise UsageError("--config %s: %s" % (self['config'].path, e)) 
        finally:
            configFile.close()


Arrival = namedtuple('Arrival', 'factory parameters')


from twisted.web import server

class LoadSimulator(object):
    """
    A L{LoadSimulator} simulates some configuration of calendar
    clients.

    @type server: C{str}
    @type arrival: L{Arrival}
    @type parameters: L{PopulationParameters}

    @ivar records: A C{list} of L{_DirectoryRecord} instances giving
        user information about the accounts on the server being put
        under load.
    """
    def __init__(self, server, principalPathTemplate, webadminPort, serializationPath, arrival, parameters, observers=None,
                 records=None, reactor=None, runtime=None, workers=None,
                 configTemplate=None, workerID=None, workerCount=1):
        if reactor is None:
            from twisted.internet import reactor
        self.server = server
        self.principalPathTemplate = principalPathTemplate
        self.webadminPort = webadminPort
        self.serializationPath = serializationPath
        self.arrival = arrival
        self.parameters = parameters
        self.observers = observers
        self.reporter = None
        self.records = records
        self.reactor = LagTrackingReactor(reactor)
        self.runtime = runtime
        self.workers = workers
        self.configTemplate = configTemplate
        self.workerID = workerID
        self.workerCount = workerCount


    @classmethod
    def fromCommandLine(cls, args=None, output=stdout):
        if args is None:
            args = argv[1:]

        options = SimOptions()
        try:
            options.parseOptions(args)
        except UsageError, e:
            raise SystemExit(str(e))

        return cls.fromConfig(options.config, options['runtime'], output)


    @classmethod
    def fromConfig(cls, config, runtime=None, output=stdout, reactor=None):
        """
        Create a L{LoadSimulator} from a parsed instance of a configuration
        property list.
        """

        workers = config.get("workers")
        if workers is None:
            # Client / place where the simulator actually runs configuration
            workerID = config.get("workerID", 0)
            workerCount = config.get("workerCount", 1)
            configTemplate = None
            server = 'http://127.0.0.1:8008'
            principalPathTemplate = "/principals/users/%s/"
            serializationPath = None

            if 'server' in config:
                server = config['server']

            if 'principalPathTemplate' in config:
                principalPathTemplate = config['principalPathTemplate']


            if 'clientDataSerialization' in config:
                if config['clientDataSerialization']['Enabled']:
                    serializationPath = config['clientDataSerialization']['Path']
                    if not isdir(serializationPath):
                        try:
                            mkdir(serializationPath)
                        except OSError:
                            print "Unable to create client data serialization directory: %s" % (serializationPath)
                            print "Please consult the clientDataSerialization stanza of contrib/performance/loadtest/config.plist"
                            raise

            if 'arrival' in config:
                arrival = Arrival(
                    namedAny(config['arrival']['factory']), 
                    config['arrival']['params'])
            else:
                arrival = Arrival(
                    SmoothRampUp, dict(groups=10, groupSize=1, interval=3))

            parameters = PopulationParameters()
            if 'clients' in config:
                for clientConfig in config['clients']:
                    parameters.addClient(
                        clientConfig["weight"],
                        ClientType(
                            namedAny(clientConfig["software"]),
                            cls._convertParams(clientConfig["params"]),
                            [ProfileType(
                                    namedAny(profile["class"]),
                                    cls._convertParams(profile["params"]))
                             for profile in clientConfig["profiles"]]))
            if not parameters.clients:
                parameters.addClient(1,
                                     ClientType(OS_X_10_6, {},
                                                [Eventer, Inviter, Accepter]))
        else:
            # Manager / observer process.
            server = ''
            principalPathTemplate = ''
            serializationPath = None
            arrival = None
            parameters = None
            workerID = 0
            configTemplate = config
            workerCount = 1

        webadminPort = None
        if 'webadmin' in config:
            if config['webadmin']['enabled']:
                webadminPort = config['webadmin']['HTTPPort']

        observers = []
        if 'observers' in config:
            for observer in config['observers']:
                observerName = observer["type"]
                observerParams = observer["params"]
                observers.append(namedAny(observerName)(**observerParams))

        records = []
        if 'accounts' in config:
            loader = config['accounts']['loader']
            params = config['accounts']['params']
            records.extend(namedAny(loader)(**params))
            output.write("Loaded {0} accounts.\n".format(len(records)))

        return cls(server, principalPathTemplate, webadminPort, serializationPath,
                   arrival, parameters, observers=observers,
                   records=records, runtime=runtime, reactor=reactor,
                   workers=workers, configTemplate=configTemplate,
                   workerID=workerID, workerCount=workerCount)


    @classmethod
    def _convertParams(cls, params):
        """
        Find parameter values which should be more structured than plistlib is
        capable of constructing and replace them with the more structured form.

        Specifically, find keys that end with C{"Distribution"} and convert
        them into some kind of distribution object using the associated
        dictionary of keyword arguments.
        """
        for k, v in params.iteritems():
            if k.endswith('Distribution'):
                params[k] = cls._convertDistribution(v)
        return params


    @classmethod
    def _convertDistribution(cls, value):
        """
        Construct and return a new distribution object using the type and
        params specified by C{value}.
        """
        return namedAny(value['type'])(**value['params'])


    @classmethod
    def main(cls, args=None):
        simulator = cls.fromCommandLine(args)
        raise SystemExit(simulator.run())


    def createSimulator(self):
        populator = Populator(Random())
        return CalendarClientSimulator(
            self.records,
            populator,
            self.parameters,
            self.reactor,
            self.server,
            self.principalPathTemplate,
            self.serializationPath,
            self.workerID,
            self.workerCount,
        )


    def createArrivalPolicy(self):
        return self.arrival.factory(self.reactor, **self.arrival.parameters)


    def serviceClasses(self):
        """
        Return a list of L{SimService} subclasses for C{attachServices} to
        instantiate and attach to the reactor.
        """
        if self.workers is not None:
            return [
                ObserverService,
                WorkerSpawnerService,
                ReporterService,
            ]
        return [
            ObserverService,
            SimulatorService,
            ReporterService,
        ]


    def attachServices(self, output):
        self.ms = MultiService()
        for svcclass in self.serviceClasses():
            svcclass(self, output).setServiceParent(self.ms)
        attachService(self.reactor, self, self.ms)


    def run(self, output=stdout):
        self.attachServices(output)
        if self.runtime is not None:
            self.reactor.callLater(self.runtime, self.reactor.stop)
        if self.webadminPort:
            self.reactor.listenTCP(self.webadminPort, server.Site(LoadSimAdminResource(self)))
        self.reactor.run()


    def stop(self):
        if self.ms.running:
            self.ms.stopService()
            self.reactor.callLater(5, self.reactor.stop)


    def shutdown(self):
        if self.ms.running:
            return self.ms.stopService()


def attachService(reactor, loadsim, service):
    """
    Attach a given L{IService} provider to the given L{IReactorCore}; cause it
    to be started when the reactor starts, and stopped when the reactor stops.
    """
    reactor.callWhenRunning(service.startService)
    reactor.addSystemEventTrigger('before', 'shutdown', loadsim.shutdown)



class SimService(Service, object):
    """
    Base class for services associated with the L{LoadSimulator}.
    """

    def __init__(self, loadsim, output):
        super(SimService, self).__init__()
        self.loadsim = loadsim
        self.output = output



class ObserverService(SimService):
    """
    A service that adds and removes a L{LoadSimulator}'s set of observers at
    start and stop time.
    """

    def startService(self):
        """
        Start observing.
        """
        super(ObserverService, self).startService()
        for obs in self.loadsim.observers:
            addObserver(obs.observe)


    def stopService(self):
        super(ObserverService, self).stopService()
        for obs in self.loadsim.observers:
            removeObserver(obs.observe)



class SimulatorService(SimService):
    """
    A service that starts the L{CalendarClientSimulator} associated with the
    L{LoadSimulator} and stops it at shutdown.
    """

    def startService(self):
        super(SimulatorService, self).startService()
        self.clientsim = self.loadsim.createSimulator()
        arrivalPolicy = self.loadsim.createArrivalPolicy()
        arrivalPolicy.run(self.clientsim)


    @inlineCallbacks
    def stopService(self):
        yield super(SimulatorService, self).stopService()
        yield self.clientsim.stop()



class ReporterService(SimService):
    """
    A service which reports all the results from all the observers on a load
    simulator when it is stopped.
    """

    def startService(self):
        """
        Start observing.
        """
        super(ReporterService, self).startService()
        self.loadsim.reporter = self


    def stopService(self):
        """
        Emit the report to the specified output file.
        """
        super(ReporterService, self).stopService()
        self.generateReport(self.output)


    def generateReport(self, output):
        """
        Emit the report to the specified output file.
        """
        failures = []
        for obs in self.loadsim.observers:
            obs.report(output)
            failures.extend(obs.failures())
        if failures:
            output.write('\n*** FAIL\n')
            output.write('\n'.join(failures))
            output.write('\n')
        else:
            output.write('\n*** PASS\n')


class ProcessProtocolBridge(ProcessProtocol):

    def __init__(self, spawner, proto):
        self.spawner = spawner
        self.proto = proto
        self.deferred = Deferred()


    def connectionMade(self):
        self.transport.getPeer = self.getPeer
        self.transport.getHost = self.getHost
        self.proto.makeConnection(self.transport)


    def getPeer(self):
        return "Peer:PID:" + str(self.transport.pid)


    def getHost(self):
        return "Host:PID:" + str(self.transport.pid)


    def outReceived(self, data):
        self.proto.dataReceived(data)


    def errReceived(self, error):
        from twisted.python.log import msg
        msg("stderr received from " + str(self.transport.pid))
        msg("    " + repr(error))


    def processEnded(self, reason):
        self.proto.connectionLost(reason)
        self.deferred.callback(None)
        self.spawner.bridges.remove(self)



class WorkerSpawnerService(SimService):

    def startService(self):
        from contrib.performance.loadtest.ampsim import Manager
        super(WorkerSpawnerService, self).startService()
        self.bridges = []
        for workerID, worker in enumerate(self.loadsim.workers):
            bridge = ProcessProtocolBridge(
                self, Manager(self.loadsim, workerID, len(self.loadsim.workers),
                              self.output)
            )
            self.bridges.append(bridge)
            sh = '/bin/sh'
            self.loadsim.reactor.spawnProcess(
                bridge, sh, [sh, "-c", worker], env=environ
            )


    def stopService(self):
        TERMINATE_TIMEOUT = 30.0
        def killThemAll(name):
            for bridge in self.bridges:
                bridge.transport.signalProcess(name)
        killThemAll("TERM")
        self.loadsim.reactor.callLater(TERMINATE_TIMEOUT, killThemAll, "KILL")
        return gatherResults([bridge.deferred for bridge in self.bridges])



main = LoadSimulator.main

if __name__ == '__main__':
    main()
