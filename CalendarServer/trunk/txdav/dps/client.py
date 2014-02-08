##
# Copyright (c) 2014 Apple Inc. All rights reserved.
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

import cPickle as pickle

from twext.python.log import Logger
from twext.who.directory import DirectoryRecord as BaseDirectoryRecord
from twext.who.directory import DirectoryService as BaseDirectoryService
from twext.who.idirectory import RecordType
import twext.who.idirectory
from twext.who.util import ConstantsContainer
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.protocol import ClientCreator
from twisted.protocols import amp
from txdav.dps.commands import (
    RecordWithShortNameCommand, RecordWithUIDCommand, RecordWithGUIDCommand,
    RecordsWithRecordTypeCommand, RecordsWithEmailAddressCommand,
    VerifyPlaintextPasswordCommand, VerifyHTTPDigestCommand
)
import txdav.who.idirectory
from zope.interface import implementer


log = Logger()


##
## Client implementation of Directory Proxy Service
##


@implementer(twext.who.idirectory.IDirectoryService)
class DirectoryService(BaseDirectoryService):
    """
    Client side of directory proxy
    """

    recordType = ConstantsContainer(
        (twext.who.idirectory.RecordType,
         txdav.who.idirectory.RecordType)
    )


    def _dictToRecord(self, serializedFields):
        """
        This to be replaced by something awesome
        """
        if not serializedFields:
            return None

        fields = {}
        for fieldName, value in serializedFields.iteritems():
            try:
                field = self.fieldName.lookupByName(fieldName)
            except ValueError:
                # unknown field
                pass
            else:
                fields[field] = value
        fields[self.fieldName.recordType] = self.recordType.user
        return DirectoryRecord(self, fields)


    def _processSingleRecord(self, result):
        serializedFields = pickle.loads(result['fields'])
        return self._dictToRecord(serializedFields)


    def _processMultipleRecords(self, result):
        serializedFieldsList = pickle.loads(result['fieldsList'])
        results = []
        for serializedFields in serializedFieldsList:
            record = self._dictToRecord(serializedFields)
            if record is not None:
                results.append(record)
        return results


    @inlineCallbacks
    def _getConnection(self):
        # TODO: make socket patch configurable
        # TODO: reconnect if needed

        # path = config.DirectoryProxy.SocketPath
        path = "data/Logs/state/directory-proxy.sock"
        if getattr(self, "_connection", None) is None:
            log.debug("Creating connection")
            connection = (yield ClientCreator(reactor, amp.AMP).connectUNIX(path))
            self._connection = connection
        else:
            log.debug("Already have connection")
        returnValue(self._connection)


    @inlineCallbacks
    def _call(self, command, postProcess, **kwds):
        """
        Execute a remote AMP command, first making the connection to the peer,
        then making the call, then running the results through the postProcess
        callback.  Any kwds are passed on to the AMP command.

        @param command: the AMP command to call
        @type command: L{twisted.protocols.amp.Command}

        @param postProcess: a callable which takes the AMP response dictionary
            and peforms any required massaging of the results, returning a
            L{Deferred} which fires with the post-processed results
        @type postProcess: callable
        """
        ampProto = (yield self._getConnection())
        results = (yield ampProto.callRemote(command, **kwds))
        returnValue(postProcess(results))


    def recordWithShortName(self, recordType, shortName):
        return self._call(
            RecordWithShortNameCommand,
            self._processSingleRecord,
            recordType=recordType.description.encode("utf-8"),
            shortName=shortName.encode("utf-8")
        )


    def recordWithUID(self, uid):
        return self._call(
            RecordWithUIDCommand,
            self._processSingleRecord,
            uid=uid.encode("utf-8")
        )


    def recordWithGUID(self, guid):
        return self._call(
            RecordWithGUIDCommand,
            self._processSingleRecord,
            guid=guid.encode("utf-8")
        )


    def recordsWithRecordType(self, recordType):
        return self._call(
            RecordsWithRecordTypeCommand,
            self._processMultipleRecords,
            recordType=recordType.description.encode("utf-8")
        )


    def recordsWithEmailAddress(self, emailAddress):
        return self._call(
            RecordsWithEmailAddressCommand,
            self._processMultipleRecords,
            emailAddress=emailAddress
        )



class DirectoryRecord(BaseDirectoryRecord):

    def verifyPlaintextPassword(self, password):
        return self.service._call(
            VerifyPlaintextPasswordCommand,
            lambda x: x['authenticated'],
            uid=self.uid.encode("utf-8"),
            password=password.encode("utf-8")
        )


    def verifyHTTPDigest(
        self, username, realm, uri, nonce, cnonce,
        algorithm, nc, qop, response, method,
    ):
        return self.service._call(
            VerifyHTTPDigestCommand,
            lambda x: x['authenticated'],
            uid=self.uid.encode("utf-8"),
            username=username.encode("utf-8"),
            realm=realm.encode("utf-8"),
            uri=uri.encode("utf-8"),
            nonce=nonce.encode("utf-8"),
            cnonce=cnonce.encode("utf-8"),
            algorithm=algorithm.encode("utf-8"),
            nc=nc.encode("utf-8"),
            qop=qop.encode("utf-8"),
            response=response.encode("utf-8"),
            method=method.encode("utf-8"),
        )



# Test client:


@inlineCallbacks
def makeEvenBetterRequest():
    ds = DirectoryService(None)
    record = (yield ds.recordWithShortName(RecordType.user, "sagen"))
    print("short name: {r}".format(r=record))
    if record:
        authenticated = (yield record.verifyPlaintextPassword("secret"))
        print("plain auth: {a}".format(a=authenticated))
    """
    record = (yield ds.recordWithUID("__dre__"))
    print("uid: {r}".format(r=record))
    if record:
        authenticated = (yield record.verifyPlaintextPassword("erd"))
        print("plain auth: {a}".format(a=authenticated))
    record = (yield ds.recordWithGUID("A3B1158F-0564-4F5B-81E4-A89EA5FF81B0"))
    print("guid: {r}".format(r=record))
    records = (yield ds.recordsWithRecordType(RecordType.user))
    print("recordType: {r}".format(r=records))
    records = (yield ds.recordsWithEmailAddress("cdaboo@bitbucket.calendarserver.org"))
    print("emailAddress: {r}".format(r=records))
    """


def succeeded(result):
    print("yay")
    reactor.stop()


def failed(failure):
    print("boo: {f}".format(f=failure))
    reactor.stop()


if __name__ == '__main__':
    d = makeEvenBetterRequest()
    d.addCallbacks(succeeded, failed)
    reactor.run()
