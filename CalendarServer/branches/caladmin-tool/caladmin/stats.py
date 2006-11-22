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

 Overall Stats:
  # of accounts
  # of calendars
  # of events

"""
import os
import xattr
import commands

from twisted.web import microdom

from twistedcaldav import ical

from caladmin import util        

class StatsAction(object):
    def __init__(self, config):
        self.config = config
        self.formatter = self.config.parent.formatter
        self.root = self.config.parent.root
        self.calendarCollection = self.config.parent.calendarCollection
        self.principalCollection = self.config.parent.principalCollection
        
        self.calCount = 0
        self.eventCount = 0
        self.todoCount = 0

        self.gatherers = [
            self.getAccountCount,
            self.getGroupCount,
            self.getResourceCount,
            self.getCalendarCount,
            self.getEventCount,
            self.getTodoCount,
            self.getDiskUsage]

    def getDiskUsage(self):
        return ("Disk Usage", 
                util.prepareByteValue(self.config,
                                      util.getDiskUsage(self.root)))

    def getAccountCount(self):
        return ("# Accounts", 
                len(util.getPrincipalList(
                    self.principalCollection,
                    'users')))

    def getGroupCount(self):
        return ("# Groups", 
                len(util.getPrincipalList(
                    self.principalCollection,
                    'groups')))

    def getResourceCount(self):
        return ("# Resources", 
                len(util.getPrincipalList(
                    self.principalCollection,
                    'resources')))

    def getEventCount(self):
        return ("# Events", self.eventCount)

    def getTodoCount(self):
        return ("# Todos", self.todoCount)

    def getCalendarCount(self):
        return ("# Calendars", self.calCount)

    def printStatistics(self, head, stats):
        self.formatter.printRow([head], 16)

        for stat in stats:
            self.formatter.printRow(stat, 16)

    def run(self):
        assert self.root.exists()
        stats = []

        (self.calCount, 
         self.eventCount, 
         self.todoCount) = util.getCalendarDataCounts(self.calendarCollection)

        for gatherer in self.gatherers:
            stats.append(gatherer())

        self.printStatistics("Overall Statistics", stats)

