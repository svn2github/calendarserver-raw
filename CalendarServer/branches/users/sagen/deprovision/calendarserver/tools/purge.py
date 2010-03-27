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

from calendarserver.tap.util import FakeRequest
from calendarserver.tap.util import getRootResource
from calendarserver.tools.util import loadConfig, setupMemcached, setupNotifications
from datetime import date, timedelta, datetime
from getopt import getopt, GetoptError
from grp import getgrnam
from pwd import getpwnam
from twext.python.log import Logger
from twext.web2.dav import davxml
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.python.util import switchUID
from twistedcaldav import caldavxml
from twistedcaldav.caldavxml import TimeRange
from twistedcaldav.config import config, ConfigurationError
from twistedcaldav.directory.directory import DirectoryError
from twistedcaldav.method.delete_common import DeleteResource
import os
import sys

log = Logger()

def usage_purge(e=None):

    name = os.path.basename(sys.argv[0])
    print "usage: %s [options]" % (name,)
    print ""
    print "  Remove old events from the calendar server"
    print ""
    print "options:"
    print "  -d --days <number>: specify how many days in the past to retain (default=365)"
    print "  -f --config <path>: Specify caldavd.plist configuration path"
    print "  -h --help: print this help and exit"
    print "  -n --dry-run: only calculate how many events to purge"
    print "  -v --verbose: print progress information"
    print ""

    if e:
        sys.exit(64)
    else:
        sys.exit(0)


def main_purge():

    try:
        (optargs, args) = getopt(
            sys.argv[1:], "d:f:hnv", [
                "days=",
                "dry-run",
                "config=",
                "help",
                "verbose",
            ],
        )
    except GetoptError, e:
        usage_purge(e)

    #
    # Get configuration
    #
    configFileName = None
    days = 365
    dryrun = False
    verbose = False

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage_purge()

        elif opt in ("-d", "--days"):
            try:
                days = int(arg)
            except ValueError, e:
                print "Invalid value for --days: %s" % (arg,)
                usage_purge(e)

        elif opt in ("-v", "--verbose"):
            verbose = True

        elif opt in ("-n", "--dry-run"):
            dryrun = True

        elif opt in ("-f", "--config"):
            configFileName = arg

        else:
            raise NotImplementedError(opt)

    cutoff = (date.today()-timedelta(days=days)).strftime("%Y%m%dT000000Z")

    try:
        loadConfig(configFileName)

        # Shed privileges
        if config.UserName and config.GroupName and os.getuid() == 0:
            uid = getpwnam(config.UserName).pw_uid
            gid = getgrnam(config.GroupName).gr_gid
            switchUID(uid, uid, gid)

        os.umask(config.umask)

        try:
            rootResource = getRootResource(config)
            directory = rootResource.getDirectory()
        except DirectoryError, e:
            print "Error: %s" % (e,)
            return
        setupMemcached(config)
        setupNotifications(config)
    except ConfigurationError, e:
        print "Error: %s" % (e,)
        return


    #
    # Start the reactor
    #
    reactor.callLater(0.1, callThenStop, purgeOldEvents, directory,
        rootResource, cutoff, verbose=verbose, dryrun=dryrun)

    reactor.run()

@inlineCallbacks
def callThenStop(method, *args, **kwds):
    try:
        count = (yield method(*args, **kwds))
        if kwds.get("dryrun", False):
            print "Would have purged %d events" % (count,)
        else:
            print "Purged %d events" % (count,)
    except Exception, e:
        print "Error: %s" % (e,)
    finally:
        reactor.stop()


@inlineCallbacks
def purgeOldEvents(directory, root, date, verbose=False, dryrun=False):

    calendars = root.getChild("calendars")
    uidsFPath = calendars.fp.child("__uids__")

    if dryrun:
        print "Dry run"

    if verbose:
        print "Scanning calendar homes ...",

    records = []
    calendars = root.getChild("calendars")
    uidsFPath = calendars.fp.child("__uids__")

    if uidsFPath.exists():
        for firstFPath in uidsFPath.children():
            if len(firstFPath.basename()) == 2:
                for secondFPath in firstFPath.children():
                    if len(secondFPath.basename()) == 2:
                        for homeFPath in secondFPath.children():
                            uid = homeFPath.basename()
                            record = directory.recordWithUID(uid)
                            if record is not None:
                                records.append(record)

    if verbose:
        print "%d calendar homes found" % (len(records),)

    log.info("Purging events from %d calendar homes" % (len(records),))

    filter =  caldavxml.Filter(
          caldavxml.ComponentFilter(
              caldavxml.ComponentFilter(
                  TimeRange(start=date,),
                  name=("VEVENT", "VFREEBUSY", "VAVAILABILITY"),
              ),
              name="VCALENDAR",
           )
      )

    eventCount = 0
    for record in records:
        # Get the calendar home
        principalCollection = directory.principalCollection
        principal = principalCollection.principalForRecord(record)
        calendarHome = principal.calendarHome()

        if verbose:
            print "%s %-15s :" % (record.uid, record.shortNames[0]),

        homeEventCount = 0
        # For each collection in calendar home...
        for collName in calendarHome.listChildren():
            collection = calendarHome.getChild(collName)
            if collection.isCalendarCollection():
                # ...use their indexes to figure out which events to purge.

                # First, get the list of all child resources...
                resources = set(collection.listChildren())

                # ...and ignore those that appear *after* the given cutoff
                for name, uid, type in collection.index().indexedSearch(filter):
                    if name in resources:
                        resources.remove(name)

                for name in resources:
                    resource = collection.getChild(name)
                    uri = "/calendars/__uids__/%s/%s/%s" % (
                        record.uid,
                        collName,
                        name
                    )
                    try:
                        if not dryrun:
                            (yield deleteResource(root, collection, resource,
                                uri, record.guid))
                        eventCount += 1
                        homeEventCount += 1
                    except Exception, e:
                        log.error("Failed to purge old event: %s (%s)" %
                            (uri, e))

        if verbose:
            print "%d events" % (homeEventCount,)

    returnValue(eventCount)


def deleteResource(root, collection, resource, uri, guid, implicit=False):
    request = FakeRequest(root, "DELETE", uri)
    request.authnUser = request.authzUser = davxml.Principal(
        davxml.HRef.fromString("/principals/__uids__/%s/" % (guid,))
    )

    # TODO: this seems hacky, even for a stub request:
    request._rememberResource(resource, uri)

    deleter = DeleteResource(request, resource, uri,
        collection, "infinity", allowImplicitSchedule=implicit)
    return deleter.run()


@inlineCallbacks
def purgeGUID(guid, directory, root):

    # Does the record exist?
    record = directory.recordWithGUID(guid)
    if record is None:
        # The user has already been removed from the directory service.  We
        # need to fashion a temporary, fake record
        # FIXME: implement the fake record
        pass

    principalCollection = directory.principalCollection
    principal = principalCollection.principalForRecord(record)
    calendarHome = principal.calendarHome()

    # Anything in the past should be deleted without implicit scheduling
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filter =  caldavxml.Filter(
          caldavxml.ComponentFilter(
              caldavxml.ComponentFilter(
                  TimeRange(start=now,),
                  name=("VEVENT", "VFREEBUSY", "VAVAILABILITY"),
              ),
              name="VCALENDAR",
           )
      )

    count = 0

    for collName in calendarHome.listChildren():
        collection = calendarHome.getChild(collName)
        if collection.isCalendarCollection():

            # To compute past and ongoing events...

            # ...first start with all events...
            allEvents = set(collection.listChildren())

            ongoingEvents = set()

            # ...and find those that appear *after* the given cutoff
            for name, uid, type in collection.index().indexedSearch(filter):
                ongoingEvents.add(name)

            for name in allEvents:
                resource = collection.getChild(name)
                uri = "/calendars/__uids__/%s/%s/%s" % (
                    record.uid,
                    collName,
                    name
                )

                (yield deleteResource(root, collection, resource,
                    uri, guid, implicit=(name in ongoingEvents)))
                count += 1

    returnValue(count)
