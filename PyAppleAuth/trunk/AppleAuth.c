/*
 * Copyright (c) 2006-2007 Apple Inc. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/* Module to authenticate against Directory Services. */

#include "Python.h"
#include <Security/Security.h>
#include <membership.h>

/* For some reason, these aren't exported in headers */
int checkpw(const char* userName, const char* password);
int mbr_reset_cache(void);
int mbr_user_name_to_uuid(const char* name, uuid_t uu);
int mbr_group_name_to_uuid(const char* name, uuid_t uu);
int mbr_check_service_membership(const uuid_t user, const char* servicename, int* ismember);

//static char appleauth_CheckPassword__doc__[] = "CheckPassword(username, password) -> 0 if success, <0 if failure";

/* 
	CheckPassword(username, password)
	Validates a username and password.
*/
static PyObject *appleauth_CheckPassword(PyObject *self, PyObject *args) {
	char *username;
	int usernameSize;
	char *password;
	int passwordSize;
	
	// get the args
	if (!PyArg_ParseTuple(args, "s#s#", &username, &usernameSize, &password, &passwordSize))
		return NULL;
	
	// check the password
	int result = checkpw(username, password);
	
	// build return value
	return Py_BuildValue("i", result);
}


/*
	CheckMembership(username, group)
	Checks user membership in a group.
*/
static PyObject *appleauth_CheckMembership(PyObject *self, PyObject *args) {
	char *username;
	int usernameSize;
	char *groupname;
	int groupnameSize;
	
	// get the args
	if (!PyArg_ParseTuple(args, "s#s#", &username, &usernameSize, &groupname, &groupnameSize))
		return NULL;
	
	// WARNING: RESETTING THE CACHE TO WORK AROUND memberd CACHING BUGS
	(void)mbr_reset_cache();
	
	// get a uuid for the user
	uuid_t user;
	int result = mbr_user_name_to_uuid(username, user);
	int isMember = 0;
	
	if ( result != 0 )
		return Py_BuildValue("i", (-1));
	
	// get a uuid for the group
	uuid_t group;
	result = mbr_group_name_to_uuid(groupname, group);
	
	if ( result != 0 ) 
		return Py_BuildValue("i", (-2));
	
	result = mbr_check_membership(user, group, &isMember);
	
	if (isMember != 1)
		return Py_BuildValue("i", (-3));
	
	return Py_BuildValue("i", 0);
}


/*
	CheckSACL(userOrGroupName, service)
	Checks user membership in a service.
*/
static PyObject *appleauth_CheckSACL(PyObject *self, PyObject *args) {
	char *username;
	int usernameSize;
	char *serviceName;
	int serviceNameSize;
	
	// get the args
	if (!PyArg_ParseTuple(args, "s#s#", &username, &usernameSize, &serviceName, &serviceNameSize))
		return NULL;
	
	// get a uuid for the user
	uuid_t user;
	int result = mbr_user_name_to_uuid(username, user);
	int isMember = 0;
	
	if ( result != 0 )
		result = mbr_group_name_to_uuid(username, user);

	if ( result != 0 )
		return Py_BuildValue("i", (-1));

	result = mbr_check_service_membership(user, serviceName, &isMember);
	
	if ( ( isMember == 1 ) || ( result == 2 ) ) { // passed
		return Py_BuildValue("i", 0);
	}
	
	return Py_BuildValue("i", (-2));
}


/* Method definitions. */
static struct PyMethodDef appleauth_methods[] = {
	{"CheckPassword", appleauth_CheckPassword},
	{"CheckMembership", appleauth_CheckMembership},
	{"CheckSACL", appleauth_CheckSACL},
	{NULL, NULL} /* Sentinel */
};

void initappleauth(void) {
	Py_InitModule("appleauth", appleauth_methods);
}
