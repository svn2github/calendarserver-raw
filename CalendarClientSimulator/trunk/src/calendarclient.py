##
# Copyright (c) 2007 Apple Inc. All rights reserved.
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
# DRI: Cyrus Daboo, cdaboo@apple.com
##

from xml.etree import ElementTree
from random import randint
from random import sample
import icalutils
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

PLIST_VERSION = "1"

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
        #self.interval = 15
        #self.eventsperday = 10 * 24 * 60
        #self.invitesperday = 5 * 24 * 60
        self.interval = 15 * 60
        self.eventsperday = 10
        self.invitesperday = 5
        self.cache = None
        self.clearcache = False
        self.verbose = False
        self.data = {
            "version":PLIST_VERSION,
            "ctags": {
                "calendar":"",
                "inbox":""
            },
            "calendar_data": {}
        }
        self.logger = None

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
            if self.logger:
                self.logger(text)
            else:
                print text

    def setLogger(self, logger):
        self.logger = logger

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
        self.log("Polling: %s" % (self.user,))
        status, _ignore_headers, data = self.doRequest(self.home, "PROPFIND", {"Content-Type": "application/xml", "Depth":"1"}, PROPFIND_ctag)
        if status != 207:
            self.log("Polling: %s failed: %d" % (self.user, status,))
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
                    self.log("  Calendar changed: %s" % (href,))
                    
        # Now do Depth:1 poll of changed calendars
        did_change = False
        for href, calname, ctag in changed:
            if calname == "calendar":
                self.doFullCalendarPoll(href)
                self.data["ctags"][calname] = ctag
                did_change = True
            elif calname == "inbox":
                self.doInboxPoll(href)
                self.data["ctags"][calname] = ctag
                did_change = True

        if did_change:
            self.writeCache()
    
    def doFullCalendarPoll(self, uri):
        self.log("    Getting calendar: %s for: %s" % (uri, self.user,))
        
        # Get href/etag map
        hrefs = self.doPropfindDepth1(uri)
        if hrefs is None:
            return
            
        # Find new ones
        server_hrefs = set(hrefs.keys())
        local_hrefs = set(self.data["calendar_data"].keys())
        new_hrefs = server_hrefs.difference(local_hrefs)
        
        # Delete old ones
        delete_hrefs = local_hrefs.difference(server_hrefs)
        for href in delete_hrefs:
            del self.data["calendar_data"][href]

        # Find changed ones
        changed = set()
        for href, entry in self.data["calendar_data"].iteritems():
            if hrefs[href] != entry["etag"]:
                changed.add(href)
                
        # Now do multiget of all new/changed items
        if len(changed) or len(new_hrefs):
            self.log("      Changed: %d  New: %d" % (len(changed), len(new_hrefs)))
        changed.update(new_hrefs)
        if changed:
            results = self.doMultiget(uri, changed)
            
            # Cache the new etags - we don't care about the data
            for href, (etag, data) in results.iteritems():
                self.data["calendar_data"][href] = {
                    "etag": etag,
                    "uid" : icalutils.parseUID(data),
                    "data": data,
                }

    def doInboxPoll(self, uri):
        self.log("    Getting calendar: %s for: %s" % (uri, self.user,))

        # Get href/etag map
        hrefs = self.doPropfindDepth1(uri)
        if not hrefs:
            return
        
        # Process each new item
        for href in hrefs:
            data = self.getEvent(href)
            if data:
                self.doProcessiTIP(data)

            # Always delete the inbox item
            self.doDelete(href)

    def doProcessiTIP(self, caldata):
        
        # Get the data
        calendar = icalutils.parseCalendar(caldata)
        if calendar:        
            # Determine method
            method = calendar.getMethod()
            if method == "REQUEST":
                self.processiTIPRequest(calendar)
            elif method == "REPLY":
                self.processiTIPReply(calendar)
    
    def processiTIPRequest(self, calendar):
        organizer = icalutils.getORGANZIER(calendar)
        self.log("      Processing iTIP Request for:  %s from: %s" % (self.user, organizer.split("/")[-2],))
       
        # Generate data for this user
        me = "/principals/users/%s/" % (self.user,)

        # Generate accepted data and write to main calendar
        caldata = icalutils.generateiTIPSave(calendar, me)
        self.createEvent(caldata)
        
        # Generate reply and POST
        self.log("        Sending  iTIP Reply   from: %s to:   %s" % (self.user, organizer.split("/")[-2],))
        caldata, recipient = icalutils.generateiTIPReply(calendar, me)
        self.doSchedule(self.outbox, caldata, headers={"originator":me, "recipient":recipient})

    def processiTIPReply(self, calendar):
        # Extract attendee from iTIP reply
        attendee = icalutils.getATTENDEE(calendar)
        self.log("      Processing iTIP Reply   to:   %s from: %s" % (self.user, attendee.split("/")[-2],))
               
        # Find matching UID on calendar
        uid = icalutils.getUID(calendar)
        for href, entry in self.data["calendar_data"].iteritems():
            if entry["uid"] == uid:
                break
        else:
            return
        
        # Update calendar data with attendee status
        caldata = icalutils.updateAttendeeStatus(entry["data"], attendee)
        self.putEvent(href, caldata)

    def doCreateEvent(self, organizer=None, attendees=None):
        caldata = icalutils.generateVEVENT(organizer, attendees)
        return self.createEvent(caldata)

    def createEvent(self, caldata):
        uri = "%s%s.ics" % (self.calendar, uuid.uuid4(),)
        self.putEvent(uri, caldata, True)
        return uri

    def putEvent(self, uri, caldata, creating=False):
        status, _ignore_headers, _ignore_data = self.doRequest(uri, "PUT", {"Content-Type": "text/calendar"}, caldata)
        if creating and (status != 201) or not creating and status not in (200, 204):
            self.log("Event write failed with status: %d for user: %s" % (status, self.user,))
            return
        
        status, headers, data = self.doRequest(uri, "GET")
        if status != 200:
            self.log("Event read failed with status: %d for user: %s" % (status, self.user,))
            return
        for header, value in headers:
            if header == "etag":
                etag = unq(value)
                self.data["calendar_data"][uri] = {
                    "etag": etag,
                    "uid" : icalutils.parseUID(data),
                    "data": data,
                }
                self.writeCache()
                break

    def getEvent(self, uri):
        
        status, _ignore_headers, data = self.doRequest(uri, "GET")
        if status != 200:
            self.log("Event read of %s failed with status: %d for user: %s" % (uri, status, self.user,))
            return None

        return data

    def generateInviteAttendees(self):
        num_attendees = randint(1, 10)
        my_num = int(self.user[4:])
        attendee_list = ["user%02d" % i for i in xrange(1,99)if i != my_num]
        random_list = sample(attendee_list, num_attendees)
        attendees = ["/principals/users/%s/" % (attendee,) for attendee in random_list]
        
#        if self.user == "user01":
#            attendees.append("/principals/users/user02/")
#        elif self.user == "user02":
#            attendees.append("/principals/users/user01/")
        return attendees

    def doInvite(self):
        # Generate data for this user
        organizer = "/principals/users/%s/" % (self.user,)

        # Generate a set of users
        attendees = self.generateInviteAttendees()
        self.log("Sending invite from: %s to:   %s" % (self.user, ", ".join([attendee.split("/")[-2] for attendee in attendees])))
                
        # Create invite on main calendar
        new_uri = self.doCreateEvent(organizer, attendees)

        # Do free busy lookup
        fbdata = icalutils.generateVFREEBUSY(organizer, attendees)
        self.doSchedule(self.outbox, fbdata, headers={"originator":organizer, "recipient":", ".join((organizer,) + tuple(attendees))})

        # TODO: Update invite (simulates a time shift after f-b lookup)
        
        # Do outbox POST
        entry = self.data["calendar_data"][new_uri]
        caldata = entry["data"].replace("VERSION:", "METHOD:REQUEST\nVERSION:")
        self.doSchedule(self.outbox, caldata, headers={"originator":organizer, "recipient":", ".join(attendees)})

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
        status, _ignore_headers, data = self.doRequest(uri, "PROPFIND", {"Content-Type": "application/xml", "Depth": "1"}, PROPFIND_etag)
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

    def doMultiget(self, uri, hrefs):
        hreftxt = "\n".join(["<D:href>%s</D:href>" % href for href in hrefs])
        status, _ignore_headers, data = self.doRequest(uri, "REPORT", {"Content-Type": "application/xml"}, REPORT_multiget % (hreftxt,))
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
        headers["Content-Type"] = "text/calendar"
        status, headers, data = self.doRequest(uri, "POST", headers, data)
        if status != 200:
            self.log("POST failed with status: %d for user: %s" % (status, self.user,))
            return
            
        # Parse the XML to find etags
        results = {}
        xml = ElementTree.XML(data)
        for _ignore_response in xml.getiterator("{urn:ietf:params:xml:ns:caldav}response"):
            pass
            
        return results

    def doDelete(self, uri):
        status, _ignore_headers, _ignore_data = self.doRequest(uri, "DELETE")
        if status != 204:
            self.log("Delete of %s failed with status: %d for user: %s" % (uri, status, self.user,))
