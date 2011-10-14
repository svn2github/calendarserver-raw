# -*- test-case-name: twistedcaldav.test.test_upgrade -*-
##
# Copyright (c) 2008-2010 Apple Inc. All rights reserved.
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

import xattr, os, zlib, hashlib, datetime, pwd, grp, shutil, errno
from zlib import compress
from cPickle import loads as unpickle, UnpicklingError

from twext.web2.dav.fileop import rmdir
from twext.web2.dav import davxml
from twext.python.log import Logger
from twisted.python.reflect import namedClass


from twistedcaldav.directory.appleopendirectory import OpenDirectoryService
from twistedcaldav.directory.xmlfile import XMLDirectoryService
from twistedcaldav.directory.directory import DirectoryService, GroupMembershipCacheUpdater
from twistedcaldav.directory import calendaruserproxy
from twistedcaldav.directory.calendaruserproxyloader import XMLCalendarUserProxyLoader
from twistedcaldav.directory.resourceinfo import ResourceInfoDatabase
from twistedcaldav.mail import MailGatewayTokensDatabase
from twistedcaldav.ical import Component
from twistedcaldav import caldavxml
from twistedcaldav.scheduling.cuaddress import LocalCalendarUser
from twistedcaldav.scheduling.scheduler import DirectScheduler


from twisted.application.service import Service
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, succeed, returnValue

from txdav.caldav.datastore.index_file import db_basename

from calendarserver.tap.util import getRootResource, FakeRequest, directoryFromConfig

from calendarserver.tools.util import getDirectory
from calendarserver.tools.resources import migrateResources

from twisted.python.reflect import namedAny

deadPropertyXattrPrefix = namedAny(
    "txdav.base.propertystore.xattr.PropertyStore.deadPropertyXattrPrefix"
)

INBOX_ITEMS = "inboxitems.txt"

log = Logger()

def xattrname(n):
    return deadPropertyXattrPrefix + n

def getCalendarServerIDs(config):

    # Determine uid/gid for ownership of directories we create here
    uid = -1
    if config.UserName:
        try:
            uid = pwd.getpwnam(config.UserName).pw_uid
        except KeyError:
            log.error("User not found: %s" % (config.UserName,))

    gid = -1
    if config.GroupName:
        try:
            gid = grp.getgrnam(config.GroupName).gr_gid
        except KeyError:
            log.error("Group not found: %s" % (config.GroupName,))

    return uid, gid

#
# upgrade_to_1
#
# Upconverts data from any calendar server version prior to data format 1
#

@inlineCallbacks
def upgrade_to_1(config):

    errorOccurred = False

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
                rec = principal.record

                # RFC5545 syntax does not allow backslash escaping in
                # parameter values. A double-quote is thus not allowed
                # in a parameter value except as the start/end delimiters.
                # Single quotes are allowed, so we convert any double-quotes
                # to single-quotes.
                fullName = rec.fullName.replace('"', "'")

                return (fullName, rec.guid, rec.calendarUserAddresses)

        cal.normalizeCalendarUserAddresses(lookupFunction)

        newData = str(cal)
        return newData, not newData == data


    def upgradeCalendarCollection(calPath, directory):

        errorOccurred = False
        collectionUpdated = False

        for resource in os.listdir(calPath):

            if resource.startswith("."):
                continue

            resPath = os.path.join(calPath, resource)

            if os.path.isdir(resPath):
                # Skip directories
                continue

            log.debug("Processing: %s" % (resPath,))
            needsRewrite = False
            with open(resPath) as res:
                data = res.read()

                try:
                    data, fixed = fixBadQuotes(data)
                    if fixed:
                        log.warn("Fixing bad quotes in %s" % (resPath,))
                        needsRewrite = True
                except Exception, e:
                    log.error("Error while fixing bad quotes in %s: %s" %
                        (resPath, e))
                    errorOccurred = True
                    continue

                try:
                    data, fixed = removeIllegalCharacters(data)
                    if fixed:
                        log.warn("Removing illegal characters in %s" % (resPath,))
                        needsRewrite = True
                except Exception, e:
                    log.error("Error while removing illegal characters in %s: %s" %
                        (resPath, e))
                    errorOccurred = True
                    continue

                try:
                    data, fixed = normalizeCUAddrs(data, directory)
                    if fixed:
                        log.debug("Normalized CUAddrs in %s" % (resPath,))
                        needsRewrite = True
                except Exception, e:
                    log.error("Error while normalizing %s: %s" %
                        (resPath, e))
                    errorOccurred = True
                    continue

            if needsRewrite:
                with open(resPath, "w") as res:
                    res.write(data)

                md5value = "<?xml version='1.0' encoding='UTF-8'?>\r\n<getcontentmd5 xmlns='http://twistedmatrix.com/xml_namespace/dav/'>%s</getcontentmd5>\r\n" % (hashlib.md5(data).hexdigest(),)
                md5value = zlib.compress(md5value)
                try:
                    xattr.setxattr(resPath, xattrname("{http:%2F%2Ftwistedmatrix.com%2Fxml_namespace%2Fdav%2F}getcontentmd5"), md5value)
                except IOError, ioe:
                    if ioe.errno == errno.EOPNOTSUPP:
                        # On non-native xattr systems we cannot do this,
                        # but those systems will typically not be migrating
                        # from pre-v1
                        pass
                except:
                    raise

                collectionUpdated = True


        if collectionUpdated:
            ctagValue = "<?xml version='1.0' encoding='UTF-8'?>\r\n<getctag xmlns='http://calendarserver.org/ns/'>%s</getctag>\r\n" % (str(datetime.datetime.now()),)
            ctagValue = zlib.compress(ctagValue)
            try:
                xattr.setxattr(calPath, xattrname("{http:%2F%2Fcalendarserver.org%2Fns%2F}getctag"), ctagValue)
            except IOError, ioe:
                if ioe.errno == errno.EOPNOTSUPP:
                    # On non-native xattr systems we cannot do this,
                    # but those systems will typically not be migrating
                    # from pre-v1
                    pass
            except:
                raise

        return errorOccurred


    def upgradeCalendarHome(homePath, directory):

        errorOccurred = False

        log.debug("Upgrading calendar home: %s" % (homePath,))

        try:
            for cal in os.listdir(homePath):
                calPath = os.path.join(homePath, cal)
                if not os.path.isdir(calPath):
                    # Skip non-directories; these might have been uploaded by a
                    # random DAV client, they can't be calendar collections.
                    continue
                if cal == 'notifications':
                    # Delete the old, now obsolete, notifications directory.
                    rmdir(calPath)
                    continue
                log.debug("Upgrading calendar: %s" % (calPath,))
                if not upgradeCalendarCollection(calPath, directory):
                    errorOccurred = True

                # Change the calendar-free-busy-set xattrs of the inbox to the
                # __uids__/<guid> form
                if cal == "inbox":
                    try:
                        for attr, value in xattr.xattr(calPath).iteritems():
                            if attr == xattrname("{urn:ietf:params:xml:ns:caldav}calendar-free-busy-set"):
                                value = updateFreeBusySet(value, directory)
                                if value is not None:
                                    # Need to write the xattr back to disk
                                    xattr.setxattr(calPath, attr, value)
                    except IOError, ioe:
                        if ioe.errno == errno.EOPNOTSUPP:
                            # On non-native xattr systems we cannot do this,
                            # but those systems will typically not be migrating
                            # from pre-v1
                            pass
                    except:
                        raise


        except Exception, e:
            log.error("Failed to upgrade calendar home %s: %s" % (homePath, e))
            raise

        return errorOccurred


    def doProxyDatabaseMoveUpgrade(config, uid=-1, gid=-1):
        # See if the new one is already present
        oldFilename = ".db.calendaruserproxy"
        newFilename = "proxies.sqlite"

        newDbPath = os.path.join(config.DataRoot, newFilename)
        if os.path.exists(newDbPath):
            # Nothing to be done, it's already in the new location
            return

        # See if the old DB is present
        oldDbPath = os.path.join(config.DocumentRoot, "principals", oldFilename)
        if not os.path.exists(oldDbPath):
            # Nothing to be moved
            return

        # Now move the old one to the new location
        try:
            if not os.path.exists(config.DataRoot):
                makeDirsUserGroup(config.DataRoot, uid=uid, gid=gid)
            try:
                os.rename(oldDbPath, newDbPath)
            except OSError:
                # Can't rename, must copy/delete
                shutil.copy2(oldDbPath, newDbPath)
                os.remove(oldDbPath)

        except Exception, e:
            raise UpgradeError(
                "Upgrade Error: unable to move the old calendar user proxy database at '%s' to '%s' due to %s."
                % (oldDbPath, newDbPath, str(e))
            )

        log.debug(
            "Moved the calendar user proxy database from '%s' to '%s'."
            % (oldDbPath, newDbPath,)
        )


    def moveCalendarHome(oldHome, newHome, uid=-1, gid=-1):
        if os.path.exists(newHome):
            # Both old and new homes exist; stop immediately to let the
            # administrator fix it
            raise UpgradeError(
                "Upgrade Error: calendar home is in two places: %s and %s.  Please remove one of them and restart calendar server."
                % (oldHome, newHome)
            )

        makeDirsUserGroup(os.path.dirname(newHome.rstrip("/")), uid=uid,
            gid=gid)
        os.rename(oldHome, newHome)


    @inlineCallbacks
    def migrateResourceInfo(config, directory, uid, gid):
        """
        Retrieve delegate assignments and auto-schedule flag from the directory
        service, because in "v1" that's where this info lived.
        """

        log.info("Fetching delegate assignments and auto-schedule settings from directory")
        resourceInfo = directory.getResourceInfo()
        if len(resourceInfo) == 0:
            # Nothing to migrate, or else not appleopendirectory
            return

        resourceInfoDatabase = ResourceInfoDatabase(config.DataRoot)
        proxydbClass = namedClass(config.ProxyDBService.type)
        calendarUserProxyDatabase = proxydbClass(**config.ProxyDBService.params)

        for guid, autoSchedule, proxy, readOnlyProxy in resourceInfo:
            resourceInfoDatabase.setAutoScheduleInDatabase(guid, autoSchedule)
            if proxy:
                yield calendarUserProxyDatabase.setGroupMembersInDatabase(
                    "%s#calendar-proxy-write" % (guid,),
                    [proxy]
                )
            if readOnlyProxy:
                yield calendarUserProxyDatabase.setGroupMembersInDatabase(
                    "%s#calendar-proxy-read" % (guid,),
                    [readOnlyProxy]
                )

        dbPath = os.path.join(config.DataRoot, ResourceInfoDatabase.dbFilename)
        if os.path.exists(dbPath):
            os.chown(dbPath, uid, gid)

        dbPath = os.path.join(config.DataRoot, "proxies.sqlite")
        if os.path.exists(dbPath):
            os.chown(dbPath, uid, gid)


    def createMailTokensDatabase(config, uid, gid):
        # Cause the tokens db to be created on disk so we can set the
        # permissions on it now
        MailGatewayTokensDatabase(config.DataRoot).lookupByToken("")

        dbPath = os.path.join(config.DataRoot, MailGatewayTokensDatabase.dbFilename)
        if os.path.exists(dbPath):
            os.chown(dbPath, uid, gid)

        journalPath = "%s-journal" % (dbPath,)
        if os.path.exists(journalPath):
            os.chown(journalPath, uid, gid)



    directory = getDirectory()

    docRoot = config.DocumentRoot

    uid, gid = getCalendarServerIDs(config)

    if not os.path.exists(config.DataRoot):
        makeDirsUserGroup(config.DataRoot, uid=uid, gid=gid)

    if os.path.exists(docRoot):

        # Look for the /principals/ directory on disk
        oldPrincipals = os.path.join(docRoot, "principals")
        if os.path.exists(oldPrincipals):
            # First move the proxy database and rename it
            doProxyDatabaseMoveUpgrade(config, uid=uid, gid=gid)

            # Now delete the on disk representation of principals
            rmdir(oldPrincipals)
            log.debug(
                "Removed the old principal directory at '%s'."
                % (oldPrincipals,)
            )

        calRoot = os.path.join(docRoot, "calendars")
        if os.path.exists(calRoot):

            uidHomes = os.path.join(calRoot, "__uids__")

            # Move calendar homes to new location:

            log.warn("Moving calendar homes to %s" % (uidHomes,))

            if os.path.exists(uidHomes):
                for home in os.listdir(uidHomes):

                    # MOR: This assumes no UID is going to be 2 chars or less
                    if len(home) <= 2:
                        continue

                    oldHome = os.path.join(uidHomes, home)
                    if not os.path.isdir(oldHome):
                        # Skip non-directories
                        continue

                    newHome = os.path.join(uidHomes, home[0:2], home[2:4], home)
                    moveCalendarHome(oldHome, newHome, uid=uid, gid=gid)

            else:
                os.mkdir(uidHomes)
                os.chown(uidHomes, uid, gid)

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
                        oldHome = os.path.join(dirPath, shortName)
                        if record is not None:
                            newHome = os.path.join(uidHomes, record.uid[0:2],
                                record.uid[2:4], record.uid)
                            moveCalendarHome(oldHome, newHome, uid=uid, gid=gid)
                        else:
                            # an orphaned calendar home (principal no longer
                            # exists in the directory)
                            archive(config, oldHome, uid, gid)

                    os.rmdir(dirPath)


            # Count how many calendar homes we'll be processing, and build
            # list of pending inbox items
            total = 0
            inboxItems = set()
            for first in os.listdir(uidHomes):
                if len(first) == 2:
                    firstPath = os.path.join(uidHomes, first)
                    for second in os.listdir(firstPath):
                        if len(second) == 2:
                            secondPath = os.path.join(firstPath, second)
                            for home in os.listdir(secondPath):
                                homePath = os.path.join(secondPath, home)
                                if not os.path.isdir(homePath):
                                    # Skip non-directories
                                    continue
                                total += 1
                                inboxPath = os.path.join(homePath, "inbox")
                                if os.path.exists(inboxPath):
                                    for inboxItem in os.listdir(inboxPath):
                                        if not inboxItem.startswith("."):
                                            inboxItems.add(os.path.join(inboxPath, inboxItem))

            if inboxItems:
                inboxItemsFile = os.path.join(config.DataRoot, INBOX_ITEMS)
                with open(inboxItemsFile, "w") as out:
                    for item in inboxItems:
                        out.write("%s\n" % (item))
                os.chown(inboxItemsFile, uid, gid)

            if total:
                log.warn("Processing %d calendar homes in %s" % (total, uidHomes))

                # Upgrade calendar homes in the new location:
                count = 0
                for first in os.listdir(uidHomes):
                    if len(first) == 2:
                        firstPath = os.path.join(uidHomes, first)
                        for second in os.listdir(firstPath):
                            if len(second) == 2:
                                secondPath = os.path.join(firstPath, second)
                                for home in os.listdir(secondPath):
                                    homePath = os.path.join(secondPath, home)

                                    if not os.path.isdir(homePath):
                                        # Skip non-directories
                                        continue

                                    if not upgradeCalendarHome(homePath,
                                        directory):
                                        errorOccurred = True

                                    count += 1
                                    if count % 10 == 0:
                                        log.warn("Processed calendar home %d of %d"
                                            % (count, total))

                log.warn("Done processing calendar homes")

    yield migrateResourceInfo(config, directory, uid, gid)
    createMailTokensDatabase(config, uid, gid)

    if errorOccurred:
        raise UpgradeError("Data upgrade failed, see error.log for details")


@inlineCallbacks
def upgrade_to_2(config):
    
    errorOccurred = False

    def renameProxyDB():
        #
        # Rename proxy DB
        #
        oldFilename = "calendaruserproxy.sqlite"
        newFilename = "proxies.sqlite"
    
        oldDbPath = os.path.join(config.DataRoot, oldFilename)
        newDbPath = os.path.join(config.DataRoot, newFilename)
        if os.path.exists(oldDbPath) and not os.path.exists(newDbPath):
            os.rename(oldDbPath, newDbPath)

    def migrateFromOD():
        #
        # Migrates locations and resources from OD
        #
        triggerFile = "trigger_resource_migration"
        triggerPath = os.path.join(config.ServerRoot, triggerFile)
        if os.path.exists(triggerPath):
            os.remove(triggerPath)
    
            log.info("Migrating locations and resources")
    
            directory = getDirectory()
            userService = directory.serviceForRecordType("users")
            resourceService = directory.serviceForRecordType("resources")
            if (
                not isinstance(userService, OpenDirectoryService) or
                not isinstance(resourceService, XMLDirectoryService)
            ):
                # Configuration requires no migration
                return succeed(None)
    
            # Fetch the autoSchedule assignments from resourceinfo.sqlite and pass
            # those to migrateResources
            autoSchedules = {}
            dbPath = os.path.join(config.DataRoot, ResourceInfoDatabase.dbFilename)
            if os.path.exists(dbPath):
                resourceInfoDatabase = ResourceInfoDatabase(config.DataRoot)
                results = resourceInfoDatabase._db_execute(
                    "select GUID, AUTOSCHEDULE from RESOURCEINFO"
                )
                for guid, autoSchedule in results:
                    autoSchedules[guid] = autoSchedule
    
            # Create internal copies of resources and locations based on what is
            # found in OD, overriding the autoSchedule default with existing
            # assignments from resourceinfo.sqlite
            return migrateResources(userService, resourceService,
                autoSchedules=autoSchedules)

    def flattenHome(calHome):

        log.debug("Flattening calendar home: %s" % (calHome,))

        try:
            for cal in os.listdir(calHome):
                calPath = os.path.join(calHome, cal)
                if not os.path.isdir(calPath):
                    # Skip non-directories; these might have been uploaded by a
                    # random DAV client, they can't be calendar collections.
                    continue
                
                if cal in ("dropbox",):
                    continue
                if os.path.exists(os.path.join(calPath, db_basename)):
                    continue
                
                # Commented this out because it is only needed if there are calendars nested inside of regular collections.
                # Whilst this is technically possible in early versions of the servers the main clients did not support it.
                # Also, the v1 upgrade does not look at nested calendars for cu-address normalization.
                # However, we do still need to "ignore" regular collections in the calendar home so what we do is rename them
                # with a ".collection." prefix.
#                def scanCollection(collection):
#                    
#                    for child in os.listdir(collection):
#                        childCollection = os.path.join(collection, child)
#                        if os.path.isdir(childCollection):
#                            if os.path.exists(os.path.join(childCollection, db_basename)):
#                                newPath = os.path.join(calHome, child)
#                                if os.path.exists(newPath):
#                                    newPath = os.path.join(calHome, str(uuid.uuid4()))
#                                log.debug("Moving up calendar: %s" % (childCollection,))
#                                os.rename(childCollection, newPath)
#                            else:
#                                scanCollection(childCollection)

                if os.path.isdir(calPath):
                    #log.debug("Regular collection scan: %s" % (calPath,))
                    #scanCollection(calPath)
                    log.warn("Regular collection hidden: %s" % (calPath,))
                    os.rename(calPath, os.path.join(calHome, ".collection." + os.path.basename(calPath)))

        except Exception, e:
            log.error("Failed to upgrade calendar home %s: %s" % (calHome, e))
            return False
        
        return True

    def flattenHomes():
        """
        Make sure calendars inside regular collections are all moved to the top level.
        """
        errorOccurred = False
    
        log.debug("Flattening calendar homes")

        docRoot = config.DocumentRoot
        if os.path.exists(docRoot):
            calRoot = os.path.join(docRoot, "calendars")
            if os.path.exists(calRoot) and os.path.isdir(calRoot):
                uidHomes = os.path.join(calRoot, "__uids__")
                if os.path.isdir(uidHomes): 
                    for path1 in os.listdir(uidHomes):
                        uidLevel1 = os.path.join(uidHomes, path1)
                        if not os.path.isdir(uidLevel1):
                            continue
                        for path2 in os.listdir(uidLevel1):
                            uidLevel2 = os.path.join(uidLevel1, path2)
                            if not os.path.isdir(uidLevel2):
                                continue
                            for home in os.listdir(uidLevel2):
                                calHome = os.path.join(uidLevel2, home)
                                if not os.path.isdir(calHome):
                                    continue
                                if not flattenHome(calHome):
                                    errorOccurred = True
        
        return errorOccurred
        
    renameProxyDB()
    errorOccurred = flattenHomes()
    try:
        yield migrateFromOD()
    except:
        errorOccurred = True
        
    if errorOccurred:
        raise UpgradeError("Data upgrade failed, see error.log for details")
    

# The on-disk version number (which defaults to zero if .calendarserver_version
# doesn't exist), is compared with each of the numbers in the upgradeMethods
# array.  If it is less than the number, the associated method is called.

upgradeMethods = [
    (1, upgrade_to_1),
    (2, upgrade_to_2),
]

@inlineCallbacks
def upgradeData(config):

    docRoot = config.DocumentRoot

    versionFilePath = os.path.join(docRoot, ".calendarserver_version")

    onDiskVersion = 0
    if os.path.exists(versionFilePath):
        try:
            with open(versionFilePath) as versionFile:
                onDiskVersion = int(versionFile.read().strip())
        except IOError:
            log.error("Cannot open %s; skipping migration" %
                (versionFilePath,))
        except ValueError:
            log.error("Invalid version number in %s; skipping migration" %
                (versionFilePath,))

    uid, gid = getCalendarServerIDs(config)

    for version, method in upgradeMethods:
        if onDiskVersion < version:
            log.warn("Upgrading to version %d" % (version,))
            (yield method(config))
            log.warn("Upgraded to version %d" % (version,))
            with open(versionFilePath, "w") as verFile:
                verFile.write(str(version))
            os.chown(versionFilePath, uid, gid)

class UpgradeError(RuntimeError):
    """
    Generic upgrade error.
    """
    pass


#
# Utility functions
#
def updateFreeBusyHref(href, directory):
    pieces = href.split("/")
    if pieces[2] == "__uids__":
        # Already updated
        return None

    recordType = pieces[2]
    shortName = pieces[3]
    record = directory.recordWithShortName(recordType, shortName)
    if record is None:
        # We will simply ignore this and not write out an fb-set entry
        log.error("Can't update free-busy href; %s is not in the directory" % shortName)
        return ""

    uid = record.uid
    newHref = "/calendars/__uids__/%s/%s/" % (uid, pieces[4])
    return newHref


def updateFreeBusySet(value, directory):

    try:
        value = zlib.decompress(value)
    except zlib.error:
        # Legacy data - not zlib compressed
        pass

    try:
        doc = davxml.WebDAVDocument.fromString(value)
        freeBusySet = doc.root_element
    except ValueError:
        try:
            freeBusySet = unpickle(value)
        except UnpicklingError:
            log.err("Invalid free/busy property value")
            # MOR: continue on?
            return None

    fbset = set()
    didUpdate = False
    for href in freeBusySet.children:
        href = str(href)
        newHref = updateFreeBusyHref(href, directory)
        if newHref is None:
            fbset.add(href)
        else:
            didUpdate = True
            if newHref != "":
                fbset.add(newHref)

    if didUpdate:
        property = caldavxml.CalendarFreeBusySet(*[davxml.HRef(href)
            for href in fbset])
        value = compress(property.toxml())
        return value

    return None # no update required


def makeDirsUserGroup(path, uid=-1, gid=-1):
    parts = path.split("/")
    if parts[0] == "": # absolute path
        parts[0] = "/"

    path = ""
    for part in parts:
        if not part:
            continue
        path = os.path.join(path, part)
        if not os.path.exists(path):
            os.mkdir(path)
            os.chown(path, uid, gid)


def archive(config, srcPath, uid, gid):
    """
    Move srcPath into dataroot/archived, giving the destination a unique
    (sequentially numbered) name in the case of duplicates.
    """

    archiveDir = os.path.join(config.DataRoot, "archived")

    if not os.path.exists(archiveDir):
        os.mkdir(archiveDir)
    os.chown(archiveDir, uid, gid)

    baseName = os.path.basename(srcPath)
    newName = baseName
    count = 0
    destPath = os.path.join(archiveDir, newName)
    while os.path.exists(destPath):
        count += 1
        newName = "%s.%d" % (baseName, count)
        destPath = os.path.join(archiveDir, newName)

    try:
        os.rename(srcPath, destPath)
    except OSError:
        # Can't rename, must copy/delete
        shutil.copy2(srcPath, destPath)
        os.remove(srcPath)


DELETECHARS = ''.join(chr(i) for i in xrange(32) if i not in (10, 13))
def removeIllegalCharacters(data):
    """
    Remove all characters below ASCII 32 except NL and CR

    Return tuple with the processed data, and a boolean indicating wether
    the data changed.
    """
    beforeLen = len(data)
    data =  data.translate(None, DELETECHARS)
    afterLen = len(data)
    if afterLen != beforeLen:
        return data, True
    else:
        return data, False


class UpgradeFileSystemFormatService(Service, object):
    """
    Upgrade filesystem from previous versions.
    """

    def __init__(self, config, service):
        """
        Initialize the service.
        """
        self.wrappedService = service
        self.config = config


    @inlineCallbacks
    def doUpgrade(self):
        """
        Do the upgrade.  Called by C{startService}, but a different method
        because C{startService} should return C{None}, not a L{Deferred}.

        @return: a Deferred which fires when the upgrade is complete.
        """
        yield upgradeData(self.config)

        # see http://twistedmatrix.com/trac/ticket/4649
        reactor.callLater(0, self.wrappedService.setServiceParent, self.parent)


    def startService(self):
        """
        Start the service.
        """
        self.doUpgrade()



class PostDBImportService(Service, object):
    """
    Service which runs after database import but before workers are spawned
    (except memcached will be running at this point)

    The jobs carried out here are:

        1. Populating the group-membership cache
        2. Processing non-implicit inbox items
    """

    def __init__(self, config, store, service):
        """
        Initialize the service.
        """
        self.wrappedService = service
        self.store = store
        self.config = config

    @inlineCallbacks
    def startService(self):
        """
        Start the service.
        """

        # Load proxy assignments from XML if specified
        if self.config.ProxyLoadFromFile:
            proxydbClass = namedClass(self.config.ProxyDBService.type)
            calendaruserproxy.ProxyDBService = proxydbClass(
                **self.config.ProxyDBService.params)
            loader = XMLCalendarUserProxyLoader(self.config.ProxyLoadFromFile)
            yield loader.updateProxyDB()

        # Populate the group membership cache
        if (self.config.GroupCaching.Enabled and
            self.config.GroupCaching.EnableUpdater):
            proxydb = calendaruserproxy.ProxyDBService
            if proxydb is None:
                proxydbClass = namedClass(self.config.ProxyDBService.type)
                proxydb = proxydbClass(**self.config.ProxyDBService.params)
            directory = directoryFromConfig(self.config)

            updater = GroupMembershipCacheUpdater(proxydb,
                directory, self.config.GroupCaching.ExpireSeconds,
                self.config.GroupCaching.LockSeconds,
                namespace=self.config.GroupCaching.MemcachedPool,
                useExternalProxies=self.config.GroupCaching.UseExternalProxies)
            yield updater.updateCache(fast=True)

            uid, gid = getCalendarServerIDs(self.config)
            dbPath = os.path.join(self.config.DataRoot, "proxies.sqlite")
            if os.path.exists(dbPath):
                os.chown(dbPath, uid, gid)

        # Process old inbox items
        yield self.processInboxItems()


    @inlineCallbacks
    def processInboxItems(self):
        """
        When data is migrated from a non-implicit scheduling server there can
        be inbox items that clients have not yet processed.  This method
        runs those inbox items through the implicit scheduling mechanism.
        """

        inboxItemsList = os.path.join(self.config.DataRoot, INBOX_ITEMS)
        if os.path.exists(inboxItemsList):

            root = getRootResource(self.config, self.store)
            directory = root.getDirectory()
            principalCollection = directory.principalCollection

            inboxItems = set()
            with open(inboxItemsList) as input:
                for inboxItem in input:
                    inboxItem = inboxItem.strip()
                    inboxItems.add(inboxItem)

            try:
                for inboxItem in list(inboxItems):
                    log.info("Processing inbox item: %s" % (inboxItem,))
                    ignore, uuid, ignore, fileName = inboxItem.rsplit("/", 3)

                    record = directory.recordWithUID(uuid)
                    if not record:
                        continue

                    principal = principalCollection.principalForRecord(record)
                    if not principal:
                        continue

                    request = FakeRequest(root, "PUT", None)
                    request.checkedSACL = True
                    request.authnUser = request.authzUser = davxml.Principal(
                        davxml.HRef.fromString("/principals/__uids__/%s/" % (uuid,))
                    )

                    calendarHome = yield principal.calendarHome(request)
                    if not calendarHome:
                        continue

                    inbox = yield calendarHome.getChild("inbox")
                    if inbox and inbox.exists():

                        inboxItemResource = yield inbox.getChild(fileName)
                        if inboxItemResource and inboxItemResource.exists():

                            uri = "/calendars/__uids__/%s/inbox/%s" % (uuid,
                                fileName)
                            request.path = uri
                            request._rememberResource(inboxItemResource, uri)

                            try:
                                yield self.processInboxItem(
                                    root,
                                    directory,
                                    principal,
                                    request,
                                    inbox,
                                    inboxItemResource,
                                    uuid,
                                    uri
                                )
                            except Exception, e:
                                log.error("Error processing inbox item: %s (%s)"
                                    % (inboxItem, e))

                    inboxItems.remove(inboxItem)


            finally:
                # Rewrite the inbox items file in case we exit before we're
                # done so we'll pick up where we left off next time we start up.
                if inboxItems:
                    with open(inboxItemsList + ".tmp", "w") as output:
                        for inboxItem in inboxItems:
                            output.write("%s\n" % (inboxItem,))
                    os.rename(inboxItemsList + ".tmp", inboxItemsList)
                    log.error("Restart calendar service to reattempt processing")
                else:
                    os.remove(inboxItemsList)

        reactor.callLater(0, self.wrappedService.setServiceParent, self.parent)


    @inlineCallbacks
    def processInboxItem(self, root, directory, principal, request, inbox,
        inboxItem, uuid, uri):
        """
        Run an individual inbox item through implicit scheduling and remove
        the inbox item.
        """

        log.debug("Processing inbox item %s" % (inboxItem,))

        ownerPrincipal = principal
        cua = "urn:uuid:%s" % (uuid,)
        owner = LocalCalendarUser(cua, ownerPrincipal,
            inbox, ownerPrincipal.scheduleInboxURL())

        calendar = yield inboxItem.iCalendar()
        if calendar.mainType() is not None:
            try:
                method = calendar.propertyValue("METHOD")
            except ValueError:
                returnValue(None)

            if method == "REPLY":
                # originator is attendee sending reply
                originator = calendar.getAttendees()[0]
            else:
                # originator is the organizer
                originator = calendar.getOrganizer()

            principalCollection = directory.principalCollection
            originatorPrincipal = principalCollection.principalForCalendarUserAddress(originator)
            originator = LocalCalendarUser(originator, originatorPrincipal)
            recipients = (owner,)

            scheduler = DirectScheduler(request, inboxItem)
            # Process inbox item
            yield scheduler.doSchedulingViaPUT(originator, recipients, calendar,
                internal_request=False)
        else:
            log.warn("Removing invalid inbox item: %s" % (uri,))

        #
        # Remove item
        #
        txn = request._newStoreTransaction
        txn._notifierFactory = None # Do not send push notifications
        yield inboxItem.storeRemove(request, True, uri)
        yield txn.commit()

