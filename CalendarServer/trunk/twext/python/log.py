##
# Copyright (c) 2006-2013 Apple Inc. All rights reserved.
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
from __future__ import print_function

"""
Classes and functions to do granular logging.

Example usage in a module:

    from twext.python.log import Logger
    log = Logger()

    log.info("Blah blah")

Or in a class:

    from twext.python.log import LoggingMixIn

    class Foo (LoggingMixIn):
        def oops(self):
            self.log_error("Oops!")

C{Logger}s have namespaces, for which logging can be configured
independently.  Namespaces may be specified by passing in a
C{namespace} argument to L{Logger} when instantiating it, but if none
is given, the logger will derive its own namespace by using the module
name of the callable that instantiating it, or, in the case of a
L{LoggingMixIn}, by using the fully qualified name of the class.

In the first example above, the namespace would be C{some.module}, and
in the second example, it would be C{some.module.Foo}.
"""

__all__ = [
    "logLevels",
    "cmpLogLevels",
    "pythonLogLevelForLevel",
    "logLevelForNamespace",
    "setLogLevelForNamespace",
    "clearLogLevels",
    "logLevelsByNamespace",
    "Logger",
    "LoggingMixIn",
    "InvalidLogLevelError",
    "StandardIOObserver",
]

from sys import stdout, stderr

import inspect
import logging

from twisted.python.reflect import safe_str
from twisted.python.log import msg as twistedLogMessage
from twisted.python.log import addObserver, removeObserver

logLevels = (
    "debug",
    "info",
    "warn",
    "error",
)

logLevelIndexes = dict(zip(logLevels, xrange(0, len(logLevels))))

def cmpLogLevels(a, b):
    """
    Compare two log levels.
    @param a: a log level
    @param b: a log level
    @return: a negative integer if C{a < b}, C{0} if C{a == b}, or a
        positive integer if C{a > b}.
    """
    return cmp(logLevelIndexes[a], logLevelIndexes[b])

##
# Mappings to Python's logging module
##

pythonLogLevelMapping = {
    "debug"   : logging.DEBUG,
    "info"    : logging.INFO,
    "warn"    : logging.WARNING,
    "error"   : logging.ERROR,
   #"critical": logging.CRITICAL,
}

def pythonLogLevelForLevel(level):
    """
    @param: a log level
    @return: a L{logging} module log level
    """
    if level in pythonLogLevelMapping:
        return pythonLogLevelMapping[level]

    raise InvalidLogLevelError(level)

#    #
#    # In case we add log levels that don't map to pythong logging levels:
#    #
#    for l in logLevels:
#        print("Trying %s: %s, %s" % (l, l in pythonLogLevelMapping, cmpLogLevels(level, l) <= 0))
#        if l in pythonLogLevelMapping and cmpLogLevels(level, l) <= 0:
#            return pythonLogLevelMapping[l]
#
#    return logging.CRITICAL

##
# Tools for managing log levels
##

def logLevelForNamespace(namespace):
    """
    @param namespace: a logging namespace, or C{None} to set the
        default log level.
    @return: the log level for the given namespace.
    """
    if not namespace:
        return logLevelsByNamespace[None]

    if namespace in logLevelsByNamespace:
        return logLevelsByNamespace[namespace]

    segments = namespace.split(".")
    index = len(segments) - 1

    while index > 0:
        namespace = ".".join(segments[:index])
        if namespace in logLevelsByNamespace:
            return logLevelsByNamespace[namespace]
        index -= 1

    return logLevelsByNamespace[None]

def setLogLevelForNamespace(namespace, level):
    """
    Sets the log level for a logging namespace.
    @param namespace: a logging namespace
    @param level: the log level for the given namespace.
    """
    if level not in logLevels:
        raise InvalidLogLevelError(level)

    if namespace:
        logLevelsByNamespace[namespace] = level
    else:
        logLevelsByNamespace[None] = level

def clearLogLevels():
    """
    Clears all log levels to the default.
    """
    logLevelsByNamespace.clear()
    logLevelsByNamespace[None] = "warn"  # Default log level

logLevelsByNamespace = {}
clearLogLevels()

##
# Loggers
##

class Logger (object):
    """
    Logging object.
    """
    def __init__(self, namespace=None):
        """
        @param namespace: The namespace for this logger.  Uses a
            dotted notation, as used by python modules.  If not
            C{None}, then the name of the module of the caller
            is used.
        """
        if namespace is None:
            currentFrame = inspect.currentframe()
            callerFrame  = currentFrame.f_back
            callerModule = callerFrame.f_globals["__name__"]

            namespace = callerModule

        self.namespace = namespace

    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.namespace)

    def emit(self, level, message, **kwargs):
        """
        Called internally to emit log messages at a given log level.
        """
        assert level in logLevels, "Unknown log level: %r" % (level,)

        logLevel = pythonLogLevelForLevel(level)

        # FIXME: Filtering should be done by the log observer(s)
        if not self.willLogAtLevel(level):
            return

        kwargs["level"] = level
        kwargs["logLevel"] = logLevel
        kwargs["namespace"] = self.namespace

        if message:
            kwargs["legacyMessage"] = message
            kwargs["format"] = "%(legacyMessage)s"

        if "format" in kwargs:
            prefix = "[%(namespace)s#%(level)s] "
            kwargs["format"] = "%s%s" % (prefix, kwargs["format"])

        twistedLogMessage(**kwargs)

    def level(self):
        """
        @return: the logging level for this logger's namespace.
        """
        return logLevelForNamespace(self.namespace)

    def setLevel(self, level):
        """
        Set the logging level for this logger's namespace.
        @param level: a logging level
        """
        setLogLevelForNamespace(self.namespace, level)

    def willLogAtLevel(self, level):
        """
        @param level: a logging level
        @return: C{True} if this logger will log at the given logging
            level.
        """
        return cmpLogLevels(self.level(), level) <= 0


    def msg(self, *message, **kw):
        if message:
            message = " ".join(map(safe_str, message))
        else:
            message = None
        return self.emit("info", message, **kw)



class LoggingMixIn (object):
    """
    Mix-in class for logging methods.
    """
    def _getLogger(self):
        try:
            return self._logger
        except AttributeError:
            self._logger = Logger(
                "%s.%s" % (
                    self.__class__.__module__,
                    self.__class__.__name__,
                )
            )

        return self._logger

    def _setLogger(self, value):
        self._logger = value

    logger = property(_getLogger, _setLogger)


def bindEmit(level):
    doc = """
    Emit a log message at log level C{%s}.
    @param message: The message to emit.
    """ % (level,)

    #
    # Attach methods to Logger
    #
    def log_emit(self, message, raiseException=None, **kwargs):
        self.emit(level, message, **kwargs)
        if raiseException:
            raise raiseException(message)

    def will_emit(self):
        return self.willLogAtLevel(level)

    log_emit.__doc__ = doc

    setattr(Logger, level, log_emit)
    setattr(Logger, level + "_enabled", property(will_emit))

    #
    # Attach methods to LoggingMixIn
    #
    def log_emit(self, message, raiseException=None, **kwargs):
        self.logger.emit(level, message, **kwargs)
        if raiseException:
            raise raiseException(message)

    def will_emit(self=log_emit):
        return self.logger.willLogAtLevel(level)

    log_emit.__doc__ = doc
    log_emit.enabled = will_emit

    setattr(LoggingMixIn, "log_" + level, log_emit)
    setattr(LoggingMixIn, "log_" + level + "_enabled", property(will_emit))


for level in logLevels: 
    bindEmit(level)
del level


# Add some compatibility with twisted's log module
Logger.err = Logger.error


##
# Errors
##

class InvalidLogLevelError (RuntimeError):
    def __init__(self, level):
        super(InvalidLogLevelError, self).__init__(str(level))
        self.level = level


##
# Observers
##

class StandardIOObserver (object):
    """
    Log observer that writes to standard I/O.
    """
    def emit(self, eventDict):
        text = None

        if eventDict["isError"]:
            output = stderr
            if "failure" in eventDict:
                text = eventDict["failure"].getTraceback()
        else:
            output = stdout

        if not text:
            text = " ".join([str(m) for m in eventDict["message"]]) + "\n"

        output.write(text)
        output.flush()

    def start(self):
        addObserver(self.emit)

    def stop(self):
        removeObserver(self.emit)
