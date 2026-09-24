#!/usr/bin/env bash
# pk.sh <name> <ext> : kill processes whose command line contains "<name>.<ext>". The pattern is assembled here so
# the caller's own ssh command line (which only contains "<name> <ext>") can never match and kill the caller.
pat="$1\\.$2"
for p in $(pgrep -f "$pat"); do [ "$p" != "$$" ] && kill "$p" 2>/dev/null; done
sleep 1; pgrep -fa "$pat" || echo "none left"
