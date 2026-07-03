# CHG-0111 — broker org_slug sanitize-collision → cross-tenant sandbox sharing

**Change-id:** CHG-0111
**Date:** 2026-07-03
**Severity:** HIGH (cross-tenant isolation break — two DISTINCT orgs could execute in / destroy / observe the SAME sandbox container).
**Area:** HARDEN THE ARCHITECTURE — broker auth/validation at the tenant isolation boundary; the CODE root-cause behind a cross-tenant canary (G5 item 19).
**Files:** `services/mcp-broker/src/sandbox/routes.py` (`_require_canonical_org_slug` + all `/{org_slug}/` routes); `services/mcp-broker/tests/test_sandbox_org_slug_validation.py` (new, 26 tests).
**Whose work it touches:** the broker sandbox routes (`build_sandbox_router`); complements CHG-0052 (broker RPC logging) and the per-org sandbox architecture (P4.13/P6.18).

## Root cause

The broker key (`X-MCP-Broker-Key` = `MCP_BROKER_INTERNAL_KEY`) is a **shared secret** — one key for ALL orgs,
NOT per-tenant. So the path `org_slug` (`/v1/sandbox/{org_slug}/rpc`) is the **sole tenant selector** for sandbox
routing. `DockerManager` derives the container/volume/network name by **lossily sanitizing** the slug:

```python
def container_name(self, org_slug):
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", org_slug).strip("-") or "default"
    return f"…-{safe}"
```

and `find_container` matches by that **name first** (`get_container_by_name`) before the exact `LABEL_ORG_SLUG`
filter. **No route validated the slug.** So two DISTINCT slugs collapse onto ONE container name:

| slug A | slug B | shared container |
|--------|--------|------------------|
| `acme/prod` | `acme-prod` | `…-acme-prod` |
| `acme prod` | `acme-prod` | `…-acme-prod` |
| `-acme` / `acme-` | `acme` | `…-acme` |
| `teñant` | `te-ant` | `…-te-ant` |
| `""` / `///` / `!!!` | (any other invalid) | `…-default` |

Impact of a colliding/invalid slug:
- `POST /{slug}/rpc` → org B's tool call executes inside **org A's** sandbox (cross-tenant execution + data access).
- `DELETE /{slug}` → destroys **the wrong org's** container (cross-tenant DoS).
- `GET /{slug}/status` → returns **another org's** running process list.

This defeats the per-tenant isolation the whole sandbox architecture is built on, at the broker boundary.

### Byte-level proof (pre-fix) — pure `container_name`

`container_name("acme/prod") == container_name("acme-prod")` (both `…-acme-prod`); `container_name("-acme") ==
container_name("acme")`; `container_name("") == container_name("///") == "…-default"`.

## The fix (CHG-0111)

A boundary validator, applied at every `/{org_slug}/` route (`/ensure`, `/rpc`, `/stdio/rpc`, `/status`,
`DELETE /{org_slug}`):

```python
def _require_canonical_org_slug(org_slug: str) -> None:
    if (not org_slug or len(org_slug) > 64
            or re.sub(r"[^a-zA-Z0-9_.-]", "-", org_slug).strip("-") != org_slug):
        raise HTTPException(status_code=400, detail="Invalid org_slug")
```

It **fails CLOSED (400)** on any slug the sanitizer would alter — accepting ONLY a slug that maps **1:1** to its
container name (`sanitize(slug) == slug`, non-empty, ≤64 chars, no leading/trailing `-`, chars ∈ `[a-zA-Z0-9_.-]`).
Distinct tenants can no longer collide onto one sandbox. (An encoded-**slash** slug like `acme%2Fprod` is
additionally rejected by FastAPI path routing — 404 — before the handler, since `{org_slug}` cannot span `/`.)
Canonical slugs (`acme`, `acme-prod`, `acme_corp`, `acme.dev`, `Acme123`) are unaffected.

## Verification (all green)

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_org_slug_validation.py -q   # 26 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"                    # 146 passed
```

26 tests: the validator rejects 12 collision/oversize/empty vectors and accepts 8 canonical slugs (asserting
`sanitize(slug)==slug` for each); each of `/rpc`, `/ensure`, `/status`, `DELETE` returns **400** on a colliding
slug; a canonical slug still routes (→ 503 mock-docker-unavailable, NOT 400 — validation passed). Gateway
unaffected (no gateway files; the gateway `broker_send_rpc` posts to the broker over HTTP and supplies validated
org slugs).

## Scope / honesty note

Cross-tenant ROUTING / isolation fix at the broker boundary — there is no PII-text egress delta, so aidefence is
not the applicable oracle; the byte-level container-name collision demonstration + the route-level 400 rejections
are authoritative. In production the gateway supplies validated Django org slugs, so a real collision required a
non-canonical slug reaching the broker (a gateway bug, a future/other caller, or a non-slugified org id) — this
closes it fail-closed at the isolation boundary regardless, per validate-input-at-boundaries. Does not change the
host-blocked live-stress status (items 14–20); it removes a real collision the 500-scale cross-tenant canary
(item 19) would otherwise be needed to catch. Partial coverage is not completion.
