
usage ()
{
    program="$(basename "$0")";

    if [ "${1--}" != "-" ]; then echo "$@"; echo; fi;

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

    if [ "${1-}" == "-" ]
    then
        return 0
    fi
    exit 64
}
