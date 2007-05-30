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
            print "No report printer found for report type %r" % (report,)
            self.report_default(report)
    
    def report_default(self, report):
        import pprint

        preport = pprint.pformat(report)

        self.write(''.join([preport, '\n']))
        self.close()


class PPrintFormatter(BaseFormatter):
    name = "pprint"

registerFormatter(PPrintFormatter)


class PlainFormatter(BaseFormatter):
    name = "plain"

    def writeLine(self, fields, spacing=None):

        if not spacing:
            spacing = self.options.get('spacing', 16)

        for f in fields:
            if isinstance(f, float):
                p = "% 9.2f" % (f,)
            elif isinstance(f, int):
                p = "% 9d" % (f,)
            else:
                p = str(f)
            self.write(p)
            self.write(' '*(int(spacing) - len(p)))

        self.write('\n')

    def writeTable(self, report, fields, headings):
        if self.options.has_key('fields'):
            fields = self.options.get('fields', '').split(',')

        self.writeLine((headings[f] for f in fields))

        for record in report['records']:
            self.writeLine((record[f] for f in fields))

    def writeMap(self, reportmap, fields, headings):
        self.writeLine((headings[f] for f in fields))

        for key, value in reportmap.iteritems():
            values = (key,)
            values += tuple(value[f] for f in fields[1:])
            self.writeLine(values)

    def writeFrequencies(self, frequencies):
        
        width = len(frequencies)
        plot = [[" "] * width for ignore in range(20)]
        plot.append(["|---"] * 24)
        plot.append(["%02d  " % d for d in range(24)])
        
        max_count = 0
        for freq in frequencies:
            max_count = max(freq, max_count)
            
        for column, freq in enumerate(frequencies):
            if freq == 0:
                continue
            scaled = (20 * freq) / max_count
            for row in range(20):
                if row <= scaled:
                    plot[19 - row][column] = "*"
        
        self.write("\n".join(["".join(p) for p in plot]))
        self.write("\n")

    def writeReport(self, report, name, fields, headings):
        if self.options.has_key('fields'):
            fields = self.options.get('fields', '').split(',')
        
        if name:
            self.write('%s:\n' % (name,))

        for f in fields:
            self.write('  %s: %s\n' % (headings[f], report['data'][f]))

    def report_principals(self, report):
        fields = ('principalName', 'calendarCount', 'eventCount', 'todoCount',
                  'quotaRoot', 'quotaUsed', 'quotaAvail')

        headings = {
            'principalName': 'Name',
            'calendarCount': '# Calendars',
            'eventCount': '# Events',
            'todoCount': '# Todos',
            'quotaRoot': 'Quota',
            'quotaUsed': 'Used',
            'quotaAvail': 'Available',
            'disabled': 'Disaabled',
            'quotaFree': 'Free %',
            'calendarHome': 'Home',
            }

        self.writeTable(report, fields, headings)

    report_users = report_groups = report_resources = report_locations = report_principals

    def report_stats(self, report):
        fields = ('accountCount', 'groupCount', 'resourceCount', 'locationCount',
                  'calendarCount', 'eventCount', 
                  'todoCount', 'diskUsage')

        headings = {
            'accountCount':  '# Accounts ',
            'groupCount':    '# Groups   ',
            'resourceCount': '# Resources',
            'locationCount': '# Locations',
            'calendarCount': '# Calendars',
            'eventCount':    '# Events   ',
            'todoCount':     '# Todos    ',
            'diskUsage':     'Disk Usage ',
            }

        self.writeReport(report, 'Statistics', fields, headings)

    def report_logs(self, report):
        self.write('Log Statistics:\n\n')

        self.write('  Start Date: %s\n  End Date  :%s\n\n' % report['data']['dateRange'])

        self.write('  Bytes Out: %s\n\n' % (report['data']['bytesOut'],))
        self.write('  # Requests:\n')

        fields = (
            'method',
            'num',
            'numOK',
            'numBAD',
            'numISE',
            'numOther',
            'minbytes',
            'avbytes',
            'maxbytes',
            'mintime',
            'avtime',
            'maxtime',
        )
        headings = {
            'method':        'Method',
            'num':           '# Requests',
            'numOK':         '     # OK',
            'numBAD':        '    # BAD',
            'numISE':        ' # Failed',
            'numOther':      '  # Other',
            'minbytes':      'Min. bytes',
            'avbytes':       ' Av. bytes',
            'maxbytes':      'Max. bytes',
            'mintime':       'Min. time',
            'avtime':        ' Av. time',
            'maxtime':       'Max. time',
        }
        self.writeMap(report['data']['requestStats'], fields, headings)
        self.write('\n')

        self.write('  # Requests by time of day:\n')
        self.writeFrequencies(report['data']['timeOfDayStats'])
        self.write('\n')

        self.write('  User Agents:\n')

        for ua, count in report['data']['userAgents'].iteritems():
            self.write('    %s: %s\n' % (ua, count))

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
        
    report_users = report_groups = report_resources = report_locations = report_principals

    def report_stats(self, report):
        if 'fieldnames' not in self.options:
            self.options['fieldnames'] = sorted(report['data'].keys())

        self.writeList(self.options['fieldnames'],
                       [report['data']])
                
    report_logs = report_stats

registerFormatter(CsvFormatter)

import plistlib

class PlistFormatter(BaseFormatter):
    name = "plist"

    def report_principals(self, report):
        plist = plistlib.Dict()

        plist[report['type']] = list(report['records'])

        plistlib.writePlist(plist, self.dest)

    report_users = report_groups = report_resources = report_locations = report_principals

    def report_stats(self, report):
        plist = plistlib.Dict()
        plist[report['type']] = report['data']

        plistlib.writePlist(plist, self.dest)

    report_logs = report_stats

registerFormatter(PlistFormatter)
