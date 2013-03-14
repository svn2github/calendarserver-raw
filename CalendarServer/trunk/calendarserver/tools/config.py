#!/usr/bin/env python

##
# Copyright (c) 2010-2013 Apple Inc. All rights reserved.
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
This tool gets and sets Calendar Server configuration keys
"""

from getopt import getopt, GetoptError
import os
import plistlib
import signal
import sys
import xml

from twext.python.plistlib import readPlistFromString, writePlistToString
from twistedcaldav.config import config, ConfigDict, ConfigurationError, mergeData
from twistedcaldav.stdconfig import DEFAULT_CONFIG_FILE
WRITABLE_CONFIG_KEYS = [
    "EnableSSL",
    "RedirectHTTPToHTTPS",
    "EnableCalDAV",
    "EnableCardDAV",
    "DataRoot",
    "SSLCertificate",
    "SSLPrivateKey",
    "SSLAuthorityChain",
    "EnableSearchAddressBook",
    "Authentication.Basic.Enabled",
    "Authentication.Basic.AllowedOverWireUnencrypted",
    "Authentication.Digest.Enabled",
    "Authentication.Digest.AllowedOverWireUnencrypted",
    "Authentication.Kerberos.Enabled",
    "Authentication.Kerberos.AllowedOverWireUnencrypted",
    "Authentication.Wiki.Enabled",
    "Scheduling.iMIP.Enabled",
    "Scheduling.iMIP.Receiving.Username",
    "Scheduling.iMIP.Receiving.Server",
    "Scheduling.iMIP.Receiving.Port",
    "Scheduling.iMIP.Receiving.Type",
    "Scheduling.iMIP.Receiving.UseSSL",
    "Scheduling.iMIP.Sending.Username",
    "Scheduling.iMIP.Sending.Server",
    "Scheduling.iMIP.Sending.Port",
    "Scheduling.iMIP.Sending.UseSSL",
    "Scheduling.iMIP.Sending.Address",
    "Notifications.Services.APNS.Enabled",
    "Notifications.Services.APNS.CalDAV.CertificatePath",
    "Notifications.Services.APNS.CalDAV.AuthorityChainPath",
    "Notifications.Services.APNS.CalDAV.PrivateKeyPath",
    "Notifications.Services.APNS.CardDAV.CertificatePath",
    "Notifications.Services.APNS.CardDAV.AuthorityChainPath",
    "Notifications.Services.APNS.CardDAV.PrivateKeyPath",
]

def usage(e=None):
    if e:
        print(e)
        print("")

    name = os.path.basename(sys.argv[0])
    print("usage: %s [options] config_key" % (name,))
    print("")
    print("Print the value of the given config key.")
    print("options:")
    print("  -h --help: print this help and exit")
    print("  -f --config: Specify caldavd.plist configuration path")
    print("  -w --writeconfig: Specify caldavd.plist configuration path for writing")

    if e:
        sys.exit(64)
    else:
        sys.exit(0)

def main():
    try:
        (optargs, args) = getopt(
            sys.argv[1:], "hf:w:", [
                "help",
                "config=",
                "writeconfig=",
            ],
        )
    except GetoptError, e:
        usage(e)

    configFileName = DEFAULT_CONFIG_FILE
    writeConfigFileName = ""

    for opt, arg in optargs:
        if opt in ("-h", "--help"):
            usage()

        elif opt in ("-f", "--config"):
            configFileName = arg

        elif opt in ("-w", "--writeconfig"):
            writeConfigFileName = arg

    try:
        config.load(configFileName)
    except ConfigurationError, e:
        sys.stdout.write("%s\n" % (e,))
        sys.exit(1)

    if not writeConfigFileName:
        # If --writeconfig was not passed, use WritableConfigFile from
        # main plist.  If that's an empty string, writes will happen to
        # the main file.
        writeConfigFileName = config.WritableConfigFile

    if not writeConfigFileName:
        writeConfigFileName = configFileName

    writable = WritableConfig(config, writeConfigFileName)
    writable.read()

    if args:
        for configKey in args:

            if "=" in configKey:
                # This is an assignment
                configKey, stringValue = configKey.split("=")
                value = writable.convertToValue(stringValue)
                writable.set({configKey:value})
            else:
                # This is a read
                c = config
                for subKey in configKey.split("."):
                    c = c.get(subKey, None)
                    if c is None:
                        sys.stderr.write("No such config key: %s\n" % configKey)
                        break
                sys.stdout.write("%s=%s\n" % (configKey, c))

        writable.save(restart=True)

    else:
        # Read plist commands from stdin
        rawInput = sys.stdin.read()
        try:
            plist = readPlistFromString(rawInput)
        except xml.parsers.expat.ExpatError, e:
            respondWithError(str(e))
            return

        # If the plist is an array, each element of the array is a separate
        # command dictionary.
        if isinstance(plist, list):
            commands = plist
        else:
            commands = [plist]

        runner = Runner(commands)
        runner.run()



class Runner(object):

    """
    A class which carries out commands, which are plist strings containing
    dictionaries with a "command" key, plus command-specific data.
    """

    def __init__(self, commands):
        """
        @param commands: the commands to run
        @type commands: list of plist strings
        """
        self.commands = commands

    def validate(self):
        """
        Validate all the commands by making sure this class implements
        all the command keys.
        @return: True if all commands are valid, False otherwise
        """
        # Make sure commands are valid
        for command in self.commands:
            if 'command' not in command:
                respondWithError("'command' missing from plist")
                return False
            commandName = command['command']
            methodName = "command_%s" % (commandName,)
            if not hasattr(self, methodName):
                respondWithError("Unknown command '%s'" % (commandName,))
                return False
        return True

    def run(self):
        """
        Find the appropriate method for each command and call them.
        """
        try:
            for command in self.commands:
                commandName = command['command']
                methodName = "command_%s" % (commandName,)
                if hasattr(self, methodName):
                    getattr(self, methodName)(command)
                else:
                    respondWithError("Unknown command '%s'" % (commandName,))

        except Exception, e:
            respondWithError("Command failed: '%s'" % (str(e),))
            raise

    def command_readConfig(self, command):
        """
        Return current configuration

        @param command: the dictionary parsed from the plist read from stdin
        @type command: C{dict}
        """
        result = {}
        for keyPath in WRITABLE_CONFIG_KEYS:
            value = getKeyPath(config, keyPath)
            if value is not None:
                setKeyPath(result, keyPath, value)
        respond(command, result)

    def command_writeConfig(self, command):
        """
        Write config to secondary, writable plist

        @param command: the dictionary parsed from the plist read from stdin
        @type command: C{dict}
        """
        writable = WritableConfig(config, config.WritableConfigFile)
        writable.read()
        valuesToWrite = command.get("Values", {})
        for keyPath, value in flattenDictionary(valuesToWrite):
            if keyPath in WRITABLE_CONFIG_KEYS:
                writable.set(setKeyPath(ConfigDict(), keyPath, value))
        try:
            writable.save(restart=False)
        except Exception, e:
            respond(command, {"error": str(e)})
        else:
            config.reload()
            self.command_readConfig(command)


def setKeyPath(parent, keyPath, value):
    """
    Allows the setting of arbitrary nested dictionary keys via a single
    dot-separated string.  For example, setKeyPath(parent, "foo.bar.baz",
    "xyzzy") would create any intermediate missing directories (or whatever
    class parent is, such as ConfigDict) so that the following structure
    results:  parent = { "foo" : { "bar" : { "baz" : "xyzzy } } }

    @param parent: the object to modify
    @type parent: any dict-like object
    @param keyPath: a dot-delimited string specifying the path of keys to
        traverse
    @type keyPath: C{str}
    @param value: the value to set
    @type value: c{object}
    @return: parent
    """
    original = parent
    parts = keyPath.split(".")
    for part in parts[:-1]:
        child = parent.get(part, None)
        if child is None:
            parent[part] = child = parent.__class__()
        parent = child
    parent[parts[-1]] = value
    return original

def getKeyPath(parent, keyPath):
    """
    Allows the getting of arbitrary nested dictionary keys via a single
    dot-separated string.  For example, getKeyPath(parent, "foo.bar.baz")
    would fetch parent["foo"]["bar"]["baz"].  If any of the keys don't
    exist, None is returned instead.

    @param parent: the object to traverse
    @type parent: any dict-like object
    @param keyPath: a dot-delimited string specifying the path of keys to
        traverse
    @type keyPath: C{str}
    @return: the value at keyPath
    """
    parts = keyPath.split(".")
    for part in parts[:-1]:
        child = parent.get(part, None)
        if child is None:
            return None
        parent = child
    return parent.get(parts[-1], None)

def flattenDictionary(dictionary, current=""):
    """
    Returns a generator of (keyPath, value) tuples for the given dictionary,
    where each keyPath is a dot-separated string representing the complete
    path to a nested key.

    @param dictionary: the dict object to traverse
    @type dictionary: C{dict}
    @param current: do not use; used internally for recursion
    @type current: C{str}
    @return: generator of (keyPath, value) tuples
    """
    for key, value in dictionary.iteritems():
        if isinstance(value, dict):
            for result in flattenDictionary(value, current + key + "."):
                yield result
        else:
            yield (current + key, value)


def restartService(pidFilename):
    """
    Given the path to a PID file, sends a HUP signal to the contained pid
    in order to cause calendar server to restart.

    @param pidFilename: an absolute path to a PID file
    @type pidFilename: C{str}
    """
    if os.path.exists(pidFilename):
        pidFile = open(pidFilename, "r")
        pid = pidFile.read().strip()
        pidFile.close()
        try:
            pid = int(pid)
        except ValueError:
            return
        try:
            os.kill(pid, signal.SIGHUP)
        except OSError:
            pass


class WritableConfig(object):
    """
    A wrapper around a Config object which allows writing of values.  The idea
    is a deployment could have a master plist which doesn't change, and have
    it include a plist file which does.  This class facilitates writing to that
    included plist.
    """

    def __init__(self, wrappedConfig, fileName):
        """
        @param wrappedConfig: the Config object to read from
        @type wrappedConfig: C{Config}
        @param fileName: the full path to the modifiable plist
        @type fileName: C{str}
        """
        self.config = wrappedConfig
        self.fileName = fileName
        self.changes = None
        self.currentConfigSubset = ConfigDict()
        self.dirty = False

    def set(self, data):
        """
        Merges data into a ConfigDict of changes intended to be saved to disk
        when save( ) is called.

        @param data: a dict containing new values
        @type data: C{dict}
        """
        if not isinstance(data, ConfigDict):
            data = ConfigDict(mapping=data)
        mergeData(self.currentConfigSubset, data)
        self.dirty = True

    def read(self):
        """
        Reads in the data contained in the writable plist file.

        @return: C{ConfigDict}
        """
        if os.path.exists(self.fileName):
            self.currentConfigSubset = ConfigDict(mapping=plistlib.readPlist(self.fileName))
        else:
            self.currentConfigSubset = ConfigDict()

    def toString(self):
        return plistlib.writePlistToString(self.currentConfigSubset)

    def save(self, restart=False):
        """
        Writes any outstanding changes to the writable plist file.  Optionally
        restart calendar server.

        @param restart: whether to restart the calendar server.
        @type restart: C{bool}
        """
        if self.dirty:
            plistlib.writePlist(self.currentConfigSubset, self.fileName)
            self.dirty = False
            if restart:
                restartService(self.config.PIDFile)

    @classmethod
    def convertToValue(cls, string):
        """
        Inspect string and convert the value into an appropriate Python data type
        TODO: change this to look at actual types definied within stdconfig
        """
        if "." in string:
            try:
                value = float(string)
            except ValueError:
                value = string
        else:
            try:
                value = int(string)
            except ValueError:
                if string == "True":
                    value = True
                elif string == "False":
                    value = False
                else:
                    value = string
        return value


def respond(command, result):
    sys.stdout.write(writePlistToString({'command' : command['command'], 'result' : result}))

def respondWithError(msg, status=1):
    sys.stdout.write(writePlistToString({'error' : msg, }))
