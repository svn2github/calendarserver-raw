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
from twistedcaldav.directory.calendaruserproxy import CalendarUserProxyDatabase

import os

import twistedcaldav.test.util

class ProxyPrincipalDB (twistedcaldav.test.util.TestCase):
    """
    Directory service provisioned principals.
    """
    
    class old_CalendarUserProxyDatabase(CalendarUserProxyDatabase):
        
        def _db_version(self):
            """
            @return: the schema version assigned to this index.
            """
            return "3"
            
        def _db_init_data_tables(self, q):
            """
            Initialise the underlying database tables.
            @param q:           a database cursor to use.
            """
    
            #
            # GROUPS table
            #
            q.execute(
                """
                create table GROUPS (
                    GROUPNAME   text,
                    MEMBER      text
                )
                """
            )

    class new_CalendarUserProxyDatabase(CalendarUserProxyDatabase):
        
        def _db_version(self):
            """
            @return: the schema version assigned to this index.
            """
            return "11"
            
    class newer_CalendarUserProxyDatabase(CalendarUserProxyDatabase):
        
        def _db_version(self):
            """
            @return: the schema version assigned to this index.
            """
            return "51"
            
    def test_normalDB(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = CalendarUserProxyDatabase(db_path)
        db.setGroupMembers("A", ("B", "C", "D",))
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))

    def test_DBIndexed(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = CalendarUserProxyDatabase(db_path)
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set(("GROUPNAMES", "MEMBERS")))

    def test_OldDB(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = self.old_CalendarUserProxyDatabase(db_path)
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set())

    def test_DBUpgrade(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = self.old_CalendarUserProxyDatabase(db_path)
        db.setGroupMembers("A", ("B", "C", "D",))
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set())
        db._db_close()
        db = None
        
        db = CalendarUserProxyDatabase(db_path)
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set(("GROUPNAMES", "MEMBERS")))
        db._db_close()
        db = None

    def test_DBUpgradeNewer(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = self.old_CalendarUserProxyDatabase(db_path)
        db.setGroupMembers("A", ("B", "C", "D",))
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set())
        db._db_close()
        db = None
        
        db = self.new_CalendarUserProxyDatabase(db_path)
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set(("GROUPNAMES", "MEMBERS")))
        db._db_close()
        db = None

    def test_DBNoUpgradeNewer(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = self.new_CalendarUserProxyDatabase(db_path)
        db.setGroupMembers("A", ("B", "C", "D",))
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set(("GROUPNAMES", "MEMBERS")))
        db._db_close()
        db = None
        
        db = self.newer_CalendarUserProxyDatabase(db_path)
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(set([row[1] for row in db._db_execute("PRAGMA index_list(GROUPS)")]), set(("GROUPNAMES", "MEMBERS")))
        db._db_close()
        db = None

    def test_cachingDBInsert(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = CalendarUserProxyDatabase(db_path)
        
        # Do one insert and check the result
        db.setGroupMembers("A", ("B", "C", "D",))
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(db.getMemberships("C"), set(("A",)))
        self.assertEqual(db.getMemberships("D"), set(("A",)))
        self.assertEqual(db.getMemberships("E"), set(()))
        
        # Change and check the result
        db.setGroupMembers("A", ("B", "C", "E",))
        self.assertEqual(db.getMembers("A"), set(("B", "C", "E",)))
        self.assertEqual(db.getMemberships("B"), set(("A",)))
        self.assertEqual(db.getMemberships("C"), set(("A",)))
        self.assertEqual(db.getMemberships("D"), set())
        self.assertEqual(db.getMemberships("E"), set(("A",)))

    def test_cachingDBRemove(self):
    
        # Get the DB
        db_path = self.mktemp()
        os.mkdir(db_path)
        db = CalendarUserProxyDatabase(db_path)
        
        # Do one insert and check the result
        db.setGroupMembers("A", ("B", "C", "D",))
        db.setGroupMembers("X", ("B", "C",))
        self.assertEqual(db.getMembers("A"), set(("B", "C", "D",)))
        self.assertEqual(db.getMembers("X"), set(("B", "C",)))
        self.assertEqual(db.getMemberships("B"), set(("A", "X",)))
        self.assertEqual(db.getMemberships("C"), set(("A", "X",)))
        self.assertEqual(db.getMemberships("D"), set(("A",)))
        
        # Remove and check the result
        db.removeGroup("A")
        self.assertEqual(db.getMembers("A"), set())
        db.setGroupMembers("X", ("B", "C",))
        self.assertEqual(db.getMemberships("B"), set("X",))
        self.assertEqual(db.getMemberships("C"), set("X",))
        self.assertEqual(db.getMemberships("D"), set())
        