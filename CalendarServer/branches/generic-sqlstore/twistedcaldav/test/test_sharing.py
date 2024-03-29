##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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
from twext.web2.dav import davxml
from twext.web2.http_headers import MimeType
from twext.web2.stream import MemoryStream
from twext.web2.test.test_server import SimpleRequest
from twisted.internet.defer import inlineCallbacks, returnValue, succeed
from twistedcaldav import customxml
from twistedcaldav.config import config
from twistedcaldav.test.util import HomeTestCase, norequest
from twistedcaldav.resource import CalDAVResource
from txdav.caldav.datastore.test.test_sql import buildStore
from txdav.caldav.datastore.test.common import StubNotifierFactory


class SharingTests(HomeTestCase):

    class FakePrincipal(object):
        
        class FakeRecord(object):
            
            def __init__(self, name, cuaddr):
                self.fullName = name
                self.guid = name
                self.calendarUserAddresses = set((cuaddr,))

        def __init__(self, cuaddr):
            self.path = "/principals/__uids__/%s" % (cuaddr[7:].split('@')[0],)
            self.homepath = "/calendars/__uids__/%s" % (cuaddr[7:].split('@')[0],)
            self.displayname = cuaddr[7:].split('@')[0].upper()
            self.record = self.FakeRecord(cuaddr[7:].split('@')[0], cuaddr)

        def calendarHome(self, request):
            class FakeHome(object):
                def removeShareByUID(self, request, uid):pass
            return FakeHome()
        
        def principalURL(self):
            return self.path
        
        def displayName(self):
            return self.displayname


    @inlineCallbacks
    def setUp(self):
        yield super(SharingTests, self).setUp()

        self.patch(config.Sharing, "Enabled", True)
        self.patch(config.Sharing.Calendars, "Enabled", True)

        CalDAVResource.validUserIDForShare = self._fakeValidUserID
        CalDAVResource.validUserIDWithCommonNameForShare = self._fakeValidUserID
        CalDAVResource.sendInvite = lambda self, record, request: succeed(True)
        CalDAVResource.removeInvite = lambda self, record, request: succeed(True)

        CalDAVResource.principalForCalendarUserAddress = lambda self, cuaddr: SharingTests.FakePrincipal(cuaddr)


    @inlineCallbacks
    def _refreshRoot(self, request=None):
        if request is None:
            request = norequest()
        result = yield super(SharingTests, self)._refreshRoot(request)
        self.resource = (
            yield self.site.resource.locateChild(request, ["calendar"])
        )[0]
        returnValue(result)


    def _fakeValidUserID(self, userid, *args):
        if userid.startswith("/principals/"):
            return userid
        if userid.endswith("@example.com"):
            principal = SharingTests.FakePrincipal(userid)
            return principal.path if len(args) == 0 else (userid, principal.path, principal.displayname,)
        else:
            return None if len(args) == 0 else (None, None, None,)

    def _fakeInvalidUserID(self, userid, *args):
        return None if len(args) == 0 else (None, None, None,)

    @inlineCallbacks
    def _doPOST(self, body, resultcode = responsecode.OK):
        request = SimpleRequest(self.site, "POST", "/calendar/")
        request.headers.setHeader("content-type", MimeType("text", "xml"))
        request.stream = MemoryStream(body)

        response = (yield self.send(request, None))
        self.assertEqual(response.code, resultcode)
        returnValue(response)

    def _clearUIDElementValue(self, xml):
        
        for user in xml.children:
            for element in user.children:
                if type(element) == customxml.UID:
                    element.children[0].data = ""
        return xml

    @inlineCallbacks
    def test_upgradeToShareOnCreate(self):
        request = SimpleRequest(self.site, "MKCOL", "/calendar/")

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, davxml.ResourceType.calendar)
        propInvite = (yield self.resource.readProperty(customxml.Invite, request))
        self.assertEquals(propInvite, None)

        self.resource.upgradeToShare()

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, davxml.ResourceType.sharedownercalendar)
        propInvite = (yield self.resource.readProperty(customxml.Invite, request))
        self.assertEquals(propInvite, customxml.Invite())
        
        isShared = (yield self.resource.isShared(request))
        self.assertTrue(isShared)
        isVShared = self.resource.isVirtualShare()
        self.assertFalse(isVShared)

    @inlineCallbacks
    def test_upgradeToShareAfterCreate(self):
        request = SimpleRequest(self.site, "PROPPATCH", "/calendar/")

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, davxml.ResourceType.calendar)
        propInvite = (yield self.resource.readProperty(customxml.Invite, request))
        self.assertEquals(propInvite, None)

        self.resource.upgradeToShare()

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, davxml.ResourceType.sharedownercalendar)
        propInvite = (yield self.resource.readProperty(customxml.Invite, request))
        self.assertEquals(propInvite, customxml.Invite())
        
        isShared = (yield self.resource.isShared(request))
        self.assertTrue(isShared)
        isVShared = self.resource.isVirtualShare()
        self.assertFalse(isVShared)

    @inlineCallbacks
    def test_downgradeFromShare(self):
        request = SimpleRequest(self.site, "PROPPATCH", "/calendar/")

        self.resource.writeDeadProperty(davxml.ResourceType.sharedownercalendar)
        self.resource.writeDeadProperty(customxml.Invite())
        rtype = self.resource.resourceType()
        self.assertEquals(rtype, davxml.ResourceType.sharedownercalendar)
        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, customxml.Invite())

        yield self.resource.downgradeFromShare(None)

        rtype = self.resource.resourceType()
        self.assertEquals(rtype, davxml.ResourceType.calendar)
        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(propInvite, None)
        
        isShared = (yield self.resource.isShared(None))
        self.assertFalse(isShared)
        isVShared = self.resource.isVirtualShare()
        self.assertFalse(isVShared)

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
                davxml.HRef.fromString("mailto:user02@example.com"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))
        
        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)
        isVShared = self.resource.isVirtualShare()
        self.assertFalse(isVShared)

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
"""
        )

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("mailto:user02@example.com"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))
        
        isShared = (yield self.resource.isShared(None))
        self.assertTrue(isShared)
        isVShared = (yield self.resource.isVirtualShare())
        self.assertFalse(isVShared)

    @inlineCallbacks
    def test_POSTupdateInvitee(self):
        
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
        <D:href>mailto:user02@example.com</D:href>
        <CS:summary>My Shared Calendar</CS:summary>
        <CS:read/>
    </CS:set>
</CS:share>
""")

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("mailto:user02@example.com"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))

    @inlineCallbacks
    def test_POSTremoveInvitee(self):
        
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
    <CS:remove>
        <D:href>mailto:user02@example.com</D:href>
    </CS:remove>
</CS:share>
""")

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite())

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
                davxml.HRef.fromString("mailto:user02@example.com"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("mailto:user03@example.com"),
                customxml.CommonName.fromString("USER03"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("mailto:user04@example.com"),
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
                davxml.HRef.fromString("mailto:user02@example.com"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("mailto:user04@example.com"),
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
                davxml.HRef.fromString("mailto:user02@example.com"),
                customxml.CommonName.fromString("USER02"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            ),
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("mailto:user03@example.com"),
                customxml.CommonName.fromString("USER03"),
                customxml.InviteAccess(customxml.ReadAccess()),
                customxml.InviteStatusNoResponse(),
            ),
        ))


    @inlineCallbacks
    def test_POSTaddInvalidInvitee(self):
        self.resource.upgradeToShare()

        response = (yield self._doPOST("""<?xml version="1.0" encoding="utf-8" ?>
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
        self.assertEqual(
            str(response.stream.read()).replace("\r\n", "\n"),
            """<?xml version='1.0' encoding='UTF-8'?>
<multistatus xmlns='DAV:'>
  <response>
    <href>mailto:bogus@example.net</href>
    <status>HTTP/1.1 403 Forbidden</status>
  </response>
</multistatus>"""
        )
        propInvite = (yield self.resource.readProperty(customxml.Invite, None))

        self.assertEquals(
            self._clearUIDElementValue(propInvite), customxml.Invite()
        )


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
                davxml.HRef.fromString("mailto:user01@example.com"),
                customxml.CommonName.fromString("USER01"),
                customxml.InviteAccess(customxml.ReadWriteAccess()),
                customxml.InviteStatusNoResponse(),
            )
        ))

        self.resource.validUserIDForShare = self._fakeInvalidUserID
        self.resource.validUserIDWithCommonNameForShare = self._fakeInvalidUserID
        self.resource.principalForCalendarUserAddress = lambda cuaddr: None

        propInvite = (yield self.resource.readProperty(customxml.Invite, None))
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite(
            customxml.InviteUser(
                customxml.UID.fromString(""),
                davxml.HRef.fromString("mailto:user01@example.com"),
                customxml.CommonName.fromString("USER01"),
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
        self.assertEquals(self._clearUIDElementValue(propInvite), customxml.Invite())



class DatabaseSharingTests(SharingTests):

    @inlineCallbacks
    def setUp(self):
        self.calendarStore = yield buildStore(self, StubNotifierFactory())
        yield super(DatabaseSharingTests, self).setUp()


    def createDataStore(self):
        return self.calendarStore


