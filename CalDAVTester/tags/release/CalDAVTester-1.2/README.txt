README for testcaldav.py

INTRODUCTION

testcaldav.py is a Python app that will run a series of scripted tests
against a CalDAV server and verify the output, and optionally measure
the time taken to complete one or more repeated requests. The tests are
defined by XML files and ancillary HTTP request body files. A number of
different verification options are provided.

Many tests are included in this package.

COMMAND LINE OPTIONS

testcaldav.py [-s filename] [-p filename] [-d] [--all] file1 file2 ...

	-s : filename specifies the file to use for server information
	(default is 'serverinfo.xml').

	-p : filename specifies the file to use to populate the server with
	data. Server data population only occurs when this option is
	present.

	-d : in conjuntion with -p, if present specifies that the populated
	data be removed after all tests have completed.

	--all : execute all tests found in the working directory. Each .xml
	file in that directory is examined and those corresponding to the
	caldavtest.dtd are executed.

	file1 file2 ...: a list of test files to execute tests from.

QUICKSTART

Edit the serverinfo.xml file to run the test against your server setup.

Run 'testcaldav.py --all' on the command line to run the tests. The app
will print its progress through the tests.

EXECUTION PROCESS

1. Read in XML config.
2. Execute <start> requests.
3. For each <test-suite>, run each <test> the specified number of times,
   executing each <request> in the test and verifying them.
4. Delete any resources from requests marked with 'end-delete'.
5. Execute <end> requests.

XML SCRIPT FILES

serverinfo.dtd

	Defines the XML DTD for the server information XML file:

	ELEMENT <host>
		host name for server to test.

	ELEMENT <port>
		port to use to connect to server.

	ELEMENT <ssl>
		if present, use SSL to connect to the server.

	ELEMENT <calendarpath>
		base path to a calendar on the server to run tests with.

	ELEMENT <user>
		user id to use when authenticating with the server.

	ELEMENT <pswd>
		password to use when authenticating with the server.

	ELEMENT <hostsubs>
		a string that can be substituted into test scripts and data
		using '$host:' as the substitution key string.

	ELEMENT <pathsubs>
		a string that can be substituted into test scripts and data
		using '$pathprefix:' as the substitution key string.

caldavtest.dtd:

	Defines the XML DTD for test script files:
	
	ATTRIBUTE ignore-all
		used on the top-level XML element to indicate whether this test
		is run when the --all command line switch for testcaldav.py is
		used. When set to 'no' the test is not run unless the file is
		explicitly specified on the command line.

	ELEMENT <description>
		a description for this test script.

	ELEMENT <start>
		defines a series of requests that are executed before testing
		starts. This can be used to initialize a set of calendar
		resources on which tests can be run.

	ELEMENT <end>
		defines a series of requests that are executed after testing is
		complete. This can be used to clean-up the server after testing.
		Note that there are special mechanisms in place to allow
		resources created during testing to be automatically deleted
		after testing, so there is no need to explicitly delete those
		resources here.

	ELEMENT <test-suite>
		defines a group of tests to be run. The suite is given a name
		and has an 'ignore' attribute that can be used to disable it.

	ELEMENT <test>
		defines a single test within a test suite. A test has a name,
		description and one or more requests associated with it. There
		is also an 'ignore' attribute to disable the test. Tests can be
		executed multiple times by setting the 'count' attribute to a
		value greater than 1. Timing information about the test can be
		printed out by setting the 'stats' attribute to 'yes'.


	ELEMENT <request>
		defines an HTTP request to send to the server. Attributes on the
		element are:

		ATTRIBUTE auth
			if 'yes', HTTP Basic authentication is done in the request.
		ATTRIBUTE user
			if provided this value is used as the user id for HTTP Basic
			authentication instead of the one in the serverinfo
			file.
		ATTRIBUTE pswd
			if provided this value is used as the password for HTTP
			Basic authentication instead of the one in the serverinfo
			file.
		ATTRIBUTE end-delete
			if set to 'yes', then the resource targeted by the request
			is deleted after testing is complete, but before the
			requests in the <end> element are run. This allows for quick
			clean-up of resources created during testing.
		ATTRIBUTE print-response
			if set to 'yes' then the HTTP response (header and body) is
			printed along with test results.

	ELEMENT <method>
		the HTTP method for this request. There are some 'special' methods that do some useful 'compound' operations:
			1) DELETEALL - deletes all resources within the collection specified by the <ruri> element.
			2) ACCESS-DISABLE - removes the access-disabled xattr on the file specified by the <ruri> element.
			3) ACCESS-ENABLE - adds the access-disabled xattr on the file specified by the <ruri> element.
			4) DELAY - pause for the number of seconds specified by the <ruri> element.
			5) LISTNEW - find the newest resource in the collection specified by the <ruri> element and put its URI
						into the $ variable for later use in an <ruri> element.
			6) GETNEW - get the data from the newest resource in the collection specified by the <ruri> element and put its URI
					    into the $ variable for later use in an <ruri> element.

	ELEMENT <header>
		can be used to specify additional headers in the request.
		
		ELEMENT <name>
			the header name.

		ELEMENT <value>
			the header value.

	ELEMENT <ruri>
		if the text in this element does not start with a '/', then it
		is appended to the text from the serverinfo <calendarpath>
		element (with '/' in between) and used as the URI for the
		request. If the text in this element starts with '/' then it is
		used as-is for the URI of the request.

	ELEMENT <data>
		used to specify the source and nature of data used in the
		request body, if there is one.
		
		ATTRIBUTE substitutions
			if set to 'yes' then '$host:' and '$pathprefix:'
			substitutions will be performed on the data before it is sent
			in the request.

		ELEMENT <content-type>
			the MIME content type for the request body.

		ELEMENT <filepath>
			the relative path for the file containing the request body
			data.

	ELEMENT <verify>
		if present, used to specify a procedures for verifying that the
		request executed as expected.
		
		ELEMENT <callback>
			the name of the verification method to execute.

		ELEMENT <arg>
			arguments sent to the verification method.

			ELEMENT <name>
				the name of the argument.

			ELEMENT <value>
				values for the argument.

	ELEMENT <grablocation>
		if present, this stores the value of any Location header
		returned in the response in an internal variable. If a
		subsequent request specifies has an <ruri> element value of '$'
		then the last stored location value is used as the actual
		request URI.
		
VERIFICATION Methods

statusCode:
	Performs a simple test of the response status code and returns True
	if the code matches, otherwise False.
	
	Argument: 'status'
		If the argument is not present, the any 2xx status code response
		will result in True. The status code value can be specified as
		'NNN' or 'Nxx' where 'N' is a digit and 'x' the letter x. In the
		later case, the verifier will return True if the response status
		code's 'major' digit matches the first digit.
	
	Example:
	
	<verify>
		<callback>statusCode</callback>
		<arg>
			<name>status</name>
			<value>2xx</value>
		</arg>
	</verify>
	
header:
	Performs a check of response header and value. This can be used to
	test for the presence or absence of a header, or the presence of a
	header with a specific value.

	Argument: 'header'
		This can be specified in one of three forms:
		
			'headername' - will test for the presence of the response
			header named 'header name'.

			'headername$value' - will test for the presence of the
			response header named 'headername' and also check that its
			value matches 'value'.

			'!headername' - will test for the absence of a header named
			'headername' in the response header.
	
	Example:
	
	<verify>
		<callback>header</callback>
		<arg>
			<name>header</name>
			<value>Content-type$text/plain</value>
		</arg>
	</verify>
	
dataMatch:
	Performs a check of response body and matches it against the data in the specified file.

	Argument: 'filepath'
		The file path to a file containing data to match the response body to.
	
	Example:
	
	<verify>
		<callback>dataMatch</callback>
		<arg>
			<name>filepath</name>
			<value>resources/put.ics</value>
		</arg>
	</verify>
	
dataString:
	Performs a check of response body tries to find occurrences of the specified strings or the
	absence of specified strings.

	Argument: 'contains'
		One or more strings that must be contained in the data (case-sensitive).
	
	Argument: 'notcontains'
		One or more strings that must not be contained in the data (case-sensitive).
	
	Example:
	
	<verify>
		<callback>dataString</callback>
		<arg>
			<name>contains</name>
			<value>BEGIN:VEVENT</value>
		</arg>
		<arg>
			<name>notcontains</name>
			<value>BEGIN:VTODO</value>
		</arg>
	</verify>
	
prepostcondition:
	Performs a check of response body and status code to verify that a
	specific pre-/post-condition error was returned. The response status
	code has to be one of 403 or 409.

	Argument: 'error'
		The expected XML element qualified-name to match.
	
	Example:
	
	<verify>
		<callback>prepostcondition</callback>
		<arg>
			<name>error</name>
			<value>DAV:too-many-matches</value>
		</arg>
	</verify>
	
multistatusItems:
	Performs a check of multi-status response body and checks to see
	what hrefs were returned and whether those had a good (2xx) or bad
	(non-2xx) response code. The overall response status must be 207.

	Argument: 'okhrefs'
		A set of hrefs for which a 2xx response status is required.
	
	Argument: 'badhrefs'
		A set of hrefs for which a non-2xx response status is required.

	Argument: 'prefix'
		A prefix that is appended to all of the specified okhrefs and
		badhrefs values.
	
	Example:
	
	<verify>
		<callback>multistatusitems</callback>
		<arg>
			<name>okhrefs</name>
			<value>/calendar/test/1.ics</value>
			<value>/calendar/test/2.ics</value>
			<value>/calendar/test/3.ics</value>
		</arg>
		<arg>
			<name>badhrefs</name>
			<value>/calendar/test/4.ics</value>
			<value>/calendar/test/5.ics</value>
			<value>/calendar/test/6.ics</value>
		</arg>
	</verify>
	
propfindItems:
	Performs a check of propfind multi-status response body and checks to see
	whether the returned properties (and optionally their values) are good (2xx) or bad
	(non-2xx) response code. The overall response status must be 207.

	Argument: 'okprops'
		A set of properties for which a 2xx response status is required. Two forms can be used:
		
		'propname' - will test for the presence of the property named
		'propname'. The element data must be a qualified XML element
		name.
	
		'propname$value' - will test for the presence of the property
		named 'propname' and check that its value matches the provided
		'value'. The element data must be a qualified XML element name.
		XML elements in the property value can be tested provided proper
		XML escaping is used (see example).
	
	Argument: 'badhrefs'
		A set of properties for which a non-2xx response status is
		required. The same two forms as used for 'okprops' can be used
		here.

	Argument: 'ignore'
		One or more href values for hrefs in the response which will be
		ignored. e.g. when doing a PROPFIND Depth:1, you may want to
		ignore the top-level resource when testing as only the
		properties on the child resources may be of interest.
	
	Example:
	
	<verify>
		<callback>propfindItems</callback>
		<arg>
			<name>okprops</name>
			<value>DAV:getetag</value>
			<value>DAV:getcontenttype$text/plain</value>
			<value>X:getstate$&lt;X:ok/&gt;</value>
		</arg>
		<arg>
			<name>badprops</name>
			<value>X:nostate</value>
		</arg>
		<arg>
			<name>ignore</name>
			<value>/calendars/test/</value>
		</arg>
	</verify>
	
acltems:
	Performs a check of multi-status response body and checks to see
	whether the specified privileges are granted or denied on each
	resource in the response for the current user (i.e. tests the
	DAV:current-user-privilege-set).

	Argument: 'granted'
		A set of privileges that must be granted.
	
	Argument: 'denied'
		A set of privileges that must be denied denied.

	Example:
	
	<verify>
		<callback>multistatusitems</callback>
		<arg>
			<name>granted</name>
			<value>DAV:read</value>
		</arg>
		<arg>
			<name>denied</name>
			<value>DAV:write</value>
			<value>DAV:write-acl</value>
		</arg>
	</verify>
	
freeBusy:
	Performs a check of the response body to verify it contains an
	iCalendar VFREEBUSY object with the specified busy periods and
	types.

	Argument: 'busy'
		A set of iCalendar PERIOD values for FBTYPE=BUSY periods
		expected in the response.
	
	Argument: 'tentative'
		A set of iCalendar PERIOD values for FBTYPE=BUSY-TENTATIVE
		periods expected in the response.
	
	Argument: 'unavailable'
		A set of iCalendar PERIOD values for FBTYPE=BUSY-UNAVAILABLE
		periods expected in the response.
	
	Example:
	
	<verify>
		<callback>freeBusy</callback>
		<arg>
			<name>busy</name>
			<value>20060107T010000Z/20060107T020000Z</value>
			<value>20060107T150000Z/20060107T163000Z</value>
			<value>20060108T150000Z/20060108T180000Z</value>
		</arg>
		<arg>
			<name>unavailable</name>
			<value>20060108T130000Z/20060108T150000Z</value>
		</arg>
		<arg>
			<name>tentative</name>
			<value>20060108T160000Z/20060108T170000Z</value>
			<value>20060108T210000Z/20060108T213000Z</value>
		</arg>
	</verify>

