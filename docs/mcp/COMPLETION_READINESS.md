# MCP Hardening — Completion Readiness Matrix

CHG-0037 (2026-07-02) — the backstop's honest map of the mandate against reality. Each
requirement is CODE-HARDENED (done + gated tests green), VERIFIED (evidenced, no code
change needed), or OPEN with the specific blocker. **Completion is NOT met** — the final
gate (full VERY-HARD stress suite passing 3× at 300–500-sandbox scale) has not run at that
magnitude and is blocked on a dedicated non-shared host. This file is the source of truth
for "what's left and why"; the per-change log is `docs/mcp/HARDENING_CHANGELOG.md`.

Legend: ✅ code-hardened + gated · 🔎 verified (evidence) · ⛔ OPEN (blocker in brackets).

## 1.4 — prevent MCP data leakage
| Requirement | Status | Evidence / blocker |
|---|---|---|
| Field-level redaction of tool RESULTS, byte-verified, fail-closed | ✅ | CHG-0003/0004/0005 (fail-closed result floor), CHG-0024/0025 (field-RBAC, cross-stage) |
| Per-user/agent/role tool authorization (actor-keyed) | ✅ | CHG-0006/0007/0008 (all paths; roles from the API key, not spoofable headers) |
| Context minimization / least-privilege | ✅ | CHG-0021 (N-A for MCP forwarding) + **CHG-0033 (HIGH: stopped caller gateway-credential leak to external servers)** |
| Compliance tagging extended to PII/IP/regulated (tag in+out, enforce, audit) | ✅ (backend) · ⛔ (UI) | CHG-0017 (PII/PHI/PCI live), CHG-0030 (IP/INFRA). UI reflection OPEN: CHG-0036 [control PolicyTestView omits compliance_tags from its response + item-5 vocab] |
| Full per-call chain authz→minimize→scan+redact→tag→audit | 🔎 | CHG-0021 (live, in order) |

## Architecture
| Requirement | Status | Evidence / blocker |
|---|---|---|
| All transports (http/ws/sse/stdio) via the per-org sandbox; nothing in the backend | ✅ | CHG-0018 (http/sse), CHG-0026 (ws) — all 4 via sandbox by DEFAULT (http/sse honor MCP_HTTP_VIA_SANDBOX) |
| No unknown npm on the host | ✅ (default-off) · ⛔ (prod-enable) | CHG-0022 (pin/allowlist env propagated). [prod: set REQUIRE_PINNED=true + pin every server + locked .npmrc — operator] |
| Gateway auth / authz / validation / rate-limit / policy / audit | ✅ | CHG-0016 (auth/authz), CHG-0031/0032 (rate-limit all 3 routes), CHG-0034 (body-size validation), CHG-0006/0007/0008 (policy authz) |
| CPU / mem / disk / timeout limits | 🔎 | CHG-0015/0035 (cap_drop=ALL, pids/mem/cpu, swap off, read-only rootfs, tmpfs). [containment-under-bomb = item 17, dedicated host] |
| PostgreSQL + Redis correctness | 🔎 | CHG-0023 (109k events/3 orgs, scan-ver/ratelimit/toolcall keys). [restart drill = item 18, dedicated host] |
| gVisor + seccomp / no-new-privileges / cap_drop / egress-lockdown | 🔎 (seccomp/nnp/cap) · ⛔ (gVisor+egress) | CHG-0015/0035 (default seccomp NOT unconfined, no-new-priv, cap_drop=ALL). [gVisor: `runsc` not installed → runc; egress: per-org nets internal=false (open NAT) — both INFRA] |
| Sandbox-agent egress hygiene (TLS verify, no redirect-follow, timeouts) | 🔎 | CHG-0035 (follow_redirects=False, no verify=False, verifying wss SSL) |
| Phase-3 monitoring / metrics / tracing / backup / auto-recovery | ✅ (metrics/health/restart) · ⛔ (tracing/backup) | CHG-0020 (metrics+health wired), CHG-0027 (gateway docker healthcheck+restart). [OTEL tracing + PG/Redis backup = INFRA] |

## Stress (the completion gate) — ALL OPEN, blocked on a dedicated non-shared host
| Item | Status | Blocker |
|---|---|---|
| 14 — 30–50 orgs × 8–10 MCPs = 300–500 concurrent sandboxes | ⛔ | code ceiling removed (CHG-0010); live 500 provision needs a dedicated host (shared-host fork budget saturates ~244/256 at 15) |
| 15 — 5k–10k concurrent tool calls | ⛔ | needs item 14 scale (dedicated host) |
| 16 — hours-long soak | ⛔ | destabilizes the shared stack |
| 17 — resource bombs (mem/fork/disk/timeout) contained | ⛔ | destructive on the shared stack |
| 18 — chaos (kill sandbox/broker/Redis/PG) + auto-recovery, no leak | ⛔ | destructive on the shared stack |
| 19 — cross-tenant canaries at 500-sandbox scale under chaos | ⛔ | oracle+harness sound (CHG-0009/0013/0035); needs items 14+18 |
| 20 — 1.4 guardrails under peak load (redaction+authz+tagging) | 🔎 (@25) · ⛔ (peak) | CHG-0014 (redaction 3× @25), CHG-0028 (authz oracle). [5k–10k peak = item 15] |

## Harness readiness (so a dedicated-host run produces trustworthy verdicts)
- `mcp_live_matrix_harness.py`: byte-truth redaction oracle + per-actor authz oracle (CHG-0012/0028), unit-tested.
- `mcp_pipeline_matrix_live.py`: byte-truth oracle + import-safe (CHG-0029), unit-tested.
- `mcp_scale_matrix_live.py`: real cross-tenant oracle in the PASS gate (CHG-0009/0013).
- `mcp_multi_org_harness.py`: byte-level canary + fail-closed negative matrix (verified CHG-0035).

## Bottom line
Code-level 1.4 + gateway/transport/isolation hardening is comprehensive and gated green
(gateway suite 1081 passed). What remains is (a) a **dedicated host** for the VERY-HARD
stress suite (items 14–19, the completion gate), (b) **infra** (gVisor install, egress
default-deny, OTEL tracing, PG/Redis backup, npm prod-enable), and (c) **owned/cross-plane**
work (item-21 UI tag reflection via the control response + item-5 tag vocab). Until the
stress suite passes 3× at scale, the promise stays unspoken.
