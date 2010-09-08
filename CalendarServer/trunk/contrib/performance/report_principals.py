
"""
Benchmark a server's response to a simple displayname startswith
report.
"""

from urllib2 import HTTPDigestAuthHandler

from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet import reactor
from twisted.web.client import Agent
from twisted.web.http_headers import Headers

from httpauth import AuthHandlerAgent
from httpclient import StringProducer
from benchlib import initialize, sample

body = """\
<?xml version="1.0" encoding="utf-8" ?>
<x0:principal-property-search xmlns:x2="urn:ietf:params:xml:ns:caldav" xmlns:x0="DAV:" xmlns:x1="http://calendarserver.org/ns/" test="anyof"><x0:property-search><x0:prop><x0:displayname/></x0:prop><x0:match match-type="starts-with">user</x0:match></x0:property-search><x0:property-search><x0:prop><x1:email-address-set/></x0:prop><x0:match match-type="starts-with">user</x0:match></x0:property-search><x0:property-search><x0:prop><x1:first-name/></x0:prop><x0:match match-type="starts-with">user</x0:match></x0:property-search><x0:property-search><x0:prop><x1:last-name/></x0:prop><x0:match match-type="starts-with">user</x0:match></x0:property-search><x0:prop><x1:email-address-set/><x2:calendar-user-address-set/><x2:calendar-user-type/><x0:displayname/><x1:last-name/><x1:first-name/><x1:record-type/><x0:principal-URL/></x0:prop></x0:principal-property-search>"""

@inlineCallbacks
def measure(host, port, dtrace, attendeeCount, samples):
    user = password = "user01"
    root = "/"
    principal = "/"
    calendar = "report-principal"

    authinfo = HTTPDigestAuthHandler()
    authinfo.add_password(
        realm="Test Realm",
        uri="http://%s:%d/" % (host, port),
        user=user,
        passwd=password)
    agent = AuthHandlerAgent(Agent(reactor), authinfo)

    # Set up the calendar first
    yield initialize(agent, host, port, user, password, root, principal, calendar)

    url = 'http://%s:%d/principals/' % (host, port)
    headers = Headers({"content-type": ["text/xml"]})

    samples = yield sample(
        dtrace, samples, agent,
        lambda: ('REPORT', url, headers, StringProducer(body)))
    returnValue(samples)
