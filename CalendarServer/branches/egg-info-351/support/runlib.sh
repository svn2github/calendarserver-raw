#! /bin/bash
# -*- sh-basic-offset: 2 -*-

##
# Copyright (c) 2005-2009 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

. support/pystuff.sh;

# Echo the usage for the main 'run' script, then exit with an error.
usage () {
  program="$(basename "$0")";

  if [ "${1--}" != "-" ]; then
    echo "$@";
    echo;
  fi;

  echo "Usage: ${program} [-hvgsfnpdkrR] [-K key] [-iI dst] [-t type] [-S statsdirectory] [-P plugin]";
  echo "Options:";
  echo "	-h  Print this help and exit";
  echo "	-v  Be verbose";
  echo "	-g  Get dependencies only; don't run setup or run the server.";
  echo "	-s  Run setup only; don't run server";
  echo "	-f  Force setup to run";
  echo "	-n  Do not run setup";
  echo "	-p  Print PYTHONPATH value for server and exit";
  echo "	-d  Run caldavd as a daemon";
  echo "	-k  Stop caldavd";
  echo "	-r  Restart caldavd";
  echo "	-K  Print value of configuration key and exit";
  echo "	-i  Perform a system install into dst; implies -s";
  echo "	-I  Perform a home install into dst; implies -s";
  echo "	-t  Select the server process type (Master, Slave or Combined) [${service_type}]";
  echo "	-S  Write a pstats object for each process to the given directory when the server is stopped.";
  echo "	-P  Select the twistd plugin name [${plugin_name}]";
  echo "	-R  Twisted Reactor plugin to execute [${reactor}]";

  if [ "${1-}" == "-" ]; then
    return 0;
  fi;
  exit 64;
}

# Provide a default value: if the variable named by the first argument is
# empty, set it to the default in the second argument.
conditional_set () {
  local var="$1"; shift;
  local default="$1"; shift;
  if [ -z "$(eval echo "\${${var}:-}")" ]; then
    eval "${var}=\${default:-}";
  fi;
}

parse_run_options () {
  while getopts 'hvgsfnpdkrK:i:I:t:S:P:R:' option; do
    case "$option" in
      '?') usage; ;;
      'h') usage -; exit 0; ;;
      'v')       verbose="-v"; ;;
      'f')   force_setup="true"; ;;
      'k')          kill="true"; ;;
      'r')       restart="true"; ;;
      'd')     daemonize=""; ;;
      'P')   plugin_name="${OPTARG}"; ;;
      'R')       reactor="-R ${OPTARG}"; ;;
      't')  service_type="${OPTARG}"; ;;
      'K')      read_key="${OPTARG}"; ;;
      'S')       profile="--profiler cprofile -p ${OPTARG}"; ;;
      'g') do_get="true" ; do_setup="false"; do_run="false"; ;;
      's') do_get="true" ; do_setup="true" ; do_run="false"; ;;
      'p') do_get="false"; do_setup="false"; do_run="false"; print_path="true"; ;;
      'i') do_get="true" ; do_setup="true" ; do_run="false"; install="${OPTARG}"; install_flag="--root="; ;;
      'I') do_get="true" ; do_setup="true" ; do_run="false"; install="${wd}/build/dst"; install_flag="--root="; install_home="${OPTARG}"; ;;
    esac;
  done;
  shift $((${OPTIND} - 1));
  if [ $# != 0 ]; then
    usage "Unrecognized arguments:" "$@";
  fi;
}

conf_read_key ()
{
  local key="$1"; shift;

  # FIXME: This only works for simple values (no arrays, dicts)
  tr '\n' ' ' < "${config}"                                                 \
    | xpath "/plist/dict/*[preceding-sibling::key[1]='${key}'" 2> /dev/null \
    | sed -n 's|^<[^<][^<]*>\([^<]*\)</[^<][^<]*>.*$|\1|p';
}

# Initialize all the global state required to use this library.
init_runlib () {

        verbose="";
         do_get="true";
       do_setup="true";
         do_run="true";
    force_setup="false";
  disable_setup="false";
     print_path="false";
        install="";
      daemonize="-X";
           kill="false";
        restart="false";
    plugin_name="caldav";
   service_type="Combined";
       read_key="";
        profile="";
        reactor="";

  # These variables are defaults for things which might be configured by
  # environment; only set them if they're un-set.
  conditional_set wd "$(pwd)";
  conditional_set config "${wd}/conf/caldavd-dev.plist";
  conditional_set caldav "${wd}";

  if [ -z "${CALENDARSERVER_CACHE_DEPS-}" ]; then
    cache_deps="${wd}/.dependencies";
  else
    cache_deps="${CALENDARSERVER_CACHE_DEPS}";
  fi;

  if [ -z "${caldavd_wrapper_command:-}" ]; then
    if [ "$(uname -s)" == "Darwin" ] && [ "$(uname -r | cut -d . -f 1)" -ge 9 ]; then
      caldavd_wrapper_command="launchctl bsexec /";
    else
      caldavd_wrapper_command="";
    fi;
  fi;

      top="$(cd "${caldav}/.." && pwd -L)";
  patches="${caldav}/lib-patches";
  twisted="${top}/Twisted";
      dav="${twisted}/twisted/web2/dav";

  # Find a command that can hash up a string for us
  if type -t openssl > /dev/null; then
    hash="hash";
    hash () { openssl dgst -md5; }
  elif type -t md5 > /dev/null; then
    hash="md5";
  elif type -t md5sum > /dev/null; then
    hash="md5sum";
  elif type -t cksum > /dev/null; then
    hash="hash";
    hash () { cksum | cut -f 1 -d " "; }
  elif type -t sum > /dev/null; then
    hash="hash";
    hash () { sum | cut -f 1 -d " "; }
  else
    hash="";
  fi;

  if [ -n "${install}" ] && ! echo "${install}" | grep '^/' > /dev/null; then
    install="$(pwd)/${install}";
  fi;

  svn_uri_base="$(svn info "${caldav}" --xml 2> /dev/null | sed -n 's|^.*<root>\(.*\)</root>.*$|\1|p')";

  conditional_set svn_uri_base "http://svn.calendarserver.org/repository/calendarserver";
}

# The main-point of the 'run' script: parse all options, decide what to do,
# then do it.

run_main () {
  parse_run_options "$@"

  # If we've been asked to read a configuration key, just read it and exit.
  if [ -n "${read_key}" ]; then
    conf_read_key "${read_key}";
    exit $?;
  fi;

  if "${kill}" || "${restart}"; then
    pidfile="$(conf_read_key "PIDFile")";
    if [ ! -r "${pidfile}" ]; then
      echo "Unreadable PID file: ${pidfile}";
      exit 1
    fi;
    pid="$(cat "${pidfile}" | head -1)";
    if [ -z "${pid}" ]; then
      echo "No PID in PID file: ${pidfile}";
      exit 1;
    fi;
    echo "Killing process ${pid}";
    kill -TERM "${pid}";
    if ! "${restart}"; then
      exit 0;
    fi;
  fi;

  # Prepare to set up PYTHONPATH et. al.
  if [ -z "${PYTHONPATH:-}" ]; then
    user_python_path="";
  else
    user_python_path=":${PYTHONPATH}";
  fi;

  export PYTHONPATH="${caldav}";

  # About to do something for real; let's enumerate (and depending on options,
  # possibly download and/or install) the dependencies.
  dependencies;

  # If we're installing, install the calendar server itself.
  py_install "Calendar Server" "${caldav}";

  if [ -n "${install_home:-}" ]; then
    do_home_install;
  fi;

  # Finish setting up PYTHONPATH (put the user's original path at the *end* so
  # that CalendarServer takes precedence).

  export PYTHONPATH="${PYTHONPATH}${user_python_path}";

  # Finally, run the server.
  run;
}


# This is a hack, but it's needed because installing with --home doesn't work
# for python-dateutil.
do_home_install () {
  install -d "${install_home}";
  install -d "${install_home}/bin";
  install -d "${install_home}/conf";
  install -d "${install_home}/lib/python";

  rsync -av "${install}${py_prefix}/bin/" "${install_home}/bin/";
  rsync -av "${install}${py_libdir}/" "${install_home}/lib/python/";
  rsync -av "${install}${py_prefix}/caldavd/" "${install_home}/caldavd/";

  rm -rf "${install}";
}


apply_patches () {
  local name="$1"; shift;
  local path="$1"; shift;

  if [ -d "${patches}/${name}" ]; then
    echo "";
    echo "Applying patches to ${name} in ${path}...";

    cd "${path}";
    find "${patches}/${name}"                  \
        -type f                                \
        -name '*.patch'                        \
        -print                                 \
        -exec patch -p0 --forward -i '{}' ';';
    cd /;

  fi;

  echo "";
  echo "Removing build directory ${path}/build...";
  rm -rf "${path}/build";
  echo "Removing pyc files from ${path}...";
  find "${path}" -type f -name '*.pyc' -print0 | xargs -0 rm -f;
}


www_get () {
  if ! "${do_get}"; then return 0; fi;

  local name="$1"; shift;
  local path="$1"; shift;
  local  url="$1"; shift;

  if "${force_setup}" || [ ! -d "${path}" ]; then
    local ext="$(echo "${url}" | sed 's|^.*\.\([^.]*\)$|\1|')";

    case "${ext}" in
      gz|tgz) decompress="gzip -d -c"; ;;
      bz2)    decompress="bzip2 -d -c"; ;;
      tar)    decompress="cat"; ;;
      *)
        echo "Unknown extension: ${ext}";
        exit 1;
        ;;
    esac;

    echo "";

    if [ -n "${cache_deps}" ] && [ -n "${hash}" ]; then
      mkdir -p "${cache_deps}";

      cache_file="${cache_deps}/${name}-$(echo "${url}" | "${hash}")-$(basename "${url}")";

      if [ ! -f "${cache_file}" ]; then
	echo "Downloading ${name}...";
	curl -L "${url}" -o "${cache_file}";
      fi;

      echo "Unpacking ${name} from cache...";
      get () { cat "${cache_file}"; }
    else
      echo "Downloading ${name}...";
      get () { curl -L "${url}"; }
    fi;

    rm -rf "${path}";
    cd "$(dirname "${path}")";
    get | ${decompress} | tar -xvf -;
    apply_patches "${name}" "${path}";
    cd /;
  fi;
}


# If do_get is turned on, check a name out from SVN.
svn_get () {
  if ! "${do_get}"; then
    return 0;
  fi;

  local     name="$1"; shift;
  local     path="$1"; shift;
  local      uri="$1"; shift;
  local revision="$1"; shift;

  if [ -d "${path}" ]; then
    local wc_uri="$(svn info --xml "${path}" 2> /dev/null | sed -n 's|^.*<url>\(.*\)</url>.*$|\1|p')";

    if "${force_setup}"; then
      # Verify that we have a working copy checked out from the correct URI
      if [ "${wc_uri}" != "${uri}" ]; then
        echo "";
        echo "Current working copy (${path}) is from the wrong URI: ${wc_uri} != ${uri}";
        rm -rf "${path}";
        svn_get "${name}" "${path}" "${uri}" "${revision}";
        return $?;
      fi;

      echo "";

      echo "Reverting ${name}...";
      svn revert -R "${path}";

      echo "Updating ${name}...";
      svn update -r "${revision}" "${path}";

      apply_patches "${name}" "${path}";
    else
      if ! "${print_path}"; then
        # Verify that we have a working copy checked out from the correct URI
        if [ "${wc_uri}" != "${uri}" ]; then
          echo "";
          echo "Current working copy (${path}) is from the wrong URI: ${wc_uri} != ${uri}";
          echo "Performing repository switch for ${name}...";
          svn switch -r "${revision}" "${uri}" "${path}";

          apply_patches "${name}" "${path}";
        else
          local svnversion="$(svnversion "${path}")";
          if [ "${svnversion%%[M:]*}" != "${revision}" ]; then
            echo "";
            echo "Updating ${name}...";
            svn update -r "${revision}" "${path}";

            apply_patches "${name}" "${path}";
          fi;
        fi;
      fi;
    fi;
  else
    echo "";

    checkout () {
      echo "Checking out ${name}...";
      svn checkout -r "${revision}" "${uri}@${revision}" "${path}";
    }

    if [ "${revision}" != "HEAD" ] && [ -n "${cache_deps}" ] && [ -n "${hash}" ]; then
      local cache_file="${cache_deps}/${name}-$(echo "${uri}" | "${hash}")@r${revision}.tgz";

      mkdir -p "${cache_deps}";

      if [ -f "${cache_file}" ]; then
	echo "Unpacking ${name} from cache...";
	mkdir -p "${path}";
	tar -C "${path}" -xvzf "${cache_file}";
      else
	checkout;
	echo "Caching ${name}...";
	tar -C "${path}" -cvzf "${cache_file}" .;
      fi;
    else
      checkout;
    fi;

    apply_patches "${name}" "${path}";
  fi;
}


run () {
  if "${print_path}"; then
    echo "${PYTHONPATH}";
    exit 0;
  fi;

  echo "";
  echo "Using ${python} as Python";

  if "${do_run}"; then
    if [ ! -f "${config}" ]; then
      echo "";
      echo "Missing config file: ${config}";
      echo "You might want to start by copying the test configuration:";
      echo "";
      echo "  cp conf/caldavd-test.plist conf/caldavd-dev.plist";
      echo "";
      exit 1;
    fi;

    cd "${wd}";
    if [ ! -d "${wd}/logs" ]; then
      mkdir "${wd}/logs";
    fi;

    echo "";
    echo "Starting server...";
    exec ${caldavd_wrapper_command}                   \
        "${caldav}/bin/caldavd" ${daemonize}          \
        -f "${config}"                                \
        -T "${twisted}/bin/twistd"                    \
        -P "${plugin_name}"                           \
        -t "${service_type}"                          \
        ${reactor}                                    \
        ${profile};
    cd /;
  fi;
}


py_build () {
  local     name="$1"; shift;
  local     path="$1"; shift;
  local optional="$1"; shift;

  if "${do_setup}"; then
    echo "";
    echo "Building ${name}...";
    cd "${path}";
    if ! "${python}" ./setup.py -q build --build-lib "build/${py_platform_libdir}" "$@"; then
      if "${optional}"; then
        echo "WARNING: ${name} failed to build.";
        echo "WARNING: ${name} is not required to run the server; continuing without it.";
      else
        return $?;
      fi;
    fi;
    cd /;
  fi;
}

py_install () {
  local name="$1"; shift;
  local path="$1"; shift;

  if [ -n "${install}" ]; then
    echo "";
    echo "Installing ${name}...";
    cd "${path}";
    "${python}" ./setup.py install "${install_flag}${install}";
    cd /;
  fi;
}


# Declare a dependency on a Python project.

py_dependency () {

  # args
  local            name="$1"; shift; # the name of the package (for display)
  local          module="$1"; shift; # the name of the python module.
  local    distribution="$1"; shift; # the name of the directory to put the distribution into.
  local        get_type="$1"; shift; # what protocol should be used?
  local         get_uri="$1"; shift; # what URL should be fetched?
  local        optional="$1"; shift; # is this dependency optional?
  local override_system="$1"; shift; # do I need to get this dependency even if
                                     # the system already has it?
  local         inplace="$1"; shift; # do development in-place; don't run setup.py to
                                     # build, and instead add the source directory
                                     # directly to sys.path.  twisted and vobject are
                                     # developed often enough that this is convenient.
  local        skip_egg="$1"; shift; # skip even the 'egg_info' step, because nothing
                                     # needs to be built.
  local        revision="$1"; shift; # what revision to check out (for SVN dependencies)
  # end args
  local          srcdir="${top}/${distribution}"

  if "${override_system}" || ! py_have_module "${module}"; then
    "${get_type}_get" "${name}" "${srcdir}" "${get_uri}" "${revision}"
    if "${inplace}"; then
      if "${override_system}" && ! "${skip_egg}"; then
        echo;
        echo "${name} overrides system, building egg only";
        cd "${srcdir}";
        "${python}" ./setup.py egg_info;
        cd /;
      fi;
    else
      py_build "${name}" "${srcdir}" "${optional}";
    fi;
    py_install "${name}" "${srcdir}";
  fi;

  if "$inplace"; then
    local  add_path="${srcdir}";
  else
    local  add_path="${srcdir}/build/${py_platform_libdir}";
  fi;
  export PYTHONPATH="${PYTHONPATH}:${add_path}";
}


# Declare a dependency on a C project built with autotools.

c_dependency () {
  local name="$1"; shift;
  local path="$1"; shift;
  local  uri="$1"; shift;

  # Extra arguments are processed below, as arguments to './configure'.

  srcdir="${top}/${path}";

  www_get "${name}" "${srcdir}" "${uri}";

  if "${do_setup}" && (
      "${force_setup}" || [ ! -d "${srcdir}/_root" ]); then
    echo "";
    echo "Building ${name}...";
    cd "${srcdir}";
    ./configure --prefix="${srcdir}/_root" "$@";
    make;
    make install;
  fi;

  export              PATH="${PATH}:${srcdir}/_root/bin";
  export    C_INCLUDE_PATH="${C_INCLUDE_PATH:-}:${srcdir}/_root/include";
  export   LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:${srcdir}/_root/lib";
  export DYLD_LIBRARY_PATH="${DYLD_LIBRARY_PATH:-}:${srcdir}/_root/lib";
}


# Enumerate all the dependencies with c_dependency and py_dependency; depending
# on options parsed by parse_run_options and on-disk state, this may do as
# little as update the PATH, DYLD_LIBRARY_PATH, LD_LIBRARY_PATH and PYTHONPATH,
# or, it may do as much as download and install all dependencies.

dependencies () {

  # Dependencies compiled from C source code
  local le="libevent-1.4.8-stable";
  c_dependency "libevent" "${le}" \
    "http://monkey.org/~provos/libevent-1.4.8-stable.tar.gz";
  c_dependency "memcached" "memcached-1.2.6" \
    "http://www.danga.com/memcached/dist/memcached-1.2.6.tar.gz" \
    --enable-threads --with-libevent="${top}/${le}/_root";

  # Python dependencies
  py_dependency "Zope Interface" "zope.interface" "zope.interface-3.3.0" \
    "www" "http://www.zope.org/Products/ZopeInterface/3.3.0/zope.interface-3.3.0.tar.gz" \
    false false false false 0;
  py_dependency "PyXML" "xml.dom.ext" "PyXML-0.8.4" \
    "www" "http://internap.dl.sourceforge.net/sourceforge/pyxml/PyXML-0.8.4.tar.gz" \
    false false false false 0;
  py_dependency "PyOpenSSL" "OpenSSL" "pyOpenSSL-0.7" \
    "www" "http://pypi.python.org/packages/source/p/pyOpenSSL/pyOpenSSL-0.7.tar.gz" \
    false false false false 0;
  if type krb5-config > /dev/null; then
    py_dependency "PyKerberos" "kerberos" "PyKerberos" \
      "svn" "${svn_uri_base}/PyKerberos/trunk" \
      false false false false 4241;
  fi;
  if [ "$(uname -s)" == "Darwin" ]; then
    py_dependency "PyOpenDirectory" "opendirectory" "PyOpenDirectory" \
      "svn" "${svn_uri_base}/PyOpenDirectory/trunk" \
      false false false false 4106;
  fi;
  py_dependency "xattr" "xattr" "xattr" \
    "svn" "http://svn.red-bean.com/bob/xattr/releases/xattr-0.5" \
    false false false false 1013;
  if [ "${py_version}" != "${py_version##2.5}" ] && ! py_have_module select26; then
    py_dependency "select26" "select26" "select26" \
      "www" "http://pypi.python.org/packages/source/s/select26/select26-0.1a3.tar.gz" \
      true false false false 0;
  fi;

  case "${USER}" in
    wsanchez)
      proto="svn+ssh";
      ;;
    *)
      proto="svn";
      ;;
  esac;

  py_dependency "Twisted" "twisted" "Twisted" \
    "svn" "${proto}://svn.twistedmatrix.com/svn/Twisted/branches/dav-take-two-3081-4" \
    false true true false 26969;

  # twisted.web2 doesn't get installed by default, so in the install phase
  # let's make sure it does.
  if [ -n "${install}" ]; then
    echo "";
    echo "Installing Twisted.web2...";
    cd "${twisted}";
    "${python}" ./twisted/web2/topfiles/setup.py install "${install_flag}${install}";
    cd /;
  fi;

  py_dependency "dateutil" "dateutil" "python-dateutil-1.4.1" \
    "www" "http://www.labix.org/download/python-dateutil/python-dateutil-1.4.1.tar.gz" \
    false false false false 0;

  case "${USER}" in
    cyrusdaboo)
      base="svn+ssh://cdaboo@svn.osafoundation.org/svn";
      ;;
    *)
      base="http://svn.osafoundation.org";
      ;;
  esac;

  # XXX actually vObject should be imported in-place.
  py_dependency "vObject" "vobject" "vobject" \
    "svn" "${base}/vobject/trunk" \
    false true true true 212;

  py_dependency "PyDirector" "pydirector" "pydirector-1.0.0" \
    "www" http://internap.dl.sourceforge.net/sourceforge/pythondirector/pydirector-1.0.0.tar.gz \
    false false false false 0;

  # Tool dependencies.  The code itself doesn't depend on these, but you probably want them.
  svn_get "CalDAVTester" "${top}/CalDAVTester" "${svn_uri_base}/CalDAVTester/trunk" 4517;
  svn_get "Pyflakes" "${top}/Pyflakes" http://divmod.org/svn/Divmod/trunk/Pyflakes 17198;
  echo 'Dependencies done.';
}


# Actually do the initialization, once all functions are defined.
init_runlib;
