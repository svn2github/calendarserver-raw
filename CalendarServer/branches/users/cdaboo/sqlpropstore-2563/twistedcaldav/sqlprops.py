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
from twistedcaldav.log import Logger

"""
DAV Property store an sqlite database.

This API is considered private to static.py and is therefore subject to
change.
"""

__all__ = ["sqlPropertyStore"]

import cPickle
import os

from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, StatusResponse

from twistedcaldav.sql import AbstractSQLDatabase

log = Logger()

class sqlPropertyStore (object):
    """
    A dead property store that uses an SQLite database back end.
    """
 
    def __init__(self, resource, use_cache=True):
        self.resource = resource
        if os.path.exists(os.path.dirname(resource.fp.path)):
            from twistedcaldav.root import RootResource
            if resource.isCollection() and isinstance(resource, RootResource):
                self.rname = ""
                indexpath = resource.fp.path
            else:
                self.rname = os.path.basename(resource.fp.path)
                indexpath = os.path.dirname(resource.fp.path)
            self.index = SQLPropertiesDatabase(indexpath, use_cache)
            if resource.isCollection():
                self.childindex = SQLPropertiesDatabase(resource.fp.path, use_cache)
            else:
                self.childindex = None
        else:
            log.err("No sqlPropertyStore file for %s" % (os.path.dirname(resource.fp.path),))
            self.index = None
            self.childindex = None

    def get(self, qname):
        """
        Read property from index.
        
        @param qname: C{tuple} of property namespace and name.
        """
        if not self.index:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No such property: {%s}%s" % qname
            ))
            
        value = self.index.getOnePropertyValue(self.rname, qname)
        if not value:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No such property: {%s}%s" % qname
            ))
            
        return value

    def _getAll(self, hidden=False):
        """
        Read all properties from index.
        
        @param hidden: C{True} to return hidden properties, C{False} otherwise.
        @return: a C{dict} containing property name/value.
        """
        if not self.index:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No properties"
            ))
            
        return self.index.getAllPropertyValues(self.rname, hidden)

    def set(self, property):
        """
        Write property into index.
        
        @param property: C{Property} to write
        """

        if self.index:
            self.index.setOnePropertyValue(self.rname, property.qname(), property, property.hidden)

    def _setSeveral(self, properties):
        """
        Write specific properties into index.
        
        @param properties: C{list} of properties to write
        """

        if self.index:
            self.index.setSeveralPropertyValues(self.rname, [(p.qname(), p, p.hidden) for p in properties])

    def _setAll(self, properties):
        """
        Write all properties into index.
        
        @param properties: C{list} of properties to write
        """

        if self.index:
            self.index.removeAllProperties(self.rname)
            self.index.setSeveralPropertyValues(self.rname, [(p.qname(), p, p.hidden) for p in properties])

    def delete(self, qname):
        """
        Delete property from index.

        DELETE from PROPERTIES where NAME=<<rname>> and PROPNAME=<<pname>>

        @param qname:
        """
        
        if self.index:
            self.index.removeProperty(self.rname, qname)

    def deleteAll(self):
        """
        Delete all properties from index.

        DELETE from PROPERTIES where NAME=<<rname>>
        """
        
        if self.index:
            self.index.removeAllProperties(self.rname)

    def contains(self, qname):
        if self.index:
            value = self.index.getOnePropertyValue(self.rname, qname)
            return value is not None
        else:
            return False

    def list(self):
        """
        List all property names for this resource.
        
        SELECT PROPNAME from PROPERTIES where NAME=<<rname>>
        
        """

        if self.index:
            return self.index.listProperties(self.rname)
        else:
            return ()

    def copy(self, props):
        """
        Copy properties from another property store into this one, replacing everything
        currently present.
        
        @param props: a property store to copy from
        @type props: C{sqlPropertyStore}
        """
        
        oldprops = props._getAll(hidden=True)
        self._setAll(oldprops.itervalues())

class SQLPropertiesDatabase(AbstractSQLDatabase):
    """
    A database to store properties on resources in a collection.
    It maintains a cache of values read from the database.

    SCHEMA:
    
    Properties Database:
    
    ROW: RESOURCENAME, PROPERTYNAME, PROPERTYOBJECT, PROPERTYVALUE
    
    """
    
    dbType = "SQLPROPERTIES"
    dbFilename = ".db.sqlproperties"
    dbFormatVersion = "1"

    @classmethod
    def _encode(cls, name):
        return "{%s}%s" % name

    @classmethod
    def _decode(cls, name):
        index = name.find("}")
    
        if (index is -1 or not len(name) > index or not name[0] == "{"):
            raise ValueError("Invalid encoded name: %r" % (name,))
    
        return (name[1:index], name[index+1:])

    def __init__(self, path, use_cache=True):
        path = os.path.join(path, SQLPropertiesDatabase.dbFilename)
        super(SQLPropertiesDatabase, self).__init__(path, True, utf8=True)
        
        self.use_cache = use_cache
        self.cache = {}

    def getOnePropertyValue(self, rname, pname):
        """
        Get a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: an object representing the property.
        """
        
        def _getOneFromDB():
            members = []
            for row in self._db_execute("select PROPERTYOBJECT from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME = :2", rname, self._encode(pname)):
                members.append(row[0])
            setlength =  len(members)
            if setlength == 0:
                value = None
            elif setlength == 1:
                value = cPickle.loads(members[0])
            else:
                raise ValueError("Multiple properties of the same name \"%s\" stored for resource \"%s\"" % (pname, rname,))
            return value

        # Check cache first
        if self.use_cache:
            if not self.cache.has_key((rname, pname)):
                # Get the property
                log.debug("getPropertyValue: %s \"%s\" \"%s\"" % (self.dbpath, rname, pname))
                self.cache[(rname, pname)] = _getOneFromDB()
            return self.cache[(rname, pname)]
        else:
            return _getOneFromDB()

    def getAllPropertyValues(self, rname, hidden):
        """
        Get specified property values from specific resource.
    
        @param rname: a C{str} containing the resource name.
        @param hidden: C{True} to return hidden properties, C{False} otherwise.
        @return: a C{dict} containing property name/value.
        """
        
        properties = {}
        statement = "select PROPERTYNAME, PROPERTYOBJECT from PROPERTIES where RESOURCENAME = :1"
        if not hidden:
            statement += " and HIDDEN = 'F'"
        for row in self._db_execute(statement, rname):
            properties[self._decode(row[0])] = cPickle.loads(row[1])

        return properties

    def setOnePropertyValue(self, rname, pname, pvalue, hidden):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pvalue: the property to set.
        @param hidden: C{True} for a hidden property, C{False} otherwise.
        """
        
        # Remove what is there, then add it back.
        self._delete_from_db(rname, self._encode(pname))
        self._add_to_db(rname, self._encode(pname), cPickle.dumps(pvalue), pvalue.toxml(), hidden)
        self._db_commit()
        
        if self.use_cache:
            self.cache[(rname, pname)] = pvalue 

    def setSeveralPropertyValues(self, rname, properties):
        """
        Add a set of properties.
    
        @param rname: a C{str} containing the resource name.
        @param properties: a C{dict} containing the name of the properties to set.
        """
        
        # Remove what is there, then add it back.
        for p in properties:
            self._delete_from_db(rname, self._encode(p[0]))
            self._add_to_db(rname, self._encode(p[0]), cPickle.dumps(p[1]), p[1].toxml(), p[2])
        self._db_commit()

    def removeProperty(self, rname, pname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to remove.
        """

        self._delete_from_db(rname, self._encode(pname))
        self._db_commit()
        
        if self.use_cache:
            try:
                del self.cache[(rname, pname)]
            except KeyError:
                pass

    def removeAllProperties(self, rname):
        """
        Remove all properties for resource.
    
        @param rname: a C{str} containing the resource name.
        """

        self._delete_all_from_db(rname)
        self._db_commit()
        
        if self.use_cache:
            for key in list(self.cache.iterkeys()):
                key_rname, _ignore_pname = key
                if key_rname == rname:
                    del self.cache[key]

    def listProperties(self, rname):
        """
        List all properties in resource.
    
        @param rname: a C{str} containing the resource name.
        @return: a C{set} containing the property names.
        """

        members = set()
        for row in self._db_execute("select PROPERTYNAME from PROPERTIES where RESOURCENAME = :1", rname):
            members.add(self._decode(row[0]))
        return members

    def _add_to_db(self, rname, pname, pobject, pvalue, hidden):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pobject: a C{str} containing the pickled representation of the property object.
        @param pvalue: a C{str} containing the text of the property value to set.
        """
        
        self._db_execute(
            """
            insert into PROPERTIES (RESOURCENAME, PROPERTYNAME, PROPERTYOBJECT, PROPERTYVALUE, HIDDEN)
            values (:1, :2, :3, :4, :5)
            """, rname, pname, pobject, pvalue, "T" if hidden else "F"
        )
       
    def _delete_from_db(self, rname, pname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: a C{str} containing the property value.
        """

        self._db_execute("delete from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME = :2", rname, pname)
    
    def _delete_all_from_db(self, rname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: a C{str} containing the property value.
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
        Initialise the underlying database tables.
        @param q:           a database cursor to use.
        """

        #
        # PROPERTIES table
        #
        q.execute(
            """
            create table PROPERTIES (
                RESOURCENAME   text,
                PROPERTYNAME   text,
                PROPERTYOBJECT text,
                PROPERTYVALUE  text,
                HIDDEN         text(1)
            )
            """
        )
        q.execute(
            """
            create index RESOURCE on PROPERTIES (RESOURCENAME)
            """
        )
        q.execute(
            """
            create index RESOURCEandPROPERTY on PROPERTIES (RESOURCENAME, PROPERTYNAME)
            """
        )
