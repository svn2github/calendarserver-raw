##
# Copyright (c) 2010 Apple Computer, Inc. All rights reserved.
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

__all__ = ["PropertyCollection"]

try:
    from hashlib import md5
except ImportError:
    from md5 import new as md5

from cPickle import dumps as pickle, loads as unpickle, UnpicklingError

from memcacheclient import ClientFactory as MemcacheClientFactory, MemcacheError, TokenMismatchError

from twisted.python.filepath import FilePath
from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, StatusResponse

from twistedcaldav.config import config
from twistedcaldav.log import LoggingMixIn, Logger

log = Logger()


class PropertyCollection (LoggingMixIn):
    """
    Manages a single property store for all resources in a collection.
    """
    def __init__(self, collection, cacheTimeout=0):
        self.collection = collection
        self.cacheTimeout = cacheTimeout
        self._dirty = set()

    @classmethod
    def memcacheClient(cls, refresh=False):
        if not hasattr(PropertyCollection, "_memcacheClient"):
            log.info("Instantiating memcache connection for PropertyCollection")

            PropertyCollection._memcacheClient = MemcacheClientFactory.getClient(["%s:%s" % (config.Memcached.BindAddress, config.Memcached.Port)],
                debug=0,
                pickleProtocol=2,
            )
            assert PropertyCollection._memcacheClient is not None

        return PropertyCollection._memcacheClient

    def propertyCache(self):
        # The property cache has this format:
        #  {
        #    self._keyForPath("/path/to/resource/file"):
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
            self._loadCache()
        return self._propertyCache

    def childCache(self, child):
        path = child.fp.path
        key = self._keyForPath(path)
        propertyCache = self.propertyCache()

        try:
            childCache, token = propertyCache[key]
        except KeyError:
            self.log_debug("No child property cache for %s" % (child,))
            childCache, token = ({}, None)

            #message = "No child property cache for %s" % (child,)
            #log.error(message)
            #raise AssertionError(message)

        return propertyCache, key, childCache, token

    def _keyForPath(self, path):
        key = "|".join((
            self.__class__.__name__,
            path
        ))
        return md5(key).hexdigest()

    def _loadCache(self, childNames=None):
        if childNames is None:
            childNames = self.collection.listChildren()
        elif not childNames:
            self._propertyCache = {}
            return

        self.log_debug("Loading cache for %s" % (self.collection,))

        client = self.memcacheClient()
        assert client is not None, "OMG no cache!"
        if client is None:
            self._propertyCache = None
            return

        keys = tuple((
            (self._keyForPath(self.collection.fp.child(childName).path), childName)
            for childName in childNames
        ))

        cache = client.gets_multi((key for key, name in keys))

        if cache:
            self._propertyCache = cache
            if self.logger.willLogAtLevel("debug"):
                self.log_debug("Loaded keys for children of %s: %s" % (
                    self.collection,
                    [name for key, name in keys],
                ))
            return

        self.log_debug("Loading property cache from disk")
        self._buildCache()

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

    def _cacheFilePath(self):
        return self.collection.fp.child(".davprops.pickle")

    def _buildCache(self):
        def argh(what):
            msg = ("Unable to %s property collection store: %s" % (what, cacheFilePath.fp))
            log.err(msg)
            raise HTTPError(StatusResponse(responsecode.INTERNAL_SERVER_ERROR, msg))

        cacheFilePath = self._cacheFilePath()
        try:
            cacheFile = cacheFilePath.open()
        except (OSError, IOError):
            if cacheFilePath.exists():
                raise
            else:
                # No cache file: build from old prop store
                self._buildCacheOldSchool()
        else:
            try:
                data = cacheFile.read()
            except (OSError, IOError):
                argh("read")
            finally:
                cacheFile.close()

            try:
                self._propertyCache = unpickle(data)
            except UnpicklingError:
                argh("parse")

    def _buildCacheOldSchool(self):
        self.log_info("Building property file from xattrs for %s" % (self.collection,))

        cache = {}
        propertyStores = []

        childNames = self.collection.listChildren()
        for childName in childNames:
            child = self.collection.getChild(childName)
            if child is None:
                continue

            # Find the original property store
            propertyStore = child.deadProperties().oldPropertyStore

            props = {}
            for qname in propertyStore.list():
                props[qname] = propertyStore.get(qname)

            cache[self._keyForPath(child.fp.path)] = (props, None)

            propertyStores.append(propertyStore)

        self.log_info("Done building property file from xattrs for %s" % (self.collection,))

        self._propertyCache = cache
        self._dirty.update(childNames)
        self.flush()

        # Erase old property store
        for propertyStore in propertyStores:
            for qname in propertyStore.list():
                propertyStore.delete(qname)

    def setProperty(self, child, property, delete=False):
        propertyCache, key, childCache, token = self.childCache(child)

        self._dirty.add(child)

        if delete:
            qname = property
            if childCache.has_key(qname):
                del childCache[qname]
        else:
            qname = property.qname()
            childCache[qname] = property

    def flush(self):
        if self._dirty:
            def argh(what):
                msg = ("Unable to %s property collection store: %s" % (what, cacheFilePath.fp))
                log.err(msg)
                raise HTTPError(StatusResponse(responsecode.INTERNAL_SERVER_ERROR, msg))

            cacheFilePath = self._cacheFilePath()
            try:
                cacheFile = cacheFilePath.open("w")
            except (OSError, IOError):
                argh("open (for writing)")

            try:
                cacheFile.write(pickle(self.propertyCache()))
            except (OSError, IOError):
                argh("write")
            finally:
                cacheFile.close()

            return ############################################

            client = self.memcacheClient()

            if client is not None:
                retries = 10
                while retries:
                    try:
                        if client.set(key, childCache, time=self.cacheTimeout,
                            token=token):
                            # Success
                            break

                    except TokenMismatchError:
                        # The value in memcache has changed since we last
                        # fetched it
                        log.debug("memcacheprops setProperty TokenMismatchError; retrying...")

                    finally:
                        # Re-fetch the properties for this child
                        loaded = self._loadCache(childNames=(child.fp.basename(),))
                        print "-"*10, loaded
                        propertyCache.update(loaded.iteritems())

                    retries -= 1

                    propertyCache, key, childCache, token = self.childCache(child)

                    if delete:
                        if childCache.has_key(qname):
                            del childCache[qname]
                    else:
                        childCache[qname] = property

                else:
                    log.error("memcacheprops setProperty had too many failures")
                    delattr(self, "_propertyCache")
                    raise MemcacheError("Unable to %s property {%s}%s on %s"
                        % ("delete" if delete else "set",
                        qname[0], qname[1], child))

            self._dirty = set()

    def deleteProperty(self, child, qname):
        return self.setProperty(child, qname, delete=True)

    def deleteAll(self, child):
        path = child.fp.path
        key = self._keyForPath(path)
        propertyCache = self.propertyCache()

        if key in propertyCache:
            del propertyCache[key]

        client = self.memcacheClient()
        if client is not None:
            result = client.delete(key)
            if not result:
                raise MemcacheError("Unable to flush cache on %s" % (child,))

    def propertyStoreForChild(self, child, oldPropertyStore):
        return self.ChildPropertyStore(self, child, oldPropertyStore)

    class ChildPropertyStore (LoggingMixIn):
        def __init__(self, parentPropertyCollection, child, oldPropertyStore):
            self.parentPropertyCollection = parentPropertyCollection
            self.child = child
            self.oldPropertyStore = oldPropertyStore

        def propertyCache(self):
            path = self.child.fp.path
            key = self.parentPropertyCollection._keyForPath(path)
            parentPropertyCache = self.parentPropertyCollection.propertyCache()
            return parentPropertyCache.get(key, ({}, None))[0]

        def get(self, qname):
            propertyCache = self.propertyCache()
            if qname in propertyCache:
                return propertyCache[qname]
            else:
                raise HTTPError(StatusResponse(
                    responsecode.NOT_FOUND,
                    "No such property: {%s}%s" % qname
                ))

        def deleteAll(self):
            self.parentPropertyCollection.deleteAll(self.child)

        def set(self, property):
            self.parentPropertyCollection.setProperty(self.child, property)

        def delete(self, qname):
            self.parentPropertyCollection.deleteProperty(self.child, qname)

        def contains(self, qname):
            return qname in self.propertyCache()

        def list(self,):
            return self.propertyCache().iterkeys()
