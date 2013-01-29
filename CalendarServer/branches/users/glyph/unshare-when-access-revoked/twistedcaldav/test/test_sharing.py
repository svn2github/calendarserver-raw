##
# Copyright (c) 2010-2013 Apple Inc. All rights reserved.
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

from xml.etree.cElementTree import XML

from zope.interface import implements

from txdav.xml import element as davxml
from txdav.xml.parser import WebDAVDocument

from twext.web2 import responsecode
from twext.web2.iweb import IResource
from twext.web2.test.test_server import SimpleRequest

from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twistedcaldav import customxml
from twistedcaldav.config import config
from twistedcaldav.test.util import HomeTestCase, norequest
from twistedcaldav.sharing import SharedCollectionMixin, WikiDirectoryService

from twistedcaldav.resource import CalDAVResource

from txdav.common.datastore.test.util import buildStore, StubNotifierFactory
from txdav.caldav.icalendarstore import BIND_DIRECT
from twistedcaldav.directory.aggregate import AggregateDirectoryService
from twistedcaldav.directory.principal import DirectoryPrincipalProvisioningResource
from twistedcaldav.directory.calendar import DirectoryCalendarHomeProvisioningResource
from twistedcaldav.test.test_cache import StubResponseCacheResource


sharedOwnerType = davxml.ResourceType.sharedownercalendar #@UndefinedVariable
regularCalendarType = davxml.ResourceType.calendar #@UndefinedVariable



def normalize(x):
    """
    Normalize some XML by parsing it, collapsing whitespace, and
    pretty-printing.
    """
    return WebDAVDocument.fromString(x).toxml()



class StubCollection(object):

    def __init__(self):
        self._isShareeCollection = True
        self._shareePrincipal = StubUserPrincipal()


    def isCalendarCollection(self):
        return True



class StubShare(object):

    def direct(self):
        return True

    def url(self):
        return "/wikifoo"

    def uid(self):
        return "012345"

    def shareeUID(self):
        return StubUserPrincipal().record.guid



class TestCollection(SharedCollectionMixin, StubCollection):
    def principalForUID(self, uid):
        principal = StubUserPrincipal()
        return principal if principal.record.guid == uid else None



class StubRecord(object):
    def __init__(self, recordType, name, guid):
        self.recordType = recordType
        self.shortNames = [name]
        self.guid = guid



class StubUserPrincipal(object):
    def __init__(self):
        self.record = StubRecord(
            "users",
            "testuser",
            "4F364813-0415-45CB-9FD4-DBFEF7A0A8E0"
        )


    def principalURL(self):
        return "/principals/__uids__/%s/" % (self.record.guid,)



class StubWikiPrincipal(object):
    def __init__(self):
        self.record = StubRecord(
            WikiDirectoryService.recordType_wikis,
            "wikifoo",
            "foo"
        )

class StubWikiResource(object):
    implements(IResource)

    def locateChild(self, req, segments):
        pass


    def renderHTTP(self, req):
        pass


    def ownerPrincipal(self, req):
        return succeed(StubWikiPrincipal())



class FakeHome(object):
    def removeShareByUID(self, request, uid):
        pass



class FakeRecord(object):

    def __init__(self, name, cuaddr):
        self.fullName = name
        self.guid = name
        self.calendarUserAddresses = set((cuaddr,))



class FakePrincipal(object):

    def __init__(self, cuaddr, test):
        if cuaddr.startswith("mailto:"):
            name = cuaddr[7:].split('@')[0]
        elif cuaddr.startswith("urn:uuid:"):
            name = cuaddr[9:]
        else:
            name = cuaddr

        self.path = "/principals/__uids__/%s" % (name,)
        self.homepath = "/calendars/__uids__/%s" % (name,)
        self.displayname = name.upper()
        self.record = FakeRecord(name, cuaddr)
        self._test = test
        self._name = name


    @inlineCallbacks
    def calendarHome(self, request):
        a, seg = yield self._test.homeProvisioner.locateChild(request,
                                                              ["__uids__"])
        b, seg = yield a.locateChild(request, [self._name])
        if b is None:
            # XXX all tests except test_noWikiAccess currently rely on the
            # fake thing here.
            returnValue(FakeHome())
        returnValue(b)


    def principalURL(self):
        return self.path


    def principalUID(self):
        return self.record.guid


    def displayName(self):
        return self.displayname



class SharingTests(HomeTestCase):

    def configure(self):
        """
        Override configuration hook to turn on sharing.
        """
        super(SharingTests, self).configure()
        self.patch(config.Sharing, "Enabled", True)
        self.patch(config.Sharing.Calendars, "Enabled", True)


    @inlineCallbacks
    def setUp(self):
        self.calendarStore = yield buildStore(self, StubNotifierFactory())

        yield super(SharingTests, self).setUp()

        def patched(c):
            """
            The decorated method is patched on L{CalDAVResource} for the
            duration of the test.
            """
            self.patch(CalDAVResource, c.__name__, c)
            return c

        @patched
        def sendInviteNotification(resourceSelf, record, request):
            """
            For testing purposes, sending an invite notification succeeds
            without doing anything.
            """
            return succeed(True)

        @patched
        def removeInviteNotification(resourceSelf, record, request):
            """
            For testing purposes, removing an invite notification succeeds
            without doing anything.
            """
            return succeed(True)

        @patched
        def principalForCalendarUserAddress(resourceSelf, cuaddr):
            if "bogus" in cuaddr:
                return None
            else:
                return FakePrincipal(cuaddr, self)

        @patched
        def validUserIDForShare(resourceSelf, userid, request):
            """
            Temporary replacement for L{CalDAVResource.validUserIDForShare}
            that marks any principal without 'bogus' in its name.
            """
            result = principalForCalendarUserAddress(resourceSelf, userid)
            if result is None:
                return result
            return result.principalURL()

        @patched
        def principalForUID(resourceSelf, principalUID):
            return FakePrincipal("urn:uuid:" + principalUID, self)


    def createDataStore(self):
        return self.calendarStore


    @inlineCallbacks
    def _refreshRoot(self, request=None):
        if request is None:
            request = norequest()
        result = yield super(SharingTests, self)._refreshRoot(request)
        self.resource = (
            yield self.site.resource.locateChild(request, ["calendar"])
        )[0]
        returnValue(result)


    def _doPOST(self, body, resultcode=responsecode.OK):
        return self.simpleSend("POST", "/calendar/", body,
                               resultcode=resultcode)


    def _clearUIDElementValue(self, xml):

        for user in xml.children:
            for element in user.children:
                if type(element) == customxml.UID:
                    element.children[0].data = ""
        return xml


    @inlineCallbacks
    def test_upgradeToShare(self):

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, regularCalendarType)
        isShared = (yield self.resource.isShared(None))
        self.assertFalse(isShared)
        isShareeCollection = self.resource.isShareeCollection()
        self.assertFalse(isShareeCollection)

        self.resource.upgradeToShare()

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, sharedOwnerType)
        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)
        isShareeCollection = self.resource.isShareeCollection()
        self.assertFalse(isShareeCollection)


    @inlineCallbacks
    def test_downgradeFromShare(self):

        self.resource.upgradeToShare()

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, sharedOwnerType)
        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)
        isShareeCollection = self.resource.isShareeCollection()
        self.assertFalse(isShareeCollection)

        yield self.resource.downgradeFromShare(None)

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, regularCalendarType)
        isShared = (yield self.resource.isShared(None))
        self.assertFalse(isShared)
        isShareeCollection = self.resource.isShareeCollection()
        self.assertFalse(isShareeCollection)


    @inlineCallbacks
    def test_POSTaddInviteeAlreadyShared(self):

        self.resource.upgradeToShare()

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user02"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))

        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)
        isShareeCollection = self.resource.isShareeCollection()
        self.assertFalse(isShareeCollection)


    @inlineCallbacks
    def test_POSTaddInviteeNotAlreadyShared(self):

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
        <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
            <CS:set>
                <D:href>mailto:user02@example.com</D:href>
                <CS:summary>My Shared Calendar</CS:summary>
                <CS:read-write/>
            </CS:set>
        </CS:share>
        """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user02"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))

        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)
        isShareeCollection = (yield self.resource.isShareeCollection())
        self.assertFalse(isShareeCollection)


    @inlineCallbacks
    def test_POSTupdateInvitee(self):

        isShared = (yield self.resource.isShared(None))
        self.assertFalse(isShared)

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)

        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read/>
                </CS:set>
            </CS:share>
            """)

        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user02"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))


    @inlineCallbacks
    def test_POSTremoveInvitee(self):

        isShared = (yield self.resource.isShared(None))
        self.assertFalse(isShared)

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)

        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user02@example.com</D:href>
                </CS:remove>
            </CS:share>
            """)

        isShared = (yield self.resource.isShared(None))
        self.assertFalse(isShared)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, None)


    @inlineCallbacks
    def test_POSTaddMoreInvitees(self):

        self.resource.upgradeToShare()

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)
        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user03@example.com</D:href>
                    <CS:summary>Your Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
                <CS:set>
                    <D:href>mailto:user04@example.com</D:href>
                    <CS:summary>Your Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user02"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user03"),
                customxml.CommonName.fromString("USER03"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user04"),
                customxml.CommonName.fromString("USER04"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
        ))


    @inlineCallbacks
    def test_POSTaddRemoveInvitees(self):

        self.resource.upgradeToShare()

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
                <CS:set>
                    <D:href>mailto:user03@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)
        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user03@example.com</D:href>
                </CS:remove>
                <CS:set>
                    <D:href>mailto:user04@example.com</D:href>
                    <CS:summary>Your Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user02"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user04"),
                customxml.CommonName.fromString("USER04"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
        ))

    @inlineCallbacks
    def test_POSTaddRemoveSameInvitee(self):

        self.resource.upgradeToShare()

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
                <CS:set>
                    <D:href>mailto:user03@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)
        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user03@example.com</D:href>
                </CS:remove>
                <CS:set>
                    <D:href>mailto:user03@example.com</D:href>
                    <CS:summary>Your Shared Calendar</CS:summary>
                    <CS:read/>
                </CS:set>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user02"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user03"),
                customxml.CommonName.fromString("USER03"),
                customxml.InviteAccess(customxml.ReadAccess()),
                customxml.InviteStatusNoResponse(),
            ),
        ))


    @inlineCallbacks
    def test_POSTremoveNonInvitee(self):
        """
        Ensure that removing a sharee that is not currently invited
        doesn't return an error.  The server will just pretend it
        removed the sharee.
        """

        self.resource.upgradeToShare()

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user02@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
                <CS:set>
                    <D:href>mailto:user03@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)
        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user03@example.com</D:href>
                </CS:remove>
            </CS:share>
            """)
        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user02@example.com</D:href>
                </CS:remove>
                <CS:remove>
                    <D:href>mailto:user03@example.com</D:href>
                </CS:remove>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, None)


    @inlineCallbacks
    def test_POSTaddInvalidInvitee(self):
        self.resource.upgradeToShare()

        data = (yield self._doPOST(
            """<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:bogus@example.net</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """,
            responsecode.MULTI_STATUS
        ))
        self.assertXMLEquals(
            data,
            """<?xml version='1.0' encoding='UTF-8'?>
            <multistatus xmlns='DAV:'>
              <response>
                <href>mailto:bogus@example.net</href>
                <status>HTTP/1.1 403 Forbidden</status>
              </response>
            </multistatus>"""
        )
        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, None)


    def assertXMLEquals(self, a, b):
        """
        Assert two strings are equivalent as XML.
        """
        self.assertEquals(normalize(a), normalize(b))


    @inlineCallbacks
    def test_POSTremoveInvalidInvitee(self):

        self.resource.upgradeToShare()

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:set>
                    <D:href>mailto:user01@example.com</D:href>
                    <CS:summary>My Shared Calendar</CS:summary>
                    <CS:read-write/>
                </CS:set>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user01"),
                customxml.CommonName.fromString("USER01"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))

        self.resource.validUserIDForShare = lambda userid, request: None
        self.resource.principalForCalendarUserAddress = lambda cuaddr: None
        self.resource.principalForUID = lambda principalUID: None

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user01"),
                customxml.CommonName.fromString("user01"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusInvalid(),
            )
        ))

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user01@example.com</D:href>
                </CS:remove>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, None)


    @inlineCallbacks
    def test_wikiACL(self):
        """
        Ensure shareeAccessControlList( ) honors the access granted by the wiki
        to the sharee, so that delegates of the sharee get the same level of
        access.
        """

        access = "read"

        def stubWikiAccessMethod(userID, wikiID):
            return access

        collection = TestCollection()
        collection._share = StubShare()
        self.site.resource.putChild("wikifoo", StubWikiResource())
        request = SimpleRequest(self.site, "GET", "/wikifoo")

        # Simulate the wiki server granting Read access
        acl = (yield collection.shareeAccessControlList(request,
            wikiAccessMethod=stubWikiAccessMethod))
        self.assertFalse("<write/>" in acl.toxml())

        # Simulate the wiki server granting Read-Write access
        access = "write"
        acl = (yield collection.shareeAccessControlList(request,
            wikiAccessMethod=stubWikiAccessMethod))
        self.assertTrue("<write/>" in acl.toxml())


    @inlineCallbacks
    def test_noWikiAccess(self):
        """
        If L{SharedCollectionMixin.shareeAccessControlList} detects missing
        access controls for a directly shared collection, it will automatically
        un-share that collection.
        """
        wcreate = self.calendarStore.newTransaction("create wiki")
        yield wcreate.calendarHomeWithUID("wiki-testing", create=True)
        yield wcreate.commit()

        # Since this is a HomeTestCase, self.site.resource refers to a _calendar
        # home_, not the actual site root.  Rummage around in the bag of state
        # there looking for the relevant stuff to test with.

        testwiki = WikiDirectoryService()
        testwiki.realmName = "Test"
        self.directoryService = AggregateDirectoryService(
            [testwiki, self.directoryService], None
        )
        self.homeProvisioner = DirectoryCalendarHomeProvisioningResource(
            self.directoryService, "/calendars/", self.calendarStore
        )
        DirectoryPrincipalProvisioningResource(
            "/principals/", self.directoryService
        )

        origRefreshRoot = self._refreshRoot
        @inlineCallbacks
        def _newRefreshRoot(request=None):
            yield origRefreshRoot(request)
            self.site.resource.responseCache = StubResponseCacheResource()
            self.site.resource.putChild("calendars", self.homeProvisioner)
            # if request is not None:
            #     request._rememberResource(self.site.resource, "/")
        self._refreshRoot = _newRefreshRoot

        @inlineCallbacks
        def getSharedName():
            """
            Share a resource from a wiki; get its name.  Put this in its own
            function so it doesn't leak any soon-to-expire variables to the
            outer test.
            """
            txn = self.site.resource._associatedTransaction
            sharee = self.site.resource._newStoreHome

            sharer = yield txn.calendarHomeWithUID("wiki-testing")
            cal = yield sharer.calendarWithName("calendar")
            sharedName = yield cal.shareWith(sharee, BIND_DIRECT)

            yield self._refreshRoot()
            returnValue(sharedName)

        sharedName = yield getSharedName()
        access = "write"
        def stubWikiAccessMethod(userID, wikiID):
            return access
        from twistedcaldav import sharing
        self.patch(sharing, "getWikiAccess", stubWikiAccessMethod)
        @inlineCallbacks
        def listChildrenViaPropfind():
            data = yield self.simpleSend(
                "PROPFIND", "/", resultcode=responsecode.MULTI_STATUS,
                headers=[('Depth', '1')]
            )
            tree = XML(data)
            seq = [e.text for e in tree.findall("{DAV:}response/{DAV:}href")]
            shortest = min(seq, key=len)
            seq.remove(shortest)
            filtered = [elem[len(shortest):].rstrip("/") for elem in seq]
            returnValue(filtered)
        childNames = yield listChildrenViaPropfind()
        self.assertIn(sharedName, childNames)
        access = "no-access"
        childNames = yield listChildrenViaPropfind()
        self.assertNotIn(sharedName, childNames)




