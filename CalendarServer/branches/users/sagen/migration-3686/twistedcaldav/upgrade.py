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


def upgrade_to_1(config):
    log.info("Upgrading to 1")


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
        print cal
        for component in cal.subcomponents():
            if component.name() != "VTIMEZONE":
                for prop in itertools.chain(
                    component.properties("ORGANIZER"),
                    component.properties("ATTENDEE"),
                ):
                    cua = normalizeCUAddr(prop.value())
                    try:
                        principal = directory.principalForCalendarUserAddress(cua)
                    except Exception, e:
                        # lookup failed
                        log.debug("Lookup of %s failed: %s" % (cua, e))
                        principal = None

                    if principal is not None:
                        prop.setValue("urn:uuid:%s" % (principal.record.guid,))
                        if principal.record.fullName:
                            prop.params()["CN"] = [principal.record.fullName,]
                        else:
                            try:
                                del prop.params()["CN"]
                            except KeyError:
                                pass

                        # Re-write the X-CALENDARSERVER-EMAIL if its value no longer matches
                        oldEmail = prop.params().get("X-CALENDARSERVER-EMAIL", (None,))[0]
                        if oldEmail:
                            oldEmail = "mailto:%s" % (oldEmail,)

                        if oldEmail is None or oldEmail not in principal.record.calendarUserAddresses:
                            if cua.startswith("mailto:") and cua in principal.record.calendarUserAddresses:
                                email = cua[7:]
                            else:
                                for addr in principal.record.calendarUserAddresses:
                                    if addr.startswith("mailto:"):
                                        email = addr[7:]
                                        break
                                else:
                                    email = None

                            if email:
                                prop.params()["X-CALENDARSERVER-EMAIL"] = [email,]
                            else:
                                try:
                                    del prop.params()["X-CALENDARSERVER-EMAIL"]
                                except KeyError:
                                    pass

        newData = str(cal)
        return newData, not newData == data


    def upgradeCalendarCollection(oldCal, newCal, directory):
        os.rename(oldCal, newCal)

        for resource in os.listdir(newCal):
            resourcePath = os.path.join(newCal, resource)

            # MOR:  Is this the proper way to tell we have a caldav resource?
            # (".ics" extension-checking doesn't seem safe)
            xattrs = xattr.xattr(resourcePath)
            if not xattrs.has_key("WebDAV:{DAV:}getcontenttype"):
                continue

            log.info("Processing: %s" % (resourcePath,))
            needsRewrite = False
            with open(resourcePath) as res:
                data = res.read()

                data, fixed = fixBadQuotes(data)
                if fixed:
                    needsRewrite = True
                    log.info("Fixing bad quotes in %s" % (resourcePath,))

                data, fixed = normalizeCUAddrs(data, directory)
                if fixed:
                    needsRewrite = True
                    log.info("Normalized CUAddrs in %s" % (resourcePath,))
                    print "NORMALIZED TO:\n%s" % (data,)

            if needsRewrite:
                with open(resourcePath, "w") as res:
                    res.write(data)


    def upgradeCalendarHome(oldHome, newHome, directory):
        try:
            os.makedirs(newHome)
        except:
            log.info("Skipping upgrade of %s because %s already exists" %
                (oldHome, newHome))
            return

        log.info("Upgrading calendar home: %s -> %s" % (oldHome, newHome))

        for cal in os.listdir(oldHome):
            oldCal = os.path.join(oldHome, cal)

            # xattrs = xattr.xattr(oldCal)
            # if not xattrs.has_key("WebDAV:{http:%2F%2Fcalendarserver.org%2Fns%2F}getctag"):
            #     continue

            newCal = os.path.join(newHome, cal)
            log.info("Upgrading calendar: %s" % (newCal,))
            upgradeCalendarCollection(oldCal, newCal, directory)

        os.rmdir(oldHome)


    docRoot = config.DocumentRoot
    # MOR: Temporary:
    docRoot = "/Users/morgen/Migration/CalendarServer/Documents"

    directory = getDirectory()

    if os.path.exists(docRoot):

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



    
    # Don't forget to also do this:

    # UpgradeTheServer._doPrincipalCollectionInMemoryUpgrade(config)

class UpgradeTheServer(object):

    # Each method in this array will upgrade from one version to the next;
    # the index of each method within the array corresponds to the on-disk
    # version number that it upgrades from.  For example, if the on-disk
    # .version file contains a "3", but there are 6 methods in this array,
    # methods 3 through 5 (using 0-based array indexing) will be executed in
    # order.
    upgradeMethods = [
        upgradeLeopardToSnowLeopard,
    ]
    
    @staticmethod
    def doUpgrade(config):
        
        # import pdb; pdb.set_trace()

        docRoot = config.DocumentRoot
        # MOR: Temporary:
        docRoot = "/Users/morgen/Migration/CalendarServer/Documents"
        versionFilePath = os.path.join(docRoot, ".calendarserver_version")

        newestVersion = len(UpgradeTheServer.upgradeMethods)

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
            UpgradeTheServer.upgradeMethods[upgradeVersion](config)

    

    @staticmethod
    def _doPrincipalCollectionInMemoryUpgrade(config):
        
        # Look for the /principals/ directory on disk
        old_principals = os.path.join(config.DocumentRoot, "principals")
        if os.path.exists(old_principals):
            # First move the proxy database and rename it
            UpgradeTheServer._doProxyDatabaseMoveUpgrade(config)
        
            # Now delete the on disk representation of principals
            rmdir(old_principals)
            log.info(
                "Removed the old principal directory at '%s'."
                % (old_principals,)
            )

    @staticmethod
    def _doProxyDatabaseMoveUpgrade(config):
        
        # See if the old DB is present
        old_db_path = os.path.join(config.DocumentRoot, "principals", CalendarUserProxyDatabase.dbOldFilename)
        if not os.path.exists(old_db_path):
            # Nothing to be done
            return
        
        # See if the new one is already present
        new_db_path = os.path.join(config.DataRoot, CalendarUserProxyDatabase.dbFilename)
        if os.path.exists(new_db_path):
            # We have a problem - both the old and new ones exist. Stop the server from starting
            # up and alert the admin to this condition
            raise UpgradeError(
                "Upgrade Error: unable to move the old calendar user proxy database at '%s' to '%s' because the new database already exists."
                % (old_db_path, new_db_path,)
            )
        
        # Now move the old one to the new location
        try:
            os.rename(old_db_path, new_db_path)
        except Exception, e:
            raise UpgradeError(
                "Upgrade Error: unable to move the old calendar user proxy database at '%s' to '%s' due to %s."
                % (old_db_path, new_db_path, str(e))
            )
            
        log.info(
            "Moved the calendar user proxy database from '%s' to '%s'."
            % (old_db_path, new_db_path,)
        )

class UpgradeError(RuntimeError):
    """
    Generic upgrade error.
    """
    pass
