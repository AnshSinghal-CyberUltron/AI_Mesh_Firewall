#!/usr/bin/env bash
# push.sh FILE[,FILE...] NAME [NAME...]   copy corpora / extra files to ~/rv/corpora/ on each VM
source "$(dirname "$0")/lib.sh"
files=$1; shift
IFS=, read -ra F <<<"$files"
for n in "$@"; do rscp_to "$n" rv/corpora/ "${F[@]}" & done
wait
log "pushed ${F[*]} to $*"
