##
# Copyright (c) 2007 Apple Inc. All rights reserved.
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
##

import os
import re
import sys
from subprocess import Popen, PIPE, STDOUT

from twisted.internet import ssl, reactor
from twisted.web import client
from twistedcaldav.log import Logger, LoggingMixIn
from twisted.python import failure
from hashlib import md5, sha1
import base64

##
# getNCPU
##

try:
    from ctypes import *
    import ctypes.util
    hasCtypes = True
except ImportError:
    hasCtypes = False

if sys.platform == "darwin" and hasCtypes:
    libc = cdll.LoadLibrary(ctypes.util.find_library("libc"))

    def getNCPU():
        ncpu = c_int(0)
        size = c_size_t(sizeof(ncpu))

        libc.sysctlbyname.argtypes = [
            c_char_p, c_void_p, c_void_p, c_void_p, c_ulong
        ]
        libc.sysctlbyname(
            "hw.ncpu",
            c_voidp(addressof(ncpu)),
            c_voidp(addressof(size)),
            None,
            0
        )

        return int(ncpu.value)

elif sys.platform == "linux2" and hasCtypes:
    libc = cdll.LoadLibrary(ctypes.util.find_library("libc"))

    def getNCPU():
        return libc.get_nprocs()

else:
    def getNCPU():
        if not hasCtypes:
            msg = " without ctypes"
        else:
            msg = ""

        raise NotImplementedError("getNCPU not supported on %s%s" % (sys.platform, msg))

##
# Module management
##

def submodule(module, name):
    fullname = module.__name__ + "." + name

    try:
        submodule = __import__(fullname)
    except ImportError, e:
        raise ImportError("Unable to import submodule %s from module %s: %s" % (name, module, e))

    for m in fullname.split(".")[1:]:
        submodule = getattr(submodule, m)

    return submodule

##
# Tracebacks
##

from twisted.python.failure import Failure

def printTracebacks(f):
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except:
            Failure().printTraceback()
            raise
    return wrapper

##
# Helpers
##

class Alternator (object):
    """
    Object that alternates between True and False states.
    """
    def __init__(self, state=False):
        self._state = bool(state)

    def state(self):
        """
        @return: the current state
        """
        state = self._state
        self._state = not state
        return state

def utf8String(s):
    if isinstance(s, unicode):
        s = s.encode("utf-8")
    return s

##
# Keychain access
##

class KeychainPasswordNotFound(Exception):
    """
    Exception raised when the password does not exist
    """

class KeychainAccessError(Exception):
    """
    Exception raised when not able to access keychain
    """

passwordRegExp = re.compile(r'password: "(.*)"')

def getPasswordFromKeychain(account):
    if os.path.isfile("/usr/bin/security"):
        child = Popen(
            args=[
                "/usr/bin/security", "find-generic-password",
                "-a", account, "-g",
            ],
            stdout=PIPE, stderr=STDOUT,
        )
        output, error = child.communicate()

        if child.returncode:
            raise KeychainPasswordNotFound(error)
        else:
            match = passwordRegExp.search(output)
            if not match:
                error = "Password for %s not found in keychain" % (account,)
                raise KeychainPasswordNotFound(error)
            else:
                return match.group(1)

    else:
        error = "Keychain access utility ('security') not found"
        raise KeychainAccessError(error)




##
# Digest/Basic-capable HTTP GET factory
##

algorithms = {
    'md5': md5,
    'md5-sess': md5,
    'sha': sha1,
}

# DigestCalcHA1
def calcHA1(
    pszAlg,
    pszUserName,
    pszRealm,
    pszPassword,
    pszNonce,
    pszCNonce,
    preHA1=None
):
    """
    @param pszAlg: The name of the algorithm to use to calculate the digest.
        Currently supported are md5 md5-sess and sha.

    @param pszUserName: The username
    @param pszRealm: The realm
    @param pszPassword: The password
    @param pszNonce: The nonce
    @param pszCNonce: The cnonce

    @param preHA1: If available this is a str containing a previously
       calculated HA1 as a hex string. If this is given then the values for
       pszUserName, pszRealm, and pszPassword are ignored.
    """

    if (preHA1 and (pszUserName or pszRealm or pszPassword)):
        raise TypeError(("preHA1 is incompatible with the pszUserName, "
                         "pszRealm, and pszPassword arguments"))

    if preHA1 is None:
        # We need to calculate the HA1 from the username:realm:password
        m = algorithms[pszAlg]()
        m.update(pszUserName)
        m.update(":")
        m.update(pszRealm)
        m.update(":")
        m.update(pszPassword)
        HA1 = m.digest()
    else:
        # We were given a username:realm:password
        HA1 = preHA1.decode('hex')

    if pszAlg == "md5-sess":
        m = algorithms[pszAlg]()
        m.update(HA1)
        m.update(":")
        m.update(pszNonce)
        m.update(":")
        m.update(pszCNonce)
        HA1 = m.digest()

    return HA1.encode('hex')

# DigestCalcResponse
def calcResponse(
    HA1,
    algo,
    pszNonce,
    pszNonceCount,
    pszCNonce,
    pszQop,
    pszMethod,
    pszDigestUri,
    pszHEntity,
):
    m = algorithms[algo]()
    m.update(pszMethod)
    m.update(":")
    m.update(pszDigestUri)
    if pszQop == "auth-int":
        m.update(":")
        m.update(pszHEntity)
    HA2 = m.digest().encode('hex')

    m = algorithms[algo]()
    m.update(HA1)
    m.update(":")
    m.update(pszNonce)
    m.update(":")
    if pszNonceCount and pszCNonce and pszQop:
        m.update(pszNonceCount)
        m.update(":")
        m.update(pszCNonce)
        m.update(":")
        m.update(pszQop)
        m.update(":")
    m.update(HA2)
    respHash = m.digest().encode('hex')
    return respHash

class Unauthorized(Exception):
    pass

class AuthorizedHTTPGetter(client.HTTPPageGetter, LoggingMixIn):

    def handleStatus_401(self):

        self.quietLoss = 1
        self.transport.loseConnection()

        if not hasattr(self.factory, "username"):
            self.factory.deferred.errback(failure.Failure(Unauthorized("Mail gateway not able to process reply; authentication required for calendar server")))
            return self.factory.deferred

        if hasattr(self.factory, "retried"):
            self.factory.deferred.errback(failure.Failure(Unauthorized("Mail gateway not able to process reply; could not authenticate user %s with calendar server" % (self.factory.username,))))
            return self.factory.deferred

        self.factory.retried = True

        # self.log_debug("Got a 401 trying to inject [%s]" % (self.headers,))
        details = {}
        basicAvailable = digestAvailable = False
        wwwauth = self.headers.get("www-authenticate")
        for item in wwwauth:
            if item.startswith("basic "):
                basicAvailable = True
            if item.startswith("digest "):
                digestAvailable = True
                wwwauth = item[7:]
                def unq(s):
                    if s[0] == s[-1] == '"':
                        return s[1:-1]
                    return s
                parts = wwwauth.split(',')
                for (k, v) in [p.split('=', 1) for p in parts]:
                    details[k.strip()] = unq(v.strip())

        user = self.factory.username
        pswd = self.factory.password

        if digestAvailable and details:
            digest = calcResponse(
                calcHA1(
                    details.get('algorithm'),
                    user,
                    details.get('realm'),
                    pswd,
                    details.get('nonce'),
                    details.get('cnonce')
                ),
                details.get('algorithm'),
                details.get('nonce'),
                details.get('nc'),
                details.get('cnonce'),
                details.get('qop'),
                self.factory.method,
                self.factory.url,
                None
            )

            if details.get('qop'):
                response = (
                    'Digest username="%s", realm="%s", nonce="%s", uri="%s", '
                    'response=%s, algorithm=%s, cnonce="%s", qop=%s, nc=%s' %
                    (
                        user,
                        details.get('realm'),
                        details.get('nonce'),
                        self.factory.url,
                        digest,
                        details.get('algorithm'),
                        details.get('cnonce'),
                        details.get('qop'),
                        details.get('nc'),
                    )
                )
            else:
                response = (
                    'Digest username="%s", realm="%s", nonce="%s", uri="%s", '
                    'response=%s, algorithm=%s' %
                    (
                        user,
                        details.get('realm'),
                        details.get('nonce'),
                        self.factory.url,
                        digest,
                        details.get('algorithm'),
                    )
                )

            self.factory.headers['Authorization'] = response

            if self.factory.scheme == 'https':
                reactor.connectSSL(self.factory.host, self.factory.port,
                    self.factory, ssl.ClientContextFactory())
            else:
                reactor.connectTCP(self.factory.host, self.factory.port,
                    self.factory)
            # self.log_debug("Retrying with digest after 401")

            return self.factory.deferred

        elif basicAvailable:
            basicauth = "%s:%s" % (user, pswd)
            basicauth = "Basic " + base64.encodestring( basicauth )
            basicauth = basicauth.replace( "\n", "" )

            self.factory.headers['Authorization'] = basicauth

            if self.factory.scheme == 'https':
                reactor.connectSSL(self.factory.host, self.factory.port,
                    self.factory, ssl.ClientContextFactory())
            else:
                reactor.connectTCP(self.factory.host, self.factory.port,
                    self.factory)
            # self.log_debug("Retrying with basic after 401")

            return self.factory.deferred


        else:
            self.factory.deferred.errback(failure.Failure(Unauthorized("Mail gateway not able to process reply; calendar server returned 401 and doesn't support basic or digest")))
            return self.factory.deferred

