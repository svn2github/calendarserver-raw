##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
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

import xattr

from twisted.web2.dav.resource import TwistedQuotaRootProperty, TwistedQuotaUsedProperty
from twisted.web import microdom

quotaRoot = "WebDAV:" + TwistedQuotaRootProperty.sname().replace("/", "%2F")
quotaUsed = "WebDAV:" + TwistedQuotaUsedProperty.sname().replace("/", "%2F")

def getQuotaRoot(fp):
    x = xattr.xattr(fp.path)
    if not x.has_key(quotaRoot):
        return None

    dom = microdom.parseString(x[quotaRoot])

    qr = microdom.getElementsByTagName(dom, 'quota-root')[0]

    return int(qr.firstChild().value)


def getQuotaUsed(fp):
    x = xattr.xattr(fp.path)
    if not x.has_key(quotaUsed):
        return None

    dom = microdom.parseString(x[quotaUsed])

    qu = microdom.getElementsByTagName(dom, 'quota-used')[0]

    return int(qu.firstChild().value)
        

class QuotaAction(object):
    def __init__(self, config):
        self.config = config
        self.userQuotaBytes = config.parent.config['UserQuotaBytes']
        self.calendarCollection = config.parent.calendarCollection
        self.principalCollection = config.parent.principalCollection

    def getQuotaStats(self):

        defaultQuota = getQuotaRoot(self.calendarCollection)
        if not defaultQuota:
            defaultQuota = self.userQuotaBytes

        for type in self.config['types']:

            typeRoot = self.calendarCollection.child(type)

            typePrincipals = self.principalCollection.child(type)

            if not typeRoot.exists() or not typePrincipals.exists():
                continue

            typeQuota = getQuotaRoot(typeRoot)
            if not typeQuota:
                typeQuota = defaultQuota
            
            for child in typePrincipals.listdir():
                if child in ['.db.sqlite']:
                    continue

                child = typeRoot.child(child)                

                childQuota = getQuotaRoot(child)
                if not childQuota:
                    childQuota = typeQuota
                
                childUsed = getQuotaUsed(child)
                if not childUsed:
                    childUsed = 0

                childAvailable = childQuota - childUsed

                yield (type,
                       child.basename(),
                       childQuota,
                       childUsed,
                       childAvailable)
    
    def run(self):
        for x in self.getQuotaStats():
            print x
