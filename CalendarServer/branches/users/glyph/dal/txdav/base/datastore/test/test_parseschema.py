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
from twisted.trial.unittest import TestCase


class ParsingExampleTests(TestCase):
    """
    Tests for parsing some sample schemas.
    """

    def test_simplest(self):
        """
        Parse an extremely simple schema with one table in it.
        """
        s = Schema()
        addSQLToSchema(s, "create table foo (bar integer);")
        self.assertEquals(len(s.tables), 1)
        foo = s.tableNamed('foo')
        self.assertEquals(len(foo.columns), 1)
        bar = foo.columns[0]
        self.assertEquals(bar.name, "bar")
        self.assertEquals(bar.type.name, "integer")


    def test_sequence(self):
        """
        Parsing a 'create sequence' statement adds a L{Sequence} to the
        L{Schema}.
        """
        s = Schema()
        addSQLToSchema(s, "create sequence myseq;")
        self.assertEquals(len(s.sequences), 1)
        self.assertEquals(s.sequences[0].name, "myseq")


