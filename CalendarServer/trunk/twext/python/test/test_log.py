##
# Copyright (c) 2005-2013 Apple Inc. All rights reserved.
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

from zope.interface.verify import verifyObject, BrokenMethodImplementation

from twisted.python import log as twistedLogging
from twisted.python.failure import Failure
from twisted.trial import unittest

from twext.python.log import (
    LogLevel, InvalidLogLevelError,
    pythonLogLevelMapping,
    formatEvent, formatUnformattableEvent, formatWithCall,
    Logger, LegacyLogger,
    ILogObserver, LogPublisher,
    FilteringLogObserver, PredicateResult,
    LogLevelFilterPredicate,
)



defaultLogLevel         = LogLevelFilterPredicate().defaultLogLevel
clearLogLevels          = Logger.publisher.levels.clearLogLevels
logLevelForNamespace    = Logger.publisher.levels.logLevelForNamespace
setLogLevelForNamespace = Logger.publisher.levels.setLogLevelForNamespace



class TestLogger(Logger):
    def emit(self, level, format=None, **kwargs):
        if False:
            print "*"*60
            print "level =", level
            print "format =", format
            for key, value in kwargs.items():
                print key, "=", value
            print "*"*60

        def observer(event):
            self.event = event

        twistedLogging.addObserver(observer)
        try:
            Logger.emit(self, level, format, **kwargs)
        finally:
            twistedLogging.removeObserver(observer)

        self.emitted = {
            "level" : level,
            "format": format,
            "kwargs": kwargs,
        }



class TestLegacyLogger(LegacyLogger):
    def __init__(self, logger=TestLogger()):
        LegacyLogger.__init__(self, logger=logger)



class LogComposedObject(object):
    """
    Just a regular object.
    """
    log = TestLogger()

    def __init__(self, state=None):
        self.state = state


    def __str__(self):
        return "<LogComposedObject {state}>".format(state=self.state)



class SetUpTearDown(object):
    def setUp(self):
        super(SetUpTearDown, self).setUp()
        clearLogLevels()


    def tearDown(self):
        super(SetUpTearDown, self).tearDown()
        clearLogLevels()



class LoggingTests(SetUpTearDown, unittest.TestCase):
    """
    General module tests.
    """

    def test_levelWithName(self):
        """
        Look up log level by name.
        """
        for level in LogLevel.iterconstants():
            self.assertIdentical(LogLevel.levelWithName(level.name), level)


    def test_levelWithInvalidName(self):
        """
        You can't make up log level names.
        """
        bogus = "*bogus*"
        try:
            LogLevel.levelWithName(bogus)
        except InvalidLogLevelError as e:
            self.assertIdentical(e.level, bogus)
        else:
            self.fail("Expected InvalidLogLevelError.")


    def test_defaultLogLevel(self):
        """
        Default log level is used.
        """
        self.failUnless(logLevelForNamespace(None), defaultLogLevel)
        self.failUnless(logLevelForNamespace(""), defaultLogLevel)
        self.failUnless(logLevelForNamespace("rocker.cool.namespace"), defaultLogLevel)


    def test_setLogLevel(self):
        """
        Setting and retrieving log levels.
        """
        setLogLevelForNamespace(None, LogLevel.error)
        setLogLevelForNamespace("twext.web2", LogLevel.debug)
        setLogLevelForNamespace("twext.web2.dav", LogLevel.warn)

        self.assertEquals(logLevelForNamespace(None                        ), LogLevel.error)
        self.assertEquals(logLevelForNamespace("twisted"                   ), LogLevel.error)
        self.assertEquals(logLevelForNamespace("twext.web2"                ), LogLevel.debug)
        self.assertEquals(logLevelForNamespace("twext.web2.dav"            ), LogLevel.warn)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test"       ), LogLevel.warn)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test1.test2"), LogLevel.warn)


    def test_setInvalidLogLevel(self):
        """
        Can't pass invalid log levels to setLogLevelForNamespace().
        """
        self.assertRaises(InvalidLogLevelError, setLogLevelForNamespace, "twext.web2", object())

        # Level must be a constant, not the name of a constant
        self.assertRaises(InvalidLogLevelError, setLogLevelForNamespace, "twext.web2", "debug")


    def test_clearLogLevels(self):
        """
        Clearing log levels.
        """
        setLogLevelForNamespace("twext.web2"    , LogLevel.debug)
        setLogLevelForNamespace("twext.web2.dav", LogLevel.error)

        clearLogLevels()

        self.assertEquals(logLevelForNamespace("twisted"                   ), defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2"                ), defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2.dav"            ), defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test"       ), defaultLogLevel)
        self.assertEquals(logLevelForNamespace("twext.web2.dav.test1.test2"), defaultLogLevel)


    def test_namespace_default(self):
        """
        Default namespace is module name.
        """
        log = Logger()
        self.assertEquals(log.namespace, __name__)


    def test_formatWithCall(self):
        """
        L{formatWithCall} is an extended version of L{unicode.format} that will
        interpret a set of parentheses "C{()}" at the end of a format key to
        mean that the format key ought to be I{called} rather than stringified.
        """
        self.assertEquals(
            formatWithCall(
                u"Hello, {world}. {callme()}.",
                dict(world="earth", callme=lambda: "maybe")
            ),
            "Hello, earth. maybe."
        )
        self.assertEquals(
            formatWithCall(
                u"Hello, {repr()!r}.",
                dict(repr=lambda: 'repr')
            ),
            "Hello, 'repr'."
        )


    def test_formatEvent(self):
        """
        L{formatEvent} will format an event according to several rules:

            - A string with no formatting instructions will be passed straight
              through.

            - PEP 3101 strings will be formatted using the keys and values of
              the event as named fields.

            - PEP 3101 keys ending with C{()} will be treated as instructions
              to call that key (which ought to be a callable) before
              formatting.

        L{formatEvent} will always return L{unicode}, and if given
        bytes, will always treat its format string as UTF-8 encoded.
        """
        def format(log_format, **event):
            event["log_format"] = log_format
            result = formatEvent(event)
            self.assertIdentical(type(result), unicode)
            return result

        self.assertEquals(u"", format(b""))
        self.assertEquals(u"", format(u""))
        self.assertEquals(u"abc", format("{x}", x="abc"))
        self.assertEquals(u"no, yes.",
                          format("{not_called}, {called()}.",
                                 not_called="no", called=lambda: "yes"))
        self.assertEquals(u'S\xe1nchez', format("S\xc3\xa1nchez"))
        self.assertIn(u"Unable to format event", format(b"S\xe1nchez"))
        self.assertIn(u"Unable to format event",
                      format(b"S{a}nchez", a=b"\xe1"))
        self.assertIn(u"S'\\xe1'nchez",
                      format(b"S{a!r}nchez", a=b"\xe1"))


    def test_formatEventNoFormat(self):
        """
        Formatting an event with no format.
        """
        event = dict(foo=1, bar=2)
        result = formatEvent(event)

        self.assertIn("Unable to format event", result)
        self.assertIn(repr(event), result)


    def test_formatEventWeirdFormat(self):
        """
        Formatting an event with a bogus format.
        """
        event = dict(log_format=object(), foo=1, bar=2)
        result = formatEvent(event)

        self.assertIn("Log format must be unicode or bytes", result)
        self.assertIn(repr(event), result)


    def test_formatUnformattableEvent(self):
        """
        Formatting an event that's just plain out to get us.
        """
        event = dict(log_format="{evil()}", evil=lambda: 1/0)
        result = formatEvent(event)

        self.assertIn("Unable to format event", result)
        self.assertIn(repr(event), result)


    def test_formatUnformattableEventWithUnformattableKey(self):
         """
         Formatting an unformattable event that has an unformattable key.
         """
         event = {
             "log_format": "{evil()}",
             "evil": lambda: 1/0,
             Gurk(): "gurk",
         }
         result = formatEvent(event)

         self.assertIn("MESSAGE LOST: unformattable object logged:", result)
         self.assertIn("Recoverable data:", result)
         self.assertIn("Exception during formatting:", result)


    def test_formatUnformattableEventWithUnformattableValue(self):
         """
         Formatting an unformattable event that has an unformattable value.
         """
         event = dict(
             log_format="{evil()}",
             evil=lambda: 1/0,
             gurk=Gurk(),
         )
         result = formatEvent(event)

         self.assertIn("MESSAGE LOST: unformattable object logged:", result)
         self.assertIn("Recoverable data:", result)
         self.assertIn("Exception during formatting:", result)


    def test_formatUnformattableEventWithUnformattableErrorOMGWillItStop(self):
         """
         Formatting an unformattable event that has an unformattable value.
         """
         event = dict(
             log_format="{evil()}",
             evil=lambda: 1/0,
         )

         # Call formatUnformattableEvent() directly with a bogus exception.
         result = formatUnformattableEvent(event, Gurk())

         self.assertIn("MESSAGE LOST: unable to recover any data from message:", result)



class LoggerTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{Logger}.
    """

    def test_repr(self):
        """
        repr() on Logger
        """
        namespace = "bleargh"
        log = Logger(namespace)
        self.assertEquals(repr(log), "<Logger {0}>".format(repr(namespace)))


    def test_namespace_attribute(self):
        """
        Default namespace for classes using L{Logger} as a descriptor is the
        class name they were retrieved from.
        """
        obj = LogComposedObject()
        self.assertEquals(obj.log.namespace,
                          "twext.python.test.test_log.LogComposedObject")
        self.assertEquals(LogComposedObject.log.namespace,
                          "twext.python.test.test_log.LogComposedObject")
        self.assertIdentical(LogComposedObject.log.source, LogComposedObject)
        self.assertIdentical(obj.log.source, obj)
        self.assertIdentical(Logger().source, None)


    def test_sourceAvailableForFormatting(self):
        """
        On instances that have a L{Logger} class attribute, the C{log_source} key
        is available to format strings.
        """
        obj = LogComposedObject("hello")
        log = obj.log
        log.error("Hello, {log_source}.")

        self.assertIn("log_source", log.event)
        self.assertEquals(log.event["log_source"], obj)

        stuff = formatEvent(log.event)
        self.assertIn("Hello, <LogComposedObject hello>.", stuff)


    def test_basic_Logger(self):
        """
        Test that log levels and messages are emitted correctly for
        Logger.
        """
        # FIXME: Need a basic test like this for logger attached to a class.
        # At least: source should not be None in that case.

        log = TestLogger()

        for level in LogLevel.iterconstants():
            format = "This is a {level_name} message"
            message = format.format(level_name=level.name)

            method = getattr(log, level.name)
            method(format, junk=message, level_name=level.name)

            # Ensure that test_emit got called with expected arguments
            self.assertEquals(log.emitted["level"], level)
            self.assertEquals(log.emitted["format"], format)
            self.assertEquals(log.emitted["kwargs"]["junk"], message)

            if level >= logLevelForNamespace(log.namespace):
                self.assertTrue(hasattr(log, "event"), "No event observed.")
                self.assertEquals(log.event["log_format"], format)
                self.assertEquals(log.event["log_level"], level)
                self.assertEquals(log.event["log_namespace"], __name__)
                self.assertEquals(log.event["log_source"], None)

                self.assertEquals(log.event["logLevel"], pythonLogLevelMapping[level])

                self.assertEquals(log.event["junk"], message)

                # FIXME: this checks the end of message because we do formatting in emit()
                self.assertEquals(
                    formatEvent(log.event),
                    message
                )
            else:
                self.assertFalse(hasattr(log, "event"))


    def test_defaultFailure(self):
        """
        Test that log.failure() emits the right data.
        """
        log = TestLogger()
        try:
            raise RuntimeError("baloney!")
        except RuntimeError:
            log.failure("Whoops")

        #
        # log.failure() will cause trial to complain, so here we check that
        # trial saw the correct error and remove it from the list of things to
        # complain about.
        #
        errors = self.flushLoggedErrors(RuntimeError)
        self.assertEquals(len(errors), 1)

        self.assertEquals(log.emitted["level"], LogLevel.error)
        self.assertEquals(log.emitted["format"], "Whoops")


    def test_conflicting_kwargs(self):
        """
        Make sure that kwargs conflicting with args don't pass through.
        """
        log = TestLogger()

        log.warn(
            "*",
            log_format = "#",
            log_level = LogLevel.error,
            log_namespace = "*namespace*",
            log_source = "*source*",
        )

        # FIXME: Should conflicts log errors?

        self.assertEquals(log.event["log_format"], "*")
        self.assertEquals(log.event["log_level"], LogLevel.warn)
        self.assertEquals(log.event["log_namespace"], log.namespace)
        self.assertEquals(log.event["log_source"], None)


    def test_logInvalidLogLevel(self):
        """
        Test passing in a bogus log level to C{emit()}.
        """
        log = TestLogger()

        log.emit("*bogus*")

        errors = self.flushLoggedErrors(InvalidLogLevelError)
        self.assertEquals(len(errors), 1)



class LogPublisherTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{LogPublisher}.
    """

    def test_interface(self):
        """
        L{LogPublisher} is an L{ILogObserver}.
        """
        publisher = LogPublisher()
        try:
            verifyObject(ILogObserver, publisher)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def test_observers(self):
        """
        L{LogPublisher.observers} returns the observers.
        """
        o1 = lambda e: None
        o2 = lambda e: None

        publisher = LogPublisher(o1, o2)
        self.assertEquals(set((o1, o2)), set(publisher.observers))


    def test_addObserver(self):
        """
        L{LogPublisher.addObserver} adds an observer.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = LogPublisher(o1, o2)
        publisher.addObserver(o3)
        self.assertEquals(set((o1, o2, o3)), set(publisher.observers))


    def test_removeObserver(self):
        """
        L{LogPublisher.removeObserver} removes an observer.
        """
        o1 = lambda e: None
        o2 = lambda e: None
        o3 = lambda e: None

        publisher = LogPublisher(o1, o2, o3)
        publisher.removeObserver(o2)
        self.assertEquals(set((o1, o3)), set(publisher.observers))


    def test_fanOut(self):
        """
        L{LogPublisher} calls its observers.
        """
        e1 = []
        e2 = []
        e3 = []

        o1 = lambda e: e1.append(e)
        o2 = lambda e: e2.append(e)
        o3 = lambda e: e3.append(e)

        publisher = LogPublisher(o1, o2, o3)
        publisher.removeObserver(o2)
        self.assertEquals(set((o1, o3)), set(publisher.observers))



class FilteringLogObserverTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{FilteringLogObserver}.
    """

    def test_interface(self):
        """
        L{FilteringLogObserver} is an L{ILogObserver}.
        """
        observer = FilteringLogObserver(lambda e: None, ())
        try:
            verifyObject(ILogObserver, observer)
        except BrokenMethodImplementation as e:
            self.fail(e)


    def filterWith(self, *filters):
        events = [
            dict(count=0),
            dict(count=1),
            dict(count=2),
            dict(count=3),
        ]

        class Filters(object):
            @staticmethod
            def twoMinus(event):
                if event["count"] <= 2:
                    return PredicateResult.yes
                return PredicateResult.maybe

            @staticmethod
            def twoPlus(event):
                if event["count"] >= 2:
                    return PredicateResult.yes
                return PredicateResult.maybe

            @staticmethod
            def notTwo(event):
                if event["count"] == 2:
                    return PredicateResult.no
                return PredicateResult.maybe

            @staticmethod
            def no(event):
                return PredicateResult.no

        predicates = (getattr(Filters, f) for f in filters)
        eventsSeen = []
        trackingObserver = lambda e: eventsSeen.append(e)
        filteringObserver = FilteringLogObserver(trackingObserver, predicates)
        for e in events: filteringObserver(e)

        return [e["count"] for e in eventsSeen]


    def test_shouldLogEvent_noFilters(self):
        self.assertEquals(self.filterWith(), [0, 1, 2, 3])

    def test_shouldLogEvent_noFilter(self):
        self.assertEquals(self.filterWith("notTwo"), [0, 1, 3])

    def test_shouldLogEvent_yesFilter(self):
        self.assertEquals(self.filterWith("twoPlus"), [0, 1, 2, 3])

    def test_shouldLogEvent_yesNoFilter(self):
        self.assertEquals(self.filterWith("twoPlus", "no"), [2, 3])

    def test_shouldLogEvent_yesYesNoFilter(self):
        self.assertEquals(self.filterWith("twoPlus", "twoMinus", "no"), [0, 1, 2, 3])


    def test_call(self):
        e = dict(obj=object())

        def callWithPredicateResult(result):
            seen = []
            observer = FilteringLogObserver(lambda e: seen.append(e), (lambda e: result,))
            observer(e)
            return seen

        self.assertIn(e, callWithPredicateResult(PredicateResult.yes))
        self.assertIn(e, callWithPredicateResult(PredicateResult.maybe))
        self.assertNotIn(e, callWithPredicateResult(PredicateResult.no))



class LegacyLoggerTests(SetUpTearDown, unittest.TestCase):
    """
    Tests for L{LegacyLogger}.
    """

    def test_namespace_default(self):
        """
        Default namespace is module name.
        """
        log = TestLegacyLogger(logger=None)
        self.assertEquals(log.newStyleLogger.namespace, __name__)


    def test_passThroughAttributes(self):
        """
        C{__getattribute__} on L{LegacyLogger} is passing through to Twisted's
        logging module.
        """
        log = TestLegacyLogger()

        # Not passed through
        self.assertIn("API-compatible", log.msg.__doc__)
        self.assertIn("API-compatible", log.err.__doc__)

        # Passed through
        self.assertIdentical(log.addObserver, twistedLogging.addObserver)


    def test_legacy_msg(self):
        """
        Test LegacyLogger's log.msg()
        """
        log = TestLegacyLogger()

        message = "Hi, there."
        kwargs = { "foo": "bar", "obj": object() }

        log.msg(message, **kwargs)

        self.assertIdentical(log.newStyleLogger.emitted["level"], LogLevel.info)
        self.assertEquals(log.newStyleLogger.emitted["format"], message)

        for key, value in kwargs.items():
            self.assertIdentical(log.newStyleLogger.emitted["kwargs"][key], value)

        log.msg(foo="")

        self.assertIdentical(log.newStyleLogger.emitted["level"], LogLevel.info)
        self.assertIdentical(log.newStyleLogger.emitted["format"], None)


    def test_legacy_err_implicit(self):
        """
        Test LegacyLogger's log.err() capturing the in-flight exception.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = { "foo": "bar", "obj": object() }

        try:
            raise exception
        except RuntimeError:
            log.err(**kwargs)

        self.legacy_err(log, kwargs, None, exception)


    def test_legacy_err_exception(self):
        """
        Test LegacyLogger's log.err() with a given exception.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = { "foo": "bar", "obj": object() }
        why = "Because I said so."

        try:
            raise exception
        except RuntimeError as e:
            log.err(e, why, **kwargs)

        self.legacy_err(log, kwargs, why, exception)


    def test_legacy_err_failure(self):
        """
        Test LegacyLogger's log.err() with a given L{Failure}.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = { "foo": "bar", "obj": object() }
        why = "Because I said so."

        try:
            raise exception
        except RuntimeError:
            log.err(Failure(), why, **kwargs)

        self.legacy_err(log, kwargs, why, exception)


    def test_legacy_err_bogus(self):
        """
        Test LegacyLogger's log.err() with a bogus argument.
        """
        log = TestLegacyLogger()

        exception = RuntimeError("Oh me, oh my.")
        kwargs = { "foo": "bar", "obj": object() }
        why = "Because I said so."
        bogus = object()

        try:
            raise exception
        except RuntimeError:
            log.err(bogus, why, **kwargs)

        errors = self.flushLoggedErrors(exception.__class__)
        self.assertEquals(len(errors), 0)

        self.assertIdentical(log.newStyleLogger.emitted["level"], LogLevel.error)
        self.assertEquals(log.newStyleLogger.emitted["format"], repr(bogus))
        self.assertIdentical(log.newStyleLogger.emitted["kwargs"]["why"], why)

        for key, value in kwargs.items():
            self.assertIdentical(log.newStyleLogger.emitted["kwargs"][key], value)


    def legacy_err(self, log, kwargs, why, exception):
        #
        # log.failure() will cause trial to complain, so here we check that
        # trial saw the correct error and remove it from the list of things to
        # complain about.
        #
        errors = self.flushLoggedErrors(exception.__class__)
        self.assertEquals(len(errors), 1)

        self.assertIdentical(log.newStyleLogger.emitted["level"], LogLevel.error)
        self.assertEquals(log.newStyleLogger.emitted["format"], None)
        self.assertIdentical(log.newStyleLogger.emitted["kwargs"]["failure"].__class__, Failure)
        self.assertIdentical(log.newStyleLogger.emitted["kwargs"]["failure"].value, exception)
        self.assertIdentical(log.newStyleLogger.emitted["kwargs"]["why"], why)

        for key, value in kwargs.items():
            self.assertIdentical(log.newStyleLogger.emitted["kwargs"][key], value)



class Gurk(object):
    # Class that raises in C{__repr__()}.
    def __repr__(self):
        return str(1/0)
