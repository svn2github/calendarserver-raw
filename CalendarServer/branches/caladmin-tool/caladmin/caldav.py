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

import os

from urlparse import urlparse

from zanshin.webdav import ServerHandle, PropfindRequest
from zanshin.util import PackElement

from twisted.web import microdom

defaultPorts = {'https': 443,
				'http': 80}

def makeHandle(url, username=None, password=None):
	"""Get a ServerHandle for the given url
	"""
	scheme, server, path, parameters, query, fragment, = urlparse(url)
	
	port = defaultPorts[scheme]

	serverPortList = server.split(':', 1)
	server = serverPortList[0]
	if len(serverPortList) > 1:
		port = int(serverPortList[1])

	sh = ServerHandle(host=server, 
					  port=port, 
					  username=username,
					  password=password)

	return sh


def parseQuotaPropfind(response):
	"""Generator that yeilds:
	 name - a string as derived from basepath on the returned uri.
	 total - an integer equal to the available + the used
	 used - an integer from quota-used-bytes
	 avail - an integer from quota-available-bytes

	 total, used, and available values are given as # of bytes.
    """

	dom = microdom.parseString(response.body)
	
	for resp in microdom.getElementsByTagName(dom, 'response'):
		href = resp.getElementsByTagName('href'
										 )[0].firstChild().toxml()

		avail = resp.getElementsByTagName('quota-available-bytes'
										  )[0].firstChild()
		used = resp.getElementsByTagName('quota-used-bytes'
										 )[0].firstChild()

		name = href.split('/')[-2]

		if name in ('users', 'groups', 'resources'):
			continue

		if not avail:
			avail = 0
		else:
			avail = int(avail.toxml())

		if not used:
			used = 0
		else:
			used = int(used.toxml())

		yield (name, avail+used, used, avail)
	

def getQuotaStats(handle, type, entity=None):
	"""Utility function for getting a generator as described in 
	parseQuotaPropfind given a server handle for the given type
	and possibly a given entity (username, groupname or resource name.)
	"""

	path = os.path.join('/calendars', type)
	depth = '1'

	if entity:
		path = os.path.join(path, entity)
		depth = '0'

	quotaProps = [PackElement(x) for x in ['quota-available-bytes',
										   'quota-used-bytes']]

	request = PropfindRequest(
		path,
		depth, 
		quotaProps,
		None)

	d = handle.addRequest(request)
	d.addCallback(parseQuotaPropfind)

	return d


