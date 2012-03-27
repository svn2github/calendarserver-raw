##
# Copyright (c) 2006-2012 Apple Inc. All rights reserved.
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
Apple Open Directory directory service implementation for backing up directory-backed address books
"""

__all__ = [
    "XMLDirectoryBackingService",
]

import traceback
import hashlib

import os
import sys
import time

from socket import getfqdn

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, succeed

from twistedcaldav.directory.directory import DirectoryRecord
from twistedcaldav.directory.xmlfile import XMLDirectoryService

from twistedcaldav.directory.opendirectorybacker import ABDirectoryQueryResult, dsFilterFromAddressBookFilter
from calendarserver.platform.darwin.od import dsattributes, dsquery


class XMLDirectoryBackingService(XMLDirectoryService):
    """
    """
    
    node="/Search"
    def __repr__(self):
        return "<%s %r>" % (self.__class__.__name__, self.realmName, )

    def __init__(self, params):
        self._actuallyConfigure(**params)

    def _actuallyConfigure(self, **params):
        
        self.log_debug("_actuallyConfigure: params=%s" % (params,))
        defaults = {
            "recordTypes": (self.recordType_users,), 
            "rdnSchema": {
                self.recordType_users : { 
                    "vcardPropToDirRecordAttrMap" : { 
                        "FN" : (
                                "fullName",
                                "shortNames",
                                "firstName",
                                "lastName",
                                ),
                        "N" : (
                                "fullName",
                                "shortNames",
                                "firstName",
                                "lastName",
                                ),
                        "EMAIL" : "emailAddresses",
                        "UID" : "guid",
                     },
                     "dirRecordAttrToDSAttrMap" : { 
                        "guid" :            dsattributes.kDS1AttrGeneratedUID,
                        "fullName" :        dsattributes.kDS1AttrDistinguishedName,
                        #"shortNames" :      dsattributes.kDSNAttrRecordName,
                        "firstName" :       dsattributes.kDS1AttrFirstName,
                        "lastName" :        dsattributes.kDS1AttrLastName,
                        "emailAddresses" :  dsattributes.kDSNAttrEMailAddress,
                     },
                },
            },
            "maxQueryRecords":0,            # max records returned
       }

        #params = self.getParams(params, defaults, ignored)
        def addDefaults(params, defaults, remove=None):
            keys = set(params.keys())

            for key in defaults:
                if not key in params:
                    params[key] = defaults[key]
            return params
            
        params = addDefaults(params, defaults)
        self.log_debug("_actuallyConfigure after addDefaults: params=%s" % (params,))
        
        # super does not like these extra params
        directoryBackedAddressBook=params["directoryBackedAddressBook"]
        #del params["directoryBackedAddressBook"]
        maxQueryRecords=params["maxQueryRecords"]
        del params["maxQueryRecords"]
        rdnSchema=params["rdnSchema"]
        del params["rdnSchema"]

        
        assert directoryBackedAddressBook is not None
        self.directoryBackedAddressBook = directoryBackedAddressBook
        
        self.maxQueryRecords = maxQueryRecords
        self.rdnSchema = rdnSchema

                
        self.realmName = None # needed for super        
        
        super(XMLDirectoryBackingService, self).__init__(params)
        
         ### self.defaultNodeName used by ABDirectoryQueryResult.
        # get this now once
        hostname = getfqdn()
        if hostname:
            self.defaultNodeName = "/LDAPv3/" + hostname
        else:
            self.defaultNodeName = None
        
 
    def __cmp__(self, other):
        if not isinstance(other, DirectoryRecord):
            return super(DirectoryRecord, self).__eq__(other)

        for attr in ("directory", "node"):
            diff = cmp(getattr(self, attr), getattr(other, attr))
            if diff != 0:
                return diff
        return 0

    def __hash__(self):
        h = hash(self.__class__.__name__)
        for attr in ("node",):
            h = (h + hash(getattr(self, attr))) & sys.maxint
        return h
    
    def createCache(self):
         succeed(None)
                        

    @inlineCallbacks
    def doAddressBookQuery(self, addressBookFilter, addressBookQuery, maxResults ):
        """
        Get vCards for a given addressBookFilter and addressBookQuery
        """
    
        queryRecords = []
        limited = False

         #calc maxRecords from passed in maxResults allowing extra for second stage filtering in caller
        maxRecords = int(maxResults * 1.2)
        if self.maxQueryRecords and maxRecords > self.maxQueryRecords:
            maxRecords = self.maxQueryRecords

        for queryType in self.recordTypes():

            queryMap = self.rdnSchema[queryType]
            vcardPropToDirRecordAttrMap = queryMap["vcardPropToDirRecordAttrMap"]
            dirRecordAttrToDSAttrMap = queryMap["dirRecordAttrToDSAttrMap"]

            allRecords, filterAttributes, dsFilter  = dsFilterFromAddressBookFilter( addressBookFilter, vcardPropToDirRecordAttrMap );
            self.log_debug("doAddressBookQuery: queryType=\"%s\" LDAP allRecords=%s, filterAttributes=%s, query=%s" % (queryType, allRecords, filterAttributes, "None" if dsFilter is None else dsFilter.generate(),))
    
            
            if allRecords:
                dsFilter = None #  None expression == all Records
                
            # stop query for all
            clear = not allRecords and not dsFilter
                        
            if not clear:
                
                @inlineCallbacks
                def recordsForDSFilter(dsFilter, recordType):
                    
                    """
                        Although this exercises the dsFilter expression tree and recordsMatchingFields() it make little difference to the result of
                        a addressbook query because of post filtering.
                    """
                    #returnValue(None) # dsquery.expression.NOT not supported by recordsMatchingFields()

                    if not isinstance(dsFilter, dsquery.expression):
                        #change  match list  into an expression and recurse
                        returnValue((yield recordsForDSFilter(dsquery.expression( dsquery.expression.OR, (dsFilter,)), recordType)))
                        
                    elif dsFilter.operator == dsquery.expression.NOT:
                        self.log_debug("recordsForDSFilter:  dsFilter-%s NOT NONE" % (dsFilter.generate(), ))
                        returnValue(None) # dsquery.expression.NOT not supported by recordsMatchingFields()
                    else:
                        self.log_debug("recordsForDSFilter:  dsFilter=%s" % (dsFilter.generate(), ))
                        self.log_debug("recordsForDSFilter: #subs %s" % (len(dsFilter.subexpressions), ))
                        
                        # evaluate matches
                        matches = [match for match in dsFilter.subexpressions if isinstance(match, dsquery.match)]
                        fields = []
                        for match in matches:
                            self.log_debug("recordsForDSFilter: match=%s" % (match.generate(), ))
                            xmlMatchType = {
                                dsattributes.eDSExact :        "exact",
                                dsattributes.eDSStartsWith :   "starts-with",
                                dsattributes.eDSContains :     "contains",
                            }.get(match.matchType)
                            if not xmlMatchType:
                                self.log_debug("recordsForDSFilter: match=%s match type not supported" % (match.generate(), ))
                                returnValue(None) # match type not supported by recordsMatchingFields()
                            
                            fields += ((match.attribute, match.value, True, xmlMatchType,),)
                            self.log_debug("recordsForDSFilter: fields=%s" % (fields,))
                        
                        # if there were matches, call get records that match
                        result = None
                        if len(fields):
                            operand = "and" if dsFilter.operator == dsquery.expression.AND else "or"
                            self.log_debug("recordsForDSFilter: recordsMatchingFields(fields=%s, operand=%s, recordType=%s)" % (fields, operand, recordType,))
                            result = set((yield self.recordsMatchingFields(fields, operand=operand, recordType=recordType)))
                            self.log_debug("recordsForDSFilter: result=%s" % (result,))

                        # evaluate subexpressions
                        subexpressions = [subexpression for subexpression in dsFilter.subexpressions if isinstance(subexpression, dsquery.expression)]
                        for subexpression in subexpressions:
                            self.log_debug("recordsForDSFilter: subexpression=%s" % (subexpression.generate(), ))
                            subresult = (yield recordsForDSFilter(subexpression, recordType))
                            self.log_debug("recordsForDSFilter: subresult=%s" % (subresult,))
                            if subresult is None:
                                returnValue(None)
                            
                            if result is None:
                                result = subresult
                            elif dsFilter.operator == dsquery.expression.OR:
                                result = result.union(subresult)
                            else:
                                result = result.intersection(subresult)

                    self.log_debug("recordsForDSFilter:  dsFilter=%s returning %s" % (dsFilter.generate(), result, ))
                    returnValue(result)
                                
                # walk the expression tree
                if allRecords:
                    xmlDirectoryRecords = None
                else:
                    xmlDirectoryRecords = (yield recordsForDSFilter(dsFilter, queryType))
                self.log_debug("doAddressBookQuery: #xmlDirectoryRecords %s" % (len(xmlDirectoryRecords) if xmlDirectoryRecords is not None else xmlDirectoryRecords, ))
                
                if xmlDirectoryRecords is None:
                    xmlDirectoryRecords = (yield self.listRecords(queryType))
                    self.log_debug("doAddressBookQuery: all #xmlDirectoryRecords %s" % (len(xmlDirectoryRecords), ))
                
                #sort so that CalDAVTester can have consistent results when it uses limits
                xmlDirectoryRecords = sorted(list(xmlDirectoryRecords), key=lambda x:x.guid)
                
                """ no good reason to use limit here, let caller do it
                # apply limit
                if len(xmlDirectoryRecords) > maxRecords:
                     xmlDirectoryRecords = xmlDirectoryRecords[:maxRecords]
                     self.log_debug("doAddressBookQuery: #xmlDirectoryRecords after max %s" % (len(xmlDirectoryRecords), ))
                """
                   
                for xmlDirectoryRecord in xmlDirectoryRecords:
                    
                    def dsRecordAttributesFromDirectoryRecord( xmlDirectoryRecord ):
                        dsRecordAttributes = {}
                        for attr in dirRecordAttrToDSAttrMap:
                            try:
                                value = getattr(xmlDirectoryRecord, attr)
                                dsRecordAttributes[dirRecordAttrToDSAttrMap[attr]] = value
                            except AttributeError:
                                # No value
                                pass
                        return dsRecordAttributes

                    dsRecord = None
                    dsRecordAttributes = dsRecordAttributesFromDirectoryRecord( xmlDirectoryRecord )
                    try:
                        dsRecord = ABDirectoryQueryResult(self.directoryBackedAddressBook, dsRecordAttributes,)
                        vCardText = dsRecord.vCardText()
                   
                    except:
                        traceback.print_exc()
                        self.log_info("Could not get vcard for ds record %s" % (dsRecord,))
                    else:
                        self.log_debug("doAddressBookQuery: VCard text =\n%s" % (vCardText, ))
                        queryRecords.append(dsRecord)
                
                
                # only get requested number of record results
                maxRecords -= len(xmlDirectoryRecords)
                if maxRecords <= 0:
                    limited = True
                    break

                         
        self.log_info("limited  %s len(queryRecords) %s" % (limited,len(queryRecords),))
        returnValue((queryRecords, limited,))        

