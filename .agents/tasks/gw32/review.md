# GW32 MCP transport + GW17b credentials + GW17c tool policy (iteration 2 review)

Iteration 2 closes all eight findings from the prior pass: production wiring through `extend_mcp_deps`, the upstream OAuth flow with a pinned-dial token endpoint, policy metrics publication, credential cache invalidation, control endpoint tests run on Postgres, route-level sink coverage, WebSocket hardening, and the cap window decision. The evidence is present and specific (commands, counts, negative controls). I did not re-run suites; I spot-read the OAuth server (S256 required, exact redirect match, constant-time compares), the pinned transport (one resolution, no redirects) and the policy gate.

Watch for: (1) MCP wiring exists only in the dev profile; the production profile builds no MCP deps (pre-existing, tracked as GW36, possible scope risk but recorded); (2) live gate DL-1..DL-7 not run, so staging behaviour is unverified (confirmed); (3) one residual full-suite failure, `check_tenant_scale` on pre-existing `pinecone_transport.py:140` (confirmed pre-existing, untouched).

**Verdict**: APPROVED

## High-level view

Identity is resolved before any JSON-RPC parse, for HTTP and each WebSocket frame, so `initialize` and `ping` are no longer anonymous. The OAuth server stores only HMACs, requires S256, matches redirect URIs exactly and revokes on replay. Policy and cap run inside `_forward`, so every transport shares them, and they refuse before the broker call.

Credentials are held in a repr-redacted attachment, and echoed secrets withhold the reply. Iteration 2 adds route-level assertions over audit, metrics, logs, response and broker headers, with negative controls. The upstream OAuth dial resolves once and pins the approved IP, closing the rebinding gap.

Remaining gaps are deployment-shaped: production profile wiring and the live gate.

<details>
<summary>Issues (3)</summary>

1. **Production profile has no MCP wiring (non-blocking, confirmed)** — only `build_dev_mcp` constructs the services. Ensure GW36 owns this and that the card's handoff states the feature is dev-profile only until then.
2. **Live gate DL-1..DL-7 not run (non-blocking, confirmed)** — run before claiming staging readiness.
3. **Upstream real-socket fixtures and route-table goldens missing (non-blocking, confirmed)** — iteration-1 gaps still open; offline SSE/WebSocket upstream behaviour is covered only by fakes.

</details>

<details>
<summary>Details</summary>

### Scope of wiring
`extend_mcp_deps` is the single construction site and fails closed: no store means no sessions, capped keys refused, OAuth 503; partial OAuth config refuses boot. `test_gw32_boot_wiring.py` exercises the real builder. The production profile gap predates this branch.

### Credential non-leakage and enforcement
`test_gw32_p5_sinks.py` covers audit, exposition, logs, response and broker headers with two negative controls, and `check_credential_flow` gates logging. The control write-back defect (409 instead of uniform 404) was found by running the previously unrun tests and fixed. Enforcement before broker call, the filtered listing, and the cap refusal path are tested in `test_gw32_policy_cap.py` and `test_gw32_l17c.py`.

### Residual
The metadata 3xx refusal is stricter than design (safe). The full-suite failure is the pre-existing pinecone tenant-scale finding.

</details>

<details>
<summary>File map</summary>

Full diff: `git diff suraj-revamp...HEAD` in the worktree (94 files).
- `edge/mcp*.py`, `edge/oauth_routes.py`, `edge/mcp_boot.py`: transports, identity-first, wiring
- `admit/oauth_server.py`, `admit/mcp_cap.py`, `admit/mcp_policy.py`: OAuth and GW17c
- `dispatch/mcp_credentials.py`, `pinned_transport.py`, `mcp_upstream_oauth.py`, `url_guard.py`: GW17b and pinned dial
- `runtime/resources_mcp.py`, `store_keys_mcp.py`, `lint/*`: bounds and gates
- `state_control/*`, control `mcp_connector`: policy projection and credential endpoints
- `tests/*`: offline and property tests
</details>
