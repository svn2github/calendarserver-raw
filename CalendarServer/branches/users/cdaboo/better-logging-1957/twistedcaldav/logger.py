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
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##

"""
Classes and functions to do better logging.
"""

from twisted.python import log

from twistedcaldav.config import config

from plistlib import readPlist
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

# Create the global instance of the logger
logger = Logger()
