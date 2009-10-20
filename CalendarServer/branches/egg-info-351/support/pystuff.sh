# -*- sh-basic-offset: 2 -*-

# Echo the major.minor version of the given Python interpreter.

py_version () {
  local python="$1"; shift;
  echo "$("${python}" -c "from distutils.sysconfig import get_python_version; print get_python_version()")";
}

# Test if a particular python interpreter is available, given the full path to
# that interpreter.

try_python () {
  local python="$1"; shift;

  if [ -z "${python}" ]
  then
    return 1
  fi
  if ! type "${python}" > /dev/null 2>&1
  then
    return 1
  fi

  local py_version="$(py_version "${python}")"
  if [ "${py_version/./}" -lt "25" ]
  then
    return 1
  fi
  return 0
}


# Detect which version of Python to use, then print out which one was detected.

detect_python_version () {
  for v in "" "2.6" "2.5"
  do
    for p in								\
      "${PYTHON:=}"							\
      "python${v}"							\
      "/usr/local/bin/python${v}"					\
      "/usr/local/python/bin/python${v}"				\
      "/usr/local/python${v}/bin/python${v}"				\
      "/opt/bin/python${v}"						\
      "/opt/python/bin/python${v}"					\
      "/opt/python${v}/bin/python${v}"					\
      "/Library/Frameworks/Python.framework/Versions/${v}/bin/python"	\
      "/opt/local/bin/python${v}"					\
      "/sw/bin/python${v}"						\
      ;
    do
      if try_python "${p}"
      then
        echo "${p}"
        return 0
      fi
    done
  done
  return 1
}

# Detect if the given Python module is installed in the system Python configuration.
py_have_module () {
    local module="$1"; shift;
    PYTHONPATH="" "${python}" -c "import ${module}" > /dev/null 2>&1;
}

# Detect which python to use, and store it in the 'python' variable, as well as
# setting up variables related to version and build configuration.

init_pystuff () {
  python="$(detect_python_version)"

  if [ -z "${python:-}" ]
  then
    echo "No suitable python found. Python 2.5 is required."
    exit 1
  else
      echo "Using ${python} as Python";
  fi

  py_platform="$("${python}" -c "from distutils.util import get_platform; print get_platform()")";
  py_version="$(py_version "${python}")";
  py_platform_libdir="lib.${py_platform}-${py_version}";
  py_prefix="$("${python}" -c "import sys; print sys.prefix;")";
  py_libdir="$("${python}" -c "from distutils.sysconfig import get_python_lib; print get_python_lib(1);")";
}

init_pystuff
