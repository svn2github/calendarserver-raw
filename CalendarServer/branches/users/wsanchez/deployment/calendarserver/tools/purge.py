#!/usr/bin/env python

##
# Copyright (c) 2006-2010 Apple Inc. All rights reserved.
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

from datetime import datetime
from getopt import getopt, GetoptError
from grp import getgrnam
from pwd import getpwnam
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python.util import switchUID
from twistedcaldav.caldavxml import TimeRange
from twistedcaldav.config import config, ConfigurationError
from twistedcaldav.directory.directory import DirectoryError, DirectoryRecord
from twistedcaldav.ical import Component, iCalendarProductID, Property
from vobject.icalendar import utc
import os
import sys
from calendarserver.tools.util import loadConfig, setupNotifications,\
    getDirectory, setupMemcached
from twistedcaldav.root import RootResource
from twistedcaldav.static import CalendarHomeProvisioningFile
from twistedcaldav import caldavxml
from twisted.web2.http_headers import Headers

class FakeRequest(object):

    def __init__(self, rootResource, method, path):
        self.rootResource = rootResource
        self.method = method
        self.path = path
        self._resourcesByURL = {}
        self._urlsByResource = {}
        self.headers = Headers()

    @inlineCallbacks
    def _getChild(self, resource, segments):
        if not segments:
            returnValue(resource)

        child, remaining = (yield resource.locateChild(self, segments))
        returnValue((yield self._getChild(child, remaining)))

    @inlineCallbacks
    def locateResource(self, url):
        url = url.strip("/")
        segments = url.split("/")
        resource = (yield self._getChild(self.rootResource, segments))
        if resource:
            self._rememberResource(resource, url)
        returnValue(resource)

    def _rememberResource(self, resource, url):
        self._resourcesByURL[url] = resource
        self._urlsByResource[resource] = url
        return resource

    def urlForResource(self, resource):
        url = self._urlsByResource.get(resource, None)
        if url is None:
            class NoURLForResourceError(RuntimeError):
                pass
            raise NoURLForResourceError(resource)
        return url

    def addResponseFilter(*args, **kwds):
        pass

def usage_purge_principal(e=None):

    if e:
        print "Error: %s" % (e,)
        print ""
    name = os.path.basename(sys.argv[0])
    print "usage: %s [options]" % (name,)
    print ""
    print "  Remove a principal's events from the calendar server"
    print ""
    print "options:"
    print "  -f --config <path>: Specify caldavd.plist configuration path"
    print "  -h --help: print this help and exit"
    print "  -n --dry-run: only calculate how many events to purge"
    print "  -v --verbose: print progress information"
    print ""

    if e:
        sys.exit(64)
    else:
        sys.exit(0)



def shared_main(configFileName, method, *args, **kwds):

    try:
        loadConfig(configFileName)

        # Shed privileges
        if config.UserName and config.GroupName and os.getuid() == 0:
            uid = getpwnam(config.UserName).pw_uid
            gid = getgrnam(config.GroupName).gr_gid
            switchUID(uid, uid, gid)

        os.umask(config.umask)

        try:
            config.directory = getDirectory()

            #
            # Wire up the resource hierarchy
            #
            principalCollection = config.directory.getPrincipalCollection()
            rootResource = RootResource(
                config.DocumentRoot,
                principalCollections=(principalCollection,),
            )
            rootResource.putChild("principals", principalCollection)
            calendarCollection = CalendarHomeProvisioningFile(
                os.path.join(config.DocumentRoot, "calendars"),
                config.directory, "/calendars/",
            )
            rootResource.putChild("calendars", calendarCollection)

        except DirectoryError, e:
            print "Error: %s" % (e,)
            return
        setupMemcached(config)
        setupNotifications(config)
    except ConfigurationError, e:
        raise


    #
    # Start the reactor
    #
    reactor.callLater(0.1, callThenStop, method, config.directory,
        rootResource, *args, **kwds)

    reactor.run()

def main_purge_principals():

    try:
        (optargs, args) = getopt(
            sys.argv[1:], "f:hnv", [
                "dry-run",
                "config=",
                "help",
                "verbose",
            ],
        )
    except GetoptError, e:
        usage_purge_principal(e)

    #
    # Get configuration
    #
    configFileName = None
    dryrun = False
    verbose = False

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage_purge_principal()

        elif opt in ("-v", "--verbose"):
            verbose = True

        elif opt in ("-n", "--dry-run"):
            dryrun = True

        elif opt in ("-f", "--config"):
            configFileName = arg

        else:
            raise NotImplementedError(opt)

    # args is a list of guids

    try:
        shared_main(configFileName, purgeGUIDs, args, verbose=verbose, dryrun=dryrun)
    except ConfigurationError, e:
        usage_purge_principal(e)

@inlineCallbacks
def callThenStop(method, *args, **kwds):
    try:
        count = (yield method(*args, **kwds))
        if kwds.get("dryrun", False):
            print "Would have purged %d principals" % (count,)
        else:
            print "Purged %d principals" % (count,)
    except Exception, e:
        print "Error: %s" % (e,)
    finally:
        reactor.stop()


@inlineCallbacks
def purgeGUIDs(directory, root, guids, verbose=False, dryrun=False):
    total = 0

    for guid in guids:
        yield purgeGUID(guid, directory, root, verbose=verbose, dryrun=dryrun)
        total += 1

    returnValue(total)


@inlineCallbacks
def purgeGUID(guid, directory, root, verbose=False, dryrun=False):

    # Does the record exist?
    record = directory.recordWithGUID(guid)
    if record is None:
        # The user has already been removed from the directory service.  We
        # need to fashion a temporary, fake record

        # FIXME: probably want a more elegant way to accomplish this,
        # since it requires the aggregate directory to examine these first:
        record = DirectoryRecord(directory, "users", guid, shortNames=(guid,),
            enabledForCalendaring=True)
        record.enabled = True
        directory._tmpRecords["shortNames"][guid] = record
        directory._tmpRecords["guids"][guid] = record

    principalCollection = directory.principalCollection
    principal = principalCollection.principalForRecord(record)
    calendarHome = principal._calendarHome()

    # Anything in the past is left alone
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filter =  caldavxml.Filter(
          caldavxml.ComponentFilter(
              caldavxml.ComponentFilter(
                  TimeRange(start=now,),
                  name=("VEVENT",),
              ),
              name="VCALENDAR",
           )
      )

    organized = 0
    attended = 0

    for collName in calendarHome.listChildren():
        collection = calendarHome.getChild(collName)
        if collection.isCalendarCollection():

            for name, _ignore_uid, type in collection.index().search(filter):
                if isinstance(name, unicode):
                    name = name.encode("utf-8")
                resource = collection.getChild(name)
                uri = "/calendars/__uids__/%s/%s/%s" % (
                    record.guid,
                    collName,
                    name
                )
                if not dryrun:
                    result = (yield processResource(root, collection, resource,
                                                    uri, guid, principal, implicit=True))
                    if result is not None:
                        if result:
                            organized += 1
                        else:
                            attended += 1

    print "Cancelled %d events, declined %d events, for user: %s" % (organized, attended, guid,)

@inlineCallbacks
def processResource(root, collection, resource, uri, guid, principal, implicit=False):
    
    calendar = resource.iCalendar()
    organizer = calendar.getOrganizer()
    if organizer is None:
        returnValue(None)
    organizerPrincipal = resource.principalForCalendarUserAddress(organizer)

    # Determine if user is the Organizer
    isOrganizer = guid == organizerPrincipal.principalUID()
    
    if not isOrganizer:
        cuas = principal.calendarUserAddresses()
        attendeeProps = calendar.getAttendeeProperties(cuas)
        if not attendeeProps:
            returnValue(None)

    originator = None
    recipients = None
    if isOrganizer:
        originator = organizer
        allAttendees = tuple(getAllAttendeeProperties(calendar))
        recipients = [attendee.value() for attendee in allAttendees if attendee.value() != organizer]

        # Send iTIP CANCEL to all ATTENDEEs
        master = calendar.masterComponent()
        if master is None:
            for component in calendar.subcomponents():
                if component.name() != "VTIMEZONE":
                    master = component
                    break
        
        itipmsg = Component("VCALENDAR")
        itipmsg.addProperty(Property("PRODID", iCalendarProductID))
        itipmsg.addProperty(Property("METHOD", "CANCEL"))
        itipmsg.addProperty(Property("VERSION", "2.0"))
        event = Component("VEVENT")
        itipmsg.addComponent(event)
        event.addProperty(Property("DTSTAMP", datetime.now(tz=utc)))
        event.addProperty(Property("UID", calendar.resourceUID()))
        if master.hasProperty("SUMMARY"):
            event.addProperty(Property("SUMMARY", master.propertyValue("SUMMARY")))
        if master.hasProperty("DTSTART"):
            event.addProperty(master.getProperty("DTSTART"))
        if master.hasProperty("DTEND"):
            event.addProperty(master.getProperty("DTEND"))
        if master.hasProperty("DURATION"):
            event.addProperty(master.getProperty("DURATION"))
        event.addProperty(master.getProperty("ORGANIZER"))
        for attendee in allAttendees:
            event.addProperty(attendee)
            
        # Bump sequence
        if master.hasProperty("SEQUENCE"):
            sequence = int(master.propertyValue("SEQUENCE"))
        else:
            sequence = 0
        event.addProperty(Property("SEQUENCE", str(sequence + 1)))

    else:
        # Send iTIP DECLINE to ORGANIZER
        itipmsg = calendar.duplicate()
        itipmsg.addProperty(Property("METHOD", "REPLY"))
        itipmsg.getProperty("PRODID").setValue(iCalendarProductID)
        
        # Add REQUEST-STATUS
        for component in itipmsg.subcomponents():
            component.addProperty(Property(name="REQUEST-STATUS", value="2.0; Success."))

        # Remove all attendees other than ourselves
        for component in itipmsg.subcomponents():
            if component.name() == "VTIMEZONE":
                continue
            attendeeProp = component.getAttendeeProperty(cuas)
            attendees = tuple(component.properties("ATTENDEE"))
            for attendee in attendees:
                if attendeeProp is None or (attendee.value() != attendeeProp.value()):
                    component.removeProperty(attendee)
        
        # Decline all (and check if they were all declined before)
        allDeclined = True
        attendeeProps = itipmsg.getAttendeeProperties(cuas)
        for attendeeProp in attendeeProps:
            if "PARTSTAT" in attendeeProp.params():
                if attendeeProp.params()["PARTSTAT"][0] != "DECLINED":
                    allDeclined = False
                attendeeProp.params()["PARTSTAT"][0] = "DECLINED"
            else:
                attendeeProp.params()["PARTSTAT"] = ["DECLINED"]
                allDeclined = False

        originator = attendeeProps[0].value()
        recipients = [organizer,]
        
        if allDeclined:
            returnValue(None)
        
    # Send iTIP message
    # This is a local CALDAV scheduling operation.
    from twistedcaldav.scheduling.scheduler import CalDAVScheduler
    scheduler = CalDAVScheduler(FakeRequest(root, "PUT", uri), resource)

    # Do the POST processing treating
    yield scheduler.doSchedulingViaPUT(originator, recipients, itipmsg, True)

    returnValue(isOrganizer)

# ical.py extensions that are already on trunk but not being back-ported to deployment right now

def getAllAttendeeProperties(self):
    """
    Yield all attendees as Property objects.  Works on either a VCALENDAR or
    on a component.
    @return: a generator yielding Property objects
    """

    # Extract appropriate sub-component if this is a VCALENDAR
    if self.name() == "VCALENDAR":
        for component in self.subcomponents():
            if component.name() != "VTIMEZONE":
                for attendee in getAllAttendeeProperties(component):
                    yield attendee
    else:
        # Find the primary subcomponent
        for attendee in self.properties("ATTENDEE"):
            yield attendee

if __name__ == "__main__":
    main_purge_principals()

