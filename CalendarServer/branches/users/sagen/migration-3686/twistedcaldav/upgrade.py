##
# Copyright (c) 2008 Apple Inc. All rights reserved.
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

from twisted.web2.dav.fileop import rmdir
from twistedcaldav.directory.directory import DirectoryService
from twistedcaldav.directory.calendaruserproxy import CalendarUserProxyDatabase
from twistedcaldav.log import Logger
from twistedcaldav.ical import Component
from twistedcaldav.scheduling.cuaddress import normalizeCUAddr
from calendarserver.tools.util import getDirectory, dummyDirectoryRecord
import xattr, itertools, os

log = Logger()



#
# upgrade_to_1
#
# Upconverts data from any calendar server version prior to data format 1
#

def upgrade_to_1(config):


    def fixBadQuotes(data):
        if (
            data.find('\\"') != -1 or
            data.find('\\\r\n "') != -1 or
            data.find('\r\n \r\n "') != -1
        ):
            # Fix by continuously replacing \" with " until no more
            # replacements occur
            while True:
                newData = data.replace('\\"', '"').replace('\\\r\n "', '\r\n "').replace('\r\n \r\n "', '\r\n "')
                if newData == data:
                    break
                else:
                    data = newData

            return data, True
        else:
            return data, False



    def normalizeCUAddrs(data, directory):
        cal = Component.fromString(data)

        def lookupFunction(cuaddr):
            try:
                principal = directory.principalForCalendarUserAddress(cuaddr)
            except Exception, e:
                log.debug("Lookup of %s failed: %s" % (cuaddr, e))
                principal = None

            if principal is None:
                return (None, None, None)
            else:
                return (principal.record.fullName, principal.record.guid,
                    principal.record.calendarUserAddresses)

        cal.normalizeCalendarUserAddresses(lookupFunction)

        newData = str(cal)
        return newData, not newData == data


    def upgradeCalendarCollection(oldCal, newCal, directory):
        os.mkdir(newCal)

        for resource in os.listdir(oldCal):

            if resource.startswith("."):
                continue

            oldRes = os.path.join(oldCal, resource)
            newRes = os.path.join(newCal, resource)

            log.info("Processing: %s" % (oldRes,))
            with open(oldRes) as res:
                data = res.read()

                try:
                    data, fixed = fixBadQuotes(data)
                    if fixed:
                        log.info("Fixing bad quotes in %s" % (oldRes,))
                except Exception, e:
                    log.error("Error while fixing bad quotes in %s: %s" %
                        (oldRes, e))
                    raise

                try:
                    data, fixed = normalizeCUAddrs(data, directory)
                    if fixed:
                        log.info("Normalized CUAddrs in %s" % (oldRes,))
                except Exception, e:
                    log.error("Error while normalizing %s: %s" %
                        (oldRes, e))
                    raise

            with open(newRes, "w") as res:
                res.write(data)

            for attr, value in xattr.xattr(oldRes).iteritems():
                xattr.setxattr(newRes, attr, value)

        for attr, value in xattr.xattr(oldCal).iteritems():
            xattr.setxattr(newCal, attr, value)


    def upgradeCalendarHome(oldHome, newHome, directory):
        try:
            os.makedirs(newHome)
            for attr, value in xattr.xattr(oldHome).iteritems():
                xattr.setxattr(newHome, attr, value)
        except:
            log.info("Skipping upgrade of %s because %s already exists" %
                (oldHome, newHome))
            return

        log.info("Upgrading calendar home: %s -> %s" % (oldHome, newHome))

        try:
            for cal in os.listdir(oldHome):
                oldCal = os.path.join(oldHome, cal)
                newCal = os.path.join(newHome, cal)
                log.info("Upgrading calendar: %s" % (newCal,))
                upgradeCalendarCollection(oldCal, newCal, directory)

            # The migration for this calendar home was successful, so now
            # we can remove the original
            for cal in os.listdir(oldHome):
                calPath = os.path.join(oldHome, cal)
                for child in os.listdir(calPath):
                    childPath = os.path.join(calPath, child)
                    os.remove(childPath)
                os.rmdir(calPath)
            os.rmdir(oldHome)

        except Exception, e:
            # A failure means that we are going to throw out everything we
            # did for this calendar home and leave the original intact
            log.error("Error while upgrading %s -> %s: %s.  Leaving original intact." % (oldHome, newHome, str(e)))
            for cal in os.listdir(newHome):
                calPath = os.path.join(newHome, cal)
                for child in os.listdir(calPath):
                    childPath = os.path.join(calPath, child)
                    os.remove(childPath)
                os.rmdir(calPath)
            os.rmdir(newHome)
            raise UpgradeError(
                "Upgrade Error: unable to migrate calendar home %s -> %s: %s" %
                (oldHome, newHome, str(e))
            )



    def doProxyDatabaseMoveUpgrade(config):

        # See if the old DB is present
        oldDbPath = os.path.join(config.DocumentRoot, "principals",
            CalendarUserProxyDatabase.dbOldFilename)
        if not os.path.exists(oldDbPath):
            # Nothing to be done
            return

        # See if the new one is already present
        newDbPath = os.path.join(config.DataRoot,
            CalendarUserProxyDatabase.dbFilename)
        if os.path.exists(newDbPath):
            # We have a problem - both the old and new ones exist. Stop the server
            # from starting up and alert the admin to this condition
            raise UpgradeError(
                "Upgrade Error: unable to move the old calendar user proxy database at '%s' to '%s' because the new database already exists."
                % (oldDbPath, newDbPath,)
            )

        # Now move the old one to the new location
        try:
            if not os.path.exists(config.DataRoot):
                os.makedirs(config.DataRoot)
            os.rename(oldDbPath, newDbPath)
        except Exception, e:
            raise UpgradeError(
                "Upgrade Error: unable to move the old calendar user proxy database at '%s' to '%s' due to %s."
                % (oldDbPath, newDbPath, str(e))
            )

        log.info(
            "Moved the calendar user proxy database from '%s' to '%s'."
            % (oldDbPath, newDbPath,)
        )




    directory = getDirectory()
    docRoot = config.DocumentRoot

    if os.path.exists(docRoot):

        # Look for the /principals/ directory on disk
        oldPrincipals = os.path.join(docRoot, "principals")
        if os.path.exists(oldPrincipals):
            # First move the proxy database and rename it
            doProxyDatabaseMoveUpgrade(config)

            # Now delete the on disk representation of principals
            rmdir(oldPrincipals)
            log.info(
                "Removed the old principal directory at '%s'."
                % (oldPrincipals,)
            )

        calRoot = os.path.join(docRoot, "calendars")
        if os.path.exists(calRoot):

            uidHomes = os.path.join(calRoot, "__uids__")

            if os.path.exists(uidHomes):
                for home in os.listdir(uidHomes):

                    # MOR: This assumes no UID is going to be 2 chars or less
                    if len(home) <= 2:
                        continue

                    oldHome = os.path.join(uidHomes, home)
                    newHome = os.path.join(uidHomes, home[0:2], home[2:4], home)
                    upgradeCalendarHome(oldHome, newHome, directory)

            else:
                os.mkdir(uidHomes)

            for recordType, dirName in (
                (DirectoryService.recordType_users, "users"),
                (DirectoryService.recordType_groups, "groups"),
                (DirectoryService.recordType_locations, "locations"),
                (DirectoryService.recordType_resources, "resources"),
            ):
                dirPath = os.path.join(calRoot, dirName)
                if os.path.exists(dirPath):
                    for shortName in os.listdir(dirPath):
                        record = directory.recordWithShortName(recordType,
                            shortName)
                        if record is not None:
                            uid = record.uid
                            oldHome = os.path.join(dirPath, shortName)
                            newHome = os.path.join(uidHomes, uid[0:2], uid[2:4],
                                uid)
                            upgradeCalendarHome(oldHome, newHome, directory)



# Each method in this array will upgrade from one version to the next;
# the index of each method within the array corresponds to the on-disk
# version number that it upgrades from.  For example, if the on-disk
# .version file contains a "3", but there are 6 methods in this array,
# methods 3 through 5 (using 0-based array indexing) will be executed in
# order.
upgradeMethods = [
    upgrade_to_1,
]

def upgradeData(config):

    # MOR: Temporary:
    # config.DocumentRoot = "/Users/morgen/Migration/CalendarServer/Documents"
    # config.DataRoot = "/Users/morgen/Migration/CalendarServer/Data"
    docRoot = config.DocumentRoot

    versionFilePath = os.path.join(docRoot, ".calendarserver_version")

    newestVersion = len(upgradeMethods)

    onDiskVersion = 0
    if os.path.exists(versionFilePath):
        try:
            with open(versionFilePath) as versionFile:
                onDiskVersion = int(versionFile.read().strip())
        except IOError, e:
            log.error("Cannot open %s; skipping migration" %
                (versionFilePath,))
        except ValueError, e:
            log.error("Invalid version number in %s; skipping migration" %
                (versionFilePath,))

    for upgradeVersion in range(onDiskVersion, newestVersion):
        log.info("Upgrading to version %d" % (upgradeVersion+1,))
        upgradeMethods[upgradeVersion](config)
        with open(versionFilePath, "w") as verFile:
            verFile.write(str(upgradeVersion+1))


class UpgradeError(RuntimeError):
    """
    Generic upgrade error.
    """
    pass
