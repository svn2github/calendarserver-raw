# -*- test-case-name: twext.enterprise.dal.test.test_sqlsyntax -*-
##
# Copyright (c) 2010-2012 Apple Inc. All rights reserved.
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
Syntax wrappers and generators for SQL.
"""

from itertools import count, repeat
from functools import partial
from operator import eq, ne

from zope.interface import implements

from twisted.internet.defer import succeed

from twext.enterprise.dal.model import Schema, Table, Column, Sequence
from twext.enterprise.ienterprise import POSTGRES_DIALECT, ORACLE_DIALECT
from twext.enterprise.ienterprise import IDerivedParameter
from twext.enterprise.util import mapOracleOutputType

try:
    import cx_Oracle
    cx_Oracle
except ImportError:
    cx_Oracle = None

class DALError(Exception):
    """
    Base class for exceptions raised by this module. This can be raised directly for
    API violations. This exception represents a serious programming error and should
    normally never be caught or ignored.
    """

class QueryPlaceholder(object):
    """
    Representation of the placeholders required to generate some SQL, for a
    single statement.  Contains information necessary
    to generate place holder strings based on the database dialect.
    """

    def placeholder(self):
        raise NotImplementedError("See subclasses.")



class FixedPlaceholder(QueryPlaceholder):
    """
    Fixed string used as the place holder.
    """

    def __init__(self, placeholder):
        self._placeholder = placeholder


    def placeholder(self):
        return self._placeholder



class NumericPlaceholder(QueryPlaceholder):
    """
    Numeric counter used as the place holder.
    """

    def __init__(self):
        self._next = count(1).next


    def placeholder(self):
        return ':' + str(self._next())



def defaultPlaceholder():
    """
    Generate a default L{QueryPlaceholder}
    """
    return FixedPlaceholder('?')



class QueryGenerator(object):
    """
    Maintains various pieces of transient information needed when building a
    query. This includes the SQL dialect, the format of the place holder and
    and automated id generator.
    """

    def __init__(self, dialect=None, placeholder=None):
        self.dialect = dialect if dialect else POSTGRES_DIALECT
        if placeholder is None:
            placeholder = defaultPlaceholder()
        self.placeholder = placeholder

        self.generatedID = count(1).next


    def nextGeneratedID(self):
        return "genid_%d" % (self.generatedID(),)


    def shouldQuote(self, name):
        return (self.dialect == ORACLE_DIALECT and name.lower() in _KEYWORDS)



class TableMismatch(Exception):
    """
    A table in a statement did not match with a column.
    """



class NotEnoughValues(DALError):
    """
    Not enough values were supplied for an L{Insert}.
    """



class _Statement(object):
    """
    An SQL statement that may be executed.  (An abstract base class, must
    implement several methods.)
    """

    _paramstyles = {
        'pyformat': partial(FixedPlaceholder, "%s"),
        'numeric': NumericPlaceholder
    }


    def toSQL(self, queryGenerator=None):
        if queryGenerator is None:
            queryGenerator = QueryGenerator()
        return self._toSQL(queryGenerator)


    def _extraVars(self, txn, queryGenerator):
        """
        A hook for subclasses to provide additional keyword arguments to the
        C{bind} call when L{_Statement.on} is executed.  Currently this is used
        only for 'out' parameters to capture results when executing statements
        that do not normally have a result (L{Insert}, L{Delete}, L{Update}).
        """
        return {}


    def _extraResult(self, result, outvars, queryGenerator):
        """
        A hook for subclasses to manipulate the results of 'on', after they've
        been retrieved by the database but before they've been given to
        application code.

        @param result: a L{Deferred} that will fire with the rows as returned by
            the database.
        @type result: C{list} of rows, which are C{list}s or C{tuple}s.

        @param outvars: a dictionary of extra variables returned by
            C{self._extraVars}.

        @param queryGenerator: information about the connection where the statement
            was executed.

        @type queryGenerator: L{QueryGenerator} (a subclass thereof)

        @return: the result to be returned from L{_Statement.on}.

        @rtype: L{Deferred} firing result rows
        """
        return result


    def on(self, txn, raiseOnZeroRowCount=None, **kw):
        """
        Execute this statement on a given L{IAsyncTransaction} and return the
        resulting L{Deferred}.

        @param txn: the L{IAsyncTransaction} to execute this on.

        @param raiseOnZeroRowCount: the exception to raise if no data was
            affected or returned by this query.

        @param kw: keyword arguments, mapping names of L{Parameter} objects
            located somewhere in C{self}

        @return: results from the database.

        @rtype: a L{Deferred} firing a C{list} of records (C{tuple}s or
            C{list}s)
        """
        queryGenerator = QueryGenerator(txn.dialect, self._paramstyles[txn.paramstyle]())
        outvars = self._extraVars(txn, queryGenerator)
        kw.update(outvars)
        fragment = self.toSQL(queryGenerator).bind(**kw)
        result = txn.execSQL(fragment.text, fragment.parameters,
                             raiseOnZeroRowCount)
        result = self._extraResult(result, outvars, queryGenerator)
        if queryGenerator.dialect == ORACLE_DIALECT and result:
            result.addCallback(self._fixOracleNulls)
        return result


    def _resultColumns(self):
        """
        Subclasses must implement this to return a description of the columns
        expected to be returned.  This is a list of L{ColumnSyntax} objects, and
        possibly other expression syntaxes which will be converted to C{None}.
        """
        raise NotImplementedError(
            "Each statement subclass must describe its result"
        )


    def _resultShape(self):
        """
        Process the result of the subclass's C{_resultColumns}, as described in
        the docstring above.
        """
        for expectation in self._resultColumns():
            if isinstance(expectation, ColumnSyntax):
                yield expectation.model
            else:
                yield None


    def _fixOracleNulls(self, rows):
        """
        Oracle treats empty strings as C{NULL}.  Fix this by looking at the
        columns we expect to have returned, and replacing any C{None}s with
        empty strings in the appropriate position.
        """
        if rows is None:
            return None
        newRows = []
        for row in rows:
            newRow = []
            for column, description in zip(row, self._resultShape()):
                if ((description is not None and
                     # FIXME: "is the python type str" is what I mean; this list
                     # should be more centrally maintained
                     description.type.name in ('varchar', 'text', 'char') and
                     column is None
                    )):
                    column = ''
                newRow.append(column)
            newRows.append(newRow)
        return newRows



class Syntax(object):
    """
    Base class for syntactic convenience.

    This class will define dynamic attribute access to represent its underlying
    model as a Python namespace.

    You can access the underlying model as '.model'.
    """

    modelType = None
    model = None

    def __init__(self, model):
        if not isinstance(model, self.modelType):
            # make sure we don't get a misleading repr()
            raise DALError("type mismatch: %r %r", type(self), model)
        self.model = model


    def __repr__(self):
        if self.model is not None:
            return '<Syntax for: %r>' % (self.model,)
        return super(Syntax, self).__repr__()



def comparison(comparator):
    def __(self, other):
        if other is None:
            return NullComparison(self, comparator)
        if isinstance(other, Select):
            return NotImplemented
        if isinstance(other, ColumnSyntax):
            return ColumnComparison(self, comparator, other)
        if isinstance(other, ExpressionSyntax):
            return CompoundComparison(self, comparator, other)
        else:
            return CompoundComparison(self, comparator, Constant(other))
    return __



class ExpressionSyntax(Syntax):
    __eq__ = comparison('=')
    __ne__ = comparison('!=')
    __gt__ = comparison('>')
    __ge__ = comparison('>=')
    __lt__ = comparison('<')
    __le__ = comparison('<=')
    __add__ = comparison("+")
    __sub__ = comparison("-")
    __div__= comparison("/")
    __mul__= comparison("*")


    def __nonzero__(self):
        raise DALError(
            "SQL expressions should not be tested for truth value in Python.")


    def In(self, subselect):
        # Can't be Select.__contains__ because __contains__ gets __nonzero__
        # called on its result by the 'in' syntax.
        return CompoundComparison(self, 'in', subselect)


    def StartsWith(self, other):
        return CompoundComparison(self, "like", CompoundComparison(Constant(other), '||', Constant('%')))


    def EndsWith(self, other):
        return CompoundComparison(self, "like", CompoundComparison(Constant('%'), '||', Constant(other)))


    def Contains(self, other):
        return CompoundComparison(self, "like", CompoundComparison(Constant('%'), '||', CompoundComparison(Constant(other), '||', Constant('%'))))

class FunctionInvocation(ExpressionSyntax):
    def __init__(self, function, *args):
        self.function = function
        self.args = args


    def allColumns(self):
        """
        All of the columns in all of the arguments' columns.
        """
        def ac():
            for arg in self.args:
                for column in arg.allColumns():
                    yield column
        return list(ac())


    def subSQL(self, queryGenerator, allTables):
        result = SQLFragment(self.function.nameFor(queryGenerator))
        result.append(_inParens(
            _commaJoined(_convert(arg).subSQL(queryGenerator, allTables)
                         for arg in self.args)))
        return result



class Constant(ExpressionSyntax):
    def __init__(self, value):
        self.value = value


    def allColumns(self):
        return []


    def subSQL(self, queryGenerator, allTables):
        return SQLFragment(queryGenerator.placeholder.placeholder(), [self.value])



class NamedValue(ExpressionSyntax):
    """
    A constant within the database; something predefined, such as
    CURRENT_TIMESTAMP.
    """
    def __init__(self, name):
        self.name = name


    def subSQL(self, queryGenerator, allTables):
        return SQLFragment(self.name)



class Function(object):
    """
    An L{Function} is a representation of an SQL Function function.
    """

    def __init__(self, name, oracleName=None):
        self.name = name
        self.oracleName = oracleName


    def nameFor(self, queryGenerator):
        if queryGenerator.dialect == ORACLE_DIALECT and self.oracleName is not None:
            return self.oracleName
        return self.name


    def __call__(self, *args):
        """
        Produce an L{FunctionInvocation}
        """
        return FunctionInvocation(self, *args)



Count = Function("count")
Max = Function("max")
Len = Function("character_length", "length")
Upper = Function("upper")
Lower = Function("lower")

# Use a specific value here for "the convention for case-insensitive values in
# the database" so we don't need to keep remembering whether it's upper or
# lowercase.
CaseFold = Lower



class SchemaSyntax(Syntax):
    """
    Syntactic convenience for L{Schema}.
    """

    modelType = Schema

    def __getattr__(self, attr):
        try:
            tableModel = self.model.tableNamed(attr)
        except KeyError:
            try:
                seqModel = self.model.sequenceNamed(attr)
            except KeyError:
                raise AttributeError("schema has no table or sequence %r" % (attr,))
            else:
                return SequenceSyntax(seqModel)
        else:
            syntax = TableSyntax(tableModel)
            # Needs to be preserved here so that aliasing will work.
            setattr(self, attr, syntax)
            return syntax


    def __iter__(self):
        for table in self.model.tables:
            yield TableSyntax(table)



class SequenceSyntax(ExpressionSyntax):
    """
    Syntactic convenience for L{Sequence}.
    """

    modelType = Sequence

    def subSQL(self, queryGenerator, allTables):
        """
        Convert to an SQL fragment.
        """
        if queryGenerator.dialect == ORACLE_DIALECT:
            fmt = "%s.nextval"
        else:
            fmt = "nextval('%s')"
        return SQLFragment(fmt % (self.model.name,))



def _nameForDialect(name, dialect):
    """
    If the given name is being computed in the oracle dialect, truncate it to 30
    characters.
    """
    if dialect == ORACLE_DIALECT:
        name = name[:30]
    return name



class TableSyntax(Syntax):
    """
    Syntactic convenience for L{Table}.
    """

    modelType = Table

    def alias(self):
        """
        Return an alias for this L{TableSyntax} so that it might be joined
        against itself.

        As in SQL, C{someTable.join(someTable)} is an error; you can't join a
        table against itself.  However, C{t = someTable.alias();
        someTable.join(t)} is usable as a 'from' clause.
        """
        return TableAlias(self.model)


    def join(self, otherTableSyntax, on=None, type=''):
        """
        Create a L{Join}, representing a join between two tables.
        """
        if on is None:
            type = 'cross'
        return Join(self, type, otherTableSyntax, on)


    def subSQL(self, queryGenerator, allTables):
        """
        Generate the L{SQLFragment} for this table's identification; this is
        for use in a 'from' clause.
        """
        # XXX maybe there should be a specific method which is only invoked
        # from the FROM clause, that only tables and joins would implement?
        return SQLFragment(_nameForDialect(self.model.name, queryGenerator.dialect))


    def __getattr__(self, attr):
        """
        Attributes named after columns on a L{TableSyntax} are returned by
        accessing their names as attributes.  For example, if there is a schema
        syntax object created from SQL equivalent to 'create table foo (bar
        integer, baz integer)', 'schemaSyntax.foo.bar' and
        'schemaSyntax.foo.baz'
        """
        return ColumnSyntax(self.model.columnNamed(attr))


    def __iter__(self):
        """
        Yield a L{ColumnSyntax} for each L{Column} in this L{TableSyntax}'s
        model's table.
        """
        for column in self.model.columns:
            yield ColumnSyntax(column)


    def tables(self):
        """
        Return a C{list} of tables involved in the query by this table.  (This
        method is expected by anything that can act as the C{From} clause: see
        L{Join.tables})
        """
        return [self]


    def columnAliases(self):
        """
        Inspect the Python aliases for this table in the given schema.  Python
        aliases for a table are created by setting an attribute on the schema.
        For example, in a schema which had "schema.MYTABLE.ID =
        schema.MYTABLE.MYTABLE_ID" applied to it,
        schema.MYTABLE.columnAliases() would return C{[("ID",
        schema.MYTABLE.MYTABLE_ID)]}.

        @return: a list of 2-tuples of (alias (C{str}), column
            (C{ColumnSyntax})), enumerating all of the Python aliases provided.
        """
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, ColumnSyntax):
                result[k] = v
        return result


    def __contains__(self, columnSyntax):
        if isinstance(columnSyntax, FunctionInvocation):
            columnSyntax = columnSyntax.arg
        return (columnSyntax.model in self.model.columns)



class TableAlias(TableSyntax):
    """
    An alias for a table, under a different name, for the purpose of doing a
    self-join.
    """

    def subSQL(self, queryGenerator, allTables):
        """
        Return an L{SQLFragment} with a string of the form C{'mytable myalias'}
        suitable for use in a FROM clause.
        """
        result = super(TableAlias, self).subSQL(queryGenerator, allTables)
        result.append(SQLFragment(" " + self._aliasName(allTables)))
        return result


    def _aliasName(self, allTables):
        """
        The alias under which this table will be known in the query.

        @param allTables: a C{list}, as passed to a C{subSQL} method during SQL
            generation.

        @return: a string naming this alias, a unique identifier, albeit one
            which is only stable within the query which populated C{allTables}.
        @rtype: C{str}
        """
        anum = [t for t in allTables
                if isinstance(t, TableAlias)].index(self) + 1
        return 'alias%d' % (anum,)


    def __getattr__(self, attr):
        return AliasedColumnSyntax(self, self.model.columnNamed(attr))



class Join(object):
    """
    A DAL object representing an SQL 'join' statement.

    @ivar leftSide: a L{Join} or L{TableSyntax} representing the left side of
        this join.

    @ivar rightSide: a L{TableSyntax} representing the right side of this join.

    @ivar type: the type of join this is.  For example, for a left outer join,
        this would be C{'left outer'}.
    @type type: C{str}

    @ivar on: the 'on' clause of this table.

    @type on: L{ExpressionSyntax}
    """

    def __init__(self, leftSide, type, rightSide, on):
        self.leftSide = leftSide
        self.type = type
        self.rightSide = rightSide
        self.on = on


    def subSQL(self, queryGenerator, allTables):
        stmt = SQLFragment()
        stmt.append(self.leftSide.subSQL(queryGenerator, allTables))
        stmt.text += ' '
        if self.type:
            stmt.text += self.type
            stmt.text += ' '
        stmt.text += 'join '
        stmt.append(self.rightSide.subSQL(queryGenerator, allTables))
        if self.type != 'cross':
            stmt.text += ' on '
            stmt.append(self.on.subSQL(queryGenerator, allTables))
        return stmt


    def tables(self):
        """
        Return a C{list} of tables which this L{Join} will involve in a query:
        all those present on the left side, as well as all those present on the
        right side.
        """
        return self.leftSide.tables() + self.rightSide.tables()


    def join(self, otherTable, on=None, type=None):
        if on is None:
            type = 'cross'
        return Join(self, type, otherTable, on)


_KEYWORDS = ["access",
             # SQL keyword, but we have a column with this name
             "path",
             # Not actually a standard keyword, but a function in oracle, and we
             # have a column with this name.
             "size",
             # not actually sure what this is; only experimentally determined
             # that not quoting it causes an issue.
            ]



class ColumnSyntax(ExpressionSyntax):
    """
    Syntactic convenience for L{Column}.

    @ivar _alwaysQualified: a boolean indicating whether to always qualify the
        column name in generated SQL, regardless of whether the column name is
        specific enough even when unqualified.
    @type _alwaysQualified: C{bool}
    """

    modelType = Column

    _alwaysQualified = False


    def allColumns(self):
        return [self]


    def subSQL(self, queryGenerator, allTables):
        # XXX This, and 'model', could in principle conflict with column names.
        # Maybe do something about that.
        name = self.model.name
        if queryGenerator.shouldQuote(name):
            name = '"%s"' % (name,)

        if self._alwaysQualified:
            qualified = True
        else:
            qualified = False
            for tableSyntax in allTables:
                if self.model.table is not tableSyntax.model:
                    if self.model.name in (c.name for c in
                                           tableSyntax.model.columns):
                        qualified = True
                        break
        if qualified:
            return SQLFragment(self._qualify(name, allTables))
        else:
            return SQLFragment(name)


    def __hash__(self):
        return hash(self.model) + 10


    def _qualify(self, name, allTables):
        return self.model.table.name + '.' + name


class ResultAliasSyntax(ExpressionSyntax):
    
    def __init__(self, expression, alias=None):
        self.expression = expression
        self.alias = alias

    def aliasName(self, queryGenerator):
        if self.alias is None:
            self.alias = queryGenerator.nextGeneratedID()
        return self.alias

    def columnReference(self):
        return AliasReferenceSyntax(self)

    def allColumns(self):
        return self.expression.allColumns()

    def subSQL(self, queryGenerator, allTables):
        result = SQLFragment()
        result.append(self.expression.subSQL(queryGenerator, allTables))
        result.append(SQLFragment(" %s" % (self.aliasName(queryGenerator),)))
        return result


class AliasReferenceSyntax(ExpressionSyntax):
    
    def __init__(self, resultAlias):
        self.resultAlias = resultAlias

    def allColumns(self):
        return self.resultAlias.allColumns()

    def subSQL(self, queryGenerator, allTables):
        return SQLFragment(self.resultAlias.aliasName(queryGenerator))


class AliasedColumnSyntax(ColumnSyntax):
    """
    An L{AliasedColumnSyntax} is like a L{ColumnSyntax}, but it generates SQL
    for a column of a table under an alias, rather than directly.  i.e. this is
    used for C{'something.col'} in C{'select something.col from tablename
    something'} rather than the 'col' in C{'select col from tablename'}.

    @see: L{TableSyntax.alias}
    """

    _alwaysQualified = True


    def __init__(self, tableAlias, model):
        super(AliasedColumnSyntax, self).__init__(model)
        self._tableAlias = tableAlias


    def _qualify(self, name, allTables):
        return self._tableAlias._aliasName(allTables) + '.' + name



class Comparison(ExpressionSyntax):

    def __init__(self, a, op, b):
        self.a = a
        self.op = op
        self.b = b


    def _subexpression(self, expr, queryGenerator, allTables):
        result = expr.subSQL(queryGenerator, allTables)
        if self.op not in ('and', 'or') and isinstance(expr, Comparison):
            result = _inParens(result)
        return result


    def booleanOp(self, operand, other):
        return CompoundComparison(self, operand, other)


    def And(self, other):
        return self.booleanOp('and', other)


    def Or(self, other):
        return self.booleanOp('or', other)



class NullComparison(Comparison):
    """
    A L{NullComparison} is a comparison of a column or expression with None.
    """
    def __init__(self, a, op):
        # 'b' is always None for this comparison type
        super(NullComparison, self).__init__(a, op, None)


    def subSQL(self, queryGenerator, allTables):
        sqls = SQLFragment()
        sqls.append(self.a.subSQL(queryGenerator, allTables))
        sqls.text += " is "
        if self.op != "=":
            sqls.text += "not "
        sqls.text += "null"
        return sqls



class CompoundComparison(Comparison):
    """
    A compound comparison; two or more constraints, joined by an operation
    (currently only AND or OR).
    """

    def allColumns(self):
        return self.a.allColumns() + self.b.allColumns()


    def subSQL(self, queryGenerator, allTables):
        if ( queryGenerator.dialect == ORACLE_DIALECT
             and isinstance(self.b, Constant) and self.b.value == ''
             and self.op in ('=', '!=') ):
            return NullComparison(self.a, self.op).subSQL(queryGenerator, allTables)
        stmt = SQLFragment()
        result = self._subexpression(self.a, queryGenerator, allTables)
        if (isinstance(self.a, CompoundComparison)
            and self.a.op == 'or' and self.op == 'and'):
            result = _inParens(result)
        stmt.append(result)

        stmt.text += ' %s ' % (self.op,)

        result = self._subexpression(self.b, queryGenerator, allTables)
        if (isinstance(self.b, CompoundComparison)
            and self.b.op == 'or' and self.op == 'and'):
            result = _inParens(result)
        stmt.append(result)
        return stmt



_operators = {"=": eq, "!=": ne}

class ColumnComparison(CompoundComparison):
    """
    Comparing two columns is the same as comparing any other two expressions,
    except that Python can retrieve a truth value, so that columns may be
    compared for value equality in scripts that want to interrogate schemas.
    """

    def __nonzero__(self):
        thunk = _operators.get(self.op)
        if thunk is None:
            return super(ColumnComparison, self).__nonzero__()
        return thunk(self.a.model, self.b.model)



class _AllColumns(object):

    def subSQL(self, queryGenerator, allTables):
        return SQLFragment('*')

ALL_COLUMNS = _AllColumns()



class _SomeColumns(object):

    def __init__(self, columns):
        self.columns = columns


    def subSQL(self, queryGenerator, allTables):
        first = True
        cstatement = SQLFragment()
        for column in self.columns:
            if first:
                first = False
            else:
                cstatement.append(SQLFragment(", "))
            cstatement.append(column.subSQL(queryGenerator, allTables))
        return cstatement



def _columnsMatchTables(columns, tables):
    for expression in columns:
        for column in expression.allColumns():
            for table in tables:
                if column in table:
                    break
            else:
                return False
    return True


class Tuple(object):

    def __init__(self, columns):
        self.columns = columns


    def subSQL(self, queryGenerator, allTables):
        return _inParens(_commaJoined(c.subSQL(queryGenerator, allTables)
                                      for c in self.columns))


    def allColumns(self):
        return self.columns


class SetExpression(object):
    """
    A UNION, INTERSECT, or EXCEPT construct used inside a SELECT.
    """
    
    OPTYPE_ALL = "all"
    OPTYPE_DISTINCT = "distinct"

    def __init__(self, selects, optype=None):
        """
        
        @param selects: a single Select or a list of Selects
        @type selects: C{list} or L{Select}
        @param optype: whether to use the ALL, DISTINCT constructs: C{None} use neither, OPTYPE_ALL, or OPTYPE_DISTINCT
        @type optype: C{str}
        """
        
        if isinstance(selects, Select):
            selects = (selects,)
        self.selects = selects
        self.optype = optype
        
        for select in self.selects:
            if not isinstance(select, Select):
                raise DALError("Must have SELECT statements in a set expression")
        if self.optype not in (None, SetExpression.OPTYPE_ALL, SetExpression.OPTYPE_DISTINCT,):
            raise DALError("Must have either 'all' or 'distinct' in a set expression")

    def subSQL(self, queryGenerator, allTables):
        result = SQLFragment()
        for select in self.selects:
            result.append(self.setOpSQL(queryGenerator))
            if self.optype == SetExpression.OPTYPE_ALL:
                result.append(SQLFragment("ALL "))
            elif self.optype == SetExpression.OPTYPE_DISTINCT:
                result.append(SQLFragment("DISTINCT "))
            result.append(select.subSQL(queryGenerator, allTables))
        return result

    def allColumns(self):
        return []

class Union(SetExpression):
    """
    A UNION construct used inside a SELECT.
    """
    def setOpSQL(self, queryGenerator):
        return SQLFragment(" UNION ")

class Intersect(SetExpression):
    """
    An INTERSECT construct used inside a SELECT.
    """
    def setOpSQL(self, queryGenerator):
        return SQLFragment(" INTERSECT ")

class Except(SetExpression):
    """
    An EXCEPT construct used inside a SELECT.
    """
    def setOpSQL(self, queryGenerator):
        if queryGenerator.dialect == POSTGRES_DIALECT:
            return SQLFragment(" EXCEPT ")
        elif queryGenerator.dialect == ORACLE_DIALECT:
            return SQLFragment(" MINUS ")
        else:
            raise NotImplementedError("Unsupported dialect")

class Select(_Statement):
    """
    'select' statement.
    """

    def __init__(self, columns=None, Where=None, From=None, OrderBy=None,
                 GroupBy=None, Limit=None, ForUpdate=False, NoWait=False, Ascending=None,
                 Having=None, Distinct=False, As=None,
                 SetExpression=None):
        self.From = From
        self.Where = Where
        self.Distinct = Distinct
        if not isinstance(OrderBy, (list, tuple, type(None))):
            OrderBy = [OrderBy]
        self.OrderBy = OrderBy
        if not isinstance(GroupBy, (list, tuple, type(None))):
            GroupBy = [GroupBy]
        self.GroupBy = GroupBy
        self.Limit = Limit
        self.Having = Having
        self.SetExpression = SetExpression

        if columns is None:
            columns = ALL_COLUMNS
        else:
            if not _columnsMatchTables(columns, From.tables()):
                raise TableMismatch()
            columns = _SomeColumns(columns)
        self.columns = columns
        
        self.ForUpdate = ForUpdate
        self.NoWait = NoWait
        self.Ascending = Ascending
        self.As = As

        # A FROM that uses a sub-select will need the AS alias name
        if isinstance(self.From, Select):
            if self.From.As is None:
                self.From.As = ""

    def __eq__(self, other):
        """
        Create a comparison.
        """
        if isinstance(other, (list, tuple)):
            other = Tuple(other)
        return CompoundComparison(other, '=', self)


    def _toSQL(self, queryGenerator):
        """
        @return: a 'select' statement with placeholders and arguments

        @rtype: L{SQLFragment}
        """
        if self.SetExpression is not None:
            stmt = SQLFragment("(")
        else:
            stmt = SQLFragment()
        stmt.append(SQLFragment("select "))
        if self.Distinct:
            stmt.text += "distinct "
        allTables = self.From.tables()
        stmt.append(self.columns.subSQL(queryGenerator, allTables))
        stmt.text += " from "
        stmt.append(self.From.subSQL(queryGenerator, allTables))
        if self.Where is not None:
            wherestmt = self.Where.subSQL(queryGenerator, allTables)
            stmt.text += " where "
            stmt.append(wherestmt)
        if self.GroupBy is not None:
            stmt.text += " group by "
            fst = True
            for subthing in self.GroupBy:
                if fst:
                    fst = False
                else:
                    stmt.text += ', '
                stmt.append(subthing.subSQL(queryGenerator, allTables))
        if self.Having is not None:
            havingstmt = self.Having.subSQL(queryGenerator, allTables)
            stmt.text += " having "
            stmt.append(havingstmt)
        if self.SetExpression is not None:
            stmt.append(SQLFragment(")"))
            stmt.append(self.SetExpression.subSQL(queryGenerator, allTables))
        if self.OrderBy is not None:
            stmt.text += " order by "
            fst = True
            for subthing in self.OrderBy:
                if fst:
                    fst = False
                else:
                    stmt.text += ', '
                stmt.append(subthing.subSQL(queryGenerator, allTables))
            if self.Ascending is not None:
                if self.Ascending:
                    kw = " asc"
                else:
                    kw = " desc"
                stmt.append(SQLFragment(kw))
        if self.ForUpdate:
            stmt.text += " for update"
            if self.NoWait:
                stmt.text += " nowait"
        if self.Limit is not None:
            limitConst = Constant(self.Limit).subSQL(queryGenerator, allTables)
            if queryGenerator.dialect == ORACLE_DIALECT:
                wrapper = SQLFragment("select * from (")
                wrapper.append(stmt)
                wrapper.append(SQLFragment(") where ROWNUM <= "))
                stmt = wrapper
            else:
                stmt.text += " limit "
            stmt.append(limitConst)
        return stmt


    def subSQL(self, queryGenerator, allTables):
        result = SQLFragment("(")
        result.append(self.toSQL(queryGenerator))
        result.append(SQLFragment(")"))
        if self.As is not None:
            if self.As == "":
                self.As = queryGenerator.nextGeneratedID()
            result.append(SQLFragment(" %s" % (self.As,)))
        return result


    def _resultColumns(self):
        """
        Determine the list of L{ColumnSyntax} objects that will represent the
        result.  Normally just the list of selected columns; if wildcard syntax
        is used though, determine the ordering from the database.
        """
        if self.columns is ALL_COLUMNS:
            # TODO: Possibly this rewriting should always be done, before even
            # executing the query, so that if we develop a schema mismatch with
            # the database (additional columns), the application will still see
            # the right rows.
            for table in self.From.tables():
                for column in table:
                    yield column
        else:
            for column in self.columns.columns:
                yield column

    def tables(self):
        """
        Determine the tables used by the result columns.
        """
        if self.columns is ALL_COLUMNS:
            # TODO: Possibly this rewriting should always be done, before even
            # executing the query, so that if we develop a schema mismatch with
            # the database (additional columns), the application will still see
            # the right rows.
            return self.From.tables()
        else:
            tables = set([column.model.table for column in self.columns.columns if isinstance(column, ColumnSyntax)])
            for table in self.From.tables():
                tables.add(table.model)
            return [TableSyntax(table) for table in tables]
        

def _commaJoined(stmts):
    first = True
    cstatement = SQLFragment()
    for stmt in stmts:
        if first:
            first = False
        else:
            cstatement.append(SQLFragment(", "))
        cstatement.append(stmt)
    return cstatement



def _inParens(stmt):
    result = SQLFragment("(")
    result.append(stmt)
    result.append(SQLFragment(")"))
    return result



def _fromSameTable(columns):
    """
    Extract the common table used by a list of L{Column} objects, raising
    L{TableMismatch}.
    """
    table = columns[0].table
    for column in columns:
        if table is not column.table:
            raise TableMismatch("Columns must all be from the same table.")
    return table



def _modelsFromMap(columnMap):
    """
    Get the L{Column} objects from a mapping of L{ColumnSyntax} to values.
    """
    return [c.model for c in columnMap.keys()]



class _CommaList(object):
    def __init__(self, subfragments):
        self.subfragments = subfragments


    def subSQL(self, queryGenerator, allTables):
        return _commaJoined(f.subSQL(queryGenerator, allTables)
                            for f in self.subfragments)



class _DMLStatement(_Statement):
    """
    Common functionality of Insert/Update/Delete statements.
    """

    def _returningClause(self, queryGenerator, stmt, allTables):
        """
        Add a dialect-appropriate 'returning' clause to the end of the given SQL
        statement.

        @param queryGenerator: describes the database we are generating the statement
            for.

        @type queryGenerator: L{QueryGenerator}

        @param stmt: the SQL fragment generated without the 'returning' clause
        @type stmt: L{SQLFragment}

        @param allTables: all tables involved in the query; see any C{subSQL}
            method.

        @return: the C{stmt} parameter.
        """
        retclause = self.Return
        if isinstance(retclause, (tuple, list)):
            retclause = _CommaList(retclause)
        if retclause is not None:
            stmt.text += ' returning '
            stmt.append(retclause.subSQL(queryGenerator, allTables))
            if queryGenerator.dialect == ORACLE_DIALECT:
                stmt.text += ' into '
                params = []
                retvals = self._returnAsList()
                for n, _ignore_v in enumerate(retvals):
                    params.append(
                        Constant(Parameter("oracle_out_" + str(n)))
                        .subSQL(queryGenerator, allTables)
                    )
                stmt.append(_commaJoined(params))
        return stmt


    def _returnAsList(self):
        if not isinstance(self.Return, (tuple, list)):
            return [self.Return]
        else:
            return self.Return


    def _extraVars(self, txn, queryGenerator):
        if self.Return is None:
            return []
        result = []
        rvars = self._returnAsList()
        if queryGenerator.dialect == ORACLE_DIALECT:
            for n, v in enumerate(rvars):
                result.append(("oracle_out_" + str(n), _OracleOutParam(v)))
        return result


    def _extraResult(self, result, outvars, queryGenerator):
        if queryGenerator.dialect == ORACLE_DIALECT and self.Return is not None:
            def processIt(shouldBeNone):
                result = [[v.value for _ignore_k, v in outvars]]
                return result
            return result.addCallback(processIt)
        else:
            return result


    def _resultColumns(self):
        return self._returnAsList()



class _OracleOutParam(object):
    """
    A parameter that will be populated using the cx_Oracle API for host
    variables.
    """
    implements(IDerivedParameter)

    def __init__(self, columnSyntax):
        self.typeID = columnSyntax.model.type.name.lower()


    def preQuery(self, cursor):
        typeMap = {'integer': cx_Oracle.NUMBER,
                   'text': cx_Oracle.NCLOB,
                   'varchar': cx_Oracle.STRING,
                   'timestamp': cx_Oracle.TIMESTAMP}
        self.var = cursor.var(typeMap[self.typeID])
        return self.var


    def postQuery(self, cursor):
        self.value = mapOracleOutputType(self.var.getvalue())
        self.var = None



class Insert(_DMLStatement):
    """
    'insert' statement.
    """

    def __init__(self, columnMap, Return=None):
        self.columnMap = columnMap
        self.Return = Return
        columns = _modelsFromMap(columnMap)
        table = _fromSameTable(columns)
        required = [column for column in table.columns if column.needsValue()]
        unspecified = [column for column in required
                       if column not in columns]
        if unspecified:
            raise NotEnoughValues(
                'Columns [%s] required.' %
                    (', '.join([c.name for c in unspecified])))


    def _toSQL(self, queryGenerator):
        """
        @return: a 'insert' statement with placeholders and arguments

        @rtype: L{SQLFragment}
        """
        columnsAndValues = self.columnMap.items()
        tableModel = columnsAndValues[0][0].model.table
        specifiedColumnModels = [x.model for x in self.columnMap.keys()]
        if queryGenerator.dialect == ORACLE_DIALECT:
            # See test_nextSequenceDefaultImplicitExplicitOracle.
            for column in tableModel.columns:
                if isinstance(column.default, Sequence):
                    columnSyntax = ColumnSyntax(column)
                    if column not in specifiedColumnModels:
                        columnsAndValues.append(
                            (columnSyntax, SequenceSyntax(column.default))
                        )
        sortedColumns = sorted(columnsAndValues,
                               key=lambda (c, v): c.model.name)
        allTables = []
        stmt = SQLFragment('insert into ')
        stmt.append(TableSyntax(tableModel).subSQL(queryGenerator, allTables))
        stmt.append(SQLFragment(" "))
        stmt.append(_inParens(_commaJoined(
            [c.subSQL(queryGenerator, allTables) for (c, _ignore_v) in
             sortedColumns])))
        stmt.append(SQLFragment(" values "))
        stmt.append(_inParens(_commaJoined(
            [_convert(v).subSQL(queryGenerator, allTables)
             for (c, v) in sortedColumns])))
        return self._returningClause(queryGenerator, stmt, allTables)



def _convert(x):
    """
    Convert a value to an appropriate SQL AST node.  (Currently a simple
    isinstance, could be promoted to use adaptation if we want to get fancy.)
    """
    if isinstance(x, ExpressionSyntax):
        return x
    else:
        return Constant(x)



class Update(_DMLStatement):
    """
    'update' statement
    """

    def __init__(self, columnMap, Where, Return=None):
        super(Update, self).__init__()
        _fromSameTable(_modelsFromMap(columnMap))
        self.columnMap = columnMap
        self.Where = Where
        self.Return = Return


    def _toSQL(self, queryGenerator):
        """
        @return: a 'insert' statement with placeholders and arguments

        @rtype: L{SQLFragment}
        """
        sortedColumns = sorted(self.columnMap.items(),
                               key=lambda (c, v): c.model.name)
        allTables = []
        result = SQLFragment('update ')
        result.append(
            TableSyntax(sortedColumns[0][0].model.table).subSQL(
                queryGenerator, allTables)
        )
        result.text += ' set '
        result.append(
            _commaJoined(
                [c.subSQL(queryGenerator, allTables).append(
                    SQLFragment(" = ").subSQL(queryGenerator, allTables)
                ).append(_convert(v).subSQL(queryGenerator, allTables))
                    for (c, v) in sortedColumns]
            )
        )
        result.append(SQLFragment( ' where '))
        result.append(self.Where.subSQL(queryGenerator, allTables))
        return self._returningClause(queryGenerator, result, allTables)



class Delete(_DMLStatement):
    """
    'delete' statement.
    """

    def __init__(self, From, Where, Return=None):
        """
        If Where is None then all rows will be deleted.
        """
        self.From = From
        self.Where = Where
        self.Return = Return


    def _toSQL(self, queryGenerator):
        result = SQLFragment()
        allTables = self.From.tables()
        result.text += 'delete from '
        result.append(self.From.subSQL(queryGenerator, allTables))
        if self.Where is not None:
            result.text += ' where '
            result.append(self.Where.subSQL(queryGenerator, allTables))
        return self._returningClause(queryGenerator, result, allTables)



class _LockingStatement(_Statement):
    """
    A statement related to lock management, which implicitly has no results.
    """
    def _resultColumns(self):
        """
        No columns should be expected, so return an infinite iterator of None.
        """
        return repeat(None)



class Lock(_LockingStatement):
    """
    An SQL 'lock' statement.
    """

    def __init__(self, table, mode):
        self.table = table
        self.mode = mode


    @classmethod
    def exclusive(cls, table):
        return cls(table, 'exclusive')


    def _toSQL(self, queryGenerator):
        return SQLFragment('lock table ').append(
            self.table.subSQL(queryGenerator, [self.table])).append(
            SQLFragment(' in %s mode' % (self.mode,)))



class Savepoint(_LockingStatement):
    """
    An SQL 'savepoint' statement.
    """

    def __init__(self, name):
        self.name = name


    def _toSQL(self, queryGenerator):
        return SQLFragment('savepoint %s' % (self.name,))


class RollbackToSavepoint(_LockingStatement):
    """
    An SQL 'rollback to savepoint' statement.
    """

    def __init__(self, name):
        self.name = name


    def _toSQL(self, queryGenerator):
        return SQLFragment('rollback to savepoint %s' % (self.name,))


class ReleaseSavepoint(_LockingStatement):
    """
    An SQL 'release savepoint' statement.
    """

    def __init__(self, name):
        self.name = name


    def _toSQL(self, queryGenerator):
        return SQLFragment('release savepoint %s' % (self.name,))



class SavepointAction(object):

    def __init__(self, name):
        self._name = name


    def acquire(self, txn):
        return Savepoint(self._name).on(txn)


    def rollback(self, txn):
        return RollbackToSavepoint(self._name).on(txn)


    def release(self, txn):
        if txn.dialect == ORACLE_DIALECT:
            # There is no 'release savepoint' statement in oracle, but then, we
            # don't need it because there's no resource to manage.  Just don't
            # do anything.
            return NoOp()
        else:
            return ReleaseSavepoint(self._name).on(txn)



class NoOp(object):
    def on(self, *a, **kw):
        return succeed(None)



class SQLFragment(object):
    """
    Combination of SQL text and arguments; a statement which may be executed
    against a database.
    """

    def __init__(self, text="", parameters=None):
        self.text = text
        if parameters is None:
            parameters = []
        self.parameters = parameters


    def bind(self, **kw):
        params = []
        for parameter in self.parameters:
            if isinstance(parameter, Parameter):
                params.append(kw[parameter.name])
            else:
                params.append(parameter)
        return SQLFragment(self.text, params)


    def append(self, anotherStatement):
        self.text += anotherStatement.text
        self.parameters += anotherStatement.parameters
        return self


    def __eq__(self, stmt):
        if not isinstance(stmt, SQLFragment):
            return NotImplemented
        return (self.text, self.parameters) == (stmt.text, stmt.parameters)


    def __ne__(self, stmt):
        if not isinstance(stmt, SQLFragment):
            return NotImplemented
        return not self.__eq__(stmt)


    def __repr__(self):
        return self.__class__.__name__ + repr((self.text, self.parameters))


    def subSQL(self, queryGenerator, allTables):
        return self



class Parameter(object):

    def __init__(self, name):
        self.name = name


    def __eq__(self, param):
        if not isinstance(param, Parameter):
            return NotImplemented
        return self.name == param.name


    def __ne__(self, param):
        if not isinstance(param, Parameter):
            return NotImplemented
        return not self.__eq__(param)


    def __repr__(self):
        return 'Parameter(%r)' % (self.name,)


# Common helpers:

# current timestamp in UTC format.  Hack to support standard syntax for this,
# rather than the compatibility procedure found in various databases.
utcNowSQL = NamedValue("CURRENT_TIMESTAMP at time zone 'UTC'")

# You can't insert a column with no rows.  In SQL that just isn't valid syntax,
# and in this DAL you need at least one key or we can't tell what table you're
# talking about.  Luckily there's the 'default' keyword to the rescue, which, in
# the context of an INSERT statement means 'use the default value explicitly'.
# (Although this is a special keyword in a CREATE statement, in an INSERT it
# behaves like an expression to the best of my knowledge.)
default = NamedValue('default')

