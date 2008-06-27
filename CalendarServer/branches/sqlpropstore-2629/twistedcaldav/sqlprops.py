##
# Copyright (c) 2005 Apple Computer, Inc. All rights reserved.
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
DAV Property store using an sqlite database.
"""

__all__ = ["sqlPropertyStore"]

import os

from twisted.web2 import responsecode
from twisted.web2.dav import davxml
from twisted.web2.http import HTTPError, StatusResponse

from twistedcaldav.log import Logger
from twistedcaldav.sql import AbstractSQLDatabase

log = Logger()

class sqlPropertyStore (object):
    """
    A dead property store that uses an SQLite database back end.
    """
 
    def __init__(self, resource, use_cache=True):
        self.resource = resource
        if os.path.exists(os.path.dirname(resource.fp.path)):
            if resource.isCollection():
                self.childindex = SQLPropertiesDatabase(resource.fp.path, use_cache)
            else:
                self.childindex = None

            from twistedcaldav.root import RootResource
            if resource.isCollection() and isinstance(resource, RootResource):
                self.rname = ""
                self.index = self.childindex
            else:
                # Try to re-use the childindex of the parent resource if one can be found.
                self.rname = os.path.basename(resource.fp.path)
                if hasattr(self.resource, "parent_resource"):
                    self.index = self.resource.parent_resource.deadProperties().childindex
                else:
                    self.index = SQLPropertiesDatabase(os.path.dirname(resource.fp.path), use_cache)
        else:
            log.err("No sqlPropertyStore file for %s" % (os.path.dirname(resource.fp.path),))
            self.index = None
            self.childindex = None

    def cacheAllChildProperties(self):
        """
        Cache all properties for all child resources
        """
        
        if self.childindex:
            self.childindex.cacheAllChildProperties()

    def get(self, qname):
        """
        Read property from index.
        
        @param qname: C{tuple} of property namespace and name.
        """

        if self.index:
            value = self.index.getOnePropertyForResource(self.rname, qname)
            if not value:
                raise HTTPError(StatusResponse(
                    responsecode.NOT_FOUND,
                    "No such property: {%s}%s" % qname
                ))
                
            return value
        else:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No such property: {%s}%s" % qname
            ))

    def _getAll(self, hidden=False):
        """
        Read all properties from index.
        
        @param hidden: C{True} to return hidden properties, C{False} otherwise.
        @return: a C{dict} containing property name/value.
        """

        if self.index:
            return self.index.getAllPropertiesForResource(self.rname, hidden)
        else:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No such property"
            ))

    def set(self, property):
        """
        Write property into index.
        
        @param property: C{Property} to write
        """

        if self.index:
            self.index.setOnePropertyForResource(self.rname, property)
        else:
            raise HTTPError(StatusResponse(
                responsecode.INTERNAL_SERVER_ERROR,
                "Property store does not exist"
            ))

    def _setSeveral(self, properties):
        """
        Write specific properties into index.
        
        @param properties: C{list} of properties to write
        """

        if self.index:
            self.index.setSeveralPropertiesForResource(self.rname, properties)
        else:
            raise HTTPError(StatusResponse(
                responsecode.INTERNAL_SERVER_ERROR,
                "Property store does not exist"
            ))

    def _setAll(self, properties):
        """
        Write all properties into index.
        
        @param properties: C{list} of properties to write
        """

        self.index.removeAllPropertiesForResource(self.rname)
        self.index.setSeveralPropertiesForResource(self.rname, properties)

    def delete(self, qname):
        """
        Delete property from index.

        DELETE from PROPERTIES where NAME=<<rname>> and PROPNAME=<<pname>>

        @param qname:
        """
        
        if self.index:
            self.index.removeOnePropertyForResource(self.rname, qname)
        else:
            raise HTTPError(StatusResponse(
                responsecode.INTERNAL_SERVER_ERROR,
                "Property store does not exist"
            ))

    def deleteAll(self):
        """
        Delete all properties from index.

        DELETE from PROPERTIES where NAME=<<rname>>
        """
        
        if self.index:
            self.index.removeAllPropertiesForResource(self.rname)
        else:
            raise HTTPError(StatusResponse(
                responsecode.INTERNAL_SERVER_ERROR,
                "Property store does not exist"
            ))

    def contains(self, qname):

        if self.index:
            return self.index.getOnePropertyForResource(self.rname, qname) is not None
        else:
            return False

    def list(self):
        """
        List all property names for this resource.
        
        SELECT PROPNAME from PROPERTIES where NAME=<<rname>>
        
        """

        if self.index:
            return self.index.listPropertiesForResource(self.rname)
        else:
            raise HTTPError(StatusResponse(
                responsecode.INTERNAL_SERVER_ERROR,
                "Property store does not exist"
            ))

    def copyFrom(self, props):
        """
        Copy properties from another property store into this one, replacing everything
        currently present.
        
        @param props: a property store to copy from
        @type props: C{sqlPropertyStore}
        """
        
        oldprops = props._getAll(hidden=True)
        self._setAll(oldprops.itervalues())

    def moveFrom(self, props):
        """
        Move properties from another property store into this one, replacing everything
        currently present.
        
        @param props: a property store to copy from
        @type props: C{sqlPropertyStore}
        """
        
        oldprops = props._getAll(hidden=True)
        self._setAll(oldprops.itervalues())
        props.deleteAll()

class SQLPropertiesDatabase(AbstractSQLDatabase):
    """
    A database to store properties on resources in a collection.
    It maintains a cache of values read from the database.

    SCHEMA:
    
    Properties Database:
    
    ROW: RESOURCENAME, PROPERTYNAME, PROPERTYXML
    
    """
    
    dbType = "SQLPROPERTIES"
    dbFilename = ".db.sqlproperties"
    dbFormatVersion = "1"

    _debug_instance = 1

    @classmethod
    def _encodePropertyName(cls, name):
        return "{%s}%s" % name

    @classmethod
    def _decodePropertyName(cls, name):
        index = name.find("}")
    
        if (index is -1 or not len(name) > index or not name[0] == "{"):
            raise ValueError("Invalid encoded name: %r" % (name,))
    
        return (name[1:index], name[index+1:])

    @classmethod
    def _decodePropertyValue(cls, pname, pxml):
        """
        @param pname: name of the property
        @type pname: str
        @param pxml: a property encoded as XML
        @type pxml: str
        @return: the davxml Element of the property, or C{None} if pxml is empty or C{None}
        """
        
        result = None
        if pxml:
            try:
                doc = davxml.WebDAVDocument.fromString(pxml)
                result = doc.root_element
            except ValueError:
                msg = ("Invalid property value stored on server: {%s}%s %s"
                       % (pname[0], pname[1], pxml))
                log.err(msg)
                raise HTTPError(StatusResponse(responsecode.INTERNAL_SERVER_ERROR, msg))

        return result

    def __init__(self, path, use_cache=True):
        path = os.path.join(path, SQLPropertiesDatabase.dbFilename)
        super(SQLPropertiesDatabase, self).__init__(path, True, utf8=True)
        
        self.use_cache = use_cache
        self.cache = {}
        
        _instance = SQLPropertiesDatabase._debug_instance
        SQLPropertiesDatabase._debug_instance += 1
        #self.instance = "%s - %s" % (path[105:-len(SQLPropertiesDatabase.dbFilename)], _instance,)
        self.instance = "%s" % (_instance,)
        log.debug("[%s]: Created instance" % (self.instance,))

    def cacheAllChildProperties(self):
        
        self.cache = {}

        log.debug("[%s]: Caching all child properties" % (self.instance,))
        for row in self._db_execute("select RESOURCENAME, PROPERTYNAME, PROPERTYXML from PROPERTIES"):
            rname = row[0]
            pname = self._decodePropertyName(row[1])
            pvalue = self._decodePropertyValue(pname, row[2])
            self.cache.setdefault(rname, {})[pname] = pvalue

    def getOnePropertyForResource(self, rname, pname):
        """
        Get a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: an object representing the property.
        """
        
        # Check cache first
        if self.use_cache:
            # Make sure we have a cache entry
            if not self.cache.has_key(rname):
                log.debug("[%s]: Caching properties for %s, triggered by {%s}%s" % (self.instance, rname, pname[0], pname[1],))
                self.cache[rname] = {}
                for row in self._db_execute("select PROPERTYNAME, PROPERTYXML from PROPERTIES where RESOURCENAME = :1", rname):
                    sqlpname = self._decodePropertyName(row[0])
                    sqlpvalue = self._decodePropertyValue(sqlpname, row[1])
                    self.cache[rname][sqlpname] = sqlpvalue
            
            # Get the property and do negative caching if not present
            return self.cache[rname].setdefault(pname, None)
        else:
            log.debug("[%s] Getting property {%s}%s for %s" % (self.instance, pname[0], pname[1], rname,))
            pxml = self._db_value_for_sql("select PROPERTYXML from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME = :2", rname, self._encodePropertyName(pname))
            pvalue = self._decodePropertyValue(pname, pxml)
            return pvalue

    def getAllPropertiesForResource(self, rname, hidden):
        """
        Get specified property values from specific resource.
    
        @param rname: a C{str} containing the resource name.
        @param hidden: C{True} to return hidden properties, C{False} otherwise.
        @return: a C{dict} containing property name/value.
        """
        
        log.debug("[%s] Caching all properties for %s" % (self.instance, rname,))
        properties = {}
        statement = "select PROPERTYNAME, PROPERTYXML from PROPERTIES where RESOURCENAME = :1"
        if not hidden:
            statement += " and HIDDEN = 'F'"
        for row in self._db_execute(statement, rname):
            pname = self._decodePropertyName(row[0])
            pvalue = self._decodePropertyValue(pname, row[1])
            properties[pname] = pvalue

        return properties

    def setOnePropertyForResource(self, rname, property):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pvalue: the property to set.
        @param hidden: C{True} for a hidden property, C{False} otherwise.
        """
        
        pname = property.qname()
        log.debug("[%s] Setting property {%s}%s for %s" % (self.instance, pname[0], pname[1], rname,))
        self._add_to_db(rname, self._encodePropertyName(pname), property.toxml(), property.hidden)
        self._db_commit()
        
        if self.use_cache and self.cache.has_key(rname):
            self.cache[rname][pname] = property 

    def setSeveralPropertiesForResource(self, rname, properties):
        """
        Add a set of properties.
    
        @param rname: a C{str} containing the resource name.
        @param properties: a C{dict} containing the name of the properties to set.
        """
        
        # Add properties.
        log.debug("[%s] Setting properties for %s" % (self.instance, rname,))
        for property in properties:
            self._add_to_db(rname, self._encodePropertyName(property.qname()), property.toxml(), property.hidden)
        self._db_commit()

        if self.use_cache and self.cache.has_key(rname):
            for property in properties:
                self.cache[rname][property.qname()] = property

    def removeOnePropertyForResource(self, rname, pname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to remove.
        """

        log.debug("[%s] Removing property {%s}%s from %s" % (self.instance, pname[0], pname[1], rname,))
        self._delete_from_db(rname, self._encodePropertyName(pname))
        self._db_commit()
        
        if self.use_cache and self.cache.has_key(rname):
            self.cache[rname][pname] = None

    def removeAllPropertiesForResource(self, rname):
        """
        Remove all properties for resource.
    
        @param rname: a C{str} containing the resource name.
        """

        log.debug("[%s] Removing all properties from %s" % (self.instance, rname,))
        self._delete_all_from_db(rname)
        self._db_commit()
        
        if self.use_cache:
            try:
                del self.cache[rname]
            except KeyError:
                pass

    def listPropertiesForResource(self, rname):
        """
        List all properties in resource.
    
        @param rname: a C{str} containing the resource name.
        @return: a C{set} containing the property names.
        """

        members = set()
        if self.use_cache and self.cache.has_key(rname):
            log.debug("[%s] Listing all properties for %s from cache" % (self.instance, rname,))
            members.update([k for k, v in self.cache[rname].iteritems() if v is not None])
        else:
            log.debug("[%s] Listing all properties for %s via query" % (self.instance, rname,))
            for row in self._db_execute("select PROPERTYNAME from PROPERTIES where RESOURCENAME = :1", rname):
                members.add(self._decodePropertyName(row[0]))
        return members

    def _add_to_db(self, rname, pname, pxml, hidden):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pxml: a C{str} containing the XML representation of the property object.
        @param hidden: a C{bool} indicating whether the property is hidden.
        """
        
        self._db_execute(
            """
            insert or replace into PROPERTIES (RESOURCENAME, PROPERTYNAME, PROPERTYXML, HIDDEN)
            values (:1, :2, :3, :4)
            """, rname, pname, pxml, "T" if hidden else "F"
        )
       
    def _delete_from_db(self, rname, pname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        """

        self._db_execute("delete from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME = :2", rname, pname)
    
    def _delete_all_from_db(self, rname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        """

        self._db_execute("delete from PROPERTIES where RESOURCENAME = :1", rname)
    
    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return SQLPropertiesDatabase.dbType
        
    def _db_version(self):
        """
        @return: the schema version assigned to this index.
        """
        return SQLPropertiesDatabase.dbFormatVersion

    def _db_init_data_tables(self, q):
        """
        Initialize the underlying database tables.
        @param q:           a database cursor to use.
        """

        #
        # PROPERTIES table
        #
        # (RESOURCENAME, PROPERTYNAME) is unique and we default to replacing whatever is
        # already present for those pairs. Using UNIQUE on those creates an index for us.
        #
        q.execute(
            """
            create table PROPERTIES (
                RESOURCENAME   text,
                PROPERTYNAME   text,
                PROPERTYXML    text,
                HIDDEN         text(1),
                UNIQUE         (RESOURCENAME, PROPERTYNAME) ON CONFLICT REPLACE
            )
            """
        )
        
        # When deleting a resource as a whole we search on the RESOURCENAME so index that column.
        q.execute(
            """
            create index RESOURCE on PROPERTIES (RESOURCENAME)
            """
        )
