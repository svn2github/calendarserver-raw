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

import sys

FORMATTERS = {}

def registerFormatter(formatter):
    FORMATTERS[formatter.name] = formatter

def listFormatters():
    return FORMATTERS.keys()

def getFormatter(short):
    return FORMATTERS[short]


class BaseFormatter(object):
    config = None

    def __init__(self, dest=None, options=None):
        self.dest = dest
        
        if not self.dest:
            self.dest = sys.stdout

        self.options = options

        if not options:
            self.options = {}

        self.reportTypes = []

        for attr in self.__dict__:
            if attr.startswith('report_'):
                self.reportTypes.append(attr.split('_', 1)[1])

    def write(self, data):
        self.dest.write(data)
        self.dest.flush()

    def close(self):
        self.dest.close()

    def printReport(self, report):
        reportPrinter = getattr(self, 'report_%s' % (report['type'],), None)

        if reportPrinter:
            reportPrinter(report)

        else:
            self.report_default(report)
    
    def report_default(self, report):
        import pprint

        preport = pprint.pformat(report)

        self.write(''.join([preport, '\n']))
        self.close()


class PlainFormatter(BaseFormatter):
    name = "plain"

registerFormatter(PlainFormatter)


import csv

class CsvFormatter(BaseFormatter):
    name = "csv"

    def writeList(self, fieldnames, l):
        dw = csv.DictWriter(self.dest,
                            **self.options)

        dw.writerow(dict(zip(fieldnames,
                             fieldnames)))

        dw.writerows(l)

    def report_principals(self, report):
        if 'fieldnames' not in self.options:
            self.options['fieldnames'] = [
                'principalName',
                'calendarHome',
                'calendarCount',
                'eventCount',
                'todoCount',
                'disabled',
                'diskUsage',
                'quotaRoot',
                'quotaUsed',
                'quotaAvail',
                'quotaFree']
            
        self.writeDict(self.options['fieldnames'],
                       report['records'])
        
    report_users = report_groups = report_resources = report_principals

    def report_stats(self, report):
        if 'fieldnames' not in self.options:
            self.options['fieldnames'] = report['data'].keys()
            self.options['fieldnames'].sort()

        self.writeList(self.options['fieldnames'],
                       [report['data']])
                
    
registerFormatter(CsvFormatter)

import plistlib

class PlistFormatter(BaseFormatter):
    name = "plist"

    def report_principals(self, report):
        plist = plistlib.Dict()

        plist[report['type']] = list(report['records'])

        plistlib.writePlist(plist, self.dest)

    report_users = report_groups = report_resources = report_principals

    def report_stats(self, report):
        plist = plistlib.Dict()
        plist[report['type']] = report['data']

        plistlib.writePlist(plist, self.dest)

registerFormatter(PlistFormatter)
