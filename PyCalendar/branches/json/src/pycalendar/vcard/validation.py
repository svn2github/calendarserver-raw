##
#    Copyright (c) 2011-2012 Cyrus Daboo. All rights reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
##

from pycalendar.validation import partial, PropertyValueChecks
from pycalendar.vcard import definitions

VCARD_VALUE_CHECKS = {
    definitions.Property_VERSION: partial(PropertyValueChecks.stringValue, "3.0"),
    definitions.Property_PROFILE: partial(PropertyValueChecks.stringValue, "VCARD"),
}
