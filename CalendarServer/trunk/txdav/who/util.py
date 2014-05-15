##
# Copyright (c) 2006-2014 Apple Inc. All rights reserved.
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


from calendarserver.tap.util import getDBPool, storeFromConfig
from twext.python.log import Logger
from twext.python.types import MappingProxyType
from twext.who.aggregate import DirectoryService as AggregateDirectoryService
from twext.who.idirectory import (
    FieldName as BaseFieldName, RecordType, DirectoryConfigurationError
)
from twext.who.ldap import (
    DirectoryService as LDAPDirectoryService, LDAPAttribute,
    FieldName as LDAPFieldName,
    RecordTypeSchema, LDAPObjectClass
)
from twext.who.util import ConstantsContainer
from twisted.cred.credentials import UsernamePassword
from twisted.python.filepath import FilePath
from twisted.python.reflect import namedClass
from twistedcaldav.config import fullServerPath
from txdav.who.augment import AugmentedDirectoryService
from txdav.who.delegates import DirectoryService as DelegateDirectoryService
from txdav.who.idirectory import (
    RecordType as CalRecordType,
    FieldName as CalFieldName
)
from txdav.who.wiki import DirectoryService as WikiDirectoryService
from txdav.who.xml import DirectoryService as XMLDirectoryService


log = Logger()


def directoryFromConfig(config, store=None, serversDB=None):
    """
    Return a directory service based on the config.  If you want to go through
    AMP to talk to one of these as a client, instantiate
    txdav.dps.client.DirectoryService
    """

    # Note: Currently the directory needs a store, and the store needs a
    # directory.  Originally the directory's store was going to be different
    # from the calendar and contacts store, but we're not doing that, maybe
    # ever, since it brings more headaches (managing multiple schema upgrades,
    # etc.) You can pass store=None in here and the store will be created for
    # you, but don't pass store=None if you already have called storeFromConfig()
    # within this same process; pass that store in instead.

    # TODO: use proxyForInterface to ensure we're only using the DPS related
    # store API.  Also define an IDirectoryProxyStore Interface
    if store is None:
        _ignore_pool, txnFactory = getDBPool(config)
        store = storeFromConfig(config, txnFactory, None)

    return buildDirectory(
        store,
        config.DataRoot,
        [config.DirectoryService, config.ResourceService],
        config.AugmentService,
        config.Authentication.Wiki,
        serversDB=serversDB
    )


def buildDirectory(
    store, dataRoot, servicesInfo, augmentServiceInfo, wikiServiceInfo,
    serversDB=None
):
    """
    Return a directory without using a config object; suitable for tests
    which need to have mulitple directory instances.

    @param store: The store.
    @param dataRoot: The path to the directory containing xml files for any xml
        based services.
    @param servicesInfo:  An interable of ConfigDicts mirroring the
        DirectoryService and ResourceService sections of stdconfig
    @param augmentServiceInfo: A ConfigDict mirroring the AugmentService section
        of stdconfig
    @param wikiServiceInfo: A ConfigDict mirroring the Wiki section of stdconfig
    @param serversDB: A ServersDB object to assign to the directory
    """

    aggregatedServices = []

    for serviceValue in servicesInfo:

        if not serviceValue.Enabled:
            continue

        directoryType = serviceValue.type.lower()
        params = serviceValue.params

        if "xml" in directoryType:
            xmlFile = params.xmlFile
            xmlFile = fullServerPath(dataRoot, xmlFile)
            fp = FilePath(xmlFile)
            if not fp.exists():
                fp.setContent(DEFAULT_XML_CONTENT)
            directory = XMLDirectoryService(fp)

        elif "opendirectory" in directoryType:
            from twext.who.opendirectory import (
                DirectoryService as ODDirectoryService
            )
            directory = ODDirectoryService()

        elif "ldap" in directoryType:
            if params.credentials.dn and params.credentials.password:
                creds = UsernamePassword(
                    params.credentials.dn,
                    params.credentials.password
                )
            else:
                creds = None
            directory = LDAPDirectoryService(
                params.uri,
                params.rdnSchema.base,
                credentials=creds,
                fieldNameToAttributesMap=MappingProxyType({
                    BaseFieldName.uid: ("apple-generateduid",),
                    BaseFieldName.guid: ("apple-generateduid",),
                    BaseFieldName.shortNames: (LDAPAttribute.uid.value,),
                    BaseFieldName.fullNames: (LDAPAttribute.cn.value,),
                    BaseFieldName.emailAddresses: (LDAPAttribute.mail.value,),
                    BaseFieldName.password: (LDAPAttribute.userPassword.value,),
                    LDAPFieldName.memberDNs: (LDAPAttribute.uniqueMember.value,),
                }),
                recordTypeSchemas=MappingProxyType({
                    RecordType.user: RecordTypeSchema(
                        relativeDN=u"ou=People",

                        # (objectClass=inetOrgPerson)
                        attributes=(
                            (
                                LDAPAttribute.objectClass.value,
                                LDAPObjectClass.inetOrgPerson.value,
                            ),
                        ),
                    ),

                    RecordType.group: RecordTypeSchema(
                        relativeDN=u"ou=Groups",

                        # (objectClass=groupOfNames)
                        attributes=(
                            (
                                LDAPAttribute.objectClass.value,
                                LDAPObjectClass.groupOfUniqueNames.value,
                            ),
                        ),
                    ),

                })
            )

        elif "inmemory" in directoryType:
            from txdav.who.test.support import InMemoryDirectoryService
            directory = InMemoryDirectoryService()

        else:
            log.error("Invalid DirectoryType: {dt}", dt=directoryType)
            raise DirectoryConfigurationError

        # Set the appropriate record types on each service
        types = []
        fieldNames = []
        for recordTypeName in params.recordTypes:
            recordType = {
                "users": RecordType.user,
                "groups": RecordType.group,
                "locations": CalRecordType.location,
                "resources": CalRecordType.resource,
                "addresses": CalRecordType.address,
            }.get(recordTypeName, None)

            if recordType is None:
                log.error("Invalid Record Type: {rt}", rt=recordTypeName)
                raise DirectoryConfigurationError

            if recordType in types:
                log.error("Duplicate Record Type: {rt}", rt=recordTypeName)
                raise DirectoryConfigurationError

            types.append(recordType)

        directory.recordType = ConstantsContainer(types)
        directory.fieldName = ConstantsContainer(
            (directory.fieldName, CalFieldName)
        )
        fieldNames.append(directory.fieldName)
        aggregatedServices.append(directory)

    #
    # Setup the Augment Service
    #
    if augmentServiceInfo.type:
        for augmentFile in augmentServiceInfo.params.xmlFiles:
            augmentFile = fullServerPath(dataRoot, augmentFile)
            augmentFilePath = FilePath(augmentFile)
            if not augmentFilePath.exists():
                augmentFilePath.setContent(DEFAULT_AUGMENT_CONTENT)

        augmentClass = namedClass(augmentServiceInfo.type)
        log.info(
            "Configuring augment service of type: {augmentClass}",
            augmentClass=augmentClass
        )
        try:
            augmentService = augmentClass(**augmentServiceInfo.params)
        except IOError:
            log.error("Could not start augment service")
            raise
    else:
        augmentService = None

    userDirectory = None
    for directory in aggregatedServices:
        if RecordType.user in directory.recordTypes():
            userDirectory = directory
            break
    else:
        log.error("No directory service set up for users")
        raise DirectoryConfigurationError

    # Delegate service
    delegateDirectory = DelegateDirectoryService(
        userDirectory.realmName,
        store
    )
    aggregatedServices.append(delegateDirectory)

    # Wiki service
    if wikiServiceInfo.Enabled:
        aggregatedServices.append(
            WikiDirectoryService(
                userDirectory.realmName,
                wikiServiceInfo.CollabHost,
                wikiServiceInfo.CollabPort
            )
        )

    # Aggregate service
    aggregateDirectory = AggregateDirectoryService(
        userDirectory.realmName, aggregatedServices
    )

    # Augment service
    try:
        fieldNames.append(CalFieldName)
        augmented = AugmentedDirectoryService(
            aggregateDirectory, store, augmentService
        )
        augmented.fieldName = ConstantsContainer(fieldNames)

        # The delegate directory needs a way to look up user/group records
        # so hand it a reference to the augmented directory.
        # FIXME: is there a better pattern to use here?
        delegateDirectory.setMasterDirectory(augmented)

    except Exception as e:
        log.error("Could not create directory service", error=e)
        raise

    if serversDB is not None:
        augmented.setServersDB(serversDB)

    return augmented


DEFAULT_XML_CONTENT = """<?xml version="1.0" encoding="utf-8"?>

<directory realm="Realm"/>
"""

DEFAULT_AUGMENT_CONTENT = """<?xml version="1.0" encoding="utf-8"?>

<augments/>
"""
