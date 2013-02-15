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
Directory service module utilities.
"""

__all__ = [
    "MergedConstants",
    "uniqueResult",
    "describe",
    "iterFlags",
]

from types import FunctionType

from twisted.python.constants import NamedConstant

from twext.who.idirectory import DirectoryServiceError



class MergedConstants(object):
    """
    Work-around for the fact that Names is apparently not subclassable
    and doesn't provide a way to merge multiple Names classes.
    """
    def __init__(self, *containers):
        seenNames = set()
        myContainers = set()
        for container in containers:
            for constant in container.iterconstants():
                if constant.name in seenNames:
                    raise ValueError(
                        "Multiple constants with the same name may not be merged: %s"
                        % (constant.name,)
                    )
                seenNames.add(constant.name)

            if isinstance(container, MergedConstants):
                # Avoid nesting
                myContainers |= container._containers
            else:
                myContainers.add(container)

        self._containers = myContainers

    def __getattr__(self, name):
        for container in self._containers:
            attr = getattr(container, name, None)
            if attr is not None:
                # Named constant or static method
                if isinstance(attr, (NamedConstant, FunctionType)):
                    return attr

        raise AttributeError(name)

    def iterconstants(self):
        for container in self._containers:
            for constant in container.iterconstants():
                yield constant

    def lookupByName(self, name):
        for container in self._containers:
            try:
                return container.lookupByName(name)
            except ValueError:
                pass

        raise ValueError(name)



def uniqueResult(values):
    result = None
    for value in values:
        if result is None:
            result = value
        else:
            raise DirectoryServiceError("Multiple values found where one expected.")
    return result


def describe(constant):
    return getattr(constant, "description", str(constant))


def iterFlags(flags):
    if hasattr(flags, "__iter__"):
        return flags
    else:
        # Work around http://twistedmatrix.com/trac/ticket/6302
        # FIXME: This depends on a private attribute (flags._container)
        return (flags._container.lookupByName(name) for name in flags.names)
