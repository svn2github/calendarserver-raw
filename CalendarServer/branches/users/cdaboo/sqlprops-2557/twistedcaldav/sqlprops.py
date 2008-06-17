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

"""
DAV Property store an sqlite database.

This API is considered private to static.py and is therefore subject to
change.
"""

__all__ = ["sqlPropertyStore"]

import cPickle
import os

from twisted.python import log
from twisted.web2 import responsecode
from twisted.web2.http import HTTPError, StatusResponse

from twistedcaldav.sql import AbstractSQLDatabase

DEBUG_LOG = False

class sqlPropertyStore (object):
    """
    A dead property store that uses an SQLite database back end.
    """
 
    def __init__(self, resource):
        self.resource = resource
        if os.path.exists(os.path.dirname(resource.fp.path)):
            from twistedcaldav.root import RootResource
            if resource.isCollection() and isinstance(resource, RootResource):
                self.rname = ""
                indexpath = resource.fp.path
            else:
                self.rname = os.path.basename(resource.fp.path)
                indexpath = os.path.dirname(resource.fp.path)
            self.index = SQLPropertiesDatabase(indexpath)
            if resource.isCollection():
                self.childindex = SQLPropertiesDatabase(resource.fp.path)
            else:
                self.childindex = None
        else:
            log.msg("No sqlPropertyStore file for %s" % (os.path.dirname(resource.fp.path),))
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

    def getSeveral(self, qnames):
        """
        Read specific properties from index.
        
        @param qnames: C{list} of C{tuple} of property namespace and name.
        @return: a C{dict} containing property name/value.
        """
        if not qnames:
            return None

        if not self.index:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No such property: {%s}%s" % qnames[0]
            ))
            
        return self.index.getSeveralPropertyValues(self.rname, qnames)

    def getAll(self, nohidden=True):
        """
        Read all properties from index.
        
        @param nohidden: C{True} to not return hidden properties, C{False} otherwise.
        @return: a C{dict} containing property name/value.
        """
        if not self.index:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No properties"
            ))
            
        return self.index.getAllPropertyValues(self.rname, nohidden)

    def getSeveralResources(self, qnames):
        """
        Read specific properties for all child resources from index.
        
        @param qnames: C{list} of C{tuple} of property namespace and name.
        @return: a C{dict} with resource name as keys and C{dict} of property name/classes as values
        """
        if not qnames:
            return None

        if not self.childindex:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No such property: {%s}%s" % qnames[0]
            ))
            
        return self.childindex.getSeveralResourcePropertyValues(qnames)

    def getAllResources(self, nohidden=True):
        """
        Read specific properties for all child resources from index.
        
        @param nohidden: C{True} to not return hidden properties, C{False} otherwise.
        @return: a C{dict} with resource name as keys and C{dict} of property name/classes as values
        """

        if not self.childindex:
            raise HTTPError(StatusResponse(
                responsecode.NOT_FOUND,
                "No properties"
            ))
            
        return self.childindex.getAllResourcePropertyValues(nohidden)

    def set(self, property):
        """
        Write property into index.
        
        @param property: C{Property} to write
        """

        if self.index:
            self.index.setOnePropertyValue(self.rname, property.qname(), property, property.hidden)

    def setSeveral(self, properties):
        """
        Write specific properties into index.
        
        @param properties: C{list} of properties to write
        """

        if self.index:
            self.index.setSeveralPropertyValues(self.rname, [(p.qname(), p, p.hidden) for p in properties])

    def delete(self, qname):
        """
        Delete property from index.

        DELETE from PROPERTIES where NAME=<<rname>> and PROPNAME=<<pname>>

        @param qname:
        """
        
        if self.index:
            self.index.removeProperty(self.rname, qname)

    def deleteSeveral(self, properties):
        """
        Delete property from index.

        DELETE from PROPERTIES where NAME=<<rname>> and PROPNAME=<<pname>> ...

        @param qname:
        """
        
        if self.index:
            self.index.removeSeveralProperties(self.rname, [p.qname() for p in properties])

    def deleteAll(self):
        """
        Delete property from index.

        DELETE from PROPERTIES where NAME=<<rname>> and PROPNAME=<<pname>>

        @param qname:
        """
        
        if self.index:
            self.index.removeResource(self.rname)

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

    def listAll(self):
        """
        List all property names for children of this resource.
        
        SELECT RESOURCENAME, PROPNAME from PROPERTIES
        
        """

        if self.childindex:
            return self.childindex.listAllProperties()
        else:
            return {}

    def search(self, qname, text):
        """
        Search a specific property.
        
        @param qname: C{tuple} of property namespace and name.
        @param text: C{str} containing the text to search for.
        @return: a C{dict} with a C{str} key for the resource name, and a C{set} of
            property name C{tuple}'s as values, for each matching property.
        """
        if self.index:
            return self.index.searchOneProperty(qname, text)
        else:
            return {}

    def searchSeveral(self, qnames, text):
        """
        Search several properties.
        
        @param qnames: a C{list} of C{tuple}'s of property namespace and name.
        @param text: C{str} containing the text to search for.
        @return: a C{dict} with a C{str} key for the resource name, and a C{set} of
            property name C{tuple}'s as values, for each matching property.
        """
        if self.index:
            return self.index.searchSeveralProperties(qnames, text)
        else:
            return {}

    def searchAll(self, text):
        """
        Search all properties.
        
        @param text: C{str} containing the text to search for.
        @return: a C{dict} with a C{str} key for the resource name, and a C{set} of
            property name C{tuple}'s as values, for each matching property.
        """
        if self.index:
            return self.index.searchAllProperties(text)
        else:
            return {}


class SQLPropertiesDatabase(AbstractSQLDatabase):
    """
    A database to maintain calendar user proxy group memberships.

    SCHEMA:
    
    Properties Database:
    
    ROW: RESOURCENAME, PROPERTYNAME, PROPERTYOBJECT, PROPERTYVALUE
    
    """
    
    dbType = "SQLPROPERTIES"
    dbFilename = ".db.sqlproperties"
    dbFormatVersion = "1"

    def _encode(cls, name):
        return "{%s}%s" % name

    def _decode(cls, name):
        index = name.find("}")
    
        if (index is -1 or not len(name) > index or not name[0] == "{"):
            raise ValueError("Invalid encoded name: %r" % (name,))
    
        return (name[1:index], name[index+1:])

    _encode = classmethod(_encode)
    _decode = classmethod(_decode)

    def __init__(self, path):
        path = os.path.join(path, SQLPropertiesDatabase.dbFilename)
        super(SQLPropertiesDatabase, self).__init__(path, True, utf8=True)

    def setOnePropertyValue(self, rname, pname, pvalue, hidden):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param hidden: C{True} for a hidden property, C{False} otherwise.
        @param pvalue: a C{str} containing the property value to set.
        """
        
        # Remove what is there, then add it back.
        self._delete_from_db(rname, self._encode(pname))
        self._add_to_db(rname, self._encode(pname), cPickle.dumps(pvalue), pvalue.toxml(), hidden)
        self._db_commit()

    def setSeveralPropertyValues(self, rname, properties):
        """
        Add a set of properties.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pvalue: a C{str} containing the property value to set.
        """
        
        # Remove what is there, then add it back.
        for p in properties:
            self._delete_from_db(rname, self._encode(p[0]))
            self._add_to_db(rname, self._encode(p[0]), cPickle.dumps(p[1]), p[1].toxml(), p[2])
        self._db_commit()

    def getOnePropertyValue(self, rname, pname):
        """
        Get a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to get.
        @return: a C{str} containing the property value.
        """
        
        # Remove what is there, then add it back.
        if DEBUG_LOG:
            log.msg("getPropertyValue: %s \"%s\" \"%s\"" % (self.dbpath, rname, pname))
        members = []
        for row in self._db_execute("select PROPERTYOBJECT from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME = :2", rname, self._encode(pname)):
            members.append(row[0])
        setlength =  len(members)
        if setlength == 0:
            return None
        elif setlength == 1:
            return cPickle.loads(members[0])
        else:
            raise ValueError("Multiple properties of the same name \"%s\" stored for resource \"%s\"" % (pname, rname,))

    def getSeveralPropertyValues(self, rname, pnames):
        """
        Get specified property values from specific resource.
    
        @param rname: a C{str} containing the resource name.
        @param pnames: a C{list} of C{str} containing the name of the properties to get.
        @return: a C{dict} containing property name/value.
        """
        
        # Remove what is there, then add it back.
        if DEBUG_LOG:
            log.msg("getSeveralPropertyValues: %s \"%s\"" % (self.dbpath, pnames))
        properties = {}
        statement = "select PROPERTYNAME, PROPERTYOBJECT from PROPERTIES where RESOURCENAME = :1 and PROPERTYNAME in ("
        args = [rname]
        for i, pname in enumerate(pnames):
            if i != 0:
                statement += ", "
            statement += ":%s" % (i + 2,)
            args.append(self._encode(pname))
        statement += ")"

        for row in self._db_execute(statement, *args):
            properties[self._decode(row[0])] = cPickle.loads(row[1])

        return properties

    def getAllPropertyValues(self, rname, nohidden):
        """
        Get specified property values from specific resource.
    
        @param rname: a C{str} containing the resource name.
        @param nohidden: C{True} to not return hidden properties, C{False} otherwise.
        @return: a C{dict} containing property name/value.
        """
        
        properties = {}
        if nohidden:
            statement = "select PROPERTYNAME, PROPERTYOBJECT from PROPERTIES where RESOURCENAME = :1 and HIDDEN = 'F'"
        else:
            statement = "select PROPERTYNAME, PROPERTYOBJECT from PROPERTIES where RESOURCENAME = :1"
        for row in self._db_execute(statement, rname):
            properties[self._decode(row[0])] = cPickle.loads(row[1])

        return properties

    def getSeveralResourcePropertyValues(self, pnames):
        """
        Get specified property values from all resources.
    
        @param pnames: a C{list} of C{str} containing the name of the properties to get.
        @return: a C{dict} containing C{str} keys (names of child resources) and C{dict} values of property name/value.
        """
        
        # Remove what is there, then add it back.
        if DEBUG_LOG:
            log.msg("getAllPropertyValues: %s \"%s\"" % (self.dbpath, pnames))
        members = {}
        statement = "select RESOURCENAME, PROPERTYNAME, PROPERTYOBJECT from PROPERTIES where PROPERTYNAME in ("
        args = []
        for i, pname in enumerate(pnames):
            if i != 0:
                statement += ", "
            statement += ":%s" % (i + 1,)
            args.append(self._encode(pname))
        statement += ")"

        for row in self._db_execute(statement, *args):
            members.setdefault(row[0], {})[self._decode(row[1])] = cPickle.loads(row[2])

        return members

    def getAllResourcePropertyValues(self, nohidden):
        """
        Get specified property values from all resources.
    
        @param nohidden: C{True} to not return hidden properties, C{False} otherwise.
        @return: a C{dict} containing C{str} keys (names of child resources) and C{dict} values of property name/value.
        """
        
        members = {}
        if nohidden:
            statement = "select RESOURCENAME, PROPERTYNAME, PROPERTYOBJECT from PROPERTIES where HIDDEN='F'"
        else:
            statement = "select RESOURCENAME, PROPERTYNAME, PROPERTYOBJECT from PROPERTIES"
        for row in self._db_execute(statement):
            members.setdefault(row[0], {})[self._decode(row[1])] = cPickle.loads(row[2])

        return members

    def removeProperty(self, rname, pname):
        """
        Remove a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to remove.
        @return: a C{str} containing the property value.
        """

        self._delete_from_db(rname, self._encode(pname))
        self._db_commit()

    def removeSeveralProperties(self, rname, pnames):
        """
        Remove specified properties.
    
        @param rname: a C{str} containing the resource name.
        @param pnames: a C{list} containing the names of the properties to remove.
        @return: a C{str} containing the property value.
        """

        for pname in pnames:
            self._delete_from_db(rname, self._encode(pname))
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
            members.add(self._decode(row[0]))
        return members

    def listAllProperties(self):
        """
        List all properties in child resources.
    
        @param rname: a C{str} containing the resource name.
        @return: a C{dict} containing the resource names and property names.
        """

        results = {}
        for row in self._db_execute("select RESOURCENAME, PROPERTYNAME from PROPERTIES"):
            results.setdefault(row[0], set()).add(self._decode(row[1]))
        return results

    def searchOneProperty(self, pname, text):
        results = {}
        liketext = "%%%s%%" % (text,)
        for row in self._db_execute("select RESOURCENAME, PROPERTYNAME from PROPERTIES where PROPERTYNAME = :1 and PROPERTYVALUE like :2", self._encode(pname), liketext):
            results.setdefault(row[0], set()).add(self._decode(row[1]))
        return results

    def searchSeveralProperties(self, pnames, text):
        results = {}
        liketext = "%%%s%%" % (text,)
        statement = "select RESOURCENAME, PROPERTYNAME from PROPERTIES where ("
        args = []
        count = 1
        for p in pnames:
            if count != 1:
                statement += " or "
            statement += "PROPERTYNAME = :%s" % (count,)
            args.append(self._encode(p))
            count += 1
        statement += ") and PROPERTYVALUE like :%s" % (count,)
        args.append(liketext)
        for row in self._db_execute(statement, *args):
            results.setdefault(row[0], set()).add(self._decode(row[1]))
        return results

    def searchAllProperties(self, text):
        results = {}
        liketext = "%%%s%%" % (text,)
        for row in self._db_execute("select RESOURCENAME, PROPERTYNAME from PROPERTIES where PROPERTYVALUE like :1", liketext):
            results.setdefault(row[0], set()).add(self._decode(row[1]))
        return results

    def _add_to_db(self, rname, pname, pobject, pvalue, hidden):
        """
        Add a property.
    
        @param rname: a C{str} containing the resource name.
        @param pname: a C{str} containing the name of the property to set.
        @param pobject: a C{str} containing the pickled representation of the property object.
        @param pvalue: a C{str} containing the text of the property value to set.
        """
        
        if hidden:
            hidden_value = "T"
        else:
            hidden_value = "F"
        self._db_execute(
            """
            insert into PROPERTIES (RESOURCENAME, PROPERTYNAME, PROPERTYOBJECT, PROPERTYVALUE, HIDDEN)
            values (:1, :2, :3, :4, :5)
            """, rname, pname, pobject, pvalue, hidden_value
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
