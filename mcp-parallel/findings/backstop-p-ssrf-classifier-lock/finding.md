# CHG-0138 — regression-lock: agent SSRF IP classifier blocks IPv6 + IPv4-mapped-IPv6 + all internal ranges

**Change-id:** CHG-0138
**Date:** 2026-07-03
**Severity:** LOW (test-only regression-lock — **no production code change**). Locks a verified-but-untested SSRF-egress invariant so a classifier refactor (or a Python `ipaddress` behavior change) can't silently re-open sandbox egress to cloud metadata / internal services.
**Area:** HARDEN THE ARCHITECTURE — egress SSRF guard on the untrusted-upstream dial (complements CHG-0067 resolved-IP SSRF, CHG-0069 error-body cap).
**Files:** `services/mcp-broker/sandbox-image/agent/tests/test_ssrf_ip_classifier.py` (new, +23 assertions). No `upstream_manager.py` change.
**Whose work it touches:** locks an invariant of the owning-session agent SSRF guard (`_resolved_ip_blocked` / `_assert_upstream_not_ssrf`).

## Context — the guard (verified sound)

The sandbox agent dials every remote upstream itself (org-scoped egress). Before dialing it runs
`_assert_upstream_not_ssrf(host)`: it resolves the host (or uses a literal IP) and rejects if any resolved IP is
internal via `_resolved_ip_blocked` — which blocks `_METADATA_IPS` (`169.254.169.254`, `fd00:ec2::254`) plus
anything `ipaddress` flags `is_private / is_loopback / is_link_local / is_reserved / is_multicast /
is_unspecified`, and fail-closes on an unparseable/unresolvable host. It checks **all** resolved IPs
(DNS-rebinding across A/AAAA records).

I byte-probed the classic **IPv4-mapped-IPv6** SSRF bypass (`::ffff:169.254.169.254`,
`::ffff:10.0.0.1`) and full IPv6 battery: **all blocked** — Python 3.14's `ipaddress` classifies the mapped form
as `is_private=True`, so the mapped-IPv6 metadata/private addresses are caught. Public IPv4/IPv6 (8.8.8.8,
`2606:4700:4700::1111`) are allowed. No bypass.

## The gap (coverage, not behavior)

The existing SSRF tests (`test_upstream_proxy.py`) cover the *flow* (an internal-resolving host → egress denied)
but had **no test of the classifier's coverage** across IPv6, the IPv4-mapped-IPv6 bypass, ULA, link-local,
metadata, reserved, and unspecified classes. A refactor of `_resolved_ip_blocked` (or a Python version whose
`ipaddress` mis-handles mapped IPv6) that dropped IPv6/mapped coverage would silently re-open SSRF egress with a
green suite.

## The lock (CHG-0138)

`test_ssrf_ip_classifier.py` parametrizes a full **must-block** battery (IPv4 metadata/private/loopback/link-local;
IPv6 metadata/loopback/link-local/ULA; **IPv4-mapped-IPv6** metadata & private; unspecified; unparseable →
fail-closed) and a **must-allow** set (public IPv4/IPv6), and asserts `_resolved_ip_blocked` blocks/allows each.
Two async tests lock the end-to-end `_assert_upstream_not_ssrf` on a literal mapped-IPv6 metadata host (→ egress
denied) and a public literal IP (→ allowed).

## Verification

```
cd services/mcp-broker/sandbox-image/agent
../../.venv/bin/python -m pytest tests/test_ssrf_ip_classifier.py -q   # 23 passed
../../.venv/bin/python -m pytest tests --collect-only -q               # collects cleanly (107 tests)
```

## Scope / honesty note

Test-only regression-lock; no behavior change. A known residual remains (out of scope here): a DNS-rebinding
**TOCTOU** — the guard resolves+validates the host, then httpx re-resolves at connect time, so an attacker-
controlled low-TTL domain could rebind to an internal IP between check and connect. A clean fix needs
connection-level IP pinning that httpx does not easily support (documented for a future infra iteration). Does
not change the host-blocked live-stress status (items 14–19). Partial coverage is not completion.
