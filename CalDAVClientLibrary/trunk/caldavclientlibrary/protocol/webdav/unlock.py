##
# Copyright (c) 2007-2013 Apple Inc. All rights reserved.
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

from caldavclientlibrary.protocol.webdav.requestresponse import RequestResponse
from caldavclientlibrary.protocol.webdav.definitions import methods
from caldavclientlibrary.protocol.webdav.definitions import headers

class Unlock(RequestResponse):

    def __init__(self, session, url, lock_token):

        super(Unlock, self).__init__(session, methods.UNLOCK, url)

        self.lock_token = lock_token


    def addHeaders(self, hdrs):
        # Do default
        super(Unlock, self).addHeaders(hdrs)

        # Add lock-token header
        hdrs.append((headers.LockToken, "<%s>" % (self.lock_token,)))
