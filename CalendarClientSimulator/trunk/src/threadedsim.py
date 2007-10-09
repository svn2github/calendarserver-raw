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

from random import randint
from clientscheduler import ClientScheduler
from calendarclient import CalendarClient
import time
import sys
import getopt

# Simulate a whole bunch of users

def usage():
    print """Usage: threadedsim [options]
Options:
    --pool            number of threads processing clients [10]
    --number          number of users to simulate [10]
    --startat         user number to start at [1]
    --server          URL for server (e.g. https://caldav.example.com:8443) [Required]
    --user            user id for user to login as [user%02d]
    --password        password for user [user%02d]
    --interval        polling interval in seconds [15 mins]
    --eventsperday    number of events per day to create [10]
    --invitesperday   number of invites per day to send  [5]
    --cache           path to .plist file to cache data [../data/user%02d.plist]
    --clear-cache     clear the cache when starting up [Optional]
    --no-throttle     do not throttle the task queue when scheduler is too busy [Optional]
    --verbose         print out activity log
    --logging         log activity
    
    -h, --help        print this help and exit
"""


if __name__ == '__main__':

    pool = 10
    count = 10
    startat = 1
    server = None
    user = "user%02d"
    password = "user%02d"
    interval = 15 * 60
    eventsperday = 10
    invitesperday = 5
    cache = "../data/user%02d.plist"
    clearcache = False
    throttle = True
    verbose = False
    logging = False
    logfile = "../logs/user%02d.txt"
    
    options, args = getopt.getopt(sys.argv[1:], "h", [
        "pool=",
        "number=",
        "startat=",
        "server=",
        "interval=",
        "eventsperday=",
        "invitesperday=",
        "cache=",
        "clear-cache",
        "no-throttle",
        "verbose",
        "logging",
        "help"
    ])

    for option, value in options:
        if option in ("-h", "--help"):
            usage()
            sys.exit(0)
        elif option == "--pool":
            pool = int(value)
        elif option == "--number":
            count = int(value)
        elif option == "--startat":
            startat = int(value)
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
        elif option == "--no-throttle":
            throttle = False
        elif option == "--verbose":
            verbose = True
        elif option == "--logging":
            logging = True
        elif option == "--clear-cache":
            clearcache = True
        else:
            print "Unrecognized option: %s" % (option,)
            usage()
            raise ValueError

    if server is None:
        usage()
        raise ValueError
        
    # Create the scheduler that will manage the thread pool and client polling
    scheduler = ClientScheduler(num_threads=pool, throttle=throttle)
    scheduler.run()
    
    # Create clients and add to scheduler
    for i in range(startat, count + startat):
        client = CalendarClient()
        client.server = server
        client.user = user % (i,)
        client.password = password % (i,)
        client.interval = interval
        client.eventsperday = eventsperday
        client.invitesperday = invitesperday
        if cache:
            client.cache = cache % (i,)
        if clearcache:
            client.clearcache = True
        if verbose:
            client.verbose = True
        if logging:
            def logIt(text):
                logger = open(logfile % (i,), "a")
                logger.write(text + "\n")
                
            client.setLogger(logIt)

        # Add random delay
        delay = randint(1,1000)
        time.sleep(delay/1000.0)
        
        client.valid()
        scheduler.add(client)
    
    killit = raw_input("Press <RETURN> to cancel all simulations.")
    scheduler.stop()
