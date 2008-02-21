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

"""
Classes and functions to do better logging.

This logger class replaces the old logging.msg etc methods.

Logging now supports tagging by multiple 'id's. These can be strings, ints etc, or
class objects or __name__s.

Logging can be turned on/off on a per-id basis. i.e. its possible to log certain
sub-systems at 'debug' level whilst the rest are at 'error' level.

accounting.py creates a new per-user logging option. This will store a log of user activity
in separate files for each user. Currently can log all iTIP messages.

Logging and accounting options are all controlled through a .plist file.

kill -USR1 can be used to toggle between the normal logging level and 'debug' level in a
running server.

kill -URS2 can be used to force a re-read of the .plist options to allow changing
of logging behavior whilst the server continues to run.

"""

from twisted.python import log

from twistedcaldav.config import config

from plistlib import readPlist
import datetime
import itertools
import os
import types


class LoggerOptions(object):
    """
    Manages the set of options controlling logging.
    """
    
    def __init__(self):
        
        self.fname = None

        self.currentLogLevel = Logger.logtypes["error"]
        self.previousLogLevel = Logger.logtypes["debug"]
        
        self.systemLogLevels = {}
        
        self.accounting = {}
        
        self.displayClassNames = False
        
    def read(self, fname=None):
        
        if not fname:
            fname = config.LoggerOptionsFile
        
        if fname and os.path.exists(fname):
            options = readPlist(fname)
            
            level = options.get("Default Log Level", "error")
            if not Logger.logtypes.has_key(level):
                log.msg("Unknown default logger level '%s', using 'error' level instead" % (level,))
                level = "error"
            self.currentLogLevel = Logger.logtypes[level]
            
            systems = options.get("System Log Levels", {})
            newLogLevels = {}
            for key, value in systems.iteritems():
                if Logger.logtypes.has_key(value):
                    newLogLevels[key] = Logger.logtypes[value]
                else:
                    log.msg("Ignoring unknown logging level '%s' for system: %s" % (value, key,), system="Logger")
            
            self.systemLogLevels = newLogLevels
            
            self.accounting = options.get("Accounting", {"Enabled":False,})
            
            self.displayClassNames = options.get("Display Class Names", False,)

class Logger(object):
    #
    # Logging levels:
    #  0 - no logging
    #  1 - errors only
    #  2 - errors and warnings only
    #  3 - errors, warnings and info
    #  4 - errors, warnings, info and debug
    #
    
    logtypes = {"none": 0, "error": 1, "warning": 2, "info": 3, "debug": 4}
    
    def __init__(self):
        
        self.options = LoggerOptions()

    def readOptions(self):
        """
        Read or re-read the options file.
        """
    
        log.msg("Reading logger options file.", system="Logger")
        self.options.read()

    def toggle(self):
        """
        Toggle between normal mode and full debug mode.
        """
    
        tempLevel = self.options.currentLogLevel
        self.options.currentLogLevel = self.options.previousLogLevel
        self.options.previousLogLevel = tempLevel
        
        for key, value in Logger.logtypes.iteritems():
            if value == self.options.currentLogLevel:
                log.msg("Switching to default log level: %s" % (key,), system="Logger")
                break
        else:
            log.msg("Switching to default log level: %d" % (self.options.currentLogLevel,), system="Logger")

    def canLog(self, level, kwargs):
        """
        Determine whether a particular log level type is current active for the current system.
    
        @param level: a string with one of the supported levels.
        @param kwargs: a dict containing logging parameters. 
        @return:     True if the log level is currently active.
        """
        
        current_level = -1
        if kwargs.has_key("id"):
            id = kwargs["id"]
            if type(id) not in (types.TupleType, types.ListType,):
                id = (id,)
            items = []
            for item in id:
                if type(item) in (
                    types.StringType,
                    types.UnicodeType,
                    types.IntType,
                    types.LongType,
                    types.FloatType,
                    types.BooleanType,
                ):
                    item = str(item)
                elif getattr(item, "__name__", None):
                    item = item.__name__
                elif getattr(item, "__class__", None):
                    item = item.__class__.__name__
                else:
                    item = str(item)
                current_level = max(current_level, self.options.systemLogLevels.get(item, self.options.currentLogLevel))
                items.append(item)
            kwargs["system"] = ",".join(items)
        if current_level == -1:
            current_level = self.options.currentLogLevel
        return current_level >= Logger.logtypes.get(level, 1)
    
    def info(self, message, **kwargs):
        """
        Log a message at the "info" level.
    
        @param message:  message to log.
        @param **kwargs: additional log arguments.
        """
    
        if self.canLog("info", kwargs):
            log.msg(message, **kwargs)
    
    def warn(self, message, **kwargs):
        """
        Log a message at the "warning" level.
    
        @param message:  message to log.
        @param **kwargs: additional log arguments.
        """
    
        if self.canLog("warning", kwargs):
            log.msg(message, **kwargs)
    
    def err(self, message, **kwargs):
        """
        Log a message at the "error" level.
    
        @param message:  message to log.
        @param **kwargs: additional log arguments.
        """
    
        if self.canLog("error", kwargs):
            log.msg(message, **kwargs)
    
    def debug(self, message, **kwargs):
        """
        Log a message at the "debug" level.
    
        @param message:  message to log.
        @param **kwargs: additional log arguments.
        """
    
        if self.canLog("debug", kwargs):
            log.msg(message, debug=True, **kwargs)

    def accounting(self):
        return self.options.accounting

    class InstanceLogger(object):
        
        def __init__(self, classid, id):
            self.classid = classid
            self.id = id
            
        def info(self, message, **kwargs):
            self._mergeIds(kwargs)
            logger.info(message, **kwargs)

        def warn(self, message, **kwargs):
            self._mergeIds(kwargs)
            logger.warn(message, **kwargs)

        def err(self, message, **kwargs):
            self._mergeIds(kwargs)
            logger.err(message, **kwargs)

        def debug(self, message, **kwargs):
            self._mergeIds(kwargs)
            logger.debug(message, **kwargs)

        def _mergeIds(self, kwargs):
            id = kwargs.get("id", ())
            if type(id) not in (types.TupleType, types.ListType,):
                id = (id,)
            if logger.options.displayClassNames:
                kwargs["id"] = (self.classid,) + self.id + id
            else:
                kwargs["id"] = self.id + id

    @staticmethod
    def getInstance(classid, id=()):
        return Logger.InstanceLogger(classid, id)

# Create the global instance of the logger
logger = Logger()

"""
Allows different sub-systems to log data on a per-user basis.
Right now only iTIP logging is supported.
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
