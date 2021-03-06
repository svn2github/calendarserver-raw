----
-- Copyright (c) 2012 Apple Inc. All rights reserved.
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
-- Upgrade database schema from VERSION 10 to 11 --
---------------------------------------------------

alter table APN_SUBSCRIPTIONS
 add column USER_AGENT varchar(255) default null,
 add column IP_ADDR varchar(255) default null;

-- Now update the version
-- No data upgrades
update CALENDARSERVER set VALUE = '11' where NAME = 'VERSION';
