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

""" "pluggable" subcommands for the caladmin script
"""

from twisted.python import usage

COMMANDS = {}

def registerCommand(command):
    COMMANDS[command.name] = command

def listCommands():
    return COMMANDS.keys()

def genSubCommandsDef():
    sc = listCommands()
    sc.sort()

    for name in sc:
        command = COMMANDS[name]
        yield [command.name, command.shortcut, command, command.help]


# Some common parameter definitions

PARAM_DOCROOT = ['docroot', 'D', '/Library/CalendarServer/Documents', 
                 'Document root for the calendar server data to back up.']


class SubCommand(usage.Options):
    name = None
    shortcut = None
    help = "FIXME"
    action = None

    params = ()

    def parseArgs(self, *rest):
        self.params += rest

    def postOptions(self):
        self.action(self).run()


from twisted.internet import reactor
from twisted.internet.defer import maybeDeferred
from twisted.python.failure import Failure

class TwistedSubCommand(SubCommand):
    """Subcommand subclass that calls it's action's run method from within a 
    reactor."""

    def postOptions(self):

        def _log(failure):
            failure.printTraceback()

        def _runRun():
            try:
                d = maybeDeferred(self.action(self).run)
                d.addErrback(_log).addBoth(lambda _: reactor.stop())
            except:
                failure = Failure()
                failure.printTraceback()

                reactor.stop()


        reactor.callLater(0, _runRun)
        reactor.run()


from caladmin.users import UserAction

class UserOptions(TwistedSubCommand):
    name = 'users'
    help = 'Retrieve information about and perform actions on users.'
    action = UserAction

    optFlags = [
        ['list', '1', 'List only usernames, one per line.'],
        ['disabled', 'd', 'Limit display to disabled users.']
        ]

    optParameters = [
        ['server', 's', 'http://localhost:8008/', 
         'The url of the calendar server to query for user information.'],
        ['username', 'u', None, 
         'The username to connect to the calendar server'],
        ['password', 'p', None,
         'The password'],
        ]

registerCommand(UserOptions)


from purge import PurgeAction

class PurgeOptions(SubCommand):
    name = 'purge'
    help = ('Keep your store from becoming unnecessarily large by purging '
            'old events.')
    action = PurgeAction

    optParameters = [
        ['days', 'n', 30, 'Age threshold for purging events.'],
        PARAM_DOCROOT
        ]

registerCommand(PurgeOptions)
