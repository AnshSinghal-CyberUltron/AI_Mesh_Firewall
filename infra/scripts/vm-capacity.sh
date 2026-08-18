#!/usr/bin/env bash
# Saturate this build VM for Docker/Compose/npm (16c / ~60GiB / high-IOPS).
# Source from Makefile targets and build scripts — never requires args.
#
#   source infra/scripts/vm-capacity.sh
#   aim_capacity_export
#   aim_capacity_print
#
# Override: AIM_BUILD_PARALLEL=8  BUILDKIT_MAX_PARALLELISM=8  COMPOSE_PARALLEL_LIMIT=4

_aim_nproc() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
  else
    echo 4
  fi
}

aim_capacity_export() {
  local n
  n="$(_aim_nproc)"
  export NPROC="${NPROC:-$n}"
  export GOMAXPROCS="${GOMAXPROCS:-$NPROC}"
  export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
  export COMPOSE_DOCKER_CLI_BUILD="${COMPOSE_DOCKER_CLI_BUILD:-1}"
  export BUILDKIT_PROGRESS="${BUILDKIT_PROGRESS:-plain}"
  export BUILDKIT_MAX_PARALLELISM="${BUILDKIT_MAX_PARALLELISM:-$NPROC}"
  export BUILDKIT_STEP_LOG_MAX_SIZE="${BUILDKIT_STEP_LOG_MAX_SIZE:-10485760}"
  # Concurrent compose service builds
  export COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-$NPROC}"
  # How many image builds we launch as background jobs (ECR / local prod images)
  export AIM_BUILD_PARALLEL="${AIM_BUILD_PARALLEL:-$NPROC}"
  # Node/Vite — use all cores for frontend production builds
  export UV_THREADPOOL_SIZE="${UV_THREADPOOL_SIZE:-$NPROC}"
  export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=8192}"
  # npm ci / install parallelism
  export npm_config_jobs="${npm_config_jobs:-$NPROC}"
  # Prefer host network for build-time pip/npm DNS (bridge DNS flakes on this VM)
  export AIM_DOCKER_NETWORK="${AIM_DOCKER_NETWORK:-host}"
  # buildx builder name (docker-container driver = better concurrent builds)
  export AIM_BUILDX_BUILDER="${AIM_BUILDX_BUILDER:-aim-fast}"
}

aim_capacity_print() {
  aim_capacity_export
  echo "=== AI Mesh VM capacity ==="
  echo "nproc=$NPROC  GOMAXPROCS=$GOMAXPROCS  BUILDKIT_MAX_PARALLELISM=$BUILDKIT_MAX_PARALLELISM"
  echo "COMPOSE_PARALLEL_LIMIT=$COMPOSE_PARALLEL_LIMIT  AIM_BUILD_PARALLEL=$AIM_BUILD_PARALLEL"
  echo "DOCKER_BUILDKIT=$DOCKER_BUILDKIT  AIM_DOCKER_NETWORK=$AIM_DOCKER_NETWORK"
  echo "builder=$AIM_BUILDX_BUILDER"
  if command -v free >/dev/null 2>&1; then
    free -h | head -2
  fi
  if command -v df >/dev/null 2>&1; then
    df -h / | tail -1
  fi
}

# Ensure a high-parallelism buildx builder (network=host for pip/npm DNS).
aim_ensure_buildx_builder() {
  aim_capacity_export
  local builder="${1:-$AIM_BUILDX_BUILDER}"
  if ! docker buildx inspect "$builder" >/dev/null 2>&1; then
    echo "==> creating buildx builder '$builder' (docker-container, network=host)"
    docker buildx create --name "$builder" --driver docker-container \
      --driver-opt "env.BUILDKIT_STEP_LOG_MAX_SIZE=${BUILDKIT_STEP_LOG_MAX_SIZE}" \
      --driver-opt "network=host" \
      --buildkitd-flags "--allow-insecure-entitlement network.host" \
      --use
  else
    docker buildx use "$builder"
  fi
  docker buildx inspect --bootstrap >/dev/null 2>&1 || true
}

# Wait on a list of background PIDs with a 30s progress ticker.
# Usage: aim_wait_pids pids_array_name names_array_name
# Sets AIM_WAIT_FAIL=1 if any child failed.
aim_wait_pids() {
  local -n _pids=$1
  local -n _names=$2
  local fail=0
  local remaining=("${_pids[@]}")
  declare -A pid_name=()
  local i
  for i in "${!_pids[@]}"; do
    pid_name[${_pids[$i]}]=${_names[$i]}
  done

  while ((${#remaining[@]})); do
    local still=()
    local pid
    for pid in "${remaining[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        still+=("$pid")
      else
        local name="${pid_name[$pid]}"
        if wait "$pid"; then
          echo "BUILD OK  $name (pid=$pid) $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        else
          echo "BUILD FAIL $name (pid=$pid) $(date -u +%Y-%m-%dT%H:%M:%SZ) — see /tmp/aim-build-${name}.log"
          fail=1
        fi
      fi
    done
    remaining=("${still[@]}")
    if ((${#remaining[@]})); then
      echo "… still running: $(for p in "${remaining[@]}"; do printf '%s ' "${pid_name[$p]}"; done) $(date -u +%Y-%m-%dT%H:%M:%SZ)"
      if command -v free >/dev/null 2>&1; then
        free -h | awk '/Mem:/{print "  mem avail="$7}'
      fi
      sleep 30
    fi
  done
  AIM_WAIT_FAIL=$fail
  return "$fail"
}
