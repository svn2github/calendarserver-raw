##
# Copyright (c) 2008-2013 Apple Inc. All rights reserved.
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

"""
Utility functionality shared between calendarserver tools.
"""

__all__ = [
    "loadConfig",
    "getDirectory",
    "dummyDirectoryRecord",
    "UsageError",
    "booleanArgument",
]

import os
from time import sleep
import socket
from pwd import getpwnam
from grp import getgrnam
from uuid import UUID

from twistedcaldav.config import config, ConfigurationError
from twistedcaldav.stdconfig import DEFAULT_CONFIG_FILE


from twisted.python.filepath import FilePath
from twisted.python.reflect import namedClass
from twext.python.log import Logger
from twisted.internet.defer import inlineCallbacks, returnValue

from txdav.xml import element as davxml

from calendarserver.provision.root import RootResource

from twistedcaldav import memcachepool
from twistedcaldav.directory import calendaruserproxy
from twistedcaldav.directory.aggregate import AggregateDirectoryService
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.directory import scheduleNextGroupCachingUpdate
from calendarserver.push.notifier import NotifierFactory

from txdav.common.datastore.file import CommonDataStore

log = Logger()

def loadConfig(configFileName):
    """
    Helper method for command-line utilities to load configuration plist
    and override certain values.
    """
    if configFileName is None:
        configFileName = DEFAULT_CONFIG_FILE

    if not os.path.isfile(configFileName):
        raise ConfigurationError("No config file: %s" % (configFileName,))

    config.load(configFileName)

    # Command-line utilities always want these enabled:
    config.EnableCalDAV = True
    config.EnableCardDAV = True

    return config

def getDirectory(config=config):

    class MyDirectoryService (AggregateDirectoryService):
        def getPrincipalCollection(self):
            if not hasattr(self, "_principalCollection"):

                if config.Notifications.Enabled:
                    # FIXME: NotifierFactory needs reference to the store in order
                    # to get a txn in order to create a Work item
                    notifierFactory = NotifierFactory(
                        None, config.ServerHostName,
                        config.Notifications.CoalesceSeconds,
                    )
                else:
                    notifierFactory = None

                # Need a data store
                _newStore = CommonDataStore(FilePath(config.DocumentRoot), 
                    notifierFactory, True, False)
                if notifierFactory is not None:
                    notifierFactory.store = _newStore

                #
                # Instantiating a DirectoryCalendarHomeProvisioningResource with a directory
                # will register it with the directory (still smells like a hack).
                #
                # We need that in order to locate calendar homes via the directory.
                #
                from twistedcaldav.directory.calendar import DirectoryCalendarHomeProvisioningResource
                DirectoryCalendarHomeProvisioningResource(self, "/calendars/", _newStore)

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
                    return principal.calendarHome()
                except AttributeError:
                    pass
            return None

        def calendarHomeForShortName(self, recordType, shortName):
            principal = self.principalCollection.principalForShortName(recordType, shortName)
            if principal:
                return principal.calendarHome()
            return None

        def principalForUID(self, uid):
            return self.principalCollection.principalForUID(uid)


    # Load augment/proxy db classes now
    if config.AugmentService.type:
        augmentClass = namedClass(config.AugmentService.type)
        augmentService = augmentClass(**config.AugmentService.params)
    else:
        augmentService = None

    proxydbClass = namedClass(config.ProxyDBService.type)
    calendaruserproxy.ProxyDBService = proxydbClass(**config.ProxyDBService.params)

    # Wait for directory service to become available
    BaseDirectoryService = namedClass(config.DirectoryService.type)
    config.DirectoryService.params.augmentService = augmentService
    directory = BaseDirectoryService(config.DirectoryService.params)
    while not directory.isAvailable():
        sleep(5)


    directories = [directory]

    if config.ResourceService.Enabled:
        resourceClass = namedClass(config.ResourceService.type)
        config.ResourceService.params.augmentService = augmentService
        resourceDirectory = resourceClass(config.ResourceService.params)
        resourceDirectory.realmName = directory.realmName
        directories.append(resourceDirectory)

    aggregate = MyDirectoryService(directories, None)
    aggregate.augmentService = augmentService

    #
    # Wire up the resource hierarchy
    #
    principalCollection = aggregate.getPrincipalCollection()
    root = RootResource(
        config.DocumentRoot,
        principalCollections=(principalCollection,),
    )
    root.putChild("principals", principalCollection)

    # Need a data store
    _newStore = CommonDataStore(FilePath(config.DocumentRoot), None, True, False)

    from twistedcaldav.directory.calendar import DirectoryCalendarHomeProvisioningResource
    calendarCollection = DirectoryCalendarHomeProvisioningResource(
        aggregate, "/calendars/",
        _newStore,
    )
    root.putChild("calendars", calendarCollection)

    return aggregate

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
    firstName = "Dummy",
    lastName = "McDummerson",
)

class UsageError (StandardError):
    pass

def booleanArgument(arg):
    if   arg in ("true",  "yes", "yup",  "uh-huh", "1", "t", "y"):
        return True
    elif arg in ("false", "no",  "nope", "nuh-uh", "0", "f", "n"):
        return False
    else:
        raise ValueError("Not a boolean: %s" % (arg,))

def autoDisableMemcached(config):
    """
    If memcached is not running, set config.Memcached.ClientEnabled to False
    """

    if not config.Memcached.Pools.Default.ClientEnabled:
        return

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        s.connect((config.Memcached.Pools.Default.BindAddress, config.Memcached.Pools.Default.Port))
        s.close()

    except socket.error:
        config.Memcached.Pools.Default.ClientEnabled = False


def setupMemcached(config):
    #
    # Connect to memcached
    #
    memcachepool.installPools(
        config.Memcached.Pools,
        config.Memcached.MaxClients
    )
    autoDisableMemcached(config)


def checkDirectory(dirpath, description, access=None, create=None, wait=False):
    """
    Make sure dirpath is an existing directory, and optionally ensure it has the
    expected permissions.  Alternatively the function can create the directory or
    can wait for someone else to create it.

    @param dirpath: The directory path we're checking
    @type dirpath: string
    @param description: A description of what the directory path represents, used in
        log messages
    @type description: string
    @param access: The type of access we're expecting, either os.W_OK or os.R_OK
    @param create: A tuple of (file permissions mode, username, groupname) to use
        when creating the directory.  If create=None then no attempt will be made
        to create the directory.
    @type create: tuple
    @param wait: Wether the function should wait in a loop for the directory to be
        created by someone else (or mounted, etc.)
    @type wait: boolean
    """
    if not os.path.exists(dirpath):

        if wait:
            while not os.path.exists(dirpath):
                log.error("Path does not exist: %s" % (dirpath,))
                sleep(1)
        else:
            try:
                mode, username, groupname = create
            except TypeError:
                raise ConfigurationError("%s does not exist: %s"
                                         % (description, dirpath))
            try:
                os.mkdir(dirpath)
            except (OSError, IOError), e:
                log.error("Could not create %s: %s" % (dirpath, e))
                raise ConfigurationError(
                    "%s does not exist and cannot be created: %s"
                    % (description, dirpath)
                )

            if username:
                uid = getpwnam(username).pw_uid
            else:
                uid = -1

            if groupname:
                gid = getgrnam(groupname).gr_gid
            else:
                gid = -1

            try:
                os.chmod(dirpath, mode)
                os.chown(dirpath, uid, gid)
            except (OSError, IOError), e:
                log.error("Unable to change mode/owner of %s: %s"
                               % (dirpath, e))

            log.info("Created directory: %s" % (dirpath,))

    if not os.path.isdir(dirpath):
        raise ConfigurationError("%s is not a directory: %s"
                                 % (description, dirpath))

    if access and not os.access(dirpath, access):
        raise ConfigurationError(
            "Insufficient permissions for server on %s directory: %s"
            % (description, dirpath)
        )



def principalForPrincipalID(principalID, checkOnly=False, directory=None):
    
    # Allow a directory parameter to be passed in, but default to config.directory
    # But config.directory isn't set right away, so only use it when we're doing more 
    # than checking.
    if not checkOnly and not directory:
        directory = config.directory

    if principalID.startswith("/"):
        segments = principalID.strip("/").split("/")
        if (len(segments) == 3 and
            segments[0] == "principals" and segments[1] == "__uids__"):
            uid = segments[2]
        else:
            raise ValueError("Can't resolve all paths yet")

        if checkOnly:
            return None

        return directory.principalCollection.principalForUID(uid)


    if principalID.startswith("("):
        try:
            i = principalID.index(")")

            if checkOnly:
                return None

            recordType = principalID[1:i]
            shortName = principalID[i+1:]

            if not recordType or not shortName or "(" in recordType:
                raise ValueError()

            return directory.principalCollection.principalForShortName(recordType, shortName)

        except ValueError:
            pass

    if ":" in principalID:
        if checkOnly:
            return None

        recordType, shortName = principalID.split(":", 1)

        return directory.principalCollection.principalForShortName(recordType, shortName)

    try:
        UUID(principalID)

        if checkOnly:
            return None

        x = directory.principalCollection.principalForUID(principalID)
        return x
    except ValueError:
        pass

    raise ValueError("Invalid principal identifier: %s" % (principalID,))

def proxySubprincipal(principal, proxyType):
    return principal.getChild("calendar-proxy-" + proxyType)

@inlineCallbacks
def action_addProxyPrincipal(rootResource, directory, store, principal, proxyType, proxyPrincipal):
    try:
        (yield addProxy(rootResource, directory, store, principal, proxyType, proxyPrincipal))
        print("Added %s as a %s proxy for %s" % (
            prettyPrincipal(proxyPrincipal), proxyType,
            prettyPrincipal(principal)))
    except ProxyError, e:
        print("Error:", e)
    except ProxyWarning, e:
        print(e)

@inlineCallbacks
def action_removeProxyPrincipal(rootResource, directory, store, principal, proxyPrincipal, **kwargs):
    try:
        removed = (yield removeProxy(rootResource, directory, store,
            principal, proxyPrincipal, **kwargs))
        if removed:
            print("Removed %s as a proxy for %s" % (
                prettyPrincipal(proxyPrincipal),
                prettyPrincipal(principal)))
    except ProxyError, e:
        print("Error:", e)
    except ProxyWarning, e:
        print(e)

@inlineCallbacks
def addProxy(rootResource, directory, store, principal, proxyType, proxyPrincipal):
    proxyURL = proxyPrincipal.url()

    subPrincipal = proxySubprincipal(principal, proxyType)
    if subPrincipal is None:
        raise ProxyError("Unable to edit %s proxies for %s\n" % (proxyType,
            prettyPrincipal(principal)))

    membersProperty = (yield subPrincipal.readProperty(davxml.GroupMemberSet, None))

    for memberURL in membersProperty.children:
        if str(memberURL) == proxyURL:
            raise ProxyWarning("%s is already a %s proxy for %s" % (
                prettyPrincipal(proxyPrincipal), proxyType,
                prettyPrincipal(principal)))

    else:
        memberURLs = list(membersProperty.children)
        memberURLs.append(davxml.HRef(proxyURL))
        membersProperty = davxml.GroupMemberSet(*memberURLs)
        (yield subPrincipal.writeProperty(membersProperty, None))

    proxyTypes = ["read", "write"]
    proxyTypes.remove(proxyType)

    (yield action_removeProxyPrincipal(rootResource, directory, store,
        principal, proxyPrincipal, proxyTypes=proxyTypes))

    yield scheduleNextGroupCachingUpdate(store, 0)

@inlineCallbacks
def removeProxy(rootResource, directory, store, principal, proxyPrincipal, **kwargs):
    removed = False
    proxyTypes = kwargs.get("proxyTypes", ("read", "write"))
    for proxyType in proxyTypes:
        proxyURL = proxyPrincipal.url()

        subPrincipal = proxySubprincipal(principal, proxyType)
        if subPrincipal is None:
            raise ProxyError("Unable to edit %s proxies for %s\n" % (proxyType,
                prettyPrincipal(principal)))

        membersProperty = (yield subPrincipal.readProperty(davxml.GroupMemberSet, None))

        memberURLs = [
            m for m in membersProperty.children
            if str(m) != proxyURL
        ]

        if len(memberURLs) == len(membersProperty.children):
            # No change
            continue
        else:
            removed = True

        membersProperty = davxml.GroupMemberSet(*memberURLs)
        (yield subPrincipal.writeProperty(membersProperty, None))

    if removed:
        yield scheduleNextGroupCachingUpdate(store, 0)
    returnValue(removed)



def prettyPrincipal(principal):
    record = principal.record
    return "\"%s\" (%s:%s)" % (record.fullName, record.recordType,
        record.shortNames[0])

class ProxyError(Exception):
    """
    Raised when proxy assignments cannot be performed
    """

class ProxyWarning(Exception):
    """
    Raised for harmless proxy assignment failures such as trying to add a
    duplicate or remove a non-existent assignment.
    """



