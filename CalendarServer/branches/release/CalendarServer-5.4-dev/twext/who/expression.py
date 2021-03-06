# -*- test-case-name: twext.who.test.test_expression -*-
##
# Copyright (c) 2013-2014 Apple Inc. All rights reserved.
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
Directory query expressions.
"""

__all__ = [
    "MatchType",
    "MatchFlags",
    "MatchExpression",
]

from twisted.python.constants import Names, NamedConstant
from twisted.python.constants import Flags, FlagConstant



##
# Match expression
##



class MatchType(Names):
    """
    Query match types.
    """
    equals     = NamedConstant()
    startsWith = NamedConstant()
    contains   = NamedConstant()

    equals.description     = "equals"
    startsWith.description = "starts with"
    contains.description   = "contains"



class MatchFlags(Flags):
    """
    Match expression flags.
    """
    NOT = FlagConstant()
    NOT.description = "not"

    caseInsensitive = FlagConstant()
    caseInsensitive.description = "case insensitive"



class MatchExpression(object):
    """
    Query for a matching value in a given field.

    @ivar fieldName: a L{NamedConstant} specifying the field
    @ivar fieldValue: a text value to match
    @ivar matchType: a L{NamedConstant} specifying the match algorythm
    @ivar flags: L{NamedConstant} specifying additional options
    """

    def __init__(
        self,
        fieldName, fieldValue,
        matchType=MatchType.equals, flags=None
    ):
        self.fieldName  = fieldName
        self.fieldValue = fieldValue
        self.matchType  = matchType
        self.flags      = flags

    def __repr__(self):
        def describe(constant):
            return getattr(constant, "description", str(constant))

        if self.flags is None:
            flags = ""
        else:
            flags = " ({0})".format(describe(self.flags))

        return (
            "<{self.__class__.__name__}: {fieldName!r} "
            "{matchType} {fieldValue!r}{flags}>"
            .format(
                self=self,
                fieldName=describe(self.fieldName),
                matchType=describe(self.matchType),
                fieldValue=describe(self.fieldValue),
                flags=flags,
            )
        )
