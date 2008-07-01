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

from twistedcaldav.config import config
from twisted.python.filepath import FilePath
from twisted.web2.dav.xattrprops import xattrPropertyStore
from twistedcaldav.sqlprops import sqlPropertyStore
from twistedcaldav.log import Logger
from twisted.web2.dav.fileop import rmdir
from twistedcaldav.directory.calendaruserproxy import CalendarUserProxyDatabase
import os

log = Logger()

class UpgradeTheServer(object):
    
    @staticmethod
    def doUpgrade():
        
        UpgradePrincipalCollectionInMemory.doUpgrade()
        UpgradeXattrsToSqlite.doUpgrade()

class UpgradePrincipalCollectionInMemory(object):

    @classmethod
    def doUpgrade(cls):
        
        # Look for the /principals/ directory on disk
        old_principals = os.path.join(config.DocumentRoot, "principals")
        if os.path.exists(old_principals):
            # First move the proxy database and rename it
            cls._doProxyDatabaseMoveUpgrade()
        
            # Now delete the on disk representation of principals
            rmdir(old_principals)
            log.info(
                "Removed the old principal directory at '%s'."
                % (old_principals,)
            )

    @classmethod
    def _doProxyDatabaseMoveUpgrade(cls):
        
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

class UpgradeXattrsToSqlite(object):

    class DummyResource:
        
        def __init__(self, path):
            self.fp = FilePath(path)
    
        def isCollection(self):
            return self.fp.isdir()

    @classmethod
    def doUpgrade(cls):
        """
        Upgrade xattr properties to sqlite properties.
        """
        
        if config.PropertyStore == "SQL":
            log.info("Doing xattr->sqlite property upgrade")
            docroot = config.DocumentRoot
            cls._upgradeItem(docroot, "")
    
    @classmethod
    def _upgradeItem(cls, docroot, path):
        
        # Upgrade the properties at this path first
        cls._upgradeXAttrs(docroot, path)
        
        fullpath = os.path.join(docroot, path)
        if os.path.isdir(fullpath):
            for child in os.listdir(fullpath):
                if child[0] == '.':
                    continue
                childpath = os.path.join(path, child)
                cls._upgradeItem(docroot, childpath)
    
    @classmethod
    def _upgradeXAttrs(cls, docroot, path):
        
        splits = path.split("/")
        if len(splits) <= 5:
            log.debug("Doing xattr->sqlite property upgrade for: %s" % (path,))

        resource = cls.DummyResource(os.path.join(docroot, path))
        xprops = xattrPropertyStore(resource)
        
        # Detect some special cases for sql properties
        root_resource = (len(path) == 0)
        avoid_contention = path.startswith("calendars/__uids__/") and len(splits) == 5
        sqlprops = sqlPropertyStore(resource, root_resource=root_resource, avoid_contention=avoid_contention)
        
        props = []
        for propname in list(xprops.list()):
            try:
                props.append(xprops.get(propname))
            except Exception, e:
                log.debug("Unable to upgrade properties '%s' on '%s' because of %s" % (propname, path, e,))
            xprops.delete(propname)

        if props:
            try:
                sqlprops._setSeveral(props)
            except Exception, e:
                log.debug("Unable to upgrade properties on '%s' because of %s" % (path, e,))


class UpgradeError(RuntimeError):
    """
    Generic upgrade error.
    """
