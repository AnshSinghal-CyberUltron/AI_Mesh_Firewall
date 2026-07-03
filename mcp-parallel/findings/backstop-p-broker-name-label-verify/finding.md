# CHG-0112 — broker by-name container lookup didn't verify the org label (cross-tenant residual)

**Change-id:** CHG-0112
**Date:** 2026-07-03
**Severity:** MEDIUM–HIGH (cross-tenant residual — a name-matched-but-mislabeled container could serve the WRONG org; most acute during the CHG-0111 transition while pre-fix collided containers still run).
**Area:** HARDEN THE ARCHITECTURE — broker tenant isolation (defense-in-depth completion of CHG-0111).
**Files:** `services/mcp-broker/src/sandbox/docker_manager.py` (`get_container_by_name` + new `_container_labels`; `list_sandbox_containers` deduped); `services/mcp-broker/tests/test_sandbox_lifecycle.py` (+4 tests, realistic mock labels); `services/mcp-broker/tests/test_sandbox_routes.py` (realistic mock labels).
**Whose work it touches:** broker `DockerManager` container resolution; completes CHG-0111.

## Root cause

`find_container(org_slug)` resolves the org's sandbox:

```python
def find_container(self, org_slug):
    by_name = self.get_container_by_name(org_slug)   # <-- tried FIRST
    if by_name is not None:
        return by_name
    # …else authoritative label filter: containers.list(label=[role, org=slug])

def get_container_by_name(self, org_slug):            # BEFORE
    try:
        return self.client.containers.get(self.container_name(org_slug))  # pure NAME lookup
    except Exception:
        return None
```

`container_name` is derived by a LOSSY sanitizer, so a NAME match is NOT proof of tenancy. A container that owns
the deterministic name but carries a DIFFERENT `LABEL_ORG_SLUG` (a legacy/renamed/reused container) would be
returned for the WRONG org. CHG-0111 stops NEW colliding slugs at the route, but a container CREATED before
CHG-0111 for a colliding slug — e.g. one made for org `acme/prod` and still owning the name `…-acme-prod` — would
be matched by name for the now-canonical org `acme-prod`, with its label still `acme/prod`. Input validation
alone can't cover this: the mislabeled container already exists. So org `acme-prod`'s RPC would hit `acme/prod`'s
leftover sandbox — a cross-tenant residual.

## The fix (CHG-0112)

`get_container_by_name` now VERIFIES the org label before returning a name match:

```python
def get_container_by_name(self, org_slug):           # AFTER
    try:
        container = self.client.containers.get(self.container_name(org_slug))
    except Exception:
        return None
    labels = self._container_labels(container)
    if labels.get(LABEL_ORG_SLUG) != org_slug or labels.get(LABEL_ROLE) != ROLE_VALUE:
        logger.warning("sandbox name/label mismatch … (ignoring by-name match, fail-closed)")
        return None                                   # fail closed → fall through to label filter
    return container
```

The org **LABEL** (`ai_mesh.org_slug`) + role (`ai_mesh.role == mcp-sandbox`) are now the authoritative tenant
key; the deterministic name is only an optimization. On any mismatch the by-name path returns None and
`find_container` falls through to the label-filtered `containers.list`, which is authoritative. `_container_labels`
is robust to the SDK `.labels` dict or the raw `attrs.Config.Labels` shape (the same pattern
`list_sandbox_containers` used, now deduped onto the helper).

## Verification (all green)

```
cd services/mcp-broker
./.venv/bin/python -m pytest tests/test_sandbox_lifecycle.py -q -k "by_name or foreign_label or org_label or name_match"  # 4 passed
./.venv/bin/python -m pytest tests -q -k "not websocket"                                                                  # 150 passed
```

4 new tests: matching label → container returned; mismatched org label → None (fail-closed, no cross-tenant);
missing/wrong role label → None; `find_container` ignores a foreign-labeled name-match and uses the label filter →
returns the CORRECT org's container. The two `_mock_container` helpers were made realistic (real sandbox
containers ALWAYS carry these labels — `ensure` sets `manager.labels(org_slug)` at creation); this also silenced
the fail-closed warnings the incomplete mocks triggered. Gateway unaffected (broker-only change).

## Scope / honesty note

Defense-in-depth completion of CHG-0111: input-validation (reject non-canonical slugs) + label-verification
(never trust a name match) together make the org LABEL the authoritative tenant key end-to-end. Cross-tenant
ROUTING/isolation fix — no PII-text egress delta, so aidefence is not the applicable oracle; the
label-mismatch → None behavior + the fall-through-to-label-filter are authoritative. Does not change the
host-blocked live-stress status (items 14–20). Partial coverage is not completion.
