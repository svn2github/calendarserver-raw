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

#pragma once

class CDirectoryServiceException
{
public:
	CDirectoryServiceException();
	CDirectoryServiceException(long error, const char* file, long line);
	~CDirectoryServiceException();
	
	static void ThrowDSError(long error, const char* file, long line);

	void SetPythonException();
	
private:
	long		mDSError;
	char		mDescription[1024];
};

# define ThrowIfDSErr(x) { if (x != eDSNoErr) CDirectoryServiceException::ThrowDSError(x, __FILE__, __LINE__); }
# define ThrowIfNULL(x) { if (x == NULL) CDirectoryServiceException::ThrowDSError(-1, __FILE__, __LINE__); }
