##
# Copyright (c) 2007-2008 Apple Inc. All rights reserved.
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

from caldavclientlibrary.browser.command import Command
from caldavclientlibrary.browser.command import WrongOptions

class Cmd(Command):

    def __init__(self):
        super(Command, self).__init__()
        self.cmds = ("history",)


    def execute(self, name, options):
        if options:
            print self.usage(name)
            raise WrongOptions()

        format = "%%0%ds %%s" % (len(self.shell.history),)
        for ctr, cmd in enumerate(self.shell.history):
            print format % (ctr + 1, cmd,)
        return True


    def usage(self, name):
        return """Usage: %s
""" % (name,)


    def helpDescription(self):
        return "Displays the history of all commands used in this session."
