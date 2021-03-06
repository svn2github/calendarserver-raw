# -*- test-case-name: twext.enterprise.dal.test.test_parseschema -*-
##
# Copyright (c) 2010-2013 Apple Inc. All rights reserved.
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
Model classes for SQL.
"""
from twisted.python.util import FancyEqMixin

class SQLType(object):
    """
    A data-type as defined in SQL; like "integer" or "real" or "varchar(255)".

    @ivar name: the name of this type.
    @type name: C{str}

    @ivar length: the length of this type, if it is a type like 'varchar' or
        'character' that comes with a parenthetical length.
    @type length: C{int} or C{NoneType}
    """

    def __init__(self, name, length):
        _checkstr(name)
        self.name = name
        self.length = length


    def __eq__(self, other):
        """
        Compare equal to other L{SQLTypes} with matching name and length.
        """
        if not isinstance(other, SQLType):
            return NotImplemented
        return (self.name, self.length) == (other.name, other.length)


    def __ne__(self, other):
        """
        (Inequality is the opposite of equality.)
        """
        if not isinstance(other, SQLType):
            return NotImplemented
        return not self.__eq__(other)


    def __repr__(self):
        """
        A useful string representation which includes the name and length if
        present.
        """
        if self.length:
            lendesc = '(%s)' % (self.length)
        else:
            lendesc = ''
        return '<SQL Type: %r%s>' % (self.name, lendesc)



class Constraint(object):
    """
    A constraint on a set of columns.

    @ivar type: the type of constraint.  Currently, only C{'UNIQUE'} and C{'NOT
        NULL'} are supported.
    @type type: C{str}

    @ivar affectsColumns: Columns affected by this constraint.

    @type affectsColumns: C{list} of L{Column}
    """

    # Values for 'type' attribute:
    NOT_NULL = 'NOT NULL'
    UNIQUE = 'UNIQUE'

    def __init__(self, type, affectsColumns, name=None):
        self.affectsColumns = affectsColumns
        # XXX: possibly different constraint types should have different
        # classes?
        self.type = type
        self.name = name



class Check(Constraint):
    """
    A 'check' constraint, which evaluates an SQL expression.

    @ivar expression: the expression that should evaluate to True.
    @type expression: L{twext.enterprise.dal.syntax.ExpressionSyntax}
    """
    # XXX TODO: model for expression, rather than

    def __init__(self, syntaxExpression, name=None):
        self.expression = syntaxExpression
        super(Check, self).__init__(
            'CHECK', [c.model for c in self.expression.allColumns()], name
        )



class ProcedureCall(object):
    """
    An invocation of a stored procedure or built-in function.
    """

    def __init__(self, name, args):
        _checkstr(name)
        self.name = name
        self.args = args



class NO_DEFAULT(object):
    """
    Placeholder value for not having a default.  (C{None} would not be suitable,
    as that would imply a default of C{NULL}).
    """



def _checkstr(x):
    """
    Verify that C{x} is a C{str}.  Raise a L{ValueError} if not.  This is to
    prevent pollution with unicode values.
    """
    if not isinstance(x, str):
        raise ValueError("%r is not a str." % (x,))



class Column(FancyEqMixin, object):
    """
    A column from a table.

    @ivar table: The L{Table} to which this L{Column} belongs.
    @type table: L{Table}

    @ivar name: The unqualified name of this column.  For example, in the case
        of a column BAR in a table FOO, this would be the string C{'BAR'}.
    @type name: C{str}

    @ivar type: The declared type of this column.
    @type type: L{SQLType}

    @ivar references: If this column references a foreign key on another table,
        this will be a reference to that table; otherwise (normally) C{None}.
    @type references: L{Table} or C{NoneType}

    @ivar deleteAction: If this column references another table, home will this column's
        row be altered when the matching row in that other table is deleted? Possible values are
        None - for 'on delete no action'
        'cascade' - for 'on delete cascade'
        'set null' - for 'on delete set null'
        'set default' - for 'on delete set default'
    @type deleteAction: C{bool}
    """

    compareAttributes = 'table name'.split()

    def __init__(self, table, name, type):
        _checkstr(name)
        self.table = table
        self.name = name
        self.type = type
        self.default = NO_DEFAULT
        self.references = None
        self.deleteAction = None


    def __repr__(self):
        return '<Column (%s %r)>' % (self.name, self.type)


    def compare(self, other):
        """
        Return the differences between two columns.

        @param other: the column to compare with
        @type other: L{Column}
        """

        results = []

        # TODO: sql_dump does not do types write now - so ignore this
#        if self.type != other.type:
#            results.append("Table: %s, mismatched column type: %s" % (self.table.name, self.name))

        # TODO: figure out how to compare default, references and deleteAction
        return results


    def canBeNull(self):
        """
        Can this column ever be C{NULL}, i.e. C{None}?  In other words, is it
        free of any C{NOT NULL} constraints?

        @return: C{True} if so, C{False} if not.
        """
        for constraint in self.table.constraints:
            if self in constraint.affectsColumns:
                if constraint.type is Constraint.NOT_NULL:
                    return False
        return True


    def setDefaultValue(self, value):
        """
        Change the default value of this column.  (Should only be called during
        schema parsing.)
        """
        self.default = value


    def needsValue(self):
        """
        Does this column require a value in C{INSERT} statements which create
        rows?

        @return: C{True} for L{Column}s with no default specified which also
            cannot be NULL, C{False} otherwise.

        @rtype: C{bool}
        """
        return not (self.canBeNull() or
                    (self.default not in (None, NO_DEFAULT)))


    def doesReferenceName(self, name):
        """
        Change this column to refer to a table in the schema.  (Should only be
        called during schema parsing.)

        @param name: the name of a L{Table} in this L{Column}'s L{Schema}.
        @type name: L{str}
        """
        self.references = self.table.schema.tableNamed(name)



class Table(FancyEqMixin, object):
    """
    A set of columns.

    @ivar descriptiveComment: A docstring for the table.  Parsed from a '--'
        comment preceding this table in the SQL schema file that was parsed, if
        any.
    @type descriptiveComment: C{str}

    @ivar schema: a reference to the L{Schema} to which this table belongs.

    @ivar primaryKey: a C{list} of L{Column} objects representing the primary
        key of this table, or C{None} if no primary key has been specified.
    """

    compareAttributes = 'schema name'.split()

    def __init__(self, schema, name):
        _checkstr(name)
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


    def compare(self, other):
        """
        Return the differences between two tables.

        @param other: the table to compare with
        @type other: L{Table}
        """

        results = []

        myColumns = dict([(item.name.lower(), item) for item in self.columns])
        otherColumns = dict([(item.name.lower(), item) for item in other.columns])
        for item in set(myColumns.keys()) ^ set(otherColumns.keys()):
            results.append("Table: %s, missing column: %s" % (self.name, item,))

        for name in set(myColumns.keys()) & set(otherColumns.keys()):
            results.extend(myColumns[name].compare(otherColumns[name]))

        # TODO: figure out how to compare schemaRows
        return results


    def columnNamed(self, name):
        """
        Retrieve a column from this table with a given name.

        @raise KeyError: if no such table exists.

        @return: a column

        @rtype: L{Column}
        """
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError("no such column: %r" % (name,))


    def addColumn(self, name, type):
        """
        A new column was parsed for this table.

        @param name: The unqualified name of the column.

        @type name: C{str}

        @param type: The L{SQLType} describing the column's type.
        """
        column = Column(self, name, type)
        self.columns.append(column)
        return column


    def tableConstraint(self, constraintType, columnNames):
        """
        This table is affected by a constraint.  (Should only be called during
        schema parsing.)

        @param constraintType: the type of constraint; either
            L{Constraint.NOT_NULL} or L{Constraint.UNIQUE}, currently.
        """
        affectsColumns = []
        for name in columnNames:
            affectsColumns.append(self.columnNamed(name))
        self.constraints.append(Constraint(constraintType, affectsColumns))


    def checkConstraint(self, protoExpression, name=None):
        """
        This table is affected by a 'check' constraint.  (Should only be called
        during schema parsing.)

        @param protoExpression: proto expression.
        """
        self.constraints.append(Check(protoExpression, name))


    def insertSchemaRow(self, values):
        """
        A statically-defined row was inserted as part of the schema itself.
        This is used for tables that want to track static enumerations, for
        example, but want to be referred to by a foreign key in other tables for
        proper referential integrity.

        Append this data to this L{Table}'s L{Table.schemaRows}.

        (Should only be called during schema parsing.)

        @param values: a C{list} of data items, one for each column in this
            table's current list of L{Column}s.
        """
        row = {}
        for column, value in zip(self.columns, values):
            row[column] = value
        self.schemaRows.append(row)


    def addComment(self, comment):
        """
        Add a comment to C{descriptiveComment}.

        @param comment: some additional descriptive text
        @type comment: C{str}
        """
        self.descriptiveComment = comment


    def uniques(self):
        """
        Get the groups of unique columns for this L{Table}.

        @return: an iterable of C{list}s of C{Column}s which are unique within
            this table.
        """
        for constraint in self.constraints:
            if constraint.type is Constraint.UNIQUE:
                yield list(constraint.affectsColumns)



class Index(object):
    """
    An L{Index} is an SQL index.
    """

    def __init__(self, schema, name, table, unique=False):
        self.name = name
        self.table = table
        self.unique = unique
        self.columns = []
        schema.indexes.append(self)


    def addColumn(self, column):
        self.columns.append(column)



class PseudoIndex(object):
    """
    A class used to represent explicit and implicit indexes. An implicit index is one the
    DB creates for primary key and unique columns in a table. An explicit index is one
    created by a CREATE [UNIQUE] INDEX statement. Because the name of an implicit index
    is implementation defined, instead we create a name based on the table name, uniqueness
    and column names.
    """

    def __init__(self, table, columns, unique=False):
        self.name = "%s%s:(%s)" % (table.name, "-unique" if unique else "", ",".join([col.name for col in columns]))


    def compare(self, other):
        """
        Return the differences between two indexes.

        @param other: the index to compare with
        @type other: L{Index}
        """

        # Nothing to do as name comparison will catch differences
        return []



class Sequence(FancyEqMixin, object):
    """
    A sequence object.
    """

    compareAttributes = 'name'.split()

    def __init__(self, schema, name):
        _checkstr(name)
        self.name = name
        self.referringColumns = []
        schema.sequences.append(self)


    def __repr__(self):
        return '<Sequence %r>' % (self.name,)


    def compare(self, other):
        """
        Return the differences between two sequences.

        @param other: the sequence to compare with
        @type other: L{Sequence}
        """

        # TODO: figure out whether to compare referringColumns attribute
        return []



def _namedFrom(name, sequence):
    """
    Retrieve an item with a given name attribute from a given sequence, or raise
    a L{KeyError}.
    """
    for item in sequence:
        if item.name == name:
            return item
    raise KeyError(name)



class Schema(object):
    """
    A schema containing tables, indexes, and sequences.
    """

    def __init__(self, filename='<string>'):
        self.filename = filename
        self.tables = []
        self.indexes = []
        self.sequences = []


    def __repr__(self):
        return '<Schema %r>' % (self.filename,)


    def compare(self, other):
        """
        Return the differences between two schemas.

        @param other: the schema to compare with
        @type other: L{Schema}
        """

        results = []

        def _compareLists(list1, list2, descriptor):
            myItems = dict([(item.name.lower()[:63], item) for item in list1])
            otherItems = dict([(item.name.lower()[:63], item) for item in list2])
            for item in set(myItems.keys()) - set(otherItems.keys()):
                results.append("Schema: %s, missing %s: %s" % (other.filename, descriptor, item,))
            for item in set(otherItems.keys()) - set(myItems.keys()):
                results.append("Schema: %s, missing %s: %s" % (self.filename, descriptor, item,))

            for name in set(myItems.keys()) & set(otherItems.keys()):
                results.extend(myItems[name].compare(otherItems[name]))

        _compareLists(self.tables, other.tables, "table")
        _compareLists(self.pseudoIndexes(), other.pseudoIndexes(), "index")
        _compareLists(self.sequences, other.sequences, "sequence")

        return results


    def pseudoIndexes(self):
        """
        Return a set of indexes that include "implicit" indexes from table/column constraints. The name of the
        index is formed from the table name and then list of columns.
        """
        results = []

        # First add the list of explicit indexes we have
        for index in self.indexes:
            results.append(PseudoIndex(index.table, index.columns, index.unique))

        # Now do implicit index for each table
        for table in self.tables:
            if table.primaryKey is not None:
                results.append(PseudoIndex(table, table.primaryKey, True))
            for constraint in table.constraints:
                if constraint.type == Constraint.UNIQUE:
                    results.append(PseudoIndex(table, constraint.affectsColumns, True))

        return results


    def tableNamed(self, name):
        return _namedFrom(name, self.tables)


    def sequenceNamed(self, name):
        return _namedFrom(name, self.sequences)


    def indexNamed(self, name):
        return _namedFrom(name, self.indexes)
