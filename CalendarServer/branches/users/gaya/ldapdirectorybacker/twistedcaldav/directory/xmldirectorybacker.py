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

from calendarserver.platform.darwin.od import dsattributes, dsquery

from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twistedcaldav.directory.xmlfile import XMLDirectoryService
from twistedcaldav.directory.opendirectorybacker import ABDirectoryQueryResult, dsFilterFromAddressBookFilter


class XMLDirectoryBackingService(XMLDirectoryService):
    """
    Directory backer for L{XMLDirectoryService}.
    """
    
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
                        "firstName" :       dsattributes.kDS1AttrFirstName,
                        "lastName" :        dsattributes.kDS1AttrLastName,
                        "emailAddresses" :  dsattributes.kDSNAttrEMailAddress,
                     },
                },
            },
            "maxQueryResults":0,            # max records returned
            "sortResults":True,             # sort results by UID
            "implementNot":True,            # implement Not query by listing all records and subtracting
       }

        #params = self.getParams(params, defaults, ignored)
        def addDefaults(params, defaults, remove=None):
            for key in defaults:
                if not key in params:
                    params[key] = defaults[key]
            return params
            
        params = addDefaults(params, defaults)
        self.log_debug("_actuallyConfigure after addDefaults: params=%s" % (params,))
        
        # super does not like these extra params
        directoryBackedAddressBook=params["directoryBackedAddressBook"]
        #del params["directoryBackedAddressBook"]
        rdnSchema=params["rdnSchema"]
        del params["rdnSchema"]
        maxQueryResults=params["maxQueryResults"]
        del params["maxQueryResults"]
        sortResults=params["sortResults"]
        del params["sortResults"]
        implementNot=params["implementNot"]
        del params["implementNot"]

        
        assert directoryBackedAddressBook is not None
        self.directoryBackedAddressBook = directoryBackedAddressBook
        
        self.maxQueryResults = maxQueryResults
        self.sortResults = sortResults
        self.implementNot = implementNot
        self.rdnSchema = rdnSchema

        
        super(XMLDirectoryBackingService, self).__init__(params)
        
 
    def createCache(self):
         succeed(None)
                        

    @inlineCallbacks
    def doAddressBookQuery(self, addressBookFilter, addressBookQuery, maxResults ):
        """
        Get vCards for a given addressBookFilter and addressBookQuery
        """
    
        results = []
        limited = False

        for queryType in self.recordTypes():

            queryMap = self.rdnSchema[queryType]
            vcardPropToDirRecordAttrMap = queryMap["vcardPropToDirRecordAttrMap"]
            dirRecordAttrToDSAttrMap = queryMap["dirRecordAttrToDSAttrMap"]
            
            kind = {self.recordType_groups:"group",
                    self.recordType_locations:"location",
                    self.recordType_resources:"calendarresource",
                    }.get(queryType, "individual")
        
            constantProperties = ABDirectoryQueryResult.constantProperties.copy()
            constantProperties["KIND"] = kind

            allRecords, filterAttributes, dsFilter  = dsFilterFromAddressBookFilter( addressBookFilter, vcardPropToDirRecordAttrMap, constantProperties=constantProperties );
            self.log_debug("doAddressBookQuery: queryType=\"%s\" LDAP allRecords=%s, filterAttributes=%s, query=%s" % (queryType, allRecords, filterAttributes, "None" if dsFilter is None else dsFilter.generate(),))
    
            
            if allRecords:
                dsFilter = None #  None expression == all Records
                
            # stop query for all
            clear = not allRecords and not dsFilter
                        
            if not clear:
                
                @inlineCallbacks
                def recordsForDSFilter(dsFilter, recordType):
                    
                    """
                        recordsForDSFilter() exercises the dsFilter expression tree and recordsMatchingFields() it make little difference to the result of
                        a addressbook query because of filtering.
                    """

                    if not isinstance(dsFilter, dsquery.expression):
                        #change  match list  into an expression and recurse
                        returnValue((yield recordsForDSFilter(dsquery.expression( dsquery.expression.OR, (dsFilter,)), recordType)))

                    else:
                        #self.log_debug("recordsForDSFilter:  dsFilter=%s" % (dsFilter.generate(), ))
                        dsFilterSubexpressions = dsFilter.subexpressions if isinstance(dsFilter.subexpressions, list) else (dsFilter.subexpressions,)
                        #self.log_debug("recordsForDSFilter: #subs %s" % (len(dsFilterSubexpressions), ))
                        
                         # evaluate matches
                        matches = [match for match in dsFilterSubexpressions if isinstance(match, dsquery.match)]
                        fields = []
                        for match in matches:
                            #self.log_debug("recordsForDSFilter: match=%s" % (match.generate(), ))
                            xmlMatchType = {
                                dsattributes.eDSExact :        "exact",
                                dsattributes.eDSStartsWith :   "starts-with",
                                dsattributes.eDSContains :     "contains",
                            }.get(match.matchType)
                            if not xmlMatchType:
                                self.log_debug("recordsForDSFilter: match type=%s match not supported" % (match.generate(), ))
                                returnValue(None) # match type not supported by recordsMatchingFields()
                            
                            fields += ((match.attribute, match.value, True, xmlMatchType,),)
                            #self.log_debug("recordsForDSFilter: fields=%s" % (fields,))
                        
                        # if there were matches, call get records that match
                        result = None
                        if len(fields):
                            operand = "and" if dsFilter.operator == dsquery.expression.AND else "or"
                            #self.log_debug("recordsForDSFilter: recordsMatchingFields(fields=%s, operand=%s, recordType=%s)" % (fields, operand, recordType,))
                            result = set((yield self.recordsMatchingFields(fields, operand=operand, recordType=recordType)))
                            #self.log_debug("recordsForDSFilter: result=%s" % (result,))
                            if dsFilter.operator == dsquery.expression.NOT:
                                if self.implementNot:
                                    result = (yield self.listRecords(queryType)).difference(result)
                                else:
                                    self.log_debug("recordsForDSFilter: NOT expression not supported" % (match.generate(), ))
                                    returnValue(None)
                                    

                        # evaluate subexpressions
                        subexpressions = [subexpression for subexpression in dsFilterSubexpressions if isinstance(subexpression, dsquery.expression)]
                        for subexpression in subexpressions:
                            #self.log_debug("recordsForDSFilter: subexpression=%s" % (subexpression.generate(), ))
                            subresult = (yield recordsForDSFilter(subexpression, recordType))
                            #self.log_debug("recordsForDSFilter: subresult=%s" % (subresult,))
                            if subresult is None:
                                returnValue(None)
                            
                            if dsFilter.operator == dsquery.expression.NOT:
                                if self.implementNot:
                                    result = (yield self.listRecords(queryType)).difference(subresult)
                                else:
                                    self.log_debug("recordsForDSFilter: NOT expression not supported" % (match.generate(), ))
                                    returnValue(None)
                            elif result is None:
                                result = subresult
                            elif dsFilter.operator == dsquery.expression.OR:
                                result = result.union(subresult)
                            else:
                                result = result.intersection(subresult)

                    #self.log_debug("recordsForDSFilter:  dsFilter=%s returning %s" % (dsFilter.generate(), result, ))
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

                    result = None
                    dsRecordAttributes = dsRecordAttributesFromDirectoryRecord( xmlDirectoryRecord )
                    try:
                        result = ABDirectoryQueryResult(self.directoryBackedAddressBook, dsRecordAttributes,)
                    except:
                        traceback.print_exc()
                        self.log_info("Could not get vcard for %s" % (xmlDirectoryRecord,))
                    else:
                        if addressBookFilter.match(result.vCard()):
                            self.log_debug("doAddressBookQuery: VCard text =\n%s" % (result.vCard(),))
                            results.append(result)
                        else:
                            # should also filter for duplicate UIDs
                            self.log_debug("doAddressBookQuery did not match filter: %s (%s)" % (result.vCard().propertyValue("FN"), result.vCard().propertyValue("UID"),))
                
                if len(results) >= maxResults:
                    limited = True
                    break
                         
        #sort results so that CalDAVTester can have consistent results when it uses limits
        if self.sortResults:
            results = sorted(list(results), key=lambda result:result.vCard().propertyValue("UID"))

        self.log_info("limited  %s len(results) %s" % (limited,len(results),))
        returnValue((results, limited,))        

