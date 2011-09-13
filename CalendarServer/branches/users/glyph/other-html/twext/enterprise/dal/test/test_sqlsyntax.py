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
Tests for L{twext.enterprise.dal.syntax}
"""

from twext.enterprise.dal.model import Schema
from twext.enterprise.dal.parseschema import addSQLToSchema
from twext.enterprise.dal import syntax
from twext.enterprise.dal.syntax import (
    SchemaSyntax, Select, Insert, Update, Delete, Lock, SQLFragment,
    TableMismatch, Parameter, Max, Len, NotEnoughValues
, Savepoint, RollbackToSavepoint, ReleaseSavepoint, SavepointAction)

from twext.enterprise.dal.syntax import Function

from twext.enterprise.dal.syntax import FixedPlaceholder, NumericPlaceholder
from twext.enterprise.ienterprise import POSTGRES_DIALECT, ORACLE_DIALECT
from twext.enterprise.test.test_adbapi2 import ConnectionFactory
from twext.enterprise.adbapi2 import ConnectionPool
from twext.enterprise.test.test_adbapi2 import resultOf
from twext.enterprise.test.test_adbapi2 import FakeThreadHolder
from twisted.internet.defer import succeed
from twisted.trial.unittest import TestCase



class _FakeTransaction(object):
    """
    An L{IAsyncTransaction} that provides the relevant metadata for SQL
    generation.
    """

    def __init__(self, paramstyle):
        self.paramstyle = 'qmark'



class FakeCXOracleModule(object):
    NUMBER = 'the NUMBER type'
    STRING = 'a string type (for varchars)'
    NCLOB = 'the NCLOB type. (for text)'
    TIMESTAMP = 'for timestamps!'



class NullTestingOracleTxn(object):
    """
    Fake transaction for testing oracle NULL behavior.
    """

    dialect = ORACLE_DIALECT
    paramstyle = 'numeric'

    def execSQL(self, text, params, exc):
        return succeed([[None, None]])

class GenerationTests(TestCase):
    """
    Tests for syntactic helpers to generate SQL queries.
    """

    def setUp(self):
        s = Schema(self.id())
        addSQLToSchema(schema=s, schemaData="""
                       create sequence A_SEQ;
                       create table FOO (BAR integer, BAZ varchar(255));
                       create table BOZ (QUX integer, QUUX integer);
                       create table OTHER (BAR integer,
                                           FOO_BAR integer not null);
                       create table TEXTUAL (MYTEXT varchar(255));
                       create table LEVELS (ACCESS integer,
                                            USERNAME varchar(255));
                       create table NULLCHECK (ASTRING varchar(255) not null,
                                               ANUMBER integer);
                       """)
        self.schema = SchemaSyntax(s)


    def test_simplestSelect(self):
        """
        L{Select} generates a 'select' statement, by default, asking for all
        rows in a table.
        """
        self.assertEquals(Select(From=self.schema.FOO).toSQL(),
                          SQLFragment("select * from FOO", []))


    def test_simpleWhereClause(self):
        """
        L{Select} generates a 'select' statement with a 'where' clause
        containing an expression.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR == 1).toSQL(),
                          SQLFragment("select * from FOO where BAR = ?", [1]))


    def test_alternateMetadata(self):
        """
        L{Select} generates a 'select' statement with the specified placeholder
        syntax when explicitly given L{ConnectionMetadata} which specifies a
        placeholder.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR == 1).toSQL(
                                 FixedPlaceholder(POSTGRES_DIALECT, "$$")),
                          SQLFragment("select * from FOO where BAR = $$", [1]))


    def test_columnComparison(self):
        """
        L{Select} generates a 'select' statement which compares columns.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR ==
                                 self.schema.FOO.BAZ).toSQL(),
                          SQLFragment("select * from FOO where BAR = BAZ", []))


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


    def test_compareWithNULL(self):
        """
        Comparing a column with None results in the generation of an 'is null'
        or 'is not null' SQL statement.
        """
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR ==
                                 None).toSQL(),
                          SQLFragment(
                              "select * from FOO where BAR is null", []))
        self.assertEquals(Select(From=self.schema.FOO,
                                 Where=self.schema.FOO.BAR !=
                                 None).toSQL(),
                          SQLFragment(
                              "select * from FOO where BAR is not null", []))


    def test_compoundWhere(self):
        """
        L{Select.And} and L{Select.Or} will return compound columns.
        """
        self.assertEquals(
            Select(From=self.schema.FOO,
                   Where=(self.schema.FOO.BAR < 2).Or(
                          self.schema.FOO.BAR > 5)).toSQL(),
            SQLFragment("select * from FOO where BAR < ? or BAR > ?", [2, 5]))


    def test_orderBy(self):
        """
        L{Select}'s L{OrderBy} parameter generates an 'order by' clause for a
        'select' statement.
        """
        self.assertEquals(
            Select(From=self.schema.FOO,
                   OrderBy=self.schema.FOO.BAR).toSQL(),
            SQLFragment("select * from FOO order by BAR")
        )


    def test_orderByOrder(self):
        """
        L{Select}'s L{Ascending} parameter specifies an ascending/descending
        order for query results with an OrderBy clause.
        """
        self.assertEquals(
            Select(From=self.schema.FOO,
                   OrderBy=self.schema.FOO.BAR,
                   Ascending=False).toSQL(),
            SQLFragment("select * from FOO order by BAR desc")
        )

        self.assertEquals(
            Select(From=self.schema.FOO,
                   OrderBy=self.schema.FOO.BAR,
                   Ascending=True).toSQL(),
            SQLFragment("select * from FOO order by BAR asc")
        )

        self.assertEquals(
            Select(From=self.schema.FOO,
                   OrderBy=[self.schema.FOO.BAR, self.schema.FOO.BAZ],
                   Ascending=True).toSQL(),
            SQLFragment("select * from FOO order by BAR, BAZ asc")
        )


    def test_forUpdate(self):
        """
        L{Select}'s L{ForUpdate} parameter generates a 'for update' clause at
        the end of the query.
        """
        self.assertEquals(
            Select(From=self.schema.FOO, ForUpdate=True).toSQL(),
            SQLFragment("select * from FOO for update")
        )


    def test_groupBy(self):
        """
        L{Select}'s L{GroupBy} parameter generates a 'group by' clause for a
        'select' statement.
        """
        self.assertEquals(
            Select(From=self.schema.FOO,
                   GroupBy=self.schema.FOO.BAR).toSQL(),
            SQLFragment("select * from FOO group by BAR")
        )


    def test_groupByMulti(self):
        """
        L{Select}'s L{GroupBy} parameter can accept multiple columns in a list.
        """
        self.assertEquals(
            Select(From=self.schema.FOO,
                   GroupBy=[self.schema.FOO.BAR,
                            self.schema.FOO.BAZ]).toSQL(),
            SQLFragment("select * from FOO group by BAR, BAZ")
        )


    def test_joinClause(self):
        """
        A table's .join() method returns a join statement in a SELECT.
        """
        self.assertEquals(
            Select(From=self.schema.FOO.join(
                self.schema.BOZ, self.schema.FOO.BAR ==
                self.schema.BOZ.QUX)).toSQL(),
            SQLFragment("select * from FOO join BOZ on BAR = QUX", [])
        )


    def test_crossJoin(self):
        """
        A join with no clause specified will generate a cross join.  (This is an
        explicit synonym for an implicit join: i.e. 'select * from FOO, BAR'.)
        """
        self.assertEquals(
            Select(From=self.schema.FOO.join(self.schema.BOZ)).toSQL(),
            SQLFragment("select * from FOO cross join BOZ")
        )


    def test_joinJoin(self):
        """
        L{Join.join} will result in a multi-table join.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAR,
                    self.schema.BOZ.QUX],
                   From=self.schema.FOO
                   .join(self.schema.BOZ).join(self.schema.OTHER)).toSQL(),
            SQLFragment(
                "select FOO.BAR, QUX from FOO "
                "cross join BOZ cross join OTHER")
        )


    def test_multiJoin(self):
        """
        L{Join.join} has the same signature as L{TableSyntax.join} and supports
        the same 'on' and 'type' arguments.
        """

        self.assertEquals(
            Select([self.schema.FOO.BAR],
                   From=self.schema.FOO.join(
                       self.schema.BOZ).join(
                           self.schema.OTHER,
                           self.schema.OTHER.BAR == self.schema.FOO.BAR,
                           'left outer')).toSQL(),
            SQLFragment(
                "select FOO.BAR from FOO cross join BOZ left outer join OTHER "
                "on OTHER.BAR = FOO.BAR")
        )


    def test_columnSelection(self):
        """
        If a column is specified by the argument to L{Select}, those will be
        output by the SQL statement rather than the all-columns wildcard.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAR],
                   From=self.schema.FOO).toSQL(),
            SQLFragment("select BAR from FOO")
        )


    def test_columnAliases(self):
        """
        When attributes are set on a L{TableSyntax}, they will be remembered as
        column aliases, and their alias names may be retrieved via the
        L{TableSyntax.aliases} method.
        """
        self.assertEquals(self.schema.FOO.aliases(), {})
        self.schema.FOO.ALIAS = self.schema.FOO.BAR
        # you comparing ColumnSyntax object results in a ColumnComparison, which
        # you can't test for truth.
        fixedForEquality = dict([(k, v.model) for k, v in
                                 self.schema.FOO.aliases().items()])
        self.assertEquals(fixedForEquality,
                          {'ALIAS': self.schema.FOO.BAR.model})
        self.assertIdentical(self.schema.FOO.ALIAS.model,
                             self.schema.FOO.BAR.model)


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
            SQLFragment("select BAZ, BAR from FOO")
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
            SQLFragment("select BAZ, QUX from FOO join BOZ on BAR = QUX")
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
            SQLFragment(
                "select FOO.BAR, OTHER.BAR from FOO "
                "join OTHER on FOO_BAR = FOO.BAR"))


    def test_bindParameters(self):
        """
        L{SQLFragment.bind} returns a copy of that L{SQLFragment} with the
        L{Parameter} objects in its parameter list replaced with the keyword
        arguments to C{bind}.
        """

        self.assertEquals(
            Select(From=self.schema.FOO,
                   Where=(self.schema.FOO.BAR > Parameter("testing")).And(
                   self.schema.FOO.BAZ < 7)).toSQL().bind(testing=173),
            SQLFragment("select * from FOO where BAR > ? and BAZ < ?",
                         [173, 7]))


    def test_inSubSelect(self):
        """
        L{ColumnSyntax.In} returns a sub-expression using the SQL 'in' syntax.
        """
        wherein = (self.schema.FOO.BAR.In(
                    Select([self.schema.BOZ.QUX], From=self.schema.BOZ)))
        self.assertEquals(
            Select(From=self.schema.FOO, Where=wherein).toSQL(),
            SQLFragment(
                "select * from FOO where BAR in (select QUX from BOZ)"))


    def test_max(self):
        """
        L{Max}C{(column)} produces an object in the 'columns' clause that
        renders the 'max' aggregate in SQL.
        """
        self.assertEquals(
            Select([Max(self.schema.BOZ.QUX)], From=self.schema.BOZ).toSQL(),
            SQLFragment(
                "select max(QUX) from BOZ"))


    def test_aggregateComparison(self):
        """
        L{Max}C{(column) > constant} produces an object in the 'columns' clause
        that renders a comparison to the 'max' aggregate in SQL.
        """
        self.assertEquals(Select([Max(self.schema.BOZ.QUX) + 12],
                                From=self.schema.BOZ).toSQL(),
                          SQLFragment("select max(QUX) + ? from BOZ", [12]))


    def test_multiColumnExpression(self):
        """
        Multiple columns may be provided in an expression in the 'columns'
        portion of a Select() statement.  All arithmetic operators are
        supported.
        """
        self.assertEquals(
            Select([((self.schema.FOO.BAR + self.schema.FOO.BAZ) / 3) * 7],
                   From=self.schema.FOO).toSQL(),
            SQLFragment("select ((BAR + BAZ) / ?) * ? from FOO", [3, 7])
        )


    def test_len(self):
        """
        Test for the 'Len' function for determining character length of a
        column.  (Note that this should be updated to use different techniques
        as necessary in different databases.)
        """
        self.assertEquals(
            Select([Len(self.schema.TEXTUAL.MYTEXT)],
                    From=self.schema.TEXTUAL).toSQL(),
            SQLFragment(
                "select character_length(MYTEXT) from TEXTUAL"))


    def test_startswith(self):
        """
        Test for the string starts with comparison.
        (Note that this should be updated to use different techniques
        as necessary in different databases.)
        """
        self.assertEquals(
            Select([
                self.schema.TEXTUAL.MYTEXT],
                From=self.schema.TEXTUAL,
                Where=self.schema.TEXTUAL.MYTEXT.StartsWith("test"),
            ).toSQL(),
            SQLFragment(
                "select MYTEXT from TEXTUAL where MYTEXT like (? || ?)",
                ["test", "%"]
            )
        )


    def test_endswith(self):
        """
        Test for the string starts with comparison.
        (Note that this should be updated to use different techniques
        as necessary in different databases.)
        """
        self.assertEquals(
            Select([
                self.schema.TEXTUAL.MYTEXT],
                From=self.schema.TEXTUAL,
                Where=self.schema.TEXTUAL.MYTEXT.EndsWith("test"),
            ).toSQL(),
            SQLFragment(
                "select MYTEXT from TEXTUAL where MYTEXT like (? || ?)",
                ["%", "test"]
            )
        )


    def test_contains(self):
        """
        Test for the string starts with comparison.
        (Note that this should be updated to use different techniques
        as necessary in different databases.)
        """
        self.assertEquals(
            Select([
                self.schema.TEXTUAL.MYTEXT],
                From=self.schema.TEXTUAL,
                Where=self.schema.TEXTUAL.MYTEXT.Contains("test"),
            ).toSQL(),
            SQLFragment(
                "select MYTEXT from TEXTUAL where MYTEXT like (? || (? || ?))",
                ["%", "test", "%"]
            )
        )


    def test_insert(self):
        """
        L{Insert.toSQL} generates an 'insert' statement with all the relevant
        columns.
        """
        self.assertEquals(
            Insert({self.schema.FOO.BAR: 23,
                    self.schema.FOO.BAZ: 9}).toSQL(),
            SQLFragment("insert into FOO (BAR, BAZ) values (?, ?)", [23, 9]))


    def test_insertNotEnough(self):
        """
        L{Insert}'s constructor will raise L{NotEnoughValues} if columns have
        not been specified.
        """
        notEnough = self.assertRaises(
            NotEnoughValues, Insert, {self.schema.OTHER.BAR: 9}
        )
        self.assertEquals(str(notEnough), "Columns [FOO_BAR] required.")


    def test_insertReturning(self):
        """
        L{Insert}'s C{Return} argument will insert an SQL 'returning' clause.
        """
        self.assertEquals(
            Insert({self.schema.FOO.BAR: 23,
                    self.schema.FOO.BAZ: 9},
                   Return=self.schema.FOO.BAR).toSQL(),
            SQLFragment(
                "insert into FOO (BAR, BAZ) values (?, ?) returning BAR",
                [23, 9])
        )


    def test_insertMultiReturn(self):
        """
        L{Insert}'s C{Return} argument can also be a C{tuple}, which will insert
        an SQL 'returning' clause with multiple columns.
        """
        self.assertEquals(
            Insert({self.schema.FOO.BAR: 23,
                    self.schema.FOO.BAZ: 9},
                   Return=(self.schema.FOO.BAR, self.schema.FOO.BAZ)).toSQL(),
            SQLFragment(
                "insert into FOO (BAR, BAZ) values (?, ?) returning BAR, BAZ",
                [23, 9])
        )


    def test_insertMultiReturnOracle(self):
        """
        In Oracle's SQL dialect, the 'returning' clause requires an 'into'
        clause indicating where to put the results, as they can't be simply
        relayed to the cursor.  Further, additional bound variables are required
        to capture the output parameters.
        """
        self.assertEquals(
            Insert({self.schema.FOO.BAR: 40,
                    self.schema.FOO.BAZ: 50},
                   Return=(self.schema.FOO.BAR, self.schema.FOO.BAZ)).toSQL(
                       NumericPlaceholder(ORACLE_DIALECT)
                   ),
            SQLFragment(
                "insert into FOO (BAR, BAZ) values (:1, :2) returning BAR, BAZ"
                " into :3, :4",
                [40, 50, Parameter("oracle_out_0"), Parameter("oracle_out_1")]
            )
        )


    def simulateOracleConnection(self):
        """
        Create a fake oracle-ish connection pool without using real threads or a
        real database.

        @return: a 3-tuple of L{IAsyncTransaction}, L{ConnectionPool},
            L{ConnectionFactory}.
        """
        self.patch(syntax, 'cx_Oracle', FakeCXOracleModule)
        factory    = ConnectionFactory()
        pool       = ConnectionPool(factory.connect, maxConnections=2,
                                    dialect=ORACLE_DIALECT,
                                    paramstyle='numeric')
        self.paused = False
        pool._createHolder = lambda : FakeThreadHolder(self)
        pool.startService()
        conn = pool.connection()
        return conn, pool, factory


    def test_insertMultiReturnOnOracleTxn(self):
        """
        As described in L{test_insertMultiReturnOracle}, Oracle deals with
        'returning' clauses by using out parameters.  However, this is not quite
        enough, as the code needs to actually retrieve the values from the out
        parameters.
        """
        conn, _ignore_pool, factory = self.simulateOracleConnection()
        i = Insert({self.schema.FOO.BAR: 40,
                    self.schema.FOO.BAZ: 50},
                   Return=(self.schema.FOO.BAR, self.schema.FOO.BAZ))
        # See fake result generation in test_adbapi2.py.
        result = resultOf(i.on(conn))
        self.assertEquals(result, [[[300, 301]]])
        curvars = factory.connections[0].cursors[0].variables
        self.assertEquals(len(curvars), 2)
        self.assertEquals(curvars[0].type, FakeCXOracleModule.NUMBER)
        self.assertEquals(curvars[1].type, FakeCXOracleModule.STRING)


    def test_insertMismatch(self):
        """
        L{Insert} raises L{TableMismatch} if the columns specified aren't all
        from the same table.
        """
        self.assertRaises(
            TableMismatch,
            Insert, {self.schema.FOO.BAR: 23,
                     self.schema.FOO.BAZ: 9,
                     self.schema.TEXTUAL.MYTEXT: 'hello'}
        )


    def test_quotingOnKeywordConflict(self):
        """
        'access' is a keyword, so although our schema parser will leniently
        accept it, it must be quoted in any outgoing SQL.  (This is only done in
        the Oracle dialect, because it isn't necessary in postgres, and
        idiosyncratic case-folding rules make it challenging to do it in both.)
        """
        self.assertEquals(
            Insert({self.schema.LEVELS.ACCESS: 1,
                    self.schema.LEVELS.USERNAME:
                    "hi"}).toSQL(FixedPlaceholder(ORACLE_DIALECT, "?")),
            SQLFragment(
                'insert into LEVELS ("ACCESS", USERNAME) values (?, ?)',
                [1, "hi"])
        )
        self.assertEquals(
            Insert({self.schema.LEVELS.ACCESS: 1,
                    self.schema.LEVELS.USERNAME:
                    "hi"}).toSQL(FixedPlaceholder(POSTGRES_DIALECT, "?")),
            SQLFragment(
                'insert into LEVELS (ACCESS, USERNAME) values (?, ?)',
                [1, "hi"])
        )


    def test_updateReturning(self):
        """
        L{update}'s C{Return} argument will update an SQL 'returning' clause.
        """
        self.assertEquals(
            Update({self.schema.FOO.BAR: 23},
                   self.schema.FOO.BAZ == 43,
                   Return=self.schema.FOO.BAR).toSQL(),
            SQLFragment(
                "update FOO set BAR = ? where BAZ = ? returning BAR",
                [23, 43])
        )


    def test_updateMismatch(self):
        """
        L{Update} raises L{TableMismatch} if the columns specified aren't all
        from the same table.
        """
        self.assertRaises(
            TableMismatch,
            Update, {self.schema.FOO.BAR: 23,
                     self.schema.FOO.BAZ: 9,
                     self.schema.TEXTUAL.MYTEXT: 'hello'},
            Where=self.schema.FOO.BAZ == 9
        )


    def test_updateFunction(self):
        """
        L{Update} values may be L{FunctionInvocation}s, to update to computed
        values in the database.
        """
        sqlfunc = Function("hello")
        self.assertEquals(
            Update(
                {self.schema.FOO.BAR: 23,
                 self.schema.FOO.BAZ: sqlfunc()},
                Where=self.schema.FOO.BAZ == 9
            ).toSQL(),
            SQLFragment("update FOO set BAR = ?, BAZ = hello() "
                        "where BAZ = ?", [23, 9])
        )


    def test_insertFunction(self):
        """
        L{Update} values may be L{FunctionInvocation}s, to update to computed
        values in the database.
        """
        sqlfunc = Function("hello")
        self.assertEquals(
            Insert(
                {self.schema.FOO.BAR: 23,
                 self.schema.FOO.BAZ: sqlfunc()},
            ).toSQL(),
            SQLFragment("insert into FOO (BAR, BAZ) "
                        "values (?, hello())", [23])
        )


    def test_deleteReturning(self):
        """
        L{Delete}'s C{Return} argument will delete an SQL 'returning' clause.
        """
        self.assertEquals(
            Delete(self.schema.FOO,
                   Where=self.schema.FOO.BAR == 7,
                   Return=self.schema.FOO.BAZ).toSQL(),
            SQLFragment(
                "delete from FOO where BAR = ? returning BAZ", [7])
        )


    def test_update(self):
        """
        L{Update.toSQL} generates an 'update' statement.
        """
        self.assertEquals(
            Update({self.schema.FOO.BAR: 4321},
                    self.schema.FOO.BAZ == 1234).toSQL(),
            SQLFragment("update FOO set BAR = ? where BAZ = ?", [4321, 1234]))


    def test_delete(self):
        """
        L{Delete} generates an SQL 'delete' statement.
        """
        self.assertEquals(
            Delete(self.schema.FOO,
                   Where=self.schema.FOO.BAR == 12).toSQL(),
            SQLFragment(
                "delete from FOO where BAR = ?", [12])
        )

        self.assertEquals(
            Delete(self.schema.FOO,
                   Where=None).toSQL(),
            SQLFragment("delete from FOO")
        )


    def test_lock(self):
        """
        L{Lock.exclusive} generates a ('lock table') statement, locking the
        table in the specified mode.
        """
        self.assertEquals(Lock.exclusive(self.schema.FOO).toSQL(),
                          SQLFragment("lock table FOO in exclusive mode"))


    def test_savepoint(self):
        """
        L{Savepoint} generates a ('savepoint') statement.
        """
        self.assertEquals(Savepoint("test").toSQL(),
                          SQLFragment("savepoint test"))

    def test_rollbacktosavepoint(self):
        """
        L{RollbackToSavepoint} generates a ('rollback to savepoint') statement.
        """
        self.assertEquals(RollbackToSavepoint("test").toSQL(),
                          SQLFragment("rollback to savepoint test"))

    def test_releasesavepoint(self):
        """
        L{ReleaseSavepoint} generates a ('release savepoint') statement.
        """
        self.assertEquals(ReleaseSavepoint("test").toSQL(),
                          SQLFragment("release savepoint test"))

    def test_savepointaction(self):
        """
        L{SavepointAction} generates a ('savepoint') statement.
        """
        self.assertEquals(SavepointAction("test")._name, "test")

    def test_limit(self):
        """
        A L{Select} object with a 'Limit' keyword parameter will generate
        a SQL statement with a 'limit' clause.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAR],
                   From=self.schema.FOO,
                   Limit=123).toSQL(),
            SQLFragment(
                "select BAR from FOO limit ?", [123]))


    def test_having(self):
        """
        A L{Select} object with a 'Having' keyword parameter will generate
        a SQL statement with a 'having' expression.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAR],
                   From=self.schema.FOO,
                   Having=Max(self.schema.FOO.BAZ) < 7).toSQL(),
            SQLFragment("select BAR from FOO having max(BAZ) < ?", [7])
        )


    def test_distinct(self):
        """
        A L{Select} object with a 'Disinct' keyword parameter with a value of
        C{True} will generate a SQL statement with a 'distinct' keyword
        preceding its list of columns.
        """
        self.assertEquals(
            Select([self.schema.FOO.BAR], From=self.schema.FOO,
                   Distinct=True).toSQL(),
            SQLFragment("select distinct BAR from FOO")
        )


    def test_nextSequenceValue(self):
        """
        When a sequence is used as a value in an expression, it renders as the
        call to 'nextval' that will produce its next value.
        """
        self.assertEquals(
            Insert({self.schema.BOZ.QUX:
                    self.schema.A_SEQ}).toSQL(),
            SQLFragment("insert into BOZ (QUX) values (nextval('A_SEQ'))", []))


    def test_nextSequenceValueOracle(self):
        """
        When a sequence is used as a value in an expression in the Oracle
        dialect, it renders as the 'nextval' attribute of the appropriate
        sequence.
        """
        self.assertEquals(
            Insert({self.schema.BOZ.QUX:
                    self.schema.A_SEQ}).toSQL(
                        FixedPlaceholder(ORACLE_DIALECT, "?")),
            SQLFragment("insert into BOZ (QUX) values (A_SEQ.nextval)", []))


    def test_nextSequenceDefaultImplicitExplicitOracle(self):
        """
        In Oracle's dialect, sequence defaults can't be implemented without
        using triggers, so instead we just explicitly always include the
        sequence default value.
        """
        addSQLToSchema(
            schema=self.schema.model, schemaData=
            "create table DFLTR (a varchar(255), "
            "b integer default nextval('A_SEQ'));"
        )
        self.assertEquals(
            Insert({self.schema.DFLTR.a: 'hello'}).toSQL(
                FixedPlaceholder(ORACLE_DIALECT, "?")
            ),
            SQLFragment("insert into DFLTR (a, b) values "
                        "(?, A_SEQ.nextval)", ['hello']),
        )
        # Should be the same if it's explicitly specified.
        self.assertEquals(
            Insert({self.schema.DFLTR.a: 'hello',
                    self.schema.DFLTR.b: self.schema.A_SEQ}).toSQL(
                FixedPlaceholder(ORACLE_DIALECT, "?")
            ),
            SQLFragment("insert into DFLTR (a, b) values "
                        "(?, A_SEQ.nextval)", ['hello']),
        )


    def test_numericParams(self):
        """
        An L{IAsyncTransaction} with the 'numeric' paramstyle attribute will
        cause statements to be generated with parameters in the style of :1 :2
        :3, as per the DB-API.
        """
        stmts = []
        class FakeOracleTxn(object):
            def execSQL(self, text, params, exc):
                stmts.append((text, params))
            dialect = ORACLE_DIALECT
            paramstyle = 'numeric'
        Select([self.schema.FOO.BAR],
               From=self.schema.FOO,
               Where=(self.schema.FOO.BAR == 7).And(
                   self.schema.FOO.BAZ == 9)
              ).on(FakeOracleTxn())
        self.assertEquals(
            stmts, [("select BAR from FOO where BAR = :1 and BAZ = :2",
                     [7, 9])]
        )


    def test_rewriteOracleNULLs_Select(self):
        """
        Oracle databases cannot distinguish between the empty string and
        C{NULL}.  When you insert an empty string, C{cx_Oracle} therefore treats
        it as a C{None} and will return that when you select it back again.  We
        address this in the schema by dropping 'not null' constraints.

        Therefore, when executing a statement which includes a string column,
        'on' should rewrite None return values from C{cx_Oracle} to be empty
        bytestrings, but only for string columns.
        """

        rows = resultOf(
            Select([self.schema.NULLCHECK.ASTRING,
                    self.schema.NULLCHECK.ANUMBER],
                   From=self.schema.NULLCHECK).on(NullTestingOracleTxn()))[0]

        self.assertEquals(rows, [['', None]])


    def test_rewriteOracleNULLs_SelectAllColumns(self):
        """
        Same as L{test_rewriteOracleNULLs_Select}, but with the L{ALL_COLUMNS}
        shortcut.
        """
        rows = resultOf(
            Select(From=self.schema.NULLCHECK).on(NullTestingOracleTxn())
        )[0]
        self.assertEquals(rows, [['', None]])


    def test_rewriteOracleNULLs_Insert(self):
        """
        The behavior described in L{test_rewriteOracleNULLs_Select} applies to
        other statement types as well, specifically those with 'returning'
        clauses.
        """
        conn, _ignore_pool, factory = self.simulateOracleConnection()
        # Add 2 cursor variable values so that these will be used by
        # FakeVariable.getvalue.
        factory.varvals.extend([None, None])
        rows = resultOf(
            Insert({self.schema.NULLCHECK.ASTRING: '',
                    self.schema.NULLCHECK.ANUMBER: None},
                   Return=[self.schema.NULLCHECK.ASTRING,
                           self.schema.NULLCHECK.ANUMBER]
                  ).on(conn))[0]

        self.assertEquals(rows, [['', None]])


    def test_nestedLogicalExpressions(self):
        """
        Make sure that logical operator precedence inserts proper parenthesis
        when needed.  e.g. 'a.And(b.Or(c))' needs to be 'a and (b or c)' not 'a
        and b or c'.
        """
        self.assertEquals(
            Select(
                From=self.schema.FOO,
                Where=(self.schema.FOO.BAR != 7).
                    And(self.schema.FOO.BAZ != 8).
                    And((self.schema.FOO.BAR == 8).Or(self.schema.FOO.BAZ == 0))
            ).toSQL(),
            SQLFragment("select * from FOO where BAR != ? and BAZ != ? and "
                        "(BAR = ? or BAZ = ?)", [7, 8, 8, 0]))

        self.assertEquals(
            Select(
                From=self.schema.FOO,
                Where=(self.schema.FOO.BAR != 7).
                    Or(self.schema.FOO.BAZ != 8).
                    Or((self.schema.FOO.BAR == 8).And(self.schema.FOO.BAZ == 0))
            ).toSQL(),
            SQLFragment("select * from FOO where BAR != ? or BAZ != ? or "
                        "BAR = ? and BAZ = ?", [7, 8, 8, 0]))

        self.assertEquals(
            Select(
                From=self.schema.FOO,
                Where=(self.schema.FOO.BAR != 7).
                    Or(self.schema.FOO.BAZ != 8).
                    And((self.schema.FOO.BAR == 8).Or(self.schema.FOO.BAZ == 0))
            ).toSQL(),
            SQLFragment("select * from FOO where (BAR != ? or BAZ != ?) and "
                        "(BAR = ? or BAZ = ?)", [7, 8, 8, 0]))


    def test_updateWithNULL(self):
        """
        As per the DB-API specification, "SQL NULL values are represented by the
        Python None singleton on input and output."  When a C{None} is provided
        as a value to an L{Update}, it will be relayed to the database as a
        parameter.
        """
        self.assertEquals(
            Update({self.schema.BOZ.QUX: None},
                   Where=self.schema.BOZ.QUX == 7).toSQL(),
            SQLFragment("update BOZ set QUX = ? where QUX = ?", [None, 7])
        )


    def test_subSelectComparison(self):
        """
        A comparison of a column to a sub-select in a where clause will result
        in a parenthetical 'Where' clause.
        """
        self.assertEquals(
            Update(
                {self.schema.BOZ.QUX: 9},
                Where=self.schema.BOZ.QUX ==
                Select([self.schema.FOO.BAR], From=self.schema.FOO,
                       Where=self.schema.FOO.BAZ == 12)).toSQL(),
            SQLFragment(
                # NOTE: it's very important that the comparison _always_ go in
                # this order (column from the UPDATE first, inner SELECT second)
                # as the other order will be considered a syntax error.
                "update BOZ set QUX = ? where QUX = ("
                "select BAR from FOO where BAZ = ?)", [9, 12]
            )
        )


    def test_tupleComparison(self):
        """
        A L{Tuple} allows for simultaneous comparison of multiple values in a
        C{Where} clause.  This feature is particularly useful when issuing an
        L{Update} or L{Delete}, where the comparison is with values from a
        subselect.  (A L{Tuple} will be automatically generated upon comparison
        to a C{tuple} or C{list}.)
        """
        self.assertEquals(
            Update(
                {self.schema.BOZ.QUX: 1},
                Where=(self.schema.BOZ.QUX, self.schema.BOZ.QUUX) ==
                Select([self.schema.FOO.BAR, self.schema.FOO.BAZ],
                       From=self.schema.FOO,
                       Where=self.schema.FOO.BAZ == 2)).toSQL(),
            SQLFragment(
                # NOTE: it's very important that the comparison _always_ go in
                # this order (tuple of columns from the UPDATE first, inner
                # SELECT second) as the other order will be considered a syntax
                # error.
                "update BOZ set QUX = ? where (QUX, QUUX) = ("
                "select BAR, BAZ from FOO where BAZ = ?)", [1, 2]
            )
        )


    def test_oracleTableTruncation(self):
        """
        L{Table}'s SQL generation logic will truncate table names if the dialect
        (i.e. Oracle) demands it.  (See txdav.common.datastore.sql_tables for
        the schema translator and enforcement of name uniqueness in the derived
        schema.)
        """

        addSQLToSchema(
            self.schema.model,
            "create table veryveryveryveryveryveryveryverylong "
            "(foo integer);"
        )
        vvl = self.schema.veryveryveryveryveryveryveryverylong
        self.assertEquals(
            Insert({vvl.foo: 1}).toSQL(FixedPlaceholder(ORACLE_DIALECT, "?")),
            SQLFragment(
                "insert into veryveryveryveryveryveryveryve (foo) values "
                "(?)", [1]
            )
        )


