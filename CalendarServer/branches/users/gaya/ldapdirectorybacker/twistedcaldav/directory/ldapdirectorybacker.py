##
# Copyright (c) 2006-2010 Apple Inc. All rights reserved.
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

from os import listdir
from os.path import join, abspath
from tempfile import mkstemp, gettempdir
from random import random

from pycalendar.n import N
from pycalendar.adr import Adr
from pycalendar.datetime import PyCalendarDateTime

from socket import getfqdn

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, deferredGenerator, succeed
from twext.python.filepath import CachingFilePath as FilePath
from twext.web2.dav import davxml
from twext.web2.dav.element.base import twisted_dav_namespace, dav_namespace, parse_date, twisted_private_namespace
from twext.web2.dav.resource import DAVPropertyMixIn
from twext.web2.dav.util import joinURL
from twext.web2.http_headers import MimeType, generateContentType, ETag


from twistedcaldav import customxml, carddavxml
from twistedcaldav.customxml import calendarserver_namespace
from twistedcaldav.config import config
from twistedcaldav.directory.directory import DirectoryService, DirectoryRecord
from twistedcaldav.directory.ldapdirectory import LdapDirectoryService
from twistedcaldav.memcachelock import MemcacheLock, MemcacheLockTimeoutError
from twistedcaldav.query import addressbookqueryfilter
from twistedcaldav.vcard import Component, Property, vCardProductID

from xmlrpclib import datetime

from calendarserver.platform.darwin.od import dsattributes, dsquery
from twisted.python.reflect import namedModule

import ldap
from twistedcaldav.directory.ldapdirectory import LdapDirectoryService
from twistedcaldav.directory.opendirectorybacker import VCardRecord


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
                    "searchmap" : { # maps vCard properties to searchable ldap attributes
                        "FN" : "cn",
                     },
                    "ldapdsattrmap" : { # maps ldap attributes to ds record types
                        "cn" : "dsAttrTypeStandard:RealName",
                     },
                },
            },
            "appleInternalServer":False,         # does magic in VCardRecord
            "maxQueryRecords":0,                 # max records returned
            "fakeETag":True,                     # eTag is fake, otherwise it is md5(all attributes)
            "standardizeSyntheticUIDs":False,    # if UID is faked, use simple method for generating
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
        standardizeSyntheticUIDs=params["standardizeSyntheticUIDs"]
        del params["standardizeSyntheticUIDs"]

        
        #standardize ds attributes type names
        # or we could just require dsAttrTypeStandard: prefix in the plist
        rdnSchema = params["rdnSchema"];
        for queryType in rdnSchema["queryTypes"]:
            ldapdsattrmap = rdnSchema[queryType]["ldapdsattrmap"]
            for ldapAttrName, dsAttrNames in ldapdsattrmap.iteritems():
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
                    ldapdsattrmap[ldapAttrName] = normalizedDSAttrNames
                else:
                    ldapdsattrmap[ldapAttrName] = normalizedDSAttrNames[0]
               
                
        self.log_debug("_actuallyConfigure after clean: params=%s" % (params,))
 
        assert directoryBackedAddressBook is not None
        self.directoryBackedAddressBook = directoryBackedAddressBook
        
        self.maxQueryRecords = maxQueryRecords

                
        self.realmName = None # needed for super        
        
        ### used by VCardRecord.
        self.fakeETag = fakeETag
        self.addDSAttrXProperties = False
        self.standardizeSyntheticUIDs = standardizeSyntheticUIDs
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
    
    #@inlineCallbacks
    def createCache(self):
         succeed(None)

    #to do: use opendirectorybacker._getDSFilter passing in search map
    def _getLdapFilter(self, addressBookFilter, searchmap):
        """
        Convert the supplied addressbook-query into an expression tree.
    
        @param filter: the L{Filter} for the addressbook-query to convert.
        @return: (needsAllRecords, espressionAttributes, expression) tuple
        """
        def propFilterListQuery(filterAllOf, propFilters):

            def propFilterExpression(filterAllOf, propFilter):
                #print("propFilterExpression")
                """
                Create an expression for a single prop-filter element.
                
                @param propFilter: the L{PropertyFilter} element.
                @return: (needsAllRecords, espressionAttributes, expressions) tuple
                """
                
                def definedExpression( defined, allOf, filterName, constant, queryAttributes, allAttrStrings):
                    if constant or filterName in ("N" , "FN", "UID", ):
                        return (defined, [], [])     # all records have this property so no records do not have it
                    else:
                        matchList = list(set([dsquery.match(attrName, "", dsattributes.eDSStartsWith) for attrName in allAttrStrings]))
                        if defined:
                            return andOrExpression(allOf, queryAttributes, matchList)
                        else:
                            if len(matchList) > 1:
                                expr = dsquery.expression( dsquery.expression.OR, matchList )
                            else:
                                expr = matchList
                            return (False, queryAttributes, [dsquery.expression( dsquery.expression.NOT, expr),])
                    #end isNotDefinedExpression()
    
    
                def andOrExpression(propFilterAllOf, queryAttributes, matchList):
                    #print("andOrExpression(propFilterAllOf=%r, queryAttributes%r, matchList%r)" % (propFilterAllOf, queryAttributes, matchList))
                    if propFilterAllOf and len(matchList):
                        # add OR expression because parent will AND
                        return (False, queryAttributes, [dsquery.expression( dsquery.expression.OR, matchList),])
                    else:
                        return (False, queryAttributes, matchList)
                    #end andOrExpression()
                    
    
                # short circuit parameter filters
                def supportedParamter( filterName, paramFilters, propFilterAllOf ):
                    
                    def supported( paramFilterName, paramFilterDefined, params ):
                        paramFilterName = paramFilterName.upper()
                        if len(params.keys()) and ((paramFilterName in params.keys()) != paramFilterDefined):
                            return False
                        if len(params[paramFilterName]) and str(paramFilter.qualifier).upper() not in params[paramFilterName]:
                            return False
                        return True 
                        #end supported()
                
                    
                    oneSupported = False
                    for paramFilter in paramFilters:
                        if filterName == "PHOTO":
                            if propFilterAllOf != supported( paramFilter.filter_name, paramFilter.defined, { "ENCODING": ["B",], "TYPE": ["JPEG",], }):
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                        elif filterName == "ADR":
                            if propFilterAllOf != supported( paramFilter.filter_name, paramFilter.defined, { "TYPE": ["WORK", "PREF", "POSTAL", "PARCEL",], }):
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                        elif filterName == "LABEL":
                            if propFilterAllOf != supported( paramFilter.filter_name, paramFilter.defined, { "TYPE": ["POSTAL", "PARCEL",]}):
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                        elif filterName == "TEL":
                            if propFilterAllOf != supported( paramFilter.filter_name, paramFilter.defined, { "TYPE": [], }): # has params derived from ds attributes
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                        elif filterName == "EMAIL":
                            if propFilterAllOf != supported( paramFilter.filter_name, paramFilter.defined, { "TYPE": [], }): # has params derived from ds attributes
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                        elif filterName == "URL":
                            if propFilterAllOf != supported( paramFilter.filter_name, paramFilter.defined, {}):
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                        elif filterName == "KEY":
                            if propFilterAllOf != supported( paramFilter.filter_name, paramFilter.defined, { "ENCODING": ["B",], "TYPE": ["PGPPUBILICKEY", "USERCERTIFICATE", "USERPKCS12DATA", "USERSMIMECERTIFICATE",] }):
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                        elif not filterName.startswith("X-"): #X- IMHandles X-ABRELATEDNAMES excepted, no other params are used
                            if propFilterAllOf == paramFilter.defined:
                                return not propFilterAllOf
                            oneSupported |= propFilterAllOf
                    
                    if propFilterAllOf:
                        return True
                    else:
                        return oneSupported
                    #end supportedParamter()
                    
                    
                def textMatchElementExpression( propFilterAllOf, textMatchElement ):
    
                    # pre process text match strings for ds query 
                    def getMatchStrings( propFilter, matchString ):
                                            
                        if propFilter.filter_name in ("REV" , "BDAY", ):
                            rawString = matchString
                            matchString = ""
                            for c in rawString:
                                if not c in "TZ-:":
                                    matchString += c
                        elif propFilter.filter_name == "GEO":
                            matchString = ",".join(matchString.split(";"))
                        
                        if propFilter.filter_name in ("N" , "ADR", "ORG", ):
                            # for structured properties, change into multiple strings for ds query
                            if propFilter.filter_name == "ADR":
                                #split by newline and comma
                                rawStrings = ",".join( matchString.split("\n") ).split(",")
                            else:
                                #split by space
                                rawStrings = matchString.split(" ")
                            
                            # remove empty strings
                            matchStrings = []
                            for oneString in rawStrings:
                                if len(oneString):
                                    matchStrings += [oneString,]
                            return matchStrings
                        
                        elif len(matchString):
                            return [matchString,]
                        else:
                            return []
                        # end getMatchStrings
    
                    if constant:
                        # do the match right now!  Return either all or none.
                        return( textMatchElement.test([constant,]), [], [] )
                    else:

                        matchStrings = getMatchStrings(propFilter, textMatchElement.text)

                        if not len(matchStrings) or binaryAttrStrs:
                            # no searching text in binary ds attributes, so change to defined/not defined case
                            if textMatchElement.negate:
                                return definedExpression(False, propFilterAllOf, propFilter.filter_name, constant, queryAttributes, allAttrStrings)
                            # else fall through to attribute exists case below
                        else:
                            
                            # special case UID's formed from node and record name
                            if propFilter.filter_name == "UID":
                                matchString = matchStrings[0]
                                seperatorIndex = matchString.find(VCardRecord.peopleUIDSeparator)
                                if seperatorIndex > 1:
                                    recordNameStart = seperatorIndex + len(VCardRecord.peopleUIDSeparator)
                                else:
                                    seperatorIndex = matchString.find(VCardRecord.userUIDSeparator)                        
                                    if seperatorIndex > 1:
                                        recordNameStart = seperatorIndex + len(VCardRecord.userUIDSeparator)
                                    else:
                                        recordNameStart = sys.maxint
        
                                if recordNameStart < len(matchString)-1:
                                    try:
                                        recordNameQualifier = matchString[recordNameStart:].decode("base64").decode("utf8")
                                    except Exception, e:
                                        self.log_debug("Could not decode UID string %r in %r: %r" % (matchString[recordNameStart:], matchString, e,))
                                    else:
                                        if textMatchElement.negate:
                                            return (False, queryAttributes, 
                                                    [dsquery.expression(dsquery.expression.NOT, dsquery.match(dsattributes.kDSNAttrRecordName, recordNameQualifier, dsattributes.eDSExact)),]
                                                    )
                                        else:
                                            return (False, queryAttributes, 
                                                    [dsquery.match(dsattributes.kDSNAttrRecordName, recordNameQualifier, dsattributes.eDSExact),]
                                                    )
                            
                            # use match_type where possible depending on property/attribute mapping
                            # Note that case sensitive negate will not work
                            #        Should return all records in that case
                            matchType = dsattributes.eDSContains
                            if propFilter.filter_name in ("NICKNAME" , "TITLE" , "NOTE" , "UID", "URL", "N", "ADR", "ORG", "REV",  "LABEL", ):
                                if textMatchElement.match_type == "equals":
                                        matchType = dsattributes.eDSExact
                                elif textMatchElement.match_type == "starts-with":
                                        matchType = dsattributes.eDSStartsWith
                                elif textMatchElement.match_type == "ends-with":
                                        matchType = dsattributes.eDSEndsWith
                            
                            matchList = []
                            for matchString in matchStrings:
                                matchList += [dsquery.match(attrName, matchString, matchType) for attrName in stringAttrStrs]
                            
                            matchList = list(set(matchList))
    
                            if textMatchElement.negate:
                                if len(matchList) > 1:
                                    expr = dsquery.expression( dsquery.expression.OR, matchList )
                                else:
                                    expr = matchList
                                return (False, queryAttributes, [dsquery.expression( dsquery.expression.NOT, expr),])
                            else:
                                return andOrExpression(propFilterAllOf, queryAttributes, matchList)
    
                    # attribute exists search
                    return definedExpression(True, propFilterAllOf, propFilter.filter_name, constant, queryAttributes, allAttrStrings)
                    #end textMatchElementExpression()
                    
    
                # get attribute strings from dsqueryAttributesForProperty list 
                #queryAttributes = list(set(VCardRecord.dsqueryAttributesForProperty.get(propFilter.filter_name, [])).intersection(set(self.allowedDSQueryAttributes)))
                #queryAttributes = VCardRecord.dsqueryAttributesForProperty.get(propFilter.filter_name, [])
                queryAttributes = searchmap.get(propFilter.filter_name, [])
                if isinstance(queryAttributes, str):
                    queryAttributes = (queryAttributes,)
                
                binaryAttrStrs = []
                stringAttrStrs = []
                for attr in queryAttributes:
                    if isinstance(attr, tuple):
                        binaryAttrStrs.append(attr[0])
                    else:
                        stringAttrStrs.append(attr)
                allAttrStrings = stringAttrStrs + binaryAttrStrs
                                        
                constant = VCardRecord.constantProperties.get(propFilter.filter_name)
                if not constant and not allAttrStrings: 
                    return (False, [], [])
                
                if propFilter.qualifier and isinstance(propFilter.qualifier, addressbookqueryfilter.IsNotDefined):
                    return definedExpression(False, filterAllOf, propFilter.filter_name, constant, queryAttributes, allAttrStrings)
                
                paramFilterElements = [paramFilterElement for paramFilterElement in propFilter.filters if isinstance(paramFilterElement, addressbookqueryfilter.ParameterFilter)]
                textMatchElements = [textMatchElement for textMatchElement in propFilter.filters if isinstance(textMatchElement, addressbookqueryfilter.TextMatch)]
                propFilterAllOf = propFilter.propfilter_test == "allof"
                
                # handle parameter filter elements
                if len(paramFilterElements) > 0:
                    if supportedParamter(propFilter.filter_name, paramFilterElements, propFilterAllOf ):
                        if len(textMatchElements) == 0:
                            return definedExpression(True, filterAllOf, propFilter.filter_name, constant, queryAttributes, allAttrStrings)
                    else:
                        if propFilterAllOf:
                            return (False, [], [])
                
                # handle text match elements
                propFilterNeedsAllRecords = propFilterAllOf
                propFilterAttributes = []
                propFilterExpressionList = []
                for textMatchElement in textMatchElements:
                    
                    textMatchNeedsAllRecords, textMatchExpressionAttributes, textMatchExpression = textMatchElementExpression(propFilterAllOf, textMatchElement)
                    if propFilterAllOf:
                        propFilterNeedsAllRecords &= textMatchNeedsAllRecords
                    else:
                        propFilterNeedsAllRecords |= textMatchNeedsAllRecords
                    propFilterAttributes += textMatchExpressionAttributes
                    propFilterExpressionList += textMatchExpression
    

                if (len(propFilterExpressionList) > 1) and (filterAllOf != propFilterAllOf):
                    propFilterExpressions = [dsquery.expression(dsquery.expression.AND if propFilterAllOf else dsquery.expression.OR , list(set(propFilterExpressionList)))] # remove duplicates
                else:
                    propFilterExpressions = list(set(propFilterExpressionList))
                
                return (propFilterNeedsAllRecords, propFilterAttributes, propFilterExpressions)
                #end propFilterExpression

            #print("propFilterListQuery: filterAllOf=%r, propFilters=%r" % (filterAllOf, propFilters,))
            """
            Create an expression for a list of prop-filter elements.
            
            @param filterAllOf: the C{True} if parent filter test is "allof"
            @param propFilters: the C{list} of L{ComponentFilter} elements.
            @return: (needsAllRecords, espressionAttributes, expression) tuple
            """
            needsAllRecords = filterAllOf
            attributes = []
            expressions = []
            for propFilter in propFilters:
                
                propNeedsAllRecords, propExpressionAttributes, propExpression = propFilterExpression(filterAllOf, propFilter)
                if filterAllOf:
                    needsAllRecords &= propNeedsAllRecords
                else:
                    needsAllRecords |= propNeedsAllRecords
                attributes += propExpressionAttributes
                expressions += propExpression

            if len(expressions) > 1:
                expr = dsquery.expression(dsquery.expression.AND if filterAllOf else dsquery.expression.OR , list(set(expressions))) # remove duplicates
            elif len(expressions):
                expr = expressions[0]
            else:
                expr = None
            
            return (needsAllRecords, list(set(attributes)), expr)
        
                
        #print("_getDSFilter")
        # Lets assume we have a valid filter from the outset
        
        # Top-level filter contains zero or more prop-filters
        if addressBookFilter:
            filterAllOf = addressBookFilter.filter_test == "allof"
            if len(addressBookFilter.children) > 0:
                return propFilterListQuery(filterAllOf, addressBookFilter.children)
            else:
                return (filterAllOf, [], [])
        else:
            return (False, [], [])    
    
                        
    #to do: use opendirectorybacker: _attributesForAddressBookQuery
    def _ldapAttributesForAddressBookQuery(self, addressBookQuery, ldapdsattrmap ):
                        
        propertyNames = [] 
        #print( "addressBookQuery.qname=%r" % addressBookQuery.qname)
        if addressBookQuery.qname() == ("DAV:", "prop"):
        
            for property in addressBookQuery.children:                
                #print("property = %r" % property )
                if isinstance(property, carddavxml.AddressData):
                    for addressProperty in property.children:
                        #print("addressProperty = %r" % addressProperty )
                        if isinstance(addressProperty, carddavxml.Property):
                            #print("Adding property %r", addressProperty.attributes["name"])
                            propertyNames.append(addressProperty.attributes["name"])
                        
                elif not self.fakeETag and property.qname() == ("DAV:", "getetag"):
                    # for a real etag == md5(vCard), we need all attributes
                    propertyNames = None
                    break;
                            
        
        if not len(propertyNames):
            #return self.returnedAttributes
            #return None
            result = ldapdsattrmap.keys()
            self.log_debug("_ldapAttributesForAddressBookQuery returning all props=%s" % result)
        
        else:
            #propertyNames.append("X-INTERNAL-MINIMUM-VCARD-PROPERTIES") # these properties are required to make a vCard
            queryAttributes = []
            for prop in propertyNames:
                searchAttr = ldapdsattrmap.get()
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
            searchmap = queryMap["searchmap"]
            ldapdsattrmap = queryMap["ldapdsattrmap"]

            allRecords, filterAttributes, dsFilter  = self._getLdapFilter( addressBookFilter, searchmap );
            #print("allRecords = %s, query = %s" % (allRecords, "None" if dsFilter is None else dsFilter.generate(),))
            self.log_debug("vCardRecordsForAddressBookQuery: queryType = \"%s\" LDAP allRecords = %s, filterAttributes = %s, query = %s" % (queryType, allRecords, filterAttributes, "None" if dsFilter is None else dsFilter.generate(),))
    
            
            if allRecords:
                dsFilter = None #  None expression == all Records
                
            clear = not allRecords and not dsFilter
            clear = False
                    
            if not clear:
                queryAttributes = self._ldapAttributesForAddressBookQuery( addressBookQuery, ldapdsattrmap )
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
                
                attrlist = attributes
                #filterstr = "(&(!(objectClass=organizationalUnit))(sn=Gaya))"
                #attrlist = ("cn", "sn", "objectClass", "mail", "telephoneNumber", "appleDSID")
                self.log_debug("LDAP query with base %s and filter %s and attributes %s sizelimit %s" % (ldap.dn.dn2str(base), filterstr, attrlist, maxRecords))
                
                ldapSearchResult = (yield self.timedSearch(ldap.dn.dn2str(base), ldap.SCOPE_SUBTREE, filterstr=filterstr, attrlist=attrlist, sizelimit=maxRecords))
    
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
                                dsAttributeNames = ldapdsattrmap.get(ldapAttributeName)
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
                                   
                        dsRecord = VCardRecord(self, dsRecordAttributes, self.defaultNodeName)
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

