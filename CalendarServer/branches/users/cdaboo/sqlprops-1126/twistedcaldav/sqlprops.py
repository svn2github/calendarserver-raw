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
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##
from twistedcaldav.root import RootResource
from twisted.python import log

"""
DAV Property store an sqlite database.

This API is considered private to static.py and is therefore subject to
change.
"""

__all__ = ["sqlPropertyStore"]

import os
import urllib

from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, StatusResponse
from twisted.web2.dav import davxml

from twistedcaldav.sql import AbstractSQLDatabase

class sqlPropertyStore (object):
    """

    """
 
    def _encode(clazz, name):
        return urllib.quote("{%s}%s" % name, safe='{}:')

    def _decode(clazz, name):
        name = urllib.unquote(name)

        index = name.find("}")
    
        if (index is -1 or not len(name) > index or not name[0] == "{"):
            raise ValueError("Invalid encoded name: %r" % (name,))
    
        return (name[1:index], name[index+1:])

    _encode = classmethod(_encode)
    _decode = classmethod(_decode)

    def __init__(self, resource):
        self.resource = resource
        if os.path.exists(os.path.dirname(resource.fp.path)):
            if resource.isCollection() and isinstance(resource, RootResource):
                self.rname = ""
                indexpath = resource.fp.path
            else:
                self.rname = os.path.basename(resource.fp.path)
                indexpath = os.path.dirname(resource.fp.path)
            self.index = SQLPropertiesDatabase(indexpath)
        else:
            self.index = None

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
            
        value = self.index.getPropertyValue(self.rname, self._encode(qname))
        if not value:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No such property: {%s}%s" % qname
            ))
            
        doc = davxml.WebDAVDocument.fromString(value)

        return doc.root_element

    def set(self, property):
        """
        Write property into index.
        
        @param property: C{Property} to write
        """

        if self.index:
            self.index.setPropertyValue(self.rname, self._encode(property.qname()), property.toxml())

    def delete(self, qname):
        """
        Delete proeprty from index.

        DELETE from PROPERTIES where NAME=<<rname>> and PROPNAME=<<pname>>

        @param qname:
        """
        
        if self.index:
            self.index.removeProperty(self.rname, self._encode(qname))

    def deleteAll(self):
        """
        Delete proeprty from index.

        DELETE from PROPERTIES where NAME=<<rname>> and PROPNAME=<<pname>>

        @param qname:
        """
        
        if self.index:
            self.index.removeResource(self.rname)

    def contains(self, qname):
        if self.index:
            value = self.index.getPropertyValue(self.rname, self._encode(qname))
            return value != None
        else:
            return False

    def list(self):
        """
        List all property names for this resource.
        
        SELECT PROPNAME from PROPERTIES where NAME=<<rname>>
        
        """

        if self.index:
            results = self.index.listProperties(self.rname)
            result = [self._decode(name) for name in results]
            return result
        else:
            return []

class SQLPropertiesDatabase(AbstractSQLDatabase):
    """
    A database to maintain calendar user proxy group memberships.

    SCHEMA:
    
    Properties Database:
    
    ROW: RESOURCENAME, PROPERTYNAME, PROPERTYVALUE
    
    """
    
    dbType = "SQLPROPERTIES"
    dbFilename = ".db.sqlproperties"
    dbFormatVersion = "1"

    def __init__(self, path):
        path = os.path.join(path, SQLPropertiesDatabase.dbFilename)
        super(SQLPropertiesDatabase, self).__init__(path, SQLPropertiesDatabase.dbFormatVersion)

    def setPropertyValue(self, rname, pname, pvalue):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pvalue: a C{str} containing the property value to set.
        """
        
        # Remove what is there, then add it back.
        self._delete_from_db(rname, pname)
        self._add_to_db(rname, pname, pvalue)
        self._db_commit()

    def getPropertyValue(self, rname, pname):
        """
        Get a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: a C{str} containing the property value.
        """
        
        # Remove what is there, then add it back.
        log.msg("getPropertyValue: %s \"%s\" \"%s\"" % (self.dbpath, rname, pname))
        members = []
        for row in self._db_execute("select PROPERTYVALUE from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME = :2", rname, pname):
            members.append(row[0])
        setlength =  len(members)
        if setlength == 0:
            return None
        elif setlength == 1:
            return members[0]
        else:
            raise ValueError("Multiple properties of the same name %s stored for resource %s" % (pname, rname,))
        

    def removeProperty(self, rname, pname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: a C{str} containing the property value.
        """

        self._delete_from_db(rname, pname)
        self._db_commit()

    def removeResource(self, rname):
        """
        Remove all properties for resource.
    
        @param rname: a C{str} containing the resource name.
        @return: a C{str} containing the property value.
        """

        self._delete_all_from_db(rname)
        self._db_commit()

    def listProperties(self, rname):
        """
        List all properties in resource.
    
        @param rname: a C{str} containing the resource name.
        @return: a C{set} containing the property names.
        """

        members = set()
        for row in self._db_execute("select PROPERTYNAME from PROPERTIES where RESOURCENAME = :1", rname):
            members.add(row[0])
        return members

    def _add_to_db(self, rname, pname, pvalue):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pvalue: a C{str} containing the property value to set.
        """
        
        self._db_execute(
            """
            insert into PROPERTIES (RESOURCENAME, PROPERTYNAME, PROPERTYVALUE)
            values (:1, :2, :3)
            """, rname, pname, pvalue
        )
       
    def _delete_all_from_db(self, rname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: a C{str} containing the property value.
        """

        self._db_execute("delete from PROPERTIES where RESOURCENAME = :1", rname)
    
    def _delete_from_db(self, rname, pname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: a C{str} containing the property value.
        """

        self._db_execute("delete from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME = :2", rname, pname)
    
    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        return SQLPropertiesDatabase.dbType
        
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
                PROPERTYVALUE  text
            )
            """
        )
