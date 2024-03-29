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

"""
Tests for txdav.base.datastore.subpostgres.
"""

from twisted.trial.unittest import TestCase

from twext.python.filepath import CachingFilePath

from txdav.base.datastore.subpostgres import PostgresService
from twisted.internet.defer import inlineCallbacks, Deferred
from twisted.application.service import Service

class SubprocessStartup(TestCase):
    """
    Tests for starting and stopping the subprocess.
    """

    @inlineCallbacks
    def test_startService(self):
        """
        Assuming a properly configured environment ($PATH points at an 'initdb'
        and 'postgres', $PYTHONPATH includes pgdb), starting a
        L{PostgresService} will start the service passed to it, after executing
        the schema.
        """

        test = self
        class SimpleService(Service):

            instances = []
            rows = []
            ready = Deferred()

            def __init__(self, connectionFactory):
                self.connection = connectionFactory()
                test.addCleanup(self.connection.close)
                self.instances.append(self)


            def startService(self):
                cursor = self.connection.cursor()
                try:
                    cursor.execute(
                        "insert into test_dummy_table values ('dummy')"
                    )
                except:
                    self.ready.errback()
                else:
                    self.ready.callback(None)
                finally:
                    cursor.close()


        dbPath = "../_postgres_test_db"
        svc = PostgresService(
            CachingFilePath(dbPath),
            SimpleService,
            "create table TEST_DUMMY_TABLE (stub varchar)",
            "dummy_db",
            testMode=True
        )

        svc.startService()
        self.addCleanup(svc.stopService)
        yield SimpleService.ready
        connection = SimpleService.instances[0].connection
        cursor = connection.cursor()
        cursor.execute("select * from test_dummy_table")
        values = cursor.fetchall()
        self.assertEquals(values, [["dummy"]])
