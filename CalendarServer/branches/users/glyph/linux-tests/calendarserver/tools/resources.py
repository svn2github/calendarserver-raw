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

from calendarserver.tools.util import loadConfig, getDirectory, setupMemcached, checkDirectory
from getopt import getopt, GetoptError
from grp import getgrnam
from pwd import getpwnam
from twext.python.log import StandardIOObserver
from twext.python.log import clearLogLevels
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.python.util import switchUID
from twistedcaldav.config import config, ConfigurationError
from twistedcaldav.directory import augment
from twistedcaldav.directory.appleopendirectory import OpenDirectoryService
from twistedcaldav.directory.directory import DirectoryService, DirectoryError
from twistedcaldav.directory.xmlfile import XMLDirectoryService
import os
import sys

from calendarserver.platform.darwin.od import dsattributes

__all__ = [ "migrateResources", ]

def usage():

    name = os.path.basename(sys.argv[0])
    print "usage: %s [options] " % (name,)
    print ""
    print "  Migrates resources and locations from OD to Calendar Server"
    print ""
    print "options:"
    print "  -h --help: print this help and exit"
    print "  -f --config <path>: Specify caldavd.plist configuration path"
    print "  -v --verbose: print debugging information"
    print ""

    sys.exit(0)

def abort(msg, status=1):
    sys.stdout.write("%s\n" % (msg,))
    try:
        reactor.stop()
    except RuntimeError:
        pass
    sys.exit(status)

def main():
    try:
        (optargs, args) = getopt(
            sys.argv[1:], "hf:v", [
                "help",
                "config=",
                "verbose",
            ],
        )
    except GetoptError, e:
        usage(e)

    #
    # Get configuration
    #
    configFileName = None
    verbose = False

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage()

        elif opt in ("-v", "--verbose"):
            verbose = True

        elif opt in ("-f", "--config"):
            configFileName = arg

        else:
            raise NotImplementedError(opt)

    #
    # Get configuration
    #
    try:
        loadConfig(configFileName)

        # Do this first, because modifying the config object will cause
        # some logging activity at whatever log level the plist says
        clearLogLevels()

        config.DefaultLogLevel = "info" if verbose else "error"

        #
        # Send logging output to stdout
        #
        observer = StandardIOObserver()
        observer.start()

        # Create the DataRoot directory before shedding privileges
        if config.DataRoot.startswith(config.ServerRoot + os.sep):
            checkDirectory(
                config.DataRoot,
                "Data root",
                access=os.W_OK,
                create=(0750, config.UserName, config.GroupName),
            )

        # Shed privileges
        if config.UserName and config.GroupName and os.getuid() == 0:
            uid = getpwnam(config.UserName).pw_uid
            gid = getgrnam(config.GroupName).gr_gid
            switchUID(uid, uid, gid)

        os.umask(config.umask)

        # Configure memcached client settings prior to setting up resource
        # hierarchy (in getDirectory)
        setupMemcached(config)

        try:
            config.directory = getDirectory()
        except DirectoryError, e:
            abort(e)

    except ConfigurationError, e:
        abort(e)

    # Find the opendirectory service
    userService = config.directory.serviceForRecordType("users")
    resourceService = config.directory.serviceForRecordType("resources")
    if (not isinstance(userService, OpenDirectoryService) or
        not isinstance(resourceService, XMLDirectoryService)):
        abort("This script only migrates resources and locations from OpenDirectory to XML; this calendar server does not have such a configuration.")

    #
    # Start the reactor
    #
    reactor.callLater(0, migrate, userService, resourceService, verbose=verbose)
    reactor.run()



@inlineCallbacks
def migrate(sourceService, resourceService, verbose=False):
    """
    Simply a wrapper around migrateResources in order to stop the reactor
    """

    try:
        yield migrateResources(sourceService, resourceService, verbose=verbose)
    finally:
        reactor.stop()


def queryForType(sourceService, recordType, verbose=False):
    """
    Queries OD for all records of the specified record type
    """

    attrs = [
        dsattributes.kDS1AttrGeneratedUID,
        dsattributes.kDS1AttrDistinguishedName,
    ]

    if verbose:
        print "Querying for all %s records" % (recordType,)

    results = list(sourceService.odModule.listAllRecordsWithAttributes_list(
        sourceService.directory,
        recordType,
        attrs,
    ))

    if verbose:
        print "Found %d records" % (len(results),)

    return results


@inlineCallbacks
def migrateResources(sourceService, destService, autoSchedules=None,
    queryMethod=queryForType, verbose=False):

    directoryRecords = []
    augmentRecords = []

    for recordTypeOD, recordType in (
        (dsattributes.kDSStdRecordTypeResources, DirectoryService.recordType_resources),
        (dsattributes.kDSStdRecordTypePlaces, DirectoryService.recordType_locations),
    ):
        data = queryMethod(sourceService, recordTypeOD, verbose=verbose)
        for recordName, val in data:
            guid = val.get(dsattributes.kDS1AttrGeneratedUID, None)
            fullName = val.get(dsattributes.kDS1AttrDistinguishedName, None)
            if guid and fullName:
                if not recordName:
                    recordName = guid
                record = destService.recordWithGUID(guid)
                if record is None:
                    if verbose:
                        print "Migrating %s (%s)" % (fullName, recordType)

                    if autoSchedules is not None:
                        autoSchedule = autoSchedules.get(guid, 1)
                    else:
                        autoSchedule = True
                    augmentRecord = (yield augment.AugmentService.getAugmentRecord(guid, recordType))
                    augmentRecord.autoSchedule = autoSchedule
                    augmentRecords.append(augmentRecord)

                    directoryRecords.append(
                        (recordType,
                            {
                                "guid" : guid,
                                "shortNames" : [recordName],
                                "fullName" : fullName,
                            }
                        )
                    )

    destService.createRecords(directoryRecords)

    (yield augment.AugmentService.addAugmentRecords(augmentRecords))



if __name__ == "__main__":
    main()
