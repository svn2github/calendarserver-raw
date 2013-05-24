##
# Copyright (c) 2005-2013 Apple Inc. All rights reserved.
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
Shared main-point between utilities.
"""

from calendarserver.tap.caldav import CalDAVServiceMaker, CalDAVOptions
from calendarserver.tap.util import checkDirectories
from calendarserver.tools.util import loadConfig, autoDisableMemcached

from twext.python.log import StandardIOObserver

from twistedcaldav.config import ConfigurationError
from twisted.internet.defer import inlineCallbacks

import sys
from calendarserver.tap.util import getRootResource
from twisted.application.service import Service
from errno import ENOENT, EACCES
from twext.enterprise.queue import NonPerformingQueuer

# TODO: direct unit tests for these functions.

def utilityMain(configFileName, serviceClass, reactor=None, serviceMaker=CalDAVServiceMaker, patchConfig=None, onShutdown=None, verbose=False):
    """
    Shared main-point for utilities.

    This function will:

        - Load the configuration file named by C{configFileName},
        - launch a L{CalDAVServiceMaker}'s with the C{ProcessType} of
          C{"Utility"}
        - run the reactor, with start/stop events hooked up to the service's
          C{startService}/C{stopService} methods.

    It is C{serviceClass}'s responsibility to stop the reactor when it's
    complete.

    @param configFileName: the name of the configuration file to load.
    @type configuration: C{str}

    @param serviceClass: a 1-argument callable which takes an object that
        provides L{ICalendarStore} and/or L{IAddressbookStore} and returns an
        L{IService}.

    @param patchConfig: a 1-argument callable which takes a config object
        and makes and changes necessary for the tool.

    @param onShutdown: a 0-argument callable which will run on shutdown.

    @param reactor: if specified, the L{IReactorTime} / L{IReactorThreads} /
        L{IReactorTCP} (etc) provider to use.  If C{None}, the default reactor
        will be imported and used.
    """

    # Install std i/o observer
    if verbose:
        observer = StandardIOObserver()
        observer.start()

    if reactor is None:
        from twisted.internet import reactor
    try:
        config = loadConfig(configFileName)
        if patchConfig is not None:
            patchConfig(config)

        checkDirectories(config)

        config.ProcessType = "Utility"
        config.UtilityServiceClass = serviceClass

        autoDisableMemcached(config)

        maker = serviceMaker()

        # Only perform post-import duties if someone has explicitly said to
        maker.doPostImport =  getattr(maker, "doPostImport", False)

        options = CalDAVOptions
        service = maker.makeService(options)

        reactor.addSystemEventTrigger("during", "startup", service.startService)
        reactor.addSystemEventTrigger("before", "shutdown", service.stopService)
        if onShutdown is not None:
            reactor.addSystemEventTrigger("before", "shutdown", onShutdown)

    except (ConfigurationError, OSError), e:
        sys.stderr.write("Error: %s\n" % (e,))
        return

    reactor.run()



class WorkerService(Service):

    def __init__(self, store):
        self._store = store
        # Work can be queued but will not be performed by the command line tool
        store.queuer = NonPerformingQueuer()


    def rootResource(self):
        try:
            from twistedcaldav.config import config
            rootResource = getRootResource(config, self._store)
        except OSError, e:
            if e.errno == ENOENT:
                # Trying to re-write resources.xml but its parent directory does
                # not exist.  The server's never been started, so we're missing
                # state required to do any work.
                raise ConfigurationError(
                    "It appears that the server has never been started.\n"
                    "Please start it at least once before running this tool.")
            elif e.errno == EACCES:
                # Trying to re-write resources.xml but it is not writable by the
                # current user.  This most likely means we're in a system
                # configuration and the user doesn't have sufficient privileges
                # to do the other things the tool might need to do either.
                raise ConfigurationError("You must run this tool as root.")
            else:
                raise
        return rootResource

    @inlineCallbacks
    def startService(self):
        from twisted.internet import reactor
        try:
            yield self.doWork()
        except ConfigurationError, ce:
            sys.stderr.write("Error: %s\n" % (str(ce),))
        except Exception, e:
            sys.stderr.write("Error: %s\n" % (e,))
            raise
        finally:
            reactor.stop()

