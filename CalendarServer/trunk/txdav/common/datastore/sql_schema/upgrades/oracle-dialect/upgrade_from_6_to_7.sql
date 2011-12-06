----
-- Copyright (c) 2011 Apple Inc. All rights reserved.
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

-------------------------------------------------
-- Upgrade database schema from VERSION 6 to 7 --
-------------------------------------------------

-- Just need to add one column
alter table CALENDAR_HOME
 add ("DATAVERSION" integer default 1 null);
 
-- Need to add timestamp columns
alter table CALENDAR_HOME_METADATA
 add ("CREATED" timestamp default CURRENT_TIMESTAMP at time zone 'UTC');
alter table CALENDAR_HOME_METADATA
 add ("MODIFIED" timestamp default CURRENT_TIMESTAMP at time zone 'UTC');

 -- Just need to modify one column
alter table CALENDAR_OBJECT
 add ("SUPPORTED_COMPONENTS" nvarchar2(255) default null);

-- Just need to add one column
alter table ADDRESSBOOK_HOME
 add ("DATAVERSION" integer default 1 null);
 
-- Need to add timestamp columns
alter table ADDRESSBOOK_HOME_METADATA
 add ("CREATED" timestamp default CURRENT_TIMESTAMP at time zone 'UTC');
alter table ADDRESSBOOK_HOME_METADATA
 add ("MODIFIED" timestamp default CURRENT_TIMESTAMP at time zone 'UTC');

-- Now update the version
update CALENDARSERVER set VALUE = '7' where NAME = 'VERSION';

-- Also insert the initial data version which we will use in the data upgrade
insert into CALENDARSERVER values ('CALENDAR-DATAVERSION', '1');
insert into CALENDARSERVER values ('ADDRESSBOOK-DATAVERSION', '1');
