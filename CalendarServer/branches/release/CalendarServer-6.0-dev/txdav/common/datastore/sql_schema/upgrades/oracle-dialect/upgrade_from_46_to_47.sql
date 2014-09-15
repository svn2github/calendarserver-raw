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
-- Upgrade database schema from VERSION 46 to 47 --
---------------------------------------------------


create table GROUP_DELEGATE_CHANGES_WORK (
    "WORK_ID" integer primary key not null,
    "JOB_ID" integer not null references JOB,
    "DELEGATOR_UID" nvarchar2(255),
    "READ_DELEGATE_UID" nvarchar2(255),
    "WRITE_DELEGATE_UID" nvarchar2(255)
);

create index GROUP_DELEGATE_CHANGE_8bf9e6d8 on GROUP_DELEGATE_CHANGES_WORK (
    JOB_ID
);


-- Add "unique" to GROUPS.GROUP_UID
alter table GROUPS add unique (GROUP_UID);


-- update the version
update CALENDARSERVER set VALUE = '47' where NAME = 'VERSION';
