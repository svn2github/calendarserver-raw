##
# Copyright (c) 2008 Apple Inc. All rights reserved.
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

from twisted.internet.defer import succeed

from twistedcaldav.log import LoggingMixIn
from twistedcaldav.memcachepool import CachePoolUserMixIn
from twistedcaldav.config import config
import cPickle

class Memcacher(LoggingMixIn, CachePoolUserMixIn):

    class memoryCacher():
        """
        A class implementing the memcache client API we care about but
        using a dict to store the results in memory. This can be used
        for caching on a single instance server, and for tests, where
        memcached may not be running.
        """

        def __init__(self):
            self._cache = {}


        def add(self, key, value, expireTime=0):
            if key not in self._cache:
                self._cache[key] = value
                return succeed(True)
            else:
                return succeed(False)

        def set(self, key, value, expireTime=0):
            self._cache[key] = value
            return succeed(True)

        def get(self, key):
            return succeed((0, self._cache.get(key, None),))

        def delete(self, key):
            try:
                del self._cache[key]
                return succeed(True)
            except KeyError:
                return succeed(False)

    #TODO: an sqlite based cacher that can be used for multiple instance servers
    # in the absence of memcached. This is not ideal and we may want to not implement
    # this, but it is being documented for completeness.
    #
    # For now we implement a cacher that does not cache.
    class nullCacher():
        """
        A class implementing the memcache client API we care about but
        does not actually cache anything.
        """

        def add(self, key, value, expireTime=0):
            return succeed(True)

        def set(self, key, value, expireTime=0):
            return succeed(True)

        def get(self, key):
            return succeed((0, None,))

        def delete(self, key):
            return succeed(True)

    def __init__(self, namespace, pickle=False, no_invalidation=False):
        """
        @param namespace: a unique namespace for this cache's keys
        @type namespace: C{str}
        @param pickle: if C{True} values will be pickled/unpickled when stored/read from the cache,
            if C{False} values will be stored directly (and therefore must be strings)
        @type pickle: C{bool}
        @param no_invalidation: if C{True} the cache is static - there will be no invalidations. This allows
            Memcacher to use the memoryCacher cache instead of nullCacher for the multi-instance case when memcached
            is not present,as there is no issue with caches in each instance getting out of sync. If C{False} the
            nullCacher will be used for the multi-instance case when memcached is not configured.
        @type no_invalidation: C{bool}
        """
        self._memcacheProtocol = None
        self._namespace = namespace
        self._pickle = pickle
        self._noInvalidation = no_invalidation


    def _getMemcacheProtocol(self):
        if self._memcacheProtocol is not None:
            return self._memcacheProtocol

        if config.Memcached['ClientEnabled']:
            return self.getCachePool()

        elif config.ProcessType == "Single" or self._noInvalidation:

            # NB no need to pickle the memory cacher as it handles python types natively
            self._memcacheProtocol = Memcacher.memoryCacher()
            self._pickle = False
            return self._memcacheProtocol

        else:

            # NB no need to pickle the null cacher as it handles python types natively
            self._memcacheProtocol = Memcacher.nullCacher()
            self._pickle = False
            return self._memcacheProtocol


    def add(self, key, value, expire_time=0):
        my_value = value
        if self._pickle:
            my_value = cPickle.dumps(value)
        self.log_debug("Adding Cache Token for %r" % (key,))
        return self._getMemcacheProtocol().add(
            '%s:%s' % (self._namespace, key), my_value, expireTime=expire_time)

    def set(self, key, value, expire_time=0):
        my_value = value
        if self._pickle:
            my_value = cPickle.dumps(value)
        self.log_debug("Setting Cache Token for %r" % (key,))
        return self._getMemcacheProtocol().set(
            '%s:%s' % (self._namespace, key), my_value, expireTime=expire_time)

    def get(self, key):
        def _gotit(result):
            _ignore_flags, value = result
            if self._pickle and value is not None:
                value = cPickle.loads(value)
            return value

        self.log_debug("Getting Cache Token for %r" % (key,))
        d = self._getMemcacheProtocol().get('%s:%s' % (self._namespace, key))
        d.addCallback(_gotit)
        return d


    def delete(self, key):
        self.log_debug("Deleting Cache Token for %r" % (key,))
        return self._getMemcacheProtocol().delete(
            '%s:%s' % (self._namespace, key))
