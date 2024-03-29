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
OpenDirectory.framework access via PyObjC
"""

import odframework
import objc
import dsattributes
import base64
from twext.python.log import Logger

log = Logger()

# Single-value attributes (must be converted from lists):
SINGLE_VALUE_ATTRIBUTES = [
    dsattributes.kDS1AttrBirthday,
    dsattributes.kDS1AttrComment,
    dsattributes.kDS1AttrCreationTimestamp,
    dsattributes.kDS1AttrDistinguishedName,
    dsattributes.kDS1AttrFirstName,
    dsattributes.kDS1AttrGeneratedUID,
    dsattributes.kDS1AttrLastName,
    dsattributes.kDS1AttrMiddleName,
    dsattributes.kDS1AttrModificationTimestamp,
    dsattributes.kDS1AttrNote,
    dsattributes.kDS1AttrSearchPath,
    dsattributes.kDS1AttrUserCertificate,
    dsattributes.kDS1AttrUserPKCS12Data,
    dsattributes.kDS1AttrUserSMIMECertificate,
    dsattributes.kDS1AttrWeblogURI,
]

MATCHANY = 1
DIGEST_MD5 = "dsAuthMethodStandard:dsAuthNodeDIGEST-MD5"

class Directory(object):
    """ Encapsulates OpenDirectory session and node """

    def __init__(self, session, node, nodeName):
        self.session = session
        self.node = node
        self.nodeName = nodeName

    def __str__(self):
        return "OpenDirectory node: %s" % (self.nodeName)



def adjustMatchType(matchType, caseInsensitive):
    """ Return the case-insensitive equivalent matchType """
    return (matchType | 0x100) if caseInsensitive else matchType

    # return caseInsensitiveEquivalents[matchType] if caseInsensitive else matchType


def recordToResult(record, encodings):
    """
    Takes an ODRecord and turns it into a (recordName, attributesDictionary)
    tuple.  Unicode values are converted to utf-8 encoded strings. (Not sure
    what to do with non-unicode values)

    encodings is a attribute-name-to-encoding mapping, useful for specifying
    how to decode values.
    """
    details, error = record.recordDetailsForAttributes_error_(None, None)
    if error:
        log.error(error)
        raise ODError(error)
    result = {}
    for key, value in details.iteritems():
        encoding = encodings.get(key, None)
        if key in SINGLE_VALUE_ATTRIBUTES:
            if encoding:
                if encoding == "base64":
                    result[key] = base64.b64encode(value.bytes().tobytes())
            else:
                if len(value) == 0:
                    result[key] = None
                else:
                    if isinstance(value[0], objc.pyobjc_unicode):
                        result[key] = unicode(value[0]).encode("utf-8") # convert from pyobjc
        else:
            if encoding:
                if encoding == "base64":
                    result[key] = [base64.b64encode(v.bytes().tobytes()) for v in value]
            else:
                result[key] = [unicode(v).encode("utf-8") for v in value if isinstance(v, objc.pyobjc_unicode)]

    return (details.get(dsattributes.kDSNAttrRecordName, [None])[0], result)


def attributeNamesFromList(attributes):
    """
    The attributes list can contain string names or tuples of the form (name,
    encoding).  Return just the names.
    """

    if attributes is None:
        attributes = []

    names = []
    encodings = {}
    for attribute in attributes:
        if isinstance(attribute, tuple):
            names.append(attribute[0])
            encodings[attribute[0]] = attribute[1]
        else:
            names.append(attribute)
    return names, encodings


def odInit(nodeName):
    """
    Create an Open Directory object to operate on the specified directory service node name.

    @param nodeName: C{str} containing the node name.
    @return: C{object} an object to be passed to all subsequent functions on success,
        C{None} on failure.
    """
    session = odframework.ODSession.defaultSession()
    node, error = odframework.ODNode.nodeWithSession_name_error_(session,
        nodeName, None)
    if error:
        log.error(error)
        raise ODError(error)

    return Directory(session, node, nodeName)


def getNodeAttributes(directory, nodeName, attributes):
    """
    Return key attributes for the specified directory node. The attributes
    can be a C{str} for the attribute name, or a C{tuple} or C{list} where the first C{str}
    is the attribute name, and the second C{str} is an encoding type, either "str" or "base64".

    @param directory: C{Directory} the object obtained from an odInit call.
    @param nodeName: C{str} containing the OD nodeName to query.
    @param attributes: C{list} or C{tuple} containing the attributes to return for each record.
    @return: C{dict} of attributes found.
    """
    details, error = directory.node.nodeDetailsForKeys_error_(attributes, None)
    if error:
        log.error(error)
        raise ODError(error)
    return details


def listAllRecordsWithAttributes_list(directory, recordType, attributes, count=0):
    """
    List records in Open Directory, and return key attributes for each one.
    The attributes can be a C{str} for the attribute name, or a C{tuple} or C{list} where the first C{str}
    is the attribute name, and the second C{str} is an encoding type, either "str" or "base64".

    @param directory: C{Directory} the object obtained from an odInit call.
    @param recordType: C{str}, C{tuple} or C{list} containing the OD record types to lookup.
    @param attributes: C{list} or C{tuple} containing the attributes to return for each record.
    @param count: C{int} maximum number of records to return (zero returns all).
    @return: C{list} containing a C{list} of C{str} (record name) and C{dict} attributes
        for each record found, or C{None} otherwise.
    """
    results = []

    attributeNames, encodings = attributeNamesFromList(attributes)

    query, error = odframework.ODQuery.queryWithNode_forRecordTypes_attribute_matchType_queryValues_returnAttributes_maximumResults_error_(
        directory.node,
        recordType,
        None,
        MATCHANY,
        None,
        attributeNames,
        count,
        None)
    if error:
        log.error(error)
        raise ODError(error)
    records, error = query.resultsAllowingPartial_error_(False, None)
    if error:
        log.error(error)
        raise ODError(error)
    for record in records:
        results.append(recordToResult(record, encodings))
    return results


def queryRecordsWithAttribute_list(directory, attr, value, matchType, casei, recordType, attributes, count=0):
    """
    List records in Open Directory matching specified attribute/value, and return key attributes for each one.
    The attributes can be a C{str} for the attribute name, or a C{tuple} or C{list} where the first C{str}
    is the attribute name, and the second C{str} is an encoding type, either "str" or "base64".

    @param directory: C{Directory} the object obtained from an odInit call.
    @param attr: C{str} containing the attribute to search.
    @param value: C{str} containing the value to search for.
    @param matchType: C{int} DS match type to use when searching.
    @param casei: C{True} to do case-insensitive match, C{False} otherwise.
    @param recordType: C{str}, C{tuple} or C{list} containing the OD record types to lookup.
    @param attributes: C{list} or C{tuple} containing the attributes to return for each record.
    @param count: C{int} maximum number of records to return (zero returns all).
    @return: C{list} containing a C{list} of C{str} (record name) and C{dict} attributes
        for each record found, or C{None} otherwise.
    """

    results = []

    attributeNames, encodings = attributeNamesFromList(attributes)

    query, error = odframework.ODQuery.queryWithNode_forRecordTypes_attribute_matchType_queryValues_returnAttributes_maximumResults_error_(
        directory.node,
        recordType,
        attr,
        adjustMatchType(matchType, casei),
        value.decode("utf-8"),
        attributeNames,
        count,
        None)
    if error:
        log.error(error)
        raise ODError(error)
    records, error = query.resultsAllowingPartial_error_(False, None)
    if error:
        log.error(error)
        raise ODError(error)
    for record in records:
        results.append(recordToResult(record, encodings))
    return results


def queryRecordsWithAttributes_list(directory, compound, casei, recordType, attributes, count=0):
    """
    List records in Open Directory matching specified criteria, and return key attributes for each one.
    The attributes can be a C{str} for the attribute name, or a C{tuple} or C{list} where the first C{str}
    is the attribute name, and the second C{str} is an encoding type, either "str" or "base64".

    @param directory: C{Directory} the object obtained from an odInit call.
    @param compound: C{str} containing the compound search query to use.
    @param casei: C{True} to do case-insensitive match, C{False} otherwise.
    @param recordType: C{str}, C{tuple} or C{list} containing the OD record types to lookup.
    @param attributes: C{list} or C{tuple} containing the attributes to return for each record.
    @param count: C{int} maximum number of records to return (zero returns all).
    @return: C{list} containing a C{list} of C{str} (record name) and C{dict} attributes
        for each record found, or C{None} otherwise.
    """
    results = []

    attributeNames, encodings = attributeNamesFromList(attributes)

    query, error = odframework.ODQuery.queryWithNode_forRecordTypes_attribute_matchType_queryValues_returnAttributes_maximumResults_error_(
        directory.node,
        recordType,
        None,
        0x210B, # adjustMatchType(matchType, casei),
        compound,
        attributeNames,
        count,
        None)
    if error:
        log.error(error)
        raise ODError(error)
    records, error = query.resultsAllowingPartial_error_(False, None)
    if error:
        log.error(error)
        raise ODError(error)
    for record in records:
        results.append(recordToResult(record, encodings))
    return results


def getUserRecord(directory, user):
    """
    Look up the record for the given user within the directory's node

    @param directory: C{Directory} the object obtained from an odInit call.
    @param user: C{str} the user identifier/directory record name to fetch.
    @return: OD record if the user was found, None otherwise.
    """
    record, error = directory.node.recordWithRecordType_name_attributes_error_(
        dsattributes.kDSStdRecordTypeUsers,
        user,
        None,
        None
    )
    if error:
        log.error(error)
        raise ODError(error)
    return record

def authenticateUserBasic(directory, nodeName, user, password):
    """
    Authenticate a user with a password to Open Directory.

    @param directory: C{Directory} the object obtained from an odInit call.
    @param nodeName: C{str} the directory nodeName for the record to check.
    @param user: C{str} the user identifier/directory record name to check.
    @param pswd: C{str} containing the password to check.
    @return: C{True} if the user was found, C{False} otherwise.
    """
    record = getUserRecord(directory, user)
    if record is None:
        raise ODError("Record not found")

    result, error = record.verifyPassword_error_(password, None)
    if error:
        log.error(error)
        raise ODError(error)
    return result


def authenticateUserDigest(directory, nodeName, user, challenge, response, method):
    """
    Authenticate using HTTP Digest credentials to Open Directory.

    @param directory: C{Directory} the object obtained from an odInit call.
    @param nodeName: C{str} the directory nodeName for the record to check.
    @param user: C{str} the user identifier/directory record name to check.
    @param challenge: C{str} the HTTP challenge sent to the client.
    @param response: C{str} the HTTP response sent from the client.
    @param method: C{str} the HTTP method being used.
    @return: C{True} if the user was found, C{False} otherwise.
    """
    record = getUserRecord(directory, user)
    if record is None:
        raise ODError("Record not found")

    # TODO: what are these other return values?
    result, mystery1, mystery2, error = record.verifyExtendedWithAuthenticationType_authenticationItems_continueItems_context_error_(
        DIGEST_MD5,
        [user, challenge, response, method],
        None, None, None
    )
    if error:
        log.error(error)
        raise ODError(error)
    return result

class ODError(Exception):
    """
    Exceptions from DirectoryServices errors.
    """
    pass
