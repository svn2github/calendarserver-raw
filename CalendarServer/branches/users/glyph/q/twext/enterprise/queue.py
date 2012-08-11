
"""
This is the logic associated with queueing.
"""

from socket import getfqdn
from functools import wraps
from os import getpid
from datetime import datetime

from twisted.internet.task import LoopingCall
from twisted.application.service import Service
from twisted.internet.protocol import Factory
from twisted.internet.defer import inlineCallbacks, returnValue, Deferred
from twisted.internet.defer import succeed
from twisted.internet.task import deferLater
from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.protocols.amp import AMP, Command, Integer, Argument
from twisted.python.reflect import qual

from twext.enterprise.dal.syntax import TableSyntax, SchemaSyntax
from twext.enterprise.dal.model import ProcedureCall
from twext.enterprise.dal.syntax import NamedValue
from twext.enterprise.dal.record import fromTable
from twisted.python.failure import Failure
from twisted.internet.defer import passthru
from twext.enterprise.dal.model import Table, Schema, SQLType, Constraint


def makeNodeSchema(inSchema):
    """
    Create a self-contained schema for L{NodeInfo} to use.

    @return: a schema with just the one table.
    """
    # Initializing this duplicate schema avoids a circular dependency, but this
    # should really be accomplished with independent schema objects that the
    # transaction is made aware of somehow.
    NodeTable = Table(inSchema, 'NODE_INFO')
    NodeTable.addColumn("HOSTNAME", SQLType("varchar", 255))
    NodeTable.addColumn("PID", SQLType("integer", None))
    NodeTable.addColumn("PORT", SQLType("integer", None))
    NodeTable.addColumn("TIME", SQLType("timestamp", None)).setDefaultValue(
        # Note: in the real data structure, this is actually a not-cleaned-up
        # sqlparse internal data structure, but it *should* look closer to this.
        ProcedureCall("timezone", ["UTC", NamedValue('CURRENT_TIMESTAMP')])
    )
    for column in NodeTable.columns:
        NodeTable.tableConstraint(Constraint.NOT_NULL, [column.name])
    return inSchema

NodeInfoSchema = SchemaSyntax(makeNodeSchema(Schema(__file__)))



class TableSyntaxByName(Argument):
    """
    Serialize and deserialize L{TableSyntax} objects for an AMP protocol with
    an attached schema.
    """

    def fromStringProto(self, inString, proto):
        """
        Convert the name of the table into a table, given a C{proto} with an
        attached C{schema}.

        @param inString: the name of a table, as utf-8 encoded bytes
        @type inString: L{bytes}

        @param proto: an L{SchemaAMP}
        """
        return TableSyntax(proto.schema.tableNamed(inString.decode("UTF-8")))


    def toString(self, inObject):
        """
        Convert a L{TableSyntax} object into just its name for wire transport.

        @param inObject: a table.
        @type inObject: L{TableSyntax}

        @return: the name of that table
        @rtype: L{bytes}
        """
        return inObject.model.name.encode("UTF-8")



class NodeInfo(fromTable(NodeInfoSchema.NODE_INFO)):
    """
    A L{NodeInfo} is information about a currently-active Node process.
    """

    def endpoint(self):
        """
        Create an L{IStreamServerEndpoint} that will talk to the node process
        that is described by this L{NodeInfo}.

        @return: an endpoint that will connect to this host.
        @rtype: L{IStreamServerEndpoint}
        """
        return TCP4ClientEndpoint(self.hostname, self.port)


    def updateCurrent(self):
        return self.update(timestamp=datetime.datetime.now())



def abstract(thunk):
    """
    The decorated function is abstract.

    @note: only methods are currently supported.
    """
    @classmethod
    @wraps(thunk)
    def inner(cls, *a, **k):
        raise NotImplementedError(qual(cls) + " does not implement " +
                                  thunk.func_name)
    return inner



class WorkItem(object):
    """
    An item of work.

    @ivar workID: the unique identifier (primary key) for items of this type.
        There must be a corresponding column in the database.
    @type workID: L{int}

    @cvar created: the timestamp that a given item was created, or the column
        describing its creation time, on the class.
    @type created: L{datetime.datetime}
    """

    @abstract
    def doWork(self):
        """
        Subclasses must implement this to actually perform the queued work.

        This method will be invoked in a worker process.

        This method does I{not} need to delete the row referencing it; that
        will be taken care of by the job queueing machinery.
        """


    @classmethod
    def forTable(cls, table):
        """
        Look up a work-item class given a particular L{TableSyntax}.  Factoring
        this correctly may place it into L{twext.enterprise.record.Record}
        instead; it is probably generally useful to be able to look up a mapped
        class from a table.

        @param table: the table to look up
        @type table: L{twext.enterprise.dal.model.Table}

        @return: the relevant subclass
        @rtype: L{type}
        """



class PerformWork(Command):
    """
    Notify another process that it must do some work that has been persisted to
    the database, by informing it of the table and the ID where said work has
    been persisted.
    """

    arguments = [
        ("table", TableSyntaxByName()),
        ("workID", Integer()),
    ]
    response = []



class SchemaAMP(AMP):
    """
    An AMP instance which also has a L{Schema} attached to it.

    @ivar schema: The schema to look up L{TableSyntaxByName} arguments in.
    @type schema: L{Schema}
    """

    def __init__(self, schema, boxReceiver=None, locator=None):
        self.schema = schema
        super(SchemaAMP, self).__init__(boxReceiver, locator)




class ConnectionFromPeerNode(SchemaAMP):
    """
    A connection to a peer node.  Symmetric; since the 'client' and the
    'server' both serve the same role, the logic is the same in every node.
    """

    def __init__(self, peerPool, boxReceiver=None, locator=None):
        """
        Initialize this L{ConnectionFromPeerNode} with a reference to a pool of
        local workers.

        @param localWorkerPool: the pool of local worker procesess that can
            process queue work.
        @type localWorkerPool: L{WorkerConnectionPool}

        @see: L{AMP.__init__}
        """
        self.peerPool = peerPool
        self.localWorkerPool = peerPool.workerPool
        super(ConnectionFromPeerNode, self).__init__(peerPool.schema,
                                                     boxReceiver, locator)


    def startReceivingBoxes(self, sender):
        """
        Connection is up and running; add this to the list of active peers.
        """
        r = super(ConnectionFromPeerNode, self).startReceivingBoxes(sender)
        self.peerPool.addPeerConnection(self)
        return r


    def stopReceivingBoxes(self, reason):
        """
        The connection has shut down; remove this from the list of active
        peers.
        """
        self.peerPool.removePeerConnection(self)
        r = super(ConnectionFromPeerNode, self).stopReceivingBoxes(reason)
        return r


    def currentLoadEstimate(self):
        """
        What is the current load estimate for this peer?

        @return: The number of full "slots", i.e. currently-being-processed
            queue items (and other items which may contribute to this process's
            load, such as currently-being-processed client requests).
        @rtype: L{int}
        """
        return 0


    def performWork(self, table, workID):
        """
        A L{local worker connection <ConnectionFromWorker>} is asking this
        specific peer node-controller process to perform some work, having
        already determined that it's appropriate.

        @param table: The table where work is waiting.
        @type table: L{TableSyntax}

        @param workID: The primary key identifier of the given work.
        @type workID: L{int}

        @return: a L{Deferred} firing with an empty dictionary when the work is
            complete.
        @rtype: L{Deferred} firing L{dict}
        """
        return self.callRemote(PerformWork,
                               table=table.model.name, workID=workID)


    @PerformWork.responder
    def dispatchToWorker(self, table, workID):
        """
        A remote peer node has asked this node to do some work; dispatch it to
        a local worker on this node.

        @param table: the table to work on.
        @type table: L{TableSyntax}

        @param workID: the identifier within the table.
        @type workID: L{int}

        @return: a L{Deferred} that fires when the work has been completed.
        """
        return self.localWorkerPool.performWork(table, workID)



class WorkerConnectionPool(object):
    """
    A pool of L{ConnectionFromWorker}s.

    L{WorkerConnectionPool} also implements the same implicit protocol as a
    L{ConnectionFromPeerNode}, but one that dispenses work to the local
    worker processes rather than to a remote connection pool.
    """

    def __init__(self, maximumLoadPerWorker=0):
        self.workers = []
        self.maximumLoadPerWorker = maximumLoadPerWorker


    def addWorker(self, worker):
        """
        Add a L{ConnectionFromWorker} to this L{WorkerConnectionPool} so that
        it can be selected.
        """
        self.workers.append(worker)


    def removeWorker(self, worker):
        """
        Remove a L{ConnectionFromWorker} from this L{WorkerConnectionPool} that
        was previously added.
        """
        self.workers.remove(worker)


    def hasAvailableCapacity(self):
        """
        Does this worker connection pool have any local workers who have spare
        hasAvailableCapacity to process another queue item?
        """
        for worker in self.workers:
            if worker.currentLoad() < self.maximumLoadPerWorker:
                return True
        return False


    def _selectLowestLoadLocalConnection(self):
        """
        Select the local connection with the lowest current load, or C{None} if
        all workers are too busy.

        @return: a worker connection with the lowest current load.
        @rtype: L{ConnectionFromWorker}
        """
        return sorted(self.workers[:], key=lambda w: w.currentLoad())[0]


    def performWork(self, table, workID):
        """
        Select a local worker that is idle enough to perform the given work,
        then ask them to perform it.

        @param table: The table where work is waiting.
        @type table: L{TableSyntax}

        @param workID: The primary key identifier of the given work.
        @type workID: L{int}

        @return: a L{Deferred} firing with an empty dictionary when the work is
            complete.
        @rtype: L{Deferred} firing L{dict}
        """
        return self._selectLowestLoadLocalConnection().performWork(table, workID)



class ConnectionFromWorker(SchemaAMP):
    """
    An individual connection from a worker, as seem from the master's
    perspective.  L{ConnectionFromWorker}s go into a L{WorkerConnectionPool}.

    @ivar workerPool: The connection pool that this individual connection is
        participating in.
    @type workerPool: L{WorkerConnectionPool}
    """

    def __init__(self, schema, workerPool, boxReceiver=None, locator=None):
        self.workerPool = workerPool
        super(ConnectionFromWorker, self).__init__(schema, boxReceiver, locator)


    @property
    def currentLoad(self):
        """
        What is the current load of this worker?
        """
        # TODO: this needs to be hooked up to something.
        return 0


    def startReceivingBoxes(self, sender):
        """
        Start receiving AMP boxes from the peer.  Initialize all necessary
        state.
        """
        result = super(ConnectionFromWorker, self).startReceivingBoxes(sender)
        self.workerPool.addWorker(self)
        return result


    def stopReceivingBoxes(self, reason):
        """
        AMP boxes will no longer be received.
        """
        result = super(ConnectionFromWorker, self).stopReceivingBoxes(reason)
        self.workerPool.removeWorker(self)
        return result


    def performWork(self, table, workID):
        """
        Dispatch work to this worker.

        @see: The responder for this should always be
            L{ConnectionFromController.actuallyReallyExecuteWorkHere}.
        """
        return self.callRemote(PerformWork,
                               table=table.model.name, workID=workID)



class ConnectionFromController(SchemaAMP):
    """
    A L{ConnectionFromController} is the connection to a node-controller process,
    in a worker process.  It processes requests from its own master to do work.
    It is the opposite end of the connection from L{ConnectionFromWorker}.
    """

    def __init__(self, transactionFactory, schema,
                 boxReceiver=None, locator=None):
        super(ConnectionFromController, self).__init__(schema,
                                                       boxReceiver, locator)
        self.transactionFactory = transactionFactory


    @PerformWork.responder
    @inlineCallbacks
    def actuallyReallyExecuteWorkHere(self, table, workID):
        """
        This is where it's time to actually do the work.  The master process
        has instructed this worker to do it; so, look up the data in the row,
        and do it.
        """
        workItemClass = WorkItem.forTable(table)
        txn = self.transactionFactory()
        try:
            workItem = yield workItemClass.load(txn, workID)
            # TODO: what if we fail?  error-handling should be recorded
            # someplace, the row should probably be marked, re-tries should be
            # triggerable administratively.
            yield workItem.delete()
            # TODO: verify that workID is the primary key someplace.
            yield workItem.doWork()
        except:
            f = Failure()
            yield txn.abort()
            returnValue(f)
        else:
            yield txn.commit()
            returnValue({})



class TransactionFailed(Exception):
    """
    A transaction failed.
    """



def _cloneDeferred(d):
    """
    Make a new Deferred, adding callbacks to C{d}.

    @return: another L{Deferred} that fires with C{d's} result when C{d} fires.
    @rtype: L{Deferred}
    """
    d2 = Deferred()
    d.chainDeferred(d2)
    return d2



class WorkProposal(object):
    """
    A L{WorkProposal} is a proposal for work that will be executed, perhaps on
    another node, perhaps in the future.

    @ivar pool: the connection pool which this L{WorkProposal} will use to
        submit its work.
    @type pool: L{PeerConnectionPool}

    @ivar txn: The transaction where the work will be enqueued.
    @type txn: L{IAsyncTransaction}

    @ivar workItemType: The type of work to be enqueued by this L{WorkProposal}
    @type workItemType: L{WorkItem} subclass

    @ivar kw: The keyword arguments to pass to C{self.workItemType} to
        construct it.
    @type kw: L{dict}
    """

    def __init__(self, pool, txn, workItemType, kw):
        self.pool = pool
        self.txn = txn
        self.workItemType = workItemType
        self.kw = kw
        self._whenProposed = Deferred()
        self._whenExecuted = Deferred()
        self._whenCommitted = Deferred()


    def _start(self):
        """
        Execute this L{WorkProposal}.
        """
        @passthru(self.workItemType.create(self.txn, **self.kw).addCallback)
        def created(workItem):
            self._whenProposed.callback(None)
            @self.txn.postCommit
            def whenDone():
                self._whenCommitted.callback(None)
                @passthru(self.pool.choosePeer().addCallback)
                def peerChosen(peer):
                    @passthru(peer.performWork(workItem.__tbl__,
                                               workItem.workID))
                    def performed(result):
                        self._whenExecuted.callback(None)
                    @performed.addErrback
                    def notPerformed(why):
                        self._whenExecuted.errback(why)
                @peerChosen.addErrback
                def notChosen(whyNot):
                    self._whenExecuted.errback(whyNot)
            @self.txn.postAbort
            def whenFailed():
                self._whenCommitted.errback(TransactionFailed)


    def whenExecuted(self):
        """
        Let the caller know when the proposed work has been fully executed.

        @note: The L{Deferred} returned by C{whenExecuted} should be used with
            extreme caution.  If an application decides to do any
            database-persistent work as a result of this L{Deferred} firing,
            that work I{may be lost} as a result of a service being normally
            shut down between the time that the work is scheduled and the time
            that it is executed.  So, the only things that should be added as
            callbacks to this L{Deferred} are those which are ephemeral, in
            memory, and reflect only presentation state associated with the
            user's perception of the completion of work, not logical chains of
            work which need to be completed in sequence; those should all be
            completed within the transaction of the L{WorkItem.doWork} that
            gets executed.

        @return: a L{Deferred} that fires with C{None} when the work has been
            completed remotely.
        """
        return _cloneDeferred(self._whenExecuted)


    def whenProposed(self):
        """
        Let the caller know when the work has been proposed; i.e. when the work
        is first transmitted to the database.

        @return: a L{Deferred} that fires with C{None} when the relevant
            commands have been sent to the database to create the L{WorkItem},
            and fails if those commands do not succeed for some reason.
        """
        return _cloneDeferred(self._whenProposed)


    def whenCommitted(self):
        """
        Let the caller know when the work has been committed to; i.e. when the
        transaction where the work was proposed has been committed to the
        database.

        @return: a L{Deferred} that fires with C{None} when the relevant
            transaction has been committed, or fails if the transaction is not
            committed for any reason.
        """
        return _cloneDeferred(self._whenCommitted)



class PeerConnectionPool(Service, object):
    """
    Each node has a L{PeerConnectionPool} connecting it to all the other nodes
    currently active on the same database.

    @ivar hostName: The hostname where this node process is running, as
        reported by the local host's configuration.  Possibly this should be
        obtained via C{config.ServerHostName} instead of C{socket.getfqdn()};
        although hosts within a cluster may be configured with the same
        C{ServerHostName}; TODO need to confirm.
    @type hostName: L{bytes}

    @ivar thisProcess: a L{NodeInfo} representing this process, which is
        initialized when this L{PeerConnectionPool} service is started via
        C{startService}.  May be C{None} if this service is not fully started
        up or if it is shutting down.
    @type thisProcess: L{NodeInfo}

    @ivar queueProcessTimeout: The maximum amount of time allowed for a queue
        item to be processed.  By default, 10 minutes.
    @type queueProcessTimeout: L{float} (in seconds)

    @ivar queueDelayedProcessInterval: The amount of time between database
        pings, i.e. checks for over-due queue items that might have been
        orphaned by a master process that died mid-transaction.  This is how
        often the shared database should be pinged by I{all} nodes (i.e., all
        master processes, or each instance of L{PeerConnectionPool}); each
        individual node will ping commensurately less often as more nodes join
        the database.
    @type queueDelayedProcessInterval: L{float} (in seconds)

    @ivar reactor: The reactor used for scheduling timed events.
    @type reactor: L{IReactorTime} provider.

    @ivar peers: The list of currently connected peers.
    @type peers: L{list} of L{PeerConnectionPool}
    """

    getfqdn = staticmethod(getfqdn)
    getpid = staticmethod(getpid)

    queueProcessTimeout = (10.0 * 60.0)
    queueDelayedProcessInterval = (60.0)

    def __init__(self, reactor, transactionFactory, ampPort, schema):
        """
        Initialize a L{PeerConnectionPool}.

        @param ampPort: The AMP TCP port number to listen on for inter-host
            communication.  This must be an integer (and not, say, an endpoint,
            or an endpoint description) because we need to communicate it to
            the other peers in the cluster in a way that will be meaningful to
            them as clients.
        @type ampPort: L{int}

        @param transactionFactory: a 0- or 1-argument callable that produces an
            L{IAsyncTransaction}

        @param schema: The schema which contains all the tables associated with
            the L{WorkItem}s that this L{PeerConnectionPool} will process.
        @type schema: L{Schema}
        """
        self.reactor = reactor
        self.transactionFactory = transactionFactory
        self.hostName = self.getfqdn()
        self.pid = self.getpid()
        self.ampPort = ampPort
        self.thisProcess = None
        self.workerPool = WorkerConnectionPool()
        self.peers = []
        self.schema = schema


    def addPeerConnection(self, peer):
        """
        Add a L{ConnectionFromPeerNode} to the active list of peers.
        """
        self.peers.append(peer)


    def removePeerConnection(self, peer):
        """
        Remove a L{ConnectionFromPeerNode} to the active list of peers.
        """
        self.peers.remove(peer)


    def choosePeer(self):
        """
        Choose a peer to distribute work to based on the current known slot
        occupancy of the other masters.  Note that this will prefer
        distributing work to local workers until the current node is full,
        because that should be lower-latency.  Also, if no peers are available,
        work will be submitted locally even if the worker pool is already
        over-subscribed.

        @return: a L{Deferred <twisted.internet.defer.Deferred>} which fires
            with the chosen 'peer', i.e. object with a C{performWork} method,
            as soon as one is available.  Normally this will be synchronous,
            but we need to account for the possibility that we may need to
            connect to other hosts.
        @rtype: L{Deferred <twisted.internet.defer.Deferred>} firing
            L{ConnectionFromPeerNode} or L{WorkerConnectionPool}
        """
        if not self.workerPool.hasAvailableCapacity() and self.peers:
            return sorted(self.peers, lambda p: p.currentLoadEstimate())[0]
        else:
            return succeed(self.workerPool)


    def enqueueWork(self, txn, workItemType, **kw):
        """
        There is some work to do.  Do it, someplace else, ideally in parallel.
        Later, let the caller know that the work has been completed by firing a
        L{Deferred}.

        @param workItemType: The type of work item to be enqueued.
        @type workItemType: A subtype of L{WorkItem}

        @param kw: The parameters to construct a work item.
        @type kw: keyword parameters to C{workItemType.create}, i.e.
            C{workItemType.__init__}

        @return: an object that can track the enqueuing and remote execution of
            this work.
        @rtype: L{WorkProposal}
        """
        wp = WorkProposal(self, txn, workItemType, kw)
        wp._start()
        return wp


    def allWorkItemTypes(self):
        """
        Load all the L{WorkItem} types that this node can process and return
        them.

        @return: L{list} of L{type}
        """
        # TODO: For completeness, this may need to involve a plugin query to
        # make sure that all WorkItem subclasses are imported first.
        return WorkItem.__subclasses__()


    def totalNumberOfNodes(self):
        """
        How many nodes / master processes are there?

        @return: the maximum number of other L{PeerConnectionPool} instances
            that may be connected to the database described by
            C{self.transactionFactory}.  Note that this is not the current count
            by connectivity, but the count according to the database.
        @rtype: L{int}
        """
        # TODO
        return 20


    def nodeIndex(self):
        """
        What ordinal does this node, i.e. this instance of
        L{PeerConnectionPool}, occupy within the ordered set of all nodes
        connected to the database described by C{self.transactionFactory}?

        @return: the index of this node within the total collection.  For
            example, if this L{PeerConnectionPool} is 6 out of 30, this method
            will return C{6}.
        @rtype: L{int}
        """
        # TODO
        return 6


    @inlineCallbacks
    def _periodicLostWorkCheck(self):
        """
        Periodically, every master process has to check to make sure that work
        hasn't been dropped on the floor.  In order to do that it queries each
        work-item table.
        """
        txn = self.transactionFactory()
        try:
            for itemType in self.allWorkItemTypes():
                for overdueItem in (
                        yield itemType.query(
                            txn, itemType.created > self.queueProcessTimeout
                    )):
                    peer = yield self.choosePeer()
                    yield peer.performWork(overdueItem.__tbl__,
                                           overdueItem.workID)
        finally:
            yield txn.commit()


    @inlineCallbacks
    def _lostWorkCheckLoop(self):
        """
        While the service is running, keep checking for any overdue / lost work
        items and re-submit them to the cluster for processing.
        """
        while self.running:
            yield self._periodicLostWorkCheck()
            index = self.nodeIndex()
            now = self.reactor.seconds()
            interval = self.queueDelayedProcessInterval
            count = self.totalNumberOfNodes()
            when = (now - (now % interval)) + (interval * (count + index))
            delay = when - now
            yield deferLater(self.reactor, delay, lambda : None)


    def startService(self):
        """
        Register ourselves with the database and establish all outgoing
        connections to other servers in the cluster.
        """
        self._doStart()


    def activeNodes(self, txn):
        """
        Load information about all other nodes.
        """
        return NodeInfo.query()


    @inlineCallbacks
    def _doStart(self):
        """
        First, we tell the database that we're an active node so that other
        nodes know about us.  This should also give us a
        unique-to-the-whole-database identifier for this process instance.
        """
        txn = self.transactionFactory()
        thisProcess = yield NodeInfo.create(
            txn, hostname=self.hostName, pid=self.pid, port=self.ampPort,
            time=datetime.datetime.now()
        )

        """
        It might be a good idea to update this periodicially in order to give
        an indication that the process isn't dead.  On the other hand maybe
        there's no concrete feature which actually requires this information.
        """
        lc = LoopingCall(thisProcess.updateCurrent, self.transactionFactory)
        lc.start(30.0)

        """
        Now let's find all the other masters.
        """
        masters = self.activeNodes(txn)

        """
        Each other 'master' here is another L{NodeInfo} which tells us where
        to connect.
        """
        for master in masters:
            self._startConnectingTo(master)


    def _startConnectingTo(self, master):
        """
        Start an outgoing connection to another master process.

        @param master: a description of the master to connect to.
        @type master: L{NodeInfo}
        """
        f = Factory()
        f.buildProtocol = self.createPeerConnection
        master.endpoint().connect(f)


    def createPeerConnection(self, addr):
        # TODO: add to peer list, remove from peer list
        return ConnectionFromPeerNode(self)



def sketch():
    """
    Example demonstrating how an application would normally talk to the queue.
    """
    # XXX in real life, MyWorkItem would also need to inherit from
    # fromTable(...), would need to be declared at the top level...
    class MyWorkItem(WorkItem):
        @inlineCallbacks
        def doWork(self):
            txn = self.__txn__
            yield self.doSomethingDeferred(txn)
            returnValue(None)


    @inlineCallbacks
    def sampleFunction(txn):
        for x in range(10):
            # Note: no yield here.  Yielding for this to be completed would
            # generate unnecessary lock contention, potentially a deadlock (we
            # just created the item in this transaction, the next transaction is
            # going to want to delete it).
            txn.enqueueWork(MyWorkItem, someInt=x, someUnicode=u'4321')
        yield txn.commit()

