#!/usr/bin/env python
##
# Copyright (c) 2009-2013 Apple Inc. All rights reserved.
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

from calendarserver.tools.managetimezones import StandardIOObserver
from calendarserver.tools.util import loadConfig, getDirectory, \
    autoDisableMemcached

from getopt import getopt, GetoptError
from grp import getgrnam
from pwd import getpwnam

from twext.python.log import setLogLevelForNamespace

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.python.util import switchUID

from twistedcaldav.config import config, ConfigurationError
from twistedcaldav.directory import augment
from twistedcaldav.directory.augment import AugmentXMLDB

import os
import sys

class UsageError (StandardError):
    pass



def usage(e=None):
    if e:
        print e
        print ""

    name = os.path.basename(sys.argv[0])
    print "usage: %s [options]" % (name,)
    print ""
    print "Populate an sqlite or PostgreSQL augments database with values"
    print "from an XML augments file."
    print ""
    print "options:"
    print "  -h --help: print this help and exit"
    print "  -f --config: Specify caldavd.plist configuration path"
    print "  -x --xmlfile: Specify xml augments file path"
    print "  -r --remove: Remove all entries from the database"

    if e:
        sys.exit(64)
    else:
        sys.exit(0)



def main():
    try:
        (optargs, args) = getopt(
            sys.argv[1:], "hf:rx:", [
                "config=",
                "remove",
                "xmlfile=",
                "help",
            ],
        )
    except GetoptError, e:
        usage(e)

    configFileName = None
    xmlFileName = None
    remove = False

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage()

        elif opt in ("-f", "--config"):
            configFileName = arg

        elif opt in ("-r", "--remove"):
            remove = True
            if raw_input("Do you really want to remove all records from the database? [y/n] ") != "y":
                sys.exit(0)

        elif opt in ("-x", "--xmlfile"):
            xmlFileName = arg

    if args:
        usage("Too many arguments: %s" % (" ".join(args),))

    observer = StandardIOObserver()
    observer.start()

    #
    # Get configuration
    #
    try:
        loadConfig(configFileName)
        setLogLevelForNamespace(None, "warn")

        # Shed privileges
        if config.UserName and config.GroupName and os.getuid() == 0:
            uid = getpwnam(config.UserName).pw_uid
            gid = getgrnam(config.GroupName).gr_gid
            switchUID(uid, uid, gid)

        os.umask(config.umask)

        config.directory = getDirectory()
        autoDisableMemcached(config)
    except ConfigurationError, e:
        usage("Unable to start: %s" % (e,))

    try:
        dbxml = AugmentXMLDB((xmlFileName,)) if not remove else None
    except IOError, e:
        usage("Could not read XML augment file: %s" % (e,))

    #
    # Start the reactor
    #
    reactor.callLater(0, run, dbxml)
    reactor.run()



@inlineCallbacks
def run(dbxml):

    try:
        uids = set((yield augment.AugmentService.getAllUIDs()))
        added = 0
        updated = 0
        removed = 0
        if dbxml:
            yield augment.AugmentService.addAugmentRecords(dbxml.db.values(),)
            add_records = list()
            modify_records = list()
            for record in dbxml.db.values():
                if record.uid in uids:
                    modify_records.append(record)
                else:
                    add_records.append(record)
            added = len(add_records)
            updated = len(modify_records)

            remove_uids = uids.difference(dbxml.db.keys())
            yield augment.AugmentService.removeAugmentRecords(remove_uids)
            removed = len(remove_uids)

        print "Changes:"
        print "  Added: %d" % (added,)
        print "  Changed: %d" % (updated,)
        print "  Removed: %d" % (removed,)
    finally:
        #
        # Stop the reactor
        #
        reactor.stop()
