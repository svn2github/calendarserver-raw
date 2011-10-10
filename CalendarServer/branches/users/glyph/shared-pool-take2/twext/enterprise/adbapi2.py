# -*- test-case-name: twext.enterprise.test.test_adbapi2 -*-
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
Asynchronous multi-process connection pool.

This is similar to L{twisted.enterprise.adbapi}, but can hold a transaction (and
thereby a thread) open across multiple asynchronous operations, rather than
forcing the transaction to be completed entirely in a thread and/or entirely in
a single SQL statement.

Also, this module includes an AMP protocol for multiplexing connections through
a single choke-point host.  This is not currently in use, however, as AMP needs
some optimization before it can be low-impact enough for this to be an
improvement.
"""

import sys

from cStringIO import StringIO
from cPickle import dumps, loads
from itertools import count

from zope.interface import implements

from twisted.internet.defer import inlineCallbacks
from twisted.internet.defer import returnValue
from twisted.internet.defer import DeferredList
from twisted.internet.defer import Deferred
from twisted.protocols.amp import Boolean
from twisted.python.failure import Failure
from twisted.protocols.amp import Argument, String, Command, AMP, Integer
from twisted.internet import reactor as _reactor
from twisted.application.service import Service
from twisted.python import log
from twisted.internet.defer import maybeDeferred
from twisted.python.components import proxyForInterface

from twext.internet.threadutils import ThreadHolder
from twisted.internet.defer import succeed

from twext.enterprise.ienterprise import ConnectionError
from twext.enterprise.ienterprise import IDerivedParameter

from twisted.internet.defer import fail
from twext.enterprise.ienterprise import (
    AlreadyFinishedError, IAsyncTransaction, POSTGRES_DIALECT
)


# FIXME: there should be no defaults for connection metadata, it should be
# discovered dynamically everywhere.  Right now it's specified as an explicit
# argument to the ConnectionPool but it should probably be determined
# automatically from the database binding.

DEFAULT_PARAM_STYLE = 'pyformat'
DEFAULT_DIALECT = POSTGRES_DIALECT


def _forward(thunk):
    """
    Forward an attribute to the connection pool.
    """
    @property
    def getter(self):
        return getattr(self._pool, thunk.func_name)
    return getter



def _deriveParameters(cursor, args):
    """
    Some DB-API extensions need to call special extension methods on
    the cursor itself before executing.

    @param cursor: The DB-API cursor object to derive parameters from.

    @param args: the parameters being specified to C{execSQL}.  This list will
        be modified to present parameters suitable to pass to the C{cursor}'s
        C{execute} method.

    @return: a list of L{IDerivedParameter} providers which had C{preQuery}
        executed on them, so that after the query they may have C{postQuery}
        executed.  This may also be C{None} if no parameters were derived.

    @see: {IDerivedParameter}
    """
    # TODO: have the underlying connection report whether it has any
    # IDerivedParameters that it knows about, so we can skip even inspecting
    # the arguments if none of them could possibly provide
    # IDerivedParameter.
    derived = None
    for n, arg in enumerate(args):
        if IDerivedParameter.providedBy(arg):
            if derived is None:
                # Be as sparing as possible with extra allocations, as this
                # usually isn't needed, and we're doing a ton of extra work to
                # support it.
                derived = []
            derived.append(arg)
            args[n] = arg.preQuery(cursor)
    return derived


def _deriveQueryEnded(cursor, derived):
    """
    A query which involved some L{IDerivedParameter}s just ended.  Execute any
    post-query cleanup or tasks that those parameters have to do.

    @param cursor: The DB-API object that derived the query.

    @param derived: The L{IDerivedParameter} providers that were given
        C{preQuery} notifications when the query started.

    @return: C{None}
    """
    for arg in derived:
        arg.postQuery(cursor)



class _ConnectedTxn(object):
    """
    L{IAsyncTransaction} implementation based on a L{ThreadHolder} in the
    current process.
    """
    implements(IAsyncTransaction)

    def __init__(self, pool, threadHolder, connection, cursor):
        self._pool       = pool
        self._completed  = True
        self._cursor     = cursor
        self._connection = connection
        self._holder     = threadHolder
        self._first      = True


    @_forward
    def paramstyle(self):
        """
        The paramstyle attribute is mirrored from the connection pool.
        """


    @_forward
    def dialect(self):
        """
        The dialect attribute is mirrored from the connection pool.
        """


    def _reallyExecSQL(self, sql, args=None, raiseOnZeroRowCount=None):
        """
        Execute the given SQL on a thread, using a DB-API 2.0 cursor.

        This method is invoked internally on a non-reactor thread, one dedicated
        to and associated with the current cursor.  It executes the given SQL,
        re-connecting first if necessary, re-cycling the old connection if
        necessary, and then, if there are results from the statement (as
        determined by the DB-API 2.0 'description' attribute) it will fetch all
        the rows and return them, leaving them to be relayed to
        L{_ConnectedTxn.execSQL} via the L{ThreadHolder}.

        The rules for possibly reconnecting automatically are: if this is the
        very first statement being executed in this transaction, and an error
        occurs in C{execute}, close the connection and try again.  We will
        ignore any errors from C{close()} (or C{rollback()}) and log them during
        this process.  This is OK because adbapi2 always enforces transaction
        discipline: connections are never in autocommit mode, so if the first
        statement in a transaction fails, nothing can have happened to the
        database; as per the ADBAPI spec, a lost connection is a rolled-back
        transaction.  In the cases where some databases fail to enforce
        transaction atomicity (i.e. schema manipulations), re-executing the same
        statement will result, at worst, in a spurious and harmless error (like
        "table already exists"), not corruption.

        @param sql: The SQL string to execute.

        @type sql: C{str}

        @param args: The bind parameters to pass to adbapi, if any.

        @type args: C{list} or C{None}

        @param raiseOnZeroRowCount: If specified, an exception to raise when no
            rows are found.

        @return: all the rows that resulted from execution of the given C{sql},
            or C{None}, if the statement is one which does not produce results.

        @rtype: C{list} of C{tuple}, or C{NoneType}

        @raise Exception: this function may raise any exception raised by the
            underlying C{dbapi.connect}, C{cursor.execute},
            L{IDerivedParameter.preQuery}, C{connection.cursor}, or
            C{cursor.fetchall}.

        @raise raiseOnZeroRowCount: if the argument was specified and no rows
            were returned by the executed statement.
        """
        wasFirst = self._first
        # If this is the first time this cursor has been used in this
        # transaction, remember that, but mark it as now used.
        self._first = False
        if args is None:
            args = []
        # Note: as of this writing, derived parameters are only used to support
        # cx_Oracle's "host variable" feature (i.e. cursor.var()), and creating
        # a host variable will never be a connection-oriented error (a
        # disconnected cursor can happily create variables of all types).
        # However, this may need to move into the 'try' below if other database
        # features need to compute database arguments based on runtime state.
        derived = _deriveParameters(self._cursor, args)
        try:
            self._cursor.execute(sql, args)
        except:
            # If execute() raised an exception, and this was the first thing to
            # happen in the transaction, then the connection has probably gone
            # bad in the meanwhile, and we should try again.
            if wasFirst:
                # Report the error before doing anything else, since doing other
                # things may cause the traceback stack to be eliminated if they
                # raise exceptions (even internally).
                log.err(
                    Failure(),
                    "Exception from execute() on first statement in "
                    "transaction.  Possibly caused by a database server "
                    "restart.  Automatically reconnecting now."
                )
                try:
                    self._connection.close()
                except:
                    # close() may raise an exception to alert us of an error as
                    # well.  Right now the only type of error we know about is
                    # "the connection is already closed", which obviously
                    # doesn't need to be handled specially. Unfortunately the
                    # reporting of this type of error is not consistent or
                    # predictable across different databases, or even different
                    # bindings to the same database, so we have to do a
                    # catch-all here.  While I can't imagine another type of
                    # error at the moment, bare 'except:'s are notorious for
                    # making debugging surprising error conditions very
                    # difficult, so let's make sure that the error is logged
                    # just in case.
                    log.err(
                        Failure(),
                        "Exception from close() while automatically "
                        "reconnecting. (Probably not serious.)"
                    )

                # Now, if either of *these* things fail, there's an error here
                # that we cannot workaround or address automatically, so no
                # try:except: for them.
                self._connection = self._pool.connectionFactory()
                self._cursor     = self._connection.cursor()

                # Note that although this method is being invoked recursively,
                # the '_first' flag is re-set at the very top, so we will _not_
                # be re-entering it more than once.
                result = self._reallyExecSQL(sql, args, raiseOnZeroRowCount)
                return result
            else:
                raise
        if derived is not None:
            _deriveQueryEnded(self._cursor, derived)
        if raiseOnZeroRowCount is not None and self._cursor.rowcount == 0:
            raise raiseOnZeroRowCount()
        if self._cursor.description:
            return self._cursor.fetchall()
        else:
            return None


    noisy = False

    def execSQL(self, *args, **kw):
        result = self._holder.submit(
            lambda : self._reallyExecSQL(*args, **kw)
        )
        if self.noisy:
            def reportResult(results):
                sys.stdout.write("\n".join([
                    "",
                    "SQL: %r %r" % (args, kw),
                    "Results: %r" % (results,),
                    "",
                    ]))
                return results
            result.addBoth(reportResult)
        return result


    def _end(self, really):
        """
        Common logic for commit or abort.  Executed in the main reactor thread.

        @param really: the callable to execute in the cursor thread to actually
            do the commit or rollback.

        @return: a L{Deferred} which fires when the database logic has
            completed.

        @raise: L{AlreadyFinishedError} if the transaction has already been
            committed or aborted.
        """
        if not self._completed:
            self._completed = True
            def reallySomething():
                """
                Do the database work and set appropriate flags.  Executed in the
                cursor thread.
                """
                if self._cursor is None or self._first:
                    return
                really()
                self._first = True
            result = self._holder.submit(reallySomething)
            self._pool._repoolAfter(self, result)
            return result
        else:
            raise AlreadyFinishedError()


    def commit(self):
        return self._end(self._connection.commit)


    def abort(self):
        return self._end(self._connection.rollback).addErrback(log.err)


    def __del__(self):
        if not self._completed:
            self.abort()


    def reset(self):
        """
        Call this when placing this transaction back into the pool.

        @raise RuntimeError: if the transaction has not been committed or
            aborted.
        """
        if not self._completed:
            raise RuntimeError("Attempt to re-set active transaction.")
        self._completed = False


    def _releaseConnection(self):
        """
        Release the thread and database connection associated with this
        transaction.
        """
        self._completed = True
        self._stopped   = True
        holder          = self._holder
        self._holder    = None

        def _reallyClose():
            if self._cursor is None:
                return
            self._connection.close()
        holder.submit(_reallyClose)
        return holder.stop()



class _NoTxn(object):
    """
    An L{IAsyncTransaction} that indicates a local failure before we could even
    communicate any statements (or possibly even any connection attempts) to the
    server.
    """
    implements(IAsyncTransaction)

    def __init__(self, pool):
        self.paramstyle = pool.paramstyle
        self.dialect = pool.dialect


    def _everything(self, *a, **kw):
        """
        Everything fails with a L{ConnectionError}.
        """
        return fail(ConnectionError())


    execSQL = _everything
    commit  = _everything
    abort   = _everything



class _WaitingTxn(object):
    """
    A L{_WaitingTxn} is an implementation of L{IAsyncTransaction} which cannot
    yet actually execute anything, so it waits and spools SQL requests for later
    execution.  When a L{_ConnectedTxn} becomes available later, it can be
    unspooled onto that.
    """

    implements(IAsyncTransaction)

    def __init__(self, pool):
        """
        Initialize a L{_WaitingTxn} based on a L{ConnectionPool}.  (The C{pool}
        is used only to reflect C{dialect} and C{paramstyle} attributes; not
        remembered or modified in any way.)
        """
        self._spool = []
        self.paramstyle = pool.paramstyle
        self.dialect = pool.dialect


    def _enspool(self, cmd, a=(), kw={}):
        d = Deferred()
        self._spool.append((d, cmd, a, kw))
        return d


    def _iterDestruct(self):
        """
        Iterate the spool list destructively, while popping items from the
        beginning.  This allows code which executes more SQL in the callback of
        a Deferred to not interfere with the originally submitted order of
        commands.
        """
        while self._spool:
            yield self._spool.pop(0)


    def _unspool(self, other):
        """
        Unspool this transaction onto another transaction.

        @param other: another provider of L{IAsyncTransaction} which will
            actually execute the SQL statements we have been buffering.
        """
        for (d, cmd, a, kw) in self._iterDestruct():
            self._relayCommand(other, d, cmd, a, kw)


    def _relayCommand(self, other, d, cmd, a, kw):
        """
        Relay a single command to another transaction.
        """
        maybeDeferred(getattr(other, cmd), *a, **kw).chainDeferred(d)


    def execSQL(self, *a, **kw):
        return self._enspool('execSQL', a, kw)


    def commit(self):
        return self._enspool('commit')


    def abort(self):
        """
        Succeed and do nothing.  The actual logic for this method is mostly
        implemented by L{_SingleTxn._stopWaiting}.
        """
        return succeed(None)



class _SingleTxn(proxyForInterface(iface=IAsyncTransaction,
                                   originalAttribute='_baseTxn')):
    """
    A L{_SingleTxn} is a single-use wrapper for the longer-lived
    L{_ConnectedTxn}, so that if a badly-behaved API client accidentally hangs
    on to one of these and, for example C{.abort()}s it multiple times once
    another client is using that connection, it will get some harmless
    tracebacks.

    It's a wrapper around a "real" implementation; either a L{_ConnectedTxn},
    L{_NoTxn}, or L{_WaitingTxn} depending on the availability of real
    underlying datbase connections.

    This is the only L{IAsyncTransaction} implementation exposed to application
    code.

    It's also the only implementor of the C{commandBlock} method for grouping
    commands together.
    """

    def __init__(self, pool, baseTxn):
        self._pool           = pool
        self._baseTxn        = baseTxn
        self._complete       = False
        self._currentBlock   = None
        self._blockedQueue   = None
        self._pendingBlocks  = []
        self._stillExecuting = []


    def __repr__(self):
        """
        Reveal the backend in the string representation.
        """
        return '_SingleTxn(%r)' % (self._baseTxn,)


    def _unspoolOnto(self, baseTxn):
        """
        Replace my C{_baseTxn}, currently a L{_WaitingTxn}, with a new
        implementation of L{IAsyncTransaction} that will actually do the work;
        either a L{_ConnectedTxn} or a L{_NoTxn}.
        """
        spooledBase   = self._baseTxn
        self._baseTxn = baseTxn
        spooledBase._unspool(baseTxn)


    def execSQL(self, sql, args=None, raiseOnZeroRowCount=None):
        return self._execSQLForBlock(sql, args, raiseOnZeroRowCount, None)


    def _execSQLForBlock(self, sql, args, raiseOnZeroRowCount, block):
        """
        Execute some SQL for a particular L{CommandBlock}; or, if the given
        C{block} is C{None}, execute it in the outermost transaction context.
        """
        self._checkComplete()
        if block is None and self._blockedQueue is not None:
            return self._blockedQueue.execSQL(sql, args, raiseOnZeroRowCount)
        # 'block' should always be _currentBlock at this point.
        d = super(_SingleTxn, self).execSQL(sql, args, raiseOnZeroRowCount)
        self._stillExecuting.append(d)
        def itsDone(result):
            self._stillExecuting.remove(d)
            self._checkNextBlock()
            return result
        d.addBoth(itsDone)
        return d


    def _checkNextBlock(self):
        """
        Check to see if there are any blocks pending statements waiting to
        execute, and execute the next one if there are no outstanding execute
        calls.
        """
        if self._stillExecuting:
            # If we're still executing statements, nevermind.  We'll get called
            # again by the 'itsDone' callback above.
            return

        if self._currentBlock is not None:
            # If there's still a current block, then keep it going.  We'll be
            # called by the '_finishExecuting' callback below.
            return

        # There's no block executing now.  What to do?
        if self._pendingBlocks:
            # If there are pending blocks, start one of them.
            self._currentBlock = self._pendingBlocks.pop(0)
            d = self._currentBlock._startExecuting()
            d.addCallback(self._finishExecuting)
        elif self._blockedQueue is not None:
            # If there aren't any pending blocks any more, and there are spooled
            # statements that aren't part of a block, unspool all the statements
            # that have been held up until this point.
            bq = self._blockedQueue
            self._blockedQueue = None
            bq._unspool(self)


    def _finishExecuting(self, result):
        """
        The active block just finished executing.  Clear it and see if there are
        more blocks to execute, or if all the blocks are done and we should
        execute any queued free statements.
        """
        self._currentBlock = None
        self._checkNextBlock()


    def commit(self):
        if self._blockedQueue is not None:
            # We're in the process of executing a block of commands.  Wait until
            # they're done.  (Commit will be repeated in _checkNextBlock.)
            return self._blockedQueue.commit()

        self._markComplete()
        return super(_SingleTxn, self).commit()


    def abort(self):
        self._markComplete()
        result = super(_SingleTxn, self).abort()
        if self in self._pool._waiting:
            self._stopWaiting()
        return result


    def _stopWaiting(self):
        """
        Stop waiting for a free transaction and fail.
        """
        self._pool._waiting.remove(self)
        self._unspoolOnto(_NoTxn(self._pool))


    def _checkComplete(self):
        """
        If the transaction is complete, raise L{AlreadyFinishedError}
        """
        if self._complete:
            raise AlreadyFinishedError()


    def _markComplete(self):
        """
        Mark the transaction as complete, raising AlreadyFinishedError.
        """
        self._checkComplete()
        self._complete = True


    def commandBlock(self):
        """
        Create a L{CommandBlock} which will wait for all currently spooled
        commands to complete before executing its own.
        """
        self._checkComplete()
        block = CommandBlock(self)
        if self._currentBlock is None:
            self._blockedQueue = _WaitingTxn(self._pool)
            # FIXME: test the case where it's ready immediately.
            self._checkNextBlock()
        return block



class _Unspooler(object):
    def __init__(self, orig):
        self.orig = orig


    def execSQL(self, sql, args=None, raiseOnZeroRowCount=None):
        """
        Execute some SQL, but don't track a new Deferred.
        """
        return self.orig.execSQL(sql, args, raiseOnZeroRowCount, False)



class CommandBlock(object):
    """
    A partial implementation of L{IAsyncTransaction} that will group execSQL
    calls together.

    Does not implement commit() or abort(), because this will simply group
    commands.  In order to implement sub-transactions or checkpoints, some
    understanding of the SQL dialect in use by the underlying connection is
    required.  Instead, it provides 'end'.
    """

    def __init__(self, singleTxn):
        self._singleTxn = singleTxn
        self.paramstyle = singleTxn.paramstyle
        self.dialect = singleTxn.dialect
        self._spool = _WaitingTxn(singleTxn._pool)
        self._started = False
        self._ended = False
        self._waitingForEnd = []
        self._endDeferred = Deferred()
        singleTxn._pendingBlocks.append(self)


    def _startExecuting(self):
        self._started = True
        self._spool._unspool(_Unspooler(self))
        return self._endDeferred


    def execSQL(self, sql, args=None, raiseOnZeroRowCount=None, track=True):
        """
        Execute some SQL within this command block.

        @param sql: the SQL string to execute.

        @param args: the SQL arguments.

        @param raiseOnZeroRowCount: see L{IAsyncTransaction.execSQL}

        @param track: an internal parameter; was this called by application code
            or as part of unspooling some previously-queued requests?  True if
            application code, False if unspooling.
        """
        if track and self._ended:
            raise AlreadyFinishedError()
        self._singleTxn._checkComplete()
        if self._singleTxn._currentBlock is self and self._started:
            d = self._singleTxn._execSQLForBlock(
                sql, args, raiseOnZeroRowCount, self)
        else:
            d = self._spool.execSQL(sql, args, raiseOnZeroRowCount)
        if track:
            self._trackForEnd(d)
        return d


    def _trackForEnd(self, d):
        """
        Watch the following L{Deferred}, since we need to watch it to determine
        when C{end} should be considered done, and the next CommandBlock or
        regular SQL statement should be unqueued.
        """
        self._waitingForEnd.append(d)


    def end(self):
        """
        The block of commands has completed.  Allow other SQL to run on the
        underlying L{IAsyncTransaction}.
        """
        # FIXME: test the case where end() is called when it's not the current
        # executing block.
        if self._ended:
            raise AlreadyFinishedError()
        self._ended = True
        # TODO: maybe this should return a Deferred that's a clone of
        # _endDeferred, so that callers can determine when the block is really
        # complete?  Struggling for an actual use-case on that one.
        DeferredList(self._waitingForEnd).chainDeferred(self._endDeferred)



class _ConnectingPseudoTxn(object):

    _retry = None

    def __init__(self, pool, holder):
        self._pool   = pool
        self._holder = holder


    def abort(self):
        if self._retry is not None:
            self._retry.cancel()
        d = self._holder.stop()
        def removeme(ignored):
            if self in self._pool._busy:
                self._pool._busy.remove(self)
        d.addCallback(removeme)
        return d



def _fork(x):
    """
    Produce a L{Deferred} that will fire when another L{Deferred} fires without
    disturbing its results.
    """
    d = Deferred()
    def fired(result):
        d.callback(result)
        return result

    x.addBoth(fired)
    return d



class ConnectionPool(Service, object):
    """
    This is a central service that has a threadpool and executes SQL statements
    asynchronously, in a pool.

    @ivar connectionFactory: a 0-or-1-argument callable that returns a DB-API
        connection.  The optional argument can be used as a label for
        diagnostic purposes.

    @ivar maxConnections: The connection pool will not attempt to make more
        than this many concurrent connections to the database.

    @type maxConnections: C{int}

    @ivar reactor: The reactor used for scheduling threads as well as retries
        for failed connect() attempts.

    @type reactor: L{IReactorTime} and L{IReactorThreads} provider.

    @ivar _free: The list of free L{_ConnectedTxn} objects which are not
        currently attached to a L{_SingleTxn} object, and have active
        connections ready for processing a new transaction.

    @ivar _busy: The list of busy L{_ConnectedTxn} objects; those currently
        servicing an unfinished L{_SingleTxn} object.

    @ivar _finishing: The list of 2-tuples of L{_ConnectedTxn} objects which
        have had C{abort} or C{commit} called on them, but are not done
        executing that method, and the L{Deferred} returned from that method
        that will be fired when its execution has completed.

    @ivar _waiting: The list of L{_SingleTxn} objects attached to a
        L{_WaitingTxn}; i.e. those which are awaiting a connection to become
        free so that they can be executed.

    @ivar _stopping: Is this L{ConnectionPool} in the process of shutting down?
        (If so, new connections will not be established.)
    """

    reactor = _reactor

    RETRY_TIMEOUT = 10.0


    def __init__(self, connectionFactory, maxConnections=10,
                 paramstyle=DEFAULT_PARAM_STYLE, dialect=DEFAULT_DIALECT):

        super(ConnectionPool, self).__init__()
        self.connectionFactory = connectionFactory
        self.maxConnections = maxConnections
        self.paramstyle = paramstyle
        self.dialect = dialect

        self._free       = []
        self._busy       = []
        self._waiting    = []
        self._finishing  = []
        self._stopping   = False


    def startService(self):
        """
        Increase the thread pool size of the reactor by the number of threads
        that this service may consume.  This is important because unlike most
        L{IReactorThreads} users, the connection work units are very long-lived
        and block until this service has been stopped.
        """
        tp = self.reactor.getThreadPool()
        self.reactor.suggestThreadPoolSize(tp.max + self.maxConnections)


    @inlineCallbacks
    def stopService(self):
        """
        Forcibly abort any outstanding transactions, and release all resources
        (notably, threads).
        """
        self._stopping = True

        # Phase 1: Cancel any transactions that are waiting so they won't try to
        # eagerly acquire new connections as they flow into the free-list.
        while self._waiting:
            waiting = self._waiting[0]
            waiting._stopWaiting()

        # Phase 2: Wait for all the Deferreds from the L{_ConnectedTxn}s that
        # have *already* been stopped.
        while self._finishing:
            yield _fork(self._finishing[0][1])

        # Phase 3: All of the busy transactions must be aborted first.  As each
        # one is aborted, it will remove itself from the list.
        while self._busy:
            yield self._busy[0].abort()

        # Phase 4: All transactions should now be in the free list, since
        # 'abort()' will have put them there.  Shut down all the associated
        # ThreadHolders.
        while self._free:
            # Releasing a L{_ConnectedTxn} doesn't automatically recycle it /
            # remove it the way aborting a _SingleTxn does, so we need to .pop()
            # here.  L{_ConnectedTxn.stop} really shouldn't be able to fail, as
            # it's just stopping the thread, and the holder's stop() is
            # independently submitted from .abort() / .close().
            yield self._free.pop()._releaseConnection()

        tp = self.reactor.getThreadPool()
        self.reactor.suggestThreadPoolSize(tp.max - self.maxConnections)


    def _createHolder(self):
        """
        Create a L{ThreadHolder}.  (Test hook.)
        """
        return ThreadHolder(self.reactor)


    def connection(self, label="<unlabeled>"):
        """
        Find and immediately return an L{IAsyncTransaction} object.  Execution
        of statements, commit and abort on that transaction may be delayed until
        a real underlying database connection is available.

        @return: an L{IAsyncTransaction}
        """
        if self._stopping:
            # FIXME: should be wrapping a _SingleTxn around this to get
            # .commandBlock()
            return _NoTxn(self)
        if self._free:
            basetxn = self._free.pop(0)
            self._busy.append(basetxn)
            txn = _SingleTxn(self, basetxn)
        else:
            txn = _SingleTxn(self, _WaitingTxn(self))
            self._waiting.append(txn)
            # FIXME/TESTME: should be len(self._busy) + len(self._finishing)
            # (free doesn't need to be considered, as it's tested above)
            if self._activeConnectionCount() < self.maxConnections:
                self._startOneMore()
        return txn


    def _activeConnectionCount(self):
        """
        @return: the number of active outgoing connections to the database.
        """
        return len(self._busy) + len(self._finishing)


    def _startOneMore(self):
        """
        Start one more _ConnectedTxn.
        """
        holder = self._createHolder()
        holder.start()
        txn = _ConnectingPseudoTxn(self, holder)
        # take up a slot in the 'busy' list, sit there so we can be aborted.
        self._busy.append(txn)
        def initCursor():
            # support threadlevel=1; we can't necessarily cursor() in a
            # different thread than we do transactions in.
            connection = self.connectionFactory()
            cursor     = connection.cursor()
            return (connection, cursor)
        def finishInit((connection, cursor)):
            baseTxn = _ConnectedTxn(
                pool=self,
                threadHolder=holder,
                connection=connection,
                cursor=cursor
            )
            self._busy.remove(txn)
            self._repoolNow(baseTxn)
        def maybeTryAgain(f):
            log.err(f, "Re-trying connection due to connection failure")
            txn._retry = self.reactor.callLater(self.RETRY_TIMEOUT, resubmit)
        def resubmit():
            d = holder.submit(initCursor)
            d.addCallbacks(finishInit, maybeTryAgain)
        resubmit()


    def _repoolAfter(self, txn, d):
        """
        Re-pool the given L{_ConnectedTxn} after the given L{Deferred} has
        fired.
        """
        self._busy.remove(txn)
        finishRecord = (txn, d)
        self._finishing.append(finishRecord)
        def repool(result):
            self._finishing.remove(finishRecord)
            self._repoolNow(txn)
            return result
        def discard(result):
            self._finishing.remove(finishRecord)
            txn._releaseConnection()
            self._startOneMore()
            return result
        return d.addCallbacks(repool, discard)


    def _repoolNow(self, txn):
        """
        Recycle a L{_ConnectedTxn} into the free list.
        """
        txn.reset()
        if self._waiting:
            waiting = self._waiting.pop(0)
            self._busy.append(txn)
            waiting._unspoolOnto(txn)
        else:
            self._free.append(txn)



def txnarg():
    return [('transactionID', Integer())]


CHUNK_MAX = 0xffff

class BigArgument(Argument):
    """
    An argument whose payload can be larger than L{CHUNK_MAX}, by splitting
    across multiple AMP keys.
    """
    def fromBox(self, name, strings, objects, proto):
        value = StringIO()
        for counter in count():
            chunk = strings.get("%s.%d" % (name, counter))
            if chunk is None:
                break
            value.write(chunk)
        objects[name] = self.fromString(value.getvalue())


    def toBox(self, name, strings, objects, proto):
        value = StringIO(self.toString(objects[name]))
        for counter in count():
            nextChunk = value.read(CHUNK_MAX)
            if not nextChunk:
                break
            strings["%s.%d" % (name, counter)] = nextChunk



class Pickle(BigArgument):
    """
    A pickle sent over AMP.  This is to serialize the 'args' argument to
    C{execSQL}, which is the dynamically-typed 'args' list argument to a DB-API
    C{execute} function, as well as its dynamically-typed result ('rows').

    This should be cleaned up into a nicer structure, but this is not a network
    protocol, so we can be a little relaxed about security.

    This is a L{BigArgument} rather than a regular L{Argument} because
    individual arguments and query results need to contain entire vCard or
    iCalendar documents, which can easily be greater than 64k.
    """

    def toString(self, inObject):
        return dumps(inObject)

    def fromString(self, inString):
        return loads(inString)




class StartTxn(Command):
    """
    Start a transaction, identified with an ID generated by the client.
    """
    arguments = txnarg()



class ExecSQL(Command):
    """
    Execute an SQL statement.
    """
    arguments = [('sql', String()),
                 ('queryID', String()),
                 ('args', Pickle())] + txnarg()



class Row(Command):
    """
    A row has been returned.  Sent from server to client in response to
    L{ExecSQL}.
    """

    arguments = [('queryID', String()),
                 ('row', Pickle())]



class QueryComplete(Command):
    """
    A query issued with L{ExecSQL} is complete.
    """

    arguments = [('queryID', String()),
                 ('norows', Boolean())]



class Commit(Command):
    arguments = txnarg()



class Abort(Command):
    arguments = txnarg()



class _NoRows(Exception):
    """
    Placeholder exception to report zero rows.
    """


class ConnectionPoolConnection(AMP):
    """
    A L{ConnectionPoolConnection} is a single connection to a
    L{ConnectionPool}.
    """

    def __init__(self, pool):
        """
        Initialize a mapping of transaction IDs to transaction objects.
        """
        super(ConnectionPoolConnection, self).__init__()
        self.pool  = pool
        self._txns = {}


    @StartTxn.responder
    def start(self, transactionID):
        self._txns[transactionID] = self.pool.connection()
        return {}


    @ExecSQL.responder
    @inlineCallbacks
    def receivedSQL(self, transactionID, queryID, sql, args):
        try:
            rows = yield self._txns[transactionID].execSQL(sql, args, _NoRows)
        except _NoRows:
            norows = True
        else:
            norows = False
            if rows is not None:
                for row in rows:
                    # Either this should be yielded or it should be
                    # requiresAnswer=False
                    self.callRemote(Row, queryID=queryID, row=row)
        self.callRemote(QueryComplete, queryID=queryID, norows=norows)
        returnValue({})


    def _complete(self, transactionID, thunk):
        txn = self._txns.pop(transactionID)
        return thunk(txn).addCallback(lambda ignored: {})


    @Commit.responder
    def commit(self, transactionID):
        """
        Successfully complete the given transaction.
        """
        return self._complete(transactionID, lambda x: x.commit())


    @Abort.responder
    def abort(self, transactionID):
        """
        Roll back the given transaction.
        """
        return self._complete(transactionID, lambda x: x.abort())



class ConnectionPoolClient(AMP):
    """
    A client which can execute SQL.
    """
    def __init__(self):
        super(ConnectionPoolClient, self).__init__()
        self._nextID  = count().next
        self._txns    = {}
        self._queries = {}


    def newTransaction(self):
        """
        Create a new networked provider of L{IAsyncTransaction}.

        (This will ultimately call L{ConnectionPool.connection} on the other end
        of the wire.)

        @rtype: L{IAsyncTransaction}
        """
        txnid             = str(self._nextID())
        txn               = _NetTransaction(client=self, transactionID=txnid)
        self._txns[txnid] = txn
        self.callRemote(StartTxn, transactionID=txnid)
        return txn


    @Row.responder
    def row(self, queryID, row):
        self._queries[queryID].row(row)
        return {}


    @QueryComplete.responder
    def complete(self, queryID, norows):
        self._queries.pop(queryID).done(norows)
        return {}



class _Query(object):
    def __init__(self, raiseOnZeroRowCount):
        self.results             = []
        self.deferred            = Deferred()
        self.raiseOnZeroRowCount = raiseOnZeroRowCount


    def row(self, row):
        """
        A row was received.
        """
        self.results.append(row)


    def done(self, norows):
        """
        The query is complete.

        @param norows: A boolean.  True if there were not any rows.
        """
        if norows and (self.raiseOnZeroRowCount is not None):
            exc = self.raiseOnZeroRowCount()
            self.deferred.errback(Failure(exc))
        else:
            self.deferred.callback(self.results)



class _NetTransaction(object):
    """
    A L{_NetTransaction} is an L{AMP}-protocol-based provider of the
    L{IAsyncTransaction} interface.  It sends SQL statements, query results, and
    commit/abort commands via an AMP socket to a pooling process.
    """

    implements(IAsyncTransaction)

    # See DEFAULT_PARAM_STYLE FIXME above.
    paramstyle = DEFAULT_PARAM_STYLE

    def __init__(self, client, transactionID):
        """
        Initialize a transaction with a L{ConnectionPoolClient} and a unique
        transaction identifier.
        """
        self._client        = client
        self._transactionID = transactionID
        self._completed     = False


    def execSQL(self, sql, args=None, raiseOnZeroRowCount=None):
        if args is None:
            args = []
        queryID = str(self._client._nextID())
        query = self._client._queries[queryID] = _Query(raiseOnZeroRowCount)
        self._client.callRemote(ExecSQL, queryID=queryID, sql=sql, args=args,
                                transactionID=self._transactionID)
        return query.deferred


    def _complete(self, command):
        if self._completed:
            raise AlreadyFinishedError()
        self._completed = True
        return self._client.callRemote(
            command, transactionID=self._transactionID
            ).addCallback(lambda x: None)


    def commit(self):
        return self._complete(Commit)


    def abort(self):
        return self._complete(Abort)


