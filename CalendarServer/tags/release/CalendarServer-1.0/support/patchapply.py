#!/usr/bin/env python

##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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

import os
import sys

#
# Apply patches to dependent projects.
#

#projects = ("Twisted", "vobject", "dateutil", "xattr")
projects = ("Twisted", "vobject",)
cwd = os.getcwd()
libpatches = os.path.join(cwd, "lib-patches")

cmd = "/usr/bin/patch"

def applypatch(project, patch):
    stat = os.system("%s -s -d ../%s/ -p0 --forward --dry-run -i %s > /dev/null" % (cmd, project, patch, ))
    if stat == 0:
        print "+++ Patching %s with %s" % (project, patch[len(cwd) + 1:],)
        os.system("%s -s -d ../%s/ -p0 --forward -i %s" % (cmd, project, patch, ))
    else:
        print "*** Failed to patch %s with %s" % (project, patch[len(cwd) + 1:],)

def applypatches(project):
    
    # Iterate over each patch file in the patches directory
    path = os.path.join(libpatches, project)
    for file in os.listdir(path):
        fpath = os.path.join(path, file)
        if os.path.isfile(fpath) and fpath.endswith(".patch"):
            applypatch(project, fpath)

if __name__ == "__main__":

    try:
        for project in projects:
            applypatches(project)
    except Exception, e:
        sys.exit(str(e))
