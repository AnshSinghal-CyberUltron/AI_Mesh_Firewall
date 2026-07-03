# CHG-0113 — broker destroy removed the org VOLUME by name without label verification

**Change-id:** CHG-0113
**Date:** 2026-07-03
**Severity:** LOW–MEDIUM (cross-tenant DATA DESTRUCTION residual — `destroy` could remove another org's auth volume via a colliding/legacy name; narrow transition-window since CHG-0111 prevents new name collisions).
**Area:** HARDEN THE ARCHITECTURE — broker tenant isolation (defense-in-depth completion of CHG-0111/0112 across the volume resource).
**Files:** `services/mcp-broker/src/sandbox/docker_manager.py` (`_volume_labels`, `_ensure_volume`, `create_container`, `destroy`); `services/mcp-broker/tests/test_sandbox_lifecycle.py` (+5 tests).
**Whose work it touches:** broker `DockerManager` volume lifecycle; completes CHG-0111 (slug validation) + CHG-0112 (container label verification).

## Root cause

CHG-0112 made the org LABEL the authoritative tenant key for the **container** (`find_container` verifies
`LABEL_ORG_SLUG`). But `destroy` removed the org's auth **volume** (`/data/mcp-auth`, holding OAuth/creds) purely
by its `volume_name(org_slug)` — a LOSSY-sanitized name — with NO label check:

```python
volume_name = self.volume_name(org_slug)
volume = self.client.volumes.get(volume_name)
volume.remove(force=True)          # BEFORE: name-only, no tenancy check
```

The volume was AUTO-created (unlabeled) by the container run (`volumes={name: {bind: /data/mcp-auth}}`), so it
carried no org label to verify. A legacy/reused volume owning the canonical name but belonging to a DIFFERENT org
(e.g. a pre-CHG-0111 volume created for a colliding slug) would be DESTROYED for the wrong org — the volume-resource
twin of the CHG-0112 container residual (cross-tenant data destruction).

## The fix (CHG-0113)

1. **Label the volume at creation** — `_ensure_volume(org_slug)` explicitly `client.volumes.create(name, labels=
   labels(org_slug))` before the container run (called at the top of `create_container`). Idempotent + best-effort:
   a pre-existing volume is left as-is (Docker won't relabel); a failure never blocks provisioning because the run
   still auto-creates the volume by name. So NEW volumes carry the authoritative org label.
2. **Verify the label on destroy** — `destroy` reads `_volume_labels(volume)` and REFUSES to remove a volume whose
   `LABEL_ORG_SLUG` is present AND differs from the requested org (logs a warning + skips, fail-closed). An unlabeled
   legacy volume (name authoritative for canonical slugs post-CHG-0111) or a same-org volume is still removed.

The org LABEL is now the tenant key for the volume too — parity with the container (CHG-0112).

## Verification (all green)

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q -k volume   # 8 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"                    # 155 passed (was 150 + 5)
```

5 new tests: `_ensure_volume` creates a labeled volume when missing (asserts `labels[LABEL_ORG_SLUG]`); idempotent
when the volume already exists (`create` not called); `destroy` removes an unlabeled legacy volume; removes a
same-org volume; and REFUSES a foreign-labeled volume (`remove` NOT called — fail-closed, no cross-tenant
destruction). Gateway unaffected (broker-only). Oracle N/A — cross-tenant routing/isolation fix, no PII-text egress
delta; the foreign-label → skip behavior is authoritative.

## Cross-tenant surface audit (this iteration — verification-only, no code change)

Before landing CHG-0113 I adversarially re-probed the other tenant-selector surfaces; all were ALREADY hardened by
prior work (recorded so future sessions don't re-drill):
- **gateway OAuth token store** (`mcp_oauth_proxy.py`): Redis key `mcp:oauth:token:{org_slug}|{server_url}` is
  org-prefixed by a clean Django slug (delimiter collision needs `|` in org_slug → not reachable). `oauth_start` /
  `oauth_status` are `_require_org_scope`-gated; `oauth_callback` is the only unauth route, bound by a one-time
  signed `state` + PKCE, with org/server/secret taken from the TRUSTED flow record (not callback params); status
  returns only booleans/timestamps (no token material); the callback HTML is XSS-escaped (B3).
- **control-plane** (`mcp_connector/views.py`): `_request_org` derives the tenant from the authenticated profile
  (JWT) or a `secrets.compare_digest`-validated gateway-internal `X-Org-Slug` header — a client-supplied org is NOT
  honored and cannot be spoofed by a JWT user (profile wins). `_record_event` sanitizes ALL channels
  (`_sanitize_event_text` scalars + `_sanitize_event_structure` metadata/tags/findings) and enforces tenant +
  observability invariants.
- **gateway tool-call cap** `_incr_tool_call_count` keys on `key_hash` (per-API-key, per-tenant).

**Flagged (not fixed — deferred to a GATED iteration):** `_gateway_request_org` honors `X-Org-Slug` relying on the
caller's view permission gate rather than self-verifying the internal secret. NOT reachable today (all callers are
`IsAuthenticatedOrGatewayInternal` / `IsGatewayInternalOnly`), but a defense-in-depth self-verify would harden it
against a FUTURE weaker-permission view. Deferred because the control-plane pytest env is UNAVAILABLE in this shared
VM (no venv; `django`/`fakeredis` not importable), so the change could not be gated — parity with the host-blocked
live-stress items. Recorded for a session with a working Django test env.

## Scope / honesty note

Defense-in-depth completion of CHG-0111/0112 across the volume resource; the org LABEL is now the authoritative
tenant key for slug-validation, container lookup, AND volume destroy. LOW–MEDIUM severity (narrow transition-window,
data destruction not leak). Does not change the host-blocked live-stress status (items 14–20). Partial coverage is
not completion.
