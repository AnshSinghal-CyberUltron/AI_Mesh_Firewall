# BACKSTOP audit — sandbox-agent egress hygiene + container security + cross-tenant harness oracle

CHG-0035 (2026-07-02) — read-only verification. Motivated by the CHG-0033 credential-leak
finding: I audited every OTHER security-critical egress/isolation path for a similar class of
gap. All clean; recorded here as evidence for G3 items 10/12 + G5 item 19.

## 1. Sandbox agent HTTP upstream dialing — CLEAN
`services/mcp-broker/sandbox-image/agent/upstream_manager.py`
- `httpx.AsyncClient(timeout=httpx.Timeout(connect_timeout, read=connect_timeout + _METHOD_TIMEOUT),
  follow_redirects=False)` — redirects are NOT followed, so a malicious/compromised upstream cannot
  redirect the sandbox to an internal/metadata host (no SSRF-via-redirect / egress bypass).
- No `verify=False` ANYWHERE in `services/mcp-broker/` (excluding tests) — TLS certificate
  verification is the httpx default (enabled). No MITM window.
- Per-method timeouts are set on the POST calls (no unbounded upstream read).

## 2. Sandbox agent WebSocket upstream — CLEAN
`services/mcp-broker/sandbox-image/agent/ws_manager.py`
- `_validate_ws_url` rejects any scheme that is not `ws`/`wss`.
- `websockets.connect(url, additional_headers=..., open_timeout=connect_timeout)` passes NO `ssl=`
  override, so a `wss://` upstream uses the library's default SSL context (cert verify + hostname
  check ON). `open_timeout` + an outer `asyncio.wait_for` bound the handshake.
- No `CERT_NONE` / `check_hostname=False` / `ssl._create_unverified` anywhere in the broker/sandbox.

## 3. Sandbox container security posture — CLEAN (G3 items 10/12)
`services/mcp-broker/src/sandbox/docker_manager.py` `_run_kwargs` / `_security_opts`
- `security_opt = ["no-new-privileges:true"]` + optional `seccomp=<MCP_SANDBOX_SECCOMP_PROFILE>`.
  When the env is unset, Docker's built-in DEFAULT seccomp profile still applies — the code
  explicitly does NOT pass `seccomp=unconfined`.
- `cap_drop = ["ALL"]`, `read_only = True` (read-only rootfs), `pids_limit = 256`,
  `mem_limit` + `memswap_limit = mem_limit` (⇒ swap DISABLED — no swap-based mem-limit bypass),
  `nano_cpus` (CPU cap), writable `tmpfs` only for the npm/uv caches.
- RESIDUAL (unchanged, item 12): runtime is runc (gVisor/runsc not installed on the host) and the
  per-org networks are `internal=false` (open NAT — no network-level egress default-deny). Both are
  INFRA prerequisites, not code gaps.

## 4. Cross-tenant multi-org harness oracle — SOUND (G5 item 19)
`scripts/mcp_multi_org_harness.py` (another session's harness)
- `call_echo_conc`: canary `conc~<org>~<server>~r<rnd>~s<seq>~<rid8>` embeds the target; the
  response is byte-compared to the exact expected echo AND the returned canary's `<org>~<server>`
  owner is compared to the caller's target — a response carrying a DIFFERENT target's canary is a
  cross-target mix (`cross=True`). JSON-RPC `id` round-trip is also asserted.
- `call_cross_tenant`: attacker key vs victim org path — `rejected = status in (401,403)`; ANY 2xx
  (or any non-401/403) counts as a failure ⇒ fail-closed (an accepted cross-tenant call is a breach).
- Minor (non-blocking): `call_get_sum_conc` operands are `abs(hash((org,server,...)))%800`, so two
  targets can collide (~1/640k per pair); the echo canary is the definitive cross-wire detector, so
  this only affects the redundant get-sum signal, never a false PASS.

## Verify commands
```
grep -n "follow_redirects\|verify=\|Timeout" services/mcp-broker/sandbox-image/agent/upstream_manager.py
grep -rn "CERT_NONE\|check_hostname\s*=\s*False\|verify=False" services/mcp-broker/ | grep -v test   # → none
grep -n "security_opt\|cap_drop\|read_only\|memswap_limit\|seccomp" services/mcp-broker/src/sandbox/docker_manager.py
```
