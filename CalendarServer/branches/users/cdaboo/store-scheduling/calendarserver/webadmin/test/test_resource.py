##
# Copyright (c) 2011-2013 Apple Inc. All rights reserved.
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
Tests for L{calendarserver.webadmin.resource}.
"""

import cgi

from functools import partial

from twistedcaldav.test.util import TestCase

from twisted.web.microdom import parseString, getElementsByTagName
from twisted.web.domhelpers import gatherTextNodes


from calendarserver.tap.util import FakeRequest
from twisted.internet.defer import inlineCallbacks
from twisted.internet.defer import returnValue
from twisted.internet.defer import succeed
from calendarserver.webadmin.resource import WebAdminResource

from txdav.xml.rfc3744 import GroupMemberSet
from txdav.xml.rfc2518 import DisplayName

from twext.web2.http import HTTPError
from twext.web2.responsecode import CONFLICT
from txdav.xml.rfc2518 import HRef
from twistedcaldav.directory.directory import DirectoryRecord



class RenderingTests(TestCase):
    """
    Tests for HTML rendering L{WebAdminResource}.
    """

    def expectRecordSearch(self, searchString, result):
        """
        Expect that a search will be issued via with the given fields, and will
        yield the given result.
        """
        self.expectedSearches[(searchString,)] = result


    def recordsMatchingTokens(self, tokens):
        """
        Pretend to be a directory object for the purposes of testing.
        """
        return succeed(self.expectedSearches.pop(tuple(tokens)))


    def setUp(self):
        self.expectedSearches = {}
        self.resource = WebAdminResource(self.mktemp(), None, self, None)


    @inlineCallbacks
    def renderPage(self, args={}):
        """
        Render a page, returning a Deferred that fires with the HTML as a
        result.
        """
        req = FakeRequest(method='GET', path='/admin',
                          rootResource=self.resource)
        req.args = args
        response = yield self.resource.render(req)
        self.assertEquals(response.code, 200)
        content = response.stream.mem
        document = parseString(content)
        returnValue(document)


    @inlineCallbacks
    def test_simplestRender(self):
        """
        Rendering a L{WebAdminResource} will result in something vaguely
        parseable as HTML.
        """
        document = yield self.renderPage()
        self.assertEquals(document.documentElement.tagName, 'html')


    def expectSomeRecords(self):
        """
        Sample invocation of expectRecordSearch that includes two sample
        directory records.
        """
        self.expectRecordSearch(
            "bob", [
                DirectoryRecord(
                    service=self, recordType=recordType, guid=None,
                    authIDs=authIds, emailAddresses=tuple(emails),
                    shortNames=tuple(shortNames), fullName=fullName
                )
                for (shortNames, fullName, authIds, emails, recordType)
                in [
                    (["bobd"], "Bob Dobson", ["bobdlogin"],
                     ["bobd@example.com"], 'sudoers'),
                    (["bob"], "Bob Bobson", ["boblogin"],
                     ["bob@example.com", "bob@other.example.com"], 'users'),
                   ]
            ])


    @inlineCallbacks
    def test_resourceSearch(self):
        """
        Searching for resources should result in an HTML table resource search.
        """
        self.expectSomeRecords()
        document = yield self.renderPage(dict(resourceSearch=["bob"]))

        # Form is filled out with existing input.
        self.assertEquals(
            document.getElementById("txt_resourceSearch").getAttribute("value"),
            "bob"
        )
        tables = getElementsByTagName(document, "table")
        # search results are the first table
        rows = getElementsByTagName(tables[0], 'tr')
        self.assertEquals(len(rows), 3)
        firstRowCells = getElementsByTagName(rows[1], 'td')
        self.assertEquals(
            [gatherTextNodes(cell) for cell in firstRowCells[1:]],
            ["Bob Bobson", "User", "bob", "boblogin",
             "bob@example.com, bob@other.example.com"]
        )
        [resourceLink] = getElementsByTagName(
            firstRowCells[0], 'a')
        self.assertEquals(
            resourceLink.getAttribute("href"),
            "/admin/?resourceId=users:bob"
        )
        self.assertEquals(gatherTextNodes(resourceLink), "select")
        self.assertNotIn(
            "No matches found for resource bob",
            gatherTextNodes(document)
        )


    @inlineCallbacks
    def test_proxySearch(self):
        """
        When the user searches for a proxy, the results are displayed in a
        table, in a form that will allow them to submit it to add new read or
        write proxies.
        """
        self.expectSomeRecords()
        self.resource.getResourceById = partial(FakePrincipalResource, self)
        document = yield self.renderPage(dict(resourceId=["qux"],
                                              proxySearch=["bob"]))

        # Form is filled out with existing input.
        self.assertEquals(
            document.getElementById("txt_proxySearch").getAttribute("value"),
            "bob"
        )
        proxyAddForm = document.getElementById("frm_proxyAdd")
        [proxyResultsTable] = getElementsByTagName(proxyAddForm, "table")

        rows = getElementsByTagName(proxyResultsTable, 'tr')
        self.assertEquals(len(rows), 3)
        firstRowCells = getElementsByTagName(rows[1], 'td')
        self.assertEquals(
            [gatherTextNodes(cell) for cell in firstRowCells[1:]],
            ["User", "bob", "bob@example.com, bob@other.example.com", ""]
        )
        self.assertNotIn(
            "No matches found for proxy resource bob",
            gatherTextNodes(document)
        )


    @inlineCallbacks
    def test_emptyProxySearch(self):
        """
        When no results are found for a search for proxies, a relevant message
        should be displayed and the table/form for results should not be.
        """
        self.resource.getResourceById = partial(FakePrincipalResource, self)
        self.expectRecordSearch("bob", [])
        document = yield self.renderPage(dict(resourceId=["qux"],
                                              proxySearch=["bob"]))
        self.assertEquals(
            document.getElementById("txt_proxySearch").getAttribute("value"),
            "bob"
        )
        proxyAddForm = document.getElementById("frm_proxyAdd")
        self.assertIdentical(proxyAddForm, None)
        self.assertIn(
            "No matches found for proxy resource bob",
            gatherTextNodes(document)
        )


    @inlineCallbacks
    def test_noResourceFound(self):
        """
        Searching for resources which don't exist should result in an
        informative message.
        """
        self.expectRecordSearch("bob", [])
        document = yield self.renderPage(dict(resourceSearch=["bob"]))
        self.assertIn(
            "No matches found for resource bob",
            gatherTextNodes(document)
        )
        # Search results table should not be displayed.
        self.assertIdentical(document.getElementById("tab_searchResults"), None)


    @inlineCallbacks
    def test_selectResourceById(self):
        """
        When a resource is selected by a 'resourceId' parameter,
        """
        self.resource.getResourceById = partial(FakePrincipalResource, self)
        document = yield self.renderPage(dict(resourceId=["qux"]))
        [detailsTitle] = getElementsByTagName(document, 'h3')
        detailString = gatherTextNodes(detailsTitle)
        self.assertEquals(detailString,
                          "Resource Details: Hello Fake Resource: 'qux'")
        hiddenResourceId = document.getElementById(
            "hdn_resourceId").getAttribute("value")
        self.assertEquals(hiddenResourceId, "qux")

        autoScheduleMenu = document.getElementById("sel_autoSchedule")

        # Now, some assertions about features that are covered in other tests
        # which should be turned _off_ here since we're not asking for them.

        # Not an auto-schedule resource; there should be no auto-schedule menu.
        self.assertIdentical(autoScheduleMenu, None)
        # No resource search present; we shouldn't be performing the search.
        self.assertNotIn("No matches found for resource",
                         gatherTextNodes(document))
        self.assertIdentical(document.getElementById("tab_searchResults"), None)
        # I'm not attempting to retrieve a property, there's nothing to fail to
        # parse.
        self.assertNotIn("Unable to parse property", gatherTextNodes(document))
        # I'm not searching for proxies, so there shouldn't be any results.
        self.assertNotIn("No matches found for proxy resource",
                         gatherTextNodes(document))


    @inlineCallbacks
    def test_davProperty(self):
        """
        When a resource is selected by a resourceId parameter, and a DAV
        property is selected by the 'davPropertyName' parameter, that property
        will displayed.
        """
        self.resource.getResourceById = partial(FakePrincipalResource, self)
        document = yield self.renderPage(
            dict(resourceId=["qux"],
                 davPropertyName=["DAV:#displayname"])
        )
        propertyName = document.getElementById('txt_davPropertyName')
        self.assertEquals(propertyName.getAttribute("value"),
                          "DAV:#displayname")
        propertyValue = DisplayName("The Name To Display").toxml()
        self.assertIn(cgi.escape(propertyValue),
                      gatherTextNodes(document))
        self.assertNotIn("Unable to parse property to read:",
                         gatherTextNodes(document))


    @inlineCallbacks
    def test_notADavProperty(self):
        """
        When a DAV property is selected without the proper syntax (i.e. no "#"
        to separate namespace and name), an error will be displayed.
        """
        self.resource.getResourceById = partial(FakePrincipalResource, self)
        document = yield self.renderPage(
            dict(resourceId=["qux"],
                 davPropertyName=["blub"])
        )
        propertyName = document.getElementById('txt_davPropertyName')
        self.assertEquals(propertyName.getAttribute("value"),
                          "blub")
        propertyValue = "Unable to parse property to read: blub"
        self.assertIn(cgi.escape(propertyValue),
                      gatherTextNodes(document))


    @inlineCallbacks
    def test_autoScheduleMenu(self):
        """
        When rendering a resource, an "Auto-Schedule" menu with "Yes/No" options
        should be displayed.
        """
        for expectValue in [True, False]:

            self.resource.getResourceById = partial(FakePrincipalResource, self,
                                                    recordType='resources',
                                                    autosched=expectValue)
            document = yield self.renderPage(dict(resourceId=["qux"]))
            autoScheduleMenu = document.getElementById("sel_autoSchedule")
            self.assertEquals(autoScheduleMenu.getAttribute("name"),
                              "autoSchedule")

            yesno = getElementsByTagName(autoScheduleMenu, 'option')

            # Sanity checks to make sure we got the right items
            self.assertEquals(yesno[0].getAttribute("value"), "true")
            self.assertEquals(yesno[1].getAttribute("value"), "false")

            [expectedTrue, expectedFalse] = [yesno[not expectValue],
                                             yesno[expectValue]]

            self.assertEquals(expectedTrue.hasAttribute("selected"), True)
            self.assertEquals(expectedFalse.hasAttribute("selected"), False)
            self.assertEquals(expectedTrue.getAttribute("selected"), "selected")


    @inlineCallbacks
    def test_autoScheduleModeMenu(self):
        """
        When rendering a resource, an "Auto-Schedule Mode" menu with various options
        should be displayed.
        """

        modes = ("default", "none", "accept-always", "decline-always", "accept-if-free", "decline-if-busy", "automatic",)

        for ctr, expectValue in enumerate(modes):

            self.resource.getResourceById = partial(FakePrincipalResource, self,
                                                    recordType='resources',
                                                    autosched=True,
                                                    autoschedmode=expectValue)
            document = yield self.renderPage(dict(resourceId=["qux"]))
            autoScheduleModeMenu = document.getElementById("sel_autoScheduleMode")
            self.assertEquals(autoScheduleModeMenu.getAttribute("name"),
                              "autoScheduleMode")

            popup = getElementsByTagName(autoScheduleModeMenu, 'option')

            # Sanity checks to make sure we got the right items
            for i, mode in enumerate(modes):
                self.assertEquals(popup[i].getAttribute("value"), mode)

            popupValues = [popup[i] for i in range(len(modes))]
            for i in range(len(modes)):
                self.assertEquals(popupValues[i].hasAttribute("selected"), ctr == i)
            self.assertEquals(popupValues[ctr].getAttribute("selected"), "selected")


    @inlineCallbacks
    def test_proxiesListing(self):
        """
        Resource principals will have their proxies listed in a table.
        """
        def fakeResourceById(request, resid):
            return FakePrincipalResource(self, request, resid,
                                         recordType="resources")

        self.resource.getResourceById = fakeResourceById
        document = yield self.renderPage(dict(resourceId=["qux"]))
        proxiesForm = document.getElementById("frm_proxies")
        [proxiesTable] = getElementsByTagName(proxiesForm, "table")
        rows = getElementsByTagName(proxiesTable, "tr")

        # header + 3 data rows (see FakePrincipalResource)
        self.assertEquals(len(rows), 4)
        firstRowCells = getElementsByTagName(rows[1], "td")
        # name, buttons, name, buttons
        self.assertEquals(len(firstRowCells), 4)
        lastRowCells = getElementsByTagName(rows[-1], "td")
        # name, buttons, blank space
        self.assertEquals(len(lastRowCells), 3)
        self.assertEquals(lastRowCells[-1].getAttribute("colspan"), "2")

        self.assertNotIn("This resource has no proxies.",
                         ''.join(gatherTextNodes(document)))


    @inlineCallbacks
    def test_noProxiesListing(self):
        """
        When the selected resource principal has no proxies, the page should
        display a message saying so.
        """
        self.resource.getResourceById = partial(FakePrincipalResource, self,
                                                recordType='resources',
                                                hasProxies=False)
        document = yield self.renderPage(dict(resourceId=['qux']))
        self.assertIn("This resource has no proxies.",
                      ''.join(gatherTextNodes(document)))


    @inlineCallbacks
    def test_noDavProperty(self):
        """
        When a DAV property is not found, an error will be displayed.
        """
        self.resource.getResourceById = partial(FakePrincipalResource, self)
        document = yield self.renderPage(
            dict(resourceId=["qux"],
                 davPropertyName=["DAV:#blub"])
        )
        propertyName = document.getElementById('txt_davPropertyName')
        self.assertEquals(propertyName.getAttribute("value"),
                          "DAV:#blub")
        propertyValue = "No such property: DAV:#blub"
        self.assertIn(cgi.escape(propertyValue),
                      gatherTextNodes(document))

    # Properties for being a fake directory service as far as the implementation
    # of DirectoryRecord is concerned.
    realmName = 'Fake'
    guid = '28c57671-2bf8-4ebd-bc45-fda5ffcee1e8'



class FakePrincipalResource(object):
    def __init__(self, test, req=None, resid='no-id-given',
                 autosched=True, autoschedmode=None,
                 recordType="users", extraProperties=(), hasProxies=True):
        self.test = test
        self.resid = resid
        self.autosched = autosched
        self.autoschedmode = autoschedmode
        self.recordType = recordType
        self.extraProperties = extraProperties
        self.hasProxies = hasProxies


    @property
    def record(self):
        authIds = ['fake auth id']
        emails = ['fake email']
        shortNames = [self.resid]
        fullName = 'nobody'
        return DirectoryRecord(
            service=self.test, recordType=self.recordType, guid=None,
            authIDs=authIds, emailAddresses=tuple(emails),
            shortNames=tuple(shortNames), fullName=fullName
        )


    def __str__(self):
        return 'Hello Fake Resource: %r' % (self.resid,)


    def getChild(self, name):
        if name == 'calendar-proxy-read':
            if self.hasProxies:
                proxyProps = [HRef("read-1"), HRef("read-2"), HRef("read-3")]
            else:
                proxyProps = []
            return FakePrincipalResource(
                self.test,
                extraProperties=[GroupMemberSet(*proxyProps)]
            )
        elif name == 'calendar-proxy-write':
            if self.hasProxies:
                proxyProps = [HRef("write-1"), HRef("write-2")]
            else:
                proxyProps = []
            return FakePrincipalResource(
                self.test,
                extraProperties=[GroupMemberSet(*proxyProps)]
            )


    @inlineCallbacks
    def readProperty(self, name, request):
        yield None
        if not isinstance(name, tuple):
            name = name.qname()
        for prop in self.extraProperties:
            if name == prop.qname():
                returnValue(prop)
        if name == DisplayName.qname():
            returnValue(DisplayName("The Name To Display"))
        else:
            raise HTTPError(CONFLICT)


    def getAutoSchedule(self):
        return self.autosched


    def getAutoScheduleMode(self):
        return self.autoschedmode
