----
-- Copyright (c) 2012-2014 Apple Inc. All rights reserved.
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
-- http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.
----

---------------------------------------------------
-- Upgrade database schema from VERSION XX to YY --
---------------------------------------------------


-- Downgrade from hash partitioned indexes

DECLARE  
  PROCEDURE hash_undo_partition(PKEY_TABLE_NAME in VARCHAR2) IS
    PKEY_NAME VARCHAR(255);
  BEGIN
    -- Find pkey constraint names
    select CONSTRAINT_NAME into PKEY_NAME from USER_CONSTRAINTS where table_name = PKEY_TABLE_NAME and CONSTRAINT_TYPE = 'P';
  
    -- Disable the pkey and foreign key constraints
    execute immediate 'ALTER TABLE ' || PKEY_TABLE_NAME || ' DISABLE CONSTRAINT ' || PKEY_NAME || ' CASCADE';
  
    -- Hash partition the primary key index
    execute immediate 'ALTER TABLE ' || PKEY_TABLE_NAME || ' ENABLE CONSTRAINT ' || PKEY_NAME || ' USING INDEX';
  
    -- Enable the foreign key constraints
    FOR item in (
      SELECT TABLE_NAME, CONSTRAINT_NAME from USER_CONSTRAINTS where R_CONSTRAINT_NAME = PKEY_NAME
    )
    LOOP
      execute immediate 'ALTER TABLE ' || item.TABLE_NAME || ' ENABLE CONSTRAINT ' || item.CONSTRAINT_NAME;
    END LOOP;
  END;
  
BEGIN
  hash_undo_partition('CALENDAR_OBJECT');
  hash_undo_partition('TIME_RANGE');
  hash_undo_partition('PUSH_NOTIFICATION_WORK');
END;
