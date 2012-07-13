##
# Copyright (c) 2012 Apple Inc. All rights reserved.
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
Common invitation interface
"""

from zope.interface.interface import Interface


__all__ = [
    "IInvite",
]

class IInvite(Interface):
    """
    Invite.  The state of the an invitation to a shared resource.
    
    """
  
def uid():
        """
        Unique identifier for this invitation.  Randomly generated.

        @return: the invite unique identifier
        @rtype: C{str}
        """

def shareeUID():
        """
        Sharee's unique identifier.

        @return: the Sharee's unique identifier.
        @rtype: C{str}
        """

def sharerUID():
        """
        Sharer's unique identifier.

        @return: the Sharer's unique identifier.
        @rtype: C{str}
        """

def sharerAccess():
        """
        Sharer's access.  Currently, one of "own", "read-only", or "read-write".

        @return: the Sharer's access to the shared resource
        @rtype: C{str}
        """

def state():
        """
        Invitation or bind state.  Currently, one of "NEEDS-ACTION","ACCEPTED", "DECLINED", "INVALID".

        @return: the invitation state
        @rtype: C{str}
        """

def summary():
        """
       The shared resource's name, purpose, or description.

        @return: the summary
        @rtype: C{str}
        """
