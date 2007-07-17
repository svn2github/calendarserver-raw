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

import sys
import getopt

from calendarclient import CalendarClient

def usage():
    print """Usage: simulate [options]
Options:
    --server          URL for server (e.g. https://caldav.example.com:8443) [Required]
    --user            user id for user to login as [Required]
    --password        password for user [Required]
    --interval        polling interval in seconds [15 mins]
    --eventsperday    number of events per day to create [10]
    --invitesperday   number of invites per day to send  [5]
    --cache           path to .plist file to cache data [Optional]
    --clear-cache     clear the cache when starting up [Optional]
    --verbose         print out activity log
    
    -h, --help        print this help and exit
"""


if __name__ == '__main__':

    client = CalendarClient()
    
    options, args = getopt.getopt(sys.argv[1:], "h", [
        "server=",
        "user=",
        "password=",
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
        elif option == "--server":
            client.server = value
        elif option == "--user":
            client.user = value
        elif option == "--password":
            client.password = value
        elif option == "--interval":
            client.interval = int(value)
        elif option == "--eventsperday":
            client.eventsperday = int(value)
        elif option == "--invitesperday":
            client.invitesperday = int(value)
        elif option == "--cache":
            client.cache = value
        elif option == "--verbose":
            client.verbose = True
        elif option == "--clear-cache":
            client.clearcache = True
        else:
            print "Unrecognized option: %s" % (option,)
            usage()
            raise ValueError

    if not client.valid():
        print "Required option is missing."
        usage()
        raise ValueError
    
    client.simulate()
