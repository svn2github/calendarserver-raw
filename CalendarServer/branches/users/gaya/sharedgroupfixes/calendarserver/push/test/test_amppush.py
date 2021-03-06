##
# Copyright (c) 2011-2013 Apple Inc. All rights reserved.
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

from calendarserver.push.amppush import AMPPushMaster, AMPPushNotifierProtocol
from calendarserver.push.amppush import NotificationForID
from twistedcaldav.test.util import StoreTestCase
from twisted.internet.task import Clock
from calendarserver.push.util import PushPriority

class AMPPushMasterTests(StoreTestCase):

    def test_AMPPushMaster(self):

        # Set up the service
        clock = Clock()
        service = AMPPushMaster(None, None, 0, True, 3, reactor=clock)

        self.assertEquals(service.subscribers, [])

        client1 = TestProtocol(service)
        client1.subscribe("token1", "/CalDAV/localhost/user01/")
        client1.subscribe("token1", "/CalDAV/localhost/user02/")

        client2 = TestProtocol(service)
        client2.subscribe("token2", "/CalDAV/localhost/user01/")

        client3 = TestProtocol(service)
        client3.subscribe("token3", "any")

        service.addSubscriber(client1)
        service.addSubscriber(client2)
        service.addSubscriber(client3)

        self.assertEquals(len(service.subscribers), 3)

        self.assertTrue(client1.subscribedToID("/CalDAV/localhost/user01/"))
        self.assertTrue(client1.subscribedToID("/CalDAV/localhost/user02/"))
        self.assertFalse(client1.subscribedToID("nonexistent"))

        self.assertTrue(client2.subscribedToID("/CalDAV/localhost/user01/"))
        self.assertFalse(client2.subscribedToID("/CalDAV/localhost/user02/"))

        self.assertTrue(client3.subscribedToID("/CalDAV/localhost/user01/"))
        self.assertTrue(client3.subscribedToID("/CalDAV/localhost/user02/"))
        self.assertTrue(client3.subscribedToID("/CalDAV/localhost/user03/"))

        dataChangedTimestamp = 1354815999
        service.enqueue(None, "/CalDAV/localhost/user01/",
            dataChangedTimestamp=dataChangedTimestamp,
            priority=PushPriority.high)
        self.assertEquals(len(client1.history), 0)
        self.assertEquals(len(client2.history), 0)
        self.assertEquals(len(client3.history), 0)
        clock.advance(1)
        self.assertEquals(
            client1.history,
            [
                (
                    NotificationForID,
                    {
                        'id'                   : '/CalDAV/localhost/user01/',
                        'dataChangedTimestamp' : 1354815999,
                        'priority'             : PushPriority.high.value,
                    }
                )
            ]
        )
        self.assertEquals(len(client2.history), 0)
        self.assertEquals(len(client3.history), 0)
        clock.advance(3)
        self.assertEquals(
            client2.history,
            [
                (
                    NotificationForID,
                    {
                        'id'                   : '/CalDAV/localhost/user01/',
                        'dataChangedTimestamp' : 1354815999,
                        'priority'             : PushPriority.high.value,
                    }
                )
            ]
        )

        self.assertEquals(len(client3.history), 0)
        clock.advance(3)
        self.assertEquals(
            client3.history,
            [
                (
                    NotificationForID,
                    {
                        'id'                   : '/CalDAV/localhost/user01/',
                        'dataChangedTimestamp' : 1354815999,
                        'priority'             : PushPriority.high.value,
                    }
                )
            ]
        )

        client1.reset()
        client2.reset()
        client2.unsubscribe("token2", "/CalDAV/localhost/user01/")
        service.enqueue(None, "/CalDAV/localhost/user01/",
            dataChangedTimestamp=dataChangedTimestamp,
            priority=PushPriority.low)
        self.assertEquals(len(client1.history), 0)
        clock.advance(1)
        self.assertEquals(
            client1.history,
            [
                (
                    NotificationForID,
                    {
                        'id'                   : '/CalDAV/localhost/user01/',
                        'dataChangedTimestamp' : 1354815999,
                        'priority'             : PushPriority.low.value,
                    }
                )
            ]
        )

        self.assertEquals(len(client2.history), 0)
        clock.advance(3)
        self.assertEquals(len(client2.history), 0)

        # Turn off staggering
        service.scheduler = None
        client1.reset()
        client2.reset()
        client2.subscribe("token2", "/CalDAV/localhost/user01/")
        service.enqueue(None, "/CalDAV/localhost/user01/",
            dataChangedTimestamp=dataChangedTimestamp,
            priority=PushPriority.medium)
        self.assertEquals(
            client1.history,
            [
                (
                    NotificationForID,
                    {
                        'id'                   : '/CalDAV/localhost/user01/',
                        'dataChangedTimestamp' : 1354815999,
                        'priority'             : PushPriority.medium.value,
                    }
                )
            ]
        )
        self.assertEquals(
            client2.history,
            [
                (
                    NotificationForID,
                    {
                        'id'                   : '/CalDAV/localhost/user01/',
                        'dataChangedTimestamp' : 1354815999,
                        'priority'             : PushPriority.medium.value,
                    }
                )
            ]
        )



class TestProtocol(AMPPushNotifierProtocol):

    def __init__(self, service):
        super(TestProtocol, self).__init__(service)
        self.reset()


    def callRemote(self, cls, **kwds):
        self.history.append((cls, kwds))


    def reset(self):
        self.history = []
