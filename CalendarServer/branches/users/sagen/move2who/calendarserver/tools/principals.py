#!/usr/bin/env python

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
from __future__ import print_function

import sys
import os
import operator
from getopt import getopt, GetoptError
from uuid import UUID

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from txdav.xml import element as davxml
from txdav.who.delegates import addDelegate, removeDelegate


from twistedcaldav.config import config
from twistedcaldav.directory.directory import UnknownRecordTypeError
from txdav.who.groups import schedulePolledGroupCachingUpdate

from calendarserver.tools.util import (
    booleanArgument, proxySubprincipal,
    recordForPrincipalID, prettyPrincipal, prettyRecord, ProxyError
)
from twistedcaldav.directory.augment import allowedAutoScheduleModes

from calendarserver.tools.cmdline import utilityMain, WorkerService


def usage(e=None):
    if e:
        if isinstance(e, UnknownRecordTypeError):
            print("Valid record types:")
            for recordType in config.directory.recordTypes():
                print("    %s" % (recordType,))

        print(e)
        print("")

    name = os.path.basename(sys.argv[0])
    print("usage: %s [options] action_flags principal [principal ...]" % (name,))
    print("       %s [options] --list-principal-types" % (name,))
    print("       %s [options] --list-principals type" % (name,))
    print("")
    print("  Performs the given actions against the giving principals.")
    print("")
    print("  Principals are identified by one of the following:")
    print("    Type and shortname (eg.: users:wsanchez)")
    #print("    A principal path (eg.: /principals/users/wsanchez/)")
    print("    A GUID (eg.: E415DBA7-40B5-49F5-A7CC-ACC81E4DEC79)")
    print("")
    print("options:")
    print("  -h --help: print this help and exit")
    print("  -f --config <path>: Specify caldavd.plist configuration path")
    print("  -v --verbose: print debugging information")
    print("")
    print("actions:")
    print("  --search <search-string>: search for matching principals")
    print("  --list-principal-types: list all of the known principal types")
    print("  --list-principals type: list all principals of the given type")
    print("  --list-read-proxies: list proxies with read-only access")
    print("  --list-write-proxies: list proxies with read-write access")
    print("  --list-proxies: list all proxies")
    print("  --list-proxy-for: principals this principal is a proxy for")
    print("  --add-read-proxy=principal: add a read-only proxy")
    print("  --add-write-proxy=principal: add a read-write proxy")
    print("  --remove-proxy=principal: remove a proxy")
    print("  --set-auto-schedule={true|false}: set auto-accept state")
    print("  --get-auto-schedule: read auto-schedule state")
    print("  --set-auto-schedule-mode={default|none|accept-always|decline-always|accept-if-free|decline-if-busy|automatic}: set auto-schedule mode")
    print("  --get-auto-schedule-mode: read auto-schedule mode")
    print("  --set-auto-accept-group=principal: set auto-accept-group")
    print("  --get-auto-accept-group: read auto-accept-group")
    print("  --add {locations|resources|addresses} 'full name' [record name] [GUID]: add a principal")
    print("  --remove: remove a principal")
    print("  --set-geo=url: set the geo: url for an address (e.g. geo:37.331741,-122.030333)")
    print("  --get-geo: get the geo: url for an address")
    print("  --set-street-address=streetaddress: set the street address string for an address")
    print("  --get-street-address: get the street address string for an address")
    print("  --set-address=guid: associate principal with an address (by guid)")
    print("  --get-address: get the associated address's guid")

    if e:
        sys.exit(64)
    else:
        sys.exit(0)



class PrincipalService(WorkerService):
    """
    Executes principals-related functions in a context which has access to the store
    """

    function = None
    params = []

    @inlineCallbacks
    def doWork(self):
        """
        Calls the function that's been assigned to "function" and passes the root
        resource, directory, store, and whatever has been assigned to "params".
        """
        if self.function is not None:
            yield self.function(self.store, *self.params)


attrMap = {
    'GeneratedUID': {'attr': 'guid', },
    'RealName': {'attr': 'fullName', },
    'RecordName': {'attr': 'shortNames', },
    'AutoSchedule': {'attr': 'autoSchedule', },
    'AutoAcceptGroup': {'attr': 'autoAcceptGroup', },

    'Comment': {'extras': True, 'attr': 'comment', },
    'Description': {'extras': True, 'attr': 'description', },
    'Type': {'extras': True, 'attr': 'type', },

    # For "Locations", i.e. scheduled spaces
    'Capacity': {'extras': True, 'attr': 'capacity', },
    'Floor': {'extras': True, 'attr': 'floor', },
    'AssociatedAddress': {'extras': True, 'attr': 'associatedAddress', },

    # For "Addresses", i.e. nonscheduled areas containing Locations
    'AbbreviatedName': {'extras': True, 'attr': 'abbreviatedName', },
    'StreetAddress': {'extras': True, 'attr': 'streetAddress', },
    'Geo': {'extras': True, 'attr': 'geo', },
}


def main():
    try:
        (optargs, args) = getopt(
            sys.argv[1:], "a:hf:P:v", [
                "help",
                "config=",
                "add=",
                "remove",
                "search=",
                "list-principal-types",
                "list-principals=",
                "list-read-proxies",
                "list-write-proxies",
                "list-proxies",
                "list-proxy-for",
                "add-read-proxy=",
                "add-write-proxy=",
                "remove-proxy=",
                "set-auto-schedule=",
                "get-auto-schedule",
                "set-auto-schedule-mode=",
                "get-auto-schedule-mode",
                "set-auto-accept-group=",
                "get-auto-accept-group",
                "set-geo=",
                "get-geo",
                "set-address=",
                "get-address",
                "set-street-address=",
                "get-street-address",
                "verbose",
            ],
        )
    except GetoptError, e:
        usage(e)

    #
    # Get configuration
    #
    configFileName = None
    # addType = None
    listPrincipalTypes = False
    listPrincipals = None
    searchPrincipals = None
    principalActions = []
    verbose = False

    for opt, arg in optargs:

        # Args come in as encoded bytes
        arg = arg.decode("utf-8")

        if opt in ("-h", "--help"):
            usage()

        elif opt in ("-v", "--verbose"):
            verbose = True

        elif opt in ("-f", "--config"):
            configFileName = arg

        # elif opt in ("-a", "--add"):
        #     addType = arg

        # elif opt in ("-r", "--remove"):
        #     principalActions.append((action_removePrincipal,))

        elif opt in ("", "--list-principal-types"):
            listPrincipalTypes = True

        elif opt in ("", "--list-principals"):
            listPrincipals = arg

        elif opt in ("", "--search"):
            searchPrincipals = arg

        elif opt in ("", "--list-read-proxies"):
            principalActions.append((action_listProxies, "read"))

        elif opt in ("", "--list-write-proxies"):
            principalActions.append((action_listProxies, "write"))

        elif opt in ("-L", "--list-proxies"):
            principalActions.append((action_listProxies, "read", "write"))

        elif opt in ("--list-proxy-for"):
            principalActions.append((action_listProxyFor, "read", "write"))

        elif opt in ("--add-read-proxy", "--add-write-proxy"):
            if "read" in opt:
                proxyType = "read"
            elif "write" in opt:
                proxyType = "write"
            else:
                raise AssertionError("Unknown proxy type")
            principalActions.append((action_addProxy, proxyType, arg))

        elif opt in ("", "--remove-proxy"):
            principalActions.append((action_removeProxy, arg))

        # elif opt in ("", "--set-auto-schedule"):
        #     try:
        #         autoSchedule = booleanArgument(arg)
        #     except ValueError, e:
        #         abort(e)

        #     principalActions.append((action_setAutoSchedule, autoSchedule))

        # elif opt in ("", "--get-auto-schedule"):
        #     principalActions.append((action_getAutoSchedule,))

        # elif opt in ("", "--set-auto-schedule-mode"):
        #     try:
        #         if arg not in allowedAutoScheduleModes:
        #             raise ValueError("Unknown auto-schedule mode: %s" % (arg,))
        #         autoScheduleMode = arg
        #     except ValueError, e:
        #         abort(e)

        #     principalActions.append((action_setAutoScheduleMode, autoScheduleMode))

        # elif opt in ("", "--get-auto-schedule-mode"):
        #     principalActions.append((action_getAutoScheduleMode,))

        # elif opt in ("", "--set-auto-accept-group"):
        #     try:
        #         yield recordForPrincipalID(arg, checkOnly=True)
        #     except ValueError, e:
        #         abort(e)

        #     principalActions.append((action_setAutoAcceptGroup, arg))

        # elif opt in ("", "--get-auto-accept-group"):
        #     principalActions.append((action_getAutoAcceptGroup,))

        # elif opt in ("", "--set-geo"):
        #     principalActions.append((action_setValue, "Geo", arg))

        # elif opt in ("", "--get-geo"):
        #     principalActions.append((action_getValue, "Geo"))

        # elif opt in ("", "--set-street-address"):
        #     principalActions.append((action_setValue, "StreetAddress", arg))

        # elif opt in ("", "--get-street-address"):
        #     principalActions.append((action_getValue, "StreetAddress"))

        # elif opt in ("", "--set-address"):
        #     principalActions.append((action_setValue, "AssociatedAddress", arg))

        # elif opt in ("", "--get-address"):
        #     principalActions.append((action_getValue, "AssociatedAddress"))

        else:
            raise NotImplementedError(opt)

    #
    # List principals
    #
    if listPrincipalTypes:
        if args:
            usage("Too many arguments")

        function = runListPrincipalTypes
        params = ()

    # elif addType:

    #     try:
    #         addType = matchStrings(addType, ["locations", "resources", "addresses"])
    #     except ValueError, e:
    #         print(e)
    #         return

    #     try:
    #         fullName, shortName, guid = parseCreationArgs(args)
    #     except ValueError, e:
    #         print(e)
    #         return

    #     if shortName is not None:
    #         shortNames = [shortName]
    #     else:
    #         shortNames = ()

    #     function = runAddPrincipal
    #     params = (addType, guid, shortNames, fullName)

    elif listPrincipals:
        try:
            listPrincipals = matchStrings(
                listPrincipals,
                ["users", "groups", "locations", "resources", "addresses"]
            )
        except ValueError, e:
            print(e)
            return

        if args:
            usage("Too many arguments")

        function = runListPrincipals
        params = (listPrincipals,)

    elif searchPrincipals:
        function = runSearch
        params = (searchPrincipals,)

    else:
        if not args:
            usage("No principals specified.")

        # We don't have a directory yet
        # for arg in args:
        #     try:
        #         yield recordForPrincipalID(arg, checkOnly=True)
        #     except ValueError, e:
        #         abort(e)

        unicodeArgs = [a.decode("utf-8") for a in args]
        function = runPrincipalActions
        params = (unicodeArgs, principalActions)

    PrincipalService.function = function
    PrincipalService.params = params
    utilityMain(configFileName, PrincipalService, verbose=verbose)



def runListPrincipalTypes(service, store):
    directory = store.directoryService()
    for recordType in directory.recordTypes():
        print(directory.recordTypeToOldString(recordType))
    return succeed(None)



@inlineCallbacks
def runListPrincipals(service, store, listPrincipals):
    directory = store.directoryService()
    recordType = directory.oldNameToRecordType(listPrincipals)
    try:
        records = list((yield directory.recordsWithRecordType(recordType)))
        if records:
            printRecordList(records)
        else:
            print("No records of type %s" % (listPrincipals,))
    except UnknownRecordTypeError, e:
        usage(e)
    returnValue(None)



@inlineCallbacks
def runPrincipalActions(service, store, principalIDs, actions):
    directory = store.directoryService()
    for principalID in principalIDs:
        # Resolve the given principal IDs to records
        try:
            record = yield recordForPrincipalID(directory, principalID)
        except ValueError:
            record = None

        if record is None:
            sys.stderr.write("Invalid principal ID: %s\n" % (principalID,))
            continue

        # Performs requested actions
        for action in actions:
            (yield action[0](store, record, *action[1:]))
            print("")



@inlineCallbacks
def runSearch(service, store, searchTerm):
    directory = store.directoryService()
    fields = []
    for fieldName in ("fullNames", "emailAddresses"):
        fields.append((fieldName, searchTerm, True, "contains"))

    records = list((yield directory.recordsMatchingTokens(searchTerm.strip().split())))
    if records:
        records.sort(key=operator.attrgetter('fullNames'))
        print("{n} matches found:".format(n=len(records)))
        for record in records:
            print(
                "\n{d} ({rt})".format(
                    d=record.displayName,
                    rt=record.recordType.name
                )
            )
            print("   UID: {u}".format(u=record.uid,))
            print(
                "   Record name{plural}: {names}".format(
                    plural=("s" if len(record.shortNames) > 1 else ""),
                    names=(", ".join(record.shortNames))
                )
            )
            try:
                if record.emailAddresses:
                    print(
                        "   Email{plural}: {emails}".format(
                            plural=("s" if len(record.emailAddresses) > 1 else ""),
                            emails=(", ".join(record.emailAddresses))
                        )
                    )
            except AttributeError:
                pass
    else:
        print("No matches found")

    print("")



# @inlineCallbacks
# def runAddPrincipal(service, store, addType, guid, shortNames, fullName):
#     directory = store.directoryService()
#     try:
#         # FIXME STOP USING GUID
#         yield updateRecord(
#             True, directory, addType, guid=guid,
#             shortNames=shortNames, fullName=fullName
#         )
#         print("Added '%s'" % (fullName,))
#     except DirectoryError, e:
#         print(e)



# def action_removePrincipal(store, record):
#     directory = store.directoryService()
#     fullName = record.displayName
#     shortName = record.shortNames[0]

#     yield directory.destroyRecord(record.recordType, uid=record.uid)
#     print("Removed '%s' %s %s" % (fullName, shortName, record.uid))




@inlineCallbacks
def action_listProxies(store, record, *proxyTypes):
    directory = store.directoryService()
    for proxyType in proxyTypes:

        groupRecordType = {
            "read": directory.recordType.readDelegateGroup,
            "write": directory.recordType.writeDelegateGroup,
        }.get(proxyType)

        pseudoGroup = yield directory.recordWithShortName(
            groupRecordType,
            record.uid
        )
        proxies = yield pseudoGroup.members()
        if proxies:
            print("%s proxies for %s:" % (
                {"read": "Read-only", "write": "Read/write"}[proxyType],
                prettyRecord(record)
            ))
            printRecordList(proxies)
            print("")
        else:
            print("No %s proxies for %s" % (proxyType, prettyRecord(record)))


@inlineCallbacks
def action_listProxyFor(store, record, *proxyTypes):
    directory = store.directoryService()
    for proxyType in proxyTypes:

        groupRecordType = {
            "read": directory.recordType.readDelegatorGroup,
            "write": directory.recordType.writeDelegatorGroup,
        }.get(proxyType)

        pseudoGroup = yield directory.recordWithShortName(
            groupRecordType,
            record.uid
        )
        proxies = yield pseudoGroup.members()
        if proxies:
            print("%s is a %s proxy for:" % (
                prettyRecord(record),
                {"read": "Read-only", "write": "Read/write"}[proxyType]
            ))
            printRecordList(proxies)
            print("")
        else:
            print(
                "{r} is not a {t} proxy for anyone".format(
                    r=prettyRecord(record),
                    t={"read": "Read-only", "write": "Read/write"}[proxyType]
                )
            )


@inlineCallbacks
def _addRemoveProxy(fn, store, record, proxyType, *proxyIDs):
    directory = store.directoryService()
    readWrite = (proxyType == "write")
    for proxyID in proxyIDs:
        proxyRecord = yield recordForPrincipalID(directory, proxyID)
        if proxyRecord is None:
            print("Invalid principal ID: %s" % (proxyID,))
        else:
            txn = store.newTransaction()
            yield fn(txn, record, proxyRecord, readWrite)
            yield txn.commit()


def action_addProxy(store, record, proxyType, *proxyIDs):
    return _addRemoveProxy(addDelegate, store, record, proxyType, *proxyIDs)


@inlineCallbacks
def action_removeProxy(store, record, *proxyIDs):
    # Write
    yield _addRemoveProxy(removeDelegate, store, record, "write", *proxyIDs)
    # Read
    yield _addRemoveProxy(removeDelegate, store, record, "read", *proxyIDs)



# @inlineCallbacks
# def setProxies(store, principal, readProxyPrincipals, writeProxyPrincipals, directory=None):
#     """
#     Set read/write proxies en masse for a principal
#     @param principal: DirectoryPrincipalResource
#     @param readProxyPrincipals: a list of principal IDs (see principalForPrincipalID)
#     @param writeProxyPrincipals: a list of principal IDs (see principalForPrincipalID)
#     """

#     proxyTypes = [
#         ("read", readProxyPrincipals),
#         ("write", writeProxyPrincipals),
#     ]
#     for proxyType, proxyIDs in proxyTypes:
#         if proxyIDs is None:
#             continue
#         subPrincipal = proxySubprincipal(principal, proxyType)
#         if subPrincipal is None:
#             raise ProxyError("Unable to edit %s proxies for %s\n" % (proxyType,
#                 prettyPrincipal(principal)))
#         memberURLs = []
#         for proxyID in proxyIDs:
#             proxyPrincipal = yield principalForPrincipalID(proxyID, directory=directory)
#             proxyURL = proxyPrincipal.url()
#             memberURLs.append(davxml.HRef(proxyURL))
#         membersProperty = davxml.GroupMemberSet(*memberURLs)
#         yield subPrincipal.writeProperty(membersProperty, None)
#         if store is not None:
#             # Schedule work the PeerConnectionPool will pick up as overdue
#             yield schedulePolledGroupCachingUpdate(store)



# @inlineCallbacks
# def getProxies(principal, directory=None):
#     """
#     Returns a tuple containing the GUIDs for read proxies and write proxies
#     of the given principal
#     """

#     proxies = {
#         "read": [],
#         "write": [],
#     }
#     for proxyType in proxies.iterkeys():
#         subPrincipal = proxySubprincipal(principal, proxyType)
#         if subPrincipal is not None:
#             membersProperty = (yield subPrincipal.readProperty(davxml.GroupMemberSet, None))
#             if membersProperty.children:
#                 for member in membersProperty.children:
#                     proxyPrincipal = yield principalForPrincipalID(str(member), directory=directory)
#                     proxies[proxyType].append(proxyPrincipal.record.guid)

#     returnValue((proxies['read'], proxies['write']))





# @inlineCallbacks
# def action_setAutoSchedule(rootResource, directory, store, principal, autoSchedule):
#     if principal.record.recordType == "groups":
#         print("Enabling auto-schedule for %s is not allowed." % (principal,))

#     elif principal.record.recordType == "users" and not config.Scheduling.Options.AutoSchedule.AllowUsers:
#         print("Enabling auto-schedule for %s is not allowed." % (principal,))

#     else:
#         print("Setting auto-schedule to %s for %s" % (
#             {True: "true", False: "false"}[autoSchedule],
#             prettyPrincipal(principal),
#         ))

#         (yield updateRecord(False, directory,
#             principal.record.recordType,
#             guid=principal.record.guid,
#             shortNames=principal.record.shortNames,
#             fullName=principal.record.fullName,
#             autoSchedule=autoSchedule,
#             **principal.record.extras
#         ))



# def action_getAutoSchedule(rootResource, directory, store, principal):
#     autoSchedule = principal.getAutoSchedule()
#     print("Auto-schedule for %s is %s" % (
#         prettyPrincipal(principal),
#         {True: "true", False: "false"}[autoSchedule],
#     ))



# @inlineCallbacks
# def action_setAutoScheduleMode(rootResource, directory, store, principal, autoScheduleMode):
#     if principal.record.recordType == "groups":
#         print("Setting auto-schedule mode for %s is not allowed." % (principal,))

#     elif principal.record.recordType == "users" and not config.Scheduling.Options.AutoSchedule.AllowUsers:
#         print("Setting auto-schedule mode for %s is not allowed." % (principal,))

#     else:
#         print("Setting auto-schedule mode to %s for %s" % (
#             autoScheduleMode,
#             prettyPrincipal(principal),
#         ))

#         (yield updateRecord(False, directory,
#             principal.record.recordType,
#             guid=principal.record.guid,
#             shortNames=principal.record.shortNames,
#             fullName=principal.record.fullName,
#             autoScheduleMode=autoScheduleMode,
#             **principal.record.extras
#         ))



# def action_getAutoScheduleMode(rootResource, directory, store, principal):
#     autoScheduleMode = principal.getAutoScheduleMode()
#     if not autoScheduleMode:
#         autoScheduleMode = "automatic"
#     print("Auto-schedule mode for %s is %s" % (
#         prettyPrincipal(principal),
#         autoScheduleMode,
#     ))



# @inlineCallbacks
# def action_setAutoAcceptGroup(rootResource, directory, store, principal, autoAcceptGroup):
#     if principal.record.recordType == "groups":
#         print("Setting auto-accept-group for %s is not allowed." % (principal,))

#     elif principal.record.recordType == "users" and not config.Scheduling.Options.AutoSchedule.AllowUsers:
#         print("Setting auto-accept-group for %s is not allowed." % (principal,))

#     else:
#         groupPrincipal = yield principalForPrincipalID(autoAcceptGroup, directory=directory)
#         if groupPrincipal is None or groupPrincipal.record.recordType != "groups":
#             print("Invalid principal ID: %s" % (autoAcceptGroup,))
#         else:
#             print("Setting auto-accept-group to %s for %s" % (
#                 prettyPrincipal(groupPrincipal),
#                 prettyPrincipal(principal),
#             ))

#             (yield updateRecord(False, directory,
#                 principal.record.recordType,
#                 guid=principal.record.guid,
#                 shortNames=principal.record.shortNames,
#                 fullName=principal.record.fullName,
#                 autoAcceptGroup=groupPrincipal.record.guid,
#                 **principal.record.extras
#             ))



# def action_getAutoAcceptGroup(rootResource, directory, store, principal):
#     autoAcceptGroup = principal.getAutoAcceptGroup()
#     if autoAcceptGroup:
#         record = yield directory.recordWithGUID(autoAcceptGroup)
#         if record is not None:
#             groupPrincipal = yield directory.principalCollection.principalForUID(record.uid)
#             if groupPrincipal is not None:
#                 print("Auto-accept-group for %s is %s" % (
#                     prettyPrincipal(principal),
#                     prettyPrincipal(groupPrincipal),
#                 ))
#                 return
#         print("Invalid auto-accept-group assigned: %s" % (autoAcceptGroup,))
#     else:
#         print("No auto-accept-group assigned to %s" % (prettyPrincipal(principal),))



# @inlineCallbacks
# def action_setValue(rootResource, directory, store, principal, name, value):
#     print("Setting %s to %s for %s" % (
#         name, value, prettyPrincipal(principal),
#     ))

#     principal.record.extras[attrMap[name]["attr"]] = value
#     (yield updateRecord(False, directory,
#         principal.record.recordType,
#         guid=principal.record.guid,
#         shortNames=principal.record.shortNames,
#         fullName=principal.record.fullName,
#         **principal.record.extras
#     ))



# def action_getValue(rootResource, directory, store, principal, name):
#     print("%s for %s is %s" % (
#         name,
#         prettyPrincipal(principal),
#         principal.record.extras[attrMap[name]["attr"]]
#     ))



def abort(msg, status=1):
    sys.stdout.write("%s\n" % (msg,))
    try:
        reactor.stop()
    except RuntimeError:
        pass
    sys.exit(status)



def parseCreationArgs(args):
    """
    Look at the command line arguments for --add, and figure out which
    one is the shortName and which one is the guid by attempting to make a
    UUID object out of them.
    """

    fullName = args[0]
    shortName = None
    guid = None
    for arg in args[1:]:
        if isUUID(arg):
            if guid is not None:
                # Both the 2nd and 3rd args are UUIDs.  The first one
                # should be used for shortName.
                shortName = guid
            guid = arg
        else:
            shortName = arg

    if len(args) == 3 and guid is None:
        # both shortName and guid were specified but neither was a UUID
        raise ValueError("Invalid value for guid")

    return fullName, shortName, guid



def isUUID(value):
    try:
        UUID(value)
        return True
    except:
        return False



def matchStrings(value, validValues):
    for validValue in validValues:
        if validValue.startswith(value):
            return validValue

    raise ValueError("'%s' is not a recognized value" % (value,))



def printRecordList(records):
    results = [
        (record.displayName, record.recordType.name, record.uid, record.shortNames)
        for record in records
    ]
    results.sort()
    format = "%-22s %-10s %-20s %s"
    print(format % ("Full name", "Type", "UID", "Short names"))
    print(format % ("---------", "----", "---", "-----------"))
    for fullName, recordType, uid, shortNames in results:
        print(format % (fullName, recordType, uid, u", ".join(shortNames)))



@inlineCallbacks
def updateRecord(create, directory, recordType, **kwargs):
    """
    Create/update a record, including the extra work required to set the
    autoSchedule bit in the augment record.

    If C{create} is true, the record is created, otherwise update the record
    matching the guid in kwargs.
    """

    assignAutoSchedule = False
    if "autoSchedule" in kwargs:
        assignAutoSchedule = True
        autoSchedule = kwargs["autoSchedule"]
        del kwargs["autoSchedule"]
    elif create:
        assignAutoSchedule = True
        autoSchedule = recordType in ("locations", "resources")

    assignAutoScheduleMode = False
    if "autoScheduleMode" in kwargs:
        assignAutoScheduleMode = True
        autoScheduleMode = kwargs["autoScheduleMode"]
        del kwargs["autoScheduleMode"]
    elif create:
        assignAutoScheduleMode = True
        autoScheduleMode = None

    assignAutoAcceptGroup = False
    if "autoAcceptGroup" in kwargs:
        assignAutoAcceptGroup = True
        autoAcceptGroup = kwargs["autoAcceptGroup"]
        del kwargs["autoAcceptGroup"]
    elif create:
        assignAutoAcceptGroup = True
        autoAcceptGroup = None

    for key, value in kwargs.items():
        if isinstance(value, unicode):
            kwargs[key] = value.encode("utf-8")
        elif isinstance(value, list):
            newValue = [v.encode("utf-8") for v in value]
            kwargs[key] = newValue

    if create:
        record = yield directory.createRecord(recordType, **kwargs)
        kwargs['guid'] = record.guid
    else:
        try:
            record = yield directory.updateRecord(recordType, **kwargs)
        except NotImplementedError:
            # Updating of directory information is not supported by underlying
            # directory implementation, but allow augment information to be
            # updated
            record = yield directory.recordWithGUID(kwargs["guid"])
            pass

    augmentService = directory.serviceForRecordType(recordType).augmentService
    augmentRecord = (yield augmentService.getAugmentRecord(kwargs['guid'], recordType))

    if assignAutoSchedule:
        augmentRecord.autoSchedule = autoSchedule
    if assignAutoScheduleMode:
        augmentRecord.autoScheduleMode = autoScheduleMode
    if assignAutoAcceptGroup:
        augmentRecord.autoAcceptGroup = autoAcceptGroup
    (yield augmentService.addAugmentRecords([augmentRecord]))
    try:
        yield directory.updateRecord(recordType, **kwargs)
    except NotImplementedError:
        # Updating of directory information is not supported by underlying
        # directory implementation, but allow augment information to be
        # updated
        pass

    returnValue(record)



if __name__ == "__main__":
    main()
