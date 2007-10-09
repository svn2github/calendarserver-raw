##
# Copyright (c) 2007 Apple Inc. All rights reserved.
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
# DRI: Cyrus Daboo, cdaboo@apple.com
##

from taskscheduler import taskscheduler
from threading import Thread
import time

# Manage scheduling of client tasks.

class ClientScheduler(object):
    
    sleep = 0.1

    def __init__(self, num_threads=10, throttle=True):
        self.running = False
        self.clients = []
        self.tasks = taskscheduler(num_threads, throttle)
        self.thread = None
        
    def add(self, client):
        self.clients.append(client)
        client.start()

    def run(self):
        
        print "Starting client scheduler"
        self.running = True

        def do_work():
            while(self.running):
                try:
                    # Loop over each client and ask it whether it has a pending task
                    for client in self.clients:
                        pending = client.pending()
                        if pending:
                            for callback, args, kwargs in pending:
                                self.tasks.enqueue(callback, args, kwargs)
                    time.sleep(ClientScheduler.sleep)
                except:
                    pass
            
        # Generate a pool of threads
        self.thread = Thread(target=do_work)
        self.thread.start()
        
        print "Started client scheduler"
        
        self.tasks.run()

    def stop(self):
        print "Stopping client scheduler"
        self.running = False
        self.thread.join()
        self.tasks.stop()
        print "Stopped client scheduler"
