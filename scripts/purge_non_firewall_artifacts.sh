#!/usr/bin/env bash
# Remove artifacts that must not ship in AI Mesh Firewall standalone SKU.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Purging non-firewall artifacts under $ROOT"

rm -f control/Dockerfile.monorepo gateway/Dockerfile.monorepo 2>/dev/null || true
rm -rf control/ai_mesh_control/core/static/agents 2>/dev/null || true

# Monorepo-only frontend (not copied, guard if present)
rm -f frontend/src/pages/UserManagement.jsx 2>/dev/null || true
rm -rf frontend/src/pages/aiguardx frontend/src/pages/behaviour_and_threat_intelligence frontend/src/pages/ai_infrastructure 2>/dev/null || true

echo "Purge complete."
