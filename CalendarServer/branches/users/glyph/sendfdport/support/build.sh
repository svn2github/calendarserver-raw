#! /bin/bash
# -*- sh-basic-offset: 2 -*-

##
# Copyright (c) 2005-2010 Apple Inc. All rights reserved.
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

. "${wd}/support/py.sh";

# Provide a default value: if the variable named by the first argument is
# empty, set it to the default in the second argument.
conditional_set () {
  local var="$1"; shift;
  local default="$1"; shift;
  if [ -z "$(eval echo "\${${var}:-}")" ]; then
    eval "${var}=\${default:-}";
  fi;
}

# Read a configuration key from the configuration plist file and print it to
# stdout.
conf_read_key ()
{
  local key="$1"; shift;

  # FIXME: This only works for simple values (no arrays, dicts)
  tr '\n' ' ' < "${config}"                                                 \
    | xpath "/plist/dict/*[preceding-sibling::key[1]='${key}'" 2> /dev/null \
    | sed -n 's|^<[^<][^<]*>\([^<]*\)</[^<][^<]*>.*$|\1|p';
}

# Initialize all the global state required to use this library.
init_build () {
        verbose="";
         do_get="true";
       do_setup="true";
         do_run="true";
    force_setup="false";
  disable_setup="false";
     print_path="false";
        install="";
      daemonize="-X -L";
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
  conditional_set config "${wd}/conf/${DAVD}davd-dev.plist";
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


# Apply patches from lib-patches to the given dependency codebase.
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


# If do_get is turned on, get an archive file containing a dependency via HTTP.
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
        echo "Current working copy (${path}) is from the wrong URI: ${wc_uri} != ${uri}";
        rm -rf "${path}";
        svn_get "${name}" "${path}" "${uri}" "${revision}";
        return $?;
      fi;

      echo "Reverting ${name}...";
      svn revert -R "${path}";

      echo "Updating ${name}...";
      svn update -r "${revision}" "${path}";

      apply_patches "${name}" "${path}";
    else
      if ! "${print_path}"; then
        # Verify that we have a working copy checked out from the correct URI
        if [ "${wc_uri}" != "${uri}" ]; then
          echo "Current working copy (${path}) is from the wrong URI: ${wc_uri} != ${uri}";
          echo "Performing repository switch for ${name}...";
          svn switch -r "${revision}" "${uri}" "${path}";

          apply_patches "${name}" "${path}";
        else
          local svnversion="$(svnversion "${path}")";
          if [ "${svnversion%%[M:]*}" != "${revision}" ]; then
            echo "Updating ${name}...";
            svn update -r "${revision}" "${path}";

            apply_patches "${name}" "${path}";
          fi;
        fi;
      fi;
    fi;
  else
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


# (optionally) Invoke 'python setup.py build' on the given python project.
py_build () {
  local     name="$1"; shift;
  local     path="$1"; shift;
  local optional="$1"; shift;

  if "${do_setup}"; then
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

# If in install mode, install the given package from the given path.
# (Otherwise do nothing.)
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
  local optional="false"; # Is this dependency optional?
  local override="false"; # Do I need to get this dependency even if
                          # the system already has it?
  local  inplace="false"; # Do development in-place; don't run
                          # setup.py to build, and instead add the
                          # source directory directly to sys.path.
                          # twisted and vobject are developed often
                          # enough that this is convenient.
  local skip_egg="false"; # Skip even the 'egg_info' step, because
                          # nothing needs to be built.
  local revision="0";     # Revision (if svn)
  local get_type="www";   # Protocol to use
  local  version="";      # Minimum version required

  OPTIND=1;
  while getopts "ofier:v:" option; do
    case "${option}" in
      'o') optional="true"; ;;
      'f') override="true"; ;;
      'i')  inplace="true"; ;;
      'e') skip_egg="true"; ;;
      'r') get_type="svn"; revision="${OPTARG}"; ;;
      'v')  version="-v ${OPTARG}"; ;;
    esac;
  done;
  shift $((${OPTIND} - 1));

  # args
  local         name="$1"; shift; # the name of the package (for display)
  local       module="$1"; shift; # the name of the python module.
  local distribution="$1"; shift; # the name of the directory to put the distribution into.
  local      get_uri="$1"; shift; # what URL should be fetched?

  local srcdir="${top}/${distribution}"

  if ! "${print_path}"; then
    echo "";
  fi;
  if "${override}" || ! py_have_module ${version} "${module}"; then
    "${get_type}_get" "${name}" "${srcdir}" "${get_uri}" "${revision}"
    if "${inplace}"; then
      if "${do_setup}" && "${override}" && ! "${skip_egg}"; then
        echo;
        if py_have_module setuptools; then
          echo "Building ${name}... [overrides system, building egg-info metadata only]";
          cd "${srcdir}";
          "${python}" ./setup.py -q egg_info 2>&1 | (
            grep -i -v 'Unrecognized .svn/entries' || true);
          cd /;
        fi;
      fi;
    else
      py_build "${name}" "${srcdir}" "${optional}";
    fi;
    py_install "${name}" "${srcdir}";

    if "$inplace"; then
      local add_path="${srcdir}";
    else
      local add_path="${srcdir}/build/${py_platform_libdir}";
    fi;
    export PYTHONPATH="${PYTHONPATH}:${add_path}";
  else
    if ! "${print_path}"; then
      echo "Using system version of ${name}.";
    fi;
  fi;
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


#
# Enumerate all the dependencies with c_dependency and py_dependency;
# depending on options parsed by ../run:parse_options and on-disk
# state, this may do as little as update the PATH, DYLD_LIBRARY_PATH,
# LD_LIBRARY_PATH and PYTHONPATH, or, it may do as much as download
# and install all dependencies.
#
dependencies () {

  #
  # Dependencies compiled from C source code
  #

  if ! type memcached > /dev/null 2>&1; then
    local le="libevent-1.4.8-stable";
    c_dependency "libevent" "${le}" \
      "http://monkey.org/~provos/libevent-1.4.8-stable.tar.gz";
    c_dependency "memcached" "memcached-1.2.6" \
      "http://www.danga.com/memcached/dist/memcached-1.2.6.tar.gz" \
      --enable-threads --with-libevent="${top}/${le}/_root";
  fi;

  if ! type postgres > /dev/null 2>&1; then
    c_dependency "PostgreSQL" "postgresql-8.4.2" \
      "http://ftp9.us.postgresql.org/pub/mirrors/postgresql/source/v8.4.2/postgresql-8.4.2.tar.gz" \
      --with-python;
    :;
  fi;

  #
  # Python dependencies
  #

  py_dependency \
    "Zope Interface" "zope.interface" "zope.interface-3.3.0" \
    "http://www.zope.org/Products/ZopeInterface/3.3.0/zope.interface-3.3.0.tar.gz";

  py_dependency \
    "PyXML" "xml.dom.ext" "PyXML-0.8.4" \
    "http://internap.dl.sourceforge.net/sourceforge/pyxml/PyXML-0.8.4.tar.gz";

  py_dependency -v 0.9 \
    "PyOpenSSL" "OpenSSL" "pyOpenSSL-0.9" \
    "http://pypi.python.org/packages/source/p/pyOpenSSL/pyOpenSSL-0.9.tar.gz";

  if type krb5-config > /dev/null 2>&1; then
    py_dependency -r 4241 \
      "PyKerberos" "kerberos" "PyKerberos" \
      "${svn_uri_base}/PyKerberos/trunk";
  fi;

  if [ "$(uname -s)" == "Darwin" ]; then
    py_dependency -r 4827 \
      "PyOpenDirectory" "opendirectory" "PyOpenDirectory" \
      "${svn_uri_base}/PyOpenDirectory/trunk";
  fi;

  py_dependency -v 0.5 -r 1013 \
    "xattr" "xattr" "xattr" \
    "http://svn.red-bean.com/bob/xattr/releases/xattr-0.5";

  if [ "${py_version}" != "${py_version##2.5}" ] && ! py_have_module select26; then
    py_dependency \
      "select26" "select26" "select26-0.1a3" \
      "http://pypi.python.org/packages/source/s/select26/select26-0.1a3.tar.gz";
  fi;

  py_dependency -v 4.0 \
    "PyGreSQL" "pgdb" "PyGreSQL-4.0" \
    "ftp://ftp.pygresql.org/pub/distrib/PyGreSQL.tgz";

  py_dependency -v 10 -r 28657 \
    "Twisted" "twisted" "Twisted" \
    "svn://svn.twistedmatrix.com/svn/Twisted/tags/releases/twisted-10.0.0";

  py_dependency \
    "dateutil" "dateutil" "python-dateutil-1.4.1" \
    "http://www.labix.org/download/python-dateutil/python-dateutil-1.4.1.tar.gz";

  # XXX actually vObject should be imported in-place.
  py_dependency -fie -r 219 \
    "vObject" "vobject" "vobject" \
    "http://svn.osafoundation.org/vobject/trunk";

  # Tool dependencies.  The code itself doesn't depend on these, but you probably want them.
  svn_get "CalDAVTester" "${top}/CalDAVTester" "${svn_uri_base}/CalDAVTester/trunk" HEAD;
  svn_get "Pyflakes" "${top}/Pyflakes" http://divmod.org/svn/Divmod/trunk/Pyflakes HEAD;

  if "${do_setup}"; then
    cd "${caldav}";
    echo "Building our own extension modules...";
    python setup.py build_ext --inplace;
  fi;
}

# Actually do the initialization, once all functions are defined.
init_build;
