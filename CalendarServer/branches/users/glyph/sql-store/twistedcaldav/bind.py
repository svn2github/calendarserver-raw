##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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
Bind methods.
Have to have this in a separate module for now.
"""

from twext.web2.dav.util import bindMethods

##
# Attach methods
##

def doBind():
    import twext.web2.dav.method
    from twext.web2.dav.resource import DAVResource
    bindMethods(twext.web2.dav.method, DAVResource)

    import twistedcaldav.method
    from twistedcaldav.resource import CalDAVResource
    bindMethods(twistedcaldav.method, CalDAVResource)
