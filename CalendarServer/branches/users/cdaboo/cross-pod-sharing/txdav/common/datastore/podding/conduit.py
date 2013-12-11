##
# Copyright (c) 2013 Apple Inc. All rights reserved.
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

from twext.python.log import Logger

from twisted.internet.defer import inlineCallbacks, returnValue

from txdav.common.datastore.podding.request import ConduitRequest
from txdav.common.idirectoryservice import DirectoryRecordNotFoundError
from txdav.common.icommondatastore import ExternalShareFailed


__all__ = [
    "PoddingConduitResource",
]

log = Logger()


class FailedCrossPodRequestError(RuntimeError):
    """
    Request returned an error.
    """
    pass



class PoddingConduit(object):
    """
    This class is the API/RPC bridge between cross-pod requests and the store.

    Each cross-pod request/response is described by a Python C{dict} that is serialized
    to JSON for the HTTP request/response.

    Each request C{dict} has an "action" key that indicates what call is being made, and
    the other keys are arguments to that call.

    Each response C{dict} has a "result" key that indicates the call result, and other
    optional keys for any values returned by the call.

    The conduit provides two methods for each action: one for the sending side and one for
    the receiving side, called "send_{action}" and "recv_{action}", respectively, where
    {action} is the action value.

    The "send_{action}" calls each have a set of arguments specific to the call itself. The
    code takes care of packing that into a C{dict} and sending to the appropriate pod.

    The "recv_{action}" calls take a single C{dict} argument that is the deserialized JSON
    data from the incoming request. The return value is a C{dict} with the result.

    Some simple forms of send_/recv_ methods can be auto-generated to simplify coding.

    Right now this conduit is used for cross-pod sharing operations. In the future we will
    likely use it for cross-pod migration.
    """

    conduitRequestClass = ConduitRequest

    def __init__(self, store):
        """
        @param store: the L{CommonDataStore} in use.
        """
        self.store = store


    def validRequst(self, source_guid, destination_guid):
        """
        Verify that the specified GUIDs are valid for the request and return the
        matching directory records.

        @param source_guid: GUID for the user on whose behalf the request is being made
        @type source_guid: C{str}
        @param destination_guid: GUID for the user to whom the request is being sent
        @type destination_guid: C{str}

        @return: C{tuple} of L{IStoreDirectoryRecord}
        """

        source = self.store.directoryService().recordWithUID(source_guid)
        if source is None:
            raise DirectoryRecordNotFoundError("Cross-pod source: {}".format(source_guid))
        if not source.thisServer():
            raise FailedCrossPodRequestError("Cross-pod source not on this server: {}".format(source_guid))

        destination = self.store.directoryService().recordWithUID(destination_guid)
        if destination is None:
            raise DirectoryRecordNotFoundError("Cross-pod destination: {}".format(destination_guid))
        if destination.thisServer():
            raise FailedCrossPodRequestError("Cross-pod destination on this server: {}".format(destination_guid))

        return (source, destination,)


    @inlineCallbacks
    def sendRequest(self, txn, recipient, data):

        request = self.conduitRequestClass(recipient.server(), data)
        try:
            response = (yield request.doRequest(txn))
        except Exception as e:
            raise FailedCrossPodRequestError("Failed cross-pod request: {}".format(e))
        returnValue(response)


    @inlineCallbacks
    def processRequest(self, data):
        """
        Process the request.

        @param data: the JSON data to process
        @type data: C{dict}
        """
        # Must have a dict with an "action" key
        try:
            action = data["action"]
        except (KeyError, TypeError) as e:
            log.error("JSON data must have an object as its root with an 'action' attribute: {ex}\n{json}", ex=e, json=data)
            raise FailedCrossPodRequestError("JSON data must have an object as its root with an 'action' attribute: {}\n{}".format(e, data,))

        if action == "ping":
            result = {"result": "ok"}
            returnValue(result)

        method = "recv_{}".format(action)
        if not hasattr(self, method):
            log.error("Unsupported action: {action}", action=action)
            raise FailedCrossPodRequestError("Unsupported action: {}".format(action))

        # Need a transaction to work with
        txn = self.store.newTransaction(repr("Conduit request"))

        # Do the actual request processing
        try:
            result = (yield getattr(self, method)(txn, data))
        except Exception as e:
            yield txn.abort()
            log.error("Failed action: {action}, {ex}", action=action, ex=e)
            raise FailedCrossPodRequestError("Failed action: {}, {}".format(action, e))

        yield txn.commit()

        returnValue(result)


    #
    # Invite related apis
    #

    @inlineCallbacks
    def send_shareinvite(self, txn, homeType, ownerUID, ownerID, ownerName, shareeUID, shareUID, bindMode, summary, supported_components):
        """
        Send a sharing invite cross-pod message.

        @param homeType: Type of home being shared.
        @type homeType: C{int}
        @param ownerUID: GUID of the sharer.
        @type ownerUID: C{str}
        @param ownerID: resource ID of the sharer calendar
        @type ownerID: C{int}
        @param ownerName: owner's name of the sharer calendar
        @type ownerName: C{str}
        @param shareeUID: GUID of the sharee
        @type shareeUID: C{str}
        @param shareUID: Resource/invite ID for sharee
        @type shareUID: C{str}
        @param bindMode: bind mode for the share
        @type bindMode: C{str}
        @param summary: sharing message
        @type summary: C{str}
        @param supported_components: supproted components, may be C{None}
        @type supported_components: C{str}
        """

        _ignore_sender, recipient = self.validRequst(ownerUID, shareeUID)

        action = {
            "action": "shareinvite",
            "type": homeType,
            "owner": ownerUID,
            "owner_id": ownerID,
            "owner_name": ownerName,
            "sharee": shareeUID,
            "share_id": shareUID,
            "mode": bindMode,
            "summary": summary,
        }
        if supported_components is not None:
            action["supported-components"] = supported_components

        result = yield self.sendRequest(txn, recipient, action)
        returnValue(result)


    @inlineCallbacks
    def recv_shareinvite(self, txn, message):
        """
        Process a sharing invite cross-pod message. Message arguments as per L{send_shareinvite}.

        @param message: message arguments
        @type message: C{dict}
        """

        if message["action"] != "shareinvite":
            raise FailedCrossPodRequestError("Wrong action '{}' for recv_shareinvite".format(message["action"]))

        # Create a share
        shareeHome = yield txn.homeWithUID(message["type"], message["sharee"], create=True)
        if shareeHome is None or shareeHome.external():
            raise FailedCrossPodRequestError("Invalid sharee UID specified")

        try:
            yield shareeHome.processExternalInvite(
                message["owner"],
                message["owner_id"],
                message["owner_name"],
                message["share_id"],
                message["mode"],
                message["summary"],
                supported_components=message.get("supported-components")
            )
        except ExternalShareFailed as e:
            raise FailedCrossPodRequestError(str(e))

        returnValue({
            "result": "ok",
        })


    @inlineCallbacks
    def send_shareuninvite(self, txn, homeType, ownerUID, ownerID, shareeUID, shareUID):
        """
        Send a sharing uninvite cross-pod message.

        @param homeType: Type of home being shared.
        @type homeType: C{int}
        @param ownerUID: GUID of the sharer.
        @type ownerUID: C{str}
        @param ownerID: resource ID of the sharer calendar
        @type ownerID: C{int}
        @param shareeUID: GUID of the sharee
        @type shareeUID: C{str}
        @param shareUID: Resource/invite ID for sharee
        @type shareUID: C{str}
        """

        _ignore_sender, recipient = self.validRequst(ownerUID, shareeUID)

        action = {
            "action": "shareuninvite",
            "type": homeType,
            "owner": ownerUID,
            "owner_id": ownerID,
            "sharee": shareeUID,
            "share_id": shareUID,
        }

        result = yield self.sendRequest(txn, recipient, action)
        returnValue(result)


    @inlineCallbacks
    def recv_shareuninvite(self, txn, message):
        """
        Process a sharing uninvite cross-pod message. Message arguments as per L{send_shareuninvite}.

        @param message: message arguments
        @type message: C{dict}
        """

        if message["action"] != "shareuninvite":
            raise FailedCrossPodRequestError("Wrong action '{}' for recv_shareuninvite".format(message["action"]))

        # Create a share
        shareeHome = yield txn.homeWithUID(message["type"], message["sharee"], create=True)
        if shareeHome is None or shareeHome.external():
            FailedCrossPodRequestError("Invalid sharee UID specified")

        try:
            yield shareeHome.processExternalUninvite(
                message["owner"],
                message["owner_id"],
                message["share_id"],
            )
        except ExternalShareFailed as e:
            FailedCrossPodRequestError(str(e))

        returnValue({
            "result": "ok",
        })


    @inlineCallbacks
    def send_sharereply(self, txn, homeType, ownerUID, shareeUID, shareUID, bindStatus, summary=None):
        """
        Send a sharing reply cross-pod message.

        @param homeType: Type of home being shared.
        @type homeType: C{int}
        @param ownerUID: GUID of the sharer.
        @type ownerUID: C{str}
        @param shareeUID: GUID of the recipient
        @type shareeUID: C{str}
        @param shareUID: Resource/invite ID for recipient
        @type shareUID: C{str}
        @param bindStatus: bind mode for the share
        @type bindStatus: C{str}
        @param summary: sharing message
        @type summary: C{str}
        """

        _ignore_sender, recipient = self.validRequst(shareeUID, ownerUID)

        action = {
            "action": "sharereply",
            "type": homeType,
            "owner": ownerUID,
            "sharee": shareeUID,
            "share_id": shareUID,
            "status": bindStatus,
        }
        if summary is not None:
            action["summary"] = summary

        result = yield self.sendRequest(txn, recipient, action)
        returnValue(result)


    @inlineCallbacks
    def recv_sharereply(self, txn, message):
        """
        Process a sharing reply cross-pod message. Message arguments as per L{send_sharereply}.

        @param message: message arguments
        @type message: C{dict}
        """

        if message["action"] != "sharereply":
            raise FailedCrossPodRequestError("Wrong action '{}' for recv_sharereply".format(message["action"]))

        # Create a share
        ownerHome = yield txn.homeWithUID(message["type"], message["owner"])
        if ownerHome is None or ownerHome.external():
            FailedCrossPodRequestError("Invalid owner UID specified")

        try:
            yield ownerHome.processExternalReply(
                message["owner"],
                message["sharee"],
                message["share_id"],
                message["status"],
                summary=message.get("summary")
            )
        except ExternalShareFailed as e:
            FailedCrossPodRequestError(str(e))

        returnValue({
            "result": "ok",
        })


    #
    # Sharer data access related apis
    #

    def _send(self, action, shareeView):
        """
        Base behavior for an operation on a sharee resource.

        @param shareeView: sharee resource being operated on.
        @type shareeView: L{CommonHomeChildExternal}
        """

        homeType = shareeView.ownerHome()._homeType
        ownerUID = shareeView.ownerHome().uid()
        ownerID = shareeView.external_id()
        shareeUID = shareeView.viewerHome().uid()

        _ignore_sender, recipient = self.validRequst(shareeUID, ownerUID)

        result = {
            "action": action,
            "type": homeType,
            "owner": ownerUID,
            "owner_id": ownerID,
            "sharee": shareeUID,
        }
        return result, recipient


    @inlineCallbacks
    def _recv(self, txn, message, expected_action):
        """
        Base behavior for sharer data access.

        @param message: message arguments
        @type message: C{dict}
        """

        if message["action"] != expected_action:
            raise FailedCrossPodRequestError("Wrong action '{}' for recv_{}".format(message["action"], expected_action))

        # Create a share
        ownerHome = yield txn.homeWithUID(message["type"], message["owner"], create=True)
        if ownerHome is None or ownerHome.external():
            FailedCrossPodRequestError("Invalid owner UID specified")

        ownerHomeChild = yield ownerHome.childWithID(message["owner_id"])
        if ownerHomeChild is None:
            FailedCrossPodRequestError("Invalid owner shared resource specified")

        returnValue((ownerHome, ownerHomeChild))


    #
    # Simple calls are ones where there is no argument and a single return value. We can simplify
    # code generation for these by dynamically generating the appropriate class methods.
    #

    @inlineCallbacks
    def _simple_send(self, actionName, shareeView, args=None, kwargs=None):
        """
        A simple send operation that returns a value.

        @param actionName: name of the action.
        @type actionName: C{str}
        @param shareeView: sharee resource being operated on.
        @type shareeView: L{CommonHomeChildExternal}
        @param args: list of optional arguments.
        @type args: C{list}
        @param kwargs: optional keyword arguments.
        @type kwargs: C{dict}
        """

        action, recipient = self._send(actionName, shareeView)
        if args is not None:
            action["arguments"] = args
        if kwargs is not None:
            action["keywords"] = kwargs
        result = yield self.sendRequest(shareeView._txn, recipient, action)
        returnValue(result["value"])


    @inlineCallbacks
    def _simple_recv(self, txn, actionName, message, method):
        """
        A simple recv operation that returns a value. We also look for an optional set of arguments/keywords
        and include those only if present.

        @param actionName: name of the action.
        @type actionName: C{str}
        @param message: message arguments
        @type message: C{dict}
        @param method: name of the method to execute on the shared resource to get the result.
        @type method: C{str}
        """

        _ignore_ownerHome, ownerHomeChild = yield self._recv(txn, message, actionName)
        value = yield getattr(ownerHomeChild, method)(*message.get("arguments", ()), **message.get("keywords", {}))
        returnValue({
            "result": "ok",
            "value": value,
        })


    @classmethod
    def _make_simple_action(cls, action, method):
        setattr(cls, "send_{}".format(action), lambda self, shareeView, *args, **kwargs: self._simple_send(action, shareeView, args, kwargs))
        setattr(cls, "recv_{}".format(action), lambda self, txn, message: self._simple_recv(txn, action, message, method))


PoddingConduit._make_simple_action("countobjects", "countObjectResources")
PoddingConduit._make_simple_action("listobjects", "listObjectResources")
PoddingConduit._make_simple_action("synctoken", "syncToken")
PoddingConduit._make_simple_action("resourcenamessincerevision", "resourceNamesSinceRevision")
PoddingConduit._make_simple_action("resourceuidforname", "resourceUIDForName")
PoddingConduit._make_simple_action("resourcenameforuid", "resourceNameForUID")
