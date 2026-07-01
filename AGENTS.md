# AGENTS.md — AI Mesh Firewall

## Ralph autonomous loop — gateway hardening
- Backlog + status live in scripts/ralph/prd.json; learnings in scripts/ralph/progress.txt.
- ONE story per iteration; never break a passing gate; never mark passes:true without a green gate.
- Backend gate: `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests -q` (+ `./.venv/bin/python
  -m pytest tests/leakhunt` for leaks — build that suite under gateway/tests/leakhunt; +
  test_openai_sdk_compat.py for SDK). Frontend gate: `cd frontend && npm run lint && npm run build`
  plus dev-browser verification for UI stories.
- Security invariants are non-negotiable: egress bytes are the only source of truth; any redact/block
  verdict or assume_redacted attestation must be byte-verified; fail closed on a no-op scrub.
- Reuse responses_adapters.py error helpers; respect patterns.py false-positive protection; mirror the
  output-guard honesty check onto the input/egress path.
- UI-story gate (D*): the durable re-runnable gates are `scripts/playwright_*.mjs`. Run with
  `NODE_PATH="$PWD/tests/e2e/node_modules" BASE_URL=http://127.0.0.1:8180 node scripts/playwright_<story>.mjs`
  — playwright lives in `tests/e2e/node_modules`, NOT `frontend/`. They drive the LIVE docker stack
  (Vite :8180 → control :8100 → gateway :8300); login admin@zeroshield.io / Adm1n!Pass#2024 via the REAL
  form (localStorage-token injection alone does NOT establish a session — the AuthContext guard bounces to
  /login). Module-1.1 is telemetry-heavy: keep panel/login waits ≥60s so a slow-but-correct render is not a
  false failure; never lower an assertion to make a flaky gate pass.
