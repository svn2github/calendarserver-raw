#!/usr/bin/env python

# Generates test directory records in accounts-test.xml, resources-test.xml,
# augments-test.xml and proxies-test.xml (overwriting them if they exist in
# the current directory).

prefix = """<?xml version="1.0" encoding="utf-8"?>

<!--
Copyright (c) 2006-2014 Apple Inc. All rights reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
 -->

"""

# The uids and guids for CDT test accounts are the same
# The short name is of the form userNN
USERGUIDS = "10000000-0000-0000-0000-000000000%03d"
GROUPGUIDS = "20000000-0000-0000-0000-000000000%03d"
LOCATIONGUIDS = "30000000-0000-0000-0000-000000000%03d"
RESOURCEGUIDS = "40000000-0000-0000-0000-000000000%03d"
PUBLICGUIDS = "50000000-0000-0000-0000-0000000000%02d"

# accounts-test.xml

out = file("accounts-test.xml", "w")
out.write(prefix)
out.write('<directory realm="Test Realm">\n')


for uid, fullName, guid in (
    ("admin", "Super User", "0C8BDE62-E600-4696-83D3-8B5ECABDFD2E"),
    ("apprentice", "Apprentice Super User", "29B6C503-11DF-43EC-8CCA-40C7003149CE"),
    ("i18nuser", u"\u307e\u3060".encode("utf-8"), "860B3EE9-6D7C-4296-9639-E6B998074A78"),
):
    out.write("""<record>
    <uid>{guid}</uid>
    <guid>{guid}</guid>
    <short-name>{uid}</short-name>
    <password>{uid}</password>
    <full-name>{fullName}</full-name>
    <email>{uid}@example.com</email>
</record>
""".format(uid=uid, guid=guid, fullName=fullName))

# user01-100
for i in xrange(1, 101):
    out.write("""<record type="user">
    <short-name>user%02d</short-name>
    <uid>%s</uid>
    <guid>%s</guid>
    <password>user%02d</password>
    <full-name>User %02d</full-name>
    <email>user%02d@example.com</email>
</record>
""" % (i, USERGUIDS % i, USERGUIDS % i, i, i, i))

# public01-10
for i in xrange(1, 11):
    out.write("""<record type="user">
    <short-name>public%02d</short-name>
    <uid>%s</uid>
    <guid>%s</guid>
    <password>public%02d</password>
    <full-name>Public %02d</full-name>
    <email>public%02d@example.com</email>
</record>
""" % (i, PUBLICGUIDS % i, PUBLICGUIDS % i, i, i, i))

# group01-100
members = {
    GROUPGUIDS % 1: (USERGUIDS % 1,),
    GROUPGUIDS % 2: (USERGUIDS % 6, USERGUIDS % 7),
    GROUPGUIDS % 3: (USERGUIDS % 8, USERGUIDS % 9),
    GROUPGUIDS % 4: (GROUPGUIDS % 2, GROUPGUIDS % 3, USERGUIDS % 10),
    GROUPGUIDS % 5: (GROUPGUIDS % 6, USERGUIDS % 20),
    GROUPGUIDS % 6: (USERGUIDS % 21,),
    GROUPGUIDS % 7: (USERGUIDS % 22, USERGUIDS % 23, USERGUIDS % 24),
}

for i in xrange(1, 101):

    memberElements = []
    groupUID = GROUPGUIDS % i
    if groupUID in members:
        for uid in members[groupUID]:
            memberElements.append("<member-uid>{}</member-uid>".format(uid))
        memberString = "\n    ".join(memberElements)
    else:
        memberString = ""

    out.write("""<record type="group">
    <short-name>group%02d</short-name>
    <uid>%s</uid>
    <guid>%s</guid>
    <full-name>Group %02d</full-name>
    <email>group%02d@example.com</email>
    %s
</record>
""" % (i, GROUPGUIDS % i, GROUPGUIDS % i, i, i, memberString))

out.write("</directory>\n")
out.close()


# resources-test.xml

out = file("resources-test.xml", "w")
out.write(prefix)
out.write('<directory realm="Test Realm">\n')

out.write("""
  <record type="location">
    <short-name>pretend</short-name>
    <uid>pretend</uid>
    <full-name>Pretend Conference Room</full-name>
    <associated-address>il1</associated-address>
  </record>
  <record type="address">
    <short-name>il1</short-name>
    <uid>il1</uid>
    <full-name>IL1</full-name>
    <street-address>1 Infinite Loop, Cupertino, CA 95014</street-address>
    <geographic-location>37.331741,-122.030333</geographic-location>
  </record>
  <record type="location">
    <short-name>fantastic</short-name>
    <uid>fantastic</uid>
    <full-name>Fantastic Conference Room</full-name>
    <associated-address>il2</associated-address>
  </record>
  <record type="address">
    <short-name>il2</short-name>
    <uid>il2</uid>
    <full-name>IL2</full-name>
    <street-address>2 Infinite Loop, Cupertino, CA 95014</street-address>
    <geographic-location>37.332633,-122.030502</geographic-location>
  </record>
  <record type="location">
    <short-name>delegatedroom</short-name>
    <uid>delegatedroom</uid>
    <full-name>Delegated Conference Room</full-name>
  </record>

""")

for i in xrange(1, 101):
    out.write("""<record type="location">
    <short-name>location%02d</short-name>
    <uid>%s</uid>
    <guid>%s</guid>
    <full-name>Location %02d</full-name>
</record>
""" % (i, LOCATIONGUIDS % i, LOCATIONGUIDS % i, i))


for i in xrange(1, 101):
    out.write("""<record type="resource">
    <short-name>resource%02d</short-name>
    <uid>%s</uid>
    <guid>%s</guid>
    <full-name>Resource %02d</full-name>
</record>
""" % (i, RESOURCEGUIDS % i, RESOURCEGUIDS % i, i))

out.write("</directory>\n")
out.close()


# augments-test.xml

out = file("augments-test.xml", "w")
out.write(prefix)
out.write("<augments>\n")

augments = (
    # resource05
    (RESOURCEGUIDS % 5, {
        "auto-schedule-mode": "none",
        "enable-calendar": "true",
        "enable-addressbook": "true",
    }),
    # resource06
    (RESOURCEGUIDS % 6, {
        "auto-schedule-mode": "accept-always",
        "enable-calendar": "true",
        "enable-addressbook": "true",
    }),
    # resource07
    (RESOURCEGUIDS % 7, {
        "auto-schedule-mode": "decline-always",
        "enable-calendar": "true",
        "enable-addressbook": "true",
    }),
    # resource08
    (RESOURCEGUIDS % 8, {
        "auto-schedule-mode": "accept-if-free",
        "enable-calendar": "true",
        "enable-addressbook": "true",
    }),
    # resource09
    (RESOURCEGUIDS % 9, {
        "auto-schedule-mode": "decline-if-busy",
        "enable-calendar": "true",
        "enable-addressbook": "true",
    }),
    # resource10
    (RESOURCEGUIDS % 10, {
        "auto-schedule-mode": "automatic",
        "enable-calendar": "true",
        "enable-addressbook": "true",
    }),
    # resource11
    (RESOURCEGUIDS % 11, {
        "auto-schedule-mode": "decline-always",
        "auto-accept-group": GROUPGUIDS % 1,
        "enable-calendar": "true",
        "enable-addressbook": "true",
    }),
)

out.write("""<record>
    <uid>Default</uid>
    <enable-calendar>true</enable-calendar>
    <enable-addressbook>true</enable-addressbook>
</record>
""")

out.write("""<record>
    <uid>Default-Location</uid>
    <enable-calendar>true</enable-calendar>
    <enable-addressbook>true</enable-addressbook>
    <auto-schedule-mode>automatic</auto-schedule-mode>
</record>
""")

out.write("""<record>
    <uid>Default-Resource</uid>
    <enable-calendar>true</enable-calendar>
    <enable-addressbook>true</enable-addressbook>
    <auto-schedule-mode>automatic</auto-schedule-mode>
</record>
""")

for uid, settings in augments:
    elements = []
    for key, value in settings.iteritems():
        elements.append("<{key}>{value}</{key}>".format(key=key, value=value))
    elementsString = "\n    ".join(elements)

    out.write("""<record>
    <uid>{uid}</uid>
    {elements}
</record>
""".format(uid=uid, elements=elementsString))

out.write("</augments>\n")
out.close()


# proxies-test.xml

out = file("proxies-test.xml", "w")
out.write(prefix)
out.write("<proxies>\n")

proxies = (
    (RESOURCEGUIDS % 1, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 2, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 3, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 4, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 5, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 6, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 7, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 8, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 9, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    (RESOURCEGUIDS % 10, {
        "proxies": (USERGUIDS % 1,),
        "read-only-proxies": (USERGUIDS % 3,),
    }),
    ("delegatedroom", {
        "proxies": (GROUPGUIDS % 5,),
        "read-only-proxies": (),
    }),
)

for uid, settings in proxies:
    elements = []
    for key, values in settings.iteritems():
        elements.append("<{key}>".format(key=key))
        for value in values:
            elements.append("<member>{value}</member>".format(value=value))
        elements.append("</{key}>".format(key=key))
    elementsString = "\n    ".join(elements)

    out.write("""<record>
    <guid>{uid}</guid>
    {elements}
</record>
""".format(uid=uid, elements=elementsString))

out.write("</proxies>\n")
out.close()
