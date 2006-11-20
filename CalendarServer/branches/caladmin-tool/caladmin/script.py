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

from caladmin import commands
from caladmin import formatters

class AdminOptions(usage.Options):
    recursing = 0
    params = ()

    optParameters = [
        ['format', 'f', 'plain', "Select an appropriate output formatter: %s" % (formatters.listFormatters())]
        ]

    def parseArgs(self, *rest):
        self.params += rest

    def subCommands(self):
        return commands.genSubCommandsDef()

    subCommands = property(subCommands)
    
    def postOptions(self):
        lf = formatters.listFormatters()
        lf.sort()

        if self['format'] in lf:
            self.formatter = formatters.getFormatter(self['format'])()
        else:
            raise usage.UsageError("Please specify a valid formatter: %s" % (
                    ', '.join(lf)))

    
def run():
    config = AdminOptions()

    try:
        config.parseOptions()

    except usage.UsageError, ue:
        print config
        if len(sys.argv) > 1:
            cmd = sys.argv[1]
        else:
            cmd = sys.argv[0]

        print "%s: %s" % (cmd, ue)


    except KeyboardInterrupt:
        sys.exit(1)
