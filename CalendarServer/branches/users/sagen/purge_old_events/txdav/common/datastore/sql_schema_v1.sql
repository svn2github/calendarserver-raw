-- -*- test-case-name: txdav.caldav.datastore.test.test_sql,txdav.carddav.datastore.test.test_sql -*-

----
-- Copyright (c) 2010 Apple Inc. All rights reserved.
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

-----------------
-- Resource ID --
-----------------

create sequence RESOURCE_ID_SEQ;


-------------------
-- Calendar Home --
-------------------

create table CALENDAR_HOME (
  RESOURCE_ID      integer      primary key default nextval('RESOURCE_ID_SEQ'),
  OWNER_UID        varchar(255) not null unique
);

create index CALENDAR_HOME_OWNER_UID on CALENDAR_HOME(OWNER_UID);

----------------------------
-- Calendar Home Metadata --
----------------------------

create table CALENDAR_HOME_METADATA (
  RESOURCE_ID      integer      not null references CALENDAR_HOME on delete cascade,
  QUOTA_USED_BYTES integer      default 0 not null
);

create index CALENDAR_HOME_METADATA_RESOURCE_ID
    on CALENDAR_HOME_METADATA(RESOURCE_ID);

--------------
-- Calendar --
--------------

create table CALENDAR (
  RESOURCE_ID integer   primary key default nextval('RESOURCE_ID_SEQ'),
  CREATED     timestamp default timezone('UTC', CURRENT_TIMESTAMP),
  MODIFIED    timestamp default timezone('UTC', CURRENT_TIMESTAMP)
);


------------------------
-- Sharing Invitation --
------------------------

create table INVITE (
    INVITE_UID         varchar(255) not null,
    NAME               varchar(255) not null,
    RECIPIENT_ADDRESS  varchar(255) not null,
    HOME_RESOURCE_ID   integer      not null,
    RESOURCE_ID        integer      not null
);

create index INVITE_INVITE_UID on INVITE(INVITE_UID);
create index INVITE_RESOURCE_ID on INVITE(INVITE_UID);
create index INVITE_HOME_RESOURCE_ID on INVITE(INVITE_UID);

---------------------------
-- Sharing Notifications --
---------------------------

create table NOTIFICATION_HOME (
  RESOURCE_ID integer      primary key default nextval('RESOURCE_ID_SEQ'),
  OWNER_UID   varchar(255) not null unique
);

create index NOTIFICATION_HOME_OWNER_UID on NOTIFICATION_HOME(OWNER_UID);


create table NOTIFICATION (
  RESOURCE_ID                   integer      primary key default nextval('RESOURCE_ID_SEQ'),
  NOTIFICATION_HOME_RESOURCE_ID integer      not null references NOTIFICATION_HOME,
  NOTIFICATION_UID              varchar(255) not null,
  XML_TYPE                      varchar      not null,
  XML_DATA                      varchar      not null,
  MD5                           char(32)     not null,
  CREATED                       timestamp default timezone('UTC', CURRENT_TIMESTAMP),
  MODIFIED                      timestamp default timezone('UTC', CURRENT_TIMESTAMP),

  unique(NOTIFICATION_UID, NOTIFICATION_HOME_RESOURCE_ID)
);

create index NOTIFICATION_NOTIFICATION_HOME_RESOURCE_ID on
  NOTIFICATION(NOTIFICATION_HOME_RESOURCE_ID);

create index NOTIFICATION_NOTIFICATION_UID on NOTIFICATION(NOTIFICATION_UID);

-------------------
-- Calendar Bind --
-------------------

-- Joins CALENDAR_HOME and CALENDAR

create table CALENDAR_BIND (
  CALENDAR_HOME_RESOURCE_ID integer      not null references CALENDAR_HOME,
  CALENDAR_RESOURCE_ID      integer      not null references CALENDAR on delete cascade,
  
  -- An invitation which hasn't been accepted yet will not yet have a resource
  -- name, so this field may be null.
  
  CALENDAR_RESOURCE_NAME    varchar(255),
  BIND_MODE                 integer      not null, -- enum CALENDAR_BIND_MODE
  BIND_STATUS               integer      not null, -- enum CALENDAR_BIND_STATUS
  SEEN_BY_OWNER             boolean      not null,
  SEEN_BY_SHAREE            boolean      not null,
  MESSAGE                   text,

  primary key(CALENDAR_HOME_RESOURCE_ID, CALENDAR_RESOURCE_ID),
  unique(CALENDAR_HOME_RESOURCE_ID, CALENDAR_RESOURCE_NAME)
);

create index CALENDAR_BIND_HOME_RESOURCE_ID on
  CALENDAR_BIND(CALENDAR_HOME_RESOURCE_ID);
create index CALENDAR_BIND_RESOURCE_ID on
  CALENDAR_BIND(CALENDAR_RESOURCE_ID);

-- Enumeration of calendar bind modes

create table CALENDAR_BIND_MODE (
  ID          integer     primary key,
  DESCRIPTION varchar(16) not null unique
);

insert into CALENDAR_BIND_MODE values (0, 'own'  );
insert into CALENDAR_BIND_MODE values (1, 'read' );
insert into CALENDAR_BIND_MODE values (2, 'write');
insert into CALENDAR_BIND_MODE values (3, 'direct');

-- Enumeration of statuses

create table CALENDAR_BIND_STATUS (
  ID          integer     primary key,
  DESCRIPTION varchar(16) not null unique
);

insert into CALENDAR_BIND_STATUS values (0, 'invited' );
insert into CALENDAR_BIND_STATUS values (1, 'accepted');
insert into CALENDAR_BIND_STATUS values (2, 'declined');
insert into CALENDAR_BIND_STATUS values (3, 'invalid');


---------------------
-- Calendar Object --
---------------------

create table CALENDAR_OBJECT (
  RESOURCE_ID          integer      primary key default nextval('RESOURCE_ID_SEQ'),
  CALENDAR_RESOURCE_ID integer      not null references CALENDAR on delete cascade,
  RESOURCE_NAME        varchar(255) not null,
  ICALENDAR_TEXT       text         not null,
  ICALENDAR_UID        varchar(255) not null,
  ICALENDAR_TYPE       varchar(255) not null,
  ATTACHMENTS_MODE     integer      not null, -- enum CALENDAR_OBJECT_ATTACHMENTS_MODE
  ORGANIZER            varchar(255),
  ORGANIZER_OBJECT     integer      references CALENDAR_OBJECT,
  RECURRANCE_MAX       date,        -- maximum date that recurrences have been expanded to.
  ACCESS               integer      default 0 not null,
  SCHEDULE_OBJECT      boolean      default false not null,
  SCHEDULE_TAG         varchar(36)  default null,
  SCHEDULE_ETAGS       text         default null,
  PRIVATE_COMMENTS     boolean      default false not null,
  MD5                  char(32)     not null,
  CREATED              timestamp    default timezone('UTC', CURRENT_TIMESTAMP),
  MODIFIED             timestamp    default timezone('UTC', CURRENT_TIMESTAMP),

  unique(CALENDAR_RESOURCE_ID, RESOURCE_NAME)

  -- since the 'inbox' is a 'calendar resource' for the purpose of storing
  -- calendar objects, this constraint has to be selectively enforced by the
  -- application layer.

  -- unique(CALENDAR_RESOURCE_ID, ICALENDAR_UID)
);

create index CALENDAR_OBJECT_CALENDAR_RESOURCE_ID on
  CALENDAR_OBJECT(CALENDAR_RESOURCE_ID);

create index CALENDAR_OBJECT_ORGANIZER_OBJECT on
  CALENDAR_OBJECT(ORGANIZER_OBJECT);

-- Enumeration of attachment modes

create table CALENDAR_OBJECT_ATTACHMENTS_MODE (
  ID          integer     primary key,
  DESCRIPTION varchar(16) not null unique
);

insert into CALENDAR_OBJECT_ATTACHMENTS_MODE values (0, 'read' );
insert into CALENDAR_OBJECT_ATTACHMENTS_MODE values (1, 'write');


-- Enumeration of calendar access types

create table CALENDAR_ACCESS_TYPE (
  ID          integer     primary key,
  DESCRIPTION varchar(32) not null unique
);

insert into CALENDAR_ACCESS_TYPE values (0, ''             );
insert into CALENDAR_ACCESS_TYPE values (1, 'public'       );
insert into CALENDAR_ACCESS_TYPE values (2, 'private'      );
insert into CALENDAR_ACCESS_TYPE values (3, 'confidential' );
insert into CALENDAR_ACCESS_TYPE values (4, 'restricted'   );

-----------------
-- Instance ID --
-----------------

create sequence INSTANCE_ID_SEQ;


----------------
-- Time Range --
----------------

create table TIME_RANGE (
  INSTANCE_ID                 integer        primary key default nextval('INSTANCE_ID_SEQ'),
  CALENDAR_RESOURCE_ID        integer        not null references CALENDAR on delete cascade,
  CALENDAR_OBJECT_RESOURCE_ID integer        not null references CALENDAR_OBJECT on delete cascade,
  FLOATING                    boolean        not null,
  START_DATE                  timestamp      not null,
  END_DATE                    timestamp      not null,
  FBTYPE                      integer        not null,
  TRANSPARENT                 boolean        not null
);

create index TIME_RANGE_CALENDAR_RESOURCE_ID on
  TIME_RANGE(CALENDAR_RESOURCE_ID);
create index TIME_RANGE_CALENDAR_OBJECT_RESOURCE_ID on
  TIME_RANGE(CALENDAR_OBJECT_RESOURCE_ID);


-- Enumeration of free/busy types

create table FREE_BUSY_TYPE (
  ID          integer     primary key,
  DESCRIPTION varchar(16) not null unique
);

insert into FREE_BUSY_TYPE values (0, 'unknown'         );
insert into FREE_BUSY_TYPE values (1, 'free'            );
insert into FREE_BUSY_TYPE values (2, 'busy'            );
insert into FREE_BUSY_TYPE values (3, 'busy-unavailable');
insert into FREE_BUSY_TYPE values (4, 'busy-tentative'  );


------------------
-- Transparency --
------------------

create table TRANSPARENCY (
  TIME_RANGE_INSTANCE_ID      integer      not null references TIME_RANGE on delete cascade,
  USER_ID                     varchar(255) not null,
  TRANSPARENT                 boolean      not null
);

create index TRANSPARENCY_TIME_RANGE_INSTANCE_ID on
  TRANSPARENCY(TIME_RANGE_INSTANCE_ID);

----------------
-- Attachment --
----------------

create table ATTACHMENT (
  CALENDAR_OBJECT_RESOURCE_ID integer       not null references CALENDAR_OBJECT on delete cascade,
  CONTENT_TYPE                varchar(255)  not null,
  SIZE                        integer       not null,
  MD5                         char(32)      not null,
  CREATED                     timestamp default timezone('UTC', CURRENT_TIMESTAMP),
  MODIFIED                    timestamp default timezone('UTC', CURRENT_TIMESTAMP),
  PATH                        varchar(1024) not null,

  unique(CALENDAR_OBJECT_RESOURCE_ID, PATH)
);

create index ATTACHMENT_CALENDAR_OBJECT_RESOURCE_ID on
  ATTACHMENT(CALENDAR_OBJECT_RESOURCE_ID);


------------------
-- iTIP Message --
------------------

create table ITIP_MESSAGE (
  CALENDAR_RESOURCE_ID integer      not null references CALENDAR,
  ICALENDAR_TEXT       text         not null,
  ICALENDAR_UID        varchar(255) not null,
  MD5                  char(32)     not null,
  CHANGES              text         not null
);


create index ITIP_MESSAGE_CALENDAR_RESOURCE_ID on
  ITIP_MESSAGE(CALENDAR_RESOURCE_ID);


-----------------------
-- Resource Property --
-----------------------

create table RESOURCE_PROPERTY (
  RESOURCE_ID integer      not null, -- foreign key: *.RESOURCE_ID
  NAME        varchar(255) not null,
  VALUE       text         not null, -- FIXME: xml?
  VIEWER_UID  varchar(255),

  primary key(RESOURCE_ID, NAME, VIEWER_UID)
);


----------------------
-- AddressBook Home --
----------------------

create table ADDRESSBOOK_HOME (
  RESOURCE_ID      integer      primary key default nextval('RESOURCE_ID_SEQ'),
  OWNER_UID        varchar(255) not null unique
);

create index ADDRESSBOOK_HOME_OWNER_UID on ADDRESSBOOK_HOME(OWNER_UID);

--------------------------------
-- AddressBook Home Meta-data --
--------------------------------

create table ADDRESSBOOK_HOME_METADATA (
  RESOURCE_ID      integer      not null references ADDRESSBOOK_HOME on delete cascade,
  QUOTA_USED_BYTES integer      default 0 not null
);

create index ADDRESSBOOK_HOME_METADATA_RESOURCE_ID
    on ADDRESSBOOK_HOME_METADATA(RESOURCE_ID);

-----------------
-- AddressBook --
-----------------

create table ADDRESSBOOK (
  RESOURCE_ID integer   primary key default nextval('RESOURCE_ID_SEQ'),
  CREATED     timestamp default timezone('UTC', CURRENT_TIMESTAMP),
  MODIFIED    timestamp default timezone('UTC', CURRENT_TIMESTAMP)
);


----------------------
-- AddressBook Bind --
----------------------

-- Joins ADDRESSBOOK_HOME and ADDRESSBOOK

create table ADDRESSBOOK_BIND (
  ADDRESSBOOK_HOME_RESOURCE_ID integer      not null references ADDRESSBOOK_HOME,
  ADDRESSBOOK_RESOURCE_ID      integer      not null references ADDRESSBOOK on delete cascade,

  -- An invitation which hasn't been accepted yet will not yet have a resource
  -- name, so this field may be null.

  ADDRESSBOOK_RESOURCE_NAME    varchar(255),
  BIND_MODE                    integer      not null, -- enum CALENDAR_BIND_MODE
  BIND_STATUS                  integer      not null, -- enum CALENDAR_BIND_STATUS
  SEEN_BY_OWNER                boolean      not null,
  SEEN_BY_SHAREE               boolean      not null,
  MESSAGE                      text,                  -- FIXME: xml?

  primary key(ADDRESSBOOK_HOME_RESOURCE_ID, ADDRESSBOOK_RESOURCE_ID),
  unique(ADDRESSBOOK_HOME_RESOURCE_ID, ADDRESSBOOK_RESOURCE_NAME)
);

create index ADDRESSBOOK_BIND_HOME_RESOURCE_ID on
  ADDRESSBOOK_BIND(ADDRESSBOOK_HOME_RESOURCE_ID);
create index ADDRESSBOOK_BIND_RESOURCE_ID on
  ADDRESSBOOK_BIND(ADDRESSBOOK_RESOURCE_ID);

create table ADDRESSBOOK_OBJECT (
  RESOURCE_ID             integer      primary key default nextval('RESOURCE_ID_SEQ'),
  ADDRESSBOOK_RESOURCE_ID integer      not null references ADDRESSBOOK on delete cascade,
  RESOURCE_NAME           varchar(255) not null,
  VCARD_TEXT              text         not null,
  VCARD_UID               varchar(255) not null,
  MD5                     char(32)     not null,
  CREATED                 timestamp    default timezone('UTC', CURRENT_TIMESTAMP),
  MODIFIED                timestamp    default timezone('UTC', CURRENT_TIMESTAMP),

  unique(ADDRESSBOOK_RESOURCE_ID, RESOURCE_NAME),
  unique(ADDRESSBOOK_RESOURCE_ID, VCARD_UID)
);

create index ADDRESSBOOK_OBJECT_ADDRESSBOOK_RESOURCE_ID on
  ADDRESSBOOK_OBJECT(ADDRESSBOOK_RESOURCE_ID);

---------------
-- Revisions --
---------------

create sequence REVISION_SEQ;


---------------
-- Revisions --
---------------

create table CALENDAR_OBJECT_REVISIONS (
  CALENDAR_HOME_RESOURCE_ID integer      not null references CALENDAR_HOME,
  CALENDAR_RESOURCE_ID      integer      references CALENDAR,
  CALENDAR_NAME             varchar(255) default null,
  RESOURCE_NAME             varchar(255),
  REVISION                  integer      not null,
  DELETED                   boolean      not null,

  unique(CALENDAR_RESOURCE_ID, RESOURCE_NAME)
);


create index CALENDAR_OBJECT_REVISIONS_HOME_RESOURCE_ID
  on CALENDAR_OBJECT_REVISIONS(CALENDAR_HOME_RESOURCE_ID);

create index CALENDAR_OBJECT_REVISIONS_RESOURCE_ID
  on CALENDAR_OBJECT_REVISIONS(CALENDAR_RESOURCE_ID);


-------------------------------
-- AddressBook Object Revisions --
-------------------------------

create table ADDRESSBOOK_OBJECT_REVISIONS (
  ADDRESSBOOK_HOME_RESOURCE_ID integer      not null references ADDRESSBOOK_HOME,
  ADDRESSBOOK_RESOURCE_ID      integer      references ADDRESSBOOK,
  ADDRESSBOOK_NAME             varchar(255) default null,
  RESOURCE_NAME                varchar(255),
  REVISION                     integer      not null,
  DELETED                      boolean      not null,

  unique(ADDRESSBOOK_RESOURCE_ID, RESOURCE_NAME)
);

create index ADDRESSBOOK_OBJECT_REVISIONS_HOME_RESOURCE_ID
  on ADDRESSBOOK_OBJECT_REVISIONS(ADDRESSBOOK_HOME_RESOURCE_ID);

create index ADDRESSBOOK_OBJECT_REVISIONS_RESOURCE_ID
  on ADDRESSBOOK_OBJECT_REVISIONS(ADDRESSBOOK_RESOURCE_ID);

-----------------------------------
-- Notification Object Revisions --
-----------------------------------

create table NOTIFICATION_OBJECT_REVISIONS (
  NOTIFICATION_HOME_RESOURCE_ID integer      not null references NOTIFICATION_HOME on delete cascade,
  RESOURCE_NAME                 varchar(255),
  REVISION                      integer      not null,
  DELETED                       boolean      not null,

  unique(NOTIFICATION_HOME_RESOURCE_ID, RESOURCE_NAME)
);


create index NOTIFICATION_OBJECT_REVISIONS_HOME_RESOURCE_ID
  on NOTIFICATION_OBJECT_REVISIONS(NOTIFICATION_HOME_RESOURCE_ID);

