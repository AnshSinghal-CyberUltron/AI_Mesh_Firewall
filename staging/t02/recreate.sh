#!/usr/bin/env bash
# L02-1: destroy-and-recreate from the SAME images (no rebuild, no source bind mounts).
set -euo pipefail
REPO="${REPO:-/home/contact_cyberultron_com/AI_Mesh_Firewall}"
cd "$REPO"
SECRETS=/tmp/t02-secrets.env
set -a
# shellcheck disable=SC1090
source "$SECRETS"
set +a
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.override.yml -f docker-compose.t02.yml)
OUT="${REPO}/docs/plans/evidence/2026-09-17-t02/l02_1_recreate.json"
before=$("${COMPOSE[@]}" images -q | sort)
echo "[t02] recreate down"
"${COMPOSE[@]}" down --remove-orphans
echo "[t02] recreate up --no-build"
"${COMPOSE[@]}" up -d --no-build postgres redis pgbouncer rabbitmq control gateway t02-recorder demo mcp-stub frontend
for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8100/api/health/ >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:8300/health >/dev/null 2>&1; then
    break
  fi
  sleep 3
done
after=$("${COMPOSE[@]}" images -q | sort)
python3 - "$OUT" <<'PY'
import json, os, subprocess, sys, time
from pathlib import Path
out = Path(sys.argv[1])
repo = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall")
names = [
    "ai_mesh_firewall-gateway-1",
    "ai_mesh_firewall-control-1",
    "ai_mesh_firewall-frontend-1",
    "ai_mesh_firewall-t02-recorder-1",
]
def inspect(name):
    p = subprocess.run(["docker","inspect",name], capture_output=True, text=True)
    if p.returncode != 0:
        return {"error": p.stderr[-400:]}
    data = json.loads(p.stdout)[0]
    mounts = data.get("Mounts") or []
    binds = []
    for m in mounts:
        src = str(m.get("Source") or "")
        if str(repo) in src:
            binds.append({"source": src, "destination": m.get("Destination")})
    return {
        "image": data.get("Image"),
        "image_name": data.get("Config", {}).get("Image"),
        "repo_bind_mounts": binds,
        "created": data.get("Created"),
    }
report = {
    "task": "T02-L02-1",
    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "containers": {n: inspect(n) for n in names},
}
report["any_repo_bind_mounts"] = any(v.get("repo_bind_mounts") for v in report["containers"].values())
out.write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"path": str(out), "any_repo_bind_mounts": report["any_repo_bind_mounts"]}))
PY
