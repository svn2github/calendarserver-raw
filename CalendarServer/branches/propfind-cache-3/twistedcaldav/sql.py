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

"""
Generic SQL database access object.
"""

__all__ = [
    "AbstractSQLDatabase",
]

import os

try:
    import sqlite3 as sqlite
except ImportError:
    from pysqlite2 import dbapi2 as sqlite

from twistedcaldav.log import Logger

log = Logger()

db_prefix = ".db."

class AbstractSQLDatabase(object):
    """
    A generic SQL database.
    """

    def __init__(self, dbpath, persistent, autocommit=False):
        """
        
        @param dbpath: the path where the db file is stored.
        @type dbpath: str
        @param persistent: C{True} if the data in the DB must be perserved during upgrades,
            C{False} if the DB data can be re-created from an external source.
        @type persistent: bool
        @param autocommit: C{True} if auto-commit mode is desired, C{False} otherwise
        @type autocommit: bool
        """
        self.dbpath = dbpath
        self.persistent = persistent
        self.autocommit = autocommit

    def _db_version(self):
        """
        @return: the schema version assigned to this index.
        """
        raise NotImplementedError
        
    def _db_type(self):
        """
        @return: the collection type assigned to this index.
        """
        raise NotImplementedError
        
    def _db(self):
        """
        Access the underlying database.
        @return: a db2 connection object for this index's underlying data store.
        """
        if not hasattr(self, "_db_connection"):
            db_filename = self.dbpath
            try:
                if self.autocommit:
                    self._db_connection = sqlite.connect(db_filename, isolation_level=None)
                else:
                    self._db_connection = sqlite.connect(db_filename)
            except:
                log.err("Unable to open database: %s" % (db_filename,))
                raise

            #
            # Set up the schema
            #
            q = self._db_connection.cursor()
            try:
                # Create CALDAV table if needed

                if self._test_schema_table(q):
                    q.execute(
                        """
                        select VALUE from CALDAV
                         where KEY = 'SCHEMA_VERSION'
                        """)
                    version = q.fetchone()

                    if version is not None: version = version[0]

                    q.execute(
                        """
                        select VALUE from CALDAV
                         where KEY = 'TYPE'
                        """)
                    type = q.fetchone()

                    if type is not None: type = type[0]

                    if (version != self._db_version()) or (type != self._db_type()):

                        # Clean-up first
                        q.close()
                        q = None
                        self._db_connection.close()
                        del(self._db_connection)

                        if version != self._db_version():
                            log.err("Database %s has different schema (v.%s vs. v.%s)"
                                    % (db_filename, version, self._db_version()))
                            
                            # Upgrade the DB
                            return self._db_upgrade(version)

                        if type != self._db_type():
                            log.err("Database %s has different type (%s vs. %s)"
                                    % (db_filename, type, self._db_type()))

                            # Delete this index and start over
                            os.remove(db_filename)
                            return self._db()

                else:
                    self._db_init(db_filename, q)

                self._db_connection.commit()
            finally:
                if q is not None: q.close()
        return self._db_connection

    def _test_schema_table(self, q):
        q.execute("""
        select (1) from SQLITE_MASTER
         where TYPE = 'table' and NAME = 'CALDAV'
        """)
        return q.fetchone()

    def _db_init(self, db_filename, q):
        """
        Initialise the underlying database tables.
        @param db_filename: the file name of the index database.
        @param q:           a database cursor to use.
        """
        log.msg("Initializing database %s" % (db_filename,))

        # We need an exclusive lock here as we are making a big change to the database and we don't
        # want other processes to get stomped on or stomp on us.
        old_isolation = self._db_connection.isolation_level
        self._db_connection.isolation_level = None
        q.execute("begin exclusive transaction")
        
        # We re-check whether the schema table is present again AFTER we've got an exclusive
        # lock as some other server process may have snuck in and already created it
        # before we got the lock, or whilst we were waiting for it.
        if not self._test_schema_table(q):
            self._db_init_schema_table(q)
            self._db_init_data_tables(q)
            self._db_recreate()

        q.execute("commit")
        self._db_connection.isolation_level = old_isolation

    def _db_init_schema_table(self, q):
        """
        Initialise the underlying database tables.
        @param db_filename: the file name of the index database.
        @param q:           a database cursor to use.
        """

        #
        # CALDAV table keeps track of our schema version and type
        #
        q.execute(
            """
            create table CALDAV (
                KEY text unique, VALUE text unique
            )
            """
        )
        q.execute(
            """
            insert into CALDAV (KEY, VALUE)
            values ('SCHEMA_VERSION', :1)
            """, [self._db_version()]
        )
        q.execute(
            """
            insert into CALDAV (KEY, VALUE)
            values ('TYPE', :1)
            """, [self._db_type()]
        )

    def _db_init_data_tables(self, q):
        """
        Initialise the underlying database tables.
        @param db_filename: the file name of the index database.
        @param q:           a database cursor to use.
        """
        raise NotImplementedError

    def _db_recreate(self):
        """
        Recreate the database tables.
        """
        pass

    def _db_upgrade(self, old_version):
        """
        Upgrade the database tables.
        """
        
        if self.persistent:
            self._db_connection = sqlite.connect(self.dbpath, isolation_level=None)
            q = self._db_connection.cursor()
            self._db_upgrade_data_tables(q, old_version)
            self._db_upgrade_schema(q)
            self._db_close()
            return self._db()
        else:
            # Non-persistent DB's by default can be removed and re-created. However, for simple
            # DB upgrades they SHOULD override this method and handle those for better performance.
            os.remove(self.dbpath)
            return self._db()
    
    def _db_upgrade_data_tables(self, q, old_version):
        """
        Upgrade the data from an older version of the DB.
        """
        # Persistent DB's MUST override this method and do a proper upgrade. Their data
        # cannot be thrown away.
        raise NotImplementedError("Persistent databases MUST support an upgrade method.")

    def _db_upgrade_schema(self, q):
        """
        Upgrade the stored schema version to the current one.
        """
        q.execute(
            """
            insert or replace into CALDAV (KEY, VALUE)
            values ('SCHEMA_VERSION', :1)
            """, [self._db_version()]
        )

    def _db_close(self):
        if hasattr(self, "_db_connection"):
            self._db_connection.close()
            del self._db_connection

    def _db_values_for_sql(self, sql, *query_params):
        """
        Execute an SQL query and obtain the resulting values.
        @param sql: the SQL query to execute.
        @param query_params: parameters to C{sql}.
        @return: an interable of values in the first column of each row
            resulting from executing C{sql} with C{query_params}.
        @raise AssertionError: if the query yields multiple columns.
        """
        return (row[0] for row in self._db_execute(sql, *query_params))

    def _db_value_for_sql(self, sql, *query_params):
        """
        Execute an SQL query and obtain a single value.
        @param sql: the SQL query to execute.
        @param query_params: parameters to C{sql}.
        @return: the value resulting from the executing C{sql} with
            C{query_params}.
        @raise AssertionError: if the query yields multiple rows or columns.
        """
        value = None
        for row in self._db_values_for_sql(sql, *query_params):
            assert value is None, "Multiple values in DB for %s %s" % (sql, query_params)
            value = row
        return value

    def _db_execute(self, sql, *query_params):
        """
        Execute an SQL query and obtain the resulting values.
        @param sql: the SQL query to execute.
        @param query_params: parameters to C{sql}.
        @return: an interable of tuples for each row resulting from executing
            C{sql} with C{query_params}.
        """
        q = self._db().cursor()
        try:
            try:
                q.execute(sql, query_params)
            except:
                log.err("Exception while executing SQL: %r %r" % (sql, query_params))
                raise
            return q.fetchall()
        finally:
            q.close()

    def _db_commit  (self): self._db_connection.commit()
    def _db_rollback(self): self._db_connection.rollback()
