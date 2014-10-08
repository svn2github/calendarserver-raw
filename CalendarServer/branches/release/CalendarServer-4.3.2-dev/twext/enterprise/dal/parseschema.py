# -*- test-case-name: twext.enterprise.dal.test.test_parseschema -*-
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
Parser for SQL schema.
"""

from itertools import chain

from sqlparse import parse, keywords
from sqlparse.tokens import (
    Keyword, Punctuation, Number, String, Name, Comparison as CompTok
)
from sqlparse.sql import (Comment, Identifier, Parenthesis, IdentifierList,
                          Function, Comparison)

from twext.enterprise.dal.model import (
    Schema, Table, SQLType, ProcedureCall, Constraint, Sequence, Index)

from twext.enterprise.dal.syntax import (
    ColumnSyntax, CompoundComparison, Constant, Function as FunctionSyntax
)



def _fixKeywords():
    """
    Work around bugs in SQLParse, adding SEQUENCE as a keyword (since it is
    treated as one in postgres) and removing ACCESS and SIZE (since we use those
    as column names).  Technically those are keywords in SQL, but they aren't
    treated as such by postgres's parser.
    """
    keywords.KEYWORDS['SEQUENCE'] = Keyword
    for columnNameKeyword in ['ACCESS', 'SIZE']:
        del keywords.KEYWORDS[columnNameKeyword]

_fixKeywords()



def tableFromCreateStatement(schema, stmt):
    """
    Add a table from a CREATE TABLE sqlparse statement object.

    @param schema: The schema to add the table statement to.

    @type schema: L{Schema}

    @param stmt: The C{CREATE TABLE} statement object.

    @type stmt: L{Statement}
    """
    i = iterSignificant(stmt)
    expect(i, ttype=Keyword.DDL, value='CREATE')
    expect(i, ttype=Keyword, value='TABLE')
    function = expect(i, cls=Function)
    i = iterSignificant(function)
    name = expect(i, cls=Identifier).get_name().encode('utf-8')
    self = Table(schema, name)
    parens = expect(i, cls=Parenthesis)
    cp = _ColumnParser(self, iterSignificant(parens), parens)
    cp.parse()
    return self



def schemaFromPath(path):
    """
    Get a L{Schema}.

    @param path: a L{FilePath}-like object containing SQL.

    @return: a L{Schema} object with the contents of the given C{path} parsed
        and added to it as L{Table} objects.
    """
    schema = Schema(path.basename())
    schemaData = path.getContent()
    addSQLToSchema(schema, schemaData)
    return schema



def addSQLToSchema(schema, schemaData):
    """
    Add new SQL to an existing schema.

    @param schema: The schema to add the new SQL to.

    @type schema: L{Schema}

    @param schemaData: A string containing some SQL statements.

    @type schemaData: C{str}

    @return: the C{schema} argument
    """
    parsed = parse(schemaData)
    for stmt in parsed:
        preface = ''
        while stmt.tokens and not significant(stmt.tokens[0]):
            preface += str(stmt.tokens.pop(0))
        if not stmt.tokens:
            continue
        if stmt.get_type() == 'CREATE':
            createType = stmt.token_next(1, True).value.upper()
            if createType == u'TABLE':
                t = tableFromCreateStatement(schema, stmt)
                t.addComment(preface)
            elif createType == u'SEQUENCE':
                Sequence(schema,
                         stmt.token_next(2, True).get_name().encode('utf-8'))
            elif createType == u'INDEX':
                signifindex = iterSignificant(stmt)
                expect(signifindex, ttype=Keyword.DDL, value='CREATE')
                expect(signifindex, ttype=Keyword, value='INDEX')
                indexName = nameOrIdentifier(signifindex.next())
                expect(signifindex, ttype=Keyword, value='ON')
                [tableName, columnArgs] = iterSignificant(expect(signifindex,
                                                                 cls=Function))
                tableName = nameOrIdentifier(tableName)
                arggetter = iterSignificant(columnArgs)

                expect(arggetter, ttype=Punctuation, value=u'(')
                valueOrValues = arggetter.next()
                if isinstance(valueOrValues, IdentifierList):
                    valuelist = valueOrValues.get_identifiers()
                else:
                    valuelist = [valueOrValues]
                expect(arggetter, ttype=Punctuation, value=u')')

                idx = Index(schema, indexName, schema.tableNamed(tableName))
                for token in valuelist:
                    columnName = nameOrIdentifier(token)
                    idx.addColumn(idx.table.columnNamed(columnName))
        elif stmt.get_type() == 'INSERT':
            insertTokens = iterSignificant(stmt)
            expect(insertTokens, ttype=Keyword.DML, value='INSERT')
            expect(insertTokens, ttype=Keyword, value='INTO')
            tableName = expect(insertTokens, cls=Identifier).get_name()
            expect(insertTokens, ttype=Keyword, value='VALUES')
            values = expect(insertTokens, cls=Parenthesis)
            vals = iterSignificant(values)
            expect(vals, ttype=Punctuation, value='(')
            valuelist = expect(vals, cls=IdentifierList)
            expect(vals, ttype=Punctuation, value=')')
            rowData = []
            for ident in valuelist.get_identifiers():
                rowData.append(
                    {Number.Integer: int,
                     String.Single: _destringify}
                    [ident.ttype](ident.value)
                )

            schema.tableNamed(tableName).insertSchemaRow(rowData)
        else:
            print 'unknown type:', stmt.get_type()
    return schema



class _ColumnParser(object):
    """
    Stateful parser for the things between commas.
    """

    def __init__(self, table, parenIter, parens):
        """
        @param table: the L{Table} to add data to.

        @param parenIter: the iterator.
        """
        self.parens = parens
        self.iter = parenIter
        self.table = table


    def __iter__(self):
        """
        This object is an iterator; return itself.
        """
        return self


    def next(self):
        """
        Get the next L{IdentifierList}.
        """
        result = self.iter.next()
        if isinstance(result, IdentifierList):
            # Expand out all identifier lists, since they seem to pop up
            # incorrectly.  We should never see one in a column list anyway.
            # http://code.google.com/p/python-sqlparse/issues/detail?id=25
            while result.tokens:
                it = result.tokens.pop()
                if significant(it):
                    self.pushback(it)
            return self.next()
        return result


    def pushback(self, value):
        """
        Push the value back onto this iterator so it will be returned by the
        next call to C{next}.
        """
        self.iter = chain(iter((value,)), self.iter)


    def parse(self):
        """
        Parse everything.
        """
        expect(self.iter, ttype=Punctuation, value=u"(")
        while self.nextColumn():
            pass


    def nextColumn(self):
        """
        Parse the next column or constraint, depending on the next token.
        """
        maybeIdent = self.next()
        if maybeIdent.ttype == Name:
            return self.parseColumn(maybeIdent.value)
        elif isinstance(maybeIdent, Identifier):
            return self.parseColumn(maybeIdent.get_name())
        else:
            return self.parseConstraint(maybeIdent)


    def namesInParens(self, parens):
        parens = iterSignificant(parens)
        expect(parens, ttype=Punctuation, value="(")
        idorids = parens.next()
        if isinstance(idorids, Identifier):
            idnames = [idorids.get_name()]
        elif isinstance(idorids, IdentifierList):
            idnames = [x.get_name() for x in idorids.get_identifiers()]
        else:
            raise ViolatedExpectation("identifier or list", repr(idorids))
        expect(parens, ttype=Punctuation, value=")")
        return idnames


    def readExpression(self, parens):
        """
        Read a given expression from a Parenthesis object.  (This is currently
        a limited parser in support of simple CHECK constraints, not something
        suitable for a full WHERE Clause.)
        """
        parens = iterSignificant(parens)
        expect(parens, ttype=Punctuation, value="(")
        nexttok = parens.next()
        if isinstance(nexttok, Comparison):
            lhs, op, rhs = list(iterSignificant(nexttok))
            result = CompoundComparison(self.nameOrValue(lhs),
                                        op.value.encode("ascii"),
                                        self.nameOrValue(rhs))
        elif isinstance(nexttok, Identifier):
            # our version of SQLParse seems to break down and not create a nice
            # "Comparison" object when a keyword is present.  This is just a
            # simple workaround.
            lhs = self.nameOrValue(nexttok)
            op = expect(parens, ttype=CompTok).value.encode("ascii")
            funcName = expect(parens, ttype=Keyword).value.encode("ascii")
            rhs = FunctionSyntax(funcName)(*[
                ColumnSyntax(self.table.columnNamed(x)) for x in
                self.namesInParens(expect(parens, cls=Parenthesis))
            ])
            result = CompoundComparison(lhs, op, rhs)

        expect(parens, ttype=Punctuation, value=")")
        return result


    def nameOrValue(self, tok):
        """
        Inspecting a token present in an expression (for a CHECK constraint on
        this table), return a L{twext.enterprise.dal.syntax} object for that
        value.
        """
        if isinstance(tok, Identifier):
            return ColumnSyntax(self.table.columnNamed(tok.get_name()))
        elif tok.ttype == Number.Integer:
            return Constant(int(tok.value))


    def parseConstraint(self, constraintType):
        """
        Parse a 'free' constraint, described explicitly in the table as opposed
        to being implicitly associated with a column by being placed after it.
        """
        ident = None
        # TODO: make use of identifier in tableConstraint, currently only used
        # for checkConstraint.
        if constraintType.match(Keyword, 'CONSTRAINT'):
            ident = expect(self, cls=Identifier).get_name()
            constraintType = expect(self, ttype=Keyword)
        if constraintType.match(Keyword, 'PRIMARY'):
            expect(self, ttype=Keyword, value='KEY')
            names = self.namesInParens(expect(self, cls=Parenthesis))
            self.table.primaryKey = [self.table.columnNamed(n) for n in names]
        elif constraintType.match(Keyword, 'UNIQUE'):
            names = self.namesInParens(expect(self, cls=Parenthesis))
            self.table.tableConstraint(Constraint.UNIQUE, names)
        elif constraintType.match(Keyword, 'CHECK'):
            self.table.checkConstraint(self.readExpression(self.next()), ident)
        else:
            raise ViolatedExpectation('PRIMARY or UNIQUE', constraintType)
        return self.checkEnd(self.next())


    def checkEnd(self, val):
        """
        After a column or constraint, check the end.
        """
        if val.value == u",":
            return True
        elif val.value == u")":
            return False
        else:
            raise ViolatedExpectation(", or )", val)


    def parseColumn(self, name):
        """
        Parse a column with the given name.
        """
        typeName = self.next()
        if isinstance(typeName, Function):
            [funcIdent, args] = iterSignificant(typeName)
            typeName = funcIdent
            arggetter = iterSignificant(args)
            expect(arggetter, value=u'(')
            typeLength = int(expect(arggetter,
                                    ttype=Number.Integer).value.encode('utf-8'))
        else:
            maybeTypeArgs = self.next()
            if isinstance(maybeTypeArgs, Parenthesis):
                # type arguments
                significant = iterSignificant(maybeTypeArgs)
                expect(significant, value=u"(")
                typeLength = int(significant.next().value)
            else:
                # something else
                typeLength = None
                self.pushback(maybeTypeArgs)
        theType = SQLType(typeName.value.encode("utf-8"), typeLength)
        theColumn = self.table.addColumn(
            name=name.encode("utf-8"), type=theType
        )
        for val in self:
            if val.ttype == Punctuation:
                return self.checkEnd(val)
            else:
                expected = True
                def oneConstraint(t):
                    self.table.tableConstraint(t, [theColumn.name])

                if val.match(Keyword, 'PRIMARY'):
                    expect(self, ttype=Keyword, value='KEY')
                    # XXX check to make sure there's no other primary key yet
                    self.table.primaryKey = [theColumn]
                elif val.match(Keyword, 'UNIQUE'):
                    # XXX add UNIQUE constraint
                    oneConstraint(Constraint.UNIQUE)
                elif val.match(Keyword, 'NOT'):
                    # possibly not necessary, as 'NOT NULL' is a single keyword
                    # in sqlparse as of 0.1.2
                    expect(self, ttype=Keyword, value='NULL')
                    oneConstraint(Constraint.NOT_NULL)
                elif val.match(Keyword, 'NOT NULL'):
                    oneConstraint(Constraint.NOT_NULL)
                elif val.match(Keyword, 'CHECK'):
                    self.table.checkConstraint(self.readExpression(self.next()))
                elif val.match(Keyword, 'DEFAULT'):
                    theDefault = self.next()
                    if isinstance(theDefault, Parenthesis):
                        iDefault = iterSignificant(theDefault)
                        expect(iDefault, ttype=Punctuation, value="(")
                        theDefault = iDefault.next()
                    if isinstance(theDefault, Function):
                        thingo = theDefault.tokens[0].get_name()
                        parens = expectSingle(
                            theDefault.tokens[-1], cls=Parenthesis
                        )
                        pareniter = iterSignificant(parens)
                        if thingo.upper() == 'NEXTVAL':
                            expect(pareniter, ttype=Punctuation, value="(")
                            seqname = _destringify(
                                expect(pareniter, ttype=String.Single).value)
                            defaultValue = self.table.schema.sequenceNamed(
                                seqname
                            )
                            defaultValue.referringColumns.append(theColumn)
                        else:
                            defaultValue = ProcedureCall(thingo.encode('utf-8'),
                                                         parens)
                    elif theDefault.ttype == Number.Integer:
                        defaultValue = int(theDefault.value)
                    elif (theDefault.ttype == Keyword and
                          theDefault.value.lower() == 'false'):
                        defaultValue = False
                    elif (theDefault.ttype == Keyword and
                          theDefault.value.lower() == 'true'):
                        defaultValue = True
                    elif (theDefault.ttype == Keyword and
                          theDefault.value.lower() == 'null'):
                        defaultValue = None
                    elif theDefault.ttype == String.Single:
                        defaultValue = _destringify(theDefault.value)
                    else:
                        raise RuntimeError(
                            "not sure what to do: default %r" % (
                            theDefault))
                    theColumn.setDefaultValue(defaultValue)
                elif val.match(Keyword, 'REFERENCES'):
                    target = nameOrIdentifier(self.next())
                    theColumn.doesReferenceName(target)
                elif val.match(Keyword, 'ON'):
                    expect(self, ttype=Keyword.DML, value='DELETE')
                    expect(self, ttype=Keyword, value='CASCADE')
                    theColumn.cascade = True
                else:
                    expected = False
                if not expected:
                    print 'UNEXPECTED TOKEN:', repr(val), theColumn
                    print self.parens
                    import pprint
                    pprint.pprint(self.parens.tokens)
                    return 0




class ViolatedExpectation(Exception):
    """
    An expectation about the structure of the SQL syntax was violated.
    """

    def __init__(self, expected, got):
        self.expected = expected
        self.got = got
        super(ViolatedExpectation, self).__init__(
            "Expected %r got %s" % (expected, got)
        )



def nameOrIdentifier(token):
    """
    Determine if the given object is a name or an identifier, and return the
    textual value of that name or identifier.

    @rtype: L{str}
    """
    if isinstance(token, Identifier):
        return token.get_name()
    elif token.ttype == Name:
        return token.value
    else:
        raise ViolatedExpectation("identifier or name", repr(token))



def expectSingle(nextval, ttype=None, value=None, cls=None):
    """
    Expect some properties from retrieved value.

    @param ttype: A token type to compare against.

    @param value: A value to compare against.

    @param cls: A class to check if the value is an instance of.

    @raise ViolatedExpectation: if an unexpected token is found.

    @return: C{nextval}, if it matches.
    """
    if ttype is not None:
        if nextval.ttype != ttype:
            raise ViolatedExpectation(ttype, '%s:%r' % (nextval.ttype, nextval))
    if value is not None:
        if nextval.value.upper() != value.upper():
            raise ViolatedExpectation(value, nextval.value)
    if cls is not None:
        if nextval.__class__ != cls:
            raise ViolatedExpectation(cls, '%s:%r' %
                                      (nextval.__class__.__name__, nextval))
    return nextval



def expect(iterator, **kw):
    """
    Retrieve a value from an iterator and check its properties.  Same signature
    as L{expectSingle}, except it takes an iterator instead of a value.

    @see: L{expectSingle}
    """
    nextval = iterator.next()
    return expectSingle(nextval, **kw)



def significant(token):
    """
    Determine if the token is 'significant', i.e. that it is not a comment and
    not whitespace.
    """
    # comment has 'None' is_whitespace() result.  intentional?
    return (not isinstance(token, Comment) and not token.is_whitespace())



def iterSignificant(tokenList):
    """
    Iterate tokens that pass the test given by L{significant}, from a given
    L{TokenList}.
    """
    for token in tokenList.tokens:
        if significant(token):
            yield token



def _destringify(strval):
    """
    Convert a single-quoted SQL string into its actual repsresented value.
    (Assumes standards compliance, since we should be controlling all the input
    here.  The only quoting syntax respected is "''".)
    """
    return strval[1:-1].replace("''", "'")



