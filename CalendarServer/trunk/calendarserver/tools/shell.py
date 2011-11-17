#!/usr/bin/env python
##
# Copyright (c) 2011 Apple Inc. All rights reserved.
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
Interactive shell for navigating the data store.
"""

import os
import sys
import tty
import termios
from shlex import shlex

from twisted.python import log
from twisted.python.log import startLogging
from twisted.python.text import wordWrap
from twisted.python.usage import Options, UsageError
from twisted.internet.defer import succeed, fail, Deferred
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.stdio import StandardIO
from twisted.conch.recvline import HistoricRecvLine as ReceiveLineProtocol
from twisted.conch.insults.insults import ServerProtocol
from twisted.application.service import Service

from txdav.common.icommondatastore import NotFoundError

from twistedcaldav.stdconfig import DEFAULT_CONFIG_FILE

from calendarserver.tools.cmdline import utilityMain


def usage(e=None):
    if e:
        print e
        print ""
    try:
        ShellOptions().opt_help()
    except SystemExit:
        pass
    if e:
        sys.exit(64)
    else:
        sys.exit(0)


class ShellOptions(Options):
    """
    Command line options for "calendarserver_shell".
    """
    synopsis = "\n".join(
        wordWrap(
            """
            Usage: calendarserver_shell [options]\n
            """ + __doc__,
            int(os.environ.get("COLUMNS", "80"))
        )
    )

    optParameters = [
        ["config", "f", DEFAULT_CONFIG_FILE, "Specify caldavd.plist configuration path."],
    ]

    def __init__(self):
        super(ShellOptions, self).__init__()


class ShellService(Service, object):
    def __init__(self, store, options, reactor, config):
        super(ShellService, self).__init__()
        self.store      = store
        self.options    = options
        self.reactor    = reactor
        self.config     = config
        self.terminalFD = None
        self.protocol   = None

    def startService(self):
        """
        Start the service.
        """
        # For debugging
        f = open("/tmp/shell.log", "w")
        startLogging(f)

        super(ShellService, self).startService()

        # Set up the terminal for interactive action
        self.terminalFD = sys.__stdin__.fileno()
        self._oldTerminalSettings = termios.tcgetattr(self.terminalFD)
        tty.setraw(self.terminalFD)

        self.protocol = ServerProtocol(lambda: ShellProtocol(self))
        StandardIO(self.protocol)

    def stopService(self):
        """
        Stop the service.
        """
        # Restore terminal settings
        termios.tcsetattr(self.terminalFD, termios.TCSANOW, self._oldTerminalSettings)
        os.write(self.terminalFD, "\r\x1bc\r")


class UnknownArguments (Exception):
    """
    Unknown arguments.
    """
    def __init__(self, arguments):
        Exception.__init__(self, "Unknown arguments: %s" % (arguments,))
        self.arguments = arguments


class ShellProtocol(ReceiveLineProtocol):
    """
    Data store shell protocol.
    """

    # FIXME:
    # * Received lines are being echoed; find out why and stop it.
    # * Backspace transposes characters in the terminal.

    ps = ("ds% ", "... ")

    def __init__(self, service):
        ReceiveLineProtocol.__init__(self)
        self.service = service
        self.wd = RootDirectory(service.store)
        self.inputLines = []
        self.activeCommand = None

    def connectionMade(self):
        ReceiveLineProtocol.connectionMade(self)

        CTRL_C = '\x03'
        CTRL_D = '\x04'
        CTRL_BACKSLASH = '\x1c'
        CTRL_L = '\x0c'

        self.keyHandlers[CTRL_C] = self.handle_INT
        self.keyHandlers[CTRL_D] = self.handle_EOF
        self.keyHandlers[CTRL_L] = self.handle_FF
        self.keyHandlers[CTRL_BACKSLASH] = self.handle_QUIT

    def handle_INT(self):
        """
        Handle ^C as an interrupt keystroke by resetting the current input
        variables to their initial state.
        """
        self.pn = 0
        self.lineBuffer = []
        self.lineBufferIndex = 0

        self.terminal.nextLine()
        self.terminal.write("KeyboardInterrupt")
        self.terminal.nextLine()
        self.exit()

    def handle_EOF(self):
        if self.lineBuffer:
            self.terminal.write('\a')
        else:
            self.handle_QUIT()

    def handle_FF(self):
        """
        Handle a 'form feed' byte - generally used to request a screen
        refresh/redraw.
        """
        self.terminal.eraseDisplay()
        self.terminal.cursorHome()
        self.drawInputLine()

    def handle_QUIT(self):
        self.exit()

    def exit(self):
        self.terminal.loseConnection()
        self.service.reactor.stop()

    def lineReceived(self, line):
        if self.activeCommand is not None:
            self.inputLines.append(line)
            return

        lexer = shlex(line)
        lexer.whitespace_split = True

        tokens = []
        while True:
            token = lexer.get_token()
            if not token:
                break
            tokens.append(token)

        if tokens:
            cmd = tokens.pop(0)
            #print "Arguments: %r" % (tokens,)

            m = getattr(self, "cmd_%s" % (cmd,), None)
            if m:
                def handleUnknownArguments(f):
                    f.trap(UnknownArguments)
                    self.terminal.write("%s\n" % (f.value,))

                def handleException(f):
                    self.terminal.write("Error: %s\n" % (f.value,))
                    if not f.check(NotImplementedError, NotFoundError):
                        log.msg("-"*80 + "\n")
                        log.msg(f.getTraceback())
                        log.msg("-"*80 + "\n")

                def next(_):
                    self.activeCommand = None
                    self.drawInputLine()
                    if self.inputLines:
                        line = self.inputLines.pop(0)
                        self.lineReceived(line)

                d = self.activeCommand = Deferred()
                d.addCallback(lambda _: m(tokens))
                if True:
                    d.callback(None)
                else:
                    # Add time to test callbacks
                    self.service.reactor.callLater(4, d.callback, None)
                d.addErrback(handleUnknownArguments)
                d.addErrback(handleException)
                d.addCallback(next)
            else:
                self.terminal.write("Unknown command: %s\n" % (cmd,))
                self.drawInputLine()
        else:
            self.drawInputLine()

    def _getTarget(self, tokens):
        if tokens:
            return self.wd.locate(tokens.pop(0).split("/"))
        else:
            return self.wd

    def cmd_help(self, tokens):
        """
        Show help.

        usage: help [command]
        """
        if tokens:
            command = tokens.pop(0)
        else:
            command = None

        if tokens:
            raise UnknownArguments(tokens)

        if command:
            m = getattr(self, "cmd_%s" % (command,), None)
            if m:
                doc = m.__doc__.split("\n")

                # Throw out first and last line if it's empty
                if doc:
                    if not doc[0].strip():
                        doc.pop(0)
                    if not doc[-1].strip():
                        doc.pop()

                if doc:
                    # Get length of indentation
                    i = len(doc[0]) - len(doc[0].lstrip())

                    for line in doc:
                        self.terminal.write(line[i:])
                        self.terminal.nextLine()

                else:
                    self.terminal.write("(No documentation available for %s)\n" % (command,))
            else:
                raise NotFoundError("Unknown command: %s" % (command,))
        else:
            self.terminal.write("Available commands:\n")

            result = []

            for attr in dir(self):
                if attr.startswith("cmd_"):
                    m = getattr(self, attr)

                    for line in m.__doc__.split("\n"):
                        line = line.strip()
                        if line:
                            doc = line
                            break
                    else:
                        doc = "(no info available)"

                    result.append((attr[4:], doc))

            for info in sorted(result):
                self.terminal.write("  %s - %s\n" % (info))

    def cmd_pwd(self, tokens):
        """
        Print working directory.

        usage: pwd
        """
        if tokens:
            raise UnknownArguments(tokens)

        self.terminal.write("%s\n" % (self.wd,))

    @inlineCallbacks
    def cmd_cd(self, tokens):
        """
        Change working directory.

        usage: cd [directory]
        """
        if not tokens:
            return

        dirname = tokens.pop(0)

        if tokens:
            raise UnknownArguments(tokens)

        wd = (yield self.wd.locate(dirname.split("/")))

        if not isinstance(wd, Directory):
            raise NotFoundError("Not a directory: %s" % (wd,))

        log.msg("wd -> %s" % (wd,))
        self.wd = wd

    @inlineCallbacks
    def cmd_ls(self, tokens):
        """
        List directory contents.

        usage: ls [directory]
        """
        target = (yield self._getTarget(tokens))

        if tokens:
            raise UnknownArguments(tokens)

        listing = (yield target.list())

        #
        # FIXME: this can be ugly if, for example, there are
        # zillions of calendar homes or events to output. Paging
        # would be good.
        #
        for name in listing:
            self.terminal.write("%s\n" % (name,))

    @inlineCallbacks
    def cmd_info(self, tokens):
        """
        Print information about a directory.

        usage: info [directory]
        """
        target = (yield self._getTarget(tokens))

        if tokens:
            raise UnknownArguments(tokens)

        description = (yield target.describe())
        self.terminal.write(description)
        self.terminal.nextLine()

    def cmd_exit(self, tokens):
        """
        Exit the shell.

        usage: exit
        """
        self.exit()

    def cmd_python(self, tokens):
        """
        Switch to a python prompt.

        usage: python
        """
        # Crazy idea #19568: switch to an interactive python prompt
        # with self exposed in globals.
        raise NotImplementedError()


class File(object):
    """
    Object in virtual data hierarchy.
    """
    def __init__(self, store, path):
        assert type(path) is tuple

        self.store = store
        self.path = path

    def __str__(self):
        return "/" + "/".join(self.path)

    def describe(self):
        return succeed("%s (%s)" % (self, self.__class__.__name__))

    def list(self):
        return succeed(("%s" % (self,),))


class Directory(File):
    """
    Location in virtual data hierarchy.
    """
    @inlineCallbacks
    def locate(self, path):
        if not path:
            returnValue(RootDirectory(self.store))

        name = path[0]
        if name:
            target = (yield self.child(name))
            if len(path) > 1:
                target = (yield target.locate(path[1:]))
        else:
            target = (yield RootDirectory(self.store).locate(path[1:]))

        returnValue(target)

    def child(self, name):
        #log.msg("child(%r)" % (name,))
        if not name:
            return succeed(self)
        if name == ".":
            return succeed(self)
        if name == "..":
            path = self.path[:-1]
            if not path:
                path = "/"
            return RootDirectory(self.store).locate(path)

        raise NotFoundError("Directory %r has no child %r" % (str(self), name))

    def list(self):
        raise NotImplementedError("%s.list() isn't implemented." % (self.__class__.__name__,))


class RootDirectory(Directory):
    """
    Root of virtual data hierarchy.
    """
    def __init__(self, store):
        Directory.__init__(self, store, ())

        self._children = {}

        self._childClasses = {
            "uids": UIDDirectory,
        }

    def child(self, name):
        if name in self._children:
            return succeed(self._children[name])

        if name in self._childClasses:
            self._children[name] = self._childClasses[name](self.store, self.path + (name,))
            return succeed(self._children[name])

        return Directory.child(self, name)

    def list(self):
        return succeed(("%s/" % (n,) for n in self._childClasses))


class UIDDirectory(Directory):
    """
    Directory containing all principals by UID.
    """
    @inlineCallbacks
    def child(self, name):
        txn  = self.store.newTransaction()
        home = (yield txn.calendarHomeWithUID(name))

        if home:
            returnValue(CalendarHomeDirectory(self.store, self.path + (name,), home))
        else:
            raise NotFoundError("No calendar home for UID %r" % (name,))

    @inlineCallbacks
    def list(self):
        result = []

        for txn, home in (yield self.store.eachCalendarHome()):
            result.append("%s/" % (home.uid(),))

        returnValue(result)


class CalendarHomeDirectory(Directory):
    """
    Home directory.
    """
    def __init__(self, store, path, home):
        Directory.__init__(self, store, path)

        self.home = home

    @inlineCallbacks
    def describe(self):
        # created() -> int
        # modified() -> int
        # properties -> IPropertyStore

        uid          = (yield self.home.uid())
        created      = (yield self.home.created())
        modified     = (yield self.home.modified())
        quotaUsed    = (yield self.home.quotaUsedBytes())
        quotaAllowed = (yield self.home.quotaAllowedBytes())
        properties   = (yield self.home.properties())

        result = []
        result.append("Calendar home for UID: %s" % (uid,))
        if created is not None:
            # FIXME: convert to string
            result.append("Created: %s" % (created,))
        if modified is not None:
            # FIXME: convert to string
            result.append("Last modified: %s" % (modified,))
        if quotaUsed is not None:
            result.append("Quota: %s of %s (%.2s%%)"
                          % (quotaUsed, quotaAllowed, quotaUsed / quotaAllowed))

        if properties:
            for name in sorted(properties):
                result.append("%s: %s" % (name, properties[name]))

        returnValue("\n".join(result))

    @inlineCallbacks
    def child(self, name):
        calendar = (yield self.home.calendarWithName(name))
        if calendar:
            returnValue(CalendarDirectory(self.store, self.path + (name,), calendar))
        else:
            raise NotFoundError("Calendar home %r has no calendar %r" % (self, name))

    @inlineCallbacks
    def list(self):
        calendars = (yield self.home.calendars())
        returnValue(("%s/" % (c.name(),) for c in calendars))


class CalendarDirectory(Directory):
    """
    Calendar.
    """
    def __init__(self, store, path, calendar):
        Directory.__init__(self, store, path)

        self.calendar = calendar

    @inlineCallbacks
    def _childWithObject(self, object):
        name = (yield object.uid())
        returnValue(CalendarObject(self.store, self.path + (name,), object))

    @inlineCallbacks
    def child(self, name):
        object = (yield self.calendar.calendarObjectWithUID(name))

        if not object:
            raise NotFoundError("Calendar %r has no object %r" % (str(self), name))

        child = (yield self._childWithObject(object))
        returnValue(child)

    @inlineCallbacks
    def list(self):
        result = []

        for object in (yield self.calendar.calendarObjects()):
            object = (yield self._childWithObject(object))
            items = (yield object.list())
            assert len(items) == 1
            result.append(items[0])

        returnValue(result)


class CalendarObject(File):
    """
    Calendar object.
    """
    def __init__(self, store, path, calendarObject):
        File.__init__(self, store, path)

        self.object = calendarObject

    @inlineCallbacks
    def list(self):
        component = (yield self.object.component())
        mainComponent = component.mainComponent()
        componentType = mainComponent.name()
        uid = mainComponent.propertyValue("UID")
        summary = mainComponent.propertyValue("SUMMARY")

        assert uid == self.object.uid()
        assert componentType == (yield self.object.componentType())

        returnValue(("%s %s: %s" % (uid, componentType, summary),))


def main(argv=sys.argv, stderr=sys.stderr, reactor=None):
    """
    Do the export.
    """
    if reactor is None:
        from twisted.internet import reactor

    options = ShellOptions()
    try:
        options.parseOptions(argv[1:])
    except UsageError, e:
        usage(e)

    def makeService(store):
        from twistedcaldav.config import config
        return ShellService(store, options, reactor, config)

    print "Initializing shell..."

    utilityMain(options["config"], makeService, reactor)
