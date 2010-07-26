#!/usr/bin/env python
##
# Copyright (c) 2009-2010 Apple Inc. All rights reserved.
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

from gzip import GzipFile
import math
import collections
import datetime
import getopt
import os
import socket
import sys
import tables
import traceback

UTC_OFFSET = -8
UTC_START_HOUR = 5

def safePercent(x, y, multiplier=100):
    return ((multiplier * x) / y) if y else 0

responseCountBuckets = (
    (10, "(a):0-10"),
    (100, "(b):11-100"),
    (250, "(c):101-250"),
    (500, "(d):251-500"),
    (1000, "(e):501-1000"),
    (2500, "(f):1001-2500"),
    (5000, "(g):2501-5000"),
    (None, "(h):5001+"),
)

requestSizeBuckets = (
    (     1000, "(a):0-1KB"),
    (     2500, "(b):1-2.5KB"),
    (     5000, "(c):2.5-5KB"),
    (    10000, "(d):5-10KB"),
    (   100000, "(e):10-100KB"),
    (  1000000, "(f):100KB-1MB"),
    ( 10000000, "(g):1-10MB"),
    (100000000, "(h):10-100MB"),
    (     None, "(i):100+MB"),
)

responseSizeBuckets = (
    (    1000, "(a):0-1KB"),
    (    5000, "(b):1-2.5KB"),
    (    5000, "(c):2.5-5KB"),
    (   10000, "(d):5-10KB"),
    (  100000, "(e):10-100KB"),
    ( 1000000, "(f):100KB-1MB"),
    (10000000, "(g):1-10MB"),
    (    None, "(h):10+MB"),
)

requestTimeBuckets = (
    (    10, "(a):0-10ms"),
    (    50, "(b):10-50ms"),
    (   100, "(c):50-100ms"),
    (   250, "(d):100-250ms"),
    (   500, "(e):250-500ms"),
    (  1000, "(f):500ms-1s"),
    (  5000, "(g):1-5s"),
    ( 10000, "(h):5-10s"),
    ( 30000, "(i):10-30s"),
    ( 60000, "(j):30-60s"),
    (120000, "(k):60-120s"),
    (  None, "(l):120s+"),
)

class CalendarServerLogAnalyzer(object):
    
    class LogLine(object):
        
        def __init__(self, userid, logDateTime, logTime, method, uri, status, bytes, referer, client, extended):

            self.userid = userid
            self.logDateTime = logDateTime
            self.logTime = logTime
            self.method = method
            self.uri = uri
            self.status = status
            self.bytes = bytes
            self.referer = referer
            self.client = client
            self.extended = extended

    def __init__(self, startHour=None, endHour=None, filterByUser=None, filterByClient=None):

        self.startHour = startHour
        self.endHour = endHour
        self.filterByUser = filterByUser
        self.filterByClient = filterByClient
        
        self.utcStartHour = UTC_START_HOUR
        self.autoUTC = True
        self.localtimeOffset = UTC_OFFSET
        self.startTime = datetime.datetime.now().replace(microsecond=0)
        
        self.host = socket.getfqdn()
        self.startLog = ""
        self.endLog = ""
        
        self.hourlyTotals = [[0, 0, 0, collections.defaultdict(int), 0.0,] for _ignore in xrange(24)]
        
        self.clientTotals = collections.defaultdict(lambda:[0, set()])
        self.clientByMethodCount = collections.defaultdict(lambda:collections.defaultdict(int))
        self.clientByMethodTotalTime = collections.defaultdict(lambda:collections.defaultdict(float))
        self.clientByMethodAveragedTime = collections.defaultdict(lambda:collections.defaultdict(float))
        self.statusByMethodCount = collections.defaultdict(lambda:collections.defaultdict(int))
        
        self.hourlyByMethodCount = collections.defaultdict(lambda:[0,] * 24)
        self.hourlyByOKMethodCount = collections.defaultdict(lambda:[0,] * 24)
        self.hourlyByMethodTime = collections.defaultdict(lambda:[0.0,] * 24)
        self.averagedHourlyByMethodTime = collections.defaultdict(lambda:[0.0,] * 24)
        self.hourlyPropfindByResponseCount = collections.defaultdict(lambda:[0,] * 24)
        
        self.hourlyByStatus = collections.defaultdict(lambda:[0,] * 24)
        
        self.hourlyByRecipientCount = collections.defaultdict(lambda:[[0, 0] for _ignore in xrange(24)])
        self.averagedHourlyByRecipientCount = collections.defaultdict(lambda:[0,] * 24)
        
        self.responseTimeVsQueueDepth = collections.defaultdict(lambda:[0, 0.0,])
        self.averagedResponseTimeVsQueueDepth = collections.defaultdict(int)
        self.instanceCount = collections.defaultdict(int)

        self.requestSizeByBucket = collections.defaultdict(lambda:[0,] * 24)
        self.responseSizeByBucket = collections.defaultdict(lambda:[0,] * 24)
        self.averageResponseCountByMethod = collections.defaultdict(lambda: [0, 0])

        self.requestTimeByBucket = collections.defaultdict(lambda:[0,] * 24)
        
        self.requestURI = collections.defaultdict(int)

        self.userWeights = collections.defaultdict(int)
        self.userCounts = collections.defaultdict(int)
        self.userResponseTimes = collections.defaultdict(float)

        self.currentLine = None
        self.linesRead = 0

    def analyzeLogFile(self, logFilePath):
        
        fpath = os.path.expanduser(logFilePath)
        if fpath.endswith(".gz"):
            f = GzipFile(fpath)
        else:
            f = open(fpath)
            
        lastHourFromStart = -1
        startHourFromStart = divmod(self.startHour - (self.utcStartHour + self.localtimeOffset), 24)[1]
        endHourFromStart = divmod(self.endHour - (self.utcStartHour + self.localtimeOffset), 24)[1]
        try:
            ctr = 0
            for line in f:
                
                ctr += 1
                if ctr <= self.linesRead:
                    continue
                self.linesRead += 1
                if line.startswith("Log"):
                    continue
        
                self.parseLine(line)
        
                # Do hour ranges
                logHour = int(self.currentLine.logTime[0:2])
                if self.autoUTC:
                    self.utcStartHour = logHour
                    self.localtimeOffset = -8 if logHour == 6 else -7
                    startHourFromStart = divmod(self.startHour - (self.utcStartHour + self.localtimeOffset), 24)[1]
                    endHourFromStart = divmod(self.endHour - (self.utcStartHour + self.localtimeOffset), 24)[1]
                    self.autoUTC = False
                hourFromStart = divmod(logHour - self.utcStartHour, 24)[1]
#                if hourFromStart > 1:
#                    break
                if hourFromStart > lastHourFromStart:
                    print logHour
                    lastHourFromStart = hourFromStart
                if hourFromStart < startHourFromStart:
                    continue
                elif hourFromStart > endHourFromStart:
                    continue
                
                if not self.startLog:
                    self.startLog = self.currentLine.logDateTime
                self.endLog = self.currentLine.logDateTime 
        
                # Filter on user id
                if self.filterByUser and self.currentLine.userid != self.filterByUser:
                    continue
                
                # Filter on client
                adjustedClient = self.getClientAdjustedName()
                if self.filterByClient and adjustedClient.find(self.filterByClient) == -1:
                    continue

                # Get some useful values
                is503 = self.currentLine.method == "???"
                isOK = self.currentLine.status / 100 == 2
                adjustedMethod = self.getAdjustedMethodName()
    
                instance = self.currentLine.extended.get("i", "")
                responseTime = float(self.currentLine.extended.get("t", 0.0))
                queueDepth = int(self.currentLine.extended.get("or", 0))
                contentLength = int(self.currentLine.extended.get("cl", -1))
                rcount = int(self.currentLine.extended.get("responses", -1))
                if rcount == -1:
                    rcount = int(self.currentLine.extended.get("rcount", -1))
    
                # Main summary
                self.hourlyTotals[hourFromStart][0] += 1
                self.hourlyTotals[hourFromStart][1] += 1 if is503 else 0
                self.hourlyTotals[hourFromStart][2] += queueDepth
                self.hourlyTotals[hourFromStart][3][instance] = max(self.hourlyTotals[hourFromStart][3][instance], queueDepth)
                self.hourlyTotals[hourFromStart][4] += responseTime
                
                # Client analysis
                if not is503:
                    self.clientTotals[" TOTAL"][0] += 1
                    self.clientTotals[" TOTAL"][1].add(self.currentLine.userid)
                    self.clientTotals[adjustedClient][0] += 1
                    self.clientTotals[adjustedClient][1].add(self.currentLine.userid)
                    
                    self.clientByMethodCount[" TOTAL"][" TOTAL"] += 1
                    self.clientByMethodCount[" TOTAL"][adjustedMethod] += 1
                    self.clientByMethodCount[adjustedClient][" TOTAL"] += 1
                    self.clientByMethodCount[adjustedClient][adjustedMethod] += 1
                    
                    self.clientByMethodTotalTime[" TOTAL"][" TOTAL"] += responseTime
                    self.clientByMethodTotalTime[" TOTAL"][adjustedMethod] += responseTime
                    self.clientByMethodTotalTime[adjustedClient][" TOTAL"] += responseTime
                    self.clientByMethodTotalTime[adjustedClient][adjustedMethod] += responseTime
                    
                    self.statusByMethodCount[" TOTAL"][" TOTAL"] += 1
                    self.statusByMethodCount[" TOTAL"][adjustedMethod] += 1
                    self.statusByMethodCount["2xx" if isOK else "%d" % (self.currentLine.status,)][" TOTAL"] += 1
                    self.statusByMethodCount["2xx" if isOK else "%d" % (self.currentLine.status,)][adjustedMethod] += 1
        
                # Method counts, timing and status
                self.hourlyByMethodCount[" TOTAL"][hourFromStart] += 1
                self.hourlyByMethodCount[adjustedMethod][hourFromStart] += 1
                if isOK:
                    self.hourlyByOKMethodCount[" TOTAL"][hourFromStart] += 1
                    self.hourlyByOKMethodCount[adjustedMethod][hourFromStart] += 1
                
                self.hourlyByMethodTime[" TOTAL"][hourFromStart] += responseTime
                self.hourlyByMethodTime[adjustedMethod][hourFromStart] += responseTime
        
                self.hourlyByStatus[" TOTAL"][hourFromStart] += 1
                self.hourlyByStatus[self.currentLine.status][hourFromStart] += 1
    
                # Cache analysis
                if adjustedMethod == "PROPFIND Calendar" and self.currentLine.status == 207:
                    responses = int(self.currentLine.extended.get("responses", 0))
                    self.hourlyPropfindByResponseCount[" TOTAL"][hourFromStart] += 1
                    self.hourlyPropfindByResponseCount[self.getCountBucket(responses, responseCountBuckets)][hourFromStart] += 1
    
                # Scheduling analysis
                if adjustedMethod == "POST Freebusy" and "freebusy" in self.currentLine.extended:
                    recipients = int(self.currentLine.extended["freebusy"])
                    self.hourlyByRecipientCount["Freebusy One Offs" if recipients == 1 else "Freebusy Average"][hourFromStart][0] += 1
                    self.hourlyByRecipientCount["Freebusy One Offs" if recipients == 1 else "Freebusy Average"][hourFromStart][1] += recipients
                    self.hourlyByRecipientCount["Freebusy Max."][hourFromStart][0] = max(self.hourlyByRecipientCount["Freebusy Max."][hourFromStart][0], recipients)
                elif adjustedMethod == "POST iTIP Organizer":
                    for key, value in self.currentLine.extended.iteritems():
                        if key.startswith("itip."):
                            recipients = int(value)
                            self.hourlyByRecipientCount["iTIP Average"][hourFromStart][0] += 1
                            self.hourlyByRecipientCount["iTIP Average"][hourFromStart][1] += recipients
                            self.hourlyByRecipientCount["iTIP Max."][hourFromStart][0] = max(self.hourlyByRecipientCount["iTIP Max."][hourFromStart][0], recipients)
    
                # Queue depth analysis
                self.responseTimeVsQueueDepth[queueDepth][0] += 1
                self.responseTimeVsQueueDepth[queueDepth][1] += responseTime
    
                # Instance counts
                self.instanceCount[instance] += 1

                # Request/response size analysis
                if contentLength != -1:
                    self.requestSizeByBucket[" TOTAL"][hourFromStart] += 1
                    self.requestSizeByBucket[self.getCountBucket(contentLength, requestSizeBuckets)][hourFromStart] += 1
                if adjustedMethod != "GET Dropbox":
                    self.responseSizeByBucket[" TOTAL"][hourFromStart] += 1
                    self.responseSizeByBucket[self.getCountBucket(self.currentLine.bytes, responseSizeBuckets)][hourFromStart] += 1
                    
                if rcount != -1:
                    self.averageResponseCountByMethod[" TOTAL"][0] += rcount
                    self.averageResponseCountByMethod[" TOTAL"][1] += 1
                    self.averageResponseCountByMethod[adjustedMethod][0] += rcount
                    self.averageResponseCountByMethod[adjustedMethod][1] += 1

                # Request time analysis
                self.requestTimeByBucket[" TOTAL"][hourFromStart] += 1
                self.requestTimeByBucket[self.getCountBucket(responseTime, requestTimeBuckets)][hourFromStart] += 1

                # Request URI analysis
                self.requestURI[self.currentLine.uri] += 1

                self.userAnalysis(adjustedMethod)

        except Exception:
            print line
            raise
    
        # Average various items
        self.averagedHourlyByMethodTime.clear()
        for method, hours in self.hourlyByMethodTime.iteritems():
            counts = self.hourlyByMethodCount[method]
            for hour in xrange(24):
                if counts[hour]:
                    newValue = hours[hour] / counts[hour]
                else:
                    newValue = hours[hour]
                self.averagedHourlyByMethodTime[method][hour] = newValue
        
        self.averagedResponseTimeVsQueueDepth.clear()
        for k, v in self.responseTimeVsQueueDepth.iteritems():
            self.averagedResponseTimeVsQueueDepth[k] = (v[0], v[1] / v[0], )
    
        self.averagedHourlyByRecipientCount.clear()
        for method, value in self.hourlyByRecipientCount.iteritems():
            for hour in xrange(24):
                if method in ("Freebusy Average", "iTIP Average",):
                    newValue = ((1.0 * value[hour][1]) / value[hour][0]) if value[hour][0] != 0 else 0
                else:
                    newValue = value[hour][0]
                self.averagedHourlyByRecipientCount[method][hour] = newValue
        
        averaged = collections.defaultdict(int)
        for key, value in self.averageResponseCountByMethod.iteritems():
            averaged[key] = (value[0] / value[1]) if value[1] else 0
        self.averageResponseCountByMethod = averaged

        for client, data in self.clientByMethodTotalTime.iteritems():
            for method, totaltime in data.iteritems():
                count = self.clientByMethodCount[client][method]
                self.clientByMethodAveragedTime[client][method] = totaltime/count if count else 0

#regex1 = re.compile("""[^ ]+ - (?P<userid>"[^"]+"|[^ ]+) \[[^:]+:(?P<logTime>[^ ]+) [^"]+"(?P<method>\?\?\?|[^ ]+)( (?P<uri>[^ ]+) HTTP/..."|") (?P<status>[^ ]+) (?P<bytes>[^ ]+) "(?P<referrer>[^"]+)" "(?P<client>[^"]+)" (?P<rest>.*)""")
#regex2 = re.compile("""\[(?P<time>[^ ]+) ms\] \[(?P<port>[^ ]+) (?P<qdepth>[^ ]+)\]""")
#
#def parseLineR(line):
#    
#    m = regex1.match(line)
#    
#    userid = m.group("userid")
#    logTime = m.group("logTime")
#    method = m.group("method")
#    if method == "???":
#        uri = ""
#    else:
#        uri = m.group("uri")
#    status = int(m.group("status"))
#    bytes = int(m.group("bytes"))
#    referrer = m.group("referrer")
#    client = m.group("client")
#    rest = m.group("rest")
#    
#    if rest[0] == '[':
#        m = regex2.match(rest)
#        
#        extended = {}
#        extended["t"] = float(m.group("time"))
#        extended["i"] = int(m.group("port"))
#        extended["or"] = int(m.group("qdepth"))
#    else:
#        items = rest.split()
#        extended = dict([item.split('=') for item in items])
#
#    return userid, logTime, method, uri, status, bytes, referrer, client, extended
    
    def parseLine(self, line):
    
        startPos = line.find("- ")
        endPos = line.find(" [")
        userid = line[startPos+2:endPos]
        
        startPos = endPos + 1
        logDateTime = line[startPos + 1:startPos + 21]
        logTime = line[startPos + 13:startPos + 21]
    
        startPos = line.find(']', startPos + 21) + 3
        endPos = line.find(' ', startPos)
        if line[startPos] == '?':
            method = "???"
            uri = ""
            startPos += 5
        else:
            method = line[startPos:endPos]
            
            startPos = endPos + 1
            endPos = line.find(" HTTP/", startPos)
            uri = line[startPos:endPos]
            startPos = endPos + 11
    
        status = int(line[startPos:startPos+3])
        
        startPos += 4
        endPos = line.find(' ', startPos)
        bytes = int(line[startPos:endPos])
        
        startPos = endPos + 2
        endPos = line.find('"', startPos)
        # Handle "attacks" where double-quotes may appear in the string
        if line[endPos + 1] != ' ':
            endPos = line.find('"', endPos+1)
        referrer = line[startPos:endPos]
        
        startPos = endPos + 3
        endPos = line.find('"', startPos)
        client = line[startPos:endPos]
        
        startPos = endPos + 2
        if line[startPos] == '[':
            extended = {}
    
            startPos += 1
            endPos = line.find(' ', startPos)
            extended["t"] = line[startPos:endPos]
            
            startPos = endPos + 6
            endPos = line.find(' ', startPos)
            extended["i"] = line[startPos:endPos]
            
            startPos = endPos + 1
            endPos = line.find(']', startPos)
            extended["or"] = line[startPos:endPos]
        else:
            items = line[startPos:].split()
            extended = dict([item.split('=') for item in items])
    
        self.currentLine = CalendarServerLogAnalyzer.LogLine(userid, logDateTime, logTime, method, uri, status, bytes, referrer, client, extended)
    
    def getClientAdjustedName(self):
    
        index = self.currentLine.client.find("iCal/")
        if index != -1:
            name = self.currentLine.client[index:self.currentLine.client.find(' ', index)]
            if name.startswith("iCal/3"):
                return "iCal/3"
            elif name == "iCal/4.0":
                return "iCal/4.0"
            elif name == "iCal/4.0.1":
                return "iCal/4.0.1"
            elif name == "iCal/4.0.2":
                return "iCal/4.0.2"
            elif name.startswith("iCal/4"):
                return "iCal/4.?"
            elif name.startswith("iCal/5"):
                return "iCal/5.?"
            else:
                return "iCal/???"
        
        index = self.currentLine.client.find("iPhone/")
        if index != -1:
            name = self.currentLine.client[index:self.currentLine.client.find(' ', index)]
            if name.startswith("iPhone/3.0"):
                return "iPhone/3.0"
            elif name.startswith("iPhone/3.1"):
                return "iPhone/3.1"
            elif name.startswith("iPhone/3.2"):
                return "iPhone/3.2"
            elif name.startswith("iPhone/3"):
                return "iPhone/3.?"
            elif name.startswith("iPhone/4"):
                return "iPhone/4.?"
            else:
                return "iPhone/???"
        
        index = self.currentLine.client.find("calendarclient")
        if index != -1:
            code = self.currentLine.client[14]
            if code == 'A':
                return "iCal/3 sim"
            elif code == 'B':
                return "iPhone/3 sim"
            else:
                return "Simulator"
    
        return "Other"
    
    def getAdjustedMethodName(self):

        if self.currentLine.method == "PROPFIND":
            
            uribits = self.currentLine.uri.split('/')[1:]
            cached = "cached" in self.currentLine.extended

            if uribits[0] == "calendars":
                
                if len(uribits) > 3:
                    if uribits[3] == "":
                        return "PROPFIND%s Calendar Home" % ("-cached" if cached else "")
                    elif uribits[3] == "inbox":
                        return "PROPFIND Inbox"
                    elif uribits[3] == "dropbox":
                        pass
                    else:
                        return "PROPFIND Calendar"
    
            elif uribits[0] == "principals":
                return "PROPFIND%s Principal" % ("-cached" if cached else "")
        
        elif self.currentLine.method.startswith("REPORT"):
            
            if "(" in self.currentLine.method:
                return "REPORT %s" % self.currentLine.method.split(":")[1][:-1]
        
        elif self.currentLine.method == "PROPPATCH":
            
            uribits = self.currentLine.uri.split('/')[1:]
            
            if uribits[0] == "calendars":
                return "PROPPATCH Calendar"
            
        elif self.currentLine.method == "POST":
            
            if "freebusy" in self.currentLine.extended:
                return "POST Freebusy"
            else:
                for key in ("itip.publish", "itip.request", "itip.cancel", "itip.add", "itip.decline-counter",):
                    if key in self.currentLine.extended:
                        return "POST iTIP Organizer"
                else:
                    return "POST iTIP Attendee"
            
        elif self.currentLine.method == "PUT":
            
            uribits = self.currentLine.uri.split('/')[1:]
            
            if len(uribits) > 3 and uribits[3] == "dropbox":
                return "PUT Dropbox"
            
        elif self.currentLine.method == "GET":
            
            uribits = self.currentLine.uri.split('/')[1:]
            
            if len(uribits) > 3 and uribits[3] == "dropbox":
                return "GET Dropbox"
            
        return self.currentLine.method
    
    def getCountBucket(self, count, buckets):
        
        for limit, key in buckets:
            if limit is None:
                break
            if count <= limit:
                return key
        return key

    # Determine method weight 1 - 10
    weighting = {
        "ACL": lambda x: 3,
        "DELETE" : lambda x: 5,
        "GET" : lambda x:  3 * (1 + x.bytes / (1024 * 1024)),
        "GET Dropbox" : lambda x: 3 * (1 + x.bytes / (1024 * 1024)),
        "HEAD" : lambda x: 1,
        "MKCALENDAR" : lambda x: 2,
        "MKCOL" : lambda x: 2,
        "MOVE" : lambda x: 3,
        "OPTIONS" : lambda x: 1,
        "POST Freebusy" : lambda x: 5 * int(x.extended.get("recipients", 1)),
        "POST iTIP Organizer" : lambda x: 5 * int(x.extended.get("recipients", 1)),
        "POST iTIP Attendee" : lambda x: 5 * int(x.extended.get("recipients", 1)),
        "PROPFIND" : lambda x: 3 * int(x.extended.get("responses", 1)),
        "PROPFIND Calendar" : lambda x: 5 * (int(math.log10(float(x.extended.get("responses", 1)))) + 1),
        "PROPFIND Calendar Home" : lambda x: 5 * (int(math.log10(float(x.extended.get("responses", 1)))) + 1),
        "PROPFIND Inbox" : lambda x: 5 * (int(math.log10(float(x.extended.get("responses", 1)))) + 1),
        "PROPFIND Principal" : lambda x: 5 * (int(math.log10(float(x.extended.get("responses", 1)))) + 1),
        "PROPFIND-cached Calendar Home" : lambda x: 2,
        "PROPFIND-cached Principal" : lambda x: 2,
        "PROPPATCH" : lambda x: 4,
        "PROPPATCH Calendar" : lambda x:8,
        "PUT" : lambda x: 8,
        "PUT Dropbox" : lambda x: 10,
        "REPORT" : lambda x: 5,
        "REPORT calendar-multiget" : lambda x: 5 * int(x.extended.get("rcount", 1)),
        "REPORT calendar-query" : lambda x: 4 * int(x.extended.get("responses", 1)),
        "REPORT expand-property" : lambda x: 5,
        "REPORT principal-match" : lambda x: 5,
    }

    def userAnalysis(self, adjustedMethod):
        
        if self.currentLine.userid == "-":
            return
        try:
            self.userWeights[self.currentLine.userid] += self.weighting[adjustedMethod](self.currentLine)
        except KeyError:
            self.userWeights[self.currentLine.userid] += 5
        
        
        responseTime = float(self.currentLine.extended.get("t", 0.0))
        self.userCounts["%s:%s" % (self.currentLine.userid, self.getClientAdjustedName(),)] += 1
        self.userResponseTimes["%s:%s" % (self.currentLine.userid, self.getClientAdjustedName(),)] += responseTime
        
    def printAll(self, doTabs):

        self.printInfo(doTabs)
        
        print "Load Analysis"
        self.printHourlyTotals(doTabs)
        
        print "Client Analysis"
        self.printClientTotals(doTabs)
        
        print "Protocol Analysis Count"
        self.printHourlyByXXXDetails(self.hourlyByMethodCount, doTabs)
        
        print "Protocol Analysis Average Response Time (ms)"
        self.printHourlyByXXXDetails(self.averagedHourlyByMethodTime, doTabs)
        
        print "Status Code Analysis"
        self.printHourlyByXXXDetails(self.hourlyByStatus, doTabs)
        
        print "Protocol Analysis by Status"
        self.printXXXMethodDetails(self.statusByMethodCount, doTabs, False)
        
        print "Cache Analysis"
        self.printHourlyCacheDetails(doTabs)
        
        if len(self.hourlyPropfindByResponseCount):
            print "PROPFIND Calendar response count distribution"
            self.printHourlyByXXXDetails(self.hourlyPropfindByResponseCount, doTabs)
        
        if len(self.averagedHourlyByRecipientCount):
            print "Average Recipient Counts"
            self.printHourlyByXXXDetails(self.averagedHourlyByRecipientCount, doTabs, showTotals=False)
            
        print "Queue Depth vs Response Time"
        self.printQueueDepthResponseTime(doTabs)
        
        print "Instance Count Distribution"
        self.printInstanceCount(doTabs)

        print "Protocol Analysis by Client"
        self.printXXXMethodDetails(self.clientByMethodCount, doTabs)
        
        if len(self.requestSizeByBucket):
            print "Request size distribution"
            self.printHourlyByXXXDetails(self.requestSizeByBucket, doTabs)
        
        if len(self.responseSizeByBucket):
            print "Response size distribution (excluding GET Dropbox)"
            self.printHourlyByXXXDetails(self.responseSizeByBucket, doTabs)
        
        if len(self.averageResponseCountByMethod):
            print "Average response count by method"
            self.printResponseCounts(doTabs)
        
        if len(self.requestTimeByBucket):
            print "Response time distribution"
            self.printHourlyByXXXDetails(self.requestTimeByBucket, doTabs)
        
        print "URI Counts"
        self.printURICounts(doTabs)

        #print "User Weights (top 100)"
        #self.printUserWeights(doTabs)

        #print "User Response times"
        #self.printUserResponseTimes(doTabs)

    def printInfo(self, doTabs):
        
        table = tables.Table()
        table.addRow(("Run on:", self.startTime.isoformat(' '),))
        table.addRow(("Host:", self.host,))
        table.addRow(("First Log Entry:", self.startLog,))
        table.addRow(("Last Log Entry:", self.endLog,))
        if self.filterByUser:
            table.addRow(("Filtered to user:", self.filterByUser,))
        if self.filterByClient:
            table.addRow(("Filtered to client:", self.filterByClient,))
        table.addRow(("Lines Analyzed:", self.linesRead,))
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printHourlyTotals(self, doTabs):
        
        table = tables.Table()
        table.addHeader(("Hour UTC (PDT)", "Total",    "503 Count", "Av. Queue", "Max. Queue", "Av. Response",))
        table.addHeader(("",               "Requests", "",          "Depth",     "Depth (# queues)",      "Time(ms)",))
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY), 
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%d (%.1f%%)", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%d (%2d)", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%.1f", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        totalRequests = 0
        total503 = 0
        totalDepth = 0
        totalMaxDepth = 0
        totalTime = 0.0
        for ctr in xrange(24):
            hour = divmod(ctr + self.utcStartHour, 24)[1]
            value = self.hourlyTotals[ctr]
            countRequests, count503, countDepth, maxDepth, countTime = value
            maxDepthAll = max(maxDepth.values()) if maxDepth.values() else 0
            maxDepthCount = list(maxDepth.values()).count(maxDepthAll)
            table.addRow((
                "%02d (%02d)" % (hour, divmod(hour + self.localtimeOffset, 24)[1]),
                countRequests,
                (count503, safePercent(count503, countRequests, 100.0),),
                safePercent(countDepth, countRequests, 1),
                (maxDepthAll, maxDepthCount,),
                safePercent(countTime, countRequests, 1.0),
            ))
            totalRequests += countRequests
            total503 += count503
            totalDepth += countDepth
            totalMaxDepth = max(totalMaxDepth, maxDepthAll)
            totalTime += countTime
    
        table.addFooter((
            "Total:",
            totalRequests,
            (total503, safePercent(total503, totalRequests, 100.0)),
            safePercent(totalDepth, totalRequests, 1),
            totalMaxDepth,
            safePercent(totalTime, totalRequests, 1.0),
        ), columnFormats=(
            tables.Table.ColumnFormat("%s"), 
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%d (%.1f%%)", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%d     ", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%.1f", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printClientTotals(self, doTabs):
        
        table = tables.Table()
    
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.LEFT_JUSTIFY), 
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        table.addHeader(("Client", "Total", "Unique", "Total/Unique"))
        for title, clientData in sorted(self.clientTotals.iteritems(), key=lambda x:x[0].lower()):
            if title == " TOTAL":
                continue
            table.addRow((
                title,
                "%d (%2d%%)" % (clientData[0], safePercent(clientData[0], self.clientTotals[" TOTAL"][0]),),
                "%d (%2d%%)" % (len(clientData[1]), safePercent(len(clientData[1]), len(self.clientTotals[" TOTAL"][1])),),
                "%d" % (safePercent(clientData[0], len(clientData[1]), 1),),
            ))
    
        table.addFooter((
            "All",
            "%d      " % (self.clientTotals[" TOTAL"][0],),
            "%d      " % (len(self.clientTotals[" TOTAL"][1]),),
            "",
        ))
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printHourlyByXXXDetails(self, hourlyByXXX, doTabs, showTotals=True):
    
        totals = [0,] * len(hourlyByXXX)
        table = tables.Table()
    
        header = ["Hour UTC (PDT)",]
        header2 = ["",]
        use_header2 = False
        formats = [tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),]
        for k in sorted(hourlyByXXX.keys(), key=lambda x:str(x).lower()):
            if type(k) is str:
                if k[0] == ' ':
                    k = k[1:]
                header.append(k[:k.find(" ")] if " " in k else k)
                header2.append(k[k.find(" ")+1:] if " " in k else "")
                use_header2 = use_header2 or header2[-1] != ""
            else:
                header.append(k)
                header2.append("")
            formats.append(tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY))
        table.addHeader(header)
        if use_header2:
            table.addHeader(header2)
        table.setDefaultColumnFormats(formats)
    
        for ctr in xrange(24):
            hour = divmod(ctr + self.utcStartHour, 24)[1]
            row = ["-"] * (len(hourlyByXXX) + 1)
            row[0] = "%02d (%02d)" % (hour, divmod(hour + self.localtimeOffset, 24)[1])
            for colctr, items in enumerate(sorted(hourlyByXXX.items(), key=lambda x:str(x[0]).lower())):
                _ignore, value = items
                data = value[ctr]
                if " TOTAL" in hourlyByXXX:
                    total = hourlyByXXX[" TOTAL"][ctr]
                else:
                    total = None
                if type(data) is int:
                    if total is not None:
                        row[colctr + 1] = "%d (%2d%%)" % (data, safePercent(data, total),)
                    else:
                        row[colctr + 1] = "%d" % (data,)
                    totals[colctr] += data
                elif type(data) is float:
                    row[colctr + 1] = "%.1f" % (data,)
                    totals[colctr] += data
            table.addRow(row)
    
        if showTotals:
            row = ["-"] * (len(hourlyByXXX) + 1)
            row[0] = "Total:"
            for colctr, data in enumerate(totals):
                if type(data) is int:
                    row[colctr + 1] = "%d (%2d%%)" % (data, safePercent(data, totals[0]),)
                elif type(data) is float:
                    row[colctr + 1] = "%.1f" % (data,)
            table.addFooter(row)
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printHourlyCacheDetails(self, doTabs):
    
        totals = [0,] * 5
        table = tables.Table()
    
        header1 = ["Hour UTC (PDT)", "PROPFIND Calendar Home", "", "PROPFIND Principals", ""]
        header2 = ["",               "Uncached",     "Cached",     "Uncached",  "Cached"]
        formats = [
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ]
    
        table.addHeader(header1, columnFormats = [
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY, span=2),
            None,
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY, span=2),
            None,
        ])
    
        table.addHeaderDivider(skipColumns=(0,))
        table.addHeader(header2, columnFormats = [
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
        ])
        table.setDefaultColumnFormats(formats)
    
        for ctr in xrange(24):
            hour = divmod(ctr + self.utcStartHour, 24)[1]
            row = []
            row.append("%02d (%02d)" % (hour, divmod(hour + self.localtimeOffset, 24)[1]))
    
            homeUncached = self.hourlyByOKMethodCount["PROPFIND Calendar Home"][ctr]
            homeCached = self.hourlyByOKMethodCount["PROPFIND-cached Calendar Home"][ctr]
            homeTotal = homeUncached + homeCached
            
            principalUncached = self.hourlyByOKMethodCount["PROPFIND Principal"][ctr]
            principalCached = self.hourlyByOKMethodCount["PROPFIND-cached Principal"][ctr]
            principalTotal = principalUncached + principalCached
            
            
            row.append("%d (%2d%%)" % (homeUncached, safePercent(homeUncached, homeTotal),))
            row.append("%d (%2d%%)" % (homeCached, safePercent(homeCached, homeTotal),))
    
            row.append("%d (%2d%%)" % (principalUncached, safePercent(principalUncached, principalTotal),))
            row.append("%d (%2d%%)" % (principalCached, safePercent(principalCached, principalTotal),))
    
            totals[1] += homeUncached
            totals[2] += homeCached
            totals[3] += principalUncached
            totals[4] += principalCached
    
            table.addRow(row)
    
        row = []
        row.append("Total:")
        row.append("%d (%2d%%)" % (totals[1], safePercent(totals[1], totals[1] + totals[2]),))
        row.append("%d (%2d%%)" % (totals[2], safePercent(totals[2], totals[1] + totals[2]),))
        row.append("%d (%2d%%)" % (totals[3], safePercent(totals[3], totals[3] + totals[4]),))
        row.append("%d (%2d%%)" % (totals[4], safePercent(totals[4], totals[3] + totals[4]),))
        table.addFooter(row)
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printQueueDepthResponseTime(self, doTabs):
        
        table = tables.Table()
    
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        table.addHeader(("Queue Depth", "Av. Response Time (ms)", "Number"))
        for k, v in sorted(self.averagedResponseTimeVsQueueDepth.iteritems(), key=lambda x:x[0]):
            table.addRow((
                "%d" % (k,),
                "%.1f" % (v[1],),
                "%d" % (v[0],),
            ))
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printXXXMethodDetails(self, data, doTabs, verticalTotals=True):
    
        table = tables.Table()
    
        header = ["Method",]
        formats = [tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.LEFT_JUSTIFY),]
        for k in sorted(data.keys(), key=lambda x:x.lower()):
            header.append(k)
            formats.append(tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY))
        table.addHeader(header)
        table.setDefaultColumnFormats(formats)
    
        # Get full set of methods
        methods = set()
        for v in data.itervalues():
            methods.update(v.keys())
            
        for method in sorted(methods):
            if method == " TOTAL":
                continue
            row = []
            row.append(method)
            for k,v in sorted(data.iteritems(), key=lambda x:x[0].lower()):
                total = v[" TOTAL"] if verticalTotals else data[" TOTAL"][method]
                row.append("%d (%2d%%)" % (v[method], safePercent(v[method], total),))
            table.addRow(row)
    
        row = []
        row.append("Total:")
        for k,v in sorted(data.iteritems(), key=lambda x:x[0].lower()):
            row.append("%d" % (v[" TOTAL"],))
        table.addFooter(row)
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""

    def printInstanceCount(self, doTabs):
    
        total = sum(self.instanceCount.values())
    
        table = tables.Table()
        table.addHeader(("Instance ID", "Count", "%% Total",))
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY), 
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%.1f%%%%", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        # Top 100 only
        def safeIntKey(v):
            try:
                return int(v[0])
            except ValueError:
                return -1

        for key, value in sorted(self.instanceCount.iteritems(), key=safeIntKey):
            table.addRow((
                key,
                value,
                safePercent(value, total, 1000.0),
            ))
   
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""

    def printURICounts(self, doTabs):
    
        total = sum(self.requestURI.values())
    
        table = tables.Table()
        table.addHeader(("Request URI", "Count", "%% Total",))
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.LEFT_JUSTIFY), 
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%.1f%%%%", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        # Top 100 only
        for key, value in sorted(self.requestURI.iteritems(), key=lambda x:x[1], reverse=True)[:100]:
            table.addRow((
                key,
                value,
                safePercent(value, total, 1000.0),
            ))
   
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""

    def printUserWeights(self, doTabs):
    
        total = sum(self.userWeights.values())
    
        table = tables.Table()
        table.addHeader(("User ID", "Weight", "%% Total",))
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY), 
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%.1f%%%%", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        # Top 100 only
        for key, value in sorted(self.userWeights.iteritems(), key=lambda x:x[1], reverse=True)[:100]:
            table.addRow((
                key,
                value,
                safePercent(value, total, 1000.0),
            ))
   
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""

    def printResponseCounts(self, doTabs):
    
        table = tables.Table()
        table.addHeader(("Method", "Av. Response Count",))
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.LEFT_JUSTIFY), 
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        for method, value in sorted(self.averageResponseCountByMethod.iteritems(), key=lambda x:x[0]):
            if method == " TOTAL":
                continue
            table.addRow((
                method,
                value,
            ))

        table.addFooter(("Total:", self.averageResponseCountByMethod[" TOTAL"],))
   
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""

    def printUserResponseTimes(self, doTabs):
    
        totalCount = 0
        averages = {}
        for user in self.userResponseTimes.keys():
            count = self.userCounts[user]
            total = self.userResponseTimes[user]
            averages[user] = total / count
            totalCount += total

        table = tables.Table()
        table.addHeader(("User ID/Client", "Av. Response (ms)", "%% Total",))
        table.setDefaultColumnFormats((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY), 
            tables.Table.ColumnFormat("%d", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%.1f%%%%", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))
    
        for key, value in sorted(averages.iteritems(), key=lambda x:x[1], reverse=True):
            table.addRow((
                key,
                value,
                safePercent(value, total, 1000.0),
            ))
   
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""

class TablePrinter(object):
    
    @classmethod
    def printDictDictTable(cls, data, doTabs):
        
        table = tables.Table()
    
        header = ["",]
        formats = [tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.LEFT_JUSTIFY),]
        for k in sorted(data.keys(), key=lambda x:x.lower()):
            header.append(k)
            formats.append(tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY))
        table.addHeader(header)
        table.setDefaultColumnFormats(formats)
    
        # Get full set of row names
        rowNames = set()
        for v in data.itervalues():
            rowNames.update(v.keys())
            
        for rowName in sorted(rowNames):
            if rowName == " TOTAL":
                continue
            row = []
            row.append(rowName)
            for k,v in sorted(data.iteritems(), key=lambda x:x[0].lower()):
                value = v[rowName]
                if type(value) is str:
                    row.append(value)
                else:
                    if type(value) is float:
                        format = "%.1f"
                    else:
                        format = "%d"
                    if " TOTAL" in v:
                        total = v[" TOTAL"]
                        row.append((format + " (%2d%%)") % (value, safePercent(value, total),))
                    else:
                        row.append(format % (value,))
            table.addRow(row)
    
        if " TOTAL" in rowNames:
            row = []
            row.append("Total:")
            for k,v in sorted(data.iteritems(), key=lambda x:x[0].lower()):
                value = v[" TOTAL"]
                if type(value) is str:
                    format = "%s"
                elif type(value) is float:
                    format = "%.1f"
                else:
                    format = "%d"
                row.append(format % (value,))
            table.addFooter(row)
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
                    
class Differ(TablePrinter):
    
    def __init__(self, analyzers):
        
        self.analyzers = analyzers
    
    def printAll(self, doTabs):
        
        self.printInfo(doTabs)

        print "Load Analysis Differences"
        self.printLoadAnalysisDetails(doTabs)

        print "Client Differences"
        self.printClientTotals(doTabs)

        print "Protocol Count Differences"
        self.printMethodCountDetails(doTabs)

        print "Average Response Time Differences"
        self.printMethodTimingDetails("clientByMethodAveragedTime", doTabs)

        print "Total Response Time Differences"
        self.printMethodTimingDetails("clientByMethodTotalTime", doTabs)
        
        print "Average Response Count Differences"
        self.printResponseCountDetails(doTabs)

    def printInfo(self, doTabs):
        
        table = tables.Table()
        table.addRow(("Run on:", self.analyzers[0].startTime.isoformat(' '),))
        table.addRow(("Host:", self.analyzers[0].host,))
        for ctr, analyzer in enumerate(self.analyzers):
            table.addRow(("Log Start #%d:" % (ctr+1,), analyzer.startLog,))
        if self.analyzers[0].filterByUser:
            table.addRow(("Filtered to user:", self.analyzers[0].filterByUser,))
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printLoadAnalysisDetails(self, doTabs):
        
        # First gather all the data
        byCategory = collections.defaultdict(lambda:collections.defaultdict(str))
        firstData = []
        lastData = []
        for ctr, analyzer in enumerate(self.analyzers):
            title = "#%d %s" % (ctr+1, analyzer.startLog[0:11],)

            totalRequests = 0
            total503 = 0
            totalDepth = 0
            totalTime = 0.0
            for ctr2 in xrange(24):
                value = analyzer.hourlyTotals[ctr2]
                countRequests, count503, countDepth, _ignore_maxDepth, countTime = value
                totalRequests += countRequests
                total503 += count503
                totalDepth += countDepth
                totalTime += countTime

            byCategory[title]["#1 Total Requests"] = "%d" % (totalRequests,)
            byCategory[title]["#2 503 Count"] = "%d (%2d%%)" % (total503, safePercent(total503, totalRequests),)
            byCategory[title]["#3 Av. Queue Depth"] = "%d" % (safePercent(totalDepth, totalRequests, 1),)
            byCategory[title]["#4 Av. Response Time (ms)"] = "%.1f" % (safePercent(totalTime, totalRequests, 1.0),)
            
            if ctr == 0:
                firstData = (totalRequests, total503, safePercent(totalDepth, totalRequests, 1.0), safePercent(totalTime, totalRequests, 1.0),)
            lastData = (totalRequests, total503, safePercent(totalDepth, totalRequests, 1.0), safePercent(totalTime, totalRequests, 1.0),)

        title = "Difference"
        byCategory[title]["#1 Total Requests"] = "%+d (%+.1f%%)" % (lastData[0] - firstData[0], safePercent(lastData[0] - firstData[0], firstData[0], 100.0),)
        byCategory[title]["#2 503 Count"] = "%+d (%+.1f%%)" % (lastData[1] - firstData[1], safePercent((1.0 * lastData[1]) / lastData[0] - (1.0 * firstData[1]) / firstData[0], (1.0 * firstData[1]) / firstData[0], 100.0),)
        byCategory[title]["#3 Av. Queue Depth"] = "%+d (%+.1f%%)" % (lastData[2] - firstData[2], safePercent(lastData[2] - firstData[2], firstData[2], 100.0),)
        byCategory[title]["#4 Av. Response Time (ms)"] = "%+.1f (%+.1f%%)" % (lastData[3] - firstData[3], safePercent(lastData[3] - firstData[3], firstData[3], 100.0),)

        self.printDictDictTable(byCategory, doTabs)

    def printClientTotals(self, doTabs):
        
        table = tables.Table()
    
        header1 = ["Client",]
        header2 = ["",]
        header1formats = [tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),]
        header2formats = [tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),]
        formats = [tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.LEFT_JUSTIFY),]
        for ctr, analyzer in enumerate(self.analyzers):
            title = "#%d %s" % (ctr+1, analyzer.startLog[0:11],)
            header1.extend((title, ""))
            header2.extend(("Total", "Unique",))
            header1formats.extend((
                tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY, span=2),
                None,
            ))
            header2formats.extend((
                tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
                tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            ))
            formats.extend((
                tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
                tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            ))

        header1.extend(("Difference", ""))
        header2.extend(("Total", "Unique",))
        header1formats.extend((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY, span=2),
            None,
        ))
        header2formats.extend((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.CENTER_JUSTIFY),
        ))
        formats.extend((
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
            tables.Table.ColumnFormat("%s", tables.Table.ColumnFormat.RIGHT_JUSTIFY),
        ))

        table.addHeader(header1, columnFormats=header1formats)
        table.addHeaderDivider(skipColumns=(0,))
        table.addHeader(header2, columnFormats=header2formats)
        table.setDefaultColumnFormats(formats)
    
        allClients = set()
        for analyzer in self.analyzers:
            allClients.update(analyzer.clientTotals.keys())

        for title in sorted(allClients, key=lambda x:x.lower()):
            if title == " TOTAL":
                continue
            row = [title,]
            for analyzer in self.analyzers:
                row.append("%d (%2d%%)" % (analyzer.clientTotals[title][0], safePercent(analyzer.clientTotals[title][0], analyzer.clientTotals[" TOTAL"][0]),))
                row.append("%d (%2d%%)" % (len(analyzer.clientTotals[title][1]), safePercent(len(analyzer.clientTotals[title][1]), len(analyzer.clientTotals[" TOTAL"][1])),))

            firstTotal = (self.analyzers[0].clientTotals[title][0], safePercent(self.analyzers[0].clientTotals[title][0], self.analyzers[0].clientTotals[" TOTAL"][0], 100.0),)
            firstUnique = (len(self.analyzers[0].clientTotals[title][1]), safePercent(len(self.analyzers[0].clientTotals[title][1]), len(self.analyzers[0].clientTotals[" TOTAL"][1]), 100.0,))
            lastTotal = (self.analyzers[-1].clientTotals[title][0], safePercent(self.analyzers[-1].clientTotals[title][0], self.analyzers[-1].clientTotals[" TOTAL"][0], 100.0),)
            lastUnique = (len(self.analyzers[-1].clientTotals[title][1]), safePercent(len(self.analyzers[-1].clientTotals[title][1]), len(self.analyzers[-1].clientTotals[" TOTAL"][1]), 100.0,))
            row.append("%+d (%+.1f%%)" % (lastTotal[0] - firstTotal[0], lastTotal[1] - firstTotal[1]))
            row.append("%+d (%+.1f%%)" % (lastUnique[0] - firstUnique[0], lastUnique[1] - firstUnique[1]))

            table.addRow(row)
    
        footer = ["All",]
        for analyzer in self.analyzers:
            footer.append("%d      " % (analyzer.clientTotals[" TOTAL"][0],))
            footer.append("%d      " % (len(analyzer.clientTotals[" TOTAL"][1]),))
        firstTotal = self.analyzers[0].clientTotals[" TOTAL"][0]
        firstUnique = len(self.analyzers[0].clientTotals[" TOTAL"][1])
        lastTotal = self.analyzers[-1].clientTotals[" TOTAL"][0]
        lastUnique = len(self.analyzers[-1].clientTotals[" TOTAL"][1])
        footer.append("%+d (%+.1f%%)" % (lastTotal - firstTotal, safePercent(lastTotal - firstTotal, firstTotal, 100.0),))
        footer.append("%+d (%+.1f%%)" % (lastUnique - firstUnique, safePercent(lastUnique - firstUnique, firstUnique, 100.0),))
        table.addFooter(footer)
    
        table.printTabDelimitedData() if doTabs else table.printTable()
        print ""
    
    def printMethodCountDetails(self, doTabs):
        
        # First gather all the data
        allMethods = set()
        byMethod = collections.defaultdict(lambda:collections.defaultdict(int))
        for ctr, analyzer in enumerate(self.analyzers):
            title = "#%d %s" % (ctr+1, analyzer.startLog[0:11],)
            for method, value in analyzer.clientByMethodCount[" TOTAL"].iteritems():
                byMethod[title][method] = value
                allMethods.add(method)

        title = "Difference"
        for method in allMethods:
            firstTotal = self.analyzers[0].clientByMethodCount[" TOTAL"].get(" TOTAL", 0)
            lastTotal = self.analyzers[-1].clientByMethodCount[" TOTAL"].get(" TOTAL", 0)
            if method == " TOTAL":
                byMethod[title][method] = "%+d (%+.1f%%)" % (lastTotal - firstTotal, safePercent(lastTotal - firstTotal, firstTotal, 100.0),)
            else:
                firstValue = self.analyzers[0].clientByMethodCount[" TOTAL"].get(method, 0)
                firstPercent = ((100.0 * firstValue) / firstTotal) if firstTotal != 0 else 0.0
                lastValue = self.analyzers[-1].clientByMethodCount[" TOTAL"].get(method, 0)
                lastPercent = ((100.0 * lastValue) / lastTotal) if lastTotal != 0 else 0.0
                byMethod[title][method] = "%+d (%+.1f%%)" % (lastValue - firstValue, lastPercent - firstPercent,)

        self.printDictDictTable(byMethod, doTabs)

    def printMethodTimingDetails(self, timingType, doTabs):
        
        # First gather all the data
        allMethods = set()
        byMethod = collections.defaultdict(lambda:collections.defaultdict(int))
        for ctr, analyzer in enumerate(self.analyzers):
            title = "#%d %s" % (ctr+1, analyzer.startLog[0:11],)
            for method, value in getattr(analyzer, timingType)[" TOTAL"].iteritems():
                byMethod[title][method] = value
                allMethods.add(method)

        title1 = "1. Difference"
        title2 = "2. % Difference"
        title3 = "3. % Total Diff."
        for method in allMethods:
            firstTotal = getattr(self.analyzers[0], timingType)[" TOTAL"].get(" TOTAL", 0)
            lastTotal = getattr(self.analyzers[-1], timingType)[" TOTAL"].get(" TOTAL", 0)
            if method == " TOTAL":
                byMethod[title1][method] = "%+.1f" % (lastTotal - firstTotal,)
                byMethod[title2][method] = "%+.1f%%" % (safePercent(lastTotal - firstTotal, firstTotal, 100.0),)
                byMethod[title3][method] = ""
            else:
                firstValue = getattr(self.analyzers[0], timingType)[" TOTAL"].get(method, 0)
                lastValue = getattr(self.analyzers[-1], timingType)[" TOTAL"].get(method, 0)
                byMethod[title1][method] = "%+.1f" % (
                    lastValue - firstValue,
                )
                byMethod[title2][method] = "%+.1f%%" % (
                    safePercent(lastValue - firstValue, firstValue, 100.0),
                )
                byMethod[title3][method] = "%+.1f%%" % (
                    safePercent(lastValue - firstValue, lastTotal - firstTotal, 100.0),
                )

        self.printDictDictTable(byMethod, doTabs)

    def printResponseCountDetails(self, doTabs):
        
        # First gather all the data
        allMethods = set()
        byMethod = collections.defaultdict(lambda:collections.defaultdict(int))
        for ctr, analyzer in enumerate(self.analyzers):
            title = "#%d %s" % (ctr+1, analyzer.startLog[0:11],)
            for method, value in analyzer.averageResponseCountByMethod.iteritems():
                byMethod[title][method] = value
                allMethods.add(method)

        title = "Difference"
        for method in allMethods:
            firstTotal = self.analyzers[0].averageResponseCountByMethod.get(" TOTAL", 0)
            lastTotal = self.analyzers[-1].averageResponseCountByMethod.get(" TOTAL", 0)
            if method == " TOTAL":
                byMethod[title][method] = "%+d (%+.1f%%)" % (lastTotal - firstTotal, safePercent(lastTotal - firstTotal, firstTotal, 100.0),)
            else:
                firstValue = self.analyzers[0].averageResponseCountByMethod.get(method, 0)
                lastValue = self.analyzers[-1].averageResponseCountByMethod.get(method, 0)
                byMethod[title][method] = "%+d (%+.1f%%)" % (lastValue - firstValue, safePercent(lastValue - firstValue, firstValue, 100.0),)

        self.printDictDictTable(byMethod, doTabs)

def usage(error_msg=None):
    if error_msg:
        print error_msg

    print """Usage: protocolanalysis [options] [FILE]
Options:
    -h          Print this help and exit
    --hours     Range of hours to analyze
    --user      User to analyze
    --client    Client to analyze
    --tabs      Generate tab-delimited output rather than table
    --repeat    Parse the file and then allow more data to be parsed
    --diff      Compare two or more files

Arguments:
    FILE      File names for the access logs to analyze

Description:
    This utility will analyze the output of access logs and generate
    tabulated statistics. It can also display statistics about the
    differences between two logs.

"""

    if error_msg:
        raise ValueError(error_msg)
    else:
        sys.exit(0)

if __name__ == "__main__":

    try:
        diffMode = False
        doTabDelimited = False
        repeat = False
        startHour = UTC_START_HOUR
        endHour = UTC_START_HOUR + 23
        filterByUser = None
        filterByClient = None

        options, args = getopt.getopt(sys.argv[1:], "h", ["diff", "hours=", "repeat", "tabs", "user=", "client=", ])

        for option, value in options:
            if option == "-h":
                usage()
            elif option == "--diff":
                diffMode = True
            elif option == "--repeat":
                repeat = True
            elif option == "--tabs":
                doTabDelimited = True
            elif option == "--hours":
                splits = value.split(":")
                if len(splits) != 2:
                    usage("Wrong format for --hours: %s %s" % (value, splits))
                else:
                    startHour = int(splits[0])
                    endHour = int(splits[1])
            elif option == "--user":
                filterByUser = value
            elif option == "--client":
                filterByClient = value
            else:
                usage("Unrecognized option: %s" % (option,))

        if repeat and diffMode:
            usage("Cannot have --repeat and --diff together")

        # Process arguments
        if len(args) == 0:
            args = ("/var/log/caldavd/access.log",)
        if repeat and len(args) > 1:
            usage("Must have one argument with --repeat")

        pwd = os.getcwd()

        analyzers = []
        for arg in args:
            arg = os.path.expanduser(arg)
            if not arg.startswith("/"):
                arg = os.path.join(pwd, arg)
            if arg.endswith("/"):
                arg = arg[:-1]
            if not os.path.exists(arg):
                print "Path does not exist: '%s'. Ignoring." % (arg,)
                continue
           
            if diffMode or not analyzers:
                analyzers.append(CalendarServerLogAnalyzer(startHour, endHour, filterByUser, filterByClient))
            analyzers[-1].analyzeLogFile(arg)

        if diffMode and len(analyzers) > 1:
            Differ(analyzers).printAll(doTabDelimited)
        else:
            analyzers[-1].printAll(doTabDelimited)
            
            if repeat:
                while True:
                    again = raw_input("Repeat analysis [y/n]:")
                    if again.lower()[0] == "n":
                        break
                    print "\n\n\n"
                    analyzers[0].analyzeLogFile(arg)
                    analyzers[0].printAll(doTabDelimited)
                
    except Exception, e:
        sys.exit(str(e))
        print traceback.print_exc()
