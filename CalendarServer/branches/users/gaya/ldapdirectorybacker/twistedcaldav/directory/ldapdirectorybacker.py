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
    "LdapDirectoryBackingService", "VCardRecord",
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
from twistedcaldav.directory.opendirectorybacker import VCardRecord, dsFilterFromAddressBookFilter, propertiesInAddressBookQuery


class LdapDirectoryBackingService(LdapDirectoryService):
    """
    """

    baseGUID = "BF07A1A2-5BB5-4A4D-A59A-67260EA7E143"
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
                "queryTypes": ("people",),
                "people": {
                    "rdn":"ou=people",
                    "searchMap" : { # maps vCard properties to searchable ldap attributes
                        "FN" : "cn",
                     },
                    "ldapDSAttrMap" : { # maps ldap attributes to ds record types
                        "cn" : "dsAttrTypeStandard:RealName",
                     },
                },
            },
            "appleInternalServer":False,         # does magic in VCardRecord
            "maxQueryRecords":0,                 # max records returned
            "fakeETag":True,                     # eTag is fake, otherwise it is md5(all attributes)
            "generateSimpleUIDs":False,    # if UID is faked, use simple method for generating
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
        maxQueryRecords=params["maxQueryRecords"]
        del params["maxQueryRecords"]
        fakeETag=params["fakeETag"]
        del params["fakeETag"]
        generateSimpleUIDs=params["generateSimpleUIDs"]
        del params["generateSimpleUIDs"]

        
        #standardize ds attributes type names
        # or we could just require dsAttrTypeStandard: prefix in the plist
        rdnSchema = params["rdnSchema"];
        for queryType in rdnSchema["queryTypes"]:
            ldapDSAttrMap = rdnSchema[queryType]["ldapDSAttrMap"]
            for ldapAttrName, dsAttrNames in ldapDSAttrMap.iteritems():
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
                    ldapDSAttrMap[ldapAttrName] = normalizedDSAttrNames
                else:
                    ldapDSAttrMap[ldapAttrName] = normalizedDSAttrNames[0]
               
                
        self.log_debug("_actuallyConfigure after clean: params=%s" % (params,))
 
        assert directoryBackedAddressBook is not None
        self.directoryBackedAddressBook = directoryBackedAddressBook
        
        self.maxQueryRecords = maxQueryRecords

                
        self.realmName = None # needed for super        
        
        ### used by VCardRecord.
        self.fakeETag = fakeETag
        self.generateSimpleUIDs = generateSimpleUIDs
        self.appleInternalServer = appleInternalServer
 
        super(LdapDirectoryBackingService, self).__init__(params)
        
         ### self.defaultNodeName used by VCardRecord.
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
                        
    #to do: use opendirectorybacker: _attributesForAddressBookQuery
    def _ldapAttributesForAddressBookQuery(self, addressBookQuery, ldapDSAttrMap ):
                        
        etagRequested, propertyNames = propertiesInAddressBookQuery( addressBookQuery )
        
        if etagRequested and not self.fakeETag:
            propertyNames = None
        
        if not propertyNames:
            result = ldapDSAttrMap.keys()
            self.log_debug("_ldapAttributesForAddressBookQuery returning all props=%s" % result)
        
        else:
            queryAttributes = []
            for prop in propertyNames:
                searchAttr = ldapDSAttrMap.get()
                if searchAttr:
                    print("adding attributes %r" % searchAttr)
                    if not isinstance(searchAttr, tuple):
                        searchAttr = (searchAttr, )
                    queryAttributes += searchAttr

            result = list(set(queryAttributes))
            self.log_debug("_ldapAttributesForAddressBookQuery returning=%s" % result)
        
        return result

 
    @inlineCallbacks
    def vCardRecordsForAddressBookQuery(self, addressBookFilter, addressBookQuery, maxResults ):
        """
        Get vCards for a given addressBookFilter and addressBookQuery
        """
    
        queryRecords = []
        limited = False

         #calc maxRecords from passed in maxResults allowing extra for second stage filtering in caller
        maxRecords = int(maxResults * 1.2)
        if self.maxQueryRecords and maxRecords > self.maxQueryRecords:
            maxRecords = self.maxQueryRecords

        for queryType in self.rdnSchema["queryTypes"]:

            queryMap = self.rdnSchema[queryType]
            searchMap = queryMap["searchMap"]
            ldapDSAttrMap = queryMap["ldapDSAttrMap"]

            allRecords, filterAttributes, dsFilter  = dsFilterFromAddressBookFilter( addressBookFilter, searchMap );
            #print("allRecords = %s, query = %s" % (allRecords, "None" if dsFilter is None else dsFilter.generate(),))
            self.log_debug("vCardRecordsForAddressBookQuery: queryType = \"%s\" LDAP allRecords = %s, filterAttributes = %s, query = %s" % (queryType, allRecords, filterAttributes, "None" if dsFilter is None else dsFilter.generate(),))
    
            
            if allRecords:
                dsFilter = None #  None expression == all Records
                
            clear = not allRecords and not dsFilter
            clear = False
                    
            if not clear:
                queryAttributes = self._ldapAttributesForAddressBookQuery( addressBookQuery, ldapDSAttrMap )
                attributes = filterAttributes + queryAttributes if queryAttributes else None
                self.log_debug("vCardRecordsForAddressBookQuery: attributes = %s, queryAttributes = %s" % (attributes, queryAttributes,))
                
                if queryMap.get("getAllAttributes"):
                    attributes = None
                               
                   
                rdn = queryMap["rdn"]
                base =  ldap.dn.str2dn(rdn) + self.base
                
                #add additonal filter
                queryFilter = queryMap.get("filter")
                if dsFilter and queryFilter:
                    filterstr = "(&%s%s)" % (queryFilter, dsFilter.generate())
                elif queryFilter:
                    filterstr = queryFilter
                elif dsFilter:
                    filterstr = dsFilter.generate()
                
                #attrlist = attributes
                #filterstr = "(&(!(objectClass=organizationalUnit))(sn=Gaya))"
                #attrlist = ("cn", "sn", "objectClass", "mail", "telephoneNumber", "appleDSID")
                self.log_debug("LDAP query with base %s and filter %s and attributes %s sizelimit %s" % (ldap.dn.dn2str(base), filterstr, attributes, maxRecords))
                
                ldapSearchResult = (yield self.timedSearch(ldap.dn.dn2str(base), ldap.SCOPE_SUBTREE, filterstr=filterstr, attrlist=attributes, timeout=maxRecords, sizelimit=maxRecords))
    
                self.log_debug("ldapSearchResult=%s" % (ldapSearchResult,))
                
                for dn, ldapAttributes in ldapSearchResult:
                    #dn = normalizeDNstr(dn)
                    dsRecord = None
                    try:
                        dsRecordAttributes = {}
                        
                        for ldapAttributeName, ldapAttributeValues in ldapAttributes.iteritems():
    
                            self.log_debug("inspecting ldapAttributeName %s with values %s" % (ldapAttributeName, ldapAttributeValues,))
    
                            # get rid of '' values
                            ldapAttributeValues = [attr for attr in ldapAttributeValues if len(attr)]
                            
                            if len(ldapAttributeValues):
                                dsAttributeNames = ldapDSAttrMap.get(ldapAttributeName)
                                if dsAttributeNames:
                                    
                                    if not isinstance(dsAttributeNames, list):
                                        dsAttributeNames = [dsAttributeNames,]
                                        
                                    for dsAttributeName in dsAttributeNames:
                                    
                                        if dsAttributeName in VCardRecord.binaryDSAttributeStrs:
                                            ldapAttributeValues = [attr.encode('base64') for attr in ldapAttributeValues]
                                        
                                        # all dsRecordAttributes values are lists
                                        if dsAttributeName not in dsRecordAttributes:
                                            dsRecordAttributes[dsAttributeName] = list()
                                            
                                        dsRecordAttributes[dsAttributeName] = list(set(dsRecordAttributes[dsAttributeName] + ldapAttributeValues))
                                        self.log_debug("dsRecordAttributes[%s] = %s" % (dsAttributeName, dsRecordAttributes[dsAttributeName],))
                                   
                        dsRecord = VCardRecord(self, dsRecordAttributes, defaultNodeName=None, generateSimpleUIDs=self.generateSimpleUIDs, appleInternalServer=self.appleInternalServer)
                        vCardText = dsRecord.vCardText()
                    except:
                        traceback.print_exc()
                        self.log_info("Could not get vcard for ds record %s" % (dsRecord,))
                    else:
                        self.log_debug("VCard text =\n%s" % (vCardText, ))
                        queryRecords.append(dsRecord)
               
                self.log_info("maxRecords was %s len(ldapSearchResult) %s" % (maxRecords,len(ldapSearchResult),))
                maxRecords -= len(ldapSearchResult)
                self.log_info("maxRecords now %s len(ldapSearchResult) %s" % (maxRecords,len(ldapSearchResult),))
                if maxRecords <= 0:
                    limited = True
                    self.log_info("limited %s" % (limited,))
                    break

                         
        self.log_info("limited  %s len(queryRecords) %s" % (limited,len(queryRecords),))
        returnValue((queryRecords, limited,))        

