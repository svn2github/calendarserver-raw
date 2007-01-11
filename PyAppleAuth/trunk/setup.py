#!/usr/bin/env python
# 
# Copyright (c) 2006-2007 Apple Inc. All rights reserved.
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

from distutils.core import setup, Extension

module1 = Extension('appleauth',
	extra_compile_args = ['-arch', 'ppc', '-arch', 'i386'],
	extra_link_args = ['-framework', 'Security', '-arch', 'ppc', '-arch', 'i386'],
	sources = ['AppleAuth.c'])

setup (name = 'AppleAuth',
	version = '1.0',
	description = 'Apple Authorization',
	ext_modules = [module1])
