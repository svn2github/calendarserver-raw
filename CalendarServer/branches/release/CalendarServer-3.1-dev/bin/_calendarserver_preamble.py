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
This module is the shared preamble for all calendarserver shell commands to set
up their environment properly.  It's explicitly not installed along with the
code, and is used only to initialize the environment for a development checkout
of the code.
"""

import sys
from os.path import dirname, abspath, join, split, exists
from subprocess import Popen, PIPE

def bootstrapFromRun():
    """
    If this is a development checkout, run the 'run' script to discover the
    correct value for sys.path.
    """

    home = dirname(dirname(abspath(__file__)))
    run = join(home, "run")

    if not exists(run) or not exists(join(home, "setup.py")):
        # This doesn't look enough like a development checkout; let's not
        # attempt to run the run script.
        return

    child = Popen((run, "-p"), stdout=PIPE)
    path, stderr = child.communicate()

    path = path.rstrip("\n")

    if child.wait() == 0:
        sys.path[0:0] = path.split(":")

    noConfigOption = [
        "calendarserver_bootstrap_database",
        "calendarserver_load_augmentdb",
        "calendarserver_make_partition",
        "calendarserver_manage_augments",
        "calendarserver_manage_postgres",
        "calendarserver_manage_timezones",
        "icalendar_split",
    ]

    if split(sys.argv[0])[-1] not in noConfigOption:
        sys.argv[1:1] = ["-f", join(home, "conf", "caldavd-dev.plist")]


bootstrapFromRun()

