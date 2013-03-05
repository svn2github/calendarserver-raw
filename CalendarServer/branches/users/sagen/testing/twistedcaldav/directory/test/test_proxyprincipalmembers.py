##
# Copyright (c) 2005-2013 Apple Inc. All rights reserved.
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

from twisted.internet.defer import DeferredList, inlineCallbacks, succeed
from txdav.xml import element as davxml

from twistedcaldav.directory.directory import DirectoryService
from twistedcaldav.test.util import xmlFile, augmentsFile, proxiesFile
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource, \
    DirectoryPrincipalResource
from twistedcaldav.directory.xmlfile import XMLDirectoryService

import twistedcaldav.test.util
from twistedcaldav.directory import augment, calendaruserproxy
from twistedcaldav.directory.calendaruserproxyloader import XMLCalendarUserProxyLoader


class ProxyPrincipals (twistedcaldav.test.util.TestCase):
    """
    Directory service provisioned principals.
    """

    @inlineCallbacks
    def setUp(self):
        super(ProxyPrincipals, self).setUp()

        self.directoryFixture.addDirectoryService(XMLDirectoryService(
            {
                'xmlFile' : xmlFile,
                'augmentService' :
                    augment.AugmentXMLDB(xmlFiles=(augmentsFile.path,)),
            }
        ))
        calendaruserproxy.ProxyDBService = calendaruserproxy.ProxySqliteDB("proxies.sqlite")

        # Set up a principals hierarchy for each service we're testing with
        self.principalRootResources = {}
        name = self.directoryService.__class__.__name__
        url = "/" + name + "/"

        provisioningResource = DirectoryPrincipalProvisioningResource(url, self.directoryService)

        self.site.resource.putChild(name, provisioningResource)

        self.principalRootResources[self.directoryService.__class__.__name__] = provisioningResource

        yield XMLCalendarUserProxyLoader(proxiesFile.path).updateProxyDB()


    def tearDown(self):
        """ Empty the proxy db between tests """
        return calendaruserproxy.ProxyDBService.clean() #@UndefinedVariable


    def _getPrincipalByShortName(self, type, name):
        provisioningResource = self.principalRootResources[self.directoryService.__class__.__name__]
        return provisioningResource.principalForShortName(type, name)


    def _groupMembersTest(self, recordType, recordName, subPrincipalName, expectedMembers):
        def gotMembers(members):
            memberNames = set([p.displayName() for p in members])
            self.assertEquals(memberNames, set(expectedMembers))

        principal = self._getPrincipalByShortName(recordType, recordName)
        if subPrincipalName is not None:
            principal = principal.getChild(subPrincipalName)

        d = principal.expandedGroupMembers()
        d.addCallback(gotMembers)
        return d


    def _groupMembershipsTest(self, recordType, recordName, subPrincipalName, expectedMemberships):
        def gotMemberships(memberships):
            uids = set([p.principalUID() for p in memberships])
            self.assertEquals(uids, set(expectedMemberships))

        principal = self._getPrincipalByShortName(recordType, recordName)
        if subPrincipalName is not None:
            principal = principal.getChild(subPrincipalName)

        d = principal.groupMemberships()
        d.addCallback(gotMemberships)
        return d


    @inlineCallbacks
    def _addProxy(self, principal, subPrincipalName, proxyPrincipal):

        if isinstance(principal, tuple):
            principal = self._getPrincipalByShortName(principal[0], principal[1])
        principal = principal.getChild(subPrincipalName)
        members = (yield principal.groupMembers())

        if isinstance(proxyPrincipal, tuple):
            proxyPrincipal = self._getPrincipalByShortName(proxyPrincipal[0], proxyPrincipal[1])
        members.add(proxyPrincipal)

        yield principal.setGroupMemberSetPrincipals(members)


    @inlineCallbacks
    def _removeProxy(self, recordType, recordName, subPrincipalName, proxyRecordType, proxyRecordName):

        principal = self._getPrincipalByShortName(recordType, recordName)
        principal = principal.getChild(subPrincipalName)
        members = (yield principal.groupMembers())

        proxyPrincipal = self._getPrincipalByShortName(proxyRecordType, proxyRecordName)
        for p in members:
            if p.principalUID() == proxyPrincipal.principalUID():
                members.remove(p)
                break

        yield principal.setGroupMemberSetPrincipals(members)


    @inlineCallbacks
    def _clearProxy(self, principal, subPrincipalName):

        if isinstance(principal, tuple):
            principal = self._getPrincipalByShortName(principal[0], principal[1])
        principal = principal.getChild(subPrincipalName)
        yield principal.setGroupMemberSetPrincipals(set())


    @inlineCallbacks
    def _proxyForTest(self, recordType, recordName, expectedProxies, read_write):
        principal = self._getPrincipalByShortName(recordType, recordName)
        proxies = (yield principal.proxyFor(read_write))
        proxies = sorted([_principal.displayName() for _principal in proxies])
        self.assertEquals(proxies, sorted(expectedProxies))


    @inlineCallbacks
    def test_multipleProxyAssignmentsAtOnce(self):
        yield self._proxyForTest(
            DirectoryService.recordType_users, "userb",
            ('a',),
            True
        )
        yield self._proxyForTest(
            DirectoryService.recordType_users, "userc",
            ('a',),
            True
        )


    def test_groupMembersRegular(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        return self._groupMembersTest(
            DirectoryService.recordType_groups, "both_coasts", None,
            ("Chris Lecroy", "David Reid", "Wilfredo Sanchez", "West Coast", "East Coast", "Cyrus Daboo",),
        )


    def test_groupMembersRecursive(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        return self._groupMembersTest(
            DirectoryService.recordType_groups, "recursive1_coasts", None,
            ("Wilfredo Sanchez", "Recursive2 Coasts", "Cyrus Daboo",),
        )


    def test_groupMembersProxySingleUser(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        return self._groupMembersTest(
            DirectoryService.recordType_locations, "gemini", "calendar-proxy-write",
            ("Wilfredo Sanchez",),
        )


    def test_groupMembersProxySingleGroup(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        return self._groupMembersTest(
            DirectoryService.recordType_locations, "mercury", "calendar-proxy-write",
            ("Chris Lecroy", "David Reid", "Wilfredo Sanchez", "West Coast",),
        )


    def test_groupMembersProxySingleGroupWithNestedGroups(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        return self._groupMembersTest(
            DirectoryService.recordType_locations, "apollo", "calendar-proxy-write",
            ("Chris Lecroy", "David Reid", "Wilfredo Sanchez", "West Coast", "East Coast", "Cyrus Daboo", "Both Coasts",),
        )


    def test_groupMembersProxySingleGroupWithNestedRecursiveGroups(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        return self._groupMembersTest(
            DirectoryService.recordType_locations, "orion", "calendar-proxy-write",
            ("Wilfredo Sanchez", "Cyrus Daboo", "Recursive1 Coasts", "Recursive2 Coasts",),
        )


    def test_groupMembersProxySingleGroupWithNonCalendarGroup(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        ds = []

        ds.append(self._groupMembersTest(
            DirectoryService.recordType_resources, "non_calendar_proxy", "calendar-proxy-write",
            ("Chris Lecroy", "Cyrus Daboo", "Non-calendar group"),
        ))

        ds.append(self._groupMembershipsTest(
            DirectoryService.recordType_groups, "non_calendar_group", None,
            ("non_calendar_proxy#calendar-proxy-write",),
        ))

        return DeferredList(ds)


    def test_groupMembersProxyMissingUser(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        proxy = self._getPrincipalByShortName(DirectoryService.recordType_users, "cdaboo")
        proxyGroup = proxy.getChild("calendar-proxy-write")

        def gotMembers(members):
            members.add("12345")
            return proxyGroup._index().setGroupMembers("%s#calendar-proxy-write" % (proxy.principalUID(),), members)

        def check(_):
            return self._groupMembersTest(
                DirectoryService.recordType_users, "cdaboo", "calendar-proxy-write",
                (),
            )

        # Setup the fake entry in the DB
        d = proxyGroup._index().getMembers("%s#calendar-proxy-write" % (proxy.principalUID(),))
        d.addCallback(gotMembers)
        d.addCallback(check)
        return d


    def test_groupMembershipsMissingUser(self):
        """
        DirectoryPrincipalResource.expandedGroupMembers()
        """
        # Setup the fake entry in the DB
        fake_uid = "12345"
        proxy = self._getPrincipalByShortName(DirectoryService.recordType_users, "cdaboo")
        proxyGroup = proxy.getChild("calendar-proxy-write")

        def gotMembers(members):
            members.add("%s#calendar-proxy-write" % (proxy.principalUID(),))
            return proxyGroup._index().setGroupMembers("%s#calendar-proxy-write" % (fake_uid,), members)

        def check(_):
            return self._groupMembershipsTest(
                DirectoryService.recordType_users, "cdaboo", "calendar-proxy-write",
                (),
            )

        d = proxyGroup._index().getMembers("%s#calendar-proxy-write" % (fake_uid,))
        d.addCallback(gotMembers)
        d.addCallback(check)
        return d


    @inlineCallbacks
    def test_setGroupMemberSet(self):
        class StubMemberDB(object):
            def __init__(self):
                self.members = set()

            def setGroupMembers(self, uid, members):
                self.members = members
                return succeed(None)

            def getMembers(self, uid):
                return succeed(self.members)

        user = self._getPrincipalByShortName(self.directoryService.recordType_users,
                                           "cdaboo")

        proxyGroup = user.getChild("calendar-proxy-write")

        memberdb = StubMemberDB()

        proxyGroup._index = (lambda: memberdb)

        new_members = davxml.GroupMemberSet(
            davxml.HRef.fromString(
                "/XMLDirectoryService/__uids__/8B4288F6-CC82-491D-8EF9-642EF4F3E7D0/"),
            davxml.HRef.fromString(
                "/XMLDirectoryService/__uids__/5FF60DAD-0BDE-4508-8C77-15F0CA5C8DD1/"))

        yield proxyGroup.setGroupMemberSet(new_members, None)

        self.assertEquals(
            set([str(p) for p in memberdb.members]),
            set(["5FF60DAD-0BDE-4508-8C77-15F0CA5C8DD1",
                 "8B4288F6-CC82-491D-8EF9-642EF4F3E7D0"]))


    @inlineCallbacks
    def test_setGroupMemberSetNotifiesPrincipalCaches(self):
        class StubCacheNotifier(object):
            changedCount = 0
            def changed(self):
                self.changedCount += 1
                return succeed(None)

        user = self._getPrincipalByShortName(self.directoryService.recordType_users, "cdaboo")

        proxyGroup = user.getChild("calendar-proxy-write")

        notifier = StubCacheNotifier()

        oldCacheNotifier = DirectoryPrincipalResource.cacheNotifierFactory

        try:
            DirectoryPrincipalResource.cacheNotifierFactory = (lambda _1, _2, **kwargs: notifier)

            self.assertEquals(notifier.changedCount, 0)

            yield proxyGroup.setGroupMemberSet(
                davxml.GroupMemberSet(
                    davxml.HRef.fromString(
                        "/XMLDirectoryService/__uids__/5FF60DAD-0BDE-4508-8C77-15F0CA5C8DD1/")),
                None)

            self.assertEquals(notifier.changedCount, 1)
        finally:
            DirectoryPrincipalResource.cacheNotifierFactory = oldCacheNotifier


    def test_proxyFor(self):

        return self._proxyForTest(
            DirectoryService.recordType_users, "wsanchez",
            ("Mercury Seven", "Gemini Twelve", "Apollo Eleven", "Orion",),
            True
        )


    @inlineCallbacks
    def test_proxyForDuplicates(self):

        yield self._addProxy(
            (DirectoryService.recordType_locations, "gemini",),
            "calendar-proxy-write",
            (DirectoryService.recordType_groups, "grunts",),
        )

        yield self._proxyForTest(
            DirectoryService.recordType_users, "wsanchez",
            ("Mercury Seven", "Gemini Twelve", "Apollo Eleven", "Orion",),
            True
        )


    def test_readOnlyProxyFor(self):

        return self._proxyForTest(
            DirectoryService.recordType_users, "wsanchez",
            ("Non-calendar proxy",),
            False
        )


    @inlineCallbacks
    def test_UserProxy(self):

        for proxyType in ("calendar-proxy-read", "calendar-proxy-write"):

            yield self._addProxy(
                (DirectoryService.recordType_users, "wsanchez",),
                proxyType,
                (DirectoryService.recordType_users, "cdaboo",),
            )

            yield self._groupMembersTest(
                DirectoryService.recordType_users, "wsanchez",
                proxyType,
                ("Cyrus Daboo",),
            )

            yield self._addProxy(
                (DirectoryService.recordType_users, "wsanchez",),
                proxyType,
                (DirectoryService.recordType_users, "lecroy",),
            )

            yield self._groupMembersTest(
                DirectoryService.recordType_users, "wsanchez",
                proxyType,
                ("Cyrus Daboo", "Chris Lecroy",),
            )

            yield self._removeProxy(
                DirectoryService.recordType_users, "wsanchez",
                proxyType,
                DirectoryService.recordType_users, "cdaboo",
            )

            yield self._groupMembersTest(
                DirectoryService.recordType_users, "wsanchez",
                proxyType,
                ("Chris Lecroy",),
            )


    @inlineCallbacks
    def test_NonAsciiProxy(self):
        """
        Ensure that principalURLs with non-ascii don't cause problems
        within CalendarUserProxyPrincipalResource
        """

        recordType = DirectoryService.recordType_users
        proxyType = "calendar-proxy-read"

        record = self.directoryService.recordWithGUID("320B73A1-46E2-4180-9563-782DFDBE1F63")
        provisioningResource = self.principalRootResources[self.directoryService.__class__.__name__]
        principal = provisioningResource.principalForRecord(record)
        proxyPrincipal = provisioningResource.principalForShortName(recordType,
            "wsanchez")

        yield self._addProxy(principal, proxyType, proxyPrincipal)
        memberships = yield proxyPrincipal._calendar_user_proxy_index().getMemberships(proxyPrincipal.principalUID())
        for uid in memberships:
            provisioningResource.principalForUID(uid)


    @inlineCallbacks
    def test_getAllMembers(self):
        """
        getAllMembers( ) returns the unique set of guids that have been
        delegated-to directly
        """
        self.assertEquals(
            set((yield calendaruserproxy.ProxyDBService.getAllMembers())), #@UndefinedVariable
            set([u'6423F94A-6B76-4A3A-815B-D52CFD77935D', u'8A985493-EE2C-4665-94CF-4DFEA3A89500', u'9FF60DAD-0BDE-4508-8C77-15F0CA5C8DD2', u'both_coasts', u'left_coast', u'non_calendar_group', u'recursive1_coasts', u'recursive2_coasts', u'EC465590-E9E9-4746-ACE8-6C756A49FE4D'])
        )


    @inlineCallbacks
    def test_hideDisabledDelegates(self):
        """
        Delegates who are not enabledForLogin are "hidden" from the delegate lists
        """

        record = self.directoryService.recordWithGUID("EC465590-E9E9-4746-ACE8-6C756A49FE4D")

        record.enabledForLogin = True
        yield self._groupMembersTest(
            DirectoryService.recordType_users, "delegator", "calendar-proxy-write",
            ("Occasional Delegate",),
        )

        # Login disabled -- no longer shown as a delegate
        record.enabledForLogin = False
        yield self._groupMembersTest(
            DirectoryService.recordType_users, "delegator", "calendar-proxy-write",
            [],
        )

        # Login re-enabled -- once again a delegate (it wasn't not removed from proxydb)
        record.enabledForLogin = True
        yield self._groupMembersTest(
            DirectoryService.recordType_users, "delegator", "calendar-proxy-write",
            ("Occasional Delegate",),
        )
