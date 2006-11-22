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


from twisted.python import reflect

class SubCommand(usage.Options):
    name = None
    shortcut = None
    help = "FIXME"
    action = None

    params = ()

    def parseArgs(self, *rest):
        self.params += rest

    def postOptions(self):
        reflect.namedAny(self.action)(self).run()


class QuotaOptions(SubCommand):
    name = 'quotas'
    help = 'Retrieve quota information for principals'
    action = 'caladmin.quotas.QuotaAction'

    optFlags = [
        ['human', 'h', 'Display quota values in a human readable form.'],
        ['megabytes', 'm', 'Display quota values in megabytes'],
        ['kilobytes', 'k', 'Display quota values in kilobytes'],
        ]
         
    def __init__(self):
        SubCommand.__init__(self)

        self['types'] = []

    def opt_users(self):
        """Show Quotas for user calendars.
        """
        
        self['types'].append('users')
    opt_u = opt_users

    def opt_groups(self):
        """Show Quotas for group calendars.
        """
        
        self['types'].append('groups')
    opt_g = opt_groups

    def opt_resources(self):
        """Show Quotas for resource calendars.
        """
        
        self['types'].append('resources')
    opt_r = opt_resources


registerCommand(QuotaOptions)


class UserOptions(SubCommand):
    name = 'users'
    help = 'Retrieve information about and perform actions on users.'
    action = 'caladmin.users.UserAction'

    optFlags = [
        ['list', '1', 'List only usernames, one per line.'],
        ['disabled', 'd', 'Limit display to disabled users.'],
        ['detailed', None, 'Detailed statistics for each account.'],
        ]

registerCommand(UserOptions)


class PurgeOptions(SubCommand):
    name = 'purge'
    help = ('Keep your store from becoming unnecessarily large by purging '
            'old events.')
    action = 'caladmin.purge.PurgeAction'

    optParameters = [
        ['days', 'n', 30, 'Age threshold for purging events.'],
        ]

registerCommand(PurgeOptions)


class StatsOptions(SubCommand):
    name = 'stats'
    help = ('Overall usage statistics.')
    action = 'caladmin.stats.StatsAction'


registerCommand(StatsOptions)

from twisted.python import filepath

class LogOptions(SubCommand):
    name = 'logs'
    help = ('Gather and report useful information from the logfiles.')
    action = 'caladmin.logs.LogAction'

    def __init__(self):
        SubCommand.__init__(self)

        self['logfile'] = None

    def opt_logfile(self, logfile):
        """Path to the log file to be analyzed.
        [default: /var/log/caldavd/server.log]
        """ 

        self['logfile'] = filepath.FilePath(logfile)

    opt_l = opt_logfile

    def postOptions(self):
        if not self['logfile']:
            self['logfile'] = filepath.FilePath(
                self.parent.config['ServerLogFile'])

        SubCommand.postOptions(self)

registerCommand(LogOptions)
