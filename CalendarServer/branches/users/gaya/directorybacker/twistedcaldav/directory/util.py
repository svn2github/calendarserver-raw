# -*- test-case-name: twistedcaldav.directory.test.test_util -*-
##
# Copyright (c) 2006-2013 Apple Inc. All rights reserved.
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
Utilities.
"""

__all__ = [
    "normalizeUUID",
    "uuidFromName",
    "NotFoundResource",
]

from twext.enterprise.ienterprise import AlreadyFinishedError
from twext.python.log import Logger
from twext.web2 import responsecode
from twext.web2.auth.wrapper import UnauthorizedResponse
from twext.web2.dav.resource import DAVResource
from twext.web2.http import StatusResponse
from twisted.internet.defer import inlineCallbacks, returnValue
from txdav.xml import element as davxml
from uuid import UUID, uuid5

log = Logger()

def uuidFromName(namespace, name):
    """
    Generate a version 5 (SHA-1) UUID from a namespace UUID and a name.
    See http://www.ietf.org/rfc/rfc4122.txt, section 4.3.
    @param namespace: a UUID denoting the namespace of the generated UUID.
    @param name: a byte string to generate the UUID from.
    """
    # We don't want Unicode here; convert to UTF-8
    if type(name) is unicode:
        name = name.encode("utf-8")

    return normalizeUUID(str(uuid5(UUID(namespace), name)))


def normalizeUUID(value):
    """
    Convert strings which the uuid.UUID( ) method can parse into normalized
    (uppercase with hyphens) form.  Any value which is not parsed by UUID( )
    is returned as is.
    @param value: string value to normalize
    """
    try:
        return str(UUID(value)).upper()
    except (ValueError, TypeError):
        return value


TRANSACTION_KEY = '_newStoreTransaction'

def transactionFromRequest(request, newStore):
    """
    Return the associated transaction from the given HTTP request, creating a
    new one from the given data store if none has yet been associated.

    Also, if the request was not previously associated with a transaction, add
    a failsafe transaction-abort response filter to abort any transaction which
    has not been committed or aborted by the resource which responds to the
    request.

    @param request: The request to inspect.
    @type request: L{IRequest}

    @param newStore: The store to create a transaction from.
    @type newStore: L{IDataStore}

    @return: a transaction that should be used to read and write data
        associated with the request.
    @rtype: L{ITransaction} (and possibly L{ICalendarTransaction} and
        L{IAddressBookTransaction} as well.
    """
    transaction = getattr(request, TRANSACTION_KEY, None)
    if transaction is None:
        transaction = newStore.newTransaction(repr(request))
        def abortIfUncommitted(request, response):
            try:
                # TODO: missing 'yield' here.  For formal correctness as per
                # the interface, this should be allowed to be a Deferred.  (The
                # actual implementation still raises synchronously, so there's
                # no bug currently.)
                transaction.abort()
            except AlreadyFinishedError:
                pass
            return response
        abortIfUncommitted.handleErrors = True
        request.addResponseFilter(abortIfUncommitted)
        setattr(request, TRANSACTION_KEY, transaction)
    return transaction


def splitIntoBatches(data, size):
    """
    Return a generator of sets consisting of the contents of the data set
    split into parts no larger than size.
    """
    if not data:
        yield set([])
    data = list(data)
    while data:
        yield set(data[:size])
        del data[:size]


class NotFoundResource(DAVResource):
    """
    In order to prevent unauthenticated discovery of existing users via 401/404
    response codes, this resource can be returned from locateChild, and it will
    perform an authentication; if the user is unauthenticated, 404 responses are
    turned into 401s.
    """

    @inlineCallbacks
    def renderHTTP(self, request):

        try:
            authnUser, authzUser = yield self.authenticate(request)
        except Exception:
            authzUser = davxml.Principal(davxml.Unauthenticated())

        # Turn 404 into 401
        if authzUser == davxml.Principal(davxml.Unauthenticated()):
            response = (yield UnauthorizedResponse.makeResponse(
                request.credentialFactories,
                request.remoteAddr
            ))
            returnValue(response)
        else:
            response = StatusResponse(responsecode.NOT_FOUND, "Resource not found")
            returnValue(response)

