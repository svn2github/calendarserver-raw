# -*- test-case-name: txdav.base.datastore.test.test_parseschema -*-
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
Parser for SQL schema.
"""

from itertools import chain

from sqlparse import parse, keywords
from sqlparse.tokens import Keyword, Punctuation, Number, String, Name
from sqlparse.sql import (Comment, Identifier, Parenthesis, IdentifierList,
                          Function)

from txdav.base.datastore.sqlmodel import Schema, Table, SQLType, ProcedureCall
from txdav.base.datastore.sqlmodel import Constraint
from txdav.base.datastore.sqlmodel import Sequence

def _fixKeywords():
    # In Postgres, 'SEQUENCE' is a keyword, and it behaves like one.
    keywords.KEYWORDS['SEQUENCE'] = Keyword
    # We use these as column names, and we probably shouldn't, since they _are_
    # SQL keywords.  But let's fool the parser for a moment...
    for columnNameKeyword in ['ACCESS', 'SIZE']:
        del keywords.KEYWORDS[columnNameKeyword]

_fixKeywords()



def tableFromCreateStatement(schema, stmt):
    i = iterSignificant(stmt)
    expect(i, ttype=Keyword.DDL, value='CREATE')
    expect(i, ttype=Keyword, value='TABLE')
    function = expect(i, cls=Function)
    i = iterSignificant(function)
    name = expect(i, cls=Identifier).get_name()
    self = Table(schema, name)
    parens = expect(i, cls=Parenthesis)
    cp = _ColumnParser(self, iterSignificant(parens), parens)
    cp.parse()
    return self



def schemaFromPath(path):
    schema = Schema(path.basename())
    schemaData = path.getContent()
    addSQLToSchema(schema, schemaData)
    return schema


def addSQLToSchema(schema, schemaData):
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
                schema.sequences.append(Sequence(stmt.token_next(2, True).get_name()))
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
        self.parens = parens
        self.iter = parenIter
        self.table = table


    def __iter__(self):
        return self


    def next(self):
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
        self.iter = chain(iter((value,)), self.iter)


    def parse(self):
        expect(self.iter, ttype=Punctuation, value=u"(")
        while self.nextColumn():
            pass


    def nextColumn(self):
        maybeIdent = self.next()
        if maybeIdent.ttype == Name:
            return self.parseColumn(maybeIdent.value)
        elif isinstance(maybeIdent, Identifier):
            return self.parseColumn(maybeIdent.get_name())
        else:
            return self.parseConstraint(maybeIdent)


    def parseConstraint(self, constraintType):
        """
        Parse a 'free' constraint, described explicitly in the table as opposed
        to being implicitly associated with a column by being placed after it.
        """
        # only know about PRIMARY KEY and UNIQUE for now
        if constraintType.match(Keyword, 'PRIMARY'):
            expect(self, ttype=Keyword, value='KEY')
            expect(self, cls=Parenthesis)
            self.primaryKey = 'MULTI-VALUE-KEY'
        elif constraintType.match(Keyword, 'UNIQUE'):
            parens = iterSignificant(expect(self, cls=Parenthesis))
            expect(parens, ttype=Punctuation, value="(")
            idorids = parens.next()
            if isinstance(idorids, Identifier):
                idnames = [idorids.get_name()]
            elif isinstance(idorids, IdentifierList):
                idnames = [x.get_name() for x in idorids.get_identifiers()]
            else:
                raise ViolatedExpectation("identifier or list", repr(idorids))
            expect(parens, ttype=Punctuation, value=")")
            self.table.tableConstraint(Constraint.UNIQUE, idnames)
        else:
            raise ViolatedExpectation('PRIMARY or UNIQUE', constraintType)
        return self.checkEnd(self.next())


    def checkEnd(self, val):
        if val.value == u",":
            return True
        elif val.value == u")":
            return False
        else:
            raise ViolatedExpectation(", or )", val)


    def parseColumn(self, name):
        typeName = self.next()
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
        theColumn = self.table.addColumn(
            name=name, type=SQLType(typeName.value, typeLength)
        )
        for val in self:
            if val.ttype == Punctuation:
                return self.checkEnd(val)
            else:
                expected = True
                def oneConstraint(t):
                    self.table.tableConstraint(t,
                                               [theColumn.name])

                if val.match(Keyword, 'PRIMARY'):
                    expect(self, ttype=Keyword, value='KEY')
                    # XXX check to make sure there's no other primary key yet
                    self.table.primaryKey = theColumn
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
                elif val.match(Keyword, 'DEFAULT'):
                    theDefault = self.next()
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
                            defaultValue = ProcedureCall(thingo, parens)
                    else:
                        raise RuntimeError("not sure what to do")
                    theColumn.setDefaultValue(defaultValue)
                elif val.match(Keyword, 'REFERENCES'):
                    target = nameOrIdentifier(self.next())
                    theColumn.doesReferenceName(target)
                elif val.match(Keyword, 'ON'):
                    expect(self, ttype=Keyword.DML, value='DELETE')
                    expect(self, ttype=Keyword, value='CASCADE')
                else:
                    expected = False
                if not expected:
                    print 'UNEXPECTED TOKEN:', repr(val), theColumn
                    print self.parens
                    import pprint
                    pprint.pprint(self.parens.tokens)
                    return 0




class ViolatedExpectation(Exception):

    def __init__(self, expected, got):
        self.expected = expected
        self.got = got
        super(ViolatedExpectation, self).__init__(
            "Expected %r got %s" % (expected, got)
        )

def nameOrIdentifier(token):
    if isinstance(token, Identifier):
        return token.get_name()
    elif token.ttype == Name:
        return token.value
    else:
        raise ViolatedExpectation("identifier or name", repr(token))


def expectSingle(nextval, ttype=None, value=None, cls=None):
    if ttype is not None:
        if nextval.ttype != ttype:
            raise ViolatedExpectation(ttype, '%s:%s' % (nextval.ttype, nextval))
    if value is not None:
        if nextval.value.upper() != value.upper():
            raise ViolatedExpectation(value, nextval.value)
    if cls is not None:
        if nextval.__class__ != cls:
            raise ViolatedExpectation(cls, repr(nextval))
    return nextval



def expect(iterator, **kw):
    nextval = iterator.next()
    return expectSingle(nextval, **kw)



def significant(token):
    # comment has 'None' is_whitespace() result.  intentional?
    return (not isinstance(token, Comment) and not token.is_whitespace())



def iterSignificant(tokenList):
    for token in tokenList.tokens:
        if significant(token):
            yield token



def _destringify(strval):
    """
    Convert a single-quoted SQL string into its actual represented value.
    (Assumes standards compliance, since we should be controlling all the input
    here.  The only quoting syntax respected is "''".)
    """
    return strval[1:-1].replace("''", "'")





