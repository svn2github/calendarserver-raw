----
-- Copyright (c) 2012-2013 Apple Inc. All rights reserved.
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
-- Upgrade database schema from VERSION 33 to 34 --
---------------------------------------------------

-- Add timestamp to revision tables --

-- Add migration column to tables --

alter table CALENDAR_HOME
  drop unique ("OWNER_UID");
alter table CALENDAR_HOME
  add ("MIGRATION" integer default 0 not null);
alter table CALENDAR_HOME
  add unique(OWNER_UID, MIGRATION);

alter table NOTIFICATION_HOME
  drop unique ("OWNER_UID");
alter table NOTIFICATION_HOME
  add ("MIGRATION" integer default 0 not null);
alter table NOTIFICATION_HOME
  add unique(OWNER_UID, MIGRATION);

alter table ADDRESSBOOK_HOME
  drop unique ("OWNER_UID");
alter table ADDRESSBOOK_HOME
  add ("MIGRATION" integer default 0 not null);
alter table ADDRESSBOOK_HOME
  add unique(OWNER_UID, MIGRATION);


create table MIGRATION_STATUS (
    "ID" integer primary key,
    "DESCRIPTION" nvarchar2(16) unique
);

insert into MIGRATION_STATUS (DESCRIPTION, ID) values ('none', 0);
insert into MIGRATION_STATUS (DESCRIPTION, ID) values ('migrating', 1);
insert into MIGRATION_STATUS (DESCRIPTION, ID) values ('migrated', 2);
 
-- Update version --

update CALENDARSERVER set VALUE = '34' where NAME = 'VERSION';
