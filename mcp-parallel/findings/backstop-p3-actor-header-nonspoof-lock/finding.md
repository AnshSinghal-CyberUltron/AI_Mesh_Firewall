# CHG-0145 — regression-lock: backend-bound actor-authz headers are non-spoofable from inbound headers

**Change-id:** CHG-0145
**Date:** 2026-07-03
**Severity:** LOW (test-only regression-lock — **no production code change**). Locks a verified-sound 1.4
invariant: the actor identity forwarded to the backend policy engine (`X-Gateway-Roles` / `-User-Id` /
`-Key-Prefix` / `-Project-Id`) is derived only from the server-side auth context and cannot be spoofed by a
client-supplied inbound header. A spoofable `X-Gateway-Roles` would be a role-escalation that bypasses
per-user/agent/role tool authorization.
**Area:** HARDEN 1.4 — per-user/agent/role tool authorization (item 3); the actor-keyed authz the mandate
repeatedly emphasizes.
**Files:** `gateway/ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py` (+2 tests + 1 helper). No production
code change.
**Whose work it touches:** locks an invariant of the gateway→backend proxy-header construction
(`_backend_proxy_headers` / `_control_request_headers`).

## Context — the invariant (verified sound)

The backend policy engine evaluates `Policy.allowed_roles` / user / project using the actor headers the
gateway attaches on server-to-server calls. Those headers are built by `_backend_proxy_headers`:

- `_control_request_headers(org_slug, extra)` returns a FRESH dict (`Content-Type`, `Accept`, `Host`,
  `X-Gateway-Auth`, `X-Org-Slug`, `X-Gateway-Internal-Key`) — it takes no request and copies **no** inbound
  header.
- `_backend_proxy_headers` then sets `X-Gateway-User-Id` / `-Key-Prefix` / `-Project-Id` / `-Roles` from
  `_get_auth_context(request)` (= `request.state.auth_context`, populated by the auth middleware from the
  key's Redis payload). `AuthContext.roles` is documented as "populated by `GatewayAPIKey.build_redis_payload`
  from `owner.profile.roles`" — server-derived, not caller-supplied.

I verified the gateway **never reads** `X-Gateway-Roles`/`-User-Id` from an inbound request (grep: zero
hits), and no live path forwards inbound headers wholesale to the backend (the only wholesale-forwarder,
`_proxy`, is dead code with no callers). So a client cannot spoof the actor identity today.

## The gap (coverage, not behavior)

There was **no test** asserting this non-spoofability. A future change — `_backend_proxy_headers` merging
`request.headers`, a revived `_proxy`, or a new route trusting an inbound `X-Gateway-Roles` — would silently
open a role-escalation path with a green suite.

## The lock (CHG-0145)

`test_backend_actor_headers_come_from_auth_not_inbound_spoof`: builds an `AuthContext`
(`roles=["viewer"]`, `user_id=42`, `prefix="ak_live_real"`, `project_id="proj-real"`) and a request whose
**inbound** headers try to spoof `X-Gateway-Roles: admin,superuser` / `X-Gateway-User-Id: 999999` / etc.
Asserts the produced backend headers carry the auth-context values (`42`, `ak_live_real`, `proj-real`,
`viewer`) and that **none** of the spoofed values (`admin`, `superuser`, `999999`, …) appear anywhere in the
header set. `test_backend_headers_omit_roles_when_auth_has_none`: empty `roles` → no `X-Gateway-Roles`
header emitted, and the inbound `admin,superuser` still does not leak.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_mcp_bare_proxy_scan.py -q   # 60 passed (2 new)
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                               # 2002 passed, 0 failed
```

## Scope / honesty note

Test-only regression-lock; no behavior change. Proves + locks that the actor-authz forwarding is
non-spoofable via inbound headers. It does not add ingress stripping of client-supplied `X-Gateway-*`
headers (deliberately declined: no live path forwards/trusts them, and stripping risks breaking a subtle
header-propagation path that can't be integration-tested here — the lock guards the real invariant instead).
Does not change the host-blocked live-stress status. Partial coverage is not completion.
