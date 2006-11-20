##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
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
#
# DRI: David Reid, dreid@apple.com
##

import os

import datetime, dateutil.tz

def purge(collection, purgeDate):
    """
    Recursively purge all events older than purgeDate.

    for VTODO: 
     * if completed
       * purge if it's dueDate is older than purgeDate.

    for V*:
     * purge if endDate is older than purgeDate
     """

    from twistedcaldav import ical

    collection = os.path.abspath(collection)

    files = []
    directories = []

    for child in os.listdir(collection):
        if child == '.db.sqlite':
            continue

        child = os.path.join(collection, child)

        if os.path.isdir(child):
            directories.append(child)

        elif os.path.isfile(child):
            files.append(child)

    for directory in directories:
        purge(directory, purgeDate)

    for fname in files:
        f = open(fname)

        try:
            component = ical.Component.fromStream(f)
        except ValueError:
            # Not a calendar file?
            continue

        f.close()

        endDate = component.mainComponent().getEndDateUTC()
        
        if component.resourceType() == 'VTODO':
            if component.mainComponent().hasProperty('COMPLETED'):
                endDate = component.mainComponent().getDueDateUTC()
            else:
                endDate = None

        if isinstance(endDate, datetime.datetime):
            endDate = endDate.date()

        if endDate:
            if purgeDate > endDate:
                print "Purging %s, %s, %s" % (component.resourceType(), 
                                               component.resourceUID(), 
                                               endDate.isoformat())
                os.remove(fname)


class PurgeAction(object):
    def __init__(self, config):
        self.config = config

    def run(self):
        assert os.path.exists(self.config['docroot'])

        calendarCollectionRoot = os.path.join(
            os.path.abspath(self.config['docroot']),
            'calendars')

        if self.config.params:
            collections = [os.path.join(calendarCollectionRoot, p) 
                           for p in self.config.params]
            
        else:
            collections = []

            for type in os.listdir(calendarCollectionRoot):
                tRoot = os.path.join(calendarCollectionRoot, type)

                for collection in os.listdir(tRoot):
                    collections.append(os.path.join(tRoot, collection))

        purgeDate = datetime.date.today()
        purgeDate = purgeDate - datetime.timedelta(self.config['days'])

        for collection in collections:
            purge(collection, purgeDate)
