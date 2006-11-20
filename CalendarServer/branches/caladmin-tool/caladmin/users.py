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

from caladmin import caldav
from twisted.python.urlpath import URLPath

class UserAction(object):
	def __init__(self, config):
		self.config = config
		self.formatter = self.config.parent.formatter

		self.users = list(self.config.params)

	def printQuotaHead(self):
		if not self.config['list']:
			self.formatter.printRow(['Name', 'Quota', 'Used', 'Available'], 16)
			

	def printRecord(self, record):
		if self.config['list']:
			self.formatter.printRow([record[0]], 0)
		else:
			self.formatter.printRow(record, 16)

	def printRecords(self, records):
		self.printQuotaHead()

		for record in records:
			self.printRecord(record)

	def run(self):
		sh = caldav.makeHandle(self.config['server'],
							   self.config['username'],
							   self.config['password'])

		if not self.users:
			d = caldav.getQuotaStats(sh, 'users')
			d.addCallback(self.printRecords)

		else:
			users = self.users

			def _getNextUser(ign):
				if users:
					user = users.pop(0)

					return _getUser(user)

			def _getUser(user):
				d = caldav.getQuotaStats(sh, 'users', user)
				d.addCallback(lambda rec: self.printRecord(list(rec)[0]))
				d.addCallback(_getNextUser)

				return d

			self.printQuotaHead()
			
			user = users.pop(0)
			
			d = _getUser(user)

		return d

			
