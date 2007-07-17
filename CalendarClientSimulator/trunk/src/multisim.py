#!/usr/bin/env python
#
##
# Copyright (c) 2007 Apple Inc. All rights reserved.
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
from os import P_NOWAIT
from random import randint
import time
import signal
import os
import sys
import getopt

# Simulate a whole bunch of users

def usage():
    print """Usage: simulate [options]
Options:
    --number          number of users to simulate [10]
    --server          URL for server (e.g. https://caldav.example.com:8443) [Required]
    --user            user id for user to login as [user%02d]
    --password        password for user [user%02d]
    --interval        polling interval in seconds [15 mins]
    --eventsperday    number of events per day to create [10]
    --invitesperday   number of invites per day to send  [5]
    --cache           path to .plist file to cache data [../data/user%02d.plist]
    --clear-cache     clear the cache when starting up [Optional]
    --verbose         print out activity log
    
    -h, --help        print this help and exit
"""


if __name__ == '__main__':

    count = 5
    server = None
    user = "user%02d"
    password = "user%02d"
    interval = 15 * 60
    eventsperday = 10
    invitesperday = 5
    cache = "../data/user%02d.plist"
    clearcache = False
    verbose = False
    
    options, args = getopt.getopt(sys.argv[1:], "h", [
        "number=",
        "server=",
        "interval=",
        "eventsperday=",
        "invitesperday=",
        "cache=",
        "clear-cache",
        "verbose",
        "help"
    ])

    for option, value in options:
        if option in ("-h", "--help"):
            usage()
            sys.exit(0)
        elif option == "--number":
            count = int(value)
        elif option == "--server":
            server = value
        elif option == "--user":
            user = value
        elif option == "--password":
            password = value
        elif option == "--interval":
            interval = int(value)
        elif option == "--eventsperday":
            eventsperday = int(value)
        elif option == "--invitesperday":
            invitesperday = int(value)
        elif option == "--cache":
            cache = value
        elif option == "--verbose":
            verbose = True
        elif option == "--clear-cache":
            clearcache = True
        else:
            print "Unrecognized option: %s" % (option,)
            usage()
            raise ValueError

    pids = []
    for i in range(1, count + 1):
        cmd = [
            "python",
            "./simulate.py",
            "--server",
            server,
            "--user",
            user % (i,),
            "--password",
            password % (i,),
            "--interval",
            "%d" % (interval,),
            "--eventsperday",
            "%d" % (eventsperday,),
            "--invitesperday",
            "%d" % (invitesperday,),
        ]
        if cache:
            cmd.extend(["%s", "--cache", cache % (i,),])
        if clearcache:
            cmd.append("--clear-cache")
        if verbose:
            cmd.append("--verbose")

        # Add random delay
        delay = randint(1,1000)
        time.sleep(delay/1000.0)
        pids.append(os.spawnvp(P_NOWAIT, "python", cmd))

    killit = raw_input("Press <RETURN> to cancel all simulations.")
    
    for pid in pids:
        os.kill(pid, signal.SIGKILL)
