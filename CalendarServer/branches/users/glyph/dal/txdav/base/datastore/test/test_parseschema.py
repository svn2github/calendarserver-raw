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
Tests for parsing an SQL schema, which cover L{txdav.base.datastore.sqlmodel}
and L{txdav.base.datastore.sqlparser}.
"""

from txdav.base.datastore.sqlmodel import Schema

from txdav.base.datastore.sqlparser import addSQLToSchema
from twisted.trial.unittest import TestCase


class ParsingExampleTests(TestCase):
    """
    Tests for parsing some sample schemas.
    """

    def test_simplest(self):
        """
        Parse an extremely simple schema with one table in it.
        """
        s = Schema()
        addSQLToSchema(s, "create table foo (bar integer);")
        self.assertEquals(len(s.tables), 1)
        foo = s.tableNamed('foo')
        self.assertEquals(len(foo.columns), 1)
        bar = foo.columns[0]
        self.assertEquals(bar.name, "bar")
        self.assertEquals(bar.type.name, "integer")


    def test_sequence(self):
        """
        Parsing a 'create sequence' statement adds a L{Sequence} to the
        L{Schema}.
        """
        s = Schema()
        addSQLToSchema(s, "create sequence myseq;")
        self.assertEquals(len(s.sequences), 1)
        self.assertEquals(s.sequences[0].name, "myseq")


    def test_sequenceColumn(self):
        """
        Parsing a 'create sequence' statement adds a L{Sequence} to the
        L{Schema}, and then a table that contains a column which uses the SQL
        C{nextval()} function to retrieve its default value from that sequence,
        will cause the L{Column} object to refer to the L{Sequence} and vice
        versa.
        """
        s = Schema()
        addSQLToSchema(s,
                       """
                       create sequence thingy;
                       create table thetable (
                           thecolumn integer default nextval('thingy')
                       );
                       """)
        self.assertEquals(len(s.sequences), 1)
        self.assertEquals(s.sequences[0].name, "thingy")
        self.assertEquals(s.tables[0].columns[0].default, s.sequences[0])
        self.assertEquals(s.sequences[0].referringColumns,
                          [s.tables[0].columns[0]])


    def test_notNull(self):
        """
        A column with a NOT NULL constraint in SQL will be parsed as a
        constraint which returns False from its C{canBeNull()} method.
        """
        s = Schema()
        addSQLToSchema(s,
                       """
                       create table alpha (beta integer,
                                           gamma integer not null);
                       """)
        t = s.tableNamed('alpha')
        self.assertEquals(True, t.columnNamed('beta').canBeNull())
        self.assertEquals(False, t.columnNamed('gamma').canBeNull())


    def test_unique(self):
        """
        A column with a UNIQUE constraint in SQL will result in the table
        listing that column as a unique set.
        """
        for identicalSchema in [
                "create table sample (example integer unique);",
                "create table sample (example integer, unique(example));"]:
            s = Schema()
            addSQLToSchema(s, identicalSchema)
            table = s.tableNamed('sample')
            column = table.columnNamed('example')
            self.assertEquals(list(table.uniques()), [set([column])])



