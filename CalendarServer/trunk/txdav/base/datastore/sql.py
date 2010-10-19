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
Logic common to SQL implementations.
"""

from twisted.internet.defer import Deferred
from inspect import getargspec

def _getarg(argname, argspec, args, kw):
    """
    Get an argument from some arguments.

    @param argname: The name of the argument to retrieve.

    @param argspec: The result of L{inspect.getargspec}.

    @param args: positional arguments passed to the function specified by
        argspec.

    @param kw: keyword arguments passed to the function specified by
        argspec.

    @return: The value of the argument named by 'argname'.
    """
    argnames = argspec[0]
    try:
        argpos = argnames.index(argname)
    except ValueError:
        argpos = None
    if argpos is not None:
        if len(args) > argpos:
            return args[argpos]
    if argname in kw:
        return kw[argname]
    else:
        raise TypeError("could not find key argument %r in %r/%r (%r)" %
            (argname, args, kw, argpos)
        )



def memoized(keyArgument, memoAttribute):
    """
    Decorator which memoizes the result of a method on that method's instance.

    @param keyArgument: The name of the 'key' argument.

    @type keyArgument: C{str}

    @param memoAttribute: The name of the attribute on the instance which
        should be used for memoizing the result of this method; the attribute
        itself must be a dictionary.

    @type memoAttribute: C{str}
    """
    def decorate(thunk):
        # cheater move to try to get the right argspec from inlineCallbacks.
        # This could probably be more robust, but the 'cell_contents' thing
        # probably can't (that's the only real reference to the underlying
        # function).
        if thunk.func_code.co_name == 'unwindGenerator':
            specTarget = thunk.func_closure[0].cell_contents
        else:
            specTarget = thunk
        spec = getargspec(specTarget)
        def outer(*a, **kw):
            self = a[0]
            memo = getattr(self, memoAttribute)
            key = _getarg(keyArgument, spec, a, kw)
            if key in memo:
                result = memo[key]
            else:
                result = thunk(*a, **kw)
                if result is not None:
                    memo[key] = result
            if isinstance(result, Deferred):
                # clone the Deferred so that the old one keeps its result.
                # FIXME: cancellation?
                returnResult = Deferred()
                def relayAndPreserve(innerResult):
                    if innerResult is None and key in memo and memo[key] is result:
                        # The result was None, call it again.
                        del memo[key]
                    returnResult.callback(innerResult)
                    return innerResult
                result.addBoth(relayAndPreserve)
                return returnResult
            return result
        return outer
    return decorate


