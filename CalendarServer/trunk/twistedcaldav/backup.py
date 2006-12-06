##
# Copyright (c) 2005-2006 Apple Computer, Inc. All rights reserved.
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
# DRI: David Reid, dreid@apple.com
##

"""
Utility code for backup and restore
"""

import os
import shutil
import fnmatch
import plistlib
import tempfile
import commands

VERBOSE = os.environ.get('VERBOSE', False)
FUNCLOG = os.environ.get('FUNCLOG', False)

SERVERADMIN = "/usr/sbin/serveradmin"

SBSCONF = "/private/etc/sbs_backup"

class Options(dict):
    def parseOpts(self, argv):
        for x in xrange(0, len(argv)):
            opt = argv[x]
            if opt.startswith('-'):
                self[opt.strip('-')] = argv[x+1]


def debug(string):
    if VERBOSE:
        print "DEBUG:", string


def funclog(string):
    if FUNCLOG:
        print "FUNCLOG:", string


def logFuncCall(func):
    def printArgs(args):
        a = []
        for arg in args:
            a.append(repr(arg))
            a.append(', ')

        return ''.join(a).strip(', ')

    def printKwargs(kwargs):
        a = []
        for kwarg, value in kwargs:
            a.append('%s=%r, ' % (kwarg, value))

        return ''.join(a).strip(', ')

    def _(*args, **kwargs):
        funclog("%s(%s)" % (func.func_name, 
                            ', '.join((printArgs(args),
                                       printKwargs(kwargs))).strip(', ')))

        retval = func(*args, **kwargs)

        funclog("%s - > %s" % (func.func_name, retval))

        return retval
    
    return _


@logFuncCall
def readConfig(configFile):
    config = plistlib.readPlist(configFile + '.default')

    if os.path.exists(configFile):
        config.update(plistlib.readPlist(configFile))

    return config
        

@logFuncCall
def mkroot(path):
    root = '/'.join(path.rstrip('/').split('/')[:-1])
    os.makedirs(root)


@logFuncCall
def serveradmin(action, service):
    cmd = ' '.join((
            SERVERADMIN,
            action,
            service))

    status, output = commands.getstatusoutput(cmd)

    for line in output.split('\n'):
        debug("C: %s" % (line,))

    return status


@logFuncCall
def isRunning(service):
    cmd = ' '.join((
            SERVERADMIN,
            'status',
            service))

    debug(cmd)

    output = commands.getoutput(cmd)

    for line in output.split('\n'):
        debug("C: %s" % (line,))

    status = output.split('=')[-1].strip(' "\n')

    if status == "RUNNING":
        return True
    else:
        return False


@logFuncCall
def copy(src, dst):
    shutil.copytree(src, dst)


@logFuncCall
def move(src, dst):
    os.rename(src, dst)


@logFuncCall
def remove(dst):
    shutil.rmtree(dst)


@logFuncCall
def purge(root, patterns):
    removed = []

    for root, dirs, files in os.walk(root):
        debug("purging in %s" % (root,))

        for file in files:
            for pat in patterns:
                if fnmatch.fnmatch(file, pat):
                    full = os.path.join(root, file)
                    debug("removing %s because of %s" % (full, pat))

                    os.remove(full)

                    removed.append(full)

        for dir in dirs:
            for pat in patterns:
                if fnmatch.fnmatch(dir, pat):
                    full = os.path.join(root, dir)
                    debug("removing %s because of %s" % (full, pat))

                    os.remove(full)

                    removed.append(full)
                    
    return removed
