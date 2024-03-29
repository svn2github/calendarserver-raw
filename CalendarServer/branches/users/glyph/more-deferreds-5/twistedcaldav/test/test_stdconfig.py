# -*- coding: utf-8 -*-
##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
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

from cStringIO import StringIO

from twisted.python.filepath import FilePath
from twisted.trial.unittest import TestCase

from twistedcaldav.config import Config
from twistedcaldav.stdconfig import NoUnicodePlistParser, PListConfigProvider

nonASCIIValue = "→←"
nonASCIIPlist = "<plist version='1.0'><string>%s</string></plist>" % (
    nonASCIIValue,
)

nonASCIIConfigPList = """
<plist version="1.0">
  <dict>
    <key>DataRoot</key>
    <string>%s</string>
  </dict>
</plist>
""" % (nonASCIIValue,)

class ConfigParsingTests(TestCase):
    """
    Tests to verify the behavior of the configuration parser.
    """

    def test_noUnicodePListParser(self):
        """
        L{NoUnicodePlistParser.parse} retrieves non-ASCII property list values
        as (UTF-8 encoded) 'str' objects, so that a single type is consistently
        used regardless of the input data.
        """
        parser = NoUnicodePlistParser()
        self.assertEquals(parser.parse(StringIO(nonASCIIPlist)),
                          nonASCIIValue)


    def test_parseNonASCIIConfig(self):
        """
        Non-ASCII <string>s found as part of a configuration file will be
        retrieved as UTF-8 encoded 'str' objects, as parsed by
        L{NoUnicodePlistParser}.
        """
        cfg = Config(PListConfigProvider({"DataRoot": ""}))
        tempfile = FilePath(self.mktemp())
        tempfile.setContent(nonASCIIConfigPList)
        cfg.load(tempfile.path)
        self.assertEquals(cfg.DataRoot, nonASCIIValue)



