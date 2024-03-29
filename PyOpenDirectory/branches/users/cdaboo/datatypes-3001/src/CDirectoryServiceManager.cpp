/**
 * A class that wraps high-level Directory Service calls needed by the
 * CalDAV server.
 **
 * Copyright (c) 2006-2007 Apple Inc. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * DRI: Cyrus Daboo, cdaboo@apple.com
 **/

#include "CDirectoryServiceManager.h"

#include "CDirectoryService.h"
#include "CDirectoryServiceException.h"

#pragma mark -----Public API

CDirectoryServiceManager::CDirectoryServiceManager(const char* nodename)
{
    mNodeName = ::strdup(nodename);
}

CDirectoryServiceManager::~CDirectoryServiceManager()
{
    ::free(mNodeName);
}

CDirectoryService* CDirectoryServiceManager::GetService()
{
    return new CDirectoryService(mNodeName);
}
