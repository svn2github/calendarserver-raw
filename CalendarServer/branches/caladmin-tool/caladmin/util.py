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
# DRI: David Reid, dreid@apple.com
##

def prepareByteValue(config, value):
    if config['human']:
        KB = value/1024.0
        if KB < 1:
            return '%d' % (value,)

        MB = KB/1024.0
        if MB < 1:
            return '%5.2fKB' % (KB,)

        GB = MB/1024.0
        if GB < 1:
            return '%5.2fMB' % (MB,)

        return '%5.2fGB' % (GB,)

    elif config['gigabytes']:
        G = value/1024.0/1024.0/1024.0

        return '%5.2fGB' % (G,)

    elif config['megabytes']:
        M = value/1024.0/1024.0

        return '%5.2fMB' % (M,)

    elif config['kilobytes']:
        K = value/1024.0
        return '%5.2fKB' % (K,)

    return value
