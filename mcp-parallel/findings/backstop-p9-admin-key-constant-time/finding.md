# CHG-0144 — admin-RBAC bypass compared the shared internal key with a non-constant-time `==` (timing side-channel)

**Change-id:** CHG-0144
**Date:** 2026-07-03
**Severity:** LOW-MEDIUM (timing side-channel on a security-critical control — the server-to-server bypass of
admin-RBAC on `/v1/admin/*`. A plain `==` short-circuits on the first differing byte, leaking the secret's
length/prefix via response timing; an attacker who can call the admin endpoints could recover the key
byte-by-byte. Mitigated in practice by network placement, but it is a real crypto-hygiene omission.)
**Area:** HARDEN THE ARCHITECTURE — gateway auth/authz (item 9). Consistency with the codebase's own
constant-time-compare convention.
**Files:** `gateway/ai_mesh_gateway/main.py` (`_require_admin_role`, +`import hmac`);
`gateway/ai_mesh_gateway/tests/test_admin_internal_key_constant_time.py` (new, +4).
**Whose work it touches:** the gateway admin-gate shim (`_require_admin_role`).

## Root cause

`_require_admin_role` lets the control plane bypass admin-RBAC on `/v1/admin/*` by presenting the shared
`GATEWAY_INTERNAL_API_KEY` in the `X-Gateway-Internal-Key` header:

```python
internal_key = os.environ.get("GATEWAY_INTERNAL_API_KEY", "").strip()
if internal_key:
    header_key = (request.headers.get("x-gateway-internal-key") or "").strip()
    if header_key and header_key == internal_key:      # <-- non-constant-time
        return None
```

`str.__eq__` returns as soon as two bytes differ, so the comparison time correlates with the length of the
matching prefix — the classic secret-comparison timing oracle. Every other secret comparison in the gateway
already uses constant-time `hmac.compare_digest`:

- `mcp_proxy._valid_internal_key` (`mcp_proxy.py:114`)
- `middleware.py:301` (internal-secret gate)
- `metrics_auth.py:35` (`secrets.compare_digest`)
- `policy_signing.py:69`

So this admin-gate shim was the lone plain-`==` omission — an inconsistency, not a deliberate choice.

## The fix (CHG-0144)

```python
if header_key and hmac.compare_digest(header_key, internal_key):
    return None
```

`hmac.compare_digest` takes the same time regardless of where the first mismatch is. Behaviour is otherwise
identical: the correct key still bypasses (returns `None`); any wrong key falls through to
`require_admin(auth_ctx)` and is rejected. Added `import hmac` to the top of `main.py`.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_admin_internal_key_constant_time.py -q   # 4 passed
```

New tests: correct key → `None` (bypass preserved); a same-length one-byte-off key → not `None` (401/403);
missing header → not `None`; a source guard asserting `hmac.compare_digest(header_key, internal_key)` is
present and no `header_key == internal_key` survives (locks the fix against regression).

## Scope / honesty note

Full gateway suite currently shows **6–7 pre-existing failures** in an UNRELATED subsystem
(`test_mcp_obfuscated_cred_in_pii.py` zero-width-unicode, `test_mcp_unicode_deobfuscation.py`,
`test_mcp_tools_list_audit.py::test_block_is_audited`) that another session is actively editing. I proved
they are **not** caused by this change by reverting `main.py` to HEAD and reproducing the same 6 failures on
a clean tree (my edit does not touch that scan/redaction path — those tests never reference
`_require_admin_role` or the internal key). This change is isolated to the admin-key comparison and adds no
new failure. It does not alter the host-blocked live-stress status. Partial coverage is not completion.
