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

from twext.web2 import responsecode
from twext.web2.dav.util import allDataFromStream
from twext.web2.http_headers import MimeType
from twext.web2.iweb import IResponse

from twisted.internet.defer import inlineCallbacks, returnValue, succeed

from twistedcaldav import customxml
from twistedcaldav import sharing
from twistedcaldav.config import config
from twistedcaldav.resource import CalDAVResource
from twistedcaldav.sharing import WikiDirectoryService
from twistedcaldav.test.test_cache import StubResponseCacheResource
from twistedcaldav.test.util import norequest, StoreTestCase, SimpleStoreRequest

from txdav.caldav.icalendarstore import BIND_DIRECT
from txdav.xml import element as davxml
from txdav.xml.parser import WebDAVDocument

from xml.etree.cElementTree import XML


sharedOwnerType = davxml.ResourceType.sharedownercalendar #@UndefinedVariable
regularCalendarType = davxml.ResourceType.calendar #@UndefinedVariable



def normalize(x):
    """
    Normalize some XML by parsing it, collapsing whitespace, and
    pretty-printing.
    """
    return WebDAVDocument.fromString(x).toxml()



class FakeHome(object):
    def removeShareByUID(self, request, uid):
        pass



class FakeRecord(object):

    def __init__(self, name, cuaddr):
        self.fullName = name
        self.guid = name
        self.calendarUserAddresses = set((cuaddr,))
        if name.startswith("wiki-"):
            recordType = WikiDirectoryService.recordType_wikis
        else:
            recordType = None
        self.recordType = recordType
        self.shortNames = [name]



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
        a, _ignore_seg = yield self._test.calendarCollection.locateChild(request, ["__uids__"])
        b, _ignore_seg = yield a.locateChild(request, [self._name])
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



class SharingTests(StoreTestCase):

    def configure(self):
        """
        Override configuration hook to turn on sharing.
        """
        super(SharingTests, self).configure()
        self.patch(config.Sharing, "Enabled", True)
        self.patch(config.Sharing.Calendars, "Enabled", True)


    @inlineCallbacks
    def setUp(self):
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

        self.resource = yield self._getResource()


    @inlineCallbacks
    def _refreshRoot(self, request=None):
        if request is None:
            request = norequest()
        result = yield super(SharingTests, self)._refreshRoot(request)
        self.resource = (
            yield self.site.resource.locateChild(request, ["calendar"])
        )[0]
        self.site.resource.responseCache = StubResponseCacheResource()
        self.site.resource.putChild("calendars", self.homeProvisioner)
        returnValue(result)


    @inlineCallbacks
    def _doPOST(self, body, resultcode=responsecode.OK):
        request = SimpleStoreRequest(self, "POST", "/calendars/__uids__/user01/calendar/", content=body, authid="user01")
        request.headers.setHeader("content-type", MimeType("text", "xml"))
        response = yield self.send(request)
        response = IResponse(response)
        self.assertEqual(response.code, resultcode)

        # Reload resource
        self.resource = yield self._getResource()

        if response.stream:
            data = yield allDataFromStream(response.stream)
            returnValue(data)
        else:
            returnValue(None)


    @inlineCallbacks
    def _getResource(self):
        request = SimpleStoreRequest(self, "GET", "/calendars/__uids__/user01/calendar/")
        resource = yield request.locateResource("/calendars/__uids__/user01/calendar/")
        returnValue(resource)


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
        isShared = self.resource.isShared()
        self.assertFalse(isShared)
        isShareeResource = self.resource.isShareeResource()
        self.assertFalse(isShareeResource)

        yield self.resource.upgradeToShare()

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, sharedOwnerType)
        isShared = self.resource.isShared()
        self.assertTrue(isShared)
        isShareeResource = self.resource.isShareeResource()
        self.assertFalse(isShareeResource)


    @inlineCallbacks
    def test_downgradeFromShare(self):

        yield self.resource.upgradeToShare()

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, sharedOwnerType)
        isShared = self.resource.isShared()
        self.assertTrue(isShared)
        isShareeResource = self.resource.isShareeResource()
        self.assertFalse(isShareeResource)

        yield self.resource.downgradeFromShare(None)

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, regularCalendarType)
        isShared = self.resource.isShared()
        self.assertFalse(isShared)
        isShareeResource = self.resource.isShareeResource()
        self.assertFalse(isShareeResource)


    @inlineCallbacks
    def test_POSTaddInviteeAlreadyShared(self):

        yield self.resource.upgradeToShare()

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

        isShared = self.resource.isShared()
        self.assertTrue(isShared)
        isShareeResource = self.resource.isShareeResource()
        self.assertFalse(isShareeResource)


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

        isShared = self.resource.isShared()
        self.assertTrue(isShared)
        isShareeResource = (yield self.resource.isShareeResource())
        self.assertFalse(isShareeResource)


    @inlineCallbacks
    def test_POSTupdateInvitee(self):

        isShared = self.resource.isShared()
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

        isShared = self.resource.isShared()
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

        isShared = self.resource.isShared()
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

        isShared = self.resource.isShared()
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

        isShared = self.resource.isShared()
        self.assertTrue(isShared)

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user02@example.com</D:href>
                </CS:remove>
            </CS:share>
            """)

        isShared = self.resource.isShared()
        self.assertFalse(isShared)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, None)


    @inlineCallbacks
    def test_POSTaddMoreInvitees(self):

        yield self.resource.upgradeToShare()

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

        yield self.resource.upgradeToShare()

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

        yield self.resource.upgradeToShare()

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

        yield self.resource.upgradeToShare()

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
        yield self.resource.upgradeToShare()

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

        yield self.resource.upgradeToShare()

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

        self.resource.validUserIDForShare = lambda userid, request: None
        self.resource.principalForCalendarUserAddress = lambda cuaddr: None
        self.resource.principalForUID = lambda principalUID: None

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("urn:uuid:user02"),
                customxml.CommonName.fromString("user02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusInvalid(),
            )
        ))

        yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
            <CS:share xmlns:D="DAV:" xmlns:CS="http://calendarserver.org/ns/">
                <CS:remove>
                    <D:href>mailto:user02@example.com</D:href>
                </CS:remove>
            </CS:share>
            """)

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, None)


    @inlineCallbacks
    def wikiSetup(self):
        """
        Create a wiki called C{wiki-testing}, and share it with the user whose
        home is at /.  Return the name of the newly shared calendar in the
        sharee's home.
        """
        wcreate = self._sqlCalendarStore.newTransaction("create wiki")
        yield wcreate.calendarHomeWithUID("wiki-testing", create=True)
        yield wcreate.commit()

        newService = WikiDirectoryService()
        newService.realmName = self.directory.realmName
        self.directory.addService(newService)

        txn = self.transactionUnderTest()
        sharee = yield self.homeUnderTest(name="user01")
        sharer = yield txn.calendarHomeWithUID("wiki-testing")
        cal = yield sharer.calendarWithName("calendar")
        sharedName = yield cal.shareWith(sharee, BIND_DIRECT)
        returnValue(sharedName)


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
        self.patch(sharing, "getWikiAccess", stubWikiAccessMethod)

        sharedName = yield self.wikiSetup()
        request = SimpleStoreRequest(self, "GET", "/calendars/__uids__/user01/")
        collection = yield request.locateResource("/calendars/__uids__/user01/" + sharedName)

        # Simulate the wiki server granting Read access
        acl = (yield collection.shareeAccessControlList(request))
        self.assertFalse("<write/>" in acl.toxml())

        # Simulate the wiki server granting Read-Write access
        access = "write"
        acl = (yield collection.shareeAccessControlList(request))
        self.assertTrue("<write/>" in acl.toxml())


    @inlineCallbacks
    def test_noWikiAccess(self):
        """
        If L{SharedResourceMixin.shareeAccessControlList} detects missing
        access controls for a directly shared collection, it will automatically
        un-share that collection.
        """
        sharedName = yield self.wikiSetup()
        access = "write"
        def stubWikiAccessMethod(userID, wikiID):
            return access
        self.patch(sharing, "getWikiAccess", stubWikiAccessMethod)
        @inlineCallbacks
        def listChildrenViaPropfind():
            request = SimpleStoreRequest(self, "PROPFIND", "/calendars/__uids__/user01/", authid="user01")
            request.headers.setHeader("depth", "1")
            response = yield self.send(request)
            response = IResponse(response)
            data = yield allDataFromStream(response.stream)

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
