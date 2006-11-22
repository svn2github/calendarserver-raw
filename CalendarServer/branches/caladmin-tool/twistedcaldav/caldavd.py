#!/usr/bin/env python

##
# Copyright (c) 2005-2006 Apple Computer, Inc. All rights reserved.
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
# DRI: Cyrus Daboo, cdaboo@apple.com
##

import sys
import os
import getopt
import signal
from tempfile import mkstemp

try:
    #
    # plistlib is only included in Mac OS distributions of Python.
    # This may change in Python 2.6, see:
    #   https://sourceforge.net/tracker/?func=detail&atid=105470&aid=1555501&group_id=5470
    #
    from plistlib import readPlist
except ImportError:
    from twistedcaldav.py.plistlib import readPlist

sys.path.insert(0, "/usr/share/caldavd/lib/python")

"""
Parse the command line and read in a configuration file and then launch the server.
"""

caldavd_defaults = {
    'Verbose': False,
    "RunStandalone": True,
    "DocumentRoot": "/Library/CalendarServer/Documents",
    "Port": 8008,
    "SSLEnable": False,
    "SSLPort": 8443,
    "SSLOnly": False,
    "SSLPrivateKey": "/etc/certificates/Default.key",
    "SSLCertificate": "/etc/certificates/Default.crt",
    "ManholePort": 0,           
    "DropBoxEnabled": True,
    "DropBoxName": "dropbox",
    "DropBoxInheritedACLs": True,
    "NotificationsEnabled": False,
    "NotificationCollectionName": "notifications",
    "ServerLogFile": "/var/log/caldavd/server.log",
    "ErrorLogFile": "/var/log/caldavd/error.log",
    "PIDFile": "/var/run/caldavd.pid",
    "Repository": "etc/caldavd/repository.xml",
    "CreateAccounts": False,
    "ResetAccountACLs": False,
    "twistdLocation": "/usr/share/caldavd/bin/twistd",
    "MaximumAttachmentSizeBytes": 1048576,
    "UserQuotaBytes": 104857600,
    }

class caldavd(object):
    """
    Runs the caldav server.
    """
    
    def __init__(self):
        # Option defaults
        self.plistfile = "/etc/caldavd/caldavd.plist"

#         self.verbose = False
#         self.daemonize = True
#         self.docroot = "/Library/CalendarServer/Documents"

#         self.repo = "/etc/caldavd/repository.xml"
#         self.doacct = False
#         self.doacl = False

#         self.port = 8008
#         self.dossl = False
#         self.sslport = 8443
#         self.onlyssl = False
#         self.keyfile = "/etc/certificates/Default.key"
#         self.certfile = "/etc/certificates/Default.crt"
#         self.manhole = 0

#         self.dropbox = True
#         self.dropboxName = "dropbox"
#         self.dropboxACLs = True
#         self.notifications = False
#         self.notifcationName = "notifications"

#         self.serverlogfile = "/var/log/caldavd/server.log"
#         self.errorlogfile = "/var/log/caldavd/error.log"
#         self.pidfile = "/var/run/caldavd.pid"
        
#         self.twistd = "/usr/share/caldavd/bin/twistd"

#         self.maxsize  =  1048576    # 1 Mb
#         self.quota    =  104857600  # 100 Mb
        
        self.action = None
    
    def printit(self):
        """
        Print out details about the current configuration.
        """

        print "Current Configuration"
        print ""
        print "Configuration File:               %s" % (self.plistfile,)
        print ""
        print "Run as daemon:                    %s" % (self.daemonize,)
        print "Document Root:                    %s" % (self.docroot,)
        print "Repository Configuration:         %s" % (self.repo,)
        print "Generate Accounts in Repository:  %s" % (self.doacct,)
        print "Reset ACLs on Generated Accounts: %s" % (self.doacl,)
        print "Non-ssl Port:                     %s" % (self.port,)
        print "Use SSL:                          %s" % (self.dossl,)
        print "SSL Port:                         %s" % (self.sslport,)
        print "Only Use SSL:                     %s" % (self.onlyssl,)
        print "SSL Private Key File:             %s" % (self.keyfile,)
        print "SSL Certificate File:             %s" % (self.certfile,)
        print "Drop Box Enabled:                 %s" % (self.dropbox,)
        print "Drop Box Name:                    %s" % (self.dropboxName,)
        print "Drop Box ACLs are Inherited       %s" % (self.dropboxACLs,)
        print "Notifications Enabled:            %s" % (self.notifications,)
        print "Notification Collection Name:     %s" % (self.notifcationName,)
        print "Server Log File:                  %s" % (self.serverlogfile,)
        print "Error Log File:                   %s" % (self.errorlogfile,)
        print "PID File:                         %s" % (self.pidfile,)
        print "twistd Location:                  %s" % (self.twistd,)
        print "Maximum Calendar Resource Size:   %d bytes" % (self.maxsize,)
        print "Global per-user quota limit:      %d bytes" % (self.quota,)

    def run(self):
        """
        Run the caldavd server using the provided options and configuration.

        @raise: C:{ValueError} if options or configuration are wrong.
        """

        # Parse command line options and config file
        self.commandLine()
        if self.action is None:
            return
        
        # Dispatch action
        {"start":   self.start,
         "stop":    self.stop,
         "restart": self.restart}[self.action]()

    def start(self):
        """
        Start the caldavd server.
        """
        
        print "Starting CalDAV Server",
        try:
            fd, tac = mkstemp(prefix="caldav")
            os.write(fd, self.generateTAC())
            os.close(fd)
        except Exception, e:
            print "        [Failed]"
            print "Unable to create temporary file for server configuration."
            print e
            sys.exit(1)
        
        # Create arguments for twistd
        args = [os.path.basename(sys.executable)]
        args.append(self.twistd)
        if not self.daemonize:
            args.append("-n")
        args.append("--logfile=%s" % (self.errorlogfile,))
        args.append("--pidfile=%s" % (self.pidfile,))
        args.append("-y")
        args.append(tac)

        # Create environment for twistd
        environment = dict(os.environ)
        environment["PYTHONPATH"] = ":".join(sys.path)

        # spawn the twistd python process
        try:
            os.spawnve(os.P_WAIT, sys.executable, args, environment)
        except OSError, why:
            print "        [Failed]"
            print "Error: %s" % (why[1],)
        
        # Get rid of temp file
        try:
            os.unlink(tac)
        except:
            pass
        print "        [Done]"
    
    def stop(self):
        """
        Stop the caldavd server.
        """
        
        if os.path.exists(self.pidfile):
            try:
                pid = int(open(self.pidfile).read())
            except ValueError:
                sys.exit("Pidfile %s contains non-numeric value" % self.pidfile)
            try:
                print "Stopping CalDAV Server",
                os.kill(pid, signal.SIGTERM)
                print "        [Done]"
            except OSError, why:
                print "        [Failed]"
                print "Error: %s" % (why[1],)
        else:
            print "CalDAV server is not running"
    
    def restart(self):
        """
        Restart the caldavd server.
        """
        self.stop()
        self.start()
        
    def commandLine(self):
        """
        Parse the command line options into the config object.
        
        @return: the C{str} for the requested action, or C{None} when
            immediate exit is called for.
        @raise: C{ValueError} when a problem occurs with the options.
        """
        options, args = getopt.getopt(sys.argv[1:], "hvf:XT:p")
        
        # Process the plist file first, then the options, so that command line
        # options get to override plist options
        pls = [p for p in options if p[0] == "-f"]
        if len(pls) == 1:
            self.plistfile = pls[0][1]
        if not os.path.exists(self.plistfile):
            print "Configuration file does not exist: %s" % (self.plistfile,)
            raise ValueError
        self.parsePlist()
    
        # Parse all the options
        do_print = False
        for option, value in options:
            if option == "-h":
                self.usage()
                return
            elif option == "-v":
                self.verbose = True
            elif option == "-f":
                # We should have handled this already
                pass
            elif option == "-X":
                self.daemonize = False
            elif option == "-T":
                self.twistd = value
            elif option == "-p":
                do_print = True
            else:
                print "Unrecognized option: %s" % (option,)
                self.usage()
                raise ValueError
        
        # Print out config if requested
        if do_print:
            self.printit()
            return
    
        # Process arguments
        if len(args) == 0:
            print "No arguments given. One of start, stop or restart must be present."
            self.usage()
            raise ValueError
        elif len(args) > 1:
            print "Too many arguments given. Only one of start, stop or restart must be present."
            self.usage()
            raise ValueError
        elif args[0] not in ("start", "stop", "restart"):
            print "Wrong arguments given: %s" % (args[0],)
            self.usage()
            raise ValueError
        
        # Verify that configuration is valid
        if not self.validate():
            raise ValueError
    
        self.action = args[0]
    
    def parsePlist(self):
    	print "Reading configuration file %s." % (self.plistfile,)

        root = readPlist(self.plistfile)
        
        # dict that maps between plist keys and class attributes
        mapper = {
                   "Verbose":                    "verbose",
                   "RunStandalone":              "daemonize",
                   "DocumentRoot":               "docroot",
                   "Port":                       "port",
                   "SSLEnable":                  "dossl",
                   "SSLPort":                    "sslport",
                   "SSLOnly":                    "onlyssl",
                   "SSLPrivateKey":              "keyfile",
                   "SSLCertificate":             "certfile",
                   "ManholePort":                "manhole",
                   "DropBoxEnabled":             "dropbox",
                   "DropBoxName":                "dropboxName",
                   "DropBoxInheritedACLs":       "dropboxACLs",
                   "NotificationsEnabled":       "notifications",
                   "NotificationCollectionName": "notifcationName",
                   "ServerLogFile":              "serverlogfile",
                   "ErrorLogFile":               "errorlogfile",
                   "PIDFile":                    "pidfile",
                   "Repository":                 "repo",
                   "CreateAccounts":             "doacct",
                   "ResetAccountACLs":           "doacl",
                   "twistdLocation":             "twistd",
                   "MaximumAttachmentSizeBytes": "maxsize",
                   "UserQuotaBytes":             "quota",
                  }
        
        for k,v in root.items():
            if mapper.has_key(k) and hasattr(self, mapper[k]):
                setattr(self, mapper[k], v)
            elif caldavd_defaults.has_key(k) and hasattr(self, mapper[k]):
                setattr(self, mapper[k], caldavd_defaults[k])
            else:
                print "Unknown option: %s" % (k,)

    def validate(self):
        
        result = True

        if not os.path.exists(self.docroot):
            print "Document Root does not exist: %s" % (self.docroot,)
            result = False

        if not os.path.exists(self.repo):
            print "Repository File does not exist: %s" % (self.repo,)
            result = False

        if self.dossl and not os.path.exists(self.keyfile):
            print "SSL Private Key File does not exist: %s" % (self.keyfile,)
            result = False

        if self.dossl and not os.path.exists(self.certfile):
            print "SSL Certificate File does not exist: %s" % (self.certfile,)
            result = False

        if not self.dossl and self.onlyssl:
            self.dossl = True

        if not self.daemonize:
            self.errorlogfile = "-"

        if not os.path.exists(self.twistd):
            print "twistd does not exist: %s" % (self.twistd,)
            result = False
            
        return result

    def usage(self):
        default = caldavd()
        print """Usage: caldavd [options] start|stop|restart
Options:
    -h          Print this help and exit
    -v          Be verbose
    -f config   Specify path to configuration file [""" + default.plistfile + """]
    -X          Do not daemonize
    -T twistd   Specify path to twistd [""" + default.twistd + """]
    -p          Print current configuration and exit
"""
    
    def generateTAC(self):
        return """
docroot         = "%(docroot)s"
repo            = "%(repo)s"
doacct          =  %(doacct)s
doacl           =  %(doacl)s
dossl           =  %(dossl)s
keyfile         = "%(keyfile)s"
certfile        = "%(certfile)s"
onlyssl         =  %(onlyssl)s
port            =  %(port)d
sslport         =  %(sslport)d
maxsize         =  %(maxsize)d
quota           =  %(quota)d
serverlogfile   = "%(serverlogfile)s"
dropbox         = "%(dropbox)s"
dropboxName     = "%(dropboxName)s"
dropboxACLs     = "%(dropboxACLs)s"
notifications   = "%(notifications)s"
notifcationName = "%(notifcationName)s"
manhole         =  %(manhole)d

from twistedcaldav.repository import startServer

application, site = startServer(docroot,
                                repo,
                                doacct,
                                doacl,
                                dossl,
                                keyfile,
                                certfile,
                                onlyssl,
                                port,
                                sslport,
                                maxsize,
                                quota,
                                serverlogfile,
                                dropbox,
                                dropboxName,
                                dropboxACLs,
                                notifications,
                                notifcationName,
                                manhole)

""" % {
    "docroot":         self.docroot,
    "repo":            self.repo,
    "doacct":          self.doacct,
    "doacl":           self.doacl,
    "dossl":           self.dossl,
    "keyfile":         self.keyfile,
    "certfile":        self.certfile,
    "onlyssl":         self.onlyssl,
    "port":            self.port,
    "sslport":         self.sslport,
    "maxsize":         self.maxsize,
    "quota":           self.quota,
    "serverlogfile":   self.serverlogfile,
    "dropbox":         self.dropbox,
    "dropboxName":     self.dropboxName,
    "dropboxACLs":     self.dropboxACLs,
    "notifications":   self.notifications,
    "notifcationName": self.notifcationName,
    "manhole":         self.manhole,
    
}
