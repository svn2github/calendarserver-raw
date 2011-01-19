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


from txdav.base.datastore.sqlmodel import Schema
from txdav.base.datastore.sqlparser import addSQLToSchema
from txdav.base.datastore.sqlsyntax import SchemaSyntax, Select

from twisted.trial.unittest import TestCase

class GenerationTests(TestCase):
    """
    Tests for syntactic helpers to generate SQL queries.
    """

    def setUp(self):
        s = Schema(self.id())
        addSQLToSchema(schema=s, schemaData="""
                       create table FOO (BAR integer);
                       create table BAZ (QUX integer);
                       """)
        self.schema = SchemaSyntax(s)


    def test_simplestSelect(self):
        """
        L{Select} generates a 'select' statement, by default, asking for all
        rows in a table.
        """
        self.assertEquals(Select(From=self.schema.FOO).toSQL(),
                          ("select * from FOO", []))


    def test_simpleWhereClause(self):
        """
        L{Select} generates a 'select' statement with a 'where' clause
        containing an expression.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR == 1).toSQL(),
                          ("select * from FOO where BAR = ?", [1]))


    def test_quotingAndPlaceholder(self):
        """
        L{Select} generates a 'select' statement with the specified placeholder
        syntax and quoting function.
        """

        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR == 1).toSQL(
                                 placeholder="*",
                                 quote=lambda partial: partial.replace("*", "**")),
                          ("select ** from FOO where BAR = *", [1]))

