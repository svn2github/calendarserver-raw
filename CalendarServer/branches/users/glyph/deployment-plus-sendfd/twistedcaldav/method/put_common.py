##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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
PUT/COPY/MOVE common behavior.
"""

__all__ = ["storeCalendarObjectResource"]

from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.defer import deferredGenerator
from twisted.internet.defer import maybeDeferred
from twisted.internet.defer import waitForDeferred
from twisted.python import failure
from twisted.python.filepath import FilePath
from twisted.web2 import responsecode, http
from twisted.web2.dav import davxml
from twisted.web2.dav.element.base import dav_namespace
from twisted.web2.dav.element.base import PCDATAElement
from twisted.web2.dav.fileop import delete
from twisted.web2.dav.http import ErrorResponse
from twisted.web2.dav.resource import TwistedGETContentMD5
from twisted.web2.dav.stream import MD5StreamWrapper
from twisted.web2.dav.util import joinURL, parentForURL
from twisted.web2.http import HTTPError
from twisted.web2.http import StatusResponse
from twisted.web2.http_headers import generateContentType
from twisted.web2.iweb import IResponse
from twisted.web2.stream import MemoryStream

from twistedcaldav.config import config
from twistedcaldav.caldavxml import NoUIDConflict
from twistedcaldav.caldavxml import NumberOfRecurrencesWithinLimits
from twistedcaldav.caldavxml import caldav_namespace
from twistedcaldav.customxml import calendarserver_namespace 
from twistedcaldav.customxml import TwistedCalendarAccessProperty
from twistedcaldav.fileops import copyToWithXAttrs
from twistedcaldav.fileops import putWithXAttrs
from twistedcaldav.fileops import copyWithXAttrs
from twistedcaldav.ical import Component, Property
from twistedcaldav.index import ReservationError
from twistedcaldav.instance import TooManyInstancesError
from twistedcaldav.log import Logger

log = Logger()

@deferredGenerator
def storeCalendarObjectResource(
    request,
    sourcecal, destinationcal,
    source=None, source_uri=None, sourceparent=None,
    destination=None, destination_uri=None, destinationparent=None,
    calendardata=None,
    deletesource=False,
    isiTIP=False
):
    """
    Function that does common PUT/COPY/MOVE behaviour.
    
    @param request:           the L{twisted.web2.server.Request} for the current HTTP request.
    @param source:            the L{CalDAVFile} for the source resource to copy from, or None if source data
        is to be read from the request.
    @param source_uri:        the URI for the source resource.
    @param destination:       the L{CalDAVFile} for the destination resource to copy into.
    @param destination_uri:   the URI for the destination resource.
    @param calendardata:      the string data read directly from the request body if there is no source, None otherwise.
    @param sourcecal:         True if the source resource is in a calendar collection, False otherwise.
    @param destinationcal:    True if the destination resource is in a calendar collection, False otherwise
    @param sourceparent:      the L{CalDAVFile} for the source resource's parent collection, or None if source is None.
    @param destinationparent: the L{CalDAVFile} for the destination resource's parent collection.
    @param deletesource:      True if the source resource is to be deleted on successful completion, False otherwise.
    @param isiTIP:            True if relaxed calendar data validation is to be done, False otherwise.
    @return:                  a Deferred with a status response result.
    """
    
    try:
        assert destination is not None and destinationparent is not None and destination_uri is not None
        assert (source is None and sourceparent is None) or (source is not None and sourceparent is not None)
        assert (calendardata is None and source is not None) or (calendardata is not None and source is None)
        assert not deletesource or (deletesource and source is not None)
    except AssertionError:
        log.err("Invalid arguments to storeCalendarObjectResource():")
        log.err("request=%s\n" % (request,))
        log.err("sourcecal=%s\n" % (sourcecal,))
        log.err("destinationcal=%s\n" % (destinationcal,))
        log.err("source=%s\n" % (source,))
        log.err("source_uri=%s\n" % (source_uri,))
        log.err("sourceparent=%s\n" % (sourceparent,))
        log.err("destination=%s\n" % (destination,))
        log.err("destination_uri=%s\n" % (destination_uri,))
        log.err("destinationparent=%s\n" % (destinationparent,))
        log.err("calendardata=%s\n" % (calendardata,))
        log.err("deletesource=%s\n" % (deletesource,))
        log.err("isiTIP=%s\n" % (isiTIP,))
        raise

    class RollbackState(object):
        """
        This class encapsulates the state needed to rollback the entire PUT/COPY/MOVE
        transaction, leaving the server state the same as it was before the request was
        processed. The DoRollback method will actually execute the rollback operations.
        """
        
        def __init__(self):
            self.active = True
            self.source_copy = None
            self.destination_copy = None
            self.destination_created = False
            self.source_deleted = False
            self.source_index_deleted = False
            self.destination_index_deleted = False
        
        def Rollback(self):
            """
            Rollback the server state. Do not allow this to raise another exception. If
            rollback fails then we are going to be left in an awkward state that will need
            to be cleaned up eventually.
            """
            if self.active:
                self.active = False
                log.debug("Rollback: rollback")
                try:
                    if self.source_copy and self.source_deleted:
                        self.source_copy.moveTo(source.fp)
                        log.debug("Rollback: source restored %s to %s" % (self.source_copy.path, source.fp.path))
                        self.source_copy = None
                        self.source_deleted = False
                    if self.destination_copy:
                        destination.fp.remove()
                        log.debug("Rollback: destination restored %s to %s" % (self.destination_copy.path, destination.fp.path))
                        self.destination_copy.moveTo(destination.fp)
                        self.destination_copy = None
                    elif self.destination_created:
                        if destinationcal:
                            doRemoveDestinationIndex()
                            log.debug("Rollback: destination index removed %s" % (destination.fp.path,))
                            self.destination_index_deleted = False
                        destination.fp.remove()
                        log.debug("Rollback: destination removed %s" % (destination.fp.path,))
                        self.destination_created = False
                    if self.destination_index_deleted:
                        # Must read in calendar for destination being re-indexed
                        doDestinationIndex(destination.iCalendar())
                        self.destination_index_deleted = False
                        log.debug("Rollback: destination re-indexed %s" % (destination.fp.path,))
                    if self.source_index_deleted:
                        doSourceIndexRecover()
                        self.destination_index_deleted = False
                        log.debug("Rollback: source re-indexed %s" % (source.fp.path,))
                except:
                    log.err("Rollback: exception caught and not handled: %s" % failure.Failure())

        def Commit(self):
            """
            Commit the resource changes by wiping the rollback state.
            """
            if self.active:
                log.debug("Rollback: commit")
                self.active = False
                if self.source_copy:
                    self.source_copy.remove()
                    log.debug("Rollback: removed source backup %s" % (self.source_copy.path,))
                    self.source_copy = None
                if self.destination_copy:
                    self.destination_copy.remove()
                    log.debug("Rollback: removed destination backup %s" % (self.destination_copy.path,))
                    self.destination_copy = None
                self.destination_created = False
                self.source_deleted = False
                self.source_index_deleted = False
                self.destination_index_deleted = False
    
    rollback = RollbackState()

    def validResourceName():
        """
        Make sure that the resource name for the new resource is valid.
        """
        result = True
        message = ""
        filename = destination.fp.basename()
        if filename.startswith("."):
            result = False
            message = "File name %s not allowed in calendar collection" % (filename,)

        return result, message
        
    def validContentType():
        """
        Make sure that the content-type of the source resource is text/calendar.
        This test is only needed when the source is not in a calendar collection.
        """
        result = True
        message = ""
        content_type = source.contentType()
        if not ((content_type.mediaType == "text") and (content_type.mediaSubtype == "calendar")):
            result = False
            message = "MIME type %s not allowed in calendar collection" % (content_type,)

        return result, message
        
    def validContentLength():
        """
        Make sure that the length of the source data is within bounds.
        """
        result = True
        message = ""
        if config.MaximumAttachmentSize:
            calsize = source.contentLength()
            if calsize is not None and calsize > config.MaximumAttachmentSize:
                result = False
                message = "File size %d bytes is larger than allowed limit %d bytes" % (calsize, config.MaximumAttachmentSize)

        return result, message
        
    def validCalendarDataCheck():
        """
        Check that the calendar data is valid iCalendar.
        @return:         tuple: (True/False if the calendra data is valid,
                                 log message string).
        """
        result = True
        message = ""
        if calendar is None:
            result = False
            message = "Empty resource not allowed in calendar collection"
        else:
            try:
                calendar.validCalendarForCalDAV()
            except ValueError, e:
                result = False
                message = "Invalid calendar data: %s" % (e,)
        
        return result, message
    
    def validCalDAVDataCheck():
        """
        Check that the calendar data is valid as a CalDAV calendar object resource.
        @return:         tuple: (True/False if the calendar data is valid,
                                 log message string).
        """
        result = True
        message = ""
        try:
            if isiTIP:
                calendar.validateComponentsForCalDAV(True)
            else:
                calendar.validateForCalDAV()
        except ValueError, e:
            result = False
            message = "Calendar data does not conform to CalDAV requirements: %s" % (e,)
        
        return result, message
    
    def validSizeCheck():
        """
        Make sure that the size of the data is within bounds.
        """
        result = True
        message = ""
        if config.MaximumAttachmentSize:
            calsize = len(calendardata)
            if calsize > config.MaximumAttachmentSize:
                result = False
                message = "Data size %d bytes is larger than allowed limit %d bytes" % (calsize, config.MaximumAttachmentSize)

        return result, message

    @deferredGenerator
    def validAccess():
        """
        Make sure that the X-CALENDARSERVER-ACCESS property is properly dealt with.
        """
        
        if calendar.hasProperty(Component.ACCESS_PROPERTY):
            
            # Must be a value we know about
            access = calendar.accessLevel(default=None)
            if access is None:
                raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (calendarserver_namespace, "valid-access-restriction")))
                
            # Only DAV:owner is able to set the property to other than PUBLIC
            d = waitForDeferred(destinationparent.owner(request))
            yield d
            parent_owner = d.getResult()
            
            authz = destinationparent.currentPrincipal(request)
            if davxml.Principal(parent_owner) != authz and access != Component.ACCESS_PUBLIC:
                raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (calendarserver_namespace, "valid-access-restriction-change")))

            yield access, calendardata
        else:
            # Check whether an access property was present before and write that into the calendar data
            newcalendardata = calendardata
            if not source and destination.exists() and destination.hasDeadProperty(TwistedCalendarAccessProperty):
                old_access = str(destination.readDeadProperty(TwistedCalendarAccessProperty))
                calendar.addProperty(Property(name=Component.ACCESS_PROPERTY, value=old_access))
                newcalendardata = str(calendar)

            yield None, newcalendardata

    def noUIDConflict(uid):
        """
        Check that the UID of the new calendar object conforms to the requirements of
        CalDAV, i.e. it must be unique in the collection and we must not overwrite a
        different UID.
        @param uid: the UID for the resource being stored.
        @return: tuple: (True/False if the uid is valid, log message string,
            name of conflicted resource).
        """

        result = True
        message = ""
        rname = ""

        # Adjust for a move into same calendar collection
        oldname = None
        if sourceparent and (sourceparent.fp.path == destinationparent.fp.path) and deletesource:
            oldname = source.fp.basename()

        # UID must be unqiue
        index = destinationparent.index()
        if not index.isAllowedUID(uid, oldname, destination.fp.basename()):
            rname = index.resourceNameForUID(uid)
            # This can happen if two simulataneous PUTs occur with the same UID.
            # i.e. one PUT has reserved the UID but has not yet written the resource,
            # the other PUT tries to reserve and fails but no index entry exists yet.
            if rname is None:
                rname = "<<Unknown Resource>>"
            
            result = False
            message = "Calendar resource %s already exists with same UID %s" % (rname, uid)
        else:
            # Cannot overwrite a resource with different UID
            if destination.fp.exists():
                olduid = index.resourceUIDForName(destination.fp.basename())
                if olduid != uid:
                    rname = destination.fp.basename()
                    result = False
                    message = "Cannot overwrite calendar resource %s with different UID %s" % (rname, olduid)
        
        return result, message, rname

    try:
        """
        Handle validation operations here.
        """
        reserved = False
        access = None
        if destinationcal:
            # Valid resource name check
            result, message = validResourceName()
            if not result:
                log.err(message)
                raise HTTPError(StatusResponse(responsecode.FORBIDDEN, "Resource name not allowed"))

            # Valid data sizes - do before parsing the data
            if source is not None:
                # Valid content length check on the source resource
                result, message = validContentLength()
                if not result:
                    log.err(message)
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "max-resource-size")))
            else:
                # Valid calendar data size check
                result, message = validSizeCheck()
                if not result:
                    log.err(message)
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "max-resource-size")))

            if not sourcecal:
                # Valid content type check on the source resource if its not in a calendar collection
                if source is not None:
                    result, message = validContentType()
                    if not result:
                        log.err(message)
                        raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "supported-calendar-data")))
                
                    # At this point we need the calendar data to do more tests
                    calendar = source.iCalendar()
                else:
                    try:
                        calendar = Component.fromString(calendardata)
                    except ValueError, e:
                        log.err(str(e))
                        raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-calendar-data")))
                        
                # Valid calendar data check
                result, message = validCalendarDataCheck()
                if not result:
                    log.err(message)
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-calendar-data")))
                    
                # Valid calendar data for CalDAV check
                result, message = validCalDAVDataCheck()
                if not result:
                    log.err(message)
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-calendar-object-resource")))

                # Must have a valid UID at this point
                uid = calendar.resourceUID()
            else:
                # Get uid from original resource
                source_index = sourceparent.index()
                uid = source_index.resourceUIDForName(source.fp.basename())
                if uid is None:
                    log.err("Source calendar does not have a UID: %s" % source.fp.basename())
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-calendar-object-resource")))

                # FIXME: We need this here because we have to re-index the destination. Ideally it
                # would be better to copy the index entries from the source and add to the destination.
                calendar = source.iCalendar()

            # Check access
            if destinationcal and config.EnablePrivateEvents:
                d = waitForDeferred(validAccess())
                yield d
                access, calendardata = d.getResult()

            # Reserve UID
            destination_index = destinationparent.index()
            
            # Lets use a deferred for this and loop a few times if we cannot reserve so that we give
            # time to whoever has the reservation to finish and release it.
            failure_count = 0
            while(failure_count < 10):
                try:
                    d = waitForDeferred(destination_index.reserveUID(uid))
                    yield d
                    d.getResult()
                    reserved = True
                    break
                except ReservationError:
                    reserved = False
                failure_count += 1
                
                d = Deferred()
                def _timedDeferred():
                    d.callback(True)
                reactor.callLater(0.5, _timedDeferred)
                pause = waitForDeferred(d)
                yield pause
                pause.getResult()
            
            if destination_uri and not reserved:
                raise HTTPError(StatusResponse(responsecode.CONFLICT, "Resource: %s currently in use." % (destination_uri,)))

            # Re-evaluate the HTTP pre-conditions (If-Match etc) after acquiring the UID lock. This avoids
            # a race condition where two simultaneous PUTs with If-Match can conflict (i.e. second PUT does
            # not fail even though underlying resource was changed by first PUT). We also need to reload the
            # property cache before we do this as the etag property may have changed.
            if destination.exists():
                destination.deadProperties().reloadCache()
            destination.checkPreconditions(request)

            # uid conflict check - note we do this after reserving the UID to avoid a race condition where two requests
            # try to write the same calendar data to two different resource URIs.
            if not isiTIP:
                result, message, rname = noUIDConflict(uid)
                if not result:
                    log.err(message)
                    raise HTTPError(ErrorResponse(responsecode.FORBIDDEN,
                        NoUIDConflict(davxml.HRef.fromString(joinURL(parentForURL(destination_uri), rname.encode("utf-8"))))
                    ))
            
        """
        Handle rollback setup here.
        """

        # Do quota checks on destination and source before we start messing with adding other files
        if request is None:
            destquota = None
        else:
            destquota = waitForDeferred(destination.quota(request))
            yield destquota
            destquota = destquota.getResult()
            if destquota is not None and destination.exists():
                old_dest_size = waitForDeferred(destination.quotaSize(request))
                yield old_dest_size
                old_dest_size = old_dest_size.getResult()
            else:
                old_dest_size = 0
            
        if request is None:
            sourcequota = None
        elif source is not None:
            sourcequota = waitForDeferred(source.quota(request))
            yield sourcequota
            sourcequota = sourcequota.getResult()
            if sourcequota is not None and source.exists():
                old_source_size = waitForDeferred(source.quotaSize(request))
                yield old_source_size
                old_source_size = old_source_size.getResult()
            else:
                old_source_size = 0
        else:
            sourcequota = None
            old_source_size = 0

        # We may need to restore the original resource data if the PUT/COPY/MOVE fails,
        # so rename the original file in case we need to rollback.
        overwrite = destination.exists()
        if overwrite:
            rollback.destination_copy = FilePath(destination.fp.path)
            rollback.destination_copy.path += ".rollback"
            copyToWithXAttrs(destination.fp, rollback.destination_copy)
            log.debug("Rollback: backing up destination %s to %s" % (destination.fp.path, rollback.destination_copy.path))
        else:
            rollback.destination_created = True
            log.debug("Rollback: will create new destination %s" % (destination.fp.path,))

        if deletesource:
            rollback.source_copy = FilePath(source.fp.path)
            rollback.source_copy.path += ".rollback"
            copyToWithXAttrs(source.fp, rollback.source_copy)
            log.debug("Rollback: backing up source %s to %s" % (source.fp.path, rollback.source_copy.path))
    
        """
        Handle actual store operations here.
        
        The order in which this is done is import:
            
            1. Do store operation for new data
            2. Delete source and source index if needed
            3. Do new indexing if needed
            
        Note that we need to remove the source index BEFORE doing the destination index to cover the
        case of a resource being 'renamed', i.e. moved within the same collection. Since the index UID
        column must be unique in SQL, we cannot add the new index before remove the old one.
        """

        # Do put or copy based on whether source exists
        if source is not None:
            response = maybeDeferred(copyWithXAttrs, source.fp, destination.fp, destination_uri)
        else:
            md5 = MD5StreamWrapper(MemoryStream(calendardata))
            response = maybeDeferred(putWithXAttrs, md5, destination.fp)
        response = waitForDeferred(response)
        yield response
        response = response.getResult()

        # Update the MD5 value on the resource
        if source is not None:
            # Copy MD5 value from source to destination
            if source.hasDeadProperty(TwistedGETContentMD5):
                md5 = source.readDeadProperty(TwistedGETContentMD5)
                destination.writeDeadProperty(md5)
        else:
            # Finish MD5 calc and write dead property
            md5.close()
            md5 = md5.getMD5()
            destination.writeDeadProperty(TwistedGETContentMD5.fromString(md5))

        # Update calendar-access property value on the resource
        if access:
            destination.writeDeadProperty(TwistedCalendarAccessProperty(access))
            
        # Do not remove the property if access was not specified and we are storing in a calendar.
        # This ensure that clients that do not preserve the iCalendar property do not cause access
        # restrictions to be lost.
        elif not destinationcal:
            destination.removeDeadProperty(TwistedCalendarAccessProperty)                

        response = IResponse(response)

        def doDestinationIndex(caltoindex):
            """
            Do destination resource indexing, replacing any index previous stored.
            
            @return: None if successful, ErrorResponse on failure
            """
            
            # Delete index for original item
            if overwrite:
                doRemoveDestinationIndex()
            
            # Add or update the index for this resource.
            try:
                destination_index.addResource(destination.fp.basename(), caltoindex)
                log.debug("Destination indexed %s" % (destination.fp.path,))
            except TooManyInstancesError, ex:
                log.err("Cannot index calendar resource as there are too many recurrence instances %s" % destination)
                raise HTTPError(ErrorResponse(
                    responsecode.FORBIDDEN,
                    NumberOfRecurrencesWithinLimits(PCDATAElement(str(ex.max_allowed)))
                ))
            except (ValueError, TypeError), ex:
                log.err("Cannot index calendar resource: %s" % (ex,))
                raise HTTPError(ErrorResponse(responsecode.FORBIDDEN, (caldav_namespace, "valid-calendar-data")))

            content_type = request.headers.getHeader("content-type")
            if content_type is not None:
                destination.writeDeadProperty(davxml.GETContentType.fromString(generateContentType(content_type)))
            else:
                destination.writeDeadProperty(davxml.GETContentType.fromString("text/calendar"))
            return None

        def doRemoveDestinationIndex():
            """
            Remove any existing destination index.
            """
            
            # Delete index for original item
            if destinationcal:
                destination_index.deleteResource(destination.fp.basename())
                rollback.destination_index_deleted = True
                log.debug("Destination index removed %s" % (destination.fp.path,))

        def doSourceDelete():
            # Delete index for original item
            if sourcecal:
                source_index.deleteResource(source.fp.basename())
                rollback.source_index_deleted = True
                log.debug("Source index removed %s" % (source.fp.path,))

            # Delete the source resource
            delete(source_uri, source.fp, "0")
            rollback.source_deleted = True
            log.debug("Source removed %s" % (source.fp.path,))

        def doSourceIndexRecover():
            """
            Do source resource indexing. This only gets called when restoring
            the source after its index has been deleted.
            
            @return: None if successful, ErrorResponse on failure
            """
            
            # Add or update the index for this resource.
            try:
                source_index.addResource(source.fp.basename(), calendar)
            except TooManyInstancesError, ex:
                raise HTTPError(ErrorResponse(
                    responsecode.FORBIDDEN,
                    NumberOfRecurrencesWithinLimits(PCDATAElement(str(ex.max_allowed)))
                ))

            return None

        if deletesource:
            doSourceDelete()
            # Update quota
            if sourcequota is not None:
                delete_size = 0 - old_source_size
                d = waitForDeferred(source.quotaSizeAdjust(request, delete_size))
                yield d
                d.getResult()

            if sourcecal:
                # Change CTag on the parent calendar collection
                d = waitForDeferred(sourceparent.updateCTag())
                yield d
                d.getResult()

        if destinationcal:
            result = doDestinationIndex(calendar)
            if result is not None:
                rollback.Rollback()
                yield result
                return

        # Do quota check on destination
        if destquota is not None:
            # Get size of new/old resources
            new_dest_size = waitForDeferred(destination.quotaSize(request))
            yield new_dest_size
            new_dest_size = new_dest_size.getResult()
            diff_size = new_dest_size - old_dest_size
            if diff_size >= destquota[0]:
                log.err("Over quota: available %d, need %d" % (destquota[0], diff_size))
                raise HTTPError(ErrorResponse(responsecode.INSUFFICIENT_STORAGE_SPACE, (dav_namespace, "quota-not-exceeded")))
            d = waitForDeferred(destination.quotaSizeAdjust(request, diff_size))
            yield d
            d.getResult()


        if destinationcal:
            # Change CTag on the parent calendar collection
            d = waitForDeferred(destinationparent.updateCTag())
            yield d
            d.getResult()

        # Can now commit changes and forget the rollback details
        rollback.Commit()

        if reserved:
            d = waitForDeferred(destination_index.unreserveUID(uid))
            yield d
            d.getResult()
            reserved = False

        yield response
        return

    except Exception, err:
        if reserved:
            d = waitForDeferred(destination_index.unreserveUID(uid))
            yield d
            d.getResult()
            reserved = False

        # Roll back changes to original server state. Note this may do nothing
        # if the rollback has already ocurred or changes already committed.
        rollback.Rollback()
        raise err
