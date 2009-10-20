#!/usr/bin/env python
from twistedcaldav.directory import augment
from twistedcaldav.directory.augment import AugmentXMLDB

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
##


from getopt import getopt, GetoptError
from grp import getgrnam
from pwd import getpwnam
from sys import stdout, stderr
from twisted.internet import reactor
from twisted.python.log import addObserver, removeObserver
from twisted.python.util import switchUID
from twistedcaldav.config import config, ConfigurationError
from twistedcaldav.log import setLogLevelForNamespace
from twisted.internet.defer import inlineCallbacks
from calendarserver.tools.util import loadConfig, getDirectory,\
    autoDisableMemcached
import os
import sys

class UsageError (StandardError):
    pass

class StandardIOObserver (object):
    """
    Log observer that writes to standard I/O.
    """
    def emit(self, eventDict):
        text = None

        if eventDict["isError"]:
            output = stderr
            if "failure" in eventDict:
                text = eventDict["failure"].getTraceback()
        else:
            output = stdout

        if not text:
            text = " ".join([str(m) for m in eventDict["message"]]) + "\n"

        output.write(text)
        output.flush()

    def start(self):
        addObserver(self.emit)

    def stop(self):
        removeObserver(self.emit)

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

    if e:
        sys.exit(64)
    else:
        sys.exit(0)

def main():
    try:
        (optargs, args) = getopt(
            sys.argv[1:], "hf:x:", [
                "config=",
                "xmlfile=",
                "help",
            ],
        )
    except GetoptError, e:
        usage(e)

    configFileName = None
    xmlFileName = None

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage()

        elif opt in ("-f", "--config"):
            configFileName = arg

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
        dbxml = AugmentXMLDB((xmlFileName,))
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
        yield augment.AugmentService.clean()
        for record in dbxml.db.values():
            yield augment.AugmentService.addAugmentRecord(record)
    finally:
        #
        # Stop the reactor
        #
        reactor.stop()
