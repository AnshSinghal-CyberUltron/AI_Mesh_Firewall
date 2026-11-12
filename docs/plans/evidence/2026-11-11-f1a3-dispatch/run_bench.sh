#!/usr/bin/env bash
# Run the F1a-3 dispatch benchmark: two local upstreams (plain + TLS) and the client, under the
# shared heavy-job lock. Usage: run_bench.sh <python> <out.json> [client args...]
# The bearer is a fresh random test value per run; proxy variables are removed so no traffic
# leaves the host.
set -euo pipefail
PY=${1:?python}; OUT=${2:?out}; shift 2
HERE=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap 'kill ${UP:-} ${UPT:-} 2>/dev/null || true; rm -rf "$TMP"' EXIT
export F1A3_BENCH_KEY="bench-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy
HOST=${BENCH_HOST:-172.17.0.1}
nice -n 10 "$PY" -I "$HERE/upstream.py" --host "$HOST" --port 18950 --chunks 20 >"$TMP/up.log" 2>&1 &
UP=$!
nice -n 10 "$PY" -I "$HERE/upstream.py" --host "$HOST" --port 18951 --chunks 20 --tls \
  --tls-ca-out "$TMP/ca.pem" >"$TMP/upt.log" 2>&1 &
UPT=$!
for _ in $(seq 50); do
  grep -q ready "$TMP/up.log" 2>/dev/null && grep -q ready "$TMP/upt.log" 2>/dev/null && break
  sleep 0.2
done
uptime >"$TMP/load_before.txt"
${BENCH_LOCK:-flock /home/claude/work/.heavy.lock} nice -n 10 "$PY" "$HERE/client_bench.py" --host "$HOST" \
  --port 18950 --tls-port 18951 --ca "$TMP/ca.pem" --upstream-pid "$UP" --tls-upstream-pid "$UPT" \
  "$@" >"$OUT"
echo "load before: $(cat "$TMP/load_before.txt")" >&2
echo "load after:  $(uptime)" >&2
STATS=$(curl -s --noproxy "*" "http://$HOST:18950/stats")
echo "plain upstream stats: $STATS" >&2
