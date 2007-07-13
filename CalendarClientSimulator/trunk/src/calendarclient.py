#!/usr/bin/env python

from xml.etree import ElementTree
from random import randint
import datetime
import uuid
import os
import plistlib
import httplib
import time

PROPFIND_ctag = """<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:" xmlns:A="http://calendarserver.org/ns/" xmlns:C="urn:ietf:params:xml:ns:caldav" xmlns:I="com.apple.ical:">
<D:prop>
<D:resourcetype/>
<D:displayname/>
<A:getctag/>
<I:calendarcolor/>
<C:calendar-description/>
<D:resourcetype/>
</D:prop>
</D:propfind>
"""

PROPFIND_etag = """<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:" xmlns:A="http://calendarserver.org/ns/">
<D:prop>
<D:resourcetype/>
<D:getetag/>
</D:prop>
</D:propfind>
"""

REPORT_multiget = """<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-multiget xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
<D:prop>
<D:getetag/>
<C:calendar-data/>
</D:prop>
%s
</C:calendar-multiget>
"""

CALDATA = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//example.com//Calendar Client//EN
VERSION:2.0
BEGIN:VTIMEZONE
LAST-MODIFIED:20040110T032845Z
TZID:US/Eastern
BEGIN:DAYLIGHT
DTSTART:20000404T020000
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=4
TZNAME:EDT
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20001026T020000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
TZNAME:EST
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTAMP:%(dtstamp)s
DTSTART;TZID=US/Eastern:%(dtstart)s
DURATION:PT1H
SUMMARY:%(summary)s
UID:%(uid)s
%(otherprops)sEND:VEVENT
END:VCALENDAR
"""

ATTENDEE_NEEDSACTION = "ATTENDEE;PARTSTAT=NEEDS-ACTION:%s"

FREEBUSYDATA = """BEGIN:VCALENDAR
CALSCALE:GREGORIAN
PRODID:-//example.com//Calendar Client//EN
VERSION:2.0
METHOD:REQUEST
BEGIN:VFREEBUSY
DTSTAMP:%(dtstamp)s
DTSTART:%(dtstart)s
DTEND:%(dtend)s
SUMMARY:%(summary)s
UID:%(uid)s
%(otherprops)sEND:VFREEBUSY
END:VCALENDAR
"""


def unq(s):
    if s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    else:
        return s

class CalendarClient(object):
    """
    CalendarClient client simulator.
    """
    
    calendarHomeURI = "/calendars/users/%s/"
    mainCalendarURI = "/calendars/users/%s/calendar/"
    outboxURI = "/calendars/users/%s/outbox/"
    inboxURI = "/calendars/users/%s/inbox/"
    
    sleep = 5

    def __init__(self):
        self.server = None
        self.user = None
        self.password = None
        self.interval = 15
        self.eventsperday = 10
        self.invitesperday = 5 * 24 *60
        self.cache = None
        self.clearcache = False
        self.verbose = False
        self.data = {
            "ctags": {
                "calendar":"",
                "inbox":""
            },
            "calendar_data": {
            "calendar":{},
            "inbox":{}
            }
        }

    def valid(self):
        if (self.server is None or
                self.user is None or
                self.password is None):
            return False
        
        self.home = self.calendarHomeURI % (self.user,)
        self.calendar = self.mainCalendarURI % (self.user,)
        self.inbox = self.inboxURI % (self.user,)
        self.outbox = self.outboxURI % (self.user,)
        
        if self.cache and not self.clearcache:
            self.readCache()

        return True

    def readCache(self):
        if self.cache and os.path.exists(self.cache):
            self.data = plistlib.readPlist(self.cache)
    
    def writeCache(self):
        if self.cache:
            plistlib.writePlist(self.data, self.cache)

    def log(self, text):
        if self.verbose:
            print text

    def simulate(self):
        
        self.log("Starting CalendarClient simulation for user %s" % (self.user,))
        start_poll = time.time() - self.interval - 1
        start_events = time.time()
        start_invites = time.time()
        event_interval = 24 * 60 * 60 / self.eventsperday
        invite_interval = 24 * 60 * 60 / self.invitesperday
        while(True):
            if time.time() >= start_poll + self.interval:
                start_poll = time.time()
                self.doPoll()
            if time.time() >= start_events + event_interval:
                start_events = time.time()
                self.doCreateEvent()
            if time.time() >= start_invites + invite_interval:
                start_invites = time.time()
                self.doInvite()
            time.sleep(CalendarClient.sleep)
    
    def doPoll(self):
        self.log("Polling for user %s" % (self.user,))
        status, headers, data = self.doRequest(self.home, "PROPFIND", {"Content-Type": "application/xml"}, PROPFIND_ctag)
        if status != 207:
            self.log("Polling failed with status: %d for user: %s" % (status, self.user,))
            return
            
        # Parse the XML to find changed ctags
        changed = []
        xml = ElementTree.XML(data)
        for response in xml.getiterator("{DAV:}response"):
            href = response.find("{DAV:}href")
            if href is None:
                raise ValueError("Invalid XML response: %s" % (data,))
            href = href.text
            calname = href.rstrip("/")
            calname = calname[calname.rfind("/") + 1:]
            ctag = [i for i in response.getiterator("{http://calendarserver.org/ns/}getctag")]
            if len(ctag) != 1:
                raise ValueError("Invalid XML response: %s" % (data,))
            ctag = ctag[0].text
            if ctag and calname in ["calendar", "inbox"]:
                if self.data["ctags"][calname] != ctag:
                    changed.append((href, calname, ctag))
                    self.log("Detected a change in calendar: %s for user: %s" % (href, self.user,))
                    
        # Now do Depth:1 poll of changed calendars
        did_change = False
        for href, calname, ctag in changed:
            if calname == "calendar":
                self.doFullCalendarPoll(href, calname)
                self.data["ctags"][calname] = ctag
                did_change = True
            elif calname == "inbox":
                self.doInboxPoll(href, calname)
                self.data["ctags"][calname] = ctag
                did_change = True

        if did_change:
            self.writeCache()
    
    def doFullCalendarPoll(self, uri, calname):
        self.log("Polling calendar: %s for user %s looking for new data" % (uri, self.user,))
        
        # Get href/etag map
        hrefs = self.doPropfindDepth1(uri)
        if hrefs is None:
            return
            
        # Find new ones
        server_hrefs = set(hrefs.keys())
        local_hrefs = set(self.data["calendar_data"][calname].keys())
        new_hrefs = server_hrefs.difference(local_hrefs)
        
        # Delete old ones
        delete_hrefs = local_hrefs.difference(server_hrefs)
        for href in delete_hrefs:
            del self.data["calendar_data"][calname][href]

        # Find changed ones
        changed = set()
        for href, (etag, data) in self.data["calendar_data"][calname].iteritems():
            if hrefs[href] != etag:
                changed.add(href)
                
        # Now do multiget of all new/changed items
        changed.update(new_hrefs)
        if changed:
            results = self.doMultiget(uri, calname, changed)
            
            # Cache the new etags - we don't care about the data
            for href, (etag, data) in results.iteritems():
                 self.data["calendar_data"][calname][href] = (etag, data)

    def doInboxPoll(self, uri, calname):

        # Get href/etag map
        hrefs = self.doPropfindDepth1(uri)
        if hrefs is None:
            return
        
        # Process each new item
        for href in hrefs:
            self.doProcessiTIP(href)

    def doProcessiTIP(self, href):
        
        # Get the data
        
        # Determine method
        pass
    
    def processiTIPRequest(self, href, data):
        
        # Generate accepted data and write to main calendar
        
        # Generate reply and POST
        pass

    def processiTIPReply(self, href, data):
        
        # Find matching UID on calendar
        
        # Update calendar data with attendee status
        pass

    def doCreateEvent(self, otherprops=""):
        self.log("Creating event for user %s" % (self.user,))
        
        nowutc = datetime.datetime.utcnow()
        now = datetime.datetime.now()
        
        dtstamp = "%d%02d%02dT%02d%02d%02dZ" % (
            nowutc.year,
            nowutc.month,
            nowutc.day,
            nowutc.hour,
            nowutc.minute,
            nowutc.second
        )
        dtstart = "%d%02d%02dT%02d%02d%02d" % (
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            0
        )
        summary = "Event at %s" % (now,)
        uid = uuid.uuid4()
        caldata = CALDATA % {
            "dtstamp":dtstamp,
            "dtstart":dtstart,
            "summary":summary,
            "uid":uid,
            "otherprops":otherprops
        }
        uri = "%s%s.ics" % (self.calendar, uid,)

        status, headers, data = self.doRequest(uri, "PUT", {"Content-Type": "text/calendar"}, caldata)
        if status != 201:
            self.log("Event creation failed with status: %d for user: %s" % (status, self.user,))
            return
        
        status, headers, data = self.doRequest(uri, "GET")
        if status != 200:
            self.log("Event creation (read) failed with status: %d for user: %s" % (status, self.user,))
            return
        for header, value in headers:
            if header == "etag":
                etag = unq(value)
                self.data["calendar_data"]["calendar"][uri] = (etag, data,)
                break
            
        return uri

    def doInvite(self):
        self.log("Sending invite from user %s" % (self.user,))
        
        # Generate data for this user
        organizer = "ORGANIZER:/principals/users/%s/" % (self.user,)
        attendee_me = "ATTENDEE;PARTSTAT=ACCEPTED:/principals/users/%s/" % (self.user,)
        originator = "/principals/users/%s/" % (self.user,)

        # Generate a set of users
        users = []
        for ctr in range(randint(1, 10)):
            user = "user%02d" % randint(1,99)
            while user == self.user:
                user = "user%02d" % randint(1,99)
            users.append("/principals/users/%s/" % (user,))
        
        # Create invite on main calendar
        attendees = "\n".join([ATTENDEE_NEEDSACTION % user for user in users]) + "\n"
        new_uri = self.doCreateEvent(organizer + "\n" + attendees)

        # Do free busy lookup
        nowutc = datetime.datetime.utcnow()
        now = datetime.datetime.now().replace(hour=0, minute=0, second=0)
        now = now + datetime.timedelta(hours=5)
        nextday = now + datetime.timedelta(days=1)
        
        dtstamp = "%d%02d%02dT%02d%02d%02dZ" % (
            nowutc.year,
            nowutc.month,
            nowutc.day,
            nowutc.hour,
            nowutc.minute,
            nowutc.second
        )
        dtstart = "%d%02d%02dT%02d%02d%02dZ" % (
            now.year,
            now.month,
            now.day,
            0,
            0,
            0
        )
        dtend = "%d%02d%02dT%02d%02d%02dZ" % (
            nextday.year,
            nextday.month,
            nextday.day,
            0,
            0,
            0
        )
        summary = "Free busy at %s" % (now,)
        uid = uuid.uuid4()
        fbdata = FREEBUSYDATA % {
            "dtstamp":dtstamp,
            "dtstart":dtstart,
            "dtend":dtend,
            "summary":summary,
            "uid":uid,
            "otherprops":organizer + "\n" + attendees + attendee_me + "\n"
        }
        self.doSchedule(self.outbox, fbdata, headers={"originator":originator, "recipient":", ".join((originator,) + tuple(users))})

        # TODO: Update invite (simulates a time shift after f-b lookup)
        
        # Do outbox POST
        _ignore_etag, caldata = self.data["calendar_data"]["calendar"][new_uri]
        caldata = caldata.replace("VERSION:", "METHOD:REQUEST\nVERSION:")
        self.doSchedule(self.outbox, caldata, headers={"originator":originator, "recipient":", ".join(users)})

    def doRequest(self, ruri, method='GET', headers={}, data=None):
        
        if self.server.startswith("https://"):
            conn = httplib.HTTPSConnection(self.server[8:])
        elif self.server.startswith("http://"):
            conn = httplib.HTTPConnection(self.server[7:])
        else:
            raise ValueError("Server address invalid: %s" (self.server,))
        
        basicauth = "%s:%s" % (self.user, self.password)
        basicauth = "Basic " + basicauth.encode("base64")
        headers["Authorization"] = basicauth

        if data:
            conn.request(method, ruri, data, headers=headers)
        else:
            conn.request(method, ruri, headers=headers)
        response = conn.getresponse()
        data = response.read()
        return response.status, response.getheaders(), data

    def doPropfindDepth1(self, uri):
        status, headers, data = self.doRequest(uri, "PROPFIND", {"Content-Type": "application/xml", "Depth": "1"}, PROPFIND_etag)
        if status != 207:
            self.log("Polling failed with status: %d for user: %s" % (status, self.user,))
            return None
            
        # Parse the XML to find etags
        hrefs = {}
        xml = ElementTree.XML(data)
        for response in xml.getiterator("{DAV:}response"):
            href = response.find("{DAV:}href")
            if href is None:
                raise ValueError("Invalid XML response: %s" % (data,))
            elif href.text == uri:
                continue
            href = href.text
            etag = [i for i in response.getiterator("{DAV:}getetag")]
            if len(etag) != 1:
                raise ValueError("Invalid XML response: %s" % (data,))
            etag = unq(etag[0].text)
            if etag:
                hrefs[href] = etag
                
        return hrefs

    def doMultiget(self, uri, calname, hrefs):
        self.log("Getting %d resources in calendar: %s for user %s" % (len(hrefs), uri, self.user,))
        hreftxt = "\n".join(["<D:href>%s</D:href>" % href for href in hrefs])
        status, headers, data = self.doRequest(uri, "REPORT", {"Content-Type": "application/xml"}, REPORT_multiget % (hreftxt,))
        if status != 207:
            self.log("Polling failed with status: %d for user: %s" % (status, self.user,))
            return
            
        # Parse the XML to find etags
        results = {}
        xml = ElementTree.XML(data)
        for response in xml.getiterator("{DAV:}response"):
            href = response.find("{DAV:}href")
            if href is None:
                raise ValueError("Invalid XML response: %s" % (data,))
            elif href == uri:
                continue
            href = href.text
            etag = [i for i in response.getiterator("{DAV:}getetag")]
            if len(etag) != 1:
                raise ValueError("Invalid XML response: %s" % (data,))
            etag = unq(etag[0].text)
            caldata = [i for i in response.getiterator("{urn:ietf:params:xml:ns:caldav}calendar-data")]
            if len(caldata) != 1:
                raise ValueError("Invalid XML response: %s" % (data,))
            caldata = caldata[0].text
            results[href] = (etag, caldata)
            
        return results

    def doSchedule(self, uri, data, headers):
        self.log("Scheduling POST for user %s" % (self.user,))
        headers["Content-Type"] = "text/calendar"
        status, headers, data = self.doRequest(uri, "POST", headers, data)
        if status != 200:
            self.log("Polling failed with status: %d for user: %s" % (status, self.user,))
            return
            
        # Parse the XML to find etags
        results = {}
        xml = ElementTree.XML(data)
        for response in xml.getiterator("{urn:ietf:params:xml:ns:caldav}response"):
            pass
            
        return results
