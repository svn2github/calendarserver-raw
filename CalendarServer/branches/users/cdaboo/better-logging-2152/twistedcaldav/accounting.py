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

from twistedcaldav.logger import logger

import itertools
import datetime
import os

"""
Classes and functions for user accounting.

Allows different sub-systems to log data on a per-user basis.

Right now only iTIP logging is supported.

See logger.py for more details.
"""

class Accounting(object):
    
    # Types of account data to store. The first item in the tuple is a name for the account file/directory,
    # the second item is True if its a directory, False if its a file.
    type_iTIP  = ("iTIP", True)

    _all_types = (type_iTIP, )

    @staticmethod
    def isEnabled():
        """
        Is accounting enabled.
        """
        return logger.accounting().get("Enabled", False)
    
    @staticmethod
    def isiTIPEnabled(principal):
        """
        Is iTIP accounting enabled.

        @param principal: the principal for whom a log entry is to be created.
        @type principal: L{DirectoryPrincipalResource}
        """
        return (
            Accounting.isEnabled() and
            logger.accounting().get("iTIP", False) and
            Accounting.isPrincipalEnabled(principal)
        )
    
    @staticmethod
    def isPrincipalEnabled(principal):
        """
        Is principal accounting enabled.

        @param principal: the principal for whom a log entry is to be created.
        @type principal: L{DirectoryPrincipalResource}
        """
        
        # None specified => all enabled
        enabled = logger.accounting().get("principals", ())
        if not enabled:
            return True
        
        for url in itertools.chain((principal.principalURL(),), principal.alternateURIs()):
            if url in enabled:
                return True
            
        return False
            
    @staticmethod
    def getLog(principal, accounting_type=None):
        """
        Get the log file/directory path.

        @param principal: the principal for whom a log entry is to be created.
        @type principal: L{DirectoryPrincipalResource}
        @param accounting_type: a tuple of log name and file/directory log type.
        @type accounting_type: C{tuple}
        
        @return: the file/directory path.
        @type: C{str}
        """

        assert (accounting_type in Accounting._all_types) or (accounting_type is None), "Supplied type not valid: %s" % (accounting_type,)
        
        # Path is the config value + record type + short name + type (if provided)
        log_path = logger.accounting().get("LogDirectory", "")
        
        record = principal.record
        
        log_path = os.path.join(log_path, record.recordType)
        log_path = os.path.join(log_path, record.shortName)

        # Make sure this path exists
        if not os.path.exists(log_path):
            os.makedirs(log_path)

        if type:
            type_name, type_isdirectory = accounting_type
            log_path = os.path.join(log_path, type_name)
            if not os.path.exists(log_path) and type_isdirectory:
                os.mkdir(log_path)

        return log_path

    @staticmethod
    def writeData(principal, accounting_type, data):
        """
        Write the supplied data to the appropriate location for the principal.
        
        @param principal: the principal for whom a log entry is to be created.
        @type principal: L{DirectoryPrincipalResource}
        @param accounting_type: a tuple of log name and file/directory log type.
        @type accounting_type: C{tuple}
        @param data: data to write.
        @type data: C{str}
        """
        
        assert accounting_type in Accounting._all_types, "Supplied type not valid: %s" % (accounting_type,)

        if not Accounting.isPrincipalEnabled(principal):
            return

        _ignore_type_name, type_isdirectory = accounting_type
        log_path = Accounting.getLog(principal, accounting_type)
        if type_isdirectory:
            # Generate a timestamp
            log_path = os.path.join(log_path, datetime.datetime.now().isoformat())
            if os.path.exists(log_path):
                for ctr in range(1, 100):
                    if not os.path.exists(log_path + "-%02d" % (ctr,)):
                        log_path += "-%02d" % (ctr,)
                        break
                    
        # Now write out the data to the file
        file = open(log_path, "a")
        file.write(data)
        file.close()
