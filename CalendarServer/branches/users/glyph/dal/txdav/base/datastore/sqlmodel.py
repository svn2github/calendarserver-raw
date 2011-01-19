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


class SQLType(object):

    def __init__(self, name, length):
        self.name = name
        self.length = length


    def __eq__(self, other):
        if not isinstance(other, SQLType):
            return NotImplemented
        return (self.name, self.length) == (other.name, other.length)


    def __ne__(self, other):
        if not isinstance(other, SQLType):
            return NotImplemented
        return not self.__eq__(other)


    def __repr__(self):
        if self.length:
            lendesc = '(%s)' % (self.length)
        else:
            lendesc = ''
        return '<SQL Type: %r%s>' % (self.name, lendesc)



class Constraint(object):

    def __init__(self, type, affectsColumns, name=None):
        self.affectsColumns = affectsColumns
        self.name = name
        # XXX: possibly different constraint types should have different
        # classes?
        self.type = type



class ProcedureCall(object):
    """
    An invocation of a stored procedure or built-in function.
    """
    def __init__(self, name, args):
        self.name = name
        self.args = args


class NO_DEFAULT(object):
    """
    Placeholder value for not having a default.
    """


class Column(object):

    def __init__(self, table, name, type):
        self.table = table
        self.name = name
        self.type = type
        self.default = NO_DEFAULT
        # the table object
        self.references = None


    def __repr__(self):
        return '<Column (%s %r)>' % (self.name, self.type)


    def setDefaultValue(self, value):
        self.defaultValue = value


    def doesReferenceName(self, name):
        self.references = self.table.schema.tableNamed(name)
        if self.references.primaryKey.type != self.type:
            print 'Mismatch', self.references.primaryKey.type, self.type



class Table(object):

    def __init__(self, schema, name):
        self.descriptiveComment = ''
        self.schema = schema
        self.name = name
        self.columns = []
        self.constraints = []
        self.schemaRows = []
        self.primaryKey = None
        self.schema.tables.append(self)


    def __repr__(self):
        return '<Table %r:%r>' % (self.name, self.columns)


    def columnNamed(self, name):
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError("no such column: %r" % (name,))


    def addColumn(self, name, type):
        column = Column(self, name, type)
        self.columns.append(column)
        return column


    def tableConstraint(self, constraintType, columnNames):
        affectsColumns = []
        for name in columnNames:
            affectsColumns.append(self.columnNamed(name))
        self.constraints.append(Constraint(constraintType, columnNames))


    def insertSchemaRow(self, values):
        row = {}
        for column, value in zip(self.columns, values):
            row[column] = value
        self.schemaRows.append(row)


    def addComment(self, comment):
        self.descriptiveComment = comment



class Schema(object):
    """
    A schema containing tables, indexes, sequences.
    """

    def __init__(self, filename='<string>'):
        self.filename = filename
        self.tables = []


    def __repr__(self):
        return '<Schema %r>' % (self.filename,)


    def tableNamed(self, name):
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(name)

