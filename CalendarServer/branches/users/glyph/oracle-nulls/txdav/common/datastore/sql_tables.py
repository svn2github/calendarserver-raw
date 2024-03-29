# -*- test-case-name: txdav.caldav.datastore.test.test_sql -*-
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
SQL Table definitions.
"""

from twisted.python.modules import getModule
from twext.enterprise.dal.syntax import SchemaSyntax
from twext.enterprise.dal.model import NO_DEFAULT
from twext.enterprise.dal.model import Sequence, ProcedureCall
from twext.enterprise.dal.parseschema import schemaFromPath



def _populateSchema():
    """
    Generate the global L{SchemaSyntax}.
    """
    pathObj = getModule(__name__).filePath.sibling("sql_schema_v1.sql")
    return SchemaSyntax(schemaFromPath(pathObj))



schema = _populateSchema()


# Column aliases, defined so that similar tables (such as CALENDAR_OBJECT and
# ADDRESSBOOK_OBJECT) can be used according to a polymorphic interface.

schema.CALENDAR_BIND.RESOURCE_NAME = \
    schema.CALENDAR_BIND.CALENDAR_RESOURCE_NAME
schema.CALENDAR_BIND.RESOURCE_ID = \
    schema.CALENDAR_BIND.CALENDAR_RESOURCE_ID
schema.CALENDAR_BIND.HOME_RESOURCE_ID = \
    schema.CALENDAR_BIND.CALENDAR_HOME_RESOURCE_ID
schema.ADDRESSBOOK_BIND.RESOURCE_NAME = \
    schema.ADDRESSBOOK_BIND.ADDRESSBOOK_RESOURCE_NAME
schema.ADDRESSBOOK_BIND.RESOURCE_ID = \
    schema.ADDRESSBOOK_BIND.ADDRESSBOOK_RESOURCE_ID
schema.ADDRESSBOOK_BIND.HOME_RESOURCE_ID = \
    schema.ADDRESSBOOK_BIND.ADDRESSBOOK_HOME_RESOURCE_ID
schema.CALENDAR_OBJECT_REVISIONS.RESOURCE_ID = \
    schema.CALENDAR_OBJECT_REVISIONS.CALENDAR_RESOURCE_ID
schema.CALENDAR_OBJECT_REVISIONS.HOME_RESOURCE_ID = \
    schema.CALENDAR_OBJECT_REVISIONS.CALENDAR_HOME_RESOURCE_ID
schema.CALENDAR_OBJECT_REVISIONS.COLLECTION_NAME = \
    schema.CALENDAR_OBJECT_REVISIONS.CALENDAR_NAME
schema.ADDRESSBOOK_OBJECT_REVISIONS.RESOURCE_ID = \
    schema.ADDRESSBOOK_OBJECT_REVISIONS.ADDRESSBOOK_RESOURCE_ID
schema.ADDRESSBOOK_OBJECT_REVISIONS.HOME_RESOURCE_ID = \
    schema.ADDRESSBOOK_OBJECT_REVISIONS.ADDRESSBOOK_HOME_RESOURCE_ID
schema.ADDRESSBOOK_OBJECT_REVISIONS.COLLECTION_NAME = \
    schema.ADDRESSBOOK_OBJECT_REVISIONS.ADDRESSBOOK_NAME
schema.NOTIFICATION_OBJECT_REVISIONS.HOME_RESOURCE_ID = \
    schema.NOTIFICATION_OBJECT_REVISIONS.NOTIFICATION_HOME_RESOURCE_ID
schema.NOTIFICATION_OBJECT_REVISIONS.RESOURCE_ID = \
    schema.NOTIFICATION_OBJECT_REVISIONS.NOTIFICATION_HOME_RESOURCE_ID
schema.CALENDAR_OBJECT.TEXT = \
    schema.CALENDAR_OBJECT.ICALENDAR_TEXT
schema.CALENDAR_OBJECT.UID = \
    schema.CALENDAR_OBJECT.ICALENDAR_UID
schema.CALENDAR_OBJECT.PARENT_RESOURCE_ID = \
    schema.CALENDAR_OBJECT.CALENDAR_RESOURCE_ID
schema.ADDRESSBOOK_OBJECT.TEXT = \
    schema.ADDRESSBOOK_OBJECT.VCARD_TEXT
schema.ADDRESSBOOK_OBJECT.UID = \
    schema.ADDRESSBOOK_OBJECT.VCARD_UID
schema.ADDRESSBOOK_OBJECT.PARENT_RESOURCE_ID = \
    schema.ADDRESSBOOK_OBJECT.ADDRESSBOOK_RESOURCE_ID



def _combine(**kw):
    """
    Combine two table dictionaries used in a join to produce a single dictionary
    that can be used in formatting.
    """
    result = {}
    for tableRole, tableDictionary in kw.items():
        result.update([("%s:%s" % (tableRole, k), v)
                       for k,v in tableDictionary.items()])
    return result



def _S(tableSyntax):
    """
    Construct a dictionary of strings from a L{TableSyntax} for those queries
    that are still constructed via string interpolation.
    """
    result = {}
    result['name'] = tableSyntax.model.name
    #pkey = tableSyntax.model.primaryKey
    #if pkey is not None:
    #    default = pkey.default
    #    if isinstance(default, Sequence):
    #        result['sequence'] = default.name
    result['sequence'] = schema.model.sequenceNamed('REVISION_SEQ').name
    for columnSyntax in tableSyntax:
        result['column_' + columnSyntax.model.name] = columnSyntax.model.name
    for alias, realColumnSyntax in tableSyntax.aliases().items():
        result['column_' + alias] = realColumnSyntax.model.name
    return result



def _schemaConstants(nameColumn, valueColumn):
    """
    Get a constant value from the rows defined in the schema.
    """
    def get(name):
        for row in nameColumn.model.table.schemaRows:
            if row[nameColumn.model] == name:
                return row[valueColumn.model]
    return get



# Various constants

_bindStatus = _schemaConstants(
    schema.CALENDAR_BIND_STATUS.DESCRIPTION,
    schema.CALENDAR_BIND_STATUS.ID
)

_BIND_STATUS_INVITED  = _bindStatus('invited')
_BIND_STATUS_ACCEPTED = _bindStatus('accepted')
_BIND_STATUS_DECLINED = _bindStatus('declined')
_BIND_STATUS_INVALID  = _bindStatus('invalid')

_attachmentsMode = _schemaConstants(
    schema.CALENDAR_OBJECT_ATTACHMENTS_MODE.DESCRIPTION,
    schema.CALENDAR_OBJECT_ATTACHMENTS_MODE.ID
)

_ATTACHMENTS_MODE_NONE  = _attachmentsMode('none')
_ATTACHMENTS_MODE_READ  = _attachmentsMode('read')
_ATTACHMENTS_MODE_WRITE = _attachmentsMode('write')


_bindMode = _schemaConstants(
    schema.CALENDAR_BIND_MODE.DESCRIPTION,
    schema.CALENDAR_BIND_MODE.ID
)


_BIND_MODE_OWN = _bindMode('own')
_BIND_MODE_READ = _bindMode('read')
_BIND_MODE_WRITE = _bindMode('write')
_BIND_MODE_DIRECT = _bindMode('direct')


# Compatibility tables for string formatting:
CALENDAR_HOME_TABLE                 = _S(schema.CALENDAR_HOME)
CALENDAR_HOME_METADATA_TABLE        = _S(schema.CALENDAR_HOME_METADATA)
ADDRESSBOOK_HOME_TABLE              = _S(schema.ADDRESSBOOK_HOME)
ADDRESSBOOK_HOME_METADATA_TABLE     = _S(schema.ADDRESSBOOK_HOME_METADATA)
NOTIFICATION_HOME_TABLE             = _S(schema.NOTIFICATION_HOME)
CALENDAR_TABLE                      = _S(schema.CALENDAR)
ADDRESSBOOK_TABLE                   = _S(schema.ADDRESSBOOK)
CALENDAR_BIND_TABLE                 = _S(schema.CALENDAR_BIND)
ADDRESSBOOK_BIND_TABLE              = _S(schema.ADDRESSBOOK_BIND)
CALENDAR_OBJECT_REVISIONS_TABLE     = _S(schema.CALENDAR_OBJECT_REVISIONS)
ADDRESSBOOK_OBJECT_REVISIONS_TABLE  = _S(schema.ADDRESSBOOK_OBJECT_REVISIONS)
NOTIFICATION_OBJECT_REVISIONS_TABLE = _S(schema.NOTIFICATION_OBJECT_REVISIONS)
CALENDAR_OBJECT_TABLE               = _S(schema.CALENDAR_OBJECT)
ADDRESSBOOK_OBJECT_TABLE            = _S(schema.ADDRESSBOOK_OBJECT)

# Some combined tables used in join-string-formatting.
CALENDAR_AND_CALENDAR_BIND = _combine(CHILD=CALENDAR_TABLE,
                                     BIND=CALENDAR_BIND_TABLE)
CALENDAR_OBJECT_AND_BIND_TABLE = _combine(OBJECT=CALENDAR_OBJECT_TABLE,
                                         BIND=CALENDAR_BIND_TABLE)
CALENDAR_OBJECT_REVISIONS_AND_BIND_TABLE = _combine(
    REV=CALENDAR_OBJECT_REVISIONS_TABLE,
    BIND=CALENDAR_BIND_TABLE)
ADDRESSBOOK_AND_ADDRESSBOOK_BIND = _combine(CHILD=ADDRESSBOOK_TABLE,
                                           BIND=ADDRESSBOOK_BIND_TABLE)
ADDRESSBOOK_OBJECT_AND_BIND_TABLE = _combine(OBJECT=ADDRESSBOOK_OBJECT_TABLE,
                                             BIND=ADDRESSBOOK_BIND_TABLE)
ADDRESSBOOK_OBJECT_REVISIONS_AND_BIND_TABLE = _combine(
    REV=ADDRESSBOOK_OBJECT_REVISIONS_TABLE,
    BIND=ADDRESSBOOK_BIND_TABLE)



def _translateSchema(out):
    """
    When run as a script, translate the schema to another dialect.  Currently
    only postgres and oracle are supported, and native format is postgres, so
    emit in oracle format.
    """
    for sequence in schema.model.sequences:
        out.write('create sequence %s;\n' % (sequence.name,))
    for table in schema:
        # The only table name which actually exceeds the length limit right now
        # is CALENDAR_OBJECT_ATTACHMENTS_MODE, which isn't actually _used_
        # anywhere, so we can fake it for now.
        shortName = table.model.name[:30]
        out.write('create table %s (\n' % (shortName,))
        first = True
        for column in table:
            if first:
                first = False
            else:
                out.write(",\n")
            typeName = column.model.type.name
            if typeName == 'text':
                typeName = 'nclob'
            if typeName == 'boolean':
                typeName = 'integer'
            out.write('    "%s" %s' % (column.model.name, typeName))
            if column.model.type.length:
                out.write("(%s)" % (column.model.type.length,))
            if column.model is table.model.primaryKey:
                out.write(' primary key')
            default = column.model.default
            if default is not NO_DEFAULT:
                # Can't do default sequence types in Oracle, so don't bother.
                if not isinstance(default, Sequence):
                    out.write(' default')
                    if default is None:
                        out.write(' null')
                    elif isinstance(default, ProcedureCall):
                        # Cheating, because there are currently no other
                        # functions being used.
                        out.write(" CURRENT_TIMESTAMP at time zone 'UTC'")
                    else:
                        if default is True:
                            default = 1
                        elif default is False:
                            default = 0
                        out.write(" " + repr(default))
            if ( (not column.model.canBeNull())
                 # Oracle treats empty strings as NULLs, so we have to accept
                 # NULL values in columns of a string type.  Other types should
                 # be okay though.
                 and typeName not in ('text', 'varchar') ):
                out.write(' not null')
            if set([column.model]) in list(table.model.uniques()):
                out.write(' unique')
            if column.model.references is not None:
                out.write(" references %s" % (column.model.references.name,))
            if column.model.cascade:
                out.write(" on delete cascade")

        out.write('\n);\n\n')



if __name__ == '__main__':
    import sys
    _translateSchema(sys.stdout)



