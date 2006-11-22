##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
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
# DRI: David Reid, dreid@apple.com
##

"""
Examples:

  caladmin users

  caladmin purge

  caladmin backup

  caladmin restore
"""

import sys, os

from twisted.python import usage
from twisted.python import filepath

from plistlib import readPlist

from caladmin import commands
from caladmin import formatters

from twistedcaldav.caldavd import caldavd_defaults, caldavd

class AdminOptions(usage.Options):
    recursing = 0
    params = ()

    optParameters = [
        ['config', 'c', caldavd().plistfile, "Path to the caldavd.plist"],
        ['format', 'f', 'plain', ("Select an appropriate output formatter: "
                                  "%s" % (formatters.listFormatters(),))]
        ]

    def __init__(self):
        usage.Options.__init__(self)

        self.config = None

    def parseArgs(self, *rest):
        self.params += rest

    def parseOptions(self, options=None):
        if not options:
            options = ['--help']

        if options == ['--help']:
            self.subCommands = commands.genSubCommandsDef()

        usage.Options.parseOptions(self, options)
    
    def postOptions(self):
        if self.recursing:
            return

        if self['config']:
            self['config'] = os.path.abspath(self['config'])
            try:
                self.config = readPlist(self['config'])
            except IOError, err:
                sys.stderr.write(("Could not open configuration file: %s (%s)\n"
                                  ) % (err.filename,
                                       err.strerror))
                sys.stderr.flush()

                self.config = caldavd_defaults

        self.root = filepath.FilePath(self.config['DocumentRoot'])
        self.calendarCollection = self.root.child('calendars')
        self.principalCollection = self.root.child('principals')

        lf = formatters.listFormatters()
        lf.sort()

        if self['format'] in lf:
            self.formatter = formatters.getFormatter(self['format'])()
        else:
            raise usage.UsageError("Please specify a valid formatter: %s" % (
                    ', '.join(lf)))

        sc = commands.listCommands()
        sc.sort()

        self.subCommands = commands.genSubCommandsDef()

        self.recursing = 1

        self.parseOptions(self.params)

        if self.subCommand not in sc:
            raise usage.UsageError("Please select one of: %s" % (
                    ', '.join(sc)))

    
def run():
    config = AdminOptions()

    try:
        config.parseOptions(sys.argv[1:])

    except usage.UsageError, ue:
        print config
        if len(sys.argv) > 1:
            cmd = sys.argv[1]
        else:
            cmd = sys.argv[0]

        print "%s: %s" % (cmd, ue)


    except KeyboardInterrupt:
        sys.exit(1)
