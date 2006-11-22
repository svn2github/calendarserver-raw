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
Statisitcs Types:

 Account Stats: 
  # of calendars
  # of events
  # storage used (including things that don't count against quota?)
  Last login?

 Overall Stats:
  # of accounts
  # of calendars
  # of events

 Log Stats:
  # Invitations sent per day/week/month
  # bytes i/o
  # requests
  user agents

"""
import os
import xattr
import commands

from twisted.web import microdom

from caladmin.util import prepareByteValue

def getResourceType(fp):
    rt = 'WebDAV:{DAV:}resourcetype'
    x = xattr.xattr(fp.path)
    if not x.has_key(rt):
        return None
    
    collection = False

    type = None

    dom = microdom.parseString(x[rt])
    rt = microdom.getElementsByTagName(dom, 'resourcetype')

    for child in rt[0].childNodes:
        if child.tagName == 'collection':
            collection = True
        else:
            type = child.tagName

    return (collection, type)
        

class StatsAction(object):
    def __init__(self, config):
        self.config = config
        self.formatter = self.config.parent.formatter
        self.root = self.config.parent.root
        self.calendarCollection = self.config.parent.calendarCollection
        self.principalCollection = self.config.parent.principalCollection
        
        self.gatherers = [
            self.getAccountCount,
            self.getGroupCount,
            self.getResourceCount,
            self.getCalendarCount,
            self.getEventCount,
            self.getDiskUsage]

    def getDiskUsage(self):
        output = commands.getoutput(' '.join(
                ['/usr/bin/du', '-s', self.root.path]))

        return ("Disk Usage", prepareByteValue(self.config,
                                               int(output.split()[0])))

    def _getPrincipalList(self, type):
        typeRoot = self.principalCollection.child(type)
        assert typeRoot.exists()
        
        pl = []
        
        for child in typeRoot.listdir():
            if child not in ['.db.sqlite']:
                pl.append(child)

        return pl

    def getAccountCount(self):
        return ("# Accounts", len(self._getPrincipalList('users')))

    def getGroupCount(self):
        return ("# Groups", len(self._getPrincipalList('groups')))

    def getResourceCount(self):
        return ("# Resources", len(self._getPrincipalList('resources')))

    def getEventCount(self):
        return ("# Events", 0)

    def getCalendarCount(self):
        count = 0
        for child in self.calendarCollection.walk():
            if child.isdir():
                if getResourceType(child) == (True, 'calendar'):
                    count += 1

        return ("# Calendars", count)

    def printStatistics(self, head, stats):
        self.formatter.printRow([head], 16)

        for stat in stats:
            self.formatter.printRow(stat, 16)

    def run(self):
        assert self.root.exists()
        stats = []

        for gatherer in self.gatherers:
            stats.append(gatherer())

        self.printStatistics("Overall Statistics", stats)

