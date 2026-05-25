#!/usr/bin/env bash
# Extract AI Mesh Firewall code from parent monorepo into this product tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(cd "$ROOT/.." && pwd)"

echo "Extracting from $PARENT into $ROOT"

# --- shared ---
mkdir -p "$ROOT/shared/ai_mesh_shared"
rsync -a --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$PARENT/shared/" "$ROOT/shared/ai_mesh_shared/"
# Preserve scaffold queues if needed
[ -f "$ROOT/shared/ai_mesh_shared/jobs/queues.py" ] || true

# --- gateway (data plane) ---
mkdir -p "$ROOT/gateway/ai_mesh_gateway"
rsync -a \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'uv.lock' \
  --exclude 'agent_proxy.py' --exclude '_test_guardrails.py' \
  "$PARENT/gateway/" "$ROOT/gateway/ai_mesh_gateway/"
rsync -a "$PARENT/gateway/pyproject.toml" "$ROOT/gateway/pyproject.toml"
rsync -a "$PARENT/gateway/litellm_config.yaml" "$ROOT/gateway/" 2>/dev/null || true
rsync -a "$PARENT/gateway/Dockerfile" "$ROOT/gateway/Dockerfile.monorepo" 2>/dev/null || true

# --- control plane apps ---
APPS=(auth policy core security_engines ws mcp_connector)
for app in "${APPS[@]}"; do
  mkdir -p "$ROOT/control/ai_mesh_control/$app"
  rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests.py' \
    "$PARENT/backend/$app/" "$ROOT/control/ai_mesh_control/$app/"
done

# core: exclude agent distribution files
rm -f "$ROOT/control/ai_mesh_control/core/agent_urls.py" \
      "$ROOT/control/ai_mesh_control/core/agent_views.py" \
      "$ROOT/control/ai_mesh_control/core/pkg_builder.py" \
      "$ROOT/control/ai_mesh_control/core/msi_builder.py" \
      "$ROOT/control/ai_mesh_control/core/windows_agent_msi.py" \
      "$ROOT/control/ai_mesh_control/core/org_ca_views.py" \
      "$ROOT/control/ai_mesh_control/core/endpoint_urls.py" 2>/dev/null || true

# main_app
rsync -a --exclude '__pycache__' \
  "$PARENT/backend/main_app/" "$ROOT/control/ai_mesh_control/main_app/"
rsync -a "$PARENT/backend/manage.py" "$ROOT/control/manage.py"
rsync -a "$PARENT/backend/pyproject.toml" "$ROOT/control/pyproject.toml"
rsync -a "$PARENT/backend/Dockerfile" "$ROOT/control/Dockerfile.monorepo" 2>/dev/null || true
rsync -a "$PARENT/backend/entrypoint.sh" "$ROOT/control/entrypoint.sh" 2>/dev/null || true

# workers tasks
mkdir -p "$ROOT/workers/ai_mesh_workers/tasks" "$ROOT/workers/ai_mesh_workers/drainers"
rsync -a "$PARENT/backend/core/tasks.py" "$ROOT/workers/ai_mesh_workers/tasks/telemetry.py"
rsync -a "$PARENT/backend/core/job_drainers.py" "$ROOT/workers/ai_mesh_workers/drainers/" 2>/dev/null || true
rsync -a "$PARENT/backend/policy/tasks.py" "$ROOT/workers/ai_mesh_workers/tasks/policy.py" 2>/dev/null || true
rsync -a "$PARENT/backend/mcp_connector/tasks.py" "$ROOT/workers/ai_mesh_workers/tasks/mcp.py" 2>/dev/null || true
rsync -a "$PARENT/backend/security_engines/tasks.py" "$ROOT/workers/ai_mesh_workers/tasks/tier2.py" 2>/dev/null || true

# --- frontend ---
mkdir -p "$ROOT/frontend/src/pages" "$ROOT/frontend/src/components"
rsync -a "$PARENT/frontend/src/pages/firewall/" "$ROOT/frontend/src/pages/"
mkdir -p "$ROOT/frontend/src/components/firewall"
rsync -a "$PARENT/frontend/src/components/firewall/" "$ROOT/frontend/src/components/firewall/"
rsync -a "$PARENT/frontend/src/components/simulator/" "$ROOT/frontend/src/components/simulator/"
rsync -a "$PARENT/frontend/src/components/rag/" "$ROOT/frontend/src/components/rag/"
for f in Login OAuthCallback Profile Settings; do
  rsync -a "$PARENT/frontend/src/pages/${f}.jsx" "$ROOT/frontend/src/pages/" 2>/dev/null || true
done
rsync -a "$PARENT/frontend/src/components/layout/" "$ROOT/frontend/src/components/layout/"
rsync -a "$PARENT/frontend/src/components/ui/" "$ROOT/frontend/src/components/ui/"
rsync -a "$PARENT/frontend/src/components/ProtectedRoute.jsx" "$ROOT/frontend/src/components/"
rsync -a "$PARENT/frontend/src/components/InactivityWarningModal.jsx" "$ROOT/frontend/src/components/" 2>/dev/null || true
rsync -a "$PARENT/frontend/src/components/ChangePasswordForm.jsx" "$ROOT/frontend/src/components/" 2>/dev/null || true
rsync -a "$PARENT/frontend/src/context/" "$ROOT/frontend/src/context/"
rsync -a "$PARENT/frontend/src/hooks/" "$ROOT/frontend/src/hooks/"
rsync -a "$PARENT/frontend/src/lib/" "$ROOT/frontend/src/lib/"
rsync -a "$PARENT/frontend/src/utils/" "$ROOT/frontend/src/utils/"
rsync -a "$PARENT/frontend/src/index.css" "$ROOT/frontend/src/index.css"
rsync -a "$PARENT/frontend/package.json" "$ROOT/frontend/package.json.monorepo"
rsync -a "$PARENT/frontend/vite.config.js" "$ROOT/frontend/vite.config.js.monorepo"
rsync -a "$PARENT/frontend/index.html" "$ROOT/frontend/index.html.monorepo" 2>/dev/null || true

# tests (subset)
mkdir -p "$ROOT/tests/unit" "$ROOT/tests/integration"
rsync -a "$PARENT/tests/unit/test_gateway_middleware.py" "$ROOT/tests/unit/" 2>/dev/null || true
rsync -a "$PARENT/tests/unit/test_telemetry.py" "$ROOT/tests/unit/" 2>/dev/null || true
rsync -a "$PARENT/tests/unit/test_rag_orchestrator.py" "$ROOT/tests/unit/" 2>/dev/null || true
rsync -a "$PARENT/gateway/tests/" "$ROOT/gateway/ai_mesh_gateway/tests/" 2>/dev/null || true
rsync -a "$PARENT/gateway/conftest.py" "$ROOT/gateway/ai_mesh_gateway/conftest.py" 2>/dev/null || true

# docs
rsync -a "$PARENT/docs/MODULE1_AI_MESH_FIREWALL.md" "$ROOT/docs/" 2>/dev/null || true

echo "Extract complete. Run post-process: python scripts/post_extract_fixes.py"
