# -*- test-case-name: txdav.base.datastore.test.test_sqlsyntax -*-
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
Syntax wrappers and generators for SQL.
"""

from txdav.base.datastore.sqlmodel import Schema, Table, Column

class Syntax(object):
    """
    Base class for syntactic convenience.

    This class will define dynamic attribute access to represent its underlying
    model as a Python namespace.

    You can access the underlying model as '.model'.
    """

    modelType = None

    def __init__(self, model):
        if not isinstance(model, self.modelType):
            # make sure we don't get a misleading repr()
            raise ValueError("type mismatch: %r %r", type(self), model)
        self.model = model


    def __repr__(self):
        return '<Syntax for: %r>' % (self.model,)



class SchemaSyntax(Syntax):
    """
    Syntactic convenience for L{Schema}.
    """

    modelType = Schema

    def __getattr__(self, attr):
        return TableSyntax(self.model.tableNamed(attr))

    def __iter__(self):
        for table in self.model.tables:
            yield TableSyntax(table)



class TableSyntax(Syntax):
    """
    Syntactic convenience for L{Table}.
    """

    modelType = Table

    def join(self, otherTableSyntax, on, type=''):
        return Join(self, type, otherTableSyntax, on)


    def toSQL(self, placeholder, quote):
        """
        For use in a 'from' clause.
        """
        # XXX maybe there should be a specific method which is only invoked
        # from the FROM clause, that only tables and joins would implement?
        return SQLStatement(self.model.name)


    def __getattr__(self, attr):
        return ColumnSyntax(self.model.columnNamed(attr))


    def __iter__(self):
        for column in self.model.columns:
            yield ColumnSyntax(column)



class Join(object):
    def __init__(self, firstTable, type, secondTableOrJoin, on):
        self.firstTable = firstTable
        self.type = type
        self.secondTableOrJoin = secondTableOrJoin
        self.on = on


    def toSQL(self, placeholder, quote):
        stmt = SQLStatement()
        stmt.append(self.firstTable.toSQL(placeholder, quote))
        stmt.text += ' '
        if self.type:
            stmt.text += self.type
            stmt.text += ' '
        stmt.text += 'join '
        stmt.append(self.secondTableOrJoin.toSQL(placeholder, quote))
        stmt.text += ' on '
        stmt.append(self.on.toSQL(placeholder, quote))

        return stmt



def comparison(comparator):
    def __(self, other):
        if isinstance(other, ColumnSyntax):
            return ColumnComparison(self, comparator, other)
        else:
            return ConstantComparison(self, comparator, other)
    return __


class ColumnSyntax(Syntax):
    """
    Syntactic convenience for L{Column}.
    """

    modelType = Column

    __eq__ = comparison('=')
    __ne__ = comparison('!=')
    __gt__ = comparison('>')
    __ge__ = comparison('>=')
    __lt__ = comparison('<')
    __le__ = comparison('<=')
    __add__ = comparison("+")
    __sub__ = comparison("-")



class Comparison(object):

    def __init__(self, a, op, b):
        self.a = a
        self.op = op
        self.b = b


    def __nonzero__(self):
        raise ValueError(
            "column comparisons should not be tested for truth value")


    def booleanOp(self, operand, other):
        return CompoundComparison(self, operand, other)


    def And(self, other):
        return self.booleanOp('and', other)


    def Or(self, other):
        return self.booleanOp('or', other)



class ConstantComparison(Comparison):

    def toSQL(self, placeholder, quote):
        return SQLStatement(
            ' '.join([self.a.model.name, self.op, placeholder]), [self.b])



class ColumnComparison(Comparison):

    def toSQL(self, placeholder, quote):
        return SQLStatement(
            ' '.join([self.a.model.name, self.op, self.b.model.name]), [])


class CompoundComparison(Comparison):
    """
    A compound comparison; two or more constraints, joined by an operation
    (currently only AND or OR).
    """

    def toSQL(self, placeholder, quote):
        stmt = SQLStatement()
        stmt.append(self.a.toSQL(placeholder, quote))
        stmt.text += ' %s ' % (self.op,)
        stmt.append(self.b.toSQL(placeholder, quote))
        return stmt



class Select(object):
    """
    'select' statement.
    """

    def __init__(self, Where=None, From=None):
        self.From = From
        self.Where = Where


    def toSQL(self, placeholder="?", quote=lambda x: x):
        """
        @return: a 2-tuple of (sql, args).
        """
        stmt = SQLStatement(quote("select * from "))
        stmt.append(self.From.toSQL(placeholder, quote))
        if self.Where is not None:
            wherestmt = self.Where.toSQL(placeholder, quote)
            stmt.text += quote(" where ")
            stmt.append(wherestmt)
        return stmt


class SQLStatement(object):
    """
    Combination of SQL text and arguments; a statement which may be executed
    against a database.
    """

    def __init__(self, text="", parameters=None):
        self.text = text
        if parameters is None:
            parameters = []
        self.parameters = parameters


    def append(self, anotherStatement):
        self.text += anotherStatement.text
        self.parameters += anotherStatement.parameters


    def __eq__(self, stmt):
        if not isinstance(stmt, SQLStatement):
            return NotImplemented
        return (self.text, self.parameters) == (stmt.text, stmt.parameters)


    def __ne__(self, stmt):
        if not isinstance(stmt, SQLStatement):
            return NotImplemented
        return not self.__eq__(stmt)


    def __repr__(self):
        return 'SQLStatement' + repr((self.text, self.parameters))
