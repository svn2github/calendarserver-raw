##
# Copyright (c) 2008 Apple Inc. All rights reserved.
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

from twistedcaldav.mail import *
from twistedcaldav.test.util import TestCase
from twistedcaldav.ical import Component
from twistedcaldav.config import config
from twistedcaldav.scheduling.itip import iTIPRequestStatus


from twisted.internet.defer import inlineCallbacks
import email
import os


def echo(*args):
    return args


class MailHandlerTests(TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self.handler = MailHandler(dataRoot=":memory:")
        self.dataDir = os.path.join(os.path.dirname(__file__), "data", "mail")

    def test_iconPath(self):
        iconPath = self.handler.getIconPath({'day':'1', 'month':'1'}, False, language='en')
        iconDir = "/usr/share/caldavd/share/date_icons"
        if os.path.exists(iconDir):
            if os.path.exists("%s/JAN/01.png" % (iconDir,)):
                self.assertEquals(iconPath, "%s/JAN/01.png" % (iconDir,))
            else:
                self.assertEquals(iconPath, "%s/01/01.png" % (iconDir,))

    def test_checkDSNFailure(self):

        data = {
            'good_reply' : (False, None, None),
            'dsn_failure_no_original' : (True, 'failed', None),
            'dsn_failure_no_ics' : (True, 'failed', None),
            'dsn_failure_with_ics' : (True, 'failed', 'BEGIN:VCALENDAR\nVERSION:2.0\nCALSCALE:GREGORIAN\nMETHOD:REQUEST\nPRODID:-//example Inc.//iCal 3.0//EN\nBEGIN:VTIMEZONE\nTZID:US/Pacific\nBEGIN:STANDARD\nDTSTART:20071104T020000\nRRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\nTZNAME:PST\nTZOFFSETFROM:-0700\nTZOFFSETTO:-0800\nEND:STANDARD\nBEGIN:DAYLIGHT\nDTSTART:20070311T020000\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\nTZNAME:PDT\nTZOFFSETFROM:-0800\nTZOFFSETTO:-0700\nEND:DAYLIGHT\nEND:VTIMEZONE\nBEGIN:VEVENT\nUID:1E71F9C8-AEDA-48EB-98D0-76E898F6BB5C\nDTSTART;TZID=US/Pacific:20080812T094500\nDTEND;TZID=US/Pacific:20080812T104500\nATTENDEE;CUTYPE=INDIVIDUAL;CN=User 01;PARTSTAT=ACCEPTED:mailto:user01@exam\n ple.com\nATTENDEE;CUTYPE=INDIVIDUAL;RSVP=TRUE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-A\n CTION;CN=nonexistant@example.com:mailto:nonexistant@example.com\nCREATED:20080812T191857Z\nDTSTAMP:20080812T191932Z\nORGANIZER;CN=User 01:mailto:xyzzy+8e16b897-d544-4217-88e9-a363d08\n 46f6c@example.com\nSEQUENCE:2\nSUMMARY:New Event\nTRANSP:OPAQUE\nEND:VEVENT\nEND:VCALENDAR\n'),
        }

        for filename, expected in data.iteritems():
            msg = email.message_from_string(
                file(os.path.join(self.dataDir, filename)).read()
            )
            self.assertEquals(self.handler.checkDSN(msg), expected)


    def test_processDSN(self):

        template = 'BEGIN:VCALENDAR\nVERSION:2.0\nCALSCALE:GREGORIAN\nMETHOD:REQUEST\nPRODID:-//example Inc.//iCal 3.0//EN\nBEGIN:VTIMEZONE\nTZID:US/Pacific\nBEGIN:STANDARD\nDTSTART:20071104T020000\nRRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\nTZNAME:PST\nTZOFFSETFROM:-0700\nTZOFFSETTO:-0800\nEND:STANDARD\nBEGIN:DAYLIGHT\nDTSTART:20070311T020000\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\nTZNAME:PDT\nTZOFFSETFROM:-0800\nTZOFFSETTO:-0700\nEND:DAYLIGHT\nEND:VTIMEZONE\nBEGIN:VEVENT\nUID:1E71F9C8-AEDA-48EB-98D0-76E898F6BB5C\nDTSTART;TZID=US/Pacific:20080812T094500\nDTEND;TZID=US/Pacific:20080812T104500\nATTENDEE;CUTYPE=INDIVIDUAL;CN=User 01;PARTSTAT=ACCEPTED:mailto:user01@exam\n ple.com\nATTENDEE;CUTYPE=INDIVIDUAL;RSVP=TRUE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-A\n CTION;CN=nonexistant@example.com:mailto:nonexistant@example.com\nCREATED:20080812T191857Z\nDTSTAMP:20080812T191932Z\nORGANIZER;CN=User 01:mailto:xyzzy+%s@example.com\nSEQUENCE:2\nSUMMARY:New Event\nTRANSP:OPAQUE\nEND:VEVENT\nEND:VCALENDAR\n'

        # Make sure an unknown token is not processed
        calBody = template % "bogus_token"
        self.assertEquals(self.handler.processDSN(calBody, "xyzzy", echo),
           None)

        # Make sure a known token *is* processed
        token = self.handler.db.createToken("mailto:user01@example.com",
            "mailto:user02@example.com", "1E71F9C8-AEDA-48EB-98D0-76E898F6BB5C")
        calBody = template % token
        organizer, attendee, calendar, msgId = self.handler.processDSN(calBody,
            "xyzzy", echo)
        self.assertEquals(organizer, 'mailto:user01@example.com')
        self.assertEquals(attendee, 'mailto:user02@example.com')
        self.assertEquals(str(calendar), 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nCALSCALE:GREGORIAN\r\nMETHOD:REQUEST\r\nPRODID:-//example Inc.//iCal 3.0//EN\r\nBEGIN:VTIMEZONE\r\nTZID:US/Pacific\r\nBEGIN:STANDARD\r\nDTSTART:20071104T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU\r\nTZNAME:PST\r\nTZOFFSETFROM:-0700\r\nTZOFFSETTO:-0800\r\nEND:STANDARD\r\nBEGIN:DAYLIGHT\r\nDTSTART:20070311T020000\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU\r\nTZNAME:PDT\r\nTZOFFSETFROM:-0800\r\nTZOFFSETTO:-0700\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE\r\nBEGIN:VEVENT\r\nUID:1E71F9C8-AEDA-48EB-98D0-76E898F6BB5C\r\nDTSTART;TZID=US/Pacific:20080812T094500\r\nDTEND;TZID=US/Pacific:20080812T104500\r\nCREATED:20080812T191857Z\r\nDTSTAMP:20080812T191932Z\r\nORGANIZER;CN=User 01:mailto:user01@example.com\r\nREQUEST-STATUS:5.1;Service unavailable\r\nSEQUENCE:2\r\nSUMMARY:New Event\r\nTRANSP:OPAQUE\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
        self.assertEquals(msgId, 'xyzzy')



    def test_processReply(self):
        msg = email.message_from_string(
            file(os.path.join(self.dataDir, 'good_reply')).read()
        )

        # Make sure an unknown token is not processed
        result = self.handler.processReply(msg, echo)
        self.assertEquals(result, None)

        # Make sure a known token *is* processed
        self.handler.db.createToken("mailto:user01@example.com", "mailto:xyzzy@example.com", icaluid="1E71F9C8-AEDA-48EB-98D0-76E898F6BB5C", token="d7cdf68d-8b73-4df1-ad3b-f08002fb285f")
        organizer, attendee, calendar, msgId = self.handler.processReply(msg,
            echo)
        self.assertEquals(organizer, 'mailto:user01@example.com')
        self.assertEquals(attendee, 'mailto:xyzzy@example.com')
        self.assertEquals(msgId, '<1983F777-BE86-4B98-881E-06D938E60920@example.com>')

    def test_processReplyMissingOrganizer(self):
        msg = email.message_from_string(
            file(os.path.join(self.dataDir, 'reply_missing_organizer')).read()
        )
        # stick the token in the database first
        self.handler.db.createToken("mailto:user01@example.com", "mailto:xyzzy@example.com", icaluid="1E71F9C8-AEDA-48EB-98D0-76E898F6BB5C", token="d7cdf68d-8b73-4df1-ad3b-f08002fb285f")

        organizer, attendee, calendar, msgId = self.handler.processReply(msg,
            echo)
        organizerProp = calendar.mainComponent().getOrganizerProperty()
        self.assertTrue(organizerProp is not None)
        self.assertEquals(organizer, "mailto:user01@example.com")

    def test_processReplyMissingAttendee(self):
        msg = email.message_from_string(
            file(os.path.join(self.dataDir, 'reply_missing_attendee')).read()
        )
        # stick the token in the database first
        self.handler.db.createToken("mailto:user01@example.com", "mailto:xyzzy@example.com", icaluid="1E71F9C8-AEDA-48EB-98D0-76E898F6BB5C", token="d7cdf68d-8b73-4df1-ad3b-f08002fb285f")

        organizer, attendee, calendar, msgId = self.handler.processReply(msg,
            echo)

        # Since the expected attendee was missing, the reply processor should
        # have added an attendee back in with a "5.1;Service unavailable"
        # schedule-status
        attendeeProp = calendar.mainComponent().getAttendeeProperty([attendee])
        self.assertEquals(attendeeProp.paramValue("SCHEDULE-STATUS"),
            iTIPRequestStatus.SERVICE_UNAVAILABLE)


    @inlineCallbacks
    def test_outbound(self):
        """
        Make sure outbound( ) stores tokens properly so they can be looked up
        """

        config.Scheduling.iMIP.Sending.Address = "server@example.com"

        data = (
            # Initial invite
            (
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:CFDD5E46-4F74-478A-9311-B3FF905449C3
DTSTART:20100325T154500Z
DTEND:20100325T164500Z
ATTENDEE;CN=The Attendee;CUTYPE=INDIVIDUAL;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:attendee@example.com
ATTENDEE;CN=The Organizer;CUTYPE=INDIVIDUAL;EMAIL=organizer@example.com;PARTSTAT=ACCEPTED:urn:uuid:C3B38B00-4166-11DD-B22C-A07C87E02F6A
ORGANIZER;CN=The Organizer;EMAIL=organizer@example.com:urn:uuid:C3B38B00-4166-11DD-B22C-A07C87E02F6A
SUMMARY:testing outbound( )
END:VEVENT
END:VCALENDAR
""",
                "CFDD5E46-4F74-478A-9311-B3FF905449C3",
                "mailto:organizer@example.com",
                "mailto:attendee@example.com",
                "new",
                "organizer@example.com",
                "The Organizer",
                [
                    (u'The Attendee', u'attendee@example.com'),
                    (u'The Organizer', None)
                ],
                "The Organizer <organizer@example.com>",
                "attendee@example.com",
            ),

            # Update
            (
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:CFDD5E46-4F74-478A-9311-B3FF905449C3
DTSTART:20100325T154500Z
DTEND:20100325T164500Z
ATTENDEE;CN=The Attendee;CUTYPE=INDIVIDUAL;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:attendee@example.com
ATTENDEE;CN=The Organizer;CUTYPE=INDIVIDUAL;EMAIL=organizer@example.com;PARTSTAT=ACCEPTED:urn:uuid:C3B38B00-4166-11DD-B22C-A07C87E02F6A
ORGANIZER;CN=The Organizer;EMAIL=organizer@example.com:urn:uuid:C3B38B00-4166-11DD-B22C-A07C87E02F6A
SUMMARY:testing outbound( ) *update*
END:VEVENT
END:VCALENDAR
""",
                "CFDD5E46-4F74-478A-9311-B3FF905449C3",
                "mailto:organizer@example.com",
                "mailto:attendee@example.com",
                "update",
                "organizer@example.com",
                "The Organizer",
                [
                    (u'The Attendee', u'attendee@example.com'),
                    (u'The Organizer', None)
                ],
                "The Organizer <organizer@example.com>",
                "attendee@example.com",
            ),

            # Reply
            (
                """BEGIN:VCALENDAR
VERSION:2.0
METHOD:REPLY
BEGIN:VEVENT
UID:DFDD5E46-4F74-478A-9311-B3FF905449C4
DTSTART:20100325T154500Z
DTEND:20100325T164500Z
ATTENDEE;CN=The Attendee;CUTYPE=INDIVIDUAL;PARTSTAT=ACCEPTED:mailto:attendee@example.com
ORGANIZER;CN=The Organizer;EMAIL=organizer@example.com:mailto:organizer@example.com
SUMMARY:testing outbound( ) *reply*
END:VEVENT
END:VCALENDAR
""",
                None,
                "mailto:attendee@example.com",
                "mailto:organizer@example.com",
                "reply",
                "organizer@example.com",
                "The Organizer",
                [
                    (u'The Attendee', u'attendee@example.com'),
                ],
                "attendee@example.com",
                "organizer@example.com",
            ),

        )
        for (inputCalendar, UID, inputOriginator, inputRecipient, inviteState,
            outputOrganizerEmail, outputOrganizerName, outputAttendeeList,
            outputFrom, outputRecipient) in data:

            (actualInviteState, actualCalendar, actualOrganizerEmail,
                actualOrganizerName, actualAttendeeList, actualFrom,
                actualRecipient, actualReplyTo) = (yield self.handler.outbound(
                    inputOriginator,
                    inputRecipient,
                    Component.fromString(inputCalendar.replace("\n", "\r\n")),
                    send=False)
                )

            self.assertEquals(actualInviteState, inviteState)
            self.assertEquals(actualOrganizerEmail, outputOrganizerEmail)
            self.assertEquals(actualOrganizerName, outputOrganizerName)
            self.assertEquals(actualAttendeeList, outputAttendeeList)
            self.assertEquals(actualFrom, outputFrom)
            self.assertEquals(actualRecipient, outputRecipient)

            if UID: # The organizer is local, and server is sending to remote
                    # attendee

                token = self.handler.db.getToken(inputOriginator,
                    inputRecipient, UID)
                self.assertNotEquals(token, None)
                self.assertEquals(actualReplyTo,
                    "server+%s@example.com" % (token,))

            else: # Reply only -- the attendee is local, and server is sending
                  # reply to remote organizer

                self.assertEquals(actualReplyTo, actualFrom)


class MailGatewayTokensDatabaseTests(TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self.db = MailGatewayTokensDatabase(":memory:")

    def test_tokens(self):
        self.assertEquals(self.db.lookupByToken("xyzzy"), None)

        token = self.db.createToken("organizer", "attendee", "icaluid")
        self.assertEquals(self.db.getToken("organizer", "attendee", "icaluid"), token)
        self.assertEquals(self.db.lookupByToken(token),
            ("organizer", "attendee", "icaluid"))
        self.db.deleteToken(token)
        self.assertEquals(self.db.lookupByToken(token), None)
