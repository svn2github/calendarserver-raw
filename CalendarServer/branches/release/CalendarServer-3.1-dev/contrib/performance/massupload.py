##
# Copyright (c) 2010 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

import sys, pickle

from twisted.internet import reactor
from twisted.internet.task import coiterate
from twisted.python.usage import UsageError
from twisted.python.log import err

from benchlib import select
from upload import UploadOptions, upload

class MassUploadOptions(UploadOptions):
    optParameters = [
        ("benchmarks", None, None, ""),
        ("statistics", None, None, "")]

    opt_statistic = None

    def parseArgs(self, filename):
        self['filename'] = filename
        UploadOptions.parseArgs(self)


def main():
    options = MassUploadOptions()
    try:
        options.parseOptions(sys.argv[1:])
    except UsageError, e:
        print e
        return 1

    fname = options['filename']
    raw = pickle.load(file(fname))

    if not options['benchmarks']:
        benchmarks = raw.keys()
    else:
        benchmarks = options['benchmarks'].split()

    def go():
        for benchmark in benchmarks:
            for param in raw[benchmark].keys():
                for statistic in options['statistics'].split():
                    stat, samples = select(
                        raw, benchmark, param, statistic)
                    samples = stat.squash(samples)
                    yield upload(
                        reactor, 
                        options['url'], options['project'],
                        options['revision'], options['revision-date'],
                        benchmark, param, statistic,
                        options['backend'], options['environment'],
                        samples)
                    
                    # This is somewhat hard-coded to the currently
                    # collected stats.
                    if statistic == 'SQL':
                        stat, samples = select(
                            raw, benchmark, param, 'execute')
                        samples = stat.squash(samples, 'count')
                        yield upload(
                            reactor, 
                            options['url'], options['project'],
                            options['revision'], options['revision-date'],
                            benchmark, param, statistic + 'count',
                            options['backend'], options['environment'],
                            samples)


    d = coiterate(go())
    d.addErrback(err, "Mass upload failed")
    reactor.callWhenRunning(d.addCallback, lambda ign: reactor.stop())
    reactor.run()
