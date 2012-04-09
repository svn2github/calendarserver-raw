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
    "LdapDirectoryBackingService",
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
from twistedcaldav.directory.ldapdirectory import LdapDirectoryService

import ldap
from twistedcaldav.directory.opendirectorybacker import ABDirectoryQueryResult, dsFilterFromAddressBookFilter, propertiesInAddressBookQuery


class LdapDirectoryBackingService(LdapDirectoryService):
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
            "recordTypes": (), # for super
            "rdnSchema": {
                "base": "dc=example,dc=com",
                "queries": (
                    { #people
                        "rdn":"ou=people",
                        "vcardPropToLdapAttrMap" : { # maps vCard properties to searchable ldap attributes
                            "FN" : "cn",
                         },
                        "ldapAttrToDSAttrMap" : { # maps ldap attributes to ds attribute types
                            "cn" : "dsAttrTypeStandard:RealName",
                         },
                    },
                ),

            },
            "appleInternalServer":False,    # does magic in ABDirectoryQueryResult
            "maxQueryResults":0,            # max records returned
            "fakeETag":True,                # eTag is fake, otherwise it is md5(all attributes)
            "generateSimpleUIDs":False,     # if UID is faked, use simple method for generating
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
        del params["directoryBackedAddressBook"]
        appleInternalServer=params["appleInternalServer"]
        del params["appleInternalServer"] 
        maxQueryResults=params["maxQueryResults"]
        del params["maxQueryResults"]
        fakeETag=params["fakeETag"]
        del params["fakeETag"]
        generateSimpleUIDs=params["generateSimpleUIDs"]
        del params["generateSimpleUIDs"]

        
        #standardize ds attributes type names
        # or we could just require dsAttrTypeStandard: prefix in the plist
        rdnSchema = params["rdnSchema"];
        for query in rdnSchema["queries"]:
            ldapAttrToDSAttrMap = query["ldapAttrToDSAttrMap"]
            for ldapAttrName, dsAttrNames in ldapAttrToDSAttrMap.iteritems():
                if not isinstance(dsAttrNames, list):
                    dsAttrNames = [dsAttrNames,]
                
                normalizedDSAttrNames = []
                for dsAttrName in dsAttrNames:
                    if not dsAttrName.startswith("dsAttrTypeStandard:") and not dsAttrName.startswith("dsAttrTypeNative:"):
                        normalizedDSAttrNames.append("dsAttrTypeStandard:" + dsAttrName)
                    else:
                        normalizedDSAttrNames.append(dsAttrName)
                
                # not needed, but tests code paths
                if len(normalizedDSAttrNames) > 1:
                    ldapAttrToDSAttrMap[ldapAttrName] = normalizedDSAttrNames
                else:
                    ldapAttrToDSAttrMap[ldapAttrName] = normalizedDSAttrNames[0]
               
                
        self.log_debug("_actuallyConfigure after clean: params=%s" % (params,))
 
        assert directoryBackedAddressBook is not None
        self.directoryBackedAddressBook = directoryBackedAddressBook
        
        self.maxQueryResults = maxQueryResults

                
        self.realmName = None # needed for super        
        
        ### params for ABDirectoryQueryResult()
        self.fakeETag = fakeETag
        self.generateSimpleUIDs = generateSimpleUIDs
        self.appleInternalServer = appleInternalServer
 
        super(LdapDirectoryBackingService, self).__init__(params)
        
 
    def __hash__(self):
        h = hash(self.__class__.__name__)
        for attr in ("node",):
            h = (h + hash(getattr(self, attr))) & sys.maxint
        return h
    
    def createCache(self):
         succeed(None)
                        

    def _ldapAttributesForAddressBookQuery(self, addressBookQuery, ldapAttrToDSAttrMap ):
                        
        etagRequested, propertyNames = propertiesInAddressBookQuery( addressBookQuery )
        
        if etagRequested and not self.fakeETag:
            propertyNames = None
        
        if not propertyNames:
            result = ldapAttrToDSAttrMap.keys()
            self.log_debug("_ldapAttributesForAddressBookQuery returning all props=%s" % result)
        
        else:
            queryAttributes = []
            for prop in propertyNames:
                searchAttr = ldapAttrToDSAttrMap.get()
                if searchAttr:
                    print("adding attributes %r" % searchAttr)
                    if not isinstance(searchAttr, tuple):
                        searchAttr = (searchAttr, )
                    queryAttributes += searchAttr

            result = list(set(queryAttributes))
            self.log_debug("_ldapAttributesForAddressBookQuery returning=%s" % result)
        
        return result

 
    @inlineCallbacks
    def _getLdapQueryResults(self, base, queryStr, attributes=None, maxResults=0, ldapAttrToDSAttrMap={} ):
        """
        Get a list of filtered ABDirectoryQueryResult for the given query with the given attributes.
        query == None gets all records. attribute == None gets ABDirectoryQueryResult.allDSQueryAttributes
        """
        limited = False
        resultsDictionary = {}
        
        # can't resist also using a timeout, 1 sec per request result for now
        timeout = maxResults

        self.log_debug("_getLdapQueryResults: LDAP query base=%s and filter=%s and attributes=%s timeout=%s resultLimit=%s" % (ldap.dn.dn2str(base), queryStr, attributes, timeout, maxResults))
        
        ldapSearchResult = (yield self.timedSearch(ldap.dn.dn2str(base), ldap.SCOPE_SUBTREE, filterstr=queryStr, attrlist=attributes, timeoutSeconds=timeout, resultLimit=maxResults))
        self.log_debug("_getLdapQueryResults: ldapSearchResult=%s" % (ldapSearchResult,))
        
        if maxResults and len(ldapSearchResult) >= maxResults:
            limited = True
            self.log_debug("_getLdapQueryResults: limit (= %d) reached." % (maxResults, ))

        for dn, ldapAttributes in ldapSearchResult:
            #dn = normalizeDNstr(dn)
            result = None
            try:
                # make a dsRecordAttributes dict from the ldap attributes
                dsRecordAttributes = {}
                for ldapAttributeName, ldapAttributeValues in ldapAttributes.iteritems():

                    #self.log_debug("inspecting ldapAttributeName %s with values %s" % (ldapAttributeName, ldapAttributeValues,))

                    # get rid of '' values
                    ldapAttributeValues = [attr for attr in ldapAttributeValues if len(attr)]
                    
                    if len(ldapAttributeValues):
                        dsAttributeNames = ldapAttrToDSAttrMap.get(ldapAttributeName)
                        if dsAttributeNames:
                            
                            if not isinstance(dsAttributeNames, list):
                                dsAttributeNames = [dsAttributeNames,]
                                
                            for dsAttributeName in dsAttributeNames:
                                
                                # base64 encode binary attributes
                                if dsAttributeName in ABDirectoryQueryResult.binaryDSAttrNames:
                                    ldapAttributeValues = [attr.encode('base64') for attr in ldapAttributeValues]
                                
                                # add to dsRecordAttributes
                                if dsAttributeName not in dsRecordAttributes:
                                    dsRecordAttributes[dsAttributeName] = list()
                                    
                                dsRecordAttributes[dsAttributeName] = list(set(dsRecordAttributes[dsAttributeName] + ldapAttributeValues))
                                self.log_debug("doAddressBookQuery: dsRecordAttributes[%s] = %s" % (dsAttributeName, dsRecordAttributes[dsAttributeName],))

                # get a record for dsRecordAttributes 
                result = ABDirectoryQueryResult(self.directoryBackedAddressBook, dsRecordAttributes, generateSimpleUIDs=self.generateSimpleUIDs, appleInternalServer=self.appleInternalServer)
            except:
                traceback.print_exc()
                self.log_info("Could not get vcard for %s" % (dn,))
            else:
                uid = result.vCard().propertyValue("UID")

                if uid in resultsDictionary:
                    self.log_info("Record skipped due to duplicate UID: %s" % (dn,))
                    continue
                    
                self.log_debug("VCard text =\n%s" % (result.vCardText(), ))
                resultsDictionary[uid] = result                   

        self.log_debug("%s results (limited=%s)." % (len(resultsDictionary), limited))
        returnValue((resultsDictionary.values(), limited, ))

    @inlineCallbacks
    def doAddressBookQuery(self, addressBookFilter, addressBookQuery, maxResults ):
        """
        Get vCards for a given addressBookFilter and addressBookQuery
        """
    
        results = []
        limited = False
        remainingMaxResults = maxResults
        
        #one ldap query for each rnd in queries
        for queryMap in self.rdnSchema["queries"]:

            rdn = queryMap["rdn"]
            vcardPropToLdapAttrMap = queryMap["vcardPropToLdapAttrMap"]
            ldapAttrToDSAttrMap = queryMap["ldapAttrToDSAttrMap"]

            allRecords, filterAttributes, dsFilter  = dsFilterFromAddressBookFilter( addressBookFilter, vcardPropToLdapAttrMap );
            self.log_debug("doAddressBookQuery: rdn=%s LDAP allRecords=%s, filterAttributes=%s, query=%s" % (rdn, allRecords, filterAttributes, "None" if dsFilter is None else dsFilter.generate(),))
    
            
            if allRecords:
                dsFilter = None #  None expression == all Records
                
            # stop query all
            clear = not allRecords and not dsFilter
                    
            if not clear:
                queryAttributes = self._ldapAttributesForAddressBookQuery( addressBookQuery, ldapAttrToDSAttrMap )
                attributes = filterAttributes + queryAttributes if queryAttributes else None
                self.log_debug("doAddressBookQuery: attributes=%s, queryAttributes=%s" % (attributes, queryAttributes,))
                
                #get all ldap attributes -- for debug
                if queryMap.get("getAllAttributes"):
                    attributes = None
                   
                base =  ldap.dn.str2dn(rdn) + self.base
                
                queryStr = "(cn=*)"    # all results query  - should make a param
                #add additional filter from config
                queryFilter = queryMap.get("filter")
                if dsFilter and queryFilter:
                    queryStr = "(&%s%s)" % (queryFilter, dsFilter.generate())
                elif queryFilter:
                    queryStr = queryFilter
                elif dsFilter:
                    queryStr = dsFilter.generate()

                
                # keep trying ldap query till we get results based on filter.  Especially when doing "all results" query
                remainingMaxResults = maxResults - len(results) if maxResults else 0
                maxLdapResults = int(remainingMaxResults * 1.2)
    
                while True:
                    ldapQueryResults, ldapQueryLimited = (yield self._getLdapQueryResults(base=base, queryStr=queryStr, attributes=attributes, maxResults=maxLdapResults, ldapAttrToDSAttrMap=ldapAttrToDSAttrMap))
                    
                    filteredResults = []
                    for ldapQueryResult in ldapQueryResults:
                        # to do:  filter duplicate UIDs
                        if addressBookFilter.match(ldapQueryResult.vCard()):
                            filteredResults.append(ldapQueryResult)
                        else:
                            self.log_debug("doAddressBookQuery did not match filter: %s (%s)" % (ldapQueryResult.vCard().propertyValue("FN"), ldapQueryResult.vCard().propertyValue("UID"),))
                    
                    #no more results    
                    if not ldapQueryLimited:
                        break;
                    
                    # more than requested results
                    if maxResults and len(filteredResults) > remainingMaxResults:
                        break
                    
                    # more than max report results
                    if len(filteredResults) > config.MaxQueryWithDataResults:
                        break
                    
                    # more than self limit
                    if self.maxQueryResults and maxLdapResults >= self.maxQueryResults:
                        break
                    
                    # try again with 2x
                    maxLdapResults *= 2
                    if self.maxQueryResults and maxLdapResults > self.maxQueryResults:
                        maxLdapResults = self.maxQueryResults
                    
                results += filteredResults
                if maxResults and len(results) > maxResults:
                    break
                
        
        limited = maxResults and len(results) >= maxResults
                         
        self.log_info("limited  %s len(results) %s" % (limited,len(results),))
        returnValue((results, limited,))        

