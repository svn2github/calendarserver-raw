##
# Copyright (c) 2007-2008 Apple Inc. All rights reserved.
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

from protocol.webdav.propfindparser import PropFindParser
from protocol.webdav.definitions import davxml
from protocol.caldav.definitions import caldavxml

PropFindParser.textProperties.add(caldavxml.calendar_description)
PropFindParser.textProperties.add(caldavxml.calendar_timezone)

PropFindParser.hrefListProperties.add(caldavxml.calendar_home_set)
PropFindParser.hrefListProperties.add(caldavxml.calendar_user_address_set)
PropFindParser.hrefListProperties.add(caldavxml.calendar_free_busy_set)
PropFindParser.hrefProperties.add(caldavxml.schedule_outbox_URL)
PropFindParser.hrefProperties.add(caldavxml.schedule_inbox_URL)
