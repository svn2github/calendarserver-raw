#!/usr/bin/env python

##
# Copyright (c) 2006-2009 Apple Inc. All rights reserved.
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

from __future__ import with_statement


import sys

if "PYTHONPATH" in globals():
    sys.path.insert(0, PYTHONPATH)
else:
    from os.path import dirname, abspath, join
    from subprocess import Popen, PIPE

    home = dirname(dirname(abspath(__file__)))
    run = join(home, "run")

    child = Popen((run, "-p"), stdout=PIPE)
    path, stderr = child.communicate()

    if child.wait() == 0:
        sys.path[0:0] = path.split(":")

# sys.path.insert(0, "/usr/share/caldavd/lib/python")

from calendarserver.provision.root import RootResource
from getopt import getopt, GetoptError
from twisted.internet import reactor
from twisted.internet.address import IPv4Address
from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twisted.python import log
from twisted.python.reflect import namedClass
from twisted.web2.dav import davxml
from twistedcaldav import caldavxml
from twistedcaldav import memcachepool
from twistedcaldav.config import config, defaultConfigFile
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twistedcaldav.notify import installNotificationClient
from twistedcaldav.static import CalendarHomeProvisioningFile
import itertools
import os

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

def usage(e=None):
    if e:
        print e
        print ""

    name = os.path.basename(sys.argv[0])
    print "usage: %s [options]" % (name,)
    print ""
    print "options:"
    print "  -h --help: print this help and exit"
    print "  -f --config <path>: Specify caldavd.plist configuration path"
    print "  --resource <prinicpal-path>: path of the resource to use"
    print "  --read-property: namespace-qualified DAV property to read, e.g. 'DAV:#group-member-set'"
    print "  --list-read-delegates: list delegates with read-only access to the current resource"
    print "  --list-write-delegates: list delegates with read-write access to the current resource"
    print "  --add-read-delegate <prinicpal-path>: add argument as a read-only delegate to the current resource"
    print "  --add-write-delegate <prinicpal-path>: add argument as a read-write delegate to the current resource"
    print "  --remove-delegate <prinicpal-path>: strip argument of delegate status for the current resource"

    if e:
        sys.exit(64)
    else:
        sys.exit(0)

class UsageError (StandardError):
    pass

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

def main():
    try:
        (optargs, args) = getopt(
            sys.argv[1:], "hf:r:s", [
                "config=",
                "help",
                "resource=",
                "read-property=",
                "list-read-delegates",
                "list-write-delegates",
                "add-read-delegate=",
                "add-write-delegate=",
                "remove-delegate=",
            ],
        )
    except GetoptError, e:
        usage(e)

    if args:
        usage("Too many arguments: %s" % (" ".join(args),))

    logFileName = "/dev/stdout"
    observer = log.FileLogObserver(open(logFileName, "a"))
    log.addObserver(observer.emit)

    # First pass through the args:

    configFileName = None

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage()

        elif opt in ("-f", "--config"):
            configFileName = arg

    loadConfig(configFileName)
    directory, root = setup()
    root = ResourceWrapper(root)

    reactor.callLater(0, run, directory, root, optargs)
    reactor.run()

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

@inlineCallbacks
def run(directory, root, optargs):

    resource = None

    for opt, arg in optargs:

        if opt in ("-r", "--resource",):
            resource = root.getChild(arg)
            if resource is not None:
                print "Found resource %s at %s" % (resource.resource, arg)
            else:
                abort("Could not find resource at %s" % (arg,))

        elif opt in ("--read-property",):
            if resource is None: abort("No current resource.")

            try:
                namespace, name = arg.split("#")
            except Exception, e:
                abort("Can't parse --propertyToRead: %s" % (arg,))

            result = (yield resource.readProperty((namespace, name)))
            print result.toxml()

        elif opt in ("--list-write-delegates", "--list-read-delegates"):
            if resource is None: abort("No current resource.")

            permission = "write" if "write" in opt else "read"
            print "Delegates (%s) for %s:" % (permission, resource.resource)
            paths = (yield resource.getDelegates(permission))
            for path in paths:
                delegate = root.getChild(path)
                print delegate.resource

        elif opt in ("--add-write-delegate", "--add-read-delegate"):
            if resource is None: abort("No current resource.")

            delegate = root.getChild(arg)
            if delegate is None:
                abort("No delegate found for %s" % (arg,))

            permission = "write" if "write" in opt else "read"
            result = (yield resource.addDelegate(delegate, permission))

        elif opt == "--remove-delegate":
            if resource is None: abort("No current resource.")

            delegate = root.getChild(arg)
            if delegate is None:
                abort("No delegate found for %s" % (arg,))

            result = (yield resource.removeDelegate(delegate, "read"))
            result = (yield resource.removeDelegate(delegate, "write"))


    print "Stopping reactor"
    reactor.stop()

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

class ResourceWrapper(object):

    def __init__(self, resource):
        self.resource = resource

    def readProperty(self, prop):
        # fake a request
        request = FakeRequest()
        return self.resource.readProperty(prop, request)

    def writeProperty(self, prop):
        # fake a request
        request = FakeRequest()
        return self.resource.writeProperty(prop, request)

    def getChild(self, path):
        resource = self.resource
        segments = path.strip("/").split("/")
        for segment in segments:
            resource = resource.getChild(segment)
            if resource is None:
                return None
        return ResourceWrapper(resource)

    @inlineCallbacks
    def removeDelegate(self, delegate, permission):
        subPrincipalName = "calendar-proxy-%s" % (permission,)
        subPrincipal = self.getChild(subPrincipalName)
        if subPrincipal is None:
            abort("No proxy subprincipal found for %s" % (self.resource,))

        namespace, name = davxml.dav_namespace, "group-member-set"
        prop = (yield subPrincipal.readProperty((namespace, name)))
        newChildren = []
        for child in prop.children:
            if str(child) != delegate.url():
                newChildren.append(child)

        if len(prop.children) == len(newChildren):
            # Nothing to do -- the delegate wasn't there
            returnValue(False)

        newProp = davxml.GroupMemberSet(*newChildren)
        result = (yield subPrincipal.writeProperty(newProp))
        returnValue(result)

    @inlineCallbacks
    def addDelegate(self, delegate, permission):

        opposite = "read" if permission == "write" else "write"
        result = (yield self.removeDelegate(delegate, opposite))

        subPrincipalName = "calendar-proxy-%s" % (permission,)
        subPrincipal = self.getChild(subPrincipalName)
        if subPrincipal is None:
            abort("No proxy subprincipal found for %s" % (self.resource,))

        namespace, name = davxml.dav_namespace, "group-member-set"
        prop = (yield subPrincipal.readProperty((namespace, name)))
        for child in prop.children:
            if str(child) == delegate.url():
                # delegate is already in the group
                break
        else:
            # delegate is not already in the group
            newChildren = list(prop.children)
            newChildren.append(davxml.HRef(delegate.url()))
            newProp = davxml.GroupMemberSet(*newChildren)
            result = (yield subPrincipal.writeProperty(newProp))
            returnValue(result)

    @inlineCallbacks
    def getDelegates(self, permission):

        subPrincipalName = "calendar-proxy-%s" % (permission,)
        subPrincipal = self.getChild(subPrincipalName)
        if subPrincipal is None:
            abort("No proxy subprincipal found for %s" % (self.resource,))

        namespace, name = davxml.dav_namespace, "group-member-set"
        prop = (yield subPrincipal.readProperty((namespace, name)))
        result = []
        for child in prop.children:
            result.append(str(child))
        returnValue(result)



    def url(self):
        return self.resource.url()

class FakeRequest(object):
    pass

def abort(msg, errno=1):
    print "ERROR:", msg
    print "Exiting"
    reactor.stop()
    sys.exit(errno)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

def setup():

    directory = getDirectory()
    if config.Memcached["ClientEnabled"]:
        memcachepool.installPool(
            IPv4Address(
                'TCP',
                config.Memcached["BindAddress"],
                config.Memcached["Port"]
            ),
            config.Memcached["MaxClients"]
        )
    if config.Notifications["Enabled"]:
        installNotificationClient(
            config.Notifications["InternalNotificationHost"],
            config.Notifications["InternalNotificationPort"],
        )
    principalCollection = directory.getPrincipalCollection()
    root = RootResource(
        config.DocumentRoot,
        principalCollections=(principalCollection,),
    )
    root.putChild("principals", principalCollection)
    calendarCollection = CalendarHomeProvisioningFile(
        os.path.join(config.DocumentRoot, "calendars"),
        directory, "/calendars/",
    )
    root.putChild("calendars", calendarCollection)

    return (directory, root)

def loadConfig(configFileName):
    if configFileName is None:
        configFileName = defaultConfigFile

    if not os.path.isfile(configFileName):
        sys.stderr.write("No config file: %s\n" % (configFileName,))
        sys.exit(1)

    config.loadConfig(configFileName)

    return config


def getDirectory():
    BaseDirectoryService = namedClass(config.DirectoryService["type"])

    class MyDirectoryService (BaseDirectoryService):
        def getPrincipalCollection(self):
            if not hasattr(self, "_principalCollection"):
                from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
                self._principalCollection = DirectoryPrincipalProvisioningResource("/principals/", self)

            return self._principalCollection

        def setPrincipalCollection(self, coll):
            # See principal.py line 237:  self.directory.principalCollection = self
            pass

        principalCollection = property(getPrincipalCollection, setPrincipalCollection)

        def calendarHomeForRecord(self, record):
            principal = self.principalCollection.principalForRecord(record)
            if principal:
                try:
                    return principal._calendarHome()
                except AttributeError:
                    pass
            return None

        def calendarHomeForShortName(self, recordType, shortName):
            principal = self.principalCollection.principalForShortName(recordType, shortName)
            if principal:
                try:
                    return principal._calendarHome()
                except AttributeError:
                    pass
            return None

        def principalForCalendarUserAddress(self, cua):
            return self.principalCollection.principalForCalendarUserAddress(cua)


    return MyDirectoryService(**config.DirectoryService["params"])

class DummyDirectoryService (DirectoryService):
    realmName = ""
    baseGUID = "51856FD4-5023-4890-94FE-4356C4AAC3E4"
    def recordTypes(self): return ()
    def listRecords(self): return ()
    def recordWithShortName(self): return None

dummyDirectoryRecord = DirectoryRecord(
    service = DummyDirectoryService(),
    recordType = "dummy",
    guid = "8EF0892F-7CB6-4B8E-B294-7C5A5321136A",
    shortNames = ("dummy",),
    fullName = "Dummy McDummerson",
    calendarUserAddresses = set(),
    autoSchedule = False,
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

if __name__ == "__main__":
    main()
