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
Tests for parsing an SQL schema, which cover L{twext.enterprise.dal.model}
and L{twext.enterprise.dal.parseschema}.
"""

from twext.enterprise.dal.model import Schema

from twext.enterprise.dal.parseschema import addSQLToSchema
from twisted.trial.unittest import TestCase


class SchemaTestHelper(object):
    """
    Mix-in that can parse a schema from a string.
    """

    def schemaFromString(self, string):
        """
        Createa a L{Schema}
        """
        s = Schema(self.id())
        addSQLToSchema(s, string)
        return s



class ParsingExampleTests(TestCase, SchemaTestHelper):
    """
    Tests for parsing some sample schemas.
    """

    def test_simplest(self):
        """
        Parse an extremely simple schema with one table in it.
        """
        s = self.schemaFromString("create table foo (bar integer);")
        self.assertEquals(len(s.tables), 1)
        foo = s.tableNamed('foo')
        self.assertEquals(len(foo.columns), 1)
        bar = foo.columns[0]
        self.assertEquals(bar.name, "bar")
        self.assertEquals(bar.type.name, "integer")


    def test_stringTypes(self):
        """
        Table and column names should be byte strings.
        """
        s = self.schemaFromString("create table foo (bar integer);")
        self.assertEquals(len(s.tables), 1)
        foo = s.tableNamed('foo')
        self.assertIsInstance(foo.name, str)
        self.assertIsInstance(foo.columnNamed('bar').name, str)


    def test_typeWithLength(self):
        """
        Parse a type with a length.
        """
        s = self.schemaFromString("create table foo (bar varchar(6543))")
        bar = s.tableNamed('foo').columnNamed('bar')
        self.assertEquals(bar.type.name, "varchar")
        self.assertEquals(bar.type.length, 6543)


    def test_sequence(self):
        """
        Parsing a 'create sequence' statement adds a L{Sequence} to the
        L{Schema}.
        """
        s = self.schemaFromString("create sequence myseq;")
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
        s = self.schemaFromString(
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


    def test_sequenceDefault(self):
        """
        Default sequence column.
        """
        s = self.schemaFromString(
            """
            create sequence alpha;
            create table foo (
                bar integer default nextval('alpha') not null,
                qux integer not null
            );
            """)
        self.assertEquals(s.tableNamed("foo").columnNamed("bar").needsValue(),
                          False)


    def test_defaultConstantColumns(self):
        """
        Parsing a 'default' column with an appropriate type in it will return
        that type as the 'default' attribute of the Column object.
        """
        s = self.schemaFromString(
            """
            create table a (
                b integer default 4321,
                c boolean default false,
                d boolean default true,
                e varchar(255) default 'sample value',
                f varchar(255) default null
            );
            """)
        table = s.tableNamed("a")
        self.assertEquals(table.columnNamed("b").default, 4321)
        self.assertEquals(table.columnNamed("c").default, False)
        self.assertEquals(table.columnNamed("d").default, True)
        self.assertEquals(table.columnNamed("e").default, 'sample value')
        self.assertEquals(table.columnNamed("f").default, None)


    def test_needsValue(self):
        """
        Columns with defaults, or with a 'not null' constraint don't need a
        value; columns without one don't.
        """
        s = self.schemaFromString(
            """
            create table a (
                b integer default 4321 not null,
                c boolean default false,
                d integer not null,
                e integer
            )
            """)
        table = s.tableNamed("a")
        # Has a default, NOT NULL.
        self.assertEquals(table.columnNamed("b").needsValue(), False)
        # Has a default _and_ nullable.
        self.assertEquals(table.columnNamed("c").needsValue(), False)
        # No default, not nullable.
        self.assertEquals(table.columnNamed("d").needsValue(), True)
        # Just nullable.
        self.assertEquals(table.columnNamed("e").needsValue(), False)


    def test_notNull(self):
        """
        A column with a NOT NULL constraint in SQL will be parsed as a
        constraint which returns False from its C{canBeNull()} method.
        """
        s = self.schemaFromString(
            "create table alpha (beta integer, gamma integer not null);"
        )
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
            s = self.schemaFromString(identicalSchema)
            table = s.tableNamed('sample')
            column = table.columnNamed('example')
            self.assertEquals(list(table.uniques()), [[column]])


    def test_multiUnique(self):
        """
        A column with a UNIQUE constraint in SQL will result in the table
        listing that column as a unique set.
        """
        s = self.schemaFromString(
            "create table a (b integer, c integer, unique(b, c), unique(c));"
        )
        a = s.tableNamed('a')
        b = a.columnNamed('b')
        c = a.columnNamed('c')
        self.assertEquals(list(a.uniques()), [[b, c], [c]])


    def test_singlePrimaryKey(self):
        """
        A table with a multi-column PRIMARY KEY clause will be parsed as a list
        of a single L{Column} object and stored as a C{primaryKey} attribute on
        the L{Table} object.
        """
        s = self.schemaFromString(
            "create table a (b integer primary key, c integer)"
        )
        a = s.tableNamed("a")
        self.assertEquals(a.primaryKey, [a.columnNamed("b")])


    def test_multiPrimaryKey(self):
        """
        A table with a multi-column PRIMARY KEY clause will be parsed as a list
        C{primaryKey} attribute on the Table object.
        """
        s = self.schemaFromString(
            "create table a (b integer, c integer, primary key(b, c))"
        )
        a = s.tableNamed("a")
        self.assertEquals(
            a.primaryKey, [a.columnNamed("b"), a.columnNamed("c")]
        )


    def test_cascade(self):
        """
        A column with an 'on delete cascade' constraint will have its C{cascade}
        attribute set to True.
        """
        s = self.schemaFromString(
            """
            create table a (b integer primary key);
            create table c (d integer references a on delete cascade);
            """)
        self.assertEquals(s.tableNamed("a").columnNamed("b").cascade, False)
        self.assertEquals(s.tableNamed("c").columnNamed("d").cascade, True)


    def test_indexes(self):
        """
        A 'create index' statement will add an L{Index} object to a L{Schema}'s
        C{indexes} list.
        """
        s = self.schemaFromString(
            """
            create table q (b integer); -- noise
            create table a (b integer primary key, c integer);
            create table z (c integer); -- make sure we get the right table

            create index idx_a_b on a(b);
            create index idx_a_b_c on a(c, b);
            """)

        a = s.tableNamed("a")
        b = s.indexNamed("idx_a_b")
        bc = s.indexNamed('idx_a_b_c')
        self.assertEquals(b.table, a)
        self.assertEquals(b.columns, [a.columnNamed("b")])
        self.assertEquals(bc.table, a)
        self.assertEquals(bc.columns, [a.columnNamed("c"), a.columnNamed("b")])


