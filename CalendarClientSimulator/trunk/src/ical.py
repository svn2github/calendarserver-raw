#!/usr/bin/env python

import sys
import getopt

from calendarclient import CalendarClient

def usage():
    print """Usage: ical [options]
Options:
    --server          URL for server (e.g. https://caldav.example.com:8443) [Required]
    --user            user id for user to login as [Required]
    --password        password for user [Required]
    --interval        polling interval in minutes [15]
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
            client.interval = value
        elif option == "--eventsperday":
            client.eventsperday = value
        elif option == "--invitesperday":
            client.invitesperday = value
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
