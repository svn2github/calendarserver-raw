##
# Copyright (c) 2006-2009 Apple Inc. All rights reserved.
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
CardDAV addressbook-query report
"""

__all__ = ["report_urn_ietf_params_xml_ns_carddav_addressbook_query"]

import urllib

from twisted.internet.defer import succeed, inlineCallbacks, returnValue, maybeDeferred

from twext.python.log import Logger
from twext.web2 import responsecode
from twext.web2.dav import davxml
from twext.web2.dav.http import ErrorResponse, MultiStatusResponse
from twext.web2.dav.method.report import NumberOfMatchesWithinLimits
from twext.web2.dav.util import joinURL
from twext.web2.http import HTTPError, StatusResponse

from twistedcaldav import carddavxml
from twistedcaldav.config import config
from twistedcaldav.carddavxml import carddav_namespace, NResults
from twistedcaldav.method import report_common

log = Logger()

@inlineCallbacks
def report_urn_ietf_params_xml_ns_carddav_addressbook_query(self, request, addressbook_query):
    """
    Generate an addressbook-query REPORT.
    (CardDAV, section 8.6)
    """
    # Verify root element
    if addressbook_query.qname() != (carddav_namespace, "addressbook-query"):
        raise ValueError("{CardDAV:}addressbook-query expected as root element, not %s." % (addressbook_query.sname(),))

    if not self.isCollection():
        parent = (yield self.locateParent(request, request.uri))
        if not parent.isAddressBookCollection():
            log.err("addressbook-query report is not allowed on a resource outside of an address book collection %s" % (self,))
            raise HTTPError(StatusResponse(responsecode.FORBIDDEN, "Must be address book collection or address book resource"))

    responses = []

    filter = addressbook_query.filter
    query  = addressbook_query.query
    limit = addressbook_query.limit

    assert query is not None
    
    if query.qname() == ("DAV:", "allprop"):
        propertiesForResource = report_common.allPropertiesForResource
        generate_address_data = False

    elif query.qname() == ("DAV:", "propname"):
        propertiesForResource = report_common.propertyNamesForResource
        generate_address_data = False

    elif query.qname() == ("DAV:", "prop"):
        propertiesForResource = report_common.propertyListForResource
       
        # Verify that any address-data element matches what we can handle
        result, message, generate_address_data = report_common.validPropertyListAddressDataTypeVersion(query)
        if not result:
            log.err(message)
            raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (carddav_namespace, "supported-address-data")))
        
    else:
        raise AssertionError("We shouldn't be here")

    # Verify that the filter element is valid
    if (filter is None) or not filter.valid():
        log.err("Invalid filter element: %r" % (filter,))
        raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (carddav_namespace, "valid-filter")))

    matchcount = [0,]
    max_number_of_results = [config.MaxAddressBookQueryResults,]
    limited = [False,]
    
    if limit:
        clientLimit = int(str(limit.childOfType(NResults)))
        if clientLimit < max_number_of_results[0]:
            max_number_of_results[0] = clientLimit

    @inlineCallbacks
    def doQuery(addrresource, uri):
        """
        Run a query on the specified address book collection
        accumulating the query responses.
        @param addrresource: the L{CalDAVFile} for an address book collection.
        @param uri: the uri for the address book collecton resource.
        """
        
        def checkMaxResults():
            matchcount[0] += 1
            if matchcount[0] > max_number_of_results[0]:
                raise NumberOfMatchesWithinLimits(max_number_of_results[0])
           
        
        def queryAddressBookObjectResource(resource, uri, name, vcard, query_ok = False):
            """
            Run a query on the specified vcard.
            @param resource: the L{CalDAVFile} for the vcard.
            @param uri: the uri of the resource.
            @param name: the name of the resource.
            @param vcard: the L{Component} vcard read from the resource.
            """
            
            if query_ok or filter.match(vcard):
                # Check size of results is within limit
                checkMaxResults()

                if name:
                    href = davxml.HRef.fromString(joinURL(uri, name))
                else:
                    href = davxml.HRef.fromString(uri)
            
                return report_common.responseForHrefAB(request, responses, href, resource, propertiesForResource, query, vcard=vcard)
            else:
                return succeed(None)
            
                                
            
        @inlineCallbacks
        def queryDirectoryBackedAddressBook(directoryBackedAddressBook, addressBookFilter):
            """
            """
            records, limited[0] = (yield directoryBackedAddressBook.directory.vCardRecordsForAddressBookQuery( addressBookFilter, query, max_number_of_results[0] ))
            for vCardRecord in records:
                
                # match against original filter
                if filter.match(vCardRecord.vCard()):
 
                    # Check size of results is within limit
                    checkMaxResults()
                   
                    yield report_common.responseForHrefAB(request, responses, vCardRecord.hRef(), vCardRecord, propertiesForResource, query, vcard=vCardRecord.vCard())
 
 
            
        directoryAddressBookLock = None
        try:                

            if addrresource.isDirectoryBackedAddressBookCollection() and addrresource.directory.cacheQuery:
                
                directory = addrresource.directory
                if directory.liveQuery:
                    # if liveQuery and cacheQuery, get vCards into the directory address book on disk
                    directoryAddressBookLock, limited[0] = (yield  directory.cacheVCardsForAddressBookQuery( filter, query, max_number_of_results[0] ) )
 
                elif directory.maxDSQueryRecords and directory.maxDSQueryRecords < max_number_of_results[0]:
                    max_number_of_results[0] = directory.maxDSQueryRecords
                   
                
            elif not addrresource.isAddressBookCollection():
 
                #do UID lookup on last part of uri
                resource_name = urllib.unquote(uri[uri.rfind("/") + 1:])
                if resource_name.endswith(".vcf") and len(resource_name) > 4:
    
                    # see if parent is directory backed address book
                    parent = (yield  addrresource.locateParent( request, uri ) )
    
                    if parent.isDirectoryBackedAddressBookCollection() and parent.directory.cacheQuery:
                        
                        directory = parent.directory
                        if directory.liveQuery:
                            vCardFilter = carddavxml.Filter( *[carddavxml.PropertyFilter(
                                                        carddavxml.TextMatch.fromString(resource_name[:-4]), 
                                                        name="UID", # attributes
                                                        ),] )
                            
                            directoryAddressBookLock, limited[0] = (yield  directory.cacheVCardsForAddressBookQuery( vCardFilter, query, max_number_of_results[0] ) )

                        elif directory.maxDSQueryRecords and directory.maxDSQueryRecords < max_number_of_results[0]:
                            max_number_of_results[0] = directory.maxDSQueryRecords
   
    
        
            # Check whether supplied resource is an address book or an address book object resource
            if addrresource.isAddressBookCollection():
    
                if addrresource.isDirectoryBackedAddressBookCollection() and addrresource.directory.liveQuery and not addrresource.directory.cacheQuery:
                    yield  maybeDeferred( queryDirectoryBackedAddressBook, addrresource, filter )
                
                else:

                    # Do some optimisation of access control calculation by determining any inherited ACLs outside of
                    # the child resource loop and supply those to the checkPrivileges on each child.
                    filteredaces = (yield addrresource.inheritedACEsforChildren(request))
                
                    # Check for disabled access
                    if filteredaces is not None:
                        # See whether the filter is valid for an index only query
                        index_query_ok = addrresource.index().searchValid(filter)
                    
                        # Get list of children that match the search and have read access
                        names = [name for name, ignore_uid in addrresource.index().search(filter)] #@UnusedVariable
                        if not names:
                            return
                          
                        # Now determine which valid resources are readable and which are not
                        ok_resources = []
                        yield addrresource.findChildrenFaster(
                            "1",
                            request,
                            lambda x, y: ok_resources.append((x, y)),
                            None,
                            names,
                            (davxml.Read(),),
                            inherited_aces=filteredaces
                        )
                        
                        for child, child_uri in ok_resources:
                            child_uri_name = child_uri[child_uri.rfind("/") + 1:]
                            child_path_name = urllib.unquote(child_uri_name)
                            
                            if generate_address_data or not index_query_ok:
                                vcard = addrresource.vCard(child_path_name)
                                assert vcard is not None, "vCard %s is missing from address book collection %r" % (child_uri_name, self)
                            else:
                                vcard = None
                            
                            yield queryAddressBookObjectResource(child, uri, child_uri_name, vcard, query_ok = index_query_ok)
                        
            else:
                
                handled = False;
                resource_name = urllib.unquote(uri[uri.rfind("/") + 1:])
                if resource_name.endswith(".vcf") and len(resource_name) > 4:
                    
                    # see if parent is directory backed address book
                    parent = (yield  addrresource.locateParent( request, uri ) )
    
                    if parent.isDirectoryBackedAddressBookCollection() and parent.directory.liveQuery and not parent.directory.cacheQuery:
 
                        vCardFilter = carddavxml.Filter( *[carddavxml.PropertyFilter(
                                                    carddavxml.TextMatch.fromString(resource_name[:-4]), 
                                                    name="UID", # attributes
                                                    ),] )
                        
                        yield  maybeDeferred( queryDirectoryBackedAddressBook, parent, vCardFilter )
                        handled = True

                if not handled:
                    vcard = addrresource.vCard()
                    yield queryAddressBookObjectResource(addrresource, uri, None, vcard)
        
            if limited[0]:
                raise NumberOfMatchesWithinLimits(matchcount[0])

        finally:
            if directoryAddressBookLock:
                yield directoryAddressBookLock.release()
                
    
    # Run report taking depth into account
    try:
        depth = request.headers.getHeader("depth", "0")
        yield report_common.applyToAddressBookCollections(self, request, request.uri, depth, doQuery, (davxml.Read(),))
    except NumberOfMatchesWithinLimits, e:
        self.log_info("Too many matching components in addressbook-query report. Limited to %d items" % e.maxLimit())
        #raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, davxml.NumberOfMatchesWithinLimits()))
        responses.append(davxml.StatusResponse(
                        davxml.HRef.fromString(request.uri),
                        davxml.Status.fromResponseCode(responsecode.INSUFFICIENT_STORAGE_SPACE),
                        davxml.Error(davxml.NumberOfMatchesWithinLimits()),
                        #davxml.ResponseDescription("Results limited by %s at %d" % resultsWereLimited),
                        davxml.ResponseDescription("Results limited to %d items" % e.maxLimit()),
                    ))
    
    if not hasattr(request, "extendedLogItems"):
        request.extendedLogItems = {}
    request.extendedLogItems["responses"] = len(responses)

    returnValue(MultiStatusResponse(responses))
