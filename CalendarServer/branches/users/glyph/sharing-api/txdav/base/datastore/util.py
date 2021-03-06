# -*- test-case-name: txdav.caldav.datastore.test.test_file -*-
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
Common utility functions for a datastores.
"""

from twistedcaldav.memcacher import Memcacher

_unset = object()

class cached(object):
    """
    This object is a decorator for a 0-argument method which should be called
    only once, and its result cached so that future invocations just return the
    same result without calling the underlying method again.

    @ivar thunk: the function to call to generate a cached value.
    """

    def __init__(self, thunk):
        self.thunk = thunk


    def __get__(self, oself, owner):
        def inner():
            cacheKey = "_" + self.thunk.__name__ + "_cached"
            cached = getattr(oself, cacheKey, _unset)
            if cached is _unset:
                value = self.thunk(oself)
                setattr(oself, cacheKey, value)
                return value
            else:
                return cached
        return inner


class QueryCacher(Memcacher):
    """
    A Memcacher for the object-with-name query (more to come)
    """

    def __init__(self, cachePool="Default", cacheExpireSeconds=3600):
        super(QueryCacher, self).__init__(cachePool, pickle=True)
        self.cacheExpireSeconds = cacheExpireSeconds

    def set(self, key, value):
        return super(QueryCacher, self).set(key, value, expireTime=self.cacheExpireSeconds)

    def delete(self, key):
        return super(QueryCacher, self).delete(key)


    def setAfterCommit(self, transaction, key, value):
        transaction.postCommit(lambda: self.set(key, value), immediately=True)

    def invalidateAfterCommit(self, transaction, key):
        # Invalidate now (so that operations within this transaction see it)
        # and *also* post-commit (because there could be a scheduled setAfterCommit
        # for this key)
        transaction.postCommit(lambda: self.delete(key), immediately=True)
        return self.delete(key)

    # Home child objects by name

    def keyForObjectWithName(self, homeResourceID, name):
        return "objectWithName:%s:%s" % (homeResourceID, name)

    # Home metadata (Created/Modified)

    def keyForHomeMetaData(self, homeResourceID):
        return "homeMetaData:%s" % (homeResourceID)

    # HomeChild metadata (Created/Modified (and SUPPORTED_COMPONENTS))

    def keyForHomeChildMetaData(self, resourceID):
        return "homeChildMetaData:%s" % (resourceID)

