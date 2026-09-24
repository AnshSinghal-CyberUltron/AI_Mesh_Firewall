#!/usr/bin/env bash
# sut_step.sh start <run_id> | stop <run_id>
# Runs ON the SUT. start: launches the cgroup sampler (1 s) + /health probe (5 Hz) and snapshots
# gateway state; stop: kills them, snapshots again, and captures gateway/control log lines of the
# window (errors, worker boots/timeouts) for the run.
set -euo pipefail
cmd=$1; run=$2
D=$HOME/runs/$run; mkdir -p "$D"
snap() {  # snap <tag>
  {
    echo "## $1 $(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"
    sudo docker ps --format '{{.Names}} {{.Status}} {{.Image}}'
    sudo docker inspect -f '{{.Name}} restarts={{.RestartCount}} started={{.State.StartedAt}}' $(sudo docker ps -q)
    sudo docker exec aimeshperf-redis-1 redis-cli info stats | grep -E 'total_commands_processed|instantaneous_ops_per_sec|rejected_connections'
    sudo docker exec aimeshperf-redis-1 redis-cli info clients | grep -E 'connected_clients|blocked_clients'
    sudo docker exec aimeshperf-redis-1 redis-cli info memory | grep -E '^used_memory_human'
    cat /proc/loadavg
  } >> "$D/snap.txt" 2>&1
}
case "$cmd" in
start)
  date -u +%s.%N > "$D/start_wall"
  snap start
  sudo nohup python3 "$HOME/sut_sampler.py" --out "$D/sampler.jsonl" --probe-out "$D/probe.jsonl" --probe-hz 5 \
      > "$D/sampler.log" 2>&1 < /dev/null &
  echo $! > "$D/sampler.sudo.pid"
  ;;
stop)
  sudo pkill -f "[s]ut_sampler.py --out $D/" || true
  date -u +%s.%N > "$D/stop_wall"
  snap stop
  since=$(date -u -d @"$(cut -d. -f1 "$D/start_wall")" +%Y-%m-%dT%H:%M:%SZ)
  sudo docker logs --since "$since" aimeshperf-gateway-1 > "$D/gateway.log" 2>&1 || true
  sudo docker logs --since "$since" aimeshperf-control-1 > "$D/control.log" 2>&1 || true
  {
    echo "gateway_lines $(wc -l < "$D/gateway.log")"
    echo "gateway_error_lines $(grep -ciE ' (ERROR|CRITICAL) |Traceback' "$D/gateway.log" || true)"
    echo "gateway_warning_lines $(grep -ciE ' WARNING ' "$D/gateway.log" || true)"
    echo "worker_timeouts $(grep -ci 'WORKER TIMEOUT' "$D/gateway.log" || true)"
    echo "worker_boots $(grep -ci 'Booting worker' "$D/gateway.log" || true)"
    echo "control_error_lines $(grep -ciE ' (ERROR|CRITICAL) |Traceback' "$D/control.log" || true)"
  } > "$D/logsummary.txt"
  sudo chown -R "$USER" "$D"
  cat "$D/logsummary.txt"
  ;;
esac
