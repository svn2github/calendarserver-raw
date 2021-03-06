##
# Copyright (c) 2006-2007 Apple Inc. All rights reserved.
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
 Log Stats:
  # Invitations sent per day/week/month
  # bytes out (bytes in not provided in the current log format.)/
  # requests
  user agents

"""

import plistlib
import sys
import time

from twistedcaldav.admin import util

PLIST_VERSION = 3

statsTemplate = plistlib.Dict(
    version=PLIST_VERSION,
    bytesOut="0",
    startDate="",
    endDate="",
    requestStats=plistlib.Dict(),
    timeOfDayStats=[0] * 96,
    requestByTimeOfDayStats=plistlib.Dict(),
    invitations=plistlib.Dict(
        day=0, 
        week=0, 
        month=0, 
        ),
    userAgents=plistlib.Dict(),
    )

def _strAdd(value, add):
    return str(long(value) + add)

class Stats(object):
    def __init__(self, fp):
        self.fp = fp
        self._data = None

        if self.fp.exists():
            self._data = plistlib.readPlist(self.fp.path)
            if self._data.version != PLIST_VERSION:
                self._data = None
        
        if self._data is None:
            self._data = statsTemplate
            self.save()

    def addDate(self, date):
        if not self._data.startDate:
            self._data.startDate = date
        self._data.endDate = date

    def getDateRange(self):
        return (self._data.startDate, self._data.endDate)

    def getBytes(self):
        return long(self._data.bytesOut)

    def addBytes(self, bytes):
        self._data.bytesOut = _strAdd(self._data.bytesOut, bytes)

    def addRequestStats(self, request, status, bytes, time):
        if request in self._data.requestStats:
            request_stat = self._data.requestStats[request]
            old_num = long(request_stat['num'])
            request_stat['num'] = _strAdd(request_stat['num'], 1)
            if status >= 200 and status < 300:
                request_stat['numOK'] = _strAdd(request_stat['numOK'], 1)
            elif status == 500:
                request_stat['numISE'] = _strAdd(request_stat['numISE'], 1)
            elif status >= 400 and status < 600:
                request_stat['numBAD'] = _strAdd(request_stat['numBAD'], 1)
            else:
                request_stat['numOther'] = _strAdd(request_stat['numOther'], 1)
            if bytes < long(request_stat['minbytes']):
                request_stat['minbytes'] = str(bytes)
            if bytes > long(request_stat['maxbytes']):
                request_stat['maxbytes'] = str(bytes)
            request_stat['avbytes'] = str((long(request_stat['avbytes']) * old_num + bytes) / (old_num + 1))
            if time < request_stat['mintime']:
                request_stat['mintime'] = time
            if time > request_stat['maxtime']:
                request_stat['maxtime'] = time
            request_stat['avtime'] = (request_stat['avtime'] * old_num + time) / (old_num + 1)
        else:
            self._data.requestStats[request] = {}
            request_stat = self._data.requestStats[request]
            request_stat['num'] = "1"
            request_stat['numOK'] = "0"
            request_stat['numBAD'] = "0"
            request_stat['numISE'] = "0"
            request_stat['numOther'] = "0"
            if status >= 200 and status < 300:
                request_stat['numOK'] = "1"
            elif status == 500:
                request_stat['numISE'] = "1"
            elif status >= 400 and status < 600:
                request_stat['numBAD'] = "1"
            else:
                request_stat['numOther'] = "1"
            request_stat['minbytes'] = str(bytes)
            request_stat['maxbytes'] = str(bytes)
            request_stat['avbytes'] = str(bytes)
            request_stat['mintime'] = time
            request_stat['maxtime'] = time
            request_stat['avtime'] = time
    
    def getRequestStats(self):
        return self._data.requestStats

    def addTimeOfDayStats(self, request, time):
        hour, minute = time.split(":")
        bucket = int(hour) * 4 + divmod(int(minute), 15)[0]
        self._data.timeOfDayStats[bucket] = self._data.timeOfDayStats[bucket] + 1
        if request not in self._data.requestByTimeOfDayStats:
            self._data.requestByTimeOfDayStats[request] = [0] * 96
        self._data.requestByTimeOfDayStats[request][bucket] = self._data.requestByTimeOfDayStats[request][bucket] + 1
    
    def getTimeOfDayStats(self):
        return self._data.timeOfDayStats

    def getRequestByTimeOfDayStats(self):
        return self._data.requestByTimeOfDayStats

    def addUserAgent(self, useragent):
        if useragent in self._data.userAgents:
            self._data.userAgents[useragent] += 1
        else:
            self._data.userAgents[useragent] = 1

    def getUserAgents(self):
        return self._data.userAgents

    def save(self):
        plistlib.writePlist(self._data, self.fp.path)

NORMAL = 1
INDATE = 2
INSTRING = 3

def parseCLFLine(line):
    state = NORMAL
    elements = []
    
    rest = []

    for c in line:
        if c == ' ':
            if state == NORMAL:
                elements.append(''.join(rest))
                rest = []

            elif state == INSTRING or state == INDATE:
                rest.append(c)
                    
        elif c == '[':
            if state != INSTRING:
                state = INDATE
                        
        elif c == ']':
            if state == INDATE:
                state = NORMAL

        elif c == '"':
            if state == INSTRING:
                state = NORMAL
            else:
                state = INSTRING
        elif c == '\n':
            if state == NORMAL:
                elements.append(''.join(rest))
                rest = []

        else:
            rest.append(c)

    return elements

                    
class LogAction(object):
    def __init__(self, config):
        self.config = config

        self.noOutput = self.config['nooutput']
        self.readOnly = self.config['readonly']

        self.logfile = self.config['logfile']
        self.stats = Stats(self.config['statsfile'])

    def run(self):

        if not self.readOnly:
            total_count = -1
            for total_count, line in enumerate(self.logfile.open()):
                pass
            total_count += 1
            print "Reading file: %s (%d lines)" % (self.logfile.basename(), total_count,)
            print "|" + "----|" * 10 + "\n.",
            last_count = 0
            start_time = time.time()
            for line_count, line in enumerate(self.logfile.open()):
                if (line.startswith('Log opened') or 
                    line.startswith('Log closed')):
                    continue
                else:
                    pline = parseCLFLine(line)
                    
                    self.stats.addDate(pline[3])
                    self.stats.addBytes(int(pline[6]))
                    self.stats.addRequestStats(pline[4].split(' ')[0], int(pline[5]), int(pline[6]), float(pline[9][:-3]))
                    self.stats.addTimeOfDayStats(pline[4].split(' ')[0], pline[3][pline[3].find(":") + 1:][:5])

                    if len(pline) > 7:
                        self.stats.addUserAgent(pline[8])
                        
                if (50 * line_count) / total_count > last_count:
                    sys.stdout.write(".")
                    sys.stdout.flush()
                    last_count = (50 * line_count) / total_count

            print ".\nTime taken: %.1f secs\n" % (time.time() - start_time)

            self.stats.save()    

        if not self.noOutput:
            report = {
                'type': 'logs',
                'data': {
                    'dateRange': self.stats.getDateRange(),
                    'bytesOut': util.prepareByteValue(self.config, 
                                                      self.stats.getBytes()),
                    'requestStats': self.stats.getRequestStats(),
                    'timeOfDayStats': self.stats.getTimeOfDayStats(),
                    'requestByTimeOfDayStats': self.stats.getRequestByTimeOfDayStats(),
                    'userAgents': self.stats.getUserAgents(),
                    }
                }

            return report

        return None
