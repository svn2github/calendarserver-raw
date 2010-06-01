# -*- test-case-name: txcaldav.calendarstore.test.test_scheduling -*-
##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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
from zope.interface.declarations import implements
from txcaldav.icalendarstore import ICalendarStore, ICalendarStoreTransaction, \
    ICalendarHome, ICalendar, ICalendarObject



class ImplicitSchedulingTransaction(object):
    """
    Wrapper around an L{ICalendarStoreTransaction}.
    """
    implements(ICalendarStoreTransaction)

    def __init__(self, transaction):
        """
        Initialize an L{ImplicitSchedulingTransaction}.

        @type transaction: L{ICalendarStoreTransaction}
        """
        self._transaction = transaction


    def abort(self):
        """
        Abort the underlying transaction.
        """
        return self._transaction.abort()


    def commit(self):
        """
        Commit the underlying transaction.
        """
        return self._transaction.commit()


    def calendarHomeWithUID(self, uid, create=False):
        # FIXME: 'create' flag
        newHome = self._transaction.calendarHomeWithUID(uid)
#        return ImplicitSchedulingCalendarHome(newHome, self)
        if newHome is None:
            return None
        else:
            # FIXME: relay transaction
            return ImplicitSchedulingCalendarHome(newHome, None)



class ImplicitSchedulingCalendarHome(object):
    implements(ICalendarHome)

    def __init__(self, calendarHome, transaction):
        """
        L{ImplicitSchedulingCalendarHome}
        """
        self._calendarHome = calendarHome
        self._transaction = transaction


    def uid(self):
        return self._calendarHome.uid()

    def properties(self): ""
        # FIXME: implement
        # return self._calendarHome.properties()

    def calendars(self): ""
        # FIXME: implement
    def createCalendarWithName(self, name):
        self._calendarHome.createCalendarWithName(name)
    def removeCalendarWithName(self, name): ""
        # FIXME: implement


    def calendarWithName(self, name):
        calendar = self._calendarHome.calendarWithName(name)
        if calendar is not None:
            return ImplicitSchedulingCalendar(self, calendar)
        else:
            return None



class ImplicitSchedulingCalendarObject(object):
    implements(ICalendarObject)
    def setComponent(self, component): ""
    def component(self): ""
    def iCalendarText(self): ""
    def uid(self): ""
    def componentType(self): ""
    def organizer(self): ""
    def properties(self):""



class ImplicitSchedulingCalendar(object):
    implements(ICalendar)

    def __init__(self, parentHome, subCalendar):
        self._parentHome = parentHome
        self._subCalendar = subCalendar

    def name(self):
        return self._subCalendar.name()

    def ownerCalendarHome(self):
        return self._parentHome
    def calendarObjects(self):
        # FIXME: wrap
        return self._subCalendar.calendarObjects()
    def calendarObjectWithUID(self, uid): ""
    def createCalendarObjectWithName(self, name, component):
        # FIXME: implement most of StoreCalendarObjectResource here!
        self._subCalendar.createCalendarObjectWithName(name, component)
    def removeCalendarObjectWithName(self, name):
        # FIXME: implement deletion logic here!
        return self._subCalendar.removeCalendarObjectWithName(name)
    def removeCalendarObjectWithUID(self, uid): ""
    def syncToken(self): ""
    def calendarObjectsInTimeRange(self, start, end, timeZone): ""
    def calendarObjectsSinceToken(self, token): ""
    def properties(self):
        # FIXME: probably need to wrap this as well
        return self._subCalendar.properties()


    def calendarObjectWithName(self, name):
        #FIXME: wrap
        return self._subCalendar.calendarObjectWithName(name)


class ImplicitSchedulingStore(object):
    """
    This is a wrapper around an L{ICalendarStore} that implements implicit
    scheduling.
    """

    implements(ICalendarStore)

    def __init__(self, calendarStore):
        """
        Create an L{ImplicitSchedulingStore} wrapped around another
        L{ICalendarStore} provider.
        """
        self._calendarStore = calendarStore


    def newTransaction(self):
        """
        Wrap an underlying L{ITransaction}.
        """
        return ImplicitSchedulingTransaction(
                    self._calendarStore.newTransaction())
