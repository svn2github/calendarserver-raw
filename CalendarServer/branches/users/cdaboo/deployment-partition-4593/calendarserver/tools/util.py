##
# Copyright (c) 2008-2009 Apple Inc. All rights reserved.
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

__all__ = [
    "loadConfig",
    "getDirectory",
    "UsageError",
    "booleanArgument",
]

import os

from twisted.python.reflect import namedClass

import socket
from twistedcaldav.config import config, ConfigurationError
from twistedcaldav.directory import augment
from twistedcaldav.directory.directory import DirectoryService

def loadConfig(configFileName):
    if configFileName is None:
        configFileName = "/etc/caldav/caldavd.plist"

    if not os.path.isfile(configFileName):
        raise ConfigurationError("No config file: %s" % (configFileName,))

    config.loadConfig(configFileName)

    return config

def getDirectory():
    BaseDirectoryService = namedClass(config.DirectoryService.type)

    class MyDirectoryService (BaseDirectoryService):
        def getPrincipalCollection(self):
            if not hasattr(self, "_principalCollection"):
                #
                # Instantiating a CalendarHomeProvisioningResource with a directory
                # will register it with the directory (still smells like a hack).
                #
                # We need that in order to locate calendar homes via the directory.
                #
                from twistedcaldav.static import CalendarHomeProvisioningFile
                CalendarHomeProvisioningFile(os.path.join(config.DocumentRoot, "calendars"), self, "/calendars/")

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

        def principalForCalendarUserAddress(self, cua):
            return self.principalCollection.principalForCalendarUserAddress(cua)


    # Wait for directory service to become available
    directory = MyDirectoryService(**config.DirectoryService["params"])

    augmentClass = namedClass(config.AugmentService.type)
    augment.AugmentService = augmentClass(**config.AugmentService.params)

    return directory

class DummyDirectoryService (DirectoryService):
    realmName = ""
    baseGUID = "51856FD4-5023-4890-94FE-4356C4AAC3E4"
    def recordTypes(self): return ()
    def listRecords(self): return ()
    def recordWithShortName(self): return None

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

    if not config.Memcached.ClientEnabled:
        return

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        s.connect((config.Memcached.Pools.Default.BindAddress, config.Memcached.Pools.Default.Port))
        s.close()

    except socket.error:
        config.Memcached.ClientEnabled = False
