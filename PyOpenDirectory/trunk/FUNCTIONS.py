##
# Copyright (c) 2006 Apple Computer, Inc. All rights reserved.
#
# This file contains Original Code and/or Modifications of Original Code
# as defined in and that are subject to the Apple Public Source License
# Version 2.0 (the 'License'). You may not use this file except in
# compliance with the License. Please obtain a copy of the License at
# http://www.opensource.apple.com/apsl/ and read it before using this
# file.
# 
# The Original Code and all software distributed under the License are
# distributed on an 'AS IS' basis, WITHOUT WARRANTY OF ANY KIND, EITHER
# EXPRESS OR IMPLIED, AND APPLE HEREBY DISCLAIMS ALL SUCH WARRANTIES,
# INCLUDING WITHOUT LIMITATION, ANY WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE, QUIET ENJOYMENT OR NON-INFRINGEMENT.
# Please see the License for the specific language governing rights and
# limitations under the License.
#
# DRI: Cyrus Daboo, cdaboo@apple.com
##

"""
PyOpenDirectory Function Description.
"""
 
def odInit(nodename):
    """
    Create an Open Directory object to operate on the specified directory service node name.
 
    @param nodename: C{str} containing the node name.
    @return: C{object} an object to be passed to all subsequent functions on success,
        C{None} on failure.
    """

def listUsers(obj):
    """
    List users in Open Directory, and return key attributes for each user.
    The attributes in the tuple are (uid, guid, last-modified, calendar-principal-uri).
    
    @param obj: C{object} the object obtained from an odInit call.
    @return: C{list} containing a C{tuple} of C{str} for each user found,
        or C{None} on failure.
    """

def listGroups(obj):
    """
    List groups in Open Directory, and return key attributes for each group.
    The attributes in the tuple are (uid, guid, last-modified, calendar-principal-uri).
    
    @param obj: C{object} the object obtained from an odInit call.
    @return: C{list} containg a C{tuple} of C{str} for each group found,
        or C{None} on failure.
    """

def listResources(obj):
    """
    List resources in Open Directory, and return key attributes for each resource.
    The attributes in the tuple are (uid, guid, last-modified, calendar-principal-uri).
    
    @param obj: C{object} the object obtained from an odInit call.
    @return: C{list} containg a C{tuple} of C{str} for each resource found,
        or C{None} on failure.
    """

def checkUser(obj, user):
    """
    Check that a user exists in Open Directory.
    
    @param obj: C{object} the object obtained from an odInit call.
    @param user: C{str} containing the user to check.
    @return: C{True} if the user was found, C{False} otherwise.
    """

def checkGroup(obj, group):
    """
    Check that a group exists in Open Directory.
    
    @param obj: C{object} the object obtained from an odInit call.
    @param group: C{str} containing the group to check.
    @return: C{True} if the group was found, C{False} otherwise.
    """

def checkResource(obj, resource):
    """
    Check that a resource exists in Open Directory.
    
    @param obj: C{object} the object obtained from an odInit call.
    @param resource: C{str} containing the resource to check.
    @return: C{True} if the resource was found, C{False} otherwise.
    """

def userAttributes(obj, user):
    """
    Get user attributes relevant to CalDAV from Open Directory.
    
    @param obj: C{object} the object obtained from an odInit call.
    @param user: C{str} containing the user to get attributes for.
    @return: C{dict} of attributes if the user was found, C{None} otherwise.
    """

def groupAttributes(obj, grp):
    """
    Get group attributes relevant to CalDAV from Open Directory.
    
    @param obj: C{object} the object obtained from an odInit call.
    @param grp: C{str} containing the group to get attributes for.
    @return: C{dict} of attributes if the group was found, C{None} otherwise.
    """

def resourceAttributes(obj, rsrc):
    """
    Get resource attributes relevant to CalDAV from Open Directory.
    
    @param obj: C{object} the object obtained from an odInit call.
    @param rsrc: C{str} containing the resource to get attributes for.
    @return: C{dict} of attributes if the resource was found, C{None} otherwise.
    """

def authenticateUser(obj, user, pswd):
    """
    Authenticate a user with a password to Open Directory.
    
    @param obj: C{object} the object obtained from an odInit call.
    @param user: C{str} container the user to check.
    @param pswd: C{str} containing the password to check.
    @return: C{True} if the user was found, C{False} otherwise.
    """
