##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
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
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##

"""
Attribute names from Directory Service.
"""

attrRealName             = "dsAttrTypeStandard:RealName"
attrGUID                 = "dsAttrTypeStandard:GeneratedUID"
attrLastModified         = "dsAttrTypeStandard:ModificationTimestamp"
attrCalendarPrincipalURI = "dsAttrTypeStandard:CalendarPrincipalURI"
attrGroupMembers         = "dsAttrTypeStandard:GroupMembers"

# Array indices for the attributes returned when listing records
indexUID                  = 0
indexGUID                 = 1
indexLastModified         = 2
indexCalendarPrincipalURI = 3
