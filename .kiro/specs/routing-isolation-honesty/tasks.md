# Routing isolation honesty — Implementation tasks

> **For Claude:** REQUIRED SUB-SKILL: Use `skill-executing-plans` to implement this plan task-by-task **after the operator approves** `requirements.md` + `design.md`.
>
> **Do not start product code until approval.** Task 0 (live KS disable) is ops; still wait for approval unless the operator says to clear KS immediately.

**Goal:** When Dynamic Routing is Enabled, Attack Simulator runs routing policy; kill-switch constrains candidates and never silent-404s; already-masked PII completes on a Callable model; public E2E + full MIG bounce.

**Architecture:** Isolation = filter on remaining models; `select_model` still runs when org routing is on; Attack Simulator uses `simulatorRoutingPreferences`; save-time Callable check; runtime isolation 404→503.

**Tech stack:** FastAPI gateway, Django control, React frontend, pytest, node:test, Playwright/public HTTPS, GAR + GCP MIG.

**Worktree:** Prefer `skill-using-git-worktrees` after approval. Do not commit unless asked.

**Gates:**

```bash
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_m1_6_isolation_killswitch_sdk.py \
  ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py \
  -q
# plus new tests from tasks below

cd frontend && npm run test:unit && npm run lint && npm run build
```

Evidence dir: `mcp-parallel/findings/routing-isolation-honesty/` (create on implementation).

---

### Task 0: Deactivate live Vendor-incident kill-switch (ops)

**Files:** none (API/ops)

**Step 1:** After approval, as admin, list `/api/kill-switches/` on live control.

**Step 2:** Deactivate/delete the row rerouting `mistral-nemo-cheap` → `claude-haiku-cheap`.

**Step 3:** Redis GET `kill_switch:zeroshield:model:mistral-nemo-cheap` empty/inactive. Inspect ModelState fallback for that model. Inspect CB Redis twin; do not `--prune-orphans` CB-owned keys blindly. Credential-scoped keys with the same pair: list and clear if present.

**Step 4:** Save redacted JSON to `mcp-parallel/findings/routing-isolation-honesty/live-ks-cleared.json`.

**Verify:** Active KS list does not contain that pair.

---

### Task 1: Frontend unit — Attack Simulator prefs (TDD)

**Files:**

- Test: `frontend/src/utils/liveGateway.test.js` (already has `simulatorRoutingPreferences`)
- Modify: `frontend/src/components/AttackSimulatorPanel.jsx`
- Reuse: `frontend/src/hooks/useFirewallConfig.js`, `simulatorRoutingPreferences`

**Step 1:** Add/extend unit coverage that Attack Simulator wiring must use `simulatorRoutingPreferences` (component test if one exists; otherwise a small extracted helper tested in `liveGateway.test.js` is enough **and** a grep-style test or explicit helper `attackSimulatorRoutingPreferences = simulatorRoutingPreferences`).

**Step 2:** Run `npm run test:unit` — fail if panel still imports pin for those paths (add a node:test that reads the panel source **only if** the repo already uses source-contract tests; prefer refactoring the three call sites to one helper `attackSimRoutingPrefs(model, orgOn)` in `liveGateway.js` and unit-test that).

**Step 3:** Implement: `useFirewallConfig.jsx`, replace three `pinnedModelRoutingPreferences(...)` with `simulatorRoutingPreferences(..., { orgRoutingEnabled })`. Do not `?? true` while loading (omit `enable_routing` until config is known).

**Step 4:** `npm run test:unit` pass; `npm run lint` / `npm run build`.

---

### Task 2: Frontend unit — copy honesty (TDD)

**Files:**

- Test: `frontend/src/utils/routingExplain.test.js`
- Modify: `frontend/src/utils/routingExplain.js`
- Possibly: `frontend/src/utils/pipelineTrace.js` `resolveRoutingDecision` to pass `org_routing_enabled`; **must** fix `honestStageAction` so `model_routing` allow+0ms is not skip.

**Step 1:** Failing tests:

- `org_routing_enabled: true`, `decision_source: routing_disabled` → summary matches `/this request pinned/i`, does **not** match `/Org routing is off/i`
- `org_routing_enabled: false`, `routing_disabled` → org-off wording OK
- `kill_switch` + `candidate_count: 2` → isolation copy + remaining models
- `org_routing_enabled: true` + `routing_disabled` + Haiku selected must not say org is off

**Step 2:** Implement `summarizeRoutingDecision` branches from design §5.

**Step 3:** Tests pass.

---

### Task 3: Gateway unit — routing still runs under KS (TDD)

**Files:**

- Test: `gateway/ai_mesh_gateway/tests/test_m1_6_isolation_killswitch_sdk.py` (extend) **or** new `test_routing_runs_under_killswitch.py`
- Modify: `gateway/ai_mesh_gateway/main.py` (`routing_active` ~9668, constrain remaining, metadata merge)

**Step 1:** Failing test: org `routing_enabled=true`, no client pin, KS reroute PRIMARY→ALT (both catalog chat models), assert:

- upstream called with ALT
- `zeroshield.routing.decision_source == "kill_switch"`
- `candidate_count >= 1`
- `pipeline_trace` stage `model_routing` `action != "skip"`
- `org_routing_enabled is True`

**Step 2:** Confirm current code fails (lock skips routing).

**Step 3:** Remove `and not isolation_reroute_locked` from `routing_active`. Constrain `inference_models` to callable fallback; call `select_model`; merge KS `decision_source`.

**Step 4:** Test pass. Re-run full `test_m1_6_isolation_killswitch_sdk.py` and `test_m1_5_routing_governance_sdk.py`.

**Step 5:** Keep `test_s9_enable_routing_false_pins_the_model_when_routing_is_on` green (SDK pin).

---

### Task 4: Gateway unit — uncallable isolation target fail-closed (TDD)

**Files:** same test module + `main.py` LiteLLM error mapping (isolation-gated)

**Step 1:** Failing test: KS reroute to a fallback **in catalog name** but mock LiteLLM 404 (or missing `model_id`). Assert HTTP 503, code in `{kill_switch_active, isolation_target_uncallable}`, LiteLLM not retried, no `upstream_error` 404 as primary.

**Step 2:** Implement pre-dispatch Callable check + isolation-only remap of provider 404/401.

**Step 3:** Assert non-isolation `gpt` provider 401 still uses existing sanitizer (do not swallow Model Connections 401).

---

### Task 5: Control — Callable fallback on all save paths (TDD)

**Files:**

- Test: `control/ai_mesh_control/core/tests/test_kill_switch_partial_update.py` + new tests
- Modify: `control/ai_mesh_control/core/serializers.py` (`KillSwitchCreateSerializer`, `ModelIsolateSerializer`, `ModelStateUpdateSerializer`)

**Step 1:** Failing tests: reroute fallback with `is_active=True` but empty `model_id` → 400; empty credentials → 400; `ModelStateUpdateSerializer` PATCH `{fallback_model: unknown}` → 400.

**Step 2:** Shared `_fallback_must_be_callable(org, name)` helper.

**Step 3:** Control tests in Docker/venv as this repo usually runs them.

---

### Task 6: PIPELINE changelog (when committing)

**Files:** `AGENTS.md`, `.cursor/rules/pipeline-changelog.mdc`, `docs/pipeline/CHANGELOG.md`, Ruflo `memory_store` namespace `pipeline/changes`

**Step:** Next PIPELINE-NNNN describing: routing runs under KS constraint; Attack Sim preference hint; isolation 404 fail-closed. Same commit as gateway change **when the user asks to commit**.

---

### Task 7: Local/backend validation (CLAUDE.md Phase 6)

```bash
cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_m1_6_isolation_killswitch_sdk.py \
  ai_mesh_gateway/tests/test_m1_5_routing_governance_sdk.py \
  ai_mesh_gateway/tests/test_pipeline_pre_masked_redact.py \
  -q
```

Add the new test file(s) to this command. Do not claim complete if isolation SDK file regresses.

Malformed KS payload, concurrent requests, Redis fail-closed: existing tests must stay green.

---

### Task 8: Frontend customer validation (Phase 7) + public E2E (Phase 8)

**After images exist: Vite dry-run optional; public host after Task 9 is the only pass.**

1. Login `https://aimeshfirewall.zeroshield.ai/` (`admin@zeroshield.io`).
2. Confirm 1.5 Dynamic Routing Enabled (screenshot).
3. 1.1 Attack Simulator: benign prompt, routing-on JSON: `org_routing_enabled: true`, `enable_routing` not forcing skip, `model_routing` action ≠ skip, copy ≠ org-off.
4. Already-masked Sensitive Data Leakage: redact/noop then **200** (or honest 403), not provider 404.
5. Burst + stream once each (regression).
6. Save screenshots + response JSON (redact `zs-` keys if needed, keep `pipeline_trace`).

Use Playwright MCP / `production-live-verification`. `skill-verification-before-completion`: do not mark done on unit tests.

---

### Task 9: Full MIG bounce (deploy constraint)

1. Build/push ARM64 images for **gateway, control, nginx** (SPA) to GAR `asia-south1-docker.pkg.dev/aisecshield-prod/mesh-firewall-images/`.
2. SSH MIG `mesh-firewall-mig-nws9` (key `~/.ssh/google_compute_engine`, not IAP). Recreate **all** app services (full bounce, not nginx-only).
3. Record tags + `docker compose ps` + public JS hash.
4. Re-run Task 8 against public URL (authoritative).

---

### Task 10: Devil’s advocate / residual

- Confirm SDK pin still works from curl `enable_routing: false`.
- Confirm 1.6 cannot save empty-`model_id` fallback.
- Confirm analytics 503 and gpt-5.2 401 unchanged (out of scope).
- `skill-requesting-code-review` on the diff.

**Handover:** evidence pack path + public URLs + test command outputs. Do not mark complete without Task 8 public proof.
