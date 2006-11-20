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

import sys

FORMATTERS = {}

def registerFormatter(short, formatter):
	FORMATTERS[short] = formatter

def listFormatters():
	return FORMATTERS.keys()

def getFormatter(short):
	return FORMATTERS[short]


class BaseFormatter(object):
	def __init__(self, dst=None):
		self.dst = dst
		
		if not self.dst:
			self.dst = sys.stdout


class PlainFormatter(BaseFormatter):
	def printRow(self, row, spacelen):
		for el in row:
			self.dst.write(str(el))
			self.dst.write(' '*(spacelen - len(str(el))))
			
		self.dst.write('\n')

registerFormatter('plain', PlainFormatter)


class CsvFormatter(BaseFormatter):
	def printRow(self, row, spacelen):
		for el in row:
			self.dst.write(str(el))
			self.dst.write(',')
		
		self.dst.write('\n')

registerFormatter('csv', CsvFormatter)


