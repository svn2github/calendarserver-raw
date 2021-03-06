##
# Copyright (c) 2013 Apple Inc. All rights reserved.
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
Directory service expression tests.
"""

from twisted.trial import unittest

from twext.who.idirectory import FieldName
from twext.who.expression import MatchExpression, MatchType, MatchFlags



class MatchExpressionTest(unittest.TestCase):
    def test_repr_name(self):
        self.assertEquals(
            "<MatchExpression: u'full names' equals u'Wilfredo Sanchez'>",
            repr(MatchExpression(
                FieldName.fullNames,
                u"Wilfredo Sanchez",
            )),
        )

    def test_repr_type(self):
        self.assertEquals(
            "<MatchExpression: u'full names' contains u'Sanchez'>",
            repr(MatchExpression(
                FieldName.fullNames,
                u"Sanchez",
                matchType=MatchType.contains,
            )),
        )

    def test_repr_flags(self):
        self.assertEquals(
            "<MatchExpression: u'full names' starts with u'Wilfredo' (not)>",
            repr(MatchExpression(
                FieldName.fullNames,
                u"Wilfredo",
                matchType=MatchType.startsWith,
                flags=MatchFlags.NOT,
            )),
        )
