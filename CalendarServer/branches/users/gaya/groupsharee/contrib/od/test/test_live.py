##
# Copyright (c) 2014 Apple Inc. All rights reserved.
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
OpenDirectory live service tests.
"""

from __future__ import print_function

from itertools import chain
from uuid import UUID

from twisted.trial import unittest
from twisted.internet.defer import inlineCallbacks, returnValue



try:
    from twext.who.opendirectory import DirectoryService
    moduleImported = True
except:
    moduleImported = False
    print("Could not import OpenDirectory")

if moduleImported:

    from twext.who.expression import (
        CompoundExpression, Operand, MatchExpression, MatchType
    )

    LOCAL_SHORTNAMES = "odtestalbert odtestbill odtestcarl odtestdavid odtestsubgroupa".split()
    NETWORK_SHORTNAMES = "odtestamanda odtestbetty odtestcarlene odtestdenise odtestsubgroupb odtestgrouptop".split()


    def onlyIfPopulated(func):
        """
        Only run the decorated test method if the "odtestamanda" record exists
        """
        @inlineCallbacks
        def checkThenRun(self):
            record = yield self.service.recordWithShortName(self.service.recordType.user, u"odtestamanda")
            if record is not None:
                result = yield func(self)
                returnValue(result)
            else:
                print("OD not populated, skipping {}".format(func.func_name))
        return checkThenRun


    class LiveOpenDirectoryServiceTestCase(unittest.TestCase):
        """
        Live service tests for L{DirectoryService}.
        """

        def setUp(self):
            self.service = DirectoryService()


        def verifyResults(self, records, expected, unexpected):
            shortNames = []
            for record in records:
                for shortName in record.shortNames:
                    shortNames.append(shortName)

            for name in expected:
                self.assertTrue(name in shortNames)
            for name in unexpected:
                self.assertFalse(name in shortNames)


        @onlyIfPopulated
        @inlineCallbacks
        def test_shortNameStartsWith(self):
            records = yield self.service.recordsFromExpression(
                MatchExpression(
                    self.service.fieldName.shortNames, u"odtest",
                    matchType=MatchType.startsWith
                )
            )
            self.verifyResults(
                records,
                chain(LOCAL_SHORTNAMES, NETWORK_SHORTNAMES),
                ["anotherodtestamanda", "anotherodtestalbert"]
            )


        @onlyIfPopulated
        @inlineCallbacks
        def test_uid(self):
            for uid, name in (
                (u"9DC04A71-E6DD-11DF-9492-0800200C9A66", u"odtestbetty"),
                (u"9DC04A75-E6DD-11DF-9492-0800200C9A66", u"odtestbill"),
            ):
                record = yield self.service.recordWithUID(uid)
                self.assertTrue(record is not None)
                self.assertEquals(record.shortNames[0], name)


        @onlyIfPopulated
        @inlineCallbacks
        def test_guid(self):
            for guid, name in (
                (UUID("9DC04A71-E6DD-11DF-9492-0800200C9A66"), u"odtestbetty"),
                (UUID("9DC04A75-E6DD-11DF-9492-0800200C9A66"), u"odtestbill"),
            ):
                record = yield self.service.recordWithGUID(guid)
                self.assertTrue(record is not None)
                self.assertEquals(record.shortNames[0], name)


        @onlyIfPopulated
        @inlineCallbacks
        def test_compoundWithoutRecordType(self):
            expression = CompoundExpression(
                [
                    CompoundExpression(
                        [
                            MatchExpression(
                                self.service.fieldName.fullNames, u"be",
                                matchType=MatchType.contains
                            ),
                            MatchExpression(
                                self.service.fieldName.emailAddresses, u"be",
                                matchType=MatchType.startsWith
                            ),
                        ],
                        Operand.OR
                    ),
                    CompoundExpression(
                        [
                            MatchExpression(
                                self.service.fieldName.fullNames, u"test",
                                matchType=MatchType.contains
                            ),
                            MatchExpression(
                                self.service.fieldName.emailAddresses, u"test",
                                matchType=MatchType.startsWith
                            ),
                        ],
                        Operand.OR
                    ),
                ],
                Operand.AND
            )
            records = yield self.service.recordsFromExpression(expression)

            # We should get back users and groups since we did not specify a type:
            self.verifyResults(
                records,
                ["odtestbetty", "odtestalbert", "anotherodtestalbert", "odtestgroupbetty", "odtestgroupalbert"],
                ["odtestamanda", "odtestbill", "odtestgroupa", "odtestgroupb"]
            )


        @onlyIfPopulated
        @inlineCallbacks
        def test_compoundWithSingleRecordType(self):
            expression = CompoundExpression(
                [
                    CompoundExpression(
                        [
                            CompoundExpression(
                                [
                                    MatchExpression(
                                        self.service.fieldName.fullNames, u"be",
                                        matchType=MatchType.contains
                                    ),
                                    MatchExpression(
                                        self.service.fieldName.emailAddresses, u"be",
                                        matchType=MatchType.startsWith
                                    ),
                                ],
                                Operand.OR
                            ),
                            CompoundExpression(
                                [
                                    MatchExpression(
                                        self.service.fieldName.fullNames, u"test",
                                        matchType=MatchType.contains
                                    ),
                                    MatchExpression(
                                        self.service.fieldName.emailAddresses, u"test",
                                        matchType=MatchType.startsWith
                                    ),
                                ],
                                Operand.OR
                            ),
                        ],
                        Operand.AND
                    ),
                    MatchExpression(
                        self.service.fieldName.recordType, self.service.recordType.user,
                    ),
                ],
                Operand.AND
            )
            records = yield self.service.recordsFromExpression(expression)

            # We should only get users back, not groups:
            self.verifyResults(
                records,
                ["odtestbetty", "odtestalbert", "anotherodtestalbert"],
                ["odtestamanda", "odtestbill", "odtestgroupa", "odtestgroupb", "odtestgroupbetty", "odtestgroupalbert"]
            )


        @onlyIfPopulated
        @inlineCallbacks
        def test_compoundWithMultipleRecordTypes(self):
            expression = CompoundExpression(
                [
                    CompoundExpression(
                        [
                            CompoundExpression(
                                [
                                    MatchExpression(
                                        self.service.fieldName.fullNames, u"be",
                                        matchType=MatchType.contains
                                    ),
                                    MatchExpression(
                                        self.service.fieldName.emailAddresses, u"be",
                                        matchType=MatchType.startsWith
                                    ),
                                ],
                                Operand.OR
                            ),
                            CompoundExpression(
                                [
                                    MatchExpression(
                                        self.service.fieldName.fullNames, u"test",
                                        matchType=MatchType.contains
                                    ),
                                    MatchExpression(
                                        self.service.fieldName.emailAddresses, u"test",
                                        matchType=MatchType.startsWith
                                    ),
                                ],
                                Operand.OR
                            ),
                        ],
                        Operand.AND
                    ),
                    CompoundExpression(
                        [
                            MatchExpression(
                                self.service.fieldName.recordType, self.service.recordType.user,
                            ),
                            MatchExpression(
                                self.service.fieldName.recordType, self.service.recordType.group,
                            ),
                        ],
                        Operand.OR
                    ),
                ],
                Operand.AND
            )
            records = yield self.service.recordsFromExpression(expression)

            # We should get users and groups back, since we asked for either type:
            self.verifyResults(
                records,
                ["odtestbetty", "odtestalbert", "anotherodtestalbert", "odtestgroupbetty", "odtestgroupalbert"],
                ["odtestamanda", "odtestbill", "odtestgroupa", "odtestgroupb"]
            )

        test_compoundWithMultipleRecordTypes.skip = "This ends up doing a brute force query!"
