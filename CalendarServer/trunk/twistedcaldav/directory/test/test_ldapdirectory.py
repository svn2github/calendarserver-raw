##
# Copyright (c) 2011 Apple Inc. All rights reserved.
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

try:
    from twistedcaldav.directory.ldapdirectory import (
        buildFilter, LdapDirectoryService, MissingGuidException,
        splitIntoBatches
    )
    from twistedcaldav.test.util import proxiesFile
    from twistedcaldav.directory.calendaruserproxyloader import XMLCalendarUserProxyLoader
    from twistedcaldav.directory import calendaruserproxy
    from twistedcaldav.directory.directory import GroupMembershipCache, GroupMembershipCacheUpdater
    from twisted.internet.defer import inlineCallbacks
except ImportError:
    print "Skipping because ldap module not installed"
else:
    from twistedcaldav.test.util import TestCase

    class BuildFilterTestCase(TestCase):

        def test_buildFilter(self):
            mapping = {
                "fullName" : "cn",
                "emailAddresses" : "mail",
                "firstName" : "givenName",
                "lastName" : "sn",
            }

            entries = [
                {
                    "fields" : [
                        ("fullName", "mor", True, u"starts-with"),
                        ("emailAddresses", "mor", True, u"starts-with"),
                        ("firstName", "mor", True, u"starts-with"),
                        ("lastName", "mor", True, u"starts-with")
                    ],
                    "operand" : "or",
                    "recordType" : None,
                    "expected" : "(|(cn=mor*)(mail=mor*)(givenName=mor*)(sn=mor*))"
                },
                {
                    "fields" : [
                        ("fullName", "mor(", True, u"starts-with"),
                        ("emailAddresses", "mor)", True, u"contains"),
                        ("firstName", "mor*", True, u"exact"),
                        ("lastName", "mor\\", True, u"starts-with")
                    ],
                    "operand" : "or",
                    "recordType" : None,
                    "expected" : "(|(cn=mor\\28*)(mail=*mor\\29*)(givenName=mor\\2a)(sn=mor\\5c*))"
                },
                {
                    "fields" : [
                        ("fullName", "mor", True, u"starts-with"),
                    ],
                    "operand" : "or",
                    "recordType" : None,
                    "expected" : "(cn=mor*)"
                },
                {
                    "fields" : [
                        ("fullName", "mor", True, u"contains"),
                        ("emailAddresses", "mor", True, u"equals"),
                        ("invalid", "mor", True, u"starts-with"),
                    ],
                    "operand" : "and",
                    "recordType" : None,
                    "expected" : "(&(cn=*mor*)(mail=mor))"
                },
                {
                    "fields" : [
                        ("invalid", "mor", True, u"contains"),
                        ("invalid", "mor", True, u"starts-with"),
                    ],
                    "operand" : "and",
                    "recordType" : None,
                    "expected" : None
                },
                {
                    "fields" : [ ],
                    "operand" : "and",
                    "recordType" : None,
                    "expected" : None
                },
            ]
            for entry in entries:
                self.assertEquals(
                    buildFilter(mapping, entry["fields"],
                        operand=entry["operand"]),
                    entry["expected"]
                )


    class LdapDirectoryTestWrapper(object):
        """
        A test stub which replaces search_s( ) with a version that will return
        whatever you have previously called addTestResults( ) with.
        """

        def __init__(self, actual):
            self.actual = actual
            self.testResults = []

        def addTestResults(self, results):
            self.testResults.insert(0, results)

        def search_s(self, base, scope, filterstr="(objectClass=*)",
            attrlist=None):
            return self.testResults.pop()


    class LdapDirectoryServiceTestCase(TestCase):

        def setUp(self):
            super(LdapDirectoryServiceTestCase, self).setUp()

            params = {
                "augmentService" : None,
                "groupMembershipCache" : None,
                "cacheTimeout": 1, # Minutes
                "negativeCaching": False,
                "warningThresholdSeconds": 3,
                "batchSize": 500,
                "queryLocationsImplicitly": True,
                "restrictEnabledRecords": False,
                "restrictToGroup": "",
                "recordTypes": ("users", "groups", "locations", "resources"),
                "uri": "ldap://localhost/",
                "tls": False,
                "tlsCACertFile": None,
                "tlsCACertDir": None,
                "tlsRequireCert": None, # never, allow, try, demand, hard
                "credentials": {
                    "dn": None,
                    "password": None,
                },
                "authMethod": "LDAP",
                "rdnSchema": {
                    "base": "dc=example,dc=com",
                    "guidAttr": "apple-generateduid",
                    "users": {
                        "rdn": "cn=users",
                        "attr": "uid", # used only to synthesize email address
                        "emailSuffix": None, # used only to synthesize email address
                        "filter": "(objectClass=apple-user)", # additional filter for this type
                        "loginEnabledAttr" : "", # attribute controlling login
                        "loginEnabledValue" : "yes", # "True" value of above attribute
                        "calendarEnabledAttr" : "enable-calendar", # attribute controlling calendaring
                        "calendarEnabledValue" : "yes", # "True" value of above attribute
                        "mapping": { # maps internal record names to LDAP
                            "recordName": "uid",
                            "fullName" : "cn",
                            "emailAddresses" : "mail",
                            "firstName" : "givenName",
                            "lastName" : "sn",
                        },
                    },
                    "groups": {
                        "rdn": "cn=groups",
                        "attr": "cn", # used only to synthesize email address
                        "emailSuffix": None, # used only to synthesize email address
                        "filter": "(objectClass=apple-group)", # additional filter for this type
                        "mapping": { # maps internal record names to LDAP
                            "recordName": "cn",
                            "fullName" : "cn",
                            "emailAddresses" : "mail",
                            "firstName" : "givenName",
                            "lastName" : "sn",
                        },
                    },
                    "locations": {
                        "rdn": "cn=places",
                        "attr": "cn", # used only to synthesize email address
                        "emailSuffix": None, # used only to synthesize email address
                        "filter": "(objectClass=apple-resource)", # additional filter for this type
                        "calendarEnabledAttr" : "", # attribute controlling calendaring
                        "calendarEnabledValue" : "yes", # "True" value of above attribute
                        "mapping": { # maps internal record names to LDAP
                            "recordName": "cn",
                            "fullName" : "cn",
                            "emailAddresses" : "mail",
                            "firstName" : "givenName",
                            "lastName" : "sn",
                        },
                    },
                    "resources": {
                        "rdn": "cn=resources",
                        "attr": "cn", # used only to synthesize email address
                        "emailSuffix": None, # used only to synthesize email address
                        "filter": "(objectClass=apple-resource)", # additional filter for this type
                        "calendarEnabledAttr" : "", # attribute controlling calendaring
                        "calendarEnabledValue" : "yes", # "True" value of above attribute
                        "mapping": { # maps internal record names to LDAP
                            "recordName": "cn",
                            "fullName" : "cn",
                            "emailAddresses" : "mail",
                            "firstName" : "givenName",
                            "lastName" : "sn",
                        },
                    },
                },
                "groupSchema": {
                    "membersAttr": "uniqueMember", # how members are specified
                    "nestedGroupsAttr": "", # how nested groups are specified
                    "memberIdAttr": "", # which attribute the above refer to
                },
                "resourceSchema": {
                    "resourceInfoAttr": "apple-resource-info", # contains location/resource info
                    "autoScheduleAttr": None,
                    "proxyAttr": None,
                    "readOnlyProxyAttr": None,
                },
                "partitionSchema": {
                    "serverIdAttr": "server-id", # maps to augments server-id
                    "partitionIdAttr": "partition-id", # maps to augments partition-id
                },
            }

            self.service = LdapDirectoryService(params)
            self.service.ldap = LdapDirectoryTestWrapper(self.service.ldap)


        def test_ldapRecordCreation(self):
            """
            Exercise _ldapResultToRecord(), which converts a dictionary
            of LDAP attributes into an LdapDirectoryRecord
            """

            # User without enabled-for-calendaring specified

            dn = "uid=odtestamanda,cn=users,dc=example,dc=com"
            guid = '9DC04A70-E6DD-11DF-9492-0800200C9A66'
            attrs = {
                'uid': ['odtestamanda'],
                'apple-generateduid': [guid],
                'sn': ['Test'],
                'mail': ['odtestamanda@example.com', 'alternate@example.com'],
                'givenName': ['Amanda'],
                'cn': ['Amanda Test']
            }

            record = self.service._ldapResultToRecord(dn, attrs,
                self.service.recordType_users)
            self.assertEquals(record.guid, guid)
            self.assertEquals(record.emailAddresses,
                set(['alternate@example.com', 'odtestamanda@example.com']))
            self.assertEquals(record.shortNames, ('odtestamanda',))
            self.assertEquals(record.fullName, 'Amanda Test')
            self.assertEquals(record.firstName, 'Amanda')
            self.assertEquals(record.lastName, 'Test')
            self.assertEquals(record.serverID, None)
            self.assertEquals(record.partitionID, None)
            self.assertFalse(record.enabledForCalendaring)

            # User with enabled-for-calendaring specified

            dn = "uid=odtestamanda,cn=users,dc=example,dc=com"
            guid = '9DC04A70-E6DD-11DF-9492-0800200C9A66'
            attrs = {
                'uid': ['odtestamanda'],
                'apple-generateduid': [guid],
                'enable-calendar': ["yes"],
                'sn': ['Test'],
                'mail': ['odtestamanda@example.com', 'alternate@example.com'],
                'givenName': ['Amanda'],
                'cn': ['Amanda Test']
            }

            record = self.service._ldapResultToRecord(dn, attrs,
                self.service.recordType_users)
            self.assertTrue(record.enabledForCalendaring)

            # User with "podding" info

            dn = "uid=odtestamanda,cn=users,dc=example,dc=com"
            guid = '9DC04A70-E6DD-11DF-9492-0800200C9A66'
            attrs = {
                'uid': ['odtestamanda'],
                'apple-generateduid': [guid],
                'cn': ['Amanda Test'],
                'server-id' : ["test-server-id"],
                'partition-id' : ["test-partition-id"],
            }

            record = self.service._ldapResultToRecord(dn, attrs,
                self.service.recordType_users)
            self.assertEquals(record.serverID, "test-server-id")
            self.assertEquals(record.partitionID, "test-partition-id")

            # User missing guidAttr

            dn = "uid=odtestamanda,cn=users,dc=example,dc=com"
            attrs = {
                'uid': ['odtestamanda'],
                'cn': ['Amanda Test'],
            }

            self.assertRaises(MissingGuidException,
                self.service._ldapResultToRecord, dn, attrs,
                self.service.recordType_users)

            # Group with direct user members and nested group

            dn = "cn=odtestgrouptop,cn=groups,dc=example,dc=com"
            guid = '6C6CD280-E6E3-11DF-9492-0800200C9A66'
            attrs = {
                'apple-generateduid': [guid],
                'uniqueMember':
                    [
                        '9DC04A70-E6DD-11DF-9492-0800200C9A66',
                        '9DC04A71-E6DD-11DF-9492-0800200C9A66',
                        '6C6CD282-E6E3-11DF-9492-0800200C9A66'
                    ],
                'cn': ['odtestgrouptop']
            }
            record = self.service._ldapResultToRecord(dn, attrs,
                self.service.recordType_groups)
            self.assertEquals(record.guid, guid)
            self.assertEquals(record.memberGUIDs(),
                set(['6C6CD282-E6E3-11DF-9492-0800200C9A66',
                     '9DC04A70-E6DD-11DF-9492-0800200C9A66',
                     '9DC04A71-E6DD-11DF-9492-0800200C9A66'])
            )

            # Resource with delegates and autoSchedule = True

            dn = "cn=odtestresource,cn=resources,dc=example,dc=com"
            guid = 'D3094652-344B-4633-8DB8-09639FA00FB6'
            attrs = {
                'apple-generateduid': [guid],
                'cn': ['odtestresource'],
                'apple-resource-info': ["""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
<key>com.apple.WhitePagesFramework</key>
<dict>
 <key>AutoAcceptsInvitation</key>
<true/>
<key>CalendaringDelegate</key>
<string>6C6CD280-E6E3-11DF-9492-0800200C9A66</string>
<key>ReadOnlyCalendaringDelegate</key>
<string>6AA1AE12-592F-4190-A069-547CD83C47C0</string>
</dict>
</dict>
</plist>"""]
            }
            record = self.service._ldapResultToRecord(dn, attrs,
                self.service.recordType_resources)
            self.assertEquals(record.guid, guid)
            self.assertEquals(record.externalProxies(),
                set(['6C6CD280-E6E3-11DF-9492-0800200C9A66']))
            self.assertEquals(record.externalReadOnlyProxies(),
                set(['6AA1AE12-592F-4190-A069-547CD83C47C0']))
            self.assertTrue(record.autoSchedule)

            # Resource with no delegates and autoSchedule = False

            dn = "cn=odtestresource,cn=resources,dc=example,dc=com"
            guid = 'D3094652-344B-4633-8DB8-09639FA00FB6'
            attrs = {
                'apple-generateduid': [guid],
                'cn': ['odtestresource'],
                'apple-resource-info': ["""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
<key>com.apple.WhitePagesFramework</key>
<dict>
 <key>AutoAcceptsInvitation</key>
<false/>
</dict>
</dict>
</plist>"""]
            }
            record = self.service._ldapResultToRecord(dn, attrs,
                self.service.recordType_resources)
            self.assertEquals(record.guid, guid)
            self.assertEquals(record.externalProxies(),
                set())
            self.assertEquals(record.externalReadOnlyProxies(),
                set())
            self.assertFalse(record.autoSchedule)


            # Now switch off the resourceInfoAttr and switch to individual
            # attributes...
            self.service.resourceSchema = {
                "resourceInfoAttr" : "",
                "autoScheduleAttr" : "auto-schedule",
                "autoScheduleEnabledValue" : "yes",
                "proxyAttr" : "proxy",
                "readOnlyProxyAttr" : "read-only-proxy",
            }

            # Resource with delegates and autoSchedule = True

            dn = "cn=odtestresource,cn=resources,dc=example,dc=com"
            guid = 'D3094652-344B-4633-8DB8-09639FA00FB6'
            attrs = {
                'apple-generateduid': [guid],
                'cn': ['odtestresource'],
                'auto-schedule' : ['yes'],
                'proxy' : ['6C6CD280-E6E3-11DF-9492-0800200C9A66'],
                'read-only-proxy' : ['6AA1AE12-592F-4190-A069-547CD83C47C0'],
            }
            record = self.service._ldapResultToRecord(dn, attrs,
                self.service.recordType_resources)
            self.assertEquals(record.guid, guid)
            self.assertEquals(record.externalProxies(),
                set(['6C6CD280-E6E3-11DF-9492-0800200C9A66']))
            self.assertEquals(record.externalReadOnlyProxies(),
                set(['6AA1AE12-592F-4190-A069-547CD83C47C0']))
            self.assertTrue(record.autoSchedule)

        def test_listRecords(self):
            """
            listRecords makes an LDAP query (with fake results in this test)
            and turns the results into records
            """

            self.service.ldap.addTestResults([
                (
                    "uid=odtestamanda,cn=users,dc=example,dc=com",
                    {
                        'uid': ['odtestamanda'],
                        'apple-generateduid': ['9DC04A70-E6DD-11DF-9492-0800200C9A66'],
                        'sn': ['Test'],
                        'mail': ['odtestamanda@example.com', 'alternate@example.com'],
                        'givenName': ['Amanda'],
                        'cn': ['Amanda Test']
                    }
                ),
                (
                    "uid=odtestbetty,cn=users,dc=example,dc=com",
                    {
                        'uid': ['odtestbetty'],
                        'apple-generateduid': ['93A8F5C5-49D8-4641-840F-CD1903B0394C'],
                        'sn': ['Test'],
                        'mail': ['odtestbetty@example.com'],
                        'givenName': ['Betty'],
                        'cn': ['Betty Test']
                    }
                ),
                (
                    "uid=odtestcarlene,cn=users,dc=example,dc=com",
                    {
                        'uid': ['odtestcarlene'],
                        # Note: no guid here, to test this record is skipped
                        'sn': ['Test'],
                        'mail': ['odtestcarlene@example.com'],
                        'givenName': ['Carlene'],
                        'cn': ['Carlene Test']
                    }
                ),
            ])
            records = self.service.listRecords(self.service.recordType_users)
            self.assertEquals(len(records), 2)
            self.assertEquals(
                set([r.firstName for r in records]),
                set(["Amanda", "Betty"]) # Carlene is skipped because no guid in LDAP
            )

        @inlineCallbacks
        def test_groupMembershipAliases(self):
            """
            Exercise a directory enviornment where group membership does not refer
            to guids but instead uses LDAP DNs.  This example uses the LDAP attribute
            "uniqueMember" to specify members of a group.  The value of this attribute
            is each members' DN.  Even though the proxy database deals strictly in
            guids, updateCache( ) is smart enough to map between guids and this
            attribute which is referred to in the code as record.cachedGroupsAlias().
            """

            # Set up proxydb and preload it from xml
            calendaruserproxy.ProxyDBService = calendaruserproxy.ProxySqliteDB("proxies.sqlite")
            yield XMLCalendarUserProxyLoader(proxiesFile.path).updateProxyDB()

            # Set up the GroupMembershipCache
            cache = GroupMembershipCache("ProxyDB", expireSeconds=60)
            self.service.groupMembershipCache = cache
            updater = GroupMembershipCacheUpdater(calendaruserproxy.ProxyDBService,
                self.service, 30, 15, cache=cache, useExternalProxies=False)

            # Fake LDAP results for the getGroups() call performed within
            # updateCache().  Also include recursive groups to make sure we
            # handle that situation.
            self.service.ldap.addTestResults([
                (
                    "cn=recursive1_coasts,cn=groups,dc=example,dc=com",
                    {
                        'cn': ['recursive1_coasts'],
                        'apple-generateduid': ['recursive1_coasts'],
                        'uniqueMember': [
                            'cn=recursive2_coasts,cn=groups,dc=example,dc=com',
                            'uid=wsanchez,cn=users,dc=example,dc=com',
                        ],
                    }
                ),
                (
                    "cn=recursive2_coasts,cn=groups,dc=example,dc=com",
                    {
                        'cn': ['recursive2_coasts'],
                        'apple-generateduid': ['recursive2_coasts'],
                        'uniqueMember': [
                            'cn=recursive1_coasts,cn=groups,dc=example,dc=com',
                            'uid=cdaboo,cn=users,dc=example,dc=com',
                        ],
                    }
                ),
                (
                    'cn=both_coasts,cn=groups,dc=example,dc=com',
                    {
                        'cn': ['both_coasts'],
                        'apple-generateduid': ['both_coasts'],
                        'uniqueMember': [
                            'cn=right_coast,cn=groups,dc=example,dc=com',
                            'cn=left_coast,cn=groups,dc=example,dc=com',
                        ],
                    }
                ),
                (
                    'cn=right_coast,cn=groups,dc=example,dc=com',
                    {
                        'cn': ['right_coast'],
                        'apple-generateduid': ['right_coast'],
                        'uniqueMember': [
                            'uid=cdaboo,cn=users,dc=example,dc=com',
                        ],
                    }
                ),
                (
                    'cn=left_coast,cn=groups,dc=example,dc=com',
                    {
                        'cn': ['left_coast'],
                        'apple-generateduid': ['left_coast'],
                        'uniqueMember': [
                            'uid=wsanchez,cn=users,dc=example,dc=com',
                            'uid=lecroy,cn=users,dc=example,dc=com',
                            'uid=dreid,cn=users,dc=example,dc=com',
                        ],
                    }
                ),
            ])
            self.service.ldap.addTestResults([
                (
                    "cn=recursive2_coasts,cn=groups,dc=example,dc=com",
                    {
                        'cn': ['recursive2_coasts'],
                        'apple-generateduid': ['recursive2_coasts'],
                        'uniqueMember': [
                            'cn=recursive1_coasts,cn=groups,dc=example,dc=com',
                            'uid=cdaboo,cn=users,dc=example,dc=com',
                        ],
                    }
                ),
            ])
            self.service.ldap.addTestResults([
                (
                    'cn=left_coast,cn=groups,dc=example,dc=com',
                    {
                        'cn': ['left_coast'],
                        'apple-generateduid': ['left_coast'],
                        'uniqueMember': [
                            'uid=wsanchez,cn=users,dc=example,dc=com',
                            'uid=lecroy,cn=users,dc=example,dc=com',
                            'uid=dreid,cn=users,dc=example,dc=com',
                        ],
                    }
                ),
            ])
            self.service.ldap.addTestResults([
                (
                    'cn=right_coast,cn=groups,dc=example,dc=com',
                    {
                        'cn': ['right_coast'],
                        'apple-generateduid': ['right_coast'],
                        'uniqueMember': [
                            'uid=cdaboo,cn=users,dc=example,dc=com',
                        ],
                    }
                ),
            ])

            self.assertEquals((False, 8), (yield updater.updateCache()))

            users = self.service.recordType_users

            for shortName, groups in [
                ("cdaboo", set(["both_coasts", "recursive1_coasts", "recursive2_coasts"])),
                ("wsanchez", set(["both_coasts", "left_coast", "recursive1_coasts", "recursive2_coasts"])),
            ]:

                # Fake LDAP results for the record lookup
                self.service.ldap.addTestResults([
                    (
                        "uid=%s,cn=users,dc=example,dc=com" % (shortName,),
                        {
                            'uid': [shortName],
                            'cn': [shortName],
                            'apple-generateduid': [shortName],
                        }
                    ),
                ])

                record = self.service.recordWithShortName(users, shortName)
                self.assertEquals(groups, (yield record.cachedGroups()))


        def test_splitIntoBatches(self):
            # Data is perfect multiple of size
            results = list(splitIntoBatches(set(range(12)), 4))
            self.assertEquals(results,
                [set([0, 1, 2, 3]), set([4, 5, 6, 7]), set([8, 9, 10, 11])])

            # Some left overs
            results = list(splitIntoBatches(set(range(12)), 5))
            self.assertEquals(results,
                [set([0, 1, 2, 3, 4]), set([8, 9, 5, 6, 7]), set([10, 11])])

            # Empty
            results = list(splitIntoBatches(set([]), 5)) # empty data
            self.assertEquals(results, [set([])])
