##
# Copyright (c) 2009 Apple Computer, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
##

"""
DAV Property store using memcache on top of another property store
implementation.
"""

__all__ = ["MemcachePropertyStore"]

try:
    from hashlib import md5
except ImportError:
    from md5 import new as md5

from memcache import Client as MemcacheClient, MemcacheError

from twisted.python.filepath import FilePath
from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, StatusResponse

from twistedcaldav.config import config
from twistedcaldav.log import LoggingMixIn, Logger

log = Logger()

NoValue = ""

class MemcachePropertyStore (LoggingMixIn):
    """
    DAV property store using memcache on top of another property store
    implementation.
    """
    def __init__(self, propertyStore, cacheTimeout=0):
        self.propertyStore = propertyStore
        self.resource = propertyStore.resource
        self.cacheTimeout = cacheTimeout

    def _getMemcacheClient(self, refresh=False):
        raise NotImplementedError()
        if not config.Memcached.ClientEnabled:
            # Not allowed to switch form caching to not caching
            assert not hasattr(self, self._memcacheClient)
            return None

        if refresh or not hasattr(self, "_memcacheClient"):
            self._memcacheClient = MemcacheClient(
                ["%s:%s" % (config.Memcached.BindAddress, config.Memcached.Port)],
                debug=0,
                pickleProtocol=2,
            )
        assert self._memcacheClient is not None
        return self._memcacheClient

    def keyForQname(self, qname):
        # FIXME: This only works for file resources
        key = "|".join((
            self.__class__.__name__,
            self.propertyStore.resource.fp.path,
            "{%s}%s" % qname,
        ))
        return md5(key).hexdigest()

    def get(self, qname):
        #self.log_debug("MemcachePropertyStore read for %s on %s"
        #               % (qname, self.propertyStore.resource.fp.path))

        #
        # First, check to see if we've cached the property already.
        #
        key = self.keyForQname(qname)
        client = self._getMemcacheClient()

        if client is not None:
            try:
                property = client.get(key)
                if property is not None:
                    if property is "":
                        # Negative cache hit
                        raise HTTPError(StatusResponse(
                            responsecode.NOT_FOUND,
                            "No such property: {%s}%s" % qname
                        ))
                    else:
                        # Cache hit
                        return property
            except MemcacheError, e:
                self.log_error("Error from memcache: %s" % (e,))
                pass

        #
        # No luck, check in the property store
        #
        self.log_debug("Cache miss for %s on %s"
                       % (qname, self.propertyStore.resource.fp.path))

        if client is None:
            return self.propertyStore.get(qname)

        try:
            property = self.propertyStore.get(qname)
            assert property is not None

            # Cache the result
            client.set(key, property, time=self.cacheTimeout)
        except HTTPError, e:
            if e.response.code == responsecode.NOT_FOUND:
                # Cache the non-result
                client.set(key, NoValue, time=self.cacheTimeout)
            raise

        return property

    def set(self, property):
        #self.log_debug("Write for %s on %s"
        #               % (property.qname(), self.propertyStore.resource.fp.path))

        client = self._getMemcacheClient()
        if client is not None:
            key = self.keyForQname(property.qname())

            if client.set(key, property, time=self.cacheTimeout):
                return

            # Refresh the memcache connection and try again
            client = self._getMemcacheClient(refresh=True)
            if client.set(key, property, time=self.cacheTimeout):
                self.log_error("Temporary write failure for %s on %s"
                               % (property.qname(), self.propertyStore.resource.fp.path))
                return

            message = (
                "Write failure for %s on %s"
                % (property.qname(), self.propertyStore.resource.fp.path)
            )
            self.log_error(message)
            raise MemcacheError(message)

        self.propertyStore.set(property)

    def delete(self, qname):
        #self.log_debug("Delete for %s on %s"
        #               % (qname, self.propertyStore.resource.fp.path))
        client = self._getMemcacheClient()
        if client is not None:
            key = self.keyForQname(property.qname())
            # Flush the cache
            client.delete(key)

        self.propertyStore.delete(qname)

    def contains(self, qname):
        #self.log_debug("Contains for %s"
        #               % (self.propertyStore.resource.fp.path,))
        client = self._getMemcacheClient()
        if client is not None:
            key = self.keyForQname(qname)
            property = client.get(key)
            if property is not None:
                if property is "":
                    # Negative cache hit                                                                                                         
                    return False
                else:
                    # Cache hit
                    return True

        return self.propertyStore.contains(qname)

    def list(self):
        #self.log_debug("List for %s"
        #               % (self.propertyStore.resource.fp.path,))
        return self.propertyStore.list()


class MemcachePropertyCollection (LoggingMixIn):
    """
    Manages a single property store for all resources in a collection.
    """
    def __init__(self, collection, cacheTimeout=0):
        self.collection = collection
        self.cacheTimeout = cacheTimeout

    @classmethod
    def memcacheClient(cls, refresh=False):
        if not hasattr(MemcachePropertyCollection, "_memcacheClient"):
            if not config.Memcached.ClientEnabled:
                return None

            log.info("Instantiating memcache connection for MemcachePropertyCollection")
            MemcachePropertyCollection._memcacheClient = MemcacheClient(
                ["%s:%s" % (config.Memcached.BindAddress, config.Memcached.Port)],
                debug=0,
                pickleProtocol=2,
            )
            assert MemcachePropertyCollection._memcacheClient is not None

        return MemcachePropertyCollection._memcacheClient

    def propertyCache(self):
        # The property cache has this format:
        #  {
        #    "/path/to/resource/file":
        #      (
        #        {
        #          (namespace, name): property,
        #          ...,
        #        },
        #        memcache_token,
        #      ),
        #    ...,
        #  }
        if not hasattr(self, "_propertyCache"):
            self._propertyCache = self._loadCache()
        return self._propertyCache

    def _keyForPath(self, path):
        key = "|".join((
            self.__class__.__name__,
            path
        ))
        return md5(key).hexdigest()

    def _loadCache(self, childNames=None):
        if childNames is None:
            abortIfMissing = False
            childNames = self.collection.listChildren()
        else:
            if childNames:
                abortIfMissing = True
            else:
                return {}

        self.log_debug("Loading cache for %s" % (self.collection,))

        client = self.memcacheClient()
        assert client is not None, "OMG no cache!"
        if client is None:
            return None

        keys = tuple((
            (self._keyForPath(self.collection.fp.child(childName).path), childName)
            for childName in childNames
        ))

        result = client.gets_multi((key for key, name in keys))

        missing = tuple((
            name for key, name in keys
            if key not in result
        ))

        if missing:
            if abortIfMissing:
                raise MemcacheError("Unable to fully load cache for %s" % (self.collection,))

            loaded = self._buildCache(childNames=missing)
            loaded = self._loadCache(childNames=(FilePath(name).basename() for name in loaded.iterkeys()))

            result.update(loaded.iteritems())

        return result

    def _storeCache(self, cache):
        self.log_debug("Storing cache for %s" % (self.collection,))

        values = dict((
            (self._keyForPath(path), props)
            for path, props
            in cache.iteritems()
        ))

        client = self.memcacheClient()
        if client is not None:
            client.set_multi(values, time=self.cacheTimeout)

    def _buildCache(self, childNames=None):
        if childNames is None:
            childNames = self.collection.listChildren()
        elif not childNames:
            return {}

        self.log_debug("Building cache for %s" % (self.collection,))

        cache = {}

        for childName in childNames:
            child = self.collection.getChild(childName)
            propertyStore = child.deadProperties()

            props = {}
            for qname in propertyStore.list(cache=False):
                props[qname] = propertyStore.get(qname, cache=False)

            cache[child.fp.path] = props

        self._storeCache(cache)

        return cache

    def setProperty(self, child, property):
        path = child.fp.path
        key = self._keyForPath(path)
        propertyCache = self.propertyCache()
        childCache, token = propertyCache.get(key, (None, None))

        assert childCache is not None, "No child cache?"

        if childCache.get(property.qname(), None) == property:
            # No changes
            return

        childCache[property.qname()] = property

        client = self.memcacheClient()
        if client is not None:
            result = client.set(key, childCache, time=self.cacheTimeout, token=token)
            if not result:
                delattr(self, "_propertyCache")
                raise MemcacheError("Unable to set property")

            loaded = self._loadCache(childNames=(child.fp.basename(),))
            propertyCache.update(loaded.iteritems())

    def flushCache(self, child):
        path = child.fp.path
        key = self._keyForPath(path)
        propertyCache = self.propertyCache()

        if key in propertyCache:
            del propertyCache[key]

        client = self.memcacheClient()
        if client is not None:
            result = client.delete(key)
            if not result:
                raise MemcacheError("Unable to delete property")

    def deleteProperty(self, child, qname):
        path = child.fp.path
        key = self._keyForPath(path)
        propertyCache = self.propertyCache()
        childCache, token = propertyCache.get(key, (None, None))

        assert childCache is not None, "No child cache?"

        del childCache[qname]

        client = self.memcacheClient()
        if client is not None:
            result = client.set(key, childCache, time=self.cacheTimeout, token=token)
            if not result:
                delattr(self, "_propertyCache")
                raise MemcacheError("Unable to delete property")

            loaded = self._loadCache(childNames=(child.fp.basename(),))
            propertyCache.update(loaded.iteritems())

    def propertyStoreForChild(self, child, childPropertyStore):
        return self.ChildPropertyStore(self, child, childPropertyStore)

    class ChildPropertyStore (LoggingMixIn):
        def __init__(self, parentPropertyCollection, child, childPropertyStore):
            self.parentPropertyCollection = parentPropertyCollection
            self.child = child
            self.childPropertyStore = childPropertyStore

        def propertyCache(self):
            path = self.child.fp.path
            key = self.parentPropertyCollection._keyForPath(path)
            parentPropertyCache = self.parentPropertyCollection.propertyCache()
            return parentPropertyCache.get(key, ({}, None))[0]

        def flushCache(self):
            self.parentPropertyCollection.flushCache(self.child)

        def get(self, qname, cache=True):
            if cache:
                propertyCache = self.propertyCache()
                if qname in propertyCache:
                    return propertyCache[qname]
                else:
                    raise HTTPError(StatusResponse(
                        responsecode.NOT_FOUND,
                        "No such property: {%s}%s" % qname
                    ))

            self.log_debug("Read for %s on %s"
                           % (qname, self.childPropertyStore.resource.fp.path))
            return self.childPropertyStore.get(qname)

        def set(self, property):
            self.log_debug("Write for %s on %s"
                           % (property.qname(), self.childPropertyStore.resource.fp.path))

            self.parentPropertyCollection.setProperty(self.child, property)
            self.childPropertyStore.set(property)

        def delete(self, qname):
            self.log_debug("Delete for %s on %s"
                           % (qname, self.childPropertyStore.resource.fp.path))

            self.parentPropertyCollection.deleteProperty(self.child, qname)
            self.childPropertyStore.delete(qname)

        def contains(self, qname, cache=True):
            if cache:
                propertyCache = self.propertyCache()
                return qname in propertyCache

            self.log_debug("Contains for %s"
                           % (self.childPropertyStore.resource.fp.path,))
            return self.childPropertyStore.contains(qname)

        def list(self, cache=True):
            if cache:
                propertyCache = self.propertyCache()
                return propertyCache.iterkeys()

            self.log_debug("List for %s"
                           % (self.childPropertyStore.resource.fp.path,))
            return self.childPropertyStore.list()
