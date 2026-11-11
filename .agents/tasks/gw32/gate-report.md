# GW32 — offline gate re-run report

Worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw32`, branch `gw32-mcp-transport`, HEAD
`ae4bbbe4` (`docs: GW32 iteration 2 full offline suite evidence`). This is an independent re-run of
the full OFFLINE gate suite only — no live Docker/staging gate was run, per instruction. No code
changes were needed: every result below matches what `docs/plans/evidence/2026-11-04-gw32/` and
`.agents/tasks/gw32/verification.md` already recorded for iteration 2, and the one failure is a
pre-existing finding from a different card (GW30), outside this branch's diff.

## Environment
- Gateway venv: `/tmp/gw32venv` (Python 3.12.3), `cwd=gateway_v2/`.
- No containers used for this run (control/Django tests were not re-run; they are DB-backed and were
  already green in iteration 2 per `verification.md`).

## Commands run, verbatim, and results

```
cd /work/suraj/AI_Mesh_Firewall/.worktrees/gw32/gateway_v2
PATH=/tmp/gw32venv/bin:$PATH pytest -q -p no:cacheprovider tests
```
Result: **1 failed, 3248 passed, 106 skipped, 40 warnings in 599.75s**
Full output: `docs/plans/evidence/2026-11-04-gw32/pytest-full-gate-final.txt`

The one failure:
```
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
AssertionError: assert [TenantScaleViolation(path='.../dispatch/vector_transports/pinecone_transport.py',
  symbol='keys', line=140, reason='whole-collection store read: cannot be O(changes)')] == []
```
Pre-existing, not introduced by this card: `git log -1 -- gateway_v2/gateway_v2/dispatch/vector_transports/pinecone_transport.py`
shows the file last touched by `564e2ecf feat: GW30 Pinecone live-acceptance gate` — GW30, not GW32.
It is already documented as pre-existing in both iteration-1 and iteration-2 verification notes and
in `docs/plans/2026-11-04-gw32-mcp-transport.md`. **No fix applied** — fixing a different card's
tenant-scale violation is out of scope for this gate re-run and would be an unrequested change to
code GW32 does not own.

```
cd gateway_v2
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_sizes gateway_v2                      # => 0
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_http_outside_edge_resolve gateway_v2  # => 0
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_no_module_mutable gateway_v2          # => 0
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_capacity_literals gateway_v2          # => 0
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_frozen_dataclasses gateway_v2         # => 0
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_dispatch_provenance gateway_v2        # => 0
# repeated for state_control: all => 0
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_tenant_scale gateway_v2
```
Result: **exit 1**, one violation — the same pre-existing `pinecone_transport.py:140` finding above
(§2.1 rule 1 / R2-02: no work proportional to tenants, keys, or records on a serving path).

```
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_credential_flow gateway_v2    # => 0
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_console_inventory             # => 0
PATH=/tmp/gw32venv/bin:$PATH lint-imports
```
Result: `Gateway v2 layers KEPT`, `Resolve imports only domain KEPT`, `No HTTP framework outside
edge KEPT`. Contracts: 3 kept, 0 broken.

```
PATH=/tmp/gw32venv/bin:$PATH ruff check gateway_v2 state_control audit_control lint tests
```
Result: **All checks passed!**

```
PATH=/tmp/gw32venv/bin:$PATH mypy --strict
```
Result: **9 errors in 6 files** (checked 308 source files), all `import-not-found` for optional
vector-store client libraries not installed in this venv (`weaviate`, `qdrant_client`, `pinecone`,
`pymongo`, `elasticsearch`, `opensearchpy`, `chromadb`) — unrelated to GW32, unchanged from both prior
iterations.

```
python -c "from pathlib import Path; from lint.admin_ledger import compare; print(compare(Path('..')))"
```
Result: `[]` (no ledger drift).

```
PATH=/tmp/gw32venv/bin:$PATH python -m lint.check_secret_headers --repo .. --fail-allowlist
```
Result: exit 0, no committed key-shaped secrets.

## Property-test iteration counts (>=100, tagged in-source)

Every file carrying a `# Feature: gw32-mcp-transport, Property N` tag declares or uses an iteration
bound >= 100:

| File | Bound |
|---|---|
| `tests/dispatch/test_gw32_credentials.py` | `ITERATIONS = 200` |
| `tests/edge/test_gw32_p5_sinks.py` | `ITERATIONS = 120` |
| `tests/edge/test_gw32_paths_sse_gate.py` | `range(200)` |
| `tests/edge/test_gw32_oauth_flow.py` | `range(100)` |
| `tests/edge/test_gw32_identity_first.py` | `ITERATIONS = 200` |
| `tests/state_control/test_gw32_p11_principal_roundtrip.py` | `ITERATIONS = 200` |
| `tests/admit/test_gw32_policy_cap.py` | `ITERATIONS = 200` |
| `tests/admit/test_gw32_sessions.py` | `ITERATIONS = 200` |

12 distinct `Property N` tags found across these files (Properties 2–10 and sub-variants, e.g. 4b);
all run at their declared bound.

## Pre-existing vs new

- **Pre-existing, not new**: the single `test_the_shipped_data_plane_is_clean` failure
  (`pinecone_transport.py:140`), attributable to GW30 and already documented in iteration-1 and
  iteration-2 evidence. Everything else — ruff, import-linter, all AST gates except tenant_scale,
  mypy's 9 optional-dependency errors — matches prior iteration-2 results exactly.
- **New**: nothing. No code change was required or made during this gate re-run.

## Deferred live-gate items (DL-1..DL-7) — the human must still run these

Per `docs/plans/2026-11-04-gw32-human-handoff.md`, unchanged and NOT run in this pass:

| Item | What's needed |
|---|---|
| DL-1 | Real MCP client (VS Code / MCP SDK) against `https://<gateway>/gateway/<org>/mcp/<srv>`: initialize, tools/list, tools/call. |
| DL-2 | Real MCP client OAuth discovery + dynamic registration + PKCE + token exchange against a running gateway (`AMF_GATEWAY_PUBLIC_URL`, `AMF_OAUTH_HASH_KEY` set) then a tool call. |
| DL-3 | Real HTTP+SSE and real WebSocket MCP servers through the real broker/sandbox: tools/list, tools/call, sandbox host allowlist, SSRF check. |
| DL-4 | Real token server + real OAuth server with real refresh through control/broker/sandbox; grep all logs/audit/responses for the secret; check revocation within registry TTL; two real orgs on one stack; OAuth refresh over the pinned transport against a real IdP. |
| DL-5 | Edit a key's allow-list/cap in the console; confirm propagation within the 5 s identity freshness bound and that the cap holds across real workers on real Valkey. |
| DL-6 | The GW26 MCP live claims this card unblocks. |
| DL-7 | C2 MCP replay (LGW17-7) and fleet-scale LGW17-4, deferred as in GW17. |

Also still open from the handoff doc: control-plane Django tests need a throwaway Postgres+Redis
(not SQLite); production-profile MCP wiring is out of scope (belongs to GW36); offline real-socket
upstream fixtures and pre-change route-table goldens remain not done (iteration-1 gaps).
