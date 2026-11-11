# GW30 task 16.1 — live acceptance gates (L30-1 / L30-2 / L30-3)

**Date:** 2026-11-02 · **Branch:** `suraj-revamp` · **Base HEAD:** `502eb8f8`
**Spec:** `.kiro/specs/vector-store-adapter-gw30` · **Card:** GW30 (NOT an R2 item — no
four-memory Ruflo/Cursor mirror).

## What this task did

Wired the **real per-store SDK/HTTP transports** in BEHIND the already-shipped adapter
seam (the `Vector_Transport` protocol in `dispatch/vector_connectors.py`) **without
changing any offline layer**, then ran the three GW30 live acceptance gates against the
running local Docker stack and persisted the exit evidence here.

- New module: `gateway_v2/gateway_v2/dispatch/vector_live_transports.py` — the real
  `PgVectorTransport` (psycopg v3 + the `vector` extension) and `RedisVectorTransport`
  (`redis.asyncio`, module-free Redis → plain hashes + in-client cosine). Both satisfy
  the shipped `Vector_Transport` protocol, are **injected** (never imported downward),
  import their SDKs **lazily** (the `http_provider.py` httpx idiom), carry **no capacity
  literal** and **no module-level mutable table**. The offline registry, credential
  seam, value types and `run_bounded_operation` are untouched.
- Harness: `run_live_gates.py` drives all three gates through
  `VectorRegistry` / `build_connector` / `run_bounded_operation` /
  `resolve_scoped_credential` — the real adapter path, not a bypass.

## Environment inventory (verified against the running stack)

| Store | Backend in this env | Gate coverage |
|-------|---------------------|---------------|
| **pgvector** | LIVE — `ai_mesh_firewall-postgres-1` (`pgvector/pgvector:pg16`, `vector` 0.8.7) | L30-1 LIVE, L30-2 LIVE |
| **redis** | LIVE — `ai_mesh_firewall-redis-1` (`redis:7.4-alpine`, **no RediSearch module**) | L30-1 LIVE, L30-2 LIVE |
| pinecone, qdrant, weaviate, milvus, elasticsearch, mongodb_atlas, aws_opensearch_serverless, aws_bedrock_kb, gcp_vertex_vector, azure_ai_search, chroma | **No backend / no credentials** (chroma container exited, `CHROMA_URL` empty; mongo not running) | **INFRA-BLOCKED** — deferred live gate per design.md Open Questions |

L30-3 needs no external account (credential-scope guard refuses before any store
connection), so it runs fully regardless of backend availability.

## Results

| Gate | Verdict | Evidence file |
|------|---------|---------------|
| **L30-1** cross-connector parity | **LIVE-PASS** (pgvector + redis identical) + 11 INFRA-BLOCKED | `L30-1_cross_connector_parity.json`, `L30-1_real_wire_bytes.txt` |
| **L30-2** provider_unreachable | **LIVE-PASS** | `L30-2_provider_unreachable.json` |
| **L30-3** cross-org refusal | **LIVE-PASS** | `L30-3_cross_org_refusal.json` |

### L30-1 (R12) — uniform behaviour on byte-identical input
Same `create_collection`/`upsert`/`query`/`delete` suite, byte-identical equivalent
inputs, against the two live connectors:
- All four operations return identical codes (`vector_ok`) on both.
- `query` returns identical result sets: ids `[r1, r3, r2]` (cosine order of the
  unit-aligned query vector), identical count on both.
- `upsert` count = 3 on both; `delete` count = 3 on both.
- Real wire bytes captured in `L30-1_real_wire_bytes.txt`: pgvector's native
  `embedding <=> '[1,0,0,0]'` cosine distances (`r1`=0, `r3`≈0.0061, `r2`=1) and
  redis' `HGETALL` of a stored record hash; `MODULE LIST` empty confirms the
  brute-force model is honest for a module-free Redis.
- The 11 stores without a backend are recorded explicitly as `infra-blocked`, not
  fabricated.

### L30-2 (R13) — unreachable provider is a declared error, never an empty clean search
Each real transport pointed at a `127.0.0.1` port with nothing listening, driven
through `run_bounded_operation`:
- pgvector → `provider_unreachable`, `records is None`, ~0.4 ms (connection refused).
- redis → `provider_unreachable`, `records is None`, ~2.4 s (connect timeout).
- Both within 30 s; neither is a `vector_ok` empty result. Request id + timestamp
  persisted.

### L30-3 (R14) — cross-org credential refused before any backing-store connection
A real, verified Org A credential is presented for an Org B request (and a second case
with an empty request org), each wrapping a **real live pgvector transport in a call
counter**:
- cross-org → `credential_scope_mismatch`; empty org → `org_identity_invalid`.
- `backing_store_ops_for_request == 0` in both cases (the counter proves zero wire
  calls); never proceeded to the store; no Org B payload returned.
- The credential secret does not appear in the refusal's emitted form.

## Offline gates — unchanged and green after adding the real transport

Re-run after the new module (`offline_reverify_*` files):
- `mypy --strict gateway_v2` → `Success: no issues found in 202 source files`
- `ruff check gateway_v2` → `All checks passed!`
- `lint-imports` → 3 contracts kept, 0 broken (no new layer edge;
  no `edge`/`dispatch`→downward connector/credential edge)
- AST gates `check_capacity_literals` / `check_no_module_mutable` /
  `check_frozen_dataclasses` / `check_http_outside_edge_resolve` → all exit 0
- lgw30 offline subset → 76 passed

## Cleanup

All temporary data written during the gates was removed: no `gw30_*` tables remain in
pgvector and no `gw30:*` keys remain in redis (verified post-run).
