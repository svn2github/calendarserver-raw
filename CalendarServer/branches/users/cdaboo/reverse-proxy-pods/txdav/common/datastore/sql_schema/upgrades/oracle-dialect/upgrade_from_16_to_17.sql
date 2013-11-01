----
-- Copyright (c) 2011-2013 Apple Inc. All rights reserved.
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
-- Upgrade database schema from VERSION 16 to 17 --
---------------------------------------------------


------------------------------
-- CALENDAR_OBJECT clean-up --
------------------------------

begin
for i in (select constraint_name from user_cons_columns where column_name = 'ORGANIZER_OBJECT')
loop
execute immediate 'alter table calendar_object drop constraint ' || i.constraint_name;
end loop;
end;

alter table CALENDAR_OBJECT
 drop (ORGANIZER_OBJECT);

create index CALENDAR_OBJECT_ICALE_82e731d5 on CALENDAR_OBJECT (
    ICALENDAR_UID
);


-- Now update the version
update CALENDARSERVER set VALUE = '17' where NAME = 'VERSION';
