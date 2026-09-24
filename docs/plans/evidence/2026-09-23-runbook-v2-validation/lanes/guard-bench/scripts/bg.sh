#!/usr/bin/env bash
# bg.sh <logfile> <command string> : fully detached launch (returns immediately)
log=$1; shift
setsid -f bash -c "$*" > "$log" 2>&1 < /dev/null
echo "launched: $*"
