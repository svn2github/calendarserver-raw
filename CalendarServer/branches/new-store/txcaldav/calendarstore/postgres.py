# -*- test-case-name: txcaldav.calendarstore.test.test_postgres -*-
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
PostgreSQL data store.
"""

__all__ = [
]

v1_schema = """

-----------------
-- Resource ID --
-----------------

create sequence RESOURCE_ID_SEQ;


-----------------
- Calendar Home -
-----------------

create table CALENDAR_HOME (
  RESOURCE_ID varchar(255)
    not null
    primary key
    default nextval(ÕRESOURCE_ID_SEQÕ),
  OWNER_UID varchar(255) not null,
);


-----------------
- Calendar Bind -
-----------------

-- Joins CALENDAR_HOME and CALENDAR

create table CALENDAR_BIND (
  CALENDAR_HOME_RESOURCE_ID varchar(255) not null, -- foreign key: CALENDAR_HOME.RESOURCE_ID
  CALENDAR_RESOURCE_ID      varchar(255) not null, -- foreign key: CALENDAR.RESOURCE_ID
  CALENDAR_NAME             varchar(255) not null,
  CALENDAR_MODE             int          not null,
  SEEN_BY_OWNER             bool         not null,
  SEEN_BY_SHAREE            bool         not null,
  STATUS                    integer      not null,
  MESSAGE                   text,                  -- FIXME: xml?
);

-- Enumeration of calendar bind modes

create table CALENDAR_BIND_MODE (
  ID          int         not null primary key,
  DESCRIPTION varchar(16) not null,
);

insert into CALENDAR_MODE values (0, "own"  );
insert into CALENDAR_MODE values (1, "read" );
insert into CALENDAR_MODE values (2, "write");

-- Enumeration of statuses

create table CALENDAR_BIND_STATUS (
  ID          int         not null primary key,
  DESCRIPTION varchar(16) not null,
);

insert into CALENDAR_BIND_STATUS values (0, "invited" );
insert into CALENDAR_BIND_STATUS values (1, "accepted");
insert into CALENDAR_BIND_STATUS values (2, "declined");


------------
- Calendar -
------------

create table CALENDAR (
  RESOURCE_ID varchar(255)
    not null
    primary key
    default nextval(ÕRESOURCE_ID_SEQÕ),
  SYNC_TOKEN varchar(255),
);


-------------------
- Calendar Object -
-------------------

create table CALENDAR_OBJECT (
  RESOURCE_ID          varchar(255)
    not null
    primary key
    default nextval(ÕRESOURCE_ID_SEQÕ),
  CALENDAR_RESOURCE_ID varchar(255) not null, -- foreign key: CALENDAR.RESOURCE_ID
  RESOURCE_NAME        varchar(255) not null,
  ICALENDAR_TEXT       text         not null,
  ICALENDAR_UID        varchar(255) not null,
  ICALENDAR_TYPE       varchar(255) not null,
  ATTACHMENTS_MODE     int          not null,
  ORGANIZER            varchar(255),
  ORGANIZER_OBJECT     varchar(255), -- foreign key: CALENDAR_OBJECT.RESOURCE_ID
);

-- Enumeration of attachment modes

create table CALENDAR_OBJECT_ATTACHMENTS_MODE (
  ID          int         not null primary key,
  DESCRIPTION varchar(16) not null,
);

insert into CALENDAR_MODE values (0, "read" );
insert into CALENDAR_MODE values (1, "write");


--------------
- Attachment -
--------------

create table ATTACHMENT (
  CALENDAR_OBJECT varchar(255) not null, -- foreign key: CALENDAR_OBJECT.RESOURCE_ID
  CONTENT_TYPE    varchar(255) not null,
  SIZE            int          not null,
  MD5             char(32)     not null,
  PATH            varchar(255) not null,
);

----------------
- iTIP Message -
----------------

create table ITIP_MESSAGE (
  CALENDAR_RESOURCE_ID varchar(255) not null, -- foreign key: CALENDAR.RESOURCE_ID
  UID                  varchar(255) not null,
  ICALENDAR_TEXT       text         not null,
  MD5                  char(32)     not null,
  CHANGES              text         not null,
);


---------------------
- Resource Property -
---------------------

create table RESOURCE_PROPERTY (
  RESOURCE_ID varchar(255) not null, -- foreign key: *.RESOURCE_ID
  NAME        varchar(255) not null,
  VALUE       text         not null, -- FIXME: xml?
  VIEWER_UID  varchar(255),
);


"""
