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

Usage: simulate [options]
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


This tool simulates a typical CalDAV client talking to a CalDAV server.

Actions are:

- Polling:
	- A PROPFIND Depth:1 on calendar home looking for getctag
	- For each non-matching calendar or inbox:
		- A PROPFIND Depth:1 on the collection.
		- A multiget report to get new or changed items.
		- For inbox - iTIP handling is done (not implemented yest).
		
    The --interval option controls the polling interval in minutes.
    
- Create regular events:
	- A PUT of a new 1 hour event starting at the current time.
	
	The --eventsperday option controls the frequency of these.
	
- Create invite events:
	- A PUT of a new 1 hour event starting at the current time for between 1 and 10 random attendees.
	- A free-busy lookup for those attendees and the organizer.
	- A PUT on top of the previous one to simulate a change as a result of free-busy lookup (not implemented yet).
	- A schedule REQUEST sent to all attendees.
	
	The --invitesperday option controls the frequency of these.
	
The tool can maintain a cache of the calendar data betwen runs, so that a subsequent start-up can be treated as
a client "warm" start rather than a "cold" start. The difference being that a "cold" start requires downloading
all the calendar data in the first poll, whereas a "warm" start only downloads the changes. The --clear-cache
option determines whether the existing cache data is used on startup - so when set forces a "cold" start. A
separate cache file must be used for each user being simulated.

In this implementation the tool sleeps for one minute, then checks the exiry of the three intervals to determine
if an action should be executed. In the longer term we should allow multiple client instances to be run from
one tool and have a queue of actions set to occur at specific times and a thread pool to service those.

Prequisites: a CalDAV server with user01 .. user99 as user accounts. Those will be used to create the attendees
in invites. You can have more users with other names that are used as the simulation user, but they will never
receive invites (right now).

The tool was designed with the idea that it could be called by another script that can then run multiple instances
to simulate multiple users. That script could even run the tool on different machines for a distributed test.
