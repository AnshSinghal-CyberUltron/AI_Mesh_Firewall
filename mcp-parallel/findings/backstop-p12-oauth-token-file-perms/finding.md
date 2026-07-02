# CHG-0085 — OAuth token files written world-readable to /tmp (credential-at-rest leak)

**Change-id:** CHG-0085
**Date:** 2026-07-02
**Severity:** HIGH (OAuth access/refresh tokens + client_secret + PKCE code_verifier readable by any co-located process/tenant on the shared host)
**Area:** HARDEN THE ARCHITECTURE — credential-at-rest hygiene (gateway auth/secrets handling)
**Files:** `gateway/ai_mesh_gateway/mcp_oauth_proxy.py` (+ `tests/test_oauth_token_file_perms.py`)
**Whose work it touches:** the MCP OAuth proxy's mcp-remote token-file persistence (CHG-0042 lineage).

## How it was found (security review of the OAuth proxy)

Reviewing the OAuth proxy (PKCE, at-rest Redis encryption CHG-0042, one-time state, org-scoped token keys
all present), `_write_mcp_remote_tokens` persists token files to disk for the mcp-remote CLI.

## Gap

`_write_mcp_remote_tokens` wrote, per mcp-remote version dir under `/tmp/mcp-orgs/{org}/mcp-auth/…`:
- `{hash}_tokens.json` — **access_token + refresh_token**
- `{hash}_client_info.json` — **client_secret**
- `{hash}_code_verifier.txt` — **PKCE code_verifier**

via `Path.write_text(...)` + `Path.mkdir(parents=True, exist_ok=True)` — i.e. **default permissions**.
Verified on this host: `write_text` → mode **0664** (group+world readable), `mkdir` → **0775**
(world-traversable). So on the shared gateway host, ANY co-located process / user / neighbouring tenant
could read another org's OAuth credentials at rest → account takeover of that org's upstream MCP server.
(The Redis copies were already encrypted at rest per CHG-0042; the on-disk copies were not protected.)

## Fix

- New `_write_secure_text(path, content)` creates each file with `os.open(..., O_CREAT, 0o600)` (the
  restrictive mode is applied at creation — no world-readable window — 0600 is umask-proof) and re-chmods
  0600 in case the file pre-existed with looser perms.
- `_write_mcp_remote_tokens` chmods the whole org credential tree (`/tmp/mcp-orgs/{org}`, its `mcp-auth`,
  and each `mcp-remote-{ver}` dir) to **0700** — idempotent, and it also tightens dirs that
  `mkdir(exist_ok=True)` would otherwise leave at their old looser mode. 0700 on the org dir blocks another
  user from traversing in to the token files.

## Behaviour after fix (verified)

- All dirs under `/tmp/mcp-orgs/{org}` → `0700`; all token files → `0600`.
- `os.walk` over the org tree finds **zero** paths with any group/world bit (`mode & 0o077 == 0`).
- Token/secret/verifier content is intact (secrets are still written, just owner-only).
- `_write_secure_text` tightens a pre-existing world-readable file to 0600 on re-write.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_oauth_token_file_perms.py -q   # 2 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q -k oauth                          # 15 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                   # 1449 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- The files live under `/tmp` on the host that runs the OAuth callback. Owner-only perms close the
  shared-host read vector; a stronger posture would write them inside the per-tenant gVisor sandbox's
  isolated FS (or a tmpfs mount) — a broader architecture change (item 12 egress/isolation) for a future
  pass.
- Consider shredding/deleting these on token revocation/expiry (currently overwritten on refresh).
