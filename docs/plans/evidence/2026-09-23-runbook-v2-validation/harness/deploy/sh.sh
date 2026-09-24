#!/usr/bin/env bash
# sh.sh NAME 'command'   run a shell command on a VM as rv
source "$(dirname "$0")/lib.sh"
name=$1; shift
rssh "$name" "$@"
