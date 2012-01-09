#!/usr/bin/env python
# -*- test-case-name: calendarserver.tools.test.test_upgrade -*-
##
# Copyright (c) 2006-2011 Apple Inc. All rights reserved.
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
This tool allows any necessary upgrade to complete, then exits.
"""

import os
import sys

from twisted.python.text import wordWrap
from twisted.python import log
from twisted.python.usage import Options, UsageError

from twistedcaldav.stdconfig import DEFAULT_CONFIG_FILE
from calendarserver.tools.cmdline import utilityMain
from twisted.application.service import Service


from twext.python.log import setLogLevelForNamespace

def usage(e=None):
    if e:
        print e
        print ""
    try:
        UpgradeOptions().opt_help()
    except SystemExit:
        pass
    if e:
        sys.exit(64)
    else:
        sys.exit(0)


description = '\n'.join(
    wordWrap(
        """
        Usage: calendarserver_upgrade [options] [input specifiers]\n
        """ + __doc__,
        int(os.environ.get('COLUMNS', '80'))
    )
)

class UpgradeOptions(Options):
    """
    Command-line options for 'calendarserver_upgrade'

    @ivar upgradeers: a list of L{DirectoryUpgradeer} objects which can identify the
        calendars to upgrade, given a directory service.  This list is built by
        parsing --record and --collection options.
    """

    synopsis = description

    optParameters = [['config', 'f', DEFAULT_CONFIG_FILE,
                      "Specify caldavd.plist configuration path."]]

    def __init__(self):
        super(UpgradeOptions, self).__init__()
        self.upgradeers = []
        self.outputName = '-'


    def opt_output(self, filename):
        """
        Specify output file path (default: '-', meaning stdout).
        """
        self.outputName = filename

    opt_o = opt_output


    def openOutput(self):
        """
        Open the appropriate output file based on the '--output' option.
        """
        if self.outputName == '-':
            return sys.stdout
        else:
            return open(self.outputName, 'wb')



class UpgraderService(Service, object):
    """
    Service which runs, exports the appropriate records, then stops the reactor.
    """

    def __init__(self, store, options, output, reactor, config):
        super(UpgraderService, self).__init__()
        self.store   = store
        self.options = options
        self.output  = output
        self.reactor = reactor
        self.config = config
        self._directory = None


    def startService(self):
        """
        Immediately stop.  The upgrade will have been run before this.
        """
        self.output.write("Upgrade complete, shutting down.\n")
        from twisted.internet import reactor
        from twisted.internet.error import ReactorNotRunning
        try:
            reactor.stop()
        except ReactorNotRunning:
            # I don't care.
            pass


    def stopService(self):
        """
        Stop the service.  Nothing to do; everything should be finished by this
        time.
        """


def main(argv=sys.argv, stderr=sys.stderr, reactor=None):
    """
    Do the export.
    """
    if reactor is None:
        from twisted.internet import reactor

    options = UpgradeOptions()
    try:
        options.parseOptions(argv[1:])
    except UsageError, e:
        usage(e)

    try:
        output = options.openOutput()
    except IOError, e:
        stderr.write("Unable to open output file for writing: %s\n" %
                     (e))
        sys.exit(1)

    def makeService(store):
        from twistedcaldav.config import config
        return UpgraderService(store, options, output, reactor, config)

    def onlyUpgradeEvents(event):
        output.write(log.textFromEventDict(event)+"\n")

    setLogLevelForNamespace(None, "debug")
    log.addObserver(onlyUpgradeEvents)
    utilityMain(options["config"], makeService, reactor)
