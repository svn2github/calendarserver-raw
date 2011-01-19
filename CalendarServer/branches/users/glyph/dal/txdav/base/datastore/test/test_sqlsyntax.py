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
Tests for L{txdav.base.datastore.sqlsyntax}
"""

from txdav.base.datastore.sqlmodel import Schema
from txdav.base.datastore.sqlparser import addSQLToSchema
from txdav.base.datastore.sqlsyntax import (
    SchemaSyntax, Select, SQLStatement, TableMismatch)

from twisted.trial.unittest import TestCase

class GenerationTests(TestCase):
    """
    Tests for syntactic helpers to generate SQL queries.
    """

    def setUp(self):
        s = Schema(self.id())
        addSQLToSchema(schema=s, schemaData="""
                       create table FOO (BAR integer, BAZ integer);
                       create table BOZ (QUX integer);
                       create table OTHER (BAR integer, FOO_BAR integer);
                       """)
        self.schema = SchemaSyntax(s)


    def test_simplestSelect(self):
        """
        L{Select} generates a 'select' statement, by default, asking for all
        rows in a table.
        """
        self.assertEquals(Select(From=self.schema.FOO).toSQL(),
                          SQLStatement("select * from FOO", []))


    def test_simpleWhereClause(self):
        """
        L{Select} generates a 'select' statement with a 'where' clause
        containing an expression.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR == 1).toSQL(),
                          SQLStatement("select * from FOO where BAR = ?", [1]))


    def test_quotingAndPlaceholder(self):
        """
        L{Select} generates a 'select' statement with the specified placeholder
        syntax and quoting function.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR == 1).toSQL(
                                 placeholder="*",
                                 quote=lambda partial: partial.replace("*", "**")),
                          SQLStatement("select ** from FOO where BAR = *", [1]))


    def test_columnComparison(self):
        """
        L{Select} generates a 'select' statement which compares columns.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR ==
                                 self.schema.FOO.BAZ).toSQL(),
                          SQLStatement("select * from FOO where BAR = BAZ", []))


    def test_comparisonTestErrorPrevention(self):
        """
        The comparison object between columns raises an exception when compared
        for a truth value, so that code will not accidentally test for '==' and
        always get a true value.
        """
        def sampleComparison():
            if self.schema.FOO.BAR == self.schema.FOO.BAZ:
                return 'comparison should not succeed'
        self.assertRaises(ValueError, sampleComparison)


    def test_compoundWhere(self):
        """
        L{Select.And} and L{Select.Or} will return compound columns.
        """
        self.assertEquals(
            Select(From=self.schema.FOO,
                   Where=(self.schema.FOO.BAR < 2).Or(
                          self.schema.FOO.BAR > 5)).toSQL(),
            SQLStatement("select * from FOO where BAR < ? or BAR > ?", [2, 5]))


    def test_joinClause(self):
        """
        A table's .join() method returns a join statement in a SELECT.
        """
        self.assertEquals(
            Select(From=self.schema.FOO.join(
                self.schema.BOZ, self.schema.FOO.BAR ==
                self.schema.BOZ.QUX)).toSQL(),
            SQLStatement("select * from FOO join BOZ on BAR = QUX", [])
        )


    def test_columnSelection(self):
        """
        If a column is specified by the argument to L{Select}, those will be
        output by the SQL statement rather than the all-columns wildcard.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAR],
                   From=self.schema.FOO).toSQL(),
            SQLStatement("select BAR from FOO")
        )


    def test_multiColumnSelection(self):
        """
        If multiple columns are specified by the argument to L{Select}, those
        will be output by the SQL statement rather than the all-columns
        wildcard.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAZ,
                    self.schema.FOO.BAR],
                   From=self.schema.FOO).toSQL(),
            SQLStatement("select BAZ, BAR from FOO")
        )


    def test_joinColumnSelection(self):
        """
        If multiple columns are specified by the argument to L{Select} that uses
        a L{TableSyntax.join}, those will be output by the SQL statement.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAZ,
                    self.schema.BOZ.QUX],
                   From=self.schema.FOO.join(self.schema.BOZ,
                                             self.schema.FOO.BAR ==
                                             self.schema.BOZ.QUX)).toSQL(),
            SQLStatement("select BAZ, QUX from FOO join BOZ on BAR = QUX")
        )


    def test_tableMismatch(self):
        """
        When a column in the 'columns' argument does not match the table from
        the 'From' argument, L{Select} raises a L{TableMismatch}.
        """
        self.assertRaises(TableMismatch, Select, [self.schema.BOZ.QUX],
                          From=self.schema.FOO)


    def test_qualifyNames(self):
        """
        When two columns in the FROM clause requested from different tables have
        the same name, the emitted SQL should explicitly disambiguate them.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAR,
                    self.schema.OTHER.BAR],
                   From=self.schema.FOO.join(self.schema.OTHER,
                                             self.schema.OTHER.FOO_BAR ==
                                             self.schema.FOO.BAR)).toSQL(),
            SQLStatement(
                "select FOO.BAR, OTHER.BAR from FOO "
                "join OTHER on FOO_BAR == FOO.BAR"
            )
        )

