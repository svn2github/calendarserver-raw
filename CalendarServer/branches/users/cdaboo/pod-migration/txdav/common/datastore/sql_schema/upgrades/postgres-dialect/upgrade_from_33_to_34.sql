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

-- Add migration column to tables --

alter table CALENDAR_HOME
  drop constraint CALENDAR_HOME_OWNER_UID_KEY,
  add column MIGRATION        integer default 0 not null,
  add unique(OWNER_UID, MIGRATION);

alter table NOTIFICATION_HOME
  drop constraint NOTIFICATION_HOME_OWNER_UID_KEY,
  add column MIGRATION        integer default 0 not null,
  add unique(OWNER_UID, MIGRATION);

alter table ADDRESSBOOK_HOME
  drop constraint ADDRESSBOOK_HOME_OWNER_UID_KEY,
  add column MIGRATION        integer default 0 not null,
  add unique(OWNER_UID, MIGRATION);


create table MIGRATION_STATUS (
  ID          integer     primary key,
  DESCRIPTION varchar(16) not null unique
);

insert into MIGRATION_STATUS values (0, 'none' );
insert into MIGRATION_STATUS values (1, 'migrating');
insert into MIGRATION_STATUS values (2, 'migrated');

-- Update version --

update CALENDARSERVER set VALUE = '34' where NAME = 'VERSION';
