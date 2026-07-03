# CHG-0147 — regression-lock: sandbox egress allowlist is normalized-EXACT-match (no subdomain/suffix bypass)

**Change-id:** CHG-0147
**Date:** 2026-07-03
**Severity:** LOW (test-only regression-lock — **no production code change**). Locks a verified-sound,
exfil-critical 1.4/egress-lockdown invariant: the sandbox agent's upstream host allowlist
(`_validate_upstream`) matches by NORMALIZED EXACT set membership, fail-closed — never a substring / suffix
/ subdomain match. A regression to `endswith()` / `in` would open the classic allowlist bypass (a
tenant-registered upstream exfiltrating to an attacker host that merely *contains* an allowed host as a
suffix).
**Area:** HARDEN THE ARCHITECTURE — egress-lockdown (item 12) / the mandate's "no PII/IP/regulated escape".
**Files:** `services/mcp-broker/sandbox-image/agent/tests/test_ssrf_ip_classifier.py` (+13 parametrized).
No production code change.
**Whose work it touches:** locks an invariant of the sandbox agent egress guard (`_validate_upstream`,
CHG-0067 area).

## Context — the invariant (verified sound)

`_validate_upstream(upstream)` (services/mcp-broker/sandbox-image/agent/upstream_manager.py) is the single
chokepoint the agent runs before dialing any remote upstream (streamable-http/sse/websocket). It:

- requires a non-empty `allowed_hosts` (fail-closed if missing),
- takes the URL host, `_normalize_host`es it (lowercase, `rstrip(".")`, IDNA-encode) and the allowlist
  entries the same way, then requires `norm_host in allowed_norm` — **exact set membership**, not a
  substring/suffix test,
- raises `UpstreamError(-32002, "egress denied ...")` on a miss.

Paired with `_assert_upstream_not_ssrf` (resolved-IP block, CHG-0067/0138), this is the sandbox-side
egress-lockdown. The exact-match logic is structurally correct today.

## The gap (coverage, not behavior)

The only existing allowlist test (`test_upstream_proxy.py::test_egress_denied_for_host_not_in_allowlist`)
covers a *wholly-different* host (`evil.example.com` vs allowed `mcp.example.com`). It does NOT cover the
edge cases where allowlist bugs classically hide:

- a **subdomain** of an allowed host (`evil.mcp.example.com`),
- a **string-suffix** attack that is NOT label-aligned (`notmcp.example.com` ends with the string
  `mcp.example.com`) — the exact case a naive `endswith` would wrongly allow,
- an allowed host as a **left label** of an attacker domain (`mcp.example.com.evil.com`),
- **normalization** cases that must still ALLOW (case-insensitive URL/allowlist, trailing FQDN dot on
  either side).

Without these, a future "let's support subdomains" refactor to `endswith`/`in` would silently open an
egress-exfil bypass with a green suite.

## The lock (CHG-0147)

`test_egress_allowlist_denies_non_exact` (parametrized): subdomain / suffix-string / left-label /
different-host / IP-not-in-list → all raise `egress denied` (-32002).
`test_egress_allowlist_allows_normalized_exact` (parametrized): exact / case-insensitive (both sides) /
trailing-dot (both sides) / IP-exact / second-of-multiple → `_validate_upstream` returns `None` (no raise).
`test_egress_allowlist_required_fail_closed`: empty `allowed_hosts` → raises "allowed_hosts is required".

## Verification

```
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_ssrf_ip_classifier.py -q   # 36 passed (13 new)
```

`suffix_string_attack` (`notmcp.example.com` DENIED) proves the match is exact set membership, not
`endswith`.

## Scope / honesty note

Test-only regression-lock; no behavior change. `_validate_upstream` is a pure function (returns before any
dial), so the tests need no network / event-loop and are Python-version-agnostic (they run on the 3.14 test
venv even though the sandbox image is 3.12).

Documented RESIDUALS (found this iteration, deliberately NOT shipped):
1. `services/mcp-broker/src/sandbox/routes.py` `_forward_sandbox_rpc` reads the sandbox agent's response via
   `response.json()` / `response.text` with NO size cap — a buggy/compromised (gVisor-contained,
   auth'd, self-caps at 8MB) per-org agent returning a huge body could OOM the SHARED broker. A real fix
   needs flipping `client.post` → capped `client.stream` in `_post_agent_rpc`, which breaks every test that
   mocks `client.post` (high blast radius) for a LOW-severity scenario — deferred, not a clean fit.
2. sandbox agent `_log_stderr` stderr-flood self-hang (LOW; the fix is Python-3.12-vs-3.14 asyncio
   version-dependent and can't be verified against the prod 3.12 runtime here — see auto-memory
   `sandbox-py312-vs-test-py314-asyncio`).

Does not change the host-blocked live-stress status. Partial coverage is not completion.
