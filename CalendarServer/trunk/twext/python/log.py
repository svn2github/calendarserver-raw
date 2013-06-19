# -*- test-case-name: twext.python.test.test_log-*-
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

"""
Classes and functions to do granular logging.

Example usage in a module::

    from twext.python.log import Logger
    log = Logger()

    ...

    log.debug("Got data: {data!r}.", data=data)

Or in a class::

    from twext.python.log import Logger

    class Foo(object):
        log = Logger()

        def oops(self, data):
            self.log.error("Oops! Invalid data from server: {data!r}", data=data)

C{Logger}s have namespaces, for which logging can be configured independently.
Namespaces may be specified by passing in a C{namespace} argument to L{Logger}
when instantiating it, but if none is given, the logger will derive its own
namespace by using the module name of the callable that instantiated it, or, in
the case of a class, by using the fully qualified name of the class.

In the first example above, the namespace would be C{some.module}, and in the
second example, it would be C{some.module.Foo}.
"""

__all__ = [
    "InvalidLogLevelError",
    "LogLevel",
    "formatEvent",
    "Logger",
    "LegacyLogger",
    "ILogObserver",
    "ILegacyLogObserver",
    "LogPublisher",
    "PredicateResult",
    "ILogFilterPredicate",
    "FilteringLogObserver",
    "LogLevelFilterPredicate",
    "LegacyLogObserver",
    "replaceTwistedLoggers",
    #"StandardIOObserver",
]

import sys
from sys import stdout, stderr
from string import Formatter
import inspect
import logging
import time

from zope.interface import Interface, implementer
from twisted.python.constants import NamedConstant, Names
from twisted.python.failure import Failure
from twisted.python.reflect import safe_str
import twisted.python.log
from twisted.python.log import msg as twistedLogMessage
from twisted.python.log import addObserver, removeObserver
from twisted.python.log import ILogObserver as ILegacyLogObserver



#
# Log level definitions
#

class InvalidLogLevelError(Exception):
    """
    Someone tried to use a L{LogLevel} that is unknown to the logging system.
    """
    def __init__(self, level):
        """
        @param level: a L{LogLevel}
        """
        super(InvalidLogLevelError, self).__init__(str(level))
        self.level = level



class LogLevel(Names):
    """
    Constants denoting log levels:

     - C{debug}: Information of use to a developer of the software, not
       generally of interest to someone running the software unless they are
       attempting to diagnose a software issue.

     - C{info}: Informational events: Routine information about the status of
       an application, such as incoming connections, startup of a subsystem,
       etc.

     - C{warn}: Warnings events: Events that may require greater attention than
       informational events but are a failure condition, such as authorization
       failures, bad data from a network client, etc.

     - C{error}: Error conditions: Events indicating a software failure, such
       as unhandled exceptions, loss of connectivity to a back-end database,
       etc.
    """
    debug = NamedConstant()
    info  = NamedConstant()
    warn  = NamedConstant()
    error = NamedConstant()

    @classmethod
    def levelWithName(cls, name):
        """
        @param name: the name of a L{LogLevel}

        @return: the L{LogLevel} with the specified C{name}
        """
        try:
            return cls.lookupByName(name)
        except ValueError:
            raise InvalidLogLevelError(name)


#
# Mappings to Python's logging module
#
pythonLogLevelMapping = {
    LogLevel.debug   : logging.DEBUG,
    LogLevel.info    : logging.INFO,
    LogLevel.warn    : logging.WARNING,
    LogLevel.error   : logging.ERROR,
   #LogLevel.critical: logging.CRITICAL,
}


##
# Loggers
##

def formatEvent(event):
    """
    Formats an event as a L{unicode}, using the format in
    C{event["log_format"]}.  This implementation should never
    raise an exception; if the formatting cannot be done, the
    returned string will describe the event so that a useful
    message is emitted regardless.

    @param event: a logging event

    @return: a L{unicode}
    """
    try:
        format = event.get("log_format", None)

        if format is None:
            raise ValueError("No log format provided")

        # Make sure format is unicode.
        if isinstance(format, bytes):
            # If we get bytes, assume it's UTF-8 bytes
            format = format.decode("utf-8")

        elif isinstance(format, unicode):
            pass

        else:
            raise TypeError("Log format must be unicode or bytes, not {0!r}"
                            .format(format))

        return formatWithCall(format, event)

    except BaseException as e:
        try:
            return formatUnformattableEvent(event, e)
        except:
            return u"MESSAGE LOST"


def formatUnformattableEvent(event, error):
    """
    Formats an event as a L{unicode} that describes the event
    generically and a formatting error.

    @param event: a logging event

    @param error: the formatting error

    @return: a L{unicode}
    """
    try:
        return (
            u"Unable to format event {event!r}: {error}"
            .format(event=event, error=error)
        )
    except BaseException as error:
        #
        # Yikes, something really nasty happened.
        #
        # Try to recover as much formattable data as possible;
        # hopefully at least the namespace is sane, which will
        # help you find the offending logger.
        #
        try:
            items = []

            for key, value in event.items():
                try:
                    items.append(u"{key!r} = ".format(key=key))
                except:
                    items.append(u"<UNFORMATTABLE KEY> = ")
                try:
                    items.append(u"{value!r}".format(value=value))
                except:
                    items.append(u"<UNFORMATTABLE VALUE>")

            text = ", ".join(items)
        except:
            text = ""

        return (
            u"MESSAGE LOST: Unformattable object logged: {error}\n"
            u"Recoverable data: {text}"
            .format(text=text)
        )



class Logger(object):
    """
    Logging object.
    """

    publisher = lambda e: None


    @staticmethod
    def _namespaceFromCallingContext():
        """
        Derive a namespace from the module containing the caller's caller.

        @return: a namespace
        """
        return inspect.currentframe().f_back.f_back.f_globals["__name__"]


    def __init__(self, namespace=None, source=None):
        """
        @param namespace: The namespace for this logger.  Uses a dotted
            notation, as used by python modules.  If not C{None}, then the name
            of the module of the caller is used.

        @param source: The object which is emitting events to this
            logger; this is automatically set on instances of a class
            if this L{Logger} is an attribute of that class.
        """
        if namespace is None:
            namespace = self._namespaceFromCallingContext()

        self.namespace = namespace
        self.source = source


    def __get__(self, oself, type=None):
        """
        When used as a descriptor, i.e.::

            # athing.py
            class Something(object):
                log = Logger()
                def hello(self):
                    self.log.info("Hello")

        a L{Logger}'s namespace will be set to the name of the class it is
        declared on.  In the above example, the namespace would be
        C{athing.Something}.

        Additionally, it's source will be set to the actual object referring to
        the L{Logger}.  In the above example, C{Something.log.source} would be
        C{Something}, and C{Something().log.source} would be an instance of
        C{Something}.
        """
        if oself is None:
            source = type
        else:
            source = oself

        return self.__class__(
            '.'.join([type.__module__, type.__name__]),
            source
        )


    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.namespace)


    def emit(self, level, format=None, **kwargs):
        """
        Emit a log event to all log observers at the given level.

        @param level: a L{LogLevel}

        @param format: a message format using new-style (PEP 3101)
            formatting.  The logging event (which is a L{dict}) is
            used to render this format string.

        @param kwargs: additional keyword parameters to include with
            the event.
        """
        if level not in LogLevel.iterconstants(): # FIXME: Updated Twisted supports 'in' on constants container
            self.failure(
                "Got invalid log level {invalidLevel!r} in {logger}.emit().",
                Failure(InvalidLogLevelError(level)),
                invalidLevel = level,
                logger = self,
            )
            #level = LogLevel.error
            # FIXME: continue to emit?
            return

        event = kwargs
        event.update(
            log_logger    = self,
            log_level     = level,
            log_namespace = self.namespace,
            log_source    = self.source,
            log_format    = format,
            log_time      = time.time(),
        )

        self.publisher(event)


    def failure(self, format, failure=None, level=LogLevel.error, **kwargs):
        """
        Log an failure and emit a traceback.

        For example::

            try:
                frob(knob)
            except Exception:
                log.failure("While frobbing {knob}", knob=knob)

        or::

            d = deferred_frob(knob)
            d.addErrback(lambda f: log.failure, "While frobbing {knob}", f, knob=knob)

        @param format: a message format using new-style (PEP 3101)
            formatting.  The logging event (which is a L{dict}) is
            used to render this format string.

        @param failure: a L{Failure} to log.  If C{None}, a L{Failure} is
            created from the exception in flight.

        @param level: a L{LogLevel} to use.

        @param kwargs: additional keyword parameters to include with the
            event.
        """
        if failure is None:
            failure=Failure()

        self.emit(level, format, log_failure=failure, **kwargs)



class LegacyLogger(object):
    """
    A logging object that provides some compatibility with the
    L{twisted.python.log} module.
    """

    def __init__(self, logger=None):
        if logger is not None:
            self.newStyleLogger = logger
        else:
            self.newStyleLogger = Logger(Logger._namespaceFromCallingContext())


    def __getattribute__(self, name):
        try:
            return super(LegacyLogger, self).__getattribute__(name)
        except AttributeError:
            return getattr(twisted.python.log, name)


    def msg(self, *message, **kwargs):
        """
        This method is API-compatible with L{twisted.python.log.msg} and exists
        for compatibility with that API.
        """
        if message:
            message = " ".join(map(safe_str, message))
        else:
            message = None
        return self.newStyleLogger.emit(LogLevel.info, message, **kwargs)


    def err(self, _stuff=None, _why=None, **kwargs):
        """
        This method is API-compatible with L{twisted.python.log.err} and exists
        for compatibility with that API.
        """
        if _stuff is None:
            _stuff = Failure()
        elif isinstance(_stuff, Exception):
            _stuff = Failure(_stuff)

        if isinstance(_stuff, Failure):
            self.newStyleLogger.emit(LogLevel.error, failure=_stuff, why=_why, isError=1, **kwargs)
        else:
            # We got called with an invalid _stuff.
            self.newStyleLogger.emit(LogLevel.error, repr(_stuff), why=_why, isError=1, **kwargs)



def bindEmit(level):
    doc = """
    Emit a log event at log level L{{{level}}}.

    @param format: a message format using new-style (PEP 3101)
        formatting.  The logging event (which is a L{{dict}}) is used to
        render this format string.

    @param kwargs: additional keyword parameters to include with the
        event.
    """.format(level=level.name)

    #
    # Attach methods to Logger
    #
    def log_emit(self, format=None, **kwargs):
        self.emit(level, format, **kwargs)

    log_emit.__doc__ = doc

    setattr(Logger, level.name, log_emit)

for level in LogLevel.iterconstants(): 
    bindEmit(level)

del level



#
# Observers
#

class ILogObserver(Interface):
    """
    An observer which can handle log events.
    """

    def __call__(event):
        """
        Log an event.

        @type event: C{dict} with (native) C{str} keys.

        @param event: A dictionary with arbitrary keys as defined by
            the application emitting logging events, as well as keys
            added by the logging system, with are:
            ...
        """



@implementer(ILogObserver)
class LogPublisher(object):
    """
    I{ILogObserver} that fans out events to other observers.

    Keeps track of a set of L{ILogObserver} objects and forwards
    events to each.
    """
    log = Logger()

    def __init__(self, *observers):
        self._observers = set(observers)


    @property
    def observers(self):
        return frozenset(self._observers)


    def addObserver(self, observer):
        """
        Registers an observer with this publisher.

        @param observer: An L{ILogObserver} to add.
        """
        self._observers.add(observer)


    def removeObserver(self, observer):
        """
        Unregisters an observer with this publisher.

        @param observer: An L{ILogObserver} to remove.
        """
        self._observers.remove(observer)


    def __call__(self, event): 
        for observer in self.observers:
            try:
                observer(event)
            except:
                #
                # We have to remove the offending observer because
                # we're going to badmouth it to all of its friends
                # (other observers) and it might get offended and
                # raise again, causing an infinite loop.
                #
                self.removeObserver(observer)
                try:
                    self.log.failure("Observer {observer} raised an exception; removing.", observer=observer)
                except:
                    pass
                finally:
                    self.addObserver(observer)



class PredicateResult(Names):
    """
    Predicate results.
    """
    yes   = NamedConstant() # Log this
    no    = NamedConstant() # Don't log this
    maybe = NamedConstant() # No opinion



class ILogFilterPredicate(Interface):
    """
    A predicate that determined whether an event should be logged.
    """

    def __call__(event):
        """
        Determine whether an event should be logged.

        @returns: a L{PredicateResult}.
        """



@implementer(ILogObserver)
class FilteringLogObserver(object):
    """
    L{ILogObserver} that wraps another L{ILogObserver}, but filters
    out events based on applying a series of L{ILogFilterPredicate}s.
    """

    def __init__(self, observer, predicates):
        """
        @param observer: an L{ILogObserver} to which this observer
            will forward events.

        @param predicates: an ordered iterable of predicates to apply
            to events before forwarding to the wrapped observer.
        """
        self.observer   = observer
        self.predicates = list(predicates)


    def shouldLogEvent(self, event):
        """
        Determine whether an event should be logged, based
        C{self.predicates}.

        @param event: an event
        """
        for predicate in self.predicates:
            result = predicate(event)
            if result == PredicateResult.yes:
                return True
            if result == PredicateResult.no:
                return False
            if result == PredicateResult.maybe:
                continue
            raise TypeError("Invalid predicate result: {0!r}".format(result))
        return True


    def __call__(self, event):
        if self.shouldLogEvent(event):
            self.observer(event)



@implementer(ILogFilterPredicate)
class LogLevelFilterPredicate(object):
    """
    L{ILogFilterPredicate} that filters out events with a log level
    lower than the log level for the event's namespace.
    """

    def __init__(self):
        # FIXME: Make this a class variable. But that raises an
        # _initializeEnumerants constants error in Twisted 12.2.0.
        self.defaultLogLevel = LogLevel.info

        self._logLevelsByNamespace = {}
        self.clearLogLevels()


    def logLevelForNamespace(self, namespace):
        """
        @param namespace: a logging namespace, or C{None} for the default
            namespace.

        @return: the L{LogLevel} for the specified namespace.
        """
        if not namespace:
            return self._logLevelsByNamespace[None]

        if namespace in self._logLevelsByNamespace:
            return self._logLevelsByNamespace[namespace]

        segments = namespace.split(".")
        index = len(segments) - 1

        while index > 0:
            namespace = ".".join(segments[:index])
            if namespace in self._logLevelsByNamespace:
                return self._logLevelsByNamespace[namespace]
            index -= 1

        return self._logLevelsByNamespace[None]


    def setLogLevelForNamespace(self, namespace, level):
        """
        Sets the global log level for a logging namespace.

        @param namespace: a logging namespace

        @param level: the L{LogLevel} for the given namespace.
        """
        if level not in LogLevel.iterconstants():
            raise InvalidLogLevelError(level)

        if namespace:
            self._logLevelsByNamespace[namespace] = level
        else:
            self._logLevelsByNamespace[None] = level


    def clearLogLevels(self):
        """
        Clears all global log levels to the default.
        """
        self._logLevelsByNamespace.clear()
        self._logLevelsByNamespace[None] = self.defaultLogLevel


    def __call__(self, event):
        level     = event["log_level"]
        namespace = event["log_namespace"]

        if level < self.logLevelForNamespace(namespace):
            return PredicateResult.no

        return PredicateResult.maybe



@implementer(ILogObserver)
class LegacyLogObserver(object):
    """
    L{ILogObserver} that wraps an L{ILegacyLogObserver}.
    """

    def __init__(self, legacyObserver):
        """
        @param legacyObserver: an L{ILegacyLogObserver} to which this
            observer will forward events.
        """
        self.legacyObserver = legacyObserver

    
    def __call__(self, event): 
        prefix = "[{log_namespace}#{log_level.name}] ".format(**event)

        level = event["log_level"]

        #
        # Twisted's logging supports indicating a python log level, so let's
        # provide the equivalent to our logging levels.
        #
        if level in pythonLogLevelMapping:
            event["logLevel"] = pythonLogLevelMapping[level]

        # Format new style -> old style
        if event["log_format"]:
            #
            # Create an object that implements __str__() in order to
            # defer the work of formatting until it's needed by a
            # legacy log observer.
            #
            class LegacyFormatStub(object):
                def __str__(oself):
                    return formatEvent(event).encode("utf-8")

            event["format"] = prefix + "%(log_legacy)s"
            event["log_legacy"] = LegacyFormatStub()

        # log.failure() -> isError blah blah
        if "log_failure" in event:
            event["failure"] = event["log_failure"]
            event["isError"] = 1
            event["why"] = "{prefix}{message}".format(prefix=prefix, message=formatEvent(event))

        self.legacyObserver(**event)



# FIXME: This could have a better name.
class DefaultLogPublisher(object):
    """
    This observer sets up a set of chained observers as follows:

        1. B{rootPublisher} - a L{LogPublisher}

        2. B{filters}: a L{FilteringLogObserver} that filters out messages
           using a L{LogLevelFilterPredicate}

        3. B{filteredPublisher} - a L{LogPublisher}

        4. B{legacyLogObserver} - a A{LegacyLogObserver} wired up to
           L{twisted.python.log.msg}

    The purpose of this class is to provide a default log observer with
    sufficient hooks to enable applications to add observers that can either
    receive all log messages, or only log messages that are configured to pass
    though the L{LogLevelFilterPredicate}::

        from twext.python.log import Logger, ILogObserver

        log = Logger()

        @implementer(ILogObserver)
        class AMPObserver(object):
            def __call__(self, event):
                # eg.: Hold all emitted events in a ring buffer and expose them
                # via AMP.
                ...

        @implementer(ILogObserver)
        class FileObserver(object):
            def __call__(self, event):
                # eg.: Take only the filtered events and write them into a
                # file.
                ...

        log.publisher.rootPublisher.addObserver(AMPObserver())
        log.publisher.filteredPublisher.addObserver(FileObserver())

    With no observers added, the default behavior is that the legacy Twisted
    logging system sees messages as controlled by L{LogLevelFilterPredicate}.
    """

    def __init__(self):
        self.legacyLogObserver = LegacyLogObserver(twistedLogMessage)
        self.filteredPublisher = LogPublisher(self.legacyLogObserver)
        self.levels            = LogLevelFilterPredicate()
        self.filters           = FilteringLogObserver(self.filteredPublisher, (self.levels,))
        self.rootPublisher     = LogPublisher(self.filters)


    def __call__(self, event):
        self.rootPublisher(event)



Logger.publisher = DefaultLogPublisher()



#
# Utilities
#

class CallMapping(object):
    def __init__(self, submapping):
        self._submapping = submapping

    def __getitem__(self, key):
        callit = key.endswith(u"()")
        realKey = key[:-2] if callit else key
        value = self._submapping[realKey]
        if callit:
            value = value()
        return value


def formatWithCall(formatString, mapping):
    """
    Format a string like L{unicode.format}, but:

        - taking only a name mapping; no positional arguments

        - with the additional syntax that an empty set of parentheses
          correspond to a formatting item that should be called, and its result
          C{str}'d, rather than calling C{str} on the element directly as
          normal.

    For example::

        >>> formatWithCall("{string}, {function()}.",
        ...                dict(string="just a string",
        ...                     function=lambda: "a function"))
        'just a string, a function.'

    @param formatString: A PEP-3101 format string.
    @type formatString: L{unicode}

    @param mapping: A L{dict}-like object to format.

    @return: The string with formatted values interpolated.
    @rtype: L{unicode}
    """
    return unicode(
        theFormatter.vformat(formatString, (), CallMapping(mapping))
    )

theFormatter = Formatter()


def replaceTwistedLoggers():
    """
    Visit all Python modules that have been loaded and:

     - replace L{twisted.python.log} with a L{LegacyLogger}

     - replace L{twisted.python.log.msg} with a L{LegacyLogger}'s C{msg}

     - replace L{twisted.python.log.err} with a L{LegacyLogger}'s C{err}
    """
    log = Logger()

    for moduleName, module in sys.modules.iteritems():
        # Oddly, this happens
        if module is None:
            continue

        # Don't patch Twisted's logging module
        if module in (twisted.python, twisted.python.log):
            continue

        # Don't patch this module
        if moduleName is __name__:
            continue

        for name, obj in module.__dict__.iteritems():
            legacyLogger = LegacyLogger(logger=Logger(namespace=module.__name__))

            if obj is twisted.python.log:
                log.info("Replacing Twisted log module object {0} in {1}".format(name, module.__name__))
                setattr(module, name, legacyLogger)
            elif obj is twisted.python.log.msg:
                log.info("Replacing Twisted log.msg object {0} in {1}".format(name, module.__name__))
                setattr(module, name, legacyLogger.msg)
            elif obj is twisted.python.log.err:
                log.info("Replacing Twisted log.err object {0} in {1}".format(name, module.__name__))
                setattr(module, name, legacyLogger.err)



######################################################################
# FIXME: This may not be needed; look into removing it.

class StandardIOObserver(object):
    """
    (Legacy) log observer that writes to standard I/O.
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
