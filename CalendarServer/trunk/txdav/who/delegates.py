# -*- test-case-name: txdav.who.test.test_delegates -*-
##
# Copyright (c) 2013 Apple Inc. All rights reserved.
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
Delegate assignments
"""

from twisted.python.constants import Names, NamedConstant
from twisted.internet.defer import inlineCallbacks, returnValue, succeed

from twext.python.log import Logger
from twext.who.idirectory import (
    RecordType as BaseRecordType, FieldName, NotAllowedError
)
from twext.who.directory import (
    DirectoryService as BaseDirectoryService,
    DirectoryRecord as BaseDirectoryRecord
)
from twext.who.expression import MatchExpression, MatchType

log = Logger()



class RecordType(Names):
    """
    Constants for read-only delegates and read-write delegate groups
    """

    readDelegateGroup = NamedConstant()
    readDelegateGroup.description = u"read-delegate-group"

    writeDelegateGroup = NamedConstant()
    writeDelegateGroup.description = u"write-delegate-group"

    readDelegatorGroup = NamedConstant()
    readDelegatorGroup.description = u"read-delegator-group"

    writeDelegatorGroup = NamedConstant()
    writeDelegatorGroup.description = u"write-delegator-group"



class DirectoryRecord(BaseDirectoryRecord):

    @inlineCallbacks
    def members(self, expanded=False):
        """
        If this is a readDelegateGroup or writeDelegateGroup, the members
        will consist of the records who are delegates *of* this record.
        If this is a readDelegatorGroup or writeDelegatorGroup,
        the members will consist of the records who have delegated *to*
        this record.
        """
        parentUID, _ignore_proxyType = self.uid.split(u"#")

        txn = self.service._store.newTransaction(label="DirectoryRecord.members")

        if self.recordType in (
            RecordType.readDelegateGroup, RecordType.writeDelegateGroup
        ):  # Members are delegates of this record
            readWrite = (self.recordType is RecordType.writeDelegateGroup)
            delegateUIDs = (
                yield txn.delegates(parentUID, readWrite, expanded=expanded)
            )

        else:  # Members have delegated to this record
            readWrite = (self.recordType is RecordType.writeDelegatorGroup)
            delegateUIDs = (
                yield txn.delegators(parentUID, readWrite)
            )

        records = []
        for uid in delegateUIDs:
            if uid != parentUID:
                record = yield self.service._masterDirectory.recordWithUID(uid)
                if record is not None:
                    records.append(record)
        yield txn.commit()

        returnValue(records)


    @inlineCallbacks
    def setMembers(self, memberRecords):
        """
        Replace the members of this group with the new members.

        @param memberRecords: The new members of the group
        @type memberRecords: iterable of L{iDirectoryRecord}s
        """
        if self.recordType not in (
            RecordType.readDelegateGroup, RecordType.writeDelegateGroup
        ):
            raise NotAllowedError("Setting members not supported")

        parentUID, _ignore_proxyType = self.uid.split(u"#")
        readWrite = (self.recordType is RecordType.writeDelegateGroup)

        log.debug(
            "Setting delegate assignments for {u} ({rw}) to {m}",
            u=parentUID, rw=("write" if readWrite else "read"),
            m=[r.uid for r in memberRecords]
        )

        txn = self.service._store.newTransaction(label="DirectoryRecord.setMembers")

        yield txn.removeDelegates(parentUID, readWrite)
        yield txn.removeDelegateGroups(parentUID, readWrite)

        delegator = (
            yield self.service._masterDirectory.recordWithUID(parentUID)
        )

        for delegate in memberRecords:
            yield addDelegate(txn, delegator, delegate, readWrite)

        yield txn.commit()



def recordTypeToProxyType(recordType):
    return {
        RecordType.readDelegateGroup: "calendar-proxy-read",
        RecordType.writeDelegateGroup: "calendar-proxy-write",
        RecordType.readDelegatorGroup: "calendar-proxy-read-for",
        RecordType.writeDelegatorGroup: "calendar-proxy-write-for",
    }.get(recordType, None)



def proxyTypeToRecordType(proxyType):
    return {
        "calendar-proxy-read": RecordType.readDelegateGroup,
        "calendar-proxy-write": RecordType.writeDelegateGroup,
        "calendar-proxy-read-for": RecordType.readDelegatorGroup,
        "calendar-proxy-write-for": RecordType.writeDelegatorGroup,
    }.get(proxyType, None)



class DirectoryService(BaseDirectoryService):
    """
    Delegate directory service
    """

    recordType = RecordType


    def __init__(self, realmName, store):
        BaseDirectoryService.__init__(self, realmName)
        self._store = store
        self._masterDirectory = None


    def setMasterDirectory(self, masterDirectory):
        self._masterDirectory = masterDirectory


    def recordWithShortName(self, recordType, shortName):
        uid = shortName + "#" + recordTypeToProxyType(recordType)

        record = DirectoryRecord(self, {
            FieldName.uid: uid,
            FieldName.recordType: recordType,
            FieldName.shortNames: (uid,),
        })
        return succeed(record)


    def recordWithUID(self, uid):
        if "#" not in uid:  # Not a delegate group uid
            return succeed(None)
        uid, proxyType = uid.split("#")
        recordType = proxyTypeToRecordType(proxyType)
        if recordType is None:
            return succeed(None)
        return self.recordWithShortName(recordType, uid)


    @inlineCallbacks
    def recordsFromExpression(self, expression, records=None):
        """
        It's only ever appropriate to look up delegate group record by
        shortName or uid.  When wrapped by an aggregate directory, looking up
        by shortName will already go directly to recordWithShortName.  However
        when looking up by UID, it won't.  Inspect the expression to see if
        it's one we can handle.
        """
        if isinstance(expression, MatchExpression):
            if(
                (expression.fieldName is FieldName.uid) and
                (expression.matchType is MatchType.equals) and
                ("#" in expression.fieldValue)
            ):
                record = yield self.recordWithUID(expression.fieldValue)
                if record is not None:
                    returnValue((record,))

        returnValue(())



@inlineCallbacks
def addDelegate(txn, delegator, delegate, readWrite):
    """
    Adds "delegate" as a delegate of "delegator".  The type of access is
    specified by the "readWrite" parameter.

    @param delegator: the delegator's directory record
    @type delegator: L{IDirectoryRecord}
    @param delegate: the delegate's directory record
    @type delegate: L{IDirectoryRecord}
    @param readWrite: if True, read and write access is granted; read-only
        access otherwise
    """
    if delegate.recordType == BaseRecordType.group:
        # find the groupID
        groupID, _ignore_name, _ignore_membershipHash, _ignore_modified = yield txn.groupByUID(
            delegate.uid
        )
        yield txn.addDelegateGroup(delegator.uid, groupID, readWrite)
    else:
        yield txn.addDelegate(delegator.uid, delegate.uid, readWrite)



@inlineCallbacks
def removeDelegate(txn, delegator, delegate, readWrite):
    """
    Removes "delegate" as a delegate of "delegator".  The type of access is
    specified by the "readWrite" parameter.

    @param delegator: the delegator's directory record
    @type delegator: L{IDirectoryRecord}
    @param delegate: the delegate's directory record
    @type delegate: L{IDirectoryRecord}
    @param readWrite: if True, read and write access is revoked; read-only
        access otherwise
    """
    if delegate.recordType == BaseRecordType.group:
        # find the groupID
        groupID, _ignore_name, _ignore_membershipHash, _ignore_modified = yield txn.groupByUID(
            delegate.uid
        )
        yield txn.removeDelegateGroup(delegator.uid, groupID, readWrite)
    else:
        yield txn.removeDelegate(delegator.uid, delegate.uid, readWrite)



@inlineCallbacks
def delegatesOf(txn, delegator, readWrite, expanded=False):
    """
    Return the records of the delegates of "delegator".  The type of access
    is specified by the "readWrite" parameter.

    @param delegator: the delegator's directory record
    @type delegator: L{IDirectoryRecord}
    @param readWrite: if True, read and write access delegates are returned;
        read-only access otherwise
    @return: the set of directory records
    @rtype: a Deferred which fires a set of L{IDirectoryRecord}
    """
    records = []
    directory = delegator.service
    delegateUIDs = (
        yield txn.delegates(delegator.uid, readWrite, expanded=expanded)
    )
    for uid in delegateUIDs:
        if uid != delegator.uid:
            record = (yield directory.recordWithUID(uid))
            if record is not None:
                records.append(record)
    returnValue(records)



@inlineCallbacks
def delegatedTo(txn, delegate, readWrite):
    """
    Return the records of those who have delegated to "delegate".  The type of
    access is specified by the "readWrite" parameter.

    @param delegate: the delegate's directory record
    @type delegate: L{IDirectoryRecord}
    @param readWrite: if True, read and write access delegators are returned;
        read-only access otherwise
    @return: the set of directory records
    @rtype: a Deferred which fires a set of L{IDirectoryRecord}
    """
    records = []
    directory = delegate.service
    delegatorUIDs = (yield txn.delegators(delegate.uid, readWrite))
    for uid in delegatorUIDs:
        if uid != delegate.uid:
            record = (yield directory.recordWithUID(uid))
            if record is not None:
                records.append(record)
    returnValue(records)
