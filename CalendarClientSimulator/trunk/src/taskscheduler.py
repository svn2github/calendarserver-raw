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
from threading import Thread
import Queue

# Schedule calendar client tasks and execute via a thread pool.

class taskscheduler(object):
    
    def __init__(self, num_threads=10, throttle=True):
        self.queue = Queue.Queue()
        self.num_threads = num_threads
        self.threads = []
        self.busy = []
        self.running = False
        self.throttle = throttle
        self.throttling = False
        
    def enqueue(self, callback, *args, **kwargs):
        if self.throttling:
            busy_number = sum(self.busy)
            print "Cannot add task to queue: scheduler is too busy: %d" % (busy_number,)
        elif self.running:
            try:
                self.queue.put(("run", (callback, args, kwargs)), False)
            except Queue.Full:
                print "Cannot add task to queue: queue is full"
        else:
            print "Cannot add task to queue: scheduler has been shut down."

    def run(self):
        
        print "Starting threaded scheduler"
        self.running = True

        def do_work(thread_num):
            loop = True
            while(loop):
                try:
                    cmd, item = self.queue.get(self.running, 10)
                    self.busy[thread_num] = 1
                    qsize = self.queue.qsize()
                    busy_number = sum(self.busy)
                    if qsize > 0:
                        print "Queue size = %d, Busy threads = %d" % (qsize, busy_number,)
                        if busy_number == self.num_threads:
                            print "*** WARNING: all threads busy"
                            if qsize >= self.num_threads * 2:
                                print "*** WARNING: throttling the task queue"
                                self.throttling = True
                                
                    if cmd == "run":
                        callback, args, kwargs = item
                        try:
                            callback(*args, **kwargs)
                        except Exception, e:
                            print "Thread task exception: %s" % (e,)
                    elif cmd == "stop":
                        print "Thread #%d stopping." % (thread_num,)
                        loop = False
                    self.queue.task_done()
                    self.busy[thread_num] = 0
                    busy_number = sum(self.busy)

                    if self.throttling and busy_number < self.num_threads/2:
                        print "*** WARNING: no longer throttling the task queue"
                        self.throttling = False

                except Queue.Empty:
                    busy_number = sum(self.busy)
                    if self.throttling and busy_number < self.num_threads/2:
                        print "*** WARNING: no longer throttling the task queue"
                        self.throttling = False
                    if not self.running:
                        break
            
        # Generate a pool of threads
        for counter in range(self.num_threads):
            t = Thread(target=do_work, args=(counter,))
            t.start()
            self.threads.append(t)
            self.busy.append(0)
            print "Started thread: #%d" % (counter + 1,)
        
        print "Started threaded scheduler"

    def stop(self):
        print "Stopping threaded scheduler"
        self.running = False
        for ctr, thread in enumerate(self.threads):
            thread.join()
            print "Stopped thread: #%d" % (ctr + 1,)
        print "Stopped threaded scheduler"
