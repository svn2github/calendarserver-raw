# -*- test-case-name: txdav.base.datastore.test.test_subpostgres -*-
##
# Copyright (c) 2010 Apple Inc. All rights reserved.
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

"""
Run and manage PostgreSQL as a subprocess.
"""

import os
import pwd

from hashlib import md5

from twisted.python.procutils import which
from twisted.internet.protocol import ProcessProtocol

from twisted.python.reflect import namedAny
from twext.python.log import Logger
from twext.python.filepath import CachingFilePath

pgdb = namedAny("pgdb")

from twisted.protocols.basic import LineReceiver
from twisted.internet import reactor
from twisted.internet.defer import Deferred
from txdav.base.datastore.asyncsqlpool import BaseSqlTxn

from twisted.application.service import MultiService

log = Logger()

# This appears in the postgres log to indicate that it is accepting
# connections.
_MAGIC_READY_COOKIE = "database system is ready to accept connections"


class DiagnosticCursorWrapper(object):
    """
    Diagnostic wrapper around a DB-API 2.0 cursor for debugging connection
    status.
    """

    def __init__(self, realCursor, connectionWrapper):
        self.realCursor = realCursor
        self.connectionWrapper = connectionWrapper


    @property
    def rowcount(self):
        return self.realCursor.rowcount


    @property
    def description(self):
        return self.realCursor.description


    def execute(self, sql, args=()):
        self.connectionWrapper.state = 'executing %r' % (sql,)
# Use log.debug
#        sys.stdout.write(
#            "Really executing SQL %r in thread %r\n" %
#            ((sql % tuple(args)), thread.get_ident())
#        )
        self.realCursor.execute(sql, args)


    def close(self):
        self.realCursor.close()


    def fetchall(self):
        results = self.realCursor.fetchall()
# Use log.debug
#        sys.stdout.write(
#            "Really fetching results %r thread %r\n" %
#            (results, thread.get_ident())
#        )
        return results



class DiagnosticConnectionWrapper(object):
    """
    Diagnostic wrapper around a DB-API 2.0 connection for debugging connection
    status.
    """

    def __init__(self, realConnection, label):
        self.realConnection = realConnection
        self.label = label
        self.state = 'idle (start)'


    def cursor(self):
        return DiagnosticCursorWrapper(self.realConnection.cursor(), self)


    def close(self):
        self.realConnection.close()
        self.state = 'closed'


    def commit(self):
        self.realConnection.commit()
        self.state = 'idle (after commit)'


    def rollback(self):
        self.realConnection.rollback()
        self.state = 'idle (after rollback)'



class _PostgresMonitor(ProcessProtocol):
    """
    A monitoring protocol which watches the postgres subprocess.
    """

    def __init__(self, svc=None):
        self.lineReceiver = LineReceiver()
        self.lineReceiver.delimiter = '\n'
        self.lineReceiver.lineReceived = self.lineReceived
        self.svc = svc
        self.isReady = False
        self.completionDeferred = Deferred()


    def lineReceived(self, line):
        if self.svc is None:
            return
        if not self.isReady:
            if _MAGIC_READY_COOKIE in line:
                self.svc.ready()


    disconnecting = False
    def connectionMade(self):
        self.lineReceiver.makeConnection(self)


    def outReceived(self, out):
        log.msg("received postgres stdout %r" % (out,))
        # self.lineReceiver.dataReceived(out)


    def errReceived(self, err):
        log.msg("received postgres stderr %r" % (err,))
        self.lineReceiver.dataReceived(err)


    def processEnded(self, reason):
        log.msg("postgres process ended %r" % (reason,))
        self.lineReceiver.connectionLost(reason)
        self.completionDeferred.callback(None)



class ErrorOutput(Exception):
    """
    The process produced some error output and exited with a non-zero exit
    code.
    """



class CapturingProcessProtocol(ProcessProtocol):
    """
    A L{ProcessProtocol} that captures its output and error.

    @ivar output: a C{list} of all C{str}s received to stderr.

    @ivar error: a C{list} of all C{str}s received to stderr.
    """

    def __init__(self, deferred, inputData):
        """
        Initialize a L{CapturingProcessProtocol}.

        @param deferred: the L{Deferred} to fire when the process is complete.

        @param inputData: a C{str} to feed to the subprocess's stdin.
        """
        self.deferred = deferred
        self.input = inputData
        self.output = []
        self.error = []


    def connectionMade(self):
        """
        The process started; feed its input on stdin.
        """
        if self.input is not None:
            self.transport.write(self.input)
            self.transport.closeStdin()


    def outReceived(self, data):
        """
        Some output was received on stdout.
        """
        self.output.append(data)


    def errReceived(self, data):
        """
        Some output was received on stderr.
        """
        self.output.append(data)


    def processEnded(self, why):
        """
        The process is over, fire the Deferred with the output.
        """
        self.deferred.callback(''.join(self.output))



class UnpooledSqlTxn(BaseSqlTxn):
    """
    Unpooled variant (releases thread immediately on commit or abort),
    currently exclusively for testing.
    """
    def commit(self):
        result = super(UnpooledSqlTxn, self).commit()
        self.stop()
        return result

    def abort(self):
        result = super(UnpooledSqlTxn, self).abort()
        self.stop()
        return result



class PostgresService(MultiService):

    def __init__(self, dataStoreDirectory, subServiceFactory,
                 schema, resetSchema=False, databaseName='subpostgres',
                 logFile="postgres.log", socketDir="/tmp",
                 listenAddresses=[], sharedBuffers=30,
                 maxConnections=20, options=[],
                 testMode=False,
                 uid=None, gid=None):
        """
        Initialize a L{PostgresService} pointed at a data store directory.

        @param dataStoreDirectory: the directory to
        @type dataStoreDirectory: L{twext.python.filepath.CachingFilePath}

        @param subServiceFactory: a 1-arg callable that will be called with a
            1-arg callable which returns a DB-API cursor.
        @type subServiceFactory: C{callable}
        """

        # FIXME: By default there is very little (4MB) shared memory available,
        # so at the moment I am lowering these postgres config options to allow
        # multiple servers to run.  We might want to look into raising
        # kern.sysv.shmmax.
        # See: http://www.postgresql.org/docs/8.4/static/kernel-resources.html

        MultiService.__init__(self)
        self.subServiceFactory = subServiceFactory
        self.dataStoreDirectory = dataStoreDirectory
        self.resetSchema = resetSchema

        # In order to delay a shutdown until database initialization has
        # completed, our stopService( ) examines the delayedShutdown flag.
        # If True, we wait on the shutdownDeferred to fire before proceeding.
        # The deferred gets fired once database init is complete.
        self.delayedShutdown = False # set to True when in critical code
        self.shutdownDeferred = None # the actual deferred

        # Options from config
        self.databaseName = databaseName
        self.logFile = logFile
        if socketDir:
            # Unix socket length path limit
            self.socketDir = CachingFilePath("%s/ccs_postgres_%s/" %
                (socketDir, md5(dataStoreDirectory.path).hexdigest()))
            if len(self.socketDir.path) > 64:
                socketDir = "/tmp"
                self.socketDir = CachingFilePath("/tmp/ccs_postgres_%s/" %
                    (md5(dataStoreDirectory.path).hexdigest()))
            self.host = self.socketDir.path
        else:
            self.socketDir = None
            self.host = "localhost"
        self.listenAddresses = listenAddresses
        self.sharedBuffers = sharedBuffers if not testMode else 16
        self.maxConnections = maxConnections if not testMode else 4
        self.options = options

        self.uid = uid
        self.gid = gid
        self.schema = schema
        self.monitor = None
        self.openConnections = []


    def activateDelayedShutdown(self):
        """
        Call this when starting database initialization code to protect against
        shutdown.

        Sets the delayedShutdown flag to True so that if reactor shutdown
        commences, the shutdown will be delayed until deactivateDelayedShutdown
        is called.
        """
        self.delayedShutdown = True


    def deactivateDelayedShutdown(self):
        """
        Call this when database initialization code has completed so that the
        reactor can shutdown.
        """
        self.delayedShutdown = False
        if self.shutdownDeferred:
            self.shutdownDeferred.callback(None)


    def produceConnection(self, label="<unlabeled>", databaseName=None):
        """
        Produce a DB-API 2.0 connection pointed at this database.
        """
        if databaseName is None:
            databaseName = self.databaseName

        if self.uid is not None:
            dsn = "%s:dbname=%s:%s" % (self.host, databaseName,
                pwd.getpwuid(self.uid).pw_name)
        else:
            dsn = "%s:dbname=%s" % (self.host, databaseName)
        connection = pgdb.connect(dsn)

        w = DiagnosticConnectionWrapper(connection, label)
        c = w.cursor()

        # Turn on standard conforming strings.  This option is _required_ if
        # you want to get correct behavior out of parameter-passing with the
        # pgdb module.  If it is not set then the server is potentially
        # vulnerable to certain types of SQL injection.
        c.execute("set standard_conforming_strings=on")

        # Abort any second that takes more than 30 seconds (30000ms) to
        # execute. This is necessary as a temporary workaround since it's
        # hypothetically possible that different database operations could
        # block each other, while executing SQL in the same process (in the
        # same thread, since SQL executes in the main thread now).  It's
        # preferable to see some exceptions while we're in this state than to
        # have the entire worker process hang.
        c.execute("set statement_timeout=30000")

        # pgdb (as per DB-API 2.0) automatically puts the connection into a
        # 'executing a transaction' state when _any_ statement is executed on
        # it (even these not-touching-any-data statements); make sure to commit
        # first so that the application sees a fresh transaction, and the
        # connection can safely be pooled without executing anything on it.
        w.commit()
        c.close()
        return w


    def produceLocalTransaction(self, label="<unlabeled>"):
        """
        Create a L{IAsyncTransaction} based on a thread in the current process.
        """
        return UnpooledSqlTxn(lambda : self.produceConnection(label))


    def ready(self):
        """
        Subprocess is ready.  Time to initialize the subservice.
        """
        createDatabaseConn = self.produceConnection(
            'schema creation', 'postgres'
        )
        createDatabaseCursor = createDatabaseConn.cursor()
        createDatabaseCursor.execute("commit")

        if self.resetSchema:
            try:
                createDatabaseCursor.execute(
                    "drop database %s" % (self.databaseName)
                )
            except pgdb.DatabaseError:
                pass

        try:
            createDatabaseCursor.execute(
                "create database %s with encoding 'UTF8'" % (self.databaseName)
            )
        except:
            execSchema = False
        else:
            execSchema = True

        createDatabaseCursor.close()
        createDatabaseConn.close()

        if execSchema:
            connection = self.produceConnection()
            cursor = connection.cursor()
            cursor.execute(self.schema)
            connection.commit()
            connection.close()

        connection = self.produceConnection()
        cursor = connection.cursor()

        if self.shutdownDeferred is None:
            # Only continue startup if we've not begun shutdown
            self.subServiceFactory(self.produceConnection).setServiceParent(self)


    def pauseMonitor(self):
        """
        Pause monitoring.  This is a testing hook for when (if) we are
        continuously monitoring output from the 'postgres' process.
        """
#        for pipe in self.monitor.transport.pipes.values():
#            pipe.stopReading()
#            pipe.stopWriting()


    def unpauseMonitor(self):
        """
        Unpause monitoring.

        @see: L{pauseMonitor} 
        """
#        for pipe in self.monitor.transport.pipes.values():
#            pipe.startReading()
#            pipe.startWriting()


    def startDatabase(self):
        """
        Start the database and initialize the subservice.
        """
        monitor = _PostgresMonitor(self)
        pg_ctl = which("pg_ctl")[0]
        # check consistency of initdb and postgres?

        options = []
        options.append(
            "-c listen_addresses='%s'" % (",".join(self.listenAddresses))
        )
        if self.socketDir:
            options.append("-k '%s'" % (self.socketDir.path,))
        options.append("-c shared_buffers=%d" % (self.sharedBuffers,))
        options.append("-c max_connections=%d" % (self.maxConnections,))
        options.append("-c standard_conforming_strings=on")
        options.extend(self.options)

        reactor.spawnProcess(
            monitor, pg_ctl,
            [
                pg_ctl,
                "start",
                "-l", self.logFile,
                "-w",
                # XXX what are the quoting rules for '-o'?  do I need to repr()
                # the path here?
                "-o",
                " ".join(options),
            ],
            self.env,
            uid=self.uid, gid=self.gid,
        )
        self.monitor = monitor
        def gotReady(result):
            self.ready()
            self.deactivateDelayedShutdown()
        def reportit(f):
            log.err(f)
            self.deactivateDelayedShutdown()
        self.monitor.completionDeferred.addCallback(
            gotReady).addErrback(reportit)


    def startService(self):
        MultiService.startService(self)
        self.activateDelayedShutdown()
        clusterDir = self.dataStoreDirectory.child("cluster")
        workingDir = self.dataStoreDirectory.child("working")
        env = self.env = os.environ.copy()
        env.update(PGDATA=clusterDir.path,
                   PGHOST=self.host)
        initdb = which("initdb")[0]
        if self.socketDir:
            if not self.socketDir.isdir():
                self.socketDir.createDirectory()
            if self.uid and self.gid:
                os.chown(self.socketDir.path, self.uid, self.gid)
        if clusterDir.isdir():
            self.startDatabase()
        else:
            if not self.dataStoreDirectory.isdir():
                self.dataStoreDirectory.createDirectory()
            if not workingDir.isdir():
                workingDir.createDirectory()
            if self.uid and self.gid:
                os.chown(self.dataStoreDirectory.path, self.uid, self.gid)
                os.chown(workingDir.path, self.uid, self.gid)
            dbInited = Deferred()
            reactor.spawnProcess(
                CapturingProcessProtocol(dbInited, None),
                initdb, [initdb, "-E", "UTF8"], env, workingDir.path,
                uid=self.uid, gid=self.gid,
            )
            def doCreate(result):
                self.startDatabase()
            dbInited.addCallback(doCreate)


    def stopService(self):
        """
        Stop all child services, then stop the subprocess, if it's running.
        """

        if self.delayedShutdown:
            # We're still in the process of initializing the database, so
            # delay shutdown until the shutdownDeferred fires.
            d = self.shutdownDeferred = Deferred()
            d.addCallback(lambda ignored: MultiService.stopService(self))
        else:
            d = MultiService.stopService(self)

        def superStopped(result):
            monitor = _PostgresMonitor()
            pg_ctl = which("pg_ctl")[0]
            reactor.spawnProcess(monitor, pg_ctl,
                [pg_ctl, '-l', 'logfile', 'stop'],
                self.env,
                uid=self.uid, gid=self.gid,
            )
            return monitor.completionDeferred
        return d.addCallback(superStopped)

#        def maybeStopSubprocess(result):
#            if self.monitor is not None:
#                self.monitor.transport.signalProcess("INT")
#                return self.monitor.completionDeferred
#            return result
#        d.addCallback(maybeStopSubprocess)
#        return d
