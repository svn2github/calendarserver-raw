##
# Copyright (c) 2005-2007 Apple Inc. All rights reserved.
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

import twisted.trial.unittest

try:
    from twistedcaldav.directory.appleopendirectory import OpenDirectoryService as RealOpenDirectoryService
    import dsattributes
except ImportError:
    pass
else:
    from twistedcaldav.directory.directory import DirectoryService
    from twistedcaldav.directory.util import uuidFromName
    from twistedcaldav.test.util import TestCase

    class OpenDirectoryService (RealOpenDirectoryService):
        def _queryDirectory(self, recordType):
            try:
                return self.fakerecords[recordType]
            except KeyError:
                return []
    
    class ReloadCache(TestCase):
        def setUp(self):
            super(ReloadCache, self).setUp()
            self._service = OpenDirectoryService(node="/Search", dosetup=False)
            self._service.servicetags.add("FE588D50-0514-4DF9-BCB5-8ECA5F3DA274:030572AE-ABEC-4E0F-83C9-FCA304769E5F:calendar")
            self._service.fakerecords = { }
            
        def tearDown(self):
            for call in self._service._delayedCalls:
                call.cancel()

        def _verifyRecords(self, recordType, expected):
            expected = set(expected)
            found = set(self._service._records[recordType]["records"].keys())
            
            missing = expected.difference(found)
            extras = found.difference(expected)

            self.assertTrue(len(missing) == 0, msg="Directory records not found: %s" % (missing,))
            self.assertTrue(len(extras) == 0, msg="Directory records not expected: %s" % (extras,))
                
        def _verifyRecordsCheckEnabled(self, recordType, expected, enabled):
            expected = set(expected)
            found = set([item for item in self._service._records[recordType]["records"].iterkeys()
                         if self._service._records[recordType]["records"][item].enabledForCalendaring == enabled])
            
            missing = expected.difference(found)
            extras = found.difference(expected)

            self.assertTrue(len(missing) == 0, msg="Directory records not found: %s" % (missing,))
            self.assertTrue(len(extras) == 0, msg="Directory records not expected: %s" % (extras,))
                
        def _verifyDisabledRecords(self, recordType, expectedNames, expectedGUIDs):
            def check(disabledType, expected):
                expected = set(expected)
                found = self._service._records[recordType][disabledType]
            
                missing = expected.difference(found)
                extras = found.difference(expected)

                self.assertTrue(len(missing) == 0, msg="Disabled directory records not found: %s" % (missing,))
                self.assertTrue(len(extras) == 0, msg="Disabled directory records not expected: %s" % (extras,))

            check("disabled names", expectedNames)
            check("disabled guids", (guid.lower() for guid in expectedGUIDs))

        def test_normal(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                ],
                DirectoryService.recordType_groups: [
                    fakeODRecord("Group 01"),
                    fakeODRecord("Group 02"),
                ],
                DirectoryService.recordType_resources: [
                    fakeODRecord("Resource 01"),
                    fakeODRecord("Resource 02"),
                ],
                DirectoryService.recordType_locations: [
                    fakeODRecord("Location 01"),
                    fakeODRecord("Location 02"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)
            self._service.reloadCache(DirectoryService.recordType_groups)
            self._service.reloadCache(DirectoryService.recordType_resources)
            self._service.reloadCache(DirectoryService.recordType_locations)

            self._verifyRecords(DirectoryService.recordType_users, ("user01", "user02"))
            self._verifyDisabledRecords(DirectoryService.recordType_users, (), ())

            self._verifyRecords(DirectoryService.recordType_groups, ("group01", "group02"))
            self._verifyDisabledRecords(DirectoryService.recordType_groups, (), ())

            self._verifyRecords(DirectoryService.recordType_resources, ("resource01", "resource02"))
            self._verifyDisabledRecords(DirectoryService.recordType_resources, (), ())

            self._verifyRecords(DirectoryService.recordType_locations, ("location01", "location02"))
            self._verifyDisabledRecords(DirectoryService.recordType_locations, (), ())

        def test_normal_disabledusers(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                    fakeODRecord("User 03", addLocator=False),
                    fakeODRecord("User 04", addLocator=False),
                ],
                DirectoryService.recordType_groups: [
                    fakeODRecord("Group 01"),
                    fakeODRecord("Group 02"),
                    fakeODRecord("Group 03", addLocator=False),
                    fakeODRecord("Group 04", addLocator=False),
                ],
                DirectoryService.recordType_resources: [
                    fakeODRecord("Resource 01"),
                    fakeODRecord("Resource 02"),
                    fakeODRecord("Resource 03", addLocator=False),
                    fakeODRecord("Resource 04", addLocator=False),
                ],
                DirectoryService.recordType_locations: [
                    fakeODRecord("Location 01"),
                    fakeODRecord("Location 02"),
                    fakeODRecord("Location 03", addLocator=False),
                    fakeODRecord("Location 04", addLocator=False),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)
            self._service.reloadCache(DirectoryService.recordType_groups)
            self._service.reloadCache(DirectoryService.recordType_resources)
            self._service.reloadCache(DirectoryService.recordType_locations)

            self._verifyRecordsCheckEnabled(DirectoryService.recordType_users, ("user01", "user02"), True)
            self._verifyRecordsCheckEnabled(DirectoryService.recordType_users, ("user03", "user04"), False)

            self._verifyRecordsCheckEnabled(DirectoryService.recordType_groups, ("group01", "group02"), True)
            self._verifyRecordsCheckEnabled(DirectoryService.recordType_groups, ("group03", "group04"), False)

            self._verifyRecordsCheckEnabled(DirectoryService.recordType_resources, ("resource01", "resource02"), True)
            self._verifyRecordsCheckEnabled(DirectoryService.recordType_resources, (), False)

            self._verifyRecordsCheckEnabled(DirectoryService.recordType_locations, ("location01", "location02"), True)
            self._verifyRecordsCheckEnabled(DirectoryService.recordType_locations, (), False)

        def test_normalCacheMiss(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)

            self._verifyRecords(DirectoryService.recordType_users, ("user01",))
            self._verifyDisabledRecords(DirectoryService.recordType_users, (), ())

            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                    fakeODRecord("User 03", guid="D10F3EE0-5014-41D3-8488-3819D3EF3B2A"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users, forceUpdate=True)

            self._verifyRecords(DirectoryService.recordType_users, ("user01", "user02", "user03"))
            self._verifyDisabledRecords(DirectoryService.recordType_users, (), ())

        def test_duplicateRecords(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                    fakeODRecord("User 02"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)

            self._verifyRecords(DirectoryService.recordType_users, ("user01", "user02"))
            self._verifyDisabledRecords(DirectoryService.recordType_users, (), ())
            self._verifyDisabledRecords(DirectoryService.recordType_users, (), ())


        def test_duplicateName(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02", guid="A25775BB-1281-4606-98C6-2893B2D5CCD7"),
                    fakeODRecord("User 02", guid="30CA2BB9-C935-4A5D-80E2-79266BCB0255"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)

            self._verifyRecords(DirectoryService.recordType_users, ("user01",))
            self._verifyDisabledRecords(
                DirectoryService.recordType_users,
                ("user02",),
                ("A25775BB-1281-4606-98C6-2893B2D5CCD7", "30CA2BB9-C935-4A5D-80E2-79266BCB0255"),
            )

        def test_duplicateGUID(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02", guid="113D7F74-F84A-4F17-8C96-CE8F10D68EF8"),
                    fakeODRecord("User 03", guid="113D7F74-F84A-4F17-8C96-CE8F10D68EF8"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)

            self._verifyRecords(DirectoryService.recordType_users, ("user01",))
            self._verifyDisabledRecords(
                DirectoryService.recordType_users,
                ("user02", "user03"),
                ("113D7F74-F84A-4F17-8C96-CE8F10D68EF8",),
            )

        def test_duplicateCombo(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02", guid="113D7F74-F84A-4F17-8C96-CE8F10D68EF8"),
                    fakeODRecord("User 02", guid="113D7F74-F84A-4F17-8C96-CE8F10D68EF8", shortName="user03"),
                    fakeODRecord("User 02", guid="136E369F-DB40-4135-878D-B75D38242D39"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)

            self._verifyRecords(DirectoryService.recordType_users, ("user01",))
            self._verifyDisabledRecords(
                DirectoryService.recordType_users,
                ("user02", "user03"),
                ("113D7F74-F84A-4F17-8C96-CE8F10D68EF8", "136E369F-DB40-4135-878D-B75D38242D39"),
            )

        def test_duplicateGUIDCacheMiss(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02", guid="EDB9EE55-31F2-4EA9-B5FB-D8AE2A8BA35E"),
                    fakeODRecord("User 03", guid="D10F3EE0-5014-41D3-8488-3819D3EF3B2A"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)

            self._verifyRecords(DirectoryService.recordType_users, ("user01", "user02", "user03"))
            self._verifyDisabledRecords(DirectoryService.recordType_users, (), ())
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02", guid="EDB9EE55-31F2-4EA9-B5FB-D8AE2A8BA35E"),
                    fakeODRecord("User 02", guid="EDB9EE55-31F2-4EA9-B5FB-D8AE2A8BA35E", shortName="user04"),
                    fakeODRecord("User 03", guid="62368DDF-0C62-4C97-9A58-DE9FD46131A0"),
                    fakeODRecord("User 03", guid="62368DDF-0C62-4C97-9A58-DE9FD46131A0", shortName="user05"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users, forceUpdate=True)

            self._verifyRecords(DirectoryService.recordType_users, ("user01",))
            self._verifyDisabledRecords(
                DirectoryService.recordType_users,
                ("user02", "user03", "user04", "user05"),
                ("EDB9EE55-31F2-4EA9-B5FB-D8AE2A8BA35E", "62368DDF-0C62-4C97-9A58-DE9FD46131A0"),
            )

        def test_groupmembers(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                ],
                DirectoryService.recordType_groups: [
                    fakeODRecord("Group 01", members=[
                        guidForShortName("user01"),
                        guidForShortName("user02"),
                    ]),
                    fakeODRecord("Group 02", members=[
                        guidForShortName("resource01"),
                        guidForShortName("user02"),
                    ]),
                ],
                DirectoryService.recordType_resources: [
                    fakeODRecord("Resource 01"),
                    fakeODRecord("Resource 02"),
                ],
                DirectoryService.recordType_locations: [
                    fakeODRecord("Location 01"),
                    fakeODRecord("Location 02"),
                ],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)
            self._service.reloadCache(DirectoryService.recordType_groups)
            self._service.reloadCache(DirectoryService.recordType_resources)
            self._service.reloadCache(DirectoryService.recordType_locations)

            group1 = self._service.recordWithShortName(DirectoryService.recordType_groups, "group01")
            self.assertTrue(group1 is not None)

            group2 = self._service.recordWithShortName(DirectoryService.recordType_groups, "group02")
            self.assertTrue(group2 is not None)

            user1 = self._service.recordWithShortName(DirectoryService.recordType_users, "user01")
            self.assertTrue(user1 is not None)
            self.assertEqual(set((group1,)), user1.groups()) 
            
            user2 = self._service.recordWithShortName(DirectoryService.recordType_users, "user02")
            self.assertTrue(user2 is not None)
            self.assertEqual(set((group1, group2)), user2.groups()) 
            
            self._service.fakerecords[DirectoryService.recordType_groups] = [
                fakeODRecord("Group 01", members=[
                    guidForShortName("user01"),
                ]),
                fakeODRecord("Group 02", members=[
                    guidForShortName("resource01"),
                    guidForShortName("user02"),
                ]),
            ]
            self._service.refresh(loop=False)
            self._service.reloadCache(DirectoryService.recordType_groups, forceUpdate=True)

            group1 = self._service.recordWithShortName(DirectoryService.recordType_groups, "group01")
            self.assertTrue(group1 is not None)

            group2 = self._service.recordWithShortName(DirectoryService.recordType_groups, "group02")
            self.assertTrue(group2 is not None)

            user1 = self._service.recordWithShortName(DirectoryService.recordType_users, "user01")
            self.assertTrue(user1 is not None)
            self.assertEqual(set((group1,)), user1.groups()) 
            
            user2 = self._service.recordWithShortName(DirectoryService.recordType_users, "user02")
            self.assertTrue(user2 is not None)
            self.assertEqual(set((group2,)), user2.groups()) 
            
            self._service.fakerecords[DirectoryService.recordType_groups] = [
                fakeODRecord("Group 01", members=[
                    guidForShortName("user01"),
                ]),
                fakeODRecord("Group 02", members=[
                    guidForShortName("resource01"),
                    guidForShortName("user02"),
                ]),
                fakeODRecord("Group 03", members=[
                    guidForShortName("user01"),
                    guidForShortName("user02"),
                ]),
            ]
            self._service.refresh(loop=False)
            self._service.reloadCache(DirectoryService.recordType_groups, forceUpdate=True)

            group1 = self._service.recordWithShortName(DirectoryService.recordType_groups, "group01")
            self.assertTrue(group1 is not None)

            group2 = self._service.recordWithShortName(DirectoryService.recordType_groups, "group02")
            self.assertTrue(group2 is not None)

            group3 = self._service.recordWithShortName(DirectoryService.recordType_groups, "group03")
            self.assertTrue(group2 is not None)

            user1 = self._service.recordWithShortName(DirectoryService.recordType_users, "user01")
            self.assertTrue(user1 is not None)
            self.assertEqual(set((group1, group3)), user1.groups()) 
            
            user2 = self._service.recordWithShortName(DirectoryService.recordType_users, "user02")
            self.assertTrue(user2 is not None)
            self.assertEqual(set((group2, group3)), user2.groups()) 

        def test_calendaruseraddress(self):
            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                ],
                DirectoryService.recordType_groups: [],
                DirectoryService.recordType_resources: [],
                DirectoryService.recordType_locations: [],
            }
            self._service.refresh(loop=False)

            self._service.reloadCache(DirectoryService.recordType_users)

            user1 = self._service.recordWithCalendarUserAddress("mailto:user01@example.com")
            self.assertTrue(user1 is not None)

            user3 = self._service.recordWithCalendarUserAddress("mailto:user03@example.com")
            self.assertTrue(user3 is None)

            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                    fakeODRecord("User 03"),
                ],
                DirectoryService.recordType_groups: [],
                DirectoryService.recordType_resources: [],
                DirectoryService.recordType_locations: [],
            }
            self._service.refresh(loop=False)
            self._service.reloadCache(DirectoryService.recordType_users, forceUpdate=True)

            user1 = self._service.recordWithCalendarUserAddress("mailto:user01@example.com")
            self.assertTrue(user1 is not None)

            user3 = self._service.recordWithCalendarUserAddress("mailto:user03@example.com")
            self.assertTrue(user3 is not None)

            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 02"),
                    fakeODRecord("User 03"),
                ],
                DirectoryService.recordType_groups: [],
                DirectoryService.recordType_resources: [],
                DirectoryService.recordType_locations: [],
            }
            self._service.refresh(loop=False)
            self._service.reloadCache(DirectoryService.recordType_users, forceUpdate=True)

            user1 = self._service.recordWithCalendarUserAddress("mailto:user01@example.com")
            self.assertTrue(user1 is None)

            user3 = self._service.recordWithCalendarUserAddress("mailto:user03@example.com")
            self.assertTrue(user3 is not None)


        def test_substantialDecline(self):
            """
            Test the "substantial decline" protection logic in the case where an
            od query returns less than half the results of the previous
            successful one.
            """

            self._service.fakerecords = {
                DirectoryService.recordType_users: [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
                    fakeODRecord("User 03"),
                    fakeODRecord("User 04"),
                    fakeODRecord("User 05"),
                    fakeODRecord("User 06"),
                ],
                DirectoryService.recordType_groups: [],
                DirectoryService.recordType_resources: [],
                DirectoryService.recordType_locations: [],
            }
            self._service.refresh(loop=False)
            self._service.reloadCache(DirectoryService.recordType_users, forceUpdate=True)
            user1 = self._service.recordWithCalendarUserAddress("mailto:user01@example.com")
            self.assertTrue(user1 is not None)

            # Pretend OD suddenly returned less than half:
            self._service.fakerecords[DirectoryService.recordType_users] = [
                    fakeODRecord("User 01"),
                    fakeODRecord("User 02"),
            ]
            self._service.refresh(loop=False)
            self._service.reloadCache(DirectoryService.recordType_users, forceUpdate=True)
            user3 = self._service.recordWithCalendarUserAddress("mailto:user03@example.com")
            self.assertTrue(user3 is not None)

def fakeODRecord(fullName, shortName=None, guid=None, email=None, addLocator=True, members=None):
    if shortName is None:
        shortName = shortNameForFullName(fullName)

    if guid is None:
        guid = guidForShortName(shortName)
    else:
        guid = guid.lower()

    if email is None:
        email = "%s@example.com" % (shortName,)

    attrs = {
        dsattributes.kDS1AttrDistinguishedName: fullName,
        dsattributes.kDS1AttrGeneratedUID: guid,
        dsattributes.kDSNAttrEMailAddress: email,
        dsattributes.kDSNAttrMetaNodeLocation: "/LDAPv3/127.0.0.1",
    }
    
    if members:
        attrs[dsattributes.kDSNAttrGroupMembers] = members

    if addLocator:
        attrs[dsattributes.kDSNAttrServicesLocator] = "FE588D50-0514-4DF9-BCB5-8ECA5F3DA274:030572AE-ABEC-4E0F-83C9-FCA304769E5F:calendar"

    return [ shortName, attrs ]

def shortNameForFullName(fullName):
    return fullName.lower().replace(" ", "")

def guidForShortName(shortName):
    return uuidFromName(OpenDirectoryService.baseGUID, shortName)
