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

    def __getattr__(self, attr):
        return ColumnSyntax(self.model.columnNamed(attr))


    def __iter__(self):
        for column in self.model.columns:
            yield ColumnSyntax(column)



class ColumnSyntax(Syntax):
    """
    Syntactic convenience for L{Column}.
    """

    modelType = Column

    def __eq__(self, other):
        return ConstantComparison(self.model.name, '=', other)



class ConstantComparison(object):

    def __init__(self, a, op, b):
        self.a = a
        self.op = op
        self.b = b


    def toSQL(self):
        return (' '.join([self.a, self.op, '?']), [self.b])



class Select(object):
    """
    'select' statement.
    """

    def __init__(self, Where=None, From=None):
        self.From = From
        self.Where = Where


    def toSQL(self):
        """
        @return: a 2-tuple of (sql, args).
        """
        sql = "select * from " + self.From.model.name
        args = []
        if self.Where is not None:
            moreSQL, moreArgs = self.Where.toSQL()
            sql += (" where " + moreSQL)
            args.extend(moreArgs)
        return (sql, args)

