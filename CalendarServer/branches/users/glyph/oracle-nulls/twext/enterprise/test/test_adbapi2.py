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
Tests for L{twext.enterprise.adbapi2}.
"""

from itertools import count

from zope.interface.verify import verifyClass

from twisted.python.threadpool import ThreadPool
from twisted.trial.unittest import TestCase

from twisted.internet.defer import execute
from twisted.internet.task import Clock

from twisted.internet.defer import Deferred
from twext.enterprise.ienterprise import ConnectionError
from twext.enterprise.ienterprise import AlreadyFinishedError
from twisted.internet.interfaces import IReactorThreads
from zope.interface.declarations import implements
from twext.enterprise.adbapi2 import ConnectionPool


def resultOf(deferred, propagate=False):
    """
    Add a callback and errback which will capture the result of a L{Deferred} in
    a list, and return that list.  If 'propagate' is True, pass through the
    results.
    """
    results = []
    if propagate:
        def cb(r):
            results.append(r)
            return r
    else:
        cb = results.append
    deferred.addBoth(cb)
    return results



class Child(object):
    """
    An object with a L{Parent}, in its list of C{children}.
    """
    def __init__(self, parent):
        self.closed = False
        self.parent = parent
        self.parent.children.append(self)

    def close(self):
        self.closed = True



class Parent(object):
    """
    An object with a list of L{Child}ren.
    """

    def __init__(self):
        self.children = []



class FakeConnection(Parent, Child):
    """
    Fake Stand-in for DB-API 2.0 connection.

    @ivar executions: the number of statements which have been executed.
    """

    executions = 0

    def __init__(self, factory):
        """
        Initialize list of cursors
        """
        Parent.__init__(self)
        Child.__init__(self, factory)
        self.id = factory.idcounter.next()
        self._executeFailQueue = []


    def executeWillFail(self, thunk):
        """
        The next call to L{FakeCursor.execute} will fail with an exception
        returned from the given callable.
        """
        self._executeFailQueue.append(thunk)


    @property
    def cursors(self):
        "Alias to make tests more readable."
        return self.children


    def cursor(self):
        return FakeCursor(self)


    def commit(self):
        if self.parent.commitFail:
            self.parent.commitFail = False
            raise CommitFail()


    def rollback(self):
        if self.parent.rollbackFail:
            self.parent.rollbackFail = False
            raise RollbackFail()



class RollbackFail(Exception):
    """
    Sample rollback-failure exception.
    """



class CommitFail(Exception):
    """
    Sample Commit-failure exception.
    """



class FakeCursor(Child):
    """
    Fake stand-in for a DB-API 2.0 cursor.
    """
    def __init__(self, connection):
        Child.__init__(self, connection)
        self.rowcount = 0
        # not entirely correct, but all we care about is its truth value.
        self.description = False
        self.variables = []
        self.allExecutions = []


    @property
    def connection(self):
        "Alias to make tests more readable."
        return self.parent


    def execute(self, sql, args=()):
        self.connection.executions += 1
        if self.connection._executeFailQueue:
            raise self.connection._executeFailQueue.pop(0)()
        self.allExecutions.append((sql, args))
        self.sql = sql
        self.description = True
        self.rowcount = 1
        return


    def var(self, type, *args):
        """
        Return a database variable in the style of the cx_Oracle bindings.
        """
        v = FakeVariable(self, type, args)
        self.variables.append(v)
        return v


    def fetchall(self):
        """
        Just echo the SQL that was executed in the last query.
        """
        return [[self.connection.id, self.sql]]



class FakeVariable(object):
    def __init__(self, cursor, type, args):
        self.cursor = cursor
        self.type = type
        self.args = args


    def getvalue(self):
        return self.cursor.variables.index(self) + 300



class ConnectionFactory(Parent):

    rollbackFail = False
    commitFail = False

    def __init__(self):
        Parent.__init__(self)
        self.idcounter = count(1)
        self._connectResultQueue = []
        self.defaultConnect()


    @property
    def connections(self):
        "Alias to make tests more readable."
        return self.children


    def connect(self):
        """
        Implement the C{ConnectionFactory} callable expected by
        L{ConnectionPool}.
        """
        if self._connectResultQueue:
            thunk = self._connectResultQueue.pop(0)
        else:
            thunk = self._default
        return thunk()


    def willConnect(self):
        """
        Used by tests to queue a successful result for connect().
        """
        def thunk():
            return FakeConnection(self)
        self._connectResultQueue.append(thunk)


    def willFail(self):
        """
        Used by tests to queue a successful result for connect().
        """
        def thunk():
            raise FakeConnectionError()
        self._connectResultQueue.append(thunk)


    def defaultConnect(self):
        """
        By default, connection attempts will succeed.
        """
        self.willConnect()
        self._default = self._connectResultQueue.pop()


    def defaultFail(self):
        """
        By default, connection attempts will fail.
        """
        self.willFail()
        self._default = self._connectResultQueue.pop()



class FakeConnectionError(Exception):
    """
    Synthetic error that might occur during connection.
    """



class FakeThreadHolder(object):
    """
    Run things submitted to this ThreadHolder on the main thread, so that
    execution is easier to control.
    """

    def __init__(self, test):
        self.started = False
        self.stopped = False
        self.test = test
        self.queue = []


    def start(self):
        """
        Mark this L{FakeThreadHolder} as not started.
        """
        self.started = True


    def stop(self):
        """
        Mark this L{FakeThreadHolder} as stopped.
        """
        def stopped(nothing):
            self.stopped = True
        return self.submit(lambda : None).addCallback(stopped)


    def submit(self, work):
        """
        Call the function (or queue it)
        """
        if self.test.paused:
            d = Deferred()
            self.queue.append((d, work))
            return d
        else:
            return execute(work)


    def flush(self):
        """
        Fire all deferreds previously returned from submit.
        """
        self.queue, queue = [], self.queue
        for (d, work) in queue:
            try:
                result = work()
            except:
                d.errback()
            else:
                d.callback(result)



class ClockWithThreads(Clock):
    """
    A testing reactor that supplies L{IReactorTime} and L{IReactorThreads}.
    """
    implements(IReactorThreads)

    def __init__(self):
        super(ClockWithThreads, self).__init__()
        self._pool = ThreadPool()


    def getThreadPool(self):
        """
        Get the threadpool.
        """
        return self._pool


    def suggestThreadPoolSize(self, size):
        """
        Approximate the behavior of a 'real' reactor.
        """
        self._pool.adjustPoolsize(maxthreads=size)


    def callInThread(self, thunk, *a, **kw):
        """
        No implementation.
        """


    def callFromThread(self, thunk, *a, **kw):
        """
        No implementation.
        """


verifyClass(IReactorThreads, ClockWithThreads)



class ConnectionPoolBootTests(TestCase):
    """
    Tests for the start-up phase of L{ConnectionPool}.
    """

    def test_threadCount(self):
        """
        The reactor associated with a L{ConnectionPool} will have its maximum
        thread count adjusted when L{ConnectionPool.startService} is called, to
        accomodate for L{ConnectionPool.maxConnections} additional threads.

        Stopping the service should restore it to its original value, so that a
        repeatedly re-started L{ConnectionPool} will not cause the thread
        ceiling to grow without bound.
        """
        defaultMax = 27
        connsMax = 45
        combinedMax = defaultMax + connsMax
        pool = ConnectionPool(None, maxConnections=connsMax)
        pool.reactor = ClockWithThreads()
        threadpool = pool.reactor.getThreadPool()
        pool.reactor.suggestThreadPoolSize(defaultMax)
        self.assertEquals(threadpool.max, defaultMax)
        pool.startService()
        self.assertEquals(threadpool.max, combinedMax)
        justChecking = []
        pool.stopService().addCallback(justChecking.append)
        # No SQL run, so no threads started, so this deferred should fire
        # immediately.  If not, we're in big trouble, so sanity check.
        self.assertEquals(justChecking, [None])
        self.assertEquals(threadpool.max, defaultMax)



class ConnectionPoolTests(TestCase):
    """
    Tests for L{ConnectionPool}.
    """

    def setUp(self):
        """
        Create a L{ConnectionPool} attached to a C{ConnectionFactory}.  Start
        the L{ConnectionPool}.
        """
        self.paused             = False
        self.holders            = []
        self.factory            = ConnectionFactory()
        self.pool               = ConnectionPool(self.factory.connect,
                                                 maxConnections=2)
        self.pool._createHolder = self.makeAHolder
        self.clock              = self.pool.reactor = ClockWithThreads()
        self.pool.startService()


    def tearDown(self):
        """
        Make sure the service is stopped and the fake ThreadHolders are all
        executing their queues so failed tests can exit cleanly.
        """
        self.flushHolders()


    def flushHolders(self):
        """
        Flush all pending C{submit}s since C{pauseHolders} was called.
        """
        self.paused = False
        for holder in self.holders:
            holder.flush()


    def pauseHolders(self):
        """
        Pause all L{FakeThreadHolder}s, causing C{submit} to return an unfired
        L{Deferred}.
        """
        self.paused = True


    def makeAHolder(self):
        """
        Make a ThreadHolder-alike.
        """
        fth = FakeThreadHolder(self)
        self.holders.append(fth)
        return fth


    def test_tooManyConnections(self):
        """
        When the number of outstanding busy transactions exceeds the number of
        slots specified by L{ConnectionPool.maxConnections},
        L{ConnectionPool.connection} will return a pooled transaction that is
        not backed by any real database connection; this object will queue its
        SQL statements until an existing connection becomes available.
        """
        a = self.pool.connection()

        alphaResult = resultOf(a.execSQL("alpha"))
        [[counter, echo]] = alphaResult[0]

        b = self.pool.connection()
        # 'b' should have opened a connection.
        self.assertEquals(len(self.factory.connections), 2)
        betaResult = resultOf(b.execSQL("beta"))
        [[bcounter, becho]] = betaResult[0]

        # both 'a' and 'b' are holding open a connection now; let's try to open
        # a third one.  (The ordering will be deterministic even if this fails,
        # because those threads are already busy.)
        c = self.pool.connection()
        gammaResult = resultOf(c.execSQL("gamma"))

        # Did 'c' open a connection?  Let's hope not...
        self.assertEquals(len(self.factory.connections), 2)
        # SQL shouldn't be executed too soon...
        self.assertEquals(gammaResult, [])

        commitResult = resultOf(b.commit())

        # Now that 'b' has committed, 'c' should be able to complete.
        [[ccounter, cecho]] = gammaResult[0]

        # The connection for 'a' ought to still be busy, so let's make sure
        # we're using the one for 'c'.
        self.assertEquals(ccounter, bcounter)

        # Sanity check: the commit should have succeded!
        self.assertEquals(commitResult, [None])


    def test_stopService(self):
        """
        L{ConnectionPool.stopService} stops all the associated L{ThreadHolder}s
        and thereby frees up the resources it is holding.
        """
        a = self.pool.connection()
        alphaResult = resultOf(a.execSQL("alpha"))
        [[[counter, echo]]] = alphaResult
        self.assertEquals(len(self.factory.connections), 1)
        self.assertEquals(len(self.holders), 1)
        [holder] = self.holders
        self.assertEquals(holder.started, True)
        self.assertEquals(holder.stopped, False)
        self.pool.stopService()
        self.assertEquals(len(self.holders), 1)
        self.assertEquals(holder.started, True)
        self.assertEquals(holder.stopped, True)
        # Closing fake connections removes them from the list.
        self.assertEquals(len(self.factory.connections), 1)
        self.assertEquals(self.factory.connections[0].closed, True)


    def test_retryAfterConnectError(self):
        """
        When the C{connectionFactory} passed to L{ConnectionPool} raises an
        exception, the L{ConnectionPool} will log the exception and delay
        execution of a new connection's SQL methods until an attempt succeeds.
        """
        self.factory.willFail()
        self.factory.willFail()
        self.factory.willConnect()
        c = self.pool.connection()
        def checkOneFailure():
            errors = self.flushLoggedErrors(FakeConnectionError)
            self.assertEquals(len(errors), 1)
        checkOneFailure()
        d = c.execSQL("alpha")
        happened = []
        d.addBoth(happened.append)
        self.assertEquals(happened, [])
        self.clock.advance(self.pool.RETRY_TIMEOUT + 0.01)
        checkOneFailure()
        self.assertEquals(happened, [])
        self.clock.advance(self.pool.RETRY_TIMEOUT + 0.01)
        self.assertEquals(happened, [[[1, "alpha"]]])


    def test_shutdownDuringRetry(self):
        """
        If a L{ConnectionPool} is attempting to shut down while it's in the
        process of re-trying a connection attempt that received an error, the
        connection attempt should be cancelled and the shutdown should complete
        as normal.
        """
        self.factory.defaultFail()
        self.pool.connection()
        errors = self.flushLoggedErrors(FakeConnectionError)
        self.assertEquals(len(errors), 1)
        stopd = []
        self.pool.stopService().addBoth(stopd.append)
        self.assertEquals([None], stopd)
        self.assertEquals(self.clock.calls, [])
        [holder] = self.holders
        self.assertEquals(holder.started, True)
        self.assertEquals(holder.stopped, True)


    def test_shutdownDuringAttemptSuccess(self):
        """
        If L{ConnectionPool.stopService} is called while a connection attempt is
        outstanding, the resulting L{Deferred} won't be fired until the
        connection attempt has finished; in this case, succeeded.
        """
        self.pauseHolders()
        self.pool.connection()
        stopd = []
        self.pool.stopService().addBoth(stopd.append)
        self.assertEquals(stopd, [])
        self.flushHolders()
        self.assertEquals(stopd, [None])
        [holder] = self.holders
        self.assertEquals(holder.started, True)
        self.assertEquals(holder.stopped, True)


    def test_shutdownDuringAttemptFailed(self):
        """
        If L{ConnectionPool.stopService} is called while a connection attempt is
        outstanding, the resulting L{Deferred} won't be fired until the
        connection attempt has finished; in this case, failed.
        """
        self.factory.defaultFail()
        self.pauseHolders()
        self.pool.connection()
        stopd = []
        self.pool.stopService().addBoth(stopd.append)
        self.assertEquals(stopd, [])
        self.flushHolders()
        errors = self.flushLoggedErrors(FakeConnectionError)
        self.assertEquals(len(errors), 1)
        self.assertEquals(stopd, [None])
        [holder] = self.holders
        self.assertEquals(holder.started, True)
        self.assertEquals(holder.stopped, True)


    def test_stopServiceMidAbort(self):
        """
        When L{ConnectionPool.stopService} is called with deferreds from
        C{abort} still outstanding, it will wait for the currently-aborting
        transaction to fully abort before firing the L{Deferred} returned from
        C{stopService}.
        """
        # TODO: commit() too?
        self.pauseHolders()
        c = self.pool.connection()
        abortResult = resultOf(c.abort())
        # Should abort instantly, as it hasn't managed to unspool anything yet.
        # FIXME: kill all Deferreds associated with this thing, make sure that
        # any outstanding query callback chains get nuked.
        self.assertEquals(abortResult, [None])
        stopResult = resultOf(self.pool.stopService())
        self.assertEquals(stopResult, [])
        self.flushHolders()
        #self.assertEquals(abortResult, [None])
        self.assertEquals(stopResult, [None])


    def test_stopServiceWithSpooled(self):
        """
        When L{ConnectionPool.stopService} is called when spooled transactions
        are outstanding, any pending L{Deferreds} returned by those transactions
        will be failed with L{ConnectionError}.
        """
        # Use up the free slots so we have to spool.
        hold = []
        hold.append(self.pool.connection())
        hold.append(self.pool.connection())

        c = self.pool.connection()
        se = resultOf(c.execSQL("alpha"))
        ce = resultOf(c.commit())
        self.assertEquals(se, [])
        self.assertEquals(ce, [])
        self.pool.stopService()
        self.assertEquals(se[0].type, ConnectionError)
        self.assertEquals(ce[0].type, ConnectionError)


    def test_repoolSpooled(self):
        """
        Regression test for a somewhat tricky-to-explain bug: when a spooled
        transaction which has already had commit() called on it before it's
        received a real connection to start executing on, it will not leave
        behind any detritus that prevents stopService from working.
        """
        self.pauseHolders()
        c = self.pool.connection()
        c2 = self.pool.connection()
        c3 = self.pool.connection()
        c.commit()
        c2.commit()
        c3.commit()
        self.flushHolders()
        self.assertEquals(len(self.factory.connections), 2)
        stopResult = resultOf(self.pool.stopService())
        self.assertEquals(stopResult, [None])
        self.assertEquals(len(self.factory.connections), 2)
        self.assertEquals(self.factory.connections[0].closed, True)
        self.assertEquals(self.factory.connections[1].closed, True)


    def test_connectAfterStop(self):
        """
        Calls to connection() after stopService() result in transactions which
        immediately fail all operations.
        """
        stopResults = resultOf(self.pool.stopService())
        self.assertEquals(stopResults, [None])
        self.pauseHolders()
        postClose = self.pool.connection()
        queryResult = resultOf(postClose.execSQL("hello"))
        self.assertEquals(len(queryResult), 1)
        self.assertEquals(queryResult[0].type, ConnectionError)


    def test_connectAfterStartedStopping(self):
        """
        Calls to connection() after stopService() has been called but before it
        has completed will result in transactions which immediately fail all
        operations.
        """
        self.pauseHolders()
        preClose = self.pool.connection()
        preCloseResult = resultOf(preClose.execSQL('statement'))
        stopResult = resultOf(self.pool.stopService())
        postClose = self.pool.connection()
        queryResult = resultOf(postClose.execSQL("hello"))
        self.assertEquals(stopResult, [])
        self.assertEquals(len(queryResult), 1)
        self.assertEquals(queryResult[0].type, ConnectionError)
        self.assertEquals(len(preCloseResult), 1)
        self.assertEquals(preCloseResult[0].type, ConnectionError)


    def test_abortFailsDuringStopService(self):
        """
        L{IAsyncTransaction.abort} might fail, most likely because the
        underlying database connection has already been disconnected.  If this
        happens, shutdown should continue.
        """
        txns = []
        txns.append(self.pool.connection())
        txns.append(self.pool.connection())
        # Fail one (and only one) call to rollback().
        self.factory.rollbackFail = True
        stopResult = resultOf(self.pool.stopService())
        self.assertEquals(stopResult, [None])
        self.assertEquals(len(self.flushLoggedErrors(RollbackFail)), 1)
        self.assertEquals(self.factory.connections[0].closed, True)
        self.assertEquals(self.factory.connections[1].closed, True)


    def test_abortRecycledTransaction(self):
        """
        L{ConnectionPool.stopService} will shut down if a recycled transaction
        is still pending.
        """
        recycled = self.pool.connection()
        recycled.commit()
        remember = []
        remember.append(self.pool.connection())
        self.assertEquals(resultOf(self.pool.stopService()), [None])


    def test_abortSpooled(self):
        """
        Aborting a still-spooled transaction (one which has no statements being
        executed) will result in all of its Deferreds immediately failing and
        none of the queued statements being executed.
        """
        # Use up the available connections ...
        for i in xrange(self.pool.maxConnections):
            self.pool.connection()
        # ... so that this one has to be spooled.
        spooled = self.pool.connection()
        result = resultOf(spooled.execSQL("alpha"))
        # sanity check, it would be bad if this actually executed.
        self.assertEqual(result, [])
        spooled.abort()
        self.assertEqual(result[0].type, ConnectionError)


    def test_waitForAlreadyAbortedTransaction(self):
        """
        L{ConnectionPool.stopService} will wait for all transactions to shut
        down before exiting, including those which have already been stopped.
        """
        it = self.pool.connection()
        self.pauseHolders()
        abortResult = resultOf(it.abort())

        # steal it from the queue so we can do it out of order
        d, work = self.holders[0].queue.pop()
        # that should be the only work unit so don't continue if something else
        # got in there
        self.assertEquals(self.holders[0].queue, [])
        self.assertEquals(len(self.holders), 1)
        self.flushHolders()
        stopResult = resultOf(self.pool.stopService())
        # Sanity check that we haven't actually stopped it yet
        self.assertEquals(abortResult, [])
        # We haven't fired it yet, so the service had better not have stopped...
        self.assertEquals(stopResult, [])
        d.callback(None)
        self.assertEquals(abortResult, [None])
        self.assertEquals(stopResult, [None])


    def test_tooManyConnectionsWhileOthersFinish(self):
        """
        L{ConnectionPool.connection} will not spawn more than the maximum
        connections if there are finishing transactions outstanding.
        """
        a = self.pool.connection()
        b = self.pool.connection()
        self.pauseHolders()
        a.abort()
        b.abort()
        # Remove the holders for the existing connections, so that the 'extra'
        # connection() call wins the race and gets executed first.
        self.holders[:] = []
        self.pool.connection()
        self.flushHolders()
        self.assertEquals(len(self.factory.connections), 2)


    def test_propagateParamstyle(self):
        """
        Each different type of L{IAsyncTransaction} relays the C{paramstyle}
        attribute from the L{ConnectionPool}.
        """
        TEST_PARAMSTYLE = "justtesting"
        self.pool.paramstyle = TEST_PARAMSTYLE
        normaltxn = self.pool.connection()
        self.assertEquals(normaltxn.paramstyle, TEST_PARAMSTYLE)
        self.pauseHolders()
        extra = []
        extra.append(self.pool.connection())
        waitingtxn = self.pool.connection()
        self.assertEquals(waitingtxn.paramstyle, TEST_PARAMSTYLE)
        self.flushHolders()
        self.pool.stopService()
        notxn = self.pool.connection()
        self.assertEquals(notxn.paramstyle, TEST_PARAMSTYLE)


    def test_propagateDialect(self):
        """
        Each different type of L{IAsyncTransaction} relays the C{dialect}
        attribute from the L{ConnectionPool}.
        """
        TEST_DIALECT = "otherdialect"
        self.pool.dialect = TEST_DIALECT
        normaltxn = self.pool.connection()
        self.assertEquals(normaltxn.dialect, TEST_DIALECT)
        self.pauseHolders()
        extra = []
        extra.append(self.pool.connection())
        waitingtxn = self.pool.connection()
        self.assertEquals(waitingtxn.dialect, TEST_DIALECT)
        self.flushHolders()
        self.pool.stopService()
        notxn = self.pool.connection()
        self.assertEquals(notxn.dialect, TEST_DIALECT)


    def test_reConnectWhenFirstExecFails(self):
        """
        Generally speaking, DB-API 2.0 adapters do not provide information about
        the cause of a failed 'execute' method; they definitely don't provide it
        in a way which can be identified as related to the syntax of the query,
        the state of the database itself, the state of the connection, etc.

        Therefore the best general heuristic for whether the connection to the
        database has been lost and needs to be re-established is to catch
        exceptions which are raised by the I{first} statement executed in a
        transaction.
        """
        # Allow 'connect' to succeed.  This should behave basically the same
        # whether connect() happened to succeed in some previous transaction and
        # it's recycling the underlying transaction, or connect() just
        # succeeded.  Either way you just have a _SingleTxn wrapping a
        # _ConnectedTxn.
        txn = self.pool.connection()
        self.assertEquals(len(self.factory.connections), 1,
                          "Sanity check failed.")
        self.factory.connections[0].executeWillFail(RuntimeError)
        results = resultOf(txn.execSQL("hello, world!"))
        [[[counter, echo]]] = results
        self.assertEquals("hello, world!", echo)
        # Two execution attempts should have been made, one on each connection.
        # The first failed with a RuntimeError, but that is deliberately
        # obscured, because then we tried again and it succeeded.
        self.assertEquals(len(self.factory.connections), 2,
                          "No new connection opened.")
        self.assertEquals(self.factory.connections[0].executions, 1)
        self.assertEquals(self.factory.connections[1].executions, 1)
        self.assertEquals(self.factory.connections[0].closed, True)
        self.assertEquals(self.factory.connections[1].closed, False)


    def test_reConnectWhenSecondExecFailsThenFirstExecFails(self):
        """
        Other connection-oriented errors might raise exceptions if they occur in
        the middle of a transaction, but that should cause the error to be
        caught, the transaction to be aborted, and the (closed) connection to be
        recycled, where the next transaction that attempts to do anything with
        it will encounter the error immediately and discover it needs to be
        recycled.

        It would be better if this behavior were invisible, but that could only
        be accomplished with more precise database exceptions.  We may come up
        with support in the future for more precisely identifying exceptions,
        but I{unknown} exceptions should continue to be treated in this manner,
        relaying the exception back to application code but attempting a
        re-connection on the next try.
        """
        txn = self.pool.connection()
        [[[counter, echo]]] = resultOf(txn.execSQL("hello, world!", []))
        self.factory.connections[0].executeWillFail(ZeroDivisionError)
        [f] = resultOf(txn.execSQL("divide by zero", []))
        f.trap(ZeroDivisionError)
        self.assertEquals(self.factory.connections[0].executions, 2)
        # Reconnection should work exactly as before.
        self.assertEquals(self.factory.connections[0].closed, False)
        # Application code has to roll back its transaction at this point, since
        # it failed (and we don't necessarily know why it failed: not enough
        # information).
        txn.abort()
        self.factory.connections[0].executions = 0 # re-set for next test
        self.assertEquals(len(self.factory.connections), 1)
        self.test_reConnectWhenFirstExecFails()


    def test_disconnectOnFailedRollback(self):
        """
        When C{rollback} fails for any reason on a connection object, then we
        don't know what state it's in.  Most likely, it's already been
        disconnected, so the connection should be closed and the transaction
        de-pooled instead of recycled.

        Also, a new connection will immediately be established to keep the pool
        size the same.
        """
        txn = self.pool.connection()
        self.factory.rollbackFail = True
        [x] = resultOf(txn.abort())
        # Abort does not propagate the error on, the transaction merely gets
        # disposed of.
        self.assertIdentical(x, None)
        self.assertEquals(len(self.pool._free), 1)
        self.assertNotIn(txn._baseTxn, self.pool._free)
        self.assertEquals(self.pool._finishing, [])
        self.assertEquals(len(self.factory.connections), 2)
        self.assertEquals(self.factory.connections[0].closed, True)
        self.assertEquals(self.factory.connections[1].closed, False)
        self.assertEquals(len(self.flushLoggedErrors(RollbackFail)), 1)


    def test_exceptionPropagatesFailedCommit(self):
        """
        A failed C{rollback} is fine (the premature death of the connection
        without C{commit} means that the changes are surely gone), but a failed
        C{commit} has to be relayed to client code, since that actually means
        some changes didn't hit the database.
        """
        txn = self.pool.connection()
        self.factory.commitFail = True
        [x] = resultOf(txn.commit())
        x.trap(CommitFail)
        self.assertEquals(len(self.pool._free), 1)
        self.assertNotIn(txn._baseTxn, self.pool._free)
        self.assertEquals(self.pool._finishing, [])
        self.assertEquals(len(self.factory.connections), 2)
        self.assertEquals(self.factory.connections[0].closed, True)
        self.assertEquals(self.factory.connections[1].closed, False)


    def test_commandBlock(self):
        """
        L{IAsyncTransaction.commandBlock} returns an L{IAsyncTransaction}
        provider which ensures that a block of commands are executed together.
        """
        txn = self.pool.connection()
        a = resultOf(txn.execSQL("a"))
        cb = txn.commandBlock()
        b = resultOf(cb.execSQL("b"))
        d = resultOf(txn.execSQL("d"))
        c = resultOf(cb.execSQL("c"))
        cb.end()
        e = resultOf(txn.execSQL("e"))
        self.assertEquals(self.factory.connections[0].cursors[0].allExecutions,
                          [("a", []), ("b", []), ("c", []), ("d", []),
                           ("e", [])])
        self.assertEquals(len(a), 1)
        self.assertEquals(len(b), 1)
        self.assertEquals(len(c), 1)
        self.assertEquals(len(d), 1)
        self.assertEquals(len(e), 1)


    def test_commandBlockWithLatency(self):
        """
        A block returned by L{IAsyncTransaction.commandBlock} won't start
        executing until all SQL statements scheduled before it have completed.
        """
        self.pauseHolders()
        txn = self.pool.connection()
        a = resultOf(txn.execSQL("a"))
        b = resultOf(txn.execSQL("b"))
        cb = txn.commandBlock()
        c = resultOf(cb.execSQL("c"))
        d = resultOf(cb.execSQL("d"))
        e = resultOf(txn.execSQL("e"))
        cb.end()
        self.flushHolders()

        self.assertEquals(self.factory.connections[0].cursors[0].allExecutions,
                          [("a", []), ("b", []), ("c", []), ("d", []),
                           ("e", [])])

        self.assertEquals(len(a), 1)
        self.assertEquals(len(b), 1)
        self.assertEquals(len(c), 1)
        self.assertEquals(len(d), 1)
        self.assertEquals(len(e), 1)


    def test_twoCommandBlocks(self, flush=lambda : None):
        """
        When execution of one command block is complete, it will proceed to the
        next queued block, then to regular SQL executed on the transaction.
        """
        txn = self.pool.connection()
        cb1 = txn.commandBlock()
        cb2 = txn.commandBlock()
        txn.execSQL("e")
        cb1.execSQL("a")
        cb2.execSQL("c")
        cb1.execSQL("b")
        cb2.execSQL("d")
        cb2.end()
        cb1.end()
        flush()
        self.assertEquals(self.factory.connections[0].cursors[0].allExecutions,
                          [("a", []), ("b", []), ("c", []), ("d", []),
                           ("e", [])])


    def test_twoCommandBlocksLatently(self):
        """
        Same as L{test_twoCommandBlocks}, but with slower callbacks.
        """
        self.pauseHolders()
        self.test_twoCommandBlocks(self.flushHolders)


    def test_commandBlockEndTwice(self):
        """
        L{CommandBlock.end} will raise L{AlreadyFinishedError} when called more
        than once.
        """
        txn = self.pool.connection()
        block = txn.commandBlock()
        block.end()
        self.assertRaises(AlreadyFinishedError, block.end)


    def test_commandBlockDelaysCommit(self):
        """
        Some command blocks need to run asynchronously, without the overall
        transaction-managing code knowing how far they've progressed.  Therefore
        when you call {IAsyncTransaction.commit}(), it should not actually take
        effect if there are any pending command blocks.
        """
        txn = self.pool.connection()
        block = txn.commandBlock()
        commitResult = resultOf(txn.commit())
        block.execSQL("in block")
        self.assertEquals(commitResult, [])
        self.assertEquals(self.factory.connections[0].cursors[0].allExecutions,
                          [("in block", [])])
        block.end()
        self.assertEquals(commitResult, [None])


    def test_commandBlockDoesntDelayAbort(self):
        """
        A L{CommandBlock} can't possibly have anything interesting to say about
        a transaction that gets rolled back, so C{abort} applies immediately;
        all outstanding C{execSQL}s will fail immediately, on both command
        blocks and on the transaction itself.
        """
        txn = self.pool.connection()
        block = txn.commandBlock()
        block2 = txn.commandBlock()
        abortResult = resultOf(txn.abort())
        self.assertEquals(abortResult, [None])
        self.assertRaises(AlreadyFinishedError, block2.execSQL, "bar")
        self.assertRaises(AlreadyFinishedError, block.execSQL, "foo")
        self.assertRaises(AlreadyFinishedError, txn.execSQL, "baz")
        self.assertEquals(self.factory.connections[0].cursors[0].allExecutions,
                          [])
        # end() should _not_ raise an exception, because this is the sort of
        # thing that might be around a try/finally or try/except; it's just
        # putting the commandBlock itself into a state consistent with the
        # transaction.
        block.end()
        block2.end()


    def test_endedBlockDoesntExecuteMoreSQL(self):
        """
        Attempting to execute SQL on a L{CommandBlock} which has had C{end}
        called on it will result in an L{AlreadyFinishedError}.
        """
        txn = self.pool.connection()
        block = txn.commandBlock()
        block.end()
        self.assertRaises(AlreadyFinishedError, block.execSQL, "hello")
        self.assertEquals(self.factory.connections[0].cursors[0].allExecutions,
                          [])


    def test_commandBlockAfterCommitRaises(self):
        """
        Once an L{IAsyncTransaction} has been committed, L{commandBlock} raises
        an exception.
        """
        txn = self.pool.connection()
        txn.commit()
        self.assertRaises(AlreadyFinishedError, txn.commandBlock)


    def test_commandBlockAfterAbortRaises(self):
        """
        Once an L{IAsyncTransaction} has been committed, L{commandBlock} raises
        an exception.
        """
        txn = self.pool.connection()
        txn.abort()
        self.assertRaises(AlreadyFinishedError, txn.commandBlock)



