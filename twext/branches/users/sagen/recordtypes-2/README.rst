Introduction
============

This is a python module consisting of extensions to the Twisted Framework
(http://twistedmatrix.com/).


Copyright and License
=====================

Copyright (c) 2005-2014 Apple Inc.  All rights reserved.

This software is licensed under the Apache License, Version 2.0.

See the file LICENSE_ for the full text of the license terms.

.. _LICENSE: LICENSE.txt


Installation
============

Python version 2.7 is supported.

This library is can be built using the standard distutils mechanisms.

It is also registered with the Python Package Index (PyPI) as ``twextpy`` 
(the name ``twext`` is used by another module in PyPI) for use with ``pip`` and
``easy_install``::

  pip install twextpy

This will build and install the ``twext`` module along with its base
dependencies.  This library has a number of optional features which must be
specified in order to download build and install their dependencies, for
example::

  pip install twextpy[DAL,Postgres]

These features are:

DAL
  Enables use of the Database Abstraction Layer implemented in
  ``twext.enterprise.dal``.

LDAP
  Enables support for the Lightweight Directory Access Protocol in
  ``twext.who.ldap``.

OpenDirectory
  Enables support for the (Mac OS) OpenDirectory framework in
  ``twext.who.opendirectory``.

Oracle
  Enables support for Oracle database connectivity in ``twext.enterprise`` and
  Oracle syntax in ``twext.enterprise.dal``.

Postgres
  Enables support for Postgres database connectivity in ``twext.enterprise``.


Development
===========

If you are planning to work on this library, you can manage this with standard
distutils mechanisms.  There are, however, some tools in the ``bin`` directory
which automate this management for you.

To use these tools, you must have ``pip`` on your system.
If you do not have ``pip``, instructions for installing it are at
http://www.pip-installer.org/en/latest/installing.html.
The script ``install_pip`` (requires ``sudo`` access) automates this for you.

The shell script ``develop`` downloads and builds any dependancies and sets up a
development environment.  ``develop`` handles non-Python as well as Python
dependancies.

The tools ``python``, ``pyflakes``, ``trial``, and ``twistd`` are wrappers
around the cooresponding commands that use ``develop`` to ensure that
dependancies are available, ``PYTHONPATH`` is set up correctly, etc.

``test`` runs all of the unit tests and does linting.  It should be run before
checking in any code.
