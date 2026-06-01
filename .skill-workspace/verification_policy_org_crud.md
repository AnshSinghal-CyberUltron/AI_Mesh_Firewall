# Policy org CRUD — live verification (2026-06-01)

## Commands

```bash
docker compose exec -T control python manage.py test core.tests.test_policy_org_crud -v2
MODEL=anthropic/claude-haiku-4.5 ./scripts/verify_policy_toggle_e2e.sh
MODEL=anthropic/claude-haiku-4.5 ./scripts/verify_policy_enforcement_live.sh
```

## Results

| Check | Result |
|-------|--------|
| Django tests (8) | OK |
| E2E enabled → 403 block | PASS (Redis `policies:version:zeroshield` bumped) |
| E2E disabled → 200 chat | PASS (`anthropic/claude-haiku-4.5` request, routed upstream) |
| E2E re-enabled → 403 block | PASS |

### Full enforcement live E2E (`verify_policy_enforcement_live.sh`)

Org: **zeroshield** | Requested model: **anthropic/claude-haiku-4.5** (Custom/Other)  
Routed upstream: **live-triage-openai** → `gpt-5.2-2025-12-11` (dynamic routing; still real LLM calls)

| Scenario | HTTP | Evidence |
|----------|------|----------|
| Baseline (no E2E policy) | 200 | Live completion returned |
| BLOCK (regex token in prompt) | 403 | `content_blocked`, `blocked_by: policy`, `category: policy_violation` |
| REDACT (email in prompt) | 200 | Raw email **not** echoed in response body |
| MONITOR (token in prompt) | 200 | Request allowed through LLM (post-fix: output path no longer escalates monitor→block) |
| TOGGLE enabled → disabled | 403 then 200 | Block when policy on; safe prompt when policy off |

**Summary:** `passed=6 failed=0` — `ALL LIVE POLICY ENFORCEMENT CHECKS PASSED`

### Gateway fix verified (monitor)

Post-LLM output policy path in `gateway/ai_mesh_gateway/main.py` was escalating `monitor`/`flag` to org default `output_policy_action` (block). Fix: treat `flag` like `allow` on output re-check so monitor-only matches complete with 200.

## Triage fixes applied post-review

- Startup compile loops all orgs (`policy/apps.py`)
- System policy disable: admin-only; rule CRUD on system policies: admin-only
- `mcp_server` FK scoped to request org in `PolicyWriteSerializer`
- Frontend: fire-and-forget `POST /api/policies/compile/` after toggle
