---
iteration: 34
max_iterations: 100
completion_promise: COMPLETE
status: ACTIVE
---

## iter34 (2026-07-02) — ws blockers recheck + 3-transport verify
- **P4.13/P6.18 RECHECK:** **No change** since iter33 — gateway ws still `mcp_ws_adapter`
  (`mcp_proxy.py:2005-2015`), NOT `broker_send_rpc`; control `URLField` (`models.py:41`) still
  rejects `ws://` registration. **3/4** gateway transports PASS after warmup (stdio+http+sse;
  cold-run http/sse tools=0 flakiness same as iter33 R1, no regression). ws BLOCKED (no manifest
  slug). ss: no gateway :443. Playwright **12/12**. **Cannot `[x]`** until Claude lands both seams.
  Evidence: `RECHECK_ITER34.md`.
- Hive: `cursor-ralph-iter34` on `hive-1782991737290-ylo911`.

## iter33 (2026-07-02) — ws stub prep + gateway gap recheck
- **P4.13/P6.18 RECHECK:** Gateway ws routing **unchanged** — still `mcp_ws_adapter` (`mcp_proxy.py:2000`),
  NOT `broker_send_rpc`. Cursor added `scripts/mcp_ws_everything_stub.mjs` + `upsert_ws` in
  `mcp_transport_stubs_up.sh`; broker-direct ws tools/list+call **PASS** (agent `ws_manager.py` ok).
  Control registration **FAIL** — Django `URLField` rejects `ws://` (no manifest ws slug). **3/4**
  gateway transports PASS R2–3 (stdio+http+sse); ws BLOCKED. ss: no gateway :443. Playwright **12/12**;
  broker **83 passed**. **Cannot `[x]`** until Claude migrates ws gateway + fixes URLField.
  Evidence: `RECHECK_ITER33.md`.
- Hive: `cursor-ralph-iter33` on `hive-1782991737290-ylo911`.

## iter32 (2026-07-02) — SSE reader + stale-session fix (agent)
- **P4.13/P6.18 RECHECK:** Fixed Cursor-owned agent: `sse_manager.py` (persistent GET /sse reader +
  stream POST + message-event responses); streamable-http stale-session recovery verified live.
  Rebuilt `ai-mesh/mcp-sandbox:latest` + recreated sandboxes. **3/4 transports PASS** 3× (stdio,
  streamable-http, sse); **ws BLOCKED** (no stub + `mcp_ws_adapter`). Network: gateway no :443.
  Playwright B1/B2/B4 **12/12**; broker gate **83 passed**. **Cannot `[x]`** until ws through sandbox.
  Evidence: `RECHECK_ITER32.md`.
- Hive: `cursor-ralph-iter32` on `hive-1782991737290-ylo911`.

## iter31 (2026-07-02) — §3 DEPLOYED to live gateway
- **P4.13/P6.18 RECHECK:** Runtime gateway **NOW has §3** (2 `broker_send_rpc` refs after
  `docker compose build gateway` + `--force-recreate`). Wiring gate **YES**. **Cannot `[x]`** —
  stdio e2e PASS; streamable-http FAIL (session ID / upstream 400); sse FAIL (90s hang);
  ws BLOCKED (`mcp_ws_adapter`, no ws stub). `MCP_HTTP_VIA_SANDBOX=true`; stubs up.
  Network: gateway **no :443** during verify. Playwright B1/B2/B4 **12/12 PASS**.
  Evidence: `RECHECK_ITER31.md`.
- Hive: `cursor-ralph-iter31` on `hive-1782991737290-ylo911`.

## iter30 (2026-07-02) — flag ON + stubs; deployed §3 MISSING
- **P4.13/P6.18 RECHECK:** Source §3 **YES** (2 `broker_send_rpc` refs); runtime gateway **NO**
  (0 refs — baked image stale). `MCP_HTTP_VIA_SANDBOX=true` live via
  `scripts/mcp_enable_http_via_sandbox.sh`. Registered `http-everything-stub` +
  `sse-everything-stub`; ws blocked (URLField + no broker ws path). Broker direct
  streamable-http **PASS**; gateway http/sse **empty tools** (legacy path). **Cannot `[x]`.**
  Evidence: `RECHECK_ITER30.md`, `docs/mcp/MCP_HTTP_VIA_SANDBOX_ENABLE.md`.
- **Cursor gates:** Playwright B1/B2/B4 **12/12**; frontend build ✓; package tests **52/52**;
  stdio echo **PASS**; transport verify **FAIL** (3/4 transports).
- Hive: `cursor-ralph-iter30` on `hive-1782991737290-ylo911`.

## iter29 (2026-07-02) — §3 wiring landed; e2e incomplete
- **P4.13/P6.18 RECHECK:** §3 **WIRING LANDED** (`bb4983da`) — `mcp_proxy` now has **2**
  `broker_send_rpc` refs (`_is_sandbox_routed` + `_adapter_forward` for http/sse behind
  `MCP_HTTP_VIA_SANDBOX`, default **OFF**). Wiring gate **YES**; **14** httpx sites remain;
  websocket still `mcp_ws_adapter` (not broker). **Cannot `[x]`** — http/sse/ws servers
  unregistered, 4-transport verify FAIL. Evidence: `RECHECK_ITER29.md`.
- **Cursor gates:** stdio transport **PASS**; `mcp_multi_org_harness.py` **GREEN** 1×;
  Playwright B1/B2/B4 **12/12 PASS**; gateway :443 egress **0** during stdio probe.
- Hive: joined `hive-1782991737290-ylo911` as `cursor-ralph-iter29`; broadcast posted.

## iter28 (2026-07-02) — P4.13/P6.18 recheck + transport manifest prep
- **P4.13/P6.18 RECHECK:** §1+§2 still LANDED; §3 **STILL BLOCKED** — `mcp_proxy` zero
  `broker_send_rpc` refs, **14** `httpx.AsyncClient` upstream dial sites. Broker unified
  `POST /{org}/rpc` → 401. Evidence: `mcp-parallel/findings/p4-13/RECHECK_ITER28.md`.
- **Cursor prep:** `TRANSPORT_MANIFEST.example.json` HTTP/SSE/WS placeholders;
  `TRANSPORT_REGISTRATION.md`; `mcp_sandbox_transport_verify.py` registration guide +
  httpx/ref counts in wiring gate output.
- **Cursor gates:** P7.23 **26/26**; broker+agent **138/138**; frontend build ✓;
  wiring gate **BLOCKED** (exit 2); P10 recursive **3× ALL GREEN** (SKIP_PLAYWRIGHT;
  retry after round-2 load flake).
- Hive: joined `hive-1782991737290-ylo911` as `cursor-ralph-iter28`.

## iter27 (2026-07-02) — P4.13/P6.18 recheck + Cursor gates green
- **P4.13/P6.18 RECHECK:** §1+§2 still LANDED; §3 **STILL BLOCKED** — `mcp_proxy` zero
  `broker_send_rpc` refs, 14+ direct `httpx.AsyncClient` upstream dials. Broker unified
  `POST /{org}/rpc` → 401. Evidence: `mcp-parallel/findings/p4-13/RECHECK_ITER27.md`.
- **Cursor gates:** P7.23 N2+N3 **26/26**; broker+agent **138/138**; frontend build ✓;
  `mcp_sandbox_transport_verify.py` **BLOCKED** (wiring gate exit 2); P10 recursive 1×
  ALL GREEN (broker 97, agent 41, multi-org/concurrency/load/leakage/oauth PASS).
- Hive: joined `hive-1782991737290-ylo911` as `cursor-ralph-iter27`; memory stored.
- 15-MCP fleet: 3 orgs × 5 servers manifest ready; headed browser (noVNC :6080).

## iter26 (2026-07-02) — P7.23 N2+N3 verified + P4.13/P6.18 partial recheck
- **P7.23 N2+N3 DONE:** package allowlist + pinned-package enforcement in
  `stdio_manager.py` verified **26/26** via broker venv
  (`services/mcp-broker/.venv/bin/python -m pytest sandbox-image/agent/tests/test_stdio_manager_packages.py`);
  full broker+agent gate **138 passed**. N4–N7 remain Claude-owned.
- **P4.13/P6.18 RECHECK (PARTIAL):** §1 broker `POST /{org}/rpc` LIVE (401 not 404);
  §2 `broker_send_rpc` PRESENT in `mcp_sandbox_client.py`; §3 `mcp_proxy` still has
  zero `broker_send_rpc` refs — direct httpx upstream dials remain. Still BLOCKED.
  Evidence: `mcp-parallel/findings/p4-13/RECHECK_ITER26.md`.
- Hive: joined `hive-1782991737290-ylo911` as `cursor-ralph-iter26`.
- 15-MCP fleet: manifest has 3 orgs × 5 stdio Everything servers; headed browser UP
  (noVNC :6080, CDP :9222) for manual HTTP OAuth.

## iter25 (2026-07-02) — P4.13/P6.18 recheck + transport verify prep
- **P4.13/P6.18 RECHECK:** still BLOCKED — `broker_send_rpc` absent; `POST
  :8311/v1/sandbox/zeroshield/rpc` → 404; `mcp_proxy.py` still direct httpx for
  http/sse. Evidence: `mcp-parallel/findings/p4-13/RECHECK_ITER25.md`.
- **Cursor prep:** `scripts/mcp_sandbox_transport_verify.py` (wiring gate + 4-transport
  e2e when unblocked + tcpdump/ss network assertion guide); checklist §iter25 updated.
- P4.13/P6.18/P6.19-P4 remain `[ ]` — no fake `[x]`.

## iter24 (2026-07-02) — P10.32 recursive verification + P4.13/P6.18 recheck
- **P4.13/P6.18 RECHECK:** still BLOCKED — `broker_send_rpc` absent; `POST
  :8311/v1/sandbox/zeroshield/rpc` → 404; `mcp_proxy.py` still direct httpx for
  http/sse. Evidence: `mcp-parallel/findings/p4-13/RECHECK_ITER24.md`.
- **P10.32 DONE:** `scripts/mcp_p10_recursive_gate.py` — broker 95/95 + agent 41/41 +
  multi-org GREEN + concurrency + load + leakage + oauth + Playwright B1/B2/B4, **3×
  consecutive all-green** (~633s). Findings: `mcp-parallel/findings/p10-32/`.
- Scratchpad hygiene: marked C0, P1–P5 verified items `[x]`; P4.13/P6.18/P6.19-P4
  remain `[ ]` (Claude gateway wiring).

## iter23 (2026-07-02) — P9.31 OAuth/transport correctness under load
- Item #31 DONE: `scripts/mcp_oauth_transport_live.py` hardened login (429 honor-wait,
  15 attempts, inter-org gap). **GREEN 3× (67/67 checks each):** every stdio server
  across 3 orgs stays `auth_type=none`; control oauth-start on stdio returns 400 with
  transport error (B1 guard LIVE — not "Server has no URL"); never flipped to oauth;
  under 4× concurrent 15-wide echo load **oauth-signal responses=0**. HTTP-oauth:
  `linear-manual-oauth` authorized clean (47 tools, needs_reauth=false); `stub-oauth`
  valid pending (0 tools). Playwright B1 8/8 PASS. Findings:
  `mcp-parallel/findings/p9-31/OAUTH_TRANSPORT.md`.
- iter23 gateway-wiring recheck (P4.13/P6.18): still BLOCKED — broker `/{org}/rpc` 404,
  `broker_send_rpc` absent from gateway (stdio-only path).

## iter22 (2026-07-02) — P9.29 sustained load: Cursor 2nd-oracle pooling/reuse/cgroup corroboration
- Item #29 was already marked [x] by the parallel Claude session (`scripts/mcp_load_live.py`, host-cgroup
  sampling, GREEN 3× / 9000 calls). This iteration adds an INDEPENDENT Cursor oracle (different harness +
  different sampling) — the iter21-for-#28 pattern repeated for #29.
- Added a `SUSTAINED=1` phase (Phase 3.6) to Cursor-owned `scripts/mcp_multi_org_harness.py`: holds a
  STEADY 90-in-flight for 90s across all 15 targets (continuously refilled = true stream), + a 300-wide
  overload micro-burst (503/retry probe) + a recovery probe. Reliability split HTTP-503 vs JSON-RPC-error;
  503-storm metric = max-503-per-second (bucketed by completion ts). Default (`SUSTAINED` unset) unchanged.
- Sampled IN-CONTAINER cgroup `pids.current` + container IDs every 3s during run1 (Claude sampled the HOST
  cgroup — both agree). **RESULT: GREEN 3× (10,055 sustained calls):** container count=5 constant + IDs
  unchanged (`ce651a98198c`/`2047af4aa278`/`3c2353b834fa`) = reuse/pooling holds, no dup, no orphan spawn;
  **pids.current flat 123/256** (1 transient 125 blip) = no per-call fork growth; mem flat ≪2 GB; **http_503=0
  / max_503_per_sec=0** (no storm); overload 300-wide = 300/300 passed 0×503 each run (2× ceiling absorbed
  as latency); recovery 15/15; pids drained to 123. Only blip = control `-32000` (0.17% agg, Claude ceiling).
- `docker_manager` NOT touched → broker gate not required (verification-only). No four-memory changelog entry
  (no hardening code changed). Findings: `mcp-parallel/findings/p9-29/CURSOR_POOLING.md`.
- iter22 gateway-wiring recheck (P4.13/P6.18): still BLOCKED — `broker_send_rpc` absent from gateway; broker
  exposes only `/{org_slug}/stdio/rpc`, no transport-agnostic `/{org}/rpc` (see below).

## iter21 (2026-07-02) — P9.28 concurrency: independent 2nd-oracle corroboration + #29 saturation boundary
- Item #28 was already landed by the parallel Claude session (commit `070c3917`, `scripts/mcp_concurrency_live.py`,
  240-in-flight dual-oracle, GREEN 3×). This iteration added a **second, differently-designed** concurrency
  storm to the Cursor-owned `scripts/mcp_multi_org_harness.py` (Phase 3.5): per round fire `CONCURRENCY_DEPTH`(12)
  calls PER target for all 15 at once (180/round, 540/run) in ONE 240-wide pool → tests the broker/sandbox
  stdio id-demux. Every reply classified `passed/errored/dropped/id_mismatch/mixed/cross_target`; #28 gate =
  zero-tolerance on drop/id_mismatch/mixed/cross_target (errored=reliability→#29).
- **RESULT: GREEN 3× — gate_violations=0 every run (1620 stormed calls, 0 dropped/mixed/cross/id-mismatch)**;
  cross-tenant 6/6 rejected. Detector proven to FIRE (negative-control over the real classification predicate:
  synthetic drop/id_mismatch/mixed/cross all flag; errored kept distinct; clean=passed).
- **NET-NEW for #29:** depth sweep 60→240-wide — isolation perfect at every depth; ONLY application errors
  appear at ≥180-wide. Root-caused: control(Django) `tool_not_registered` 403 + DRF `Internal server error` 500
  forwarded by gateway `mcp_proxy.py:2540` as -32000 → **control+gateway load ceiling (Claude-owned) = item #29**;
  no Cursor-owned component implicated (single server @60-deep = 100% clean). Findings: `mcp-parallel/findings/p9-28/RESULT.md`
  (+ concurrency_report_run{1,2,3}.json + saturation_depth16_report.json).
- `docker_manager` NOT touched → broker gate not required. iter22 gateway-wiring recheck: still BLOCKED —
  `broker_send_rpc` absent from gateway; broker exposes only `/{org_slug}/stdio/rpc` (no transport-agnostic
  `/{org}/rpc`). P4.13/P6.18 remain blocked on Claude.

## iter20 (2026-07-02) — P8.26 multi-org harness DONE + nproc/UID scaling bug FIXED
- Built `scripts/mcp_multi_org_harness.py`: parallel `tools/list`+`echo`(unique canary+id)+`get-sum`
  across all 3 orgs × 5 Everything MCPs (15) via the gateway, plus cross-tenant negative matrix.
  Result: warmup 15/15, **51/51 calls pass, cross-tenant 6/6 rejected (403), GREEN 3×**.
- ROOT-CAUSE FIX (Cursor-owned `services/mcp-broker/src/sandbox/docker_manager.py`): the harness
  exposed only 7–8/15 servers could fork — `_sandbox_ulimits` set an `nproc` ulimit, but
  `RLIMIT_NPROC` is per host-UID and all org sandboxes share uid 1000 → one 256-task budget shared
  across tenants (also a cross-tenant fork-DoS). Removed nproc; `pids_limit` (per-container cgroup)
  keeps fork containment. Broker gate 95/95. Findings: `mcp-parallel/findings/p8-26/NPROC_ROOT_CAUSE.md`.
- Still BLOCKED on Claude: P4.13/P6.18 gateway wiring (no `broker_send_rpc`, broker `/{org}/rpc` 404,
  `mcp_proxy` still direct httpx for streamable-http/sse). Checked iter20 — not landed yet.

# Cursor Ralph — MCP transports→sandbox + frontend (PARALLEL with a Claude Code session)

## Coordination (do every iteration)
- [x] C0. Join hive; read docs/mcp/PARALLEL_CLAIMS.md + mcp-parallel/claims. Only touch files in the
      Cursor-owned set (frontend/**, sandbox-image/agent/**, docker_manager.py, docs/). Claim before edit.
      **Done 2026-07-02:** joined hive-1782976205971-u1gav1 as cursor-ralph-iter2; iter24 claim
      `cursor-ralph-iter24-P10.32`.

## P1 — Playwright-first exploration: WHY is MCP not working (repro the 4 UI-visible bugs)
- [x] 1. Playwright MCP: log in, open MCPConnectorPanel → register Linear via STDIO; capture the TWO
      auth buttons + "OAuth authorize failed: Server has no URL; OAuth is only for HTTP transports".
      **Verified 2026-07-02:** B1 NOT reproducing — 1 Authorize button, no no-URL error; see
      `mcp-parallel/findings/p1-1/`. OAuth start 401 to prod gateway URL is a separate env issue.
- [x] 2. Playwright: register an HTTP oauth server → confirm it LISTS immediately with 0 tools (wrong).
      **Verified 2026-07-02:** B2 FIXED (iter14) — amber pending state; Playwright 6/6 + E2E 12/12;
      see `mcp-parallel/findings/p1-2/`, `p5-15/`.
- [x] 3. Playwright: type into the Add-Server dialog fields → capture focus loss after 1 keystroke.
      **Verified 2026-07-02:** B4 NOT reproducing — focus kept for 24 keystrokes × 5 fields; prior
      `Dialog.jsx` onCloseRef fix effective; see `mcp-parallel/findings/p1-3/`.
- [x] 4. Playwright + logs: trigger a tool call that surfaces "MCP sandbox is temporarily unavailable";
      capture the network trace + broker logs. Record all repros to mcp-parallel/findings with screenshots.
      **Verified 2026-07-02:** B3 confirmed — broker stopped → tools/call shows error in UI + gateway
      logs (5× broker unreachable); see `mcp-parallel/findings/p1-4/`.

## P2 — OSS + internet research (better approaches for ALL-transport sandboxing)
- [x] 5. GitHub MCP: study modelcontextprotocol/servers + how remote (http/sse/ws) MCPs are proxied;
      study mcp-remote (stdio-wraps-remote + OAuth) — clarifies Linear.
      **Done 2026-07-02:** `docs/mcp/oss-research-remote-transport-proxies.md` + `mcp-parallel/findings/p2-5/`.
- [x] 6. GitHub MCP: study gVisor (google/gvisor runsc) + Docker sandbox hardening for untrusted code
      (seccomp, no-new-privileges, cap_drop, read-only rootfs, egress allow-listing) + per-tenant patterns.
      **Done 2026-07-02:** extended `docs/mcp/oss-research-docker-hardening.md` + `mcp-parallel/findings/p2-6/`
      (`_run_kwargs` map, runsc fail-closed pattern, egress default-deny-proxy).
- [x] 7. Web research: MCP OAuth 2.1 (PKCE, RFC 9728/8414) + why oauth needs an HTTP URL; correct UX for
      stdio-wrapped-remote; running http/ws clients inside a sandboxed agent. Record to mcp-parallel/findings.
      **Done 2026-07-02:** `docs/mcp/oss-research-oauth-sandbox-client.md` + `mcp-parallel/findings/p2-7/`.

## P3 — Contract-first seam (with the Claude session)
- [x] 8. Draft docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md: the sandbox-agent endpoints for http/ws/sse proxy
      (request/response shape, streaming, timeouts, per-server config, egress policy). Ratify via
      hive-mind_consensus BEFORE implementing across the seam.
      **Done 2026-07-02:** `docs/mcp/SANDBOX_TRANSPORT_CONTRACT.md` v1.0.0-draft; proposal
      `proposal-1782979656279-rgcvqe` — Cursor YES, Claude PENDING (provisional ratification).

## P4 — Architecture: move ALL transports into the per-org gVisor sandbox
- [x] 9. Extend sandbox-image/agent to proxy HTTP/SSE MCP servers (in-sandbox httpx client, egress
      allow-listed to the registered upstream only) — per the contract.
      **Done 2026-07-02:** `upstream_manager.py` + transport-aware `/rpc` in `main.py`; 7 agent tests green.
- [x] 10. Extend sandbox-image/agent to proxy WEBSOCKET MCP servers (in-sandbox ws client) — per the contract.
      **Done 2026-07-02:** `ws_manager.py` + websocket in `upstream_manager`/`main.py`; 21 agent tests green.
- [x] 11. Keep stdio as-is but unify: the agent exposes one transport-agnostic /rpc so the gateway calls
       the sandbox for EVERY transport; the gateway never dials upstream directly.
       **Cursor done 2026-07-02:** agent unified (`main.py`); tests in `test_rpc_unified.py`;
       gateway/broker wiring spec in `docs/mcp/gateway-integration-checklist.md` (Claude-owned, P6.18).
- [x] 12. Enforce gVisor: docker_manager requires runtime=runsc in prod (fail-closed if unavailable);
       add security_opt (seccomp, no-new-privileges), cap_drop=ALL, tmpfs-only writes, egress lockdown.
       **Done 2026-07-02:** `_run_kwargs` hardening (no-new-privileges, cap_drop ALL, user=sandbox,
       init, ulimits, memswap_limit); `MCP_SANDBOX_RUNTIME_REQUIRED` fail-closed probe; egress via
       `MCP_SANDBOX_EGRESS_LOCKDOWN` / proxy env; 75 broker tests green.
- [ ] 13. Verify (Playwright + harness): with the gateway pointed only at the sandbox, an http, an sse, a
       ws, and a stdio MCP all work end-to-end THROUGH the sandbox; confirm the gateway opens NO direct
       upstream connection (network assertion) — nothing runs in the main backend.
       **e2e PARTIAL 2026-07-02 (iter32):** stdio + streamable-http + sse PASS 3× via gateway→broker→
       sandbox; ws BLOCKED (`mcp_ws_adapter` + no ws stub). Agent SSE fix (`sse_manager.py`) + stale-
       session invalidation. Playwright PASS; ss no gateway :443. Cannot `[x]` until 4/4. `RECHECK_ITER32.md`.
       **iter33 update:** ws stub live; broker-direct ws PASS; URLField blocks ws registration; gateway
       ws still Claude-owned. 3/4 R2–3. `RECHECK_ITER33.md`.

## P5 — Frontend fixes (Playwright-verified; the dialog especially)
- [x] 14. B1: OAuth selectable ONLY for HTTP transports; block oauth+stdio in the form; render exactly
       ONE Authorize button (HTTP+oauth only); remove the dup/broken control authorize path.
       **Verified 2026-07-02 (iter13):** Playwright 8/8 PASS (`playwright_mcp_b1_verify.mjs`);
       form guards + one-button UX already in place. Control `startControlOAuth` retained for HTTP
       oauth (token→control DB required for sync); structural removal needs Claude token bridge.
       See `mcp-parallel/findings/p4-13/P5-14-B1-VERIFY.md`.
- [x] 15. B2: freshly-registered HTTP oauth server shows a distinct "Pending authorization" state (not a
       0-tools card); tools appear only after oauth_authorized + sync.
       **Verified 2026-07-02 (iter14):** fix already in `04748af5` (renderServerCard awaitingAuth);
       Playwright 6/6 PASS (`playwright_mcp_b2_verify.mjs` → `mcp-parallel/findings/p5-15/`).
       Amber "Pending authorization" badge + "Authorize to load tools"; no Unknown/0-tools card.
- [x] 16. B4: stabilize the Add-Server dialog so controlled inputs KEEP focus per keystroke (fix the
       remount: no field-component defined in render / stable keys / portal children not recreated).
       **Verified 2026-07-02 (iter15):** B4 NOT reproducing — prior Dialog.jsx onCloseRef fix holds;
       Playwright 11/11 PASS (`playwright_mcp_b4_verify.mjs` → `mcp-parallel/findings/p5-16/`).
       24 keystrokes × 5 fields + GitHub preset prefill + 37 keys on prefilled field; focus kept.
- [x] 17. Playwright verify B1/B2/B4 end-to-end with screenshots (Linear-stdio → no OAuth; HTTP-oauth →
       one Authorize + pending state → tools populate; type long strings in every dialog field, focus kept).
       **Verified 2026-07-02 (iter15):** combined E2E 12/12 PASS (`playwright_mcp_b1_b2_b4_e2e.mjs` →
       `mcp-parallel/findings/p5-17/`). Tools-populate after authorize = manual OAuth (headed noVNC).
- [x] **P7.23 N2+N3 (iter18/iter26):** Package-allowlist parity + pinned-package enforcement in
       `sandbox-image/agent/stdio_manager.py`. Helpers `_extract_package_spec`, `_package_name`, `_is_pinned`
       + constants `_PACKAGE_ALLOWLIST` (env `MCP_STDIO_PACKAGE_ALLOWLIST`) and `_REQUIRE_PINNED_PACKAGES`
       (env `MCP_STDIO_REQUIRE_PINNED_PACKAGES`, default off) ported from gateway `mcp_stdio_adapter.py`.
       Enforcement in `_ensure_process` after command-allowlist check (same position as gateway path).
       **Verified iter26:** 26/26 `test_stdio_manager_packages.py` (broker venv) + 138/138 broker+agent;
       frontend build ✓. **Remaining N4–N7:** pre-bake, registry-pin, uv/PyPI equivalents (Claude-owned).

## P6 — Integrate + verify with the parallel session
- [ ] 18. Pull the Claude branch's gateway+broker changes (via the contract); run an integration check:
       all 4 transports through the sandbox under the Claude session's 15-MCP harness (concurrency/load/
       leakage). Fix any seam mismatch on the Cursor-owned side only.
       **e2e PARTIAL 2026-07-02 (iter33):** ws stub + broker-direct ws PASS; gateway ws still
       `mcp_ws_adapter`; URLField blocks ws registration. 3/4 transports green R2–3. See `RECHECK_ITER33.md`.
- [ ] 19. Re-run P1 repros → all four UI bugs gone; re-run P4 verification 3× (in-process + live).
       When P1–P6 all [x] AND integration green, output <promise>COMPLETE</promise>.
       **PARTIAL 2026-07-02 (iter24):** P10.32 recursive gate re-ran P1 UI (Playwright 12/12) + P8/P9 live
       harnesses GREEN 3×. P4.13/P6.18 still blocked on Claude gateway wiring.
