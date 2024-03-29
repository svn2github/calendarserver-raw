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

from twistedcaldav.directory.util import uuidFromName
from twistedcaldav.test.util import TestCase

uuid_namespace_dns = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"

class UUID (TestCase):
    def test_uuidFromName(self):
        self.assertEquals(
            uuidFromName(uuid_namespace_dns, "python.org"),
            "886313e1-3b8a-5372-9b90-0c9aee199e5d",
        )
