# GW30 — Pinecone live-acceptance gate (plan + result)

Status date: 3 November 2026, branch `gw30-pinecone-live-gate`,
worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw30-pinecone`.
A GW card, NOT an R2 item — no four-memory Ruflo/Cursor/AGENTS.md mirror, only this plan doc +
the evidence directory below.

## Context

`pinecone` was already wired as one of the 13 named connectors
(`dispatch/vector_connectors.py`: in `STORE_IDS`, `connector_capabilities("pinecone")` →
`filters=True, namespaces=True, hybrid_search=True`) but had no real transport and was recorded
INFRA-BLOCKED in the 9store and Milvus live harnesses: "cloud-only SaaS; no local image / no
credentials". The account credentials are now supplied (serverless key in the gitignored
`.env.gw30-pinecone`) and the live gates run for real against the Pinecone serverless API.

## What was added

- **Transport module** `gateway_v2/gateway_v2/dispatch/vector_transports/pinecone_transport.py` — a
  real `Vector_Transport` over the modern `pinecone` SDK, following the milvus/qdrant/weaviate
  idiom exactly:
  - frozen slotted `PineconeConnectionParams(api_key, index_name, dimension, cloud, region)` —
    **no host field**: with a serverless key the SDK resolves each index's host from its name (D3);
  - `PineconeTransport` with `__slots__ = ('_params',)` and a lazy `_client()` that imports
    `pinecone.Pinecone` inside the method and raises `VectorTransportError` on `ImportError` and on
    client-construction failure;
  - all six async ops map store/connection failure to `VectorTransportError` (so
    `run_bounded_operation` answers `provider_unreachable`, never an empty success);
  - `create_collection` ensures the ONE dedicated gate index exists and returns `collection_exists()`
    for an already-present index — it NEVER resets or deletes it (`has_index` guard + the store's
    "already exists" error);
  - `query` builds results via `records_from_scored(...)` from Pinecone's OWN cosine matches (each
    match's `score` + `id` + `values`), so the id order `r1, r3, r2` comes from the store's search,
    not a client-side sort;
  - **collection → namespace mapping (D4):** Pinecone's unit of storage is an *index* and a
    serverless account caps the index count, so every adapter `collection` maps onto a Pinecone
    **namespace** (`safe_name(collection)`) inside ONE dedicated gate index (`index_name`), keeping
    the throwaway-index count at exactly one and making cleanup a single `delete_index`;
  - posture codes reused verbatim from `gateway_v2.domain.posture` (`VECTOR_OK`, `COLLECTION_EXISTS`
    via `collection_exists()`, and `provider_unreachable` through the bounded path) — no new
    spellings.
- **Export** both names from `dispatch/vector_transports/__init__.py` (import block + `__all__`,
  alphabetical, between `PgVector*` and `Qdrant*`).
- **Dependency** `pinecone==10.3.0` appended to the existing `[project.optional-dependencies].vectors`
  group (alongside `pymilvus==2.5.16`, not replacing it). The MODERN package is named `pinecone`
  (the old `pinecone-client` name is deprecated — confirmed on PyPI; 10.3.0 is the current stable).
  Imported lazily inside the transport's `_client()`, never on the base import path. **No mypy
  override was added**: unlike `pymilvus`, the `pinecone` 10.3.0 wheel SHIPS a `py.typed` marker, so
  `mypy --strict` type-checks its import cleanly (verified).
- **v10 classic-kwargs decision (D2):** the gate index is created with the classic
  `dimension=`/`metric="cosine"`/`spec=ServerlessSpec(cloud, region)` kwargs (NOT the new `schema=`
  document-index API), so the classic data plane applies and the vector methods
  `index.upsert()`/`index.query()`/`index.delete()` return the standard match shape the shared
  `records_from_scored(...)` ordering machinery consumes.
- **Harness** `docs/plans/evidence/2026-11-03-gw30-pinecone/run_live_gates_pinecone.py` — imports the
  shipped 9store gate machinery via `importlib` and overrides only `LIVE_TRANSPORTS` /
  `INFRA_BLOCKED` for Pinecone, plus Pinecone-specific L30-1/L30-1b/L30-2/L30-3. The shipped 9store
  and Milvus harnesses are NOT edited.

`vector_connectors.py`, `runtime/resources.py`, and the shipped `run_live_gates_9store.py` /
`run_live_gates_milvus.py` were NOT touched. The three cloud SaaS connectors
(`aws_bedrock_kb`, `gcp_vertex_vector`, `azure_ai_search`) stay infra-blocked; `milvus` is listed
infra-blocked for THIS run (its sidecars were not brought up here — its own live gate is the Milvus
card).

## Harness decisions

- **venv (D6):** the worktree has no `gateway_v2/.venv`, so the SDK was installed into — and the
  harness is run with — the main repo venv `/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv`, exactly
  as the Milvus plan did. The harness resolves `gateway_v2.*` to THIS worktree tree (it lives under
  the worktree's `docs/plans/evidence/`, so `REPO`/`GW` point at the worktree's own `gateway_v2`
  carrying the new transport — one class identity, no path gymnastics).
- **Eventual-consistency poll (D5):** Pinecone serverless upserts apply asynchronously, so the
  harness POLLS `query` for the namespace to report `>= 3` queryable vectors (bounded ~30 s, 1.5 s
  between polls) BEFORE asserting the ordering, rather than a fixed sleep. In this run the vectors
  were queryable on the first poll attempt.
- **Fresh-slate reset for L30-1 / L30-1b:** because D4 uses ONE shared gate index, a gate whose
  contract asserts a fresh `create_collection → vector_ok` first deletes the throwaway index
  (bounded wait until gone) so the create genuinely creates; the re-create then answers
  `collection_exists`. This is harness-only; the transport's `create_collection` stays strictly
  non-destructive.
- **L30-2 unreachable trigger:** the SDK resolves its own host, so there is no dead port. The gate
  points a transport at a BOGUS index name under the real key; `Index(name=...)` resolves the host
  by DESCRIBING the index, which raises `NotFoundError` → wrapped to `VectorTransportError` →
  `provider_unreachable` with `records is None`, within the bound (0.28 s), NOT a clean empty
  `vector_ok`.
- **Secret hygiene (D7):** the key is loaded ONLY from the gitignored `.env.gw30-pinecone` (a tiny
  inline dotenv parse, no new dependency) and is NEVER printed, logged, or written into any evidence
  file. The credential is referenced only by the ref id `cred-pinecone-gw30`. The transport carries
  the key in a dataclass field but adds no `__repr__` that prints it.

## Gate results

Live harness (cwd worktree root):
`GW30_EVIDENCE_DIR=docs/plans/evidence/2026-11-03-gw30-pinecone gateway_v2/.venv/bin/python docs/plans/evidence/2026-11-03-gw30-pinecone/run_live_gates_pinecone.py`
(SDK installed into, and run with, `/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv`).

| Gate | Result | Evidence |
|---|---|---|
| L30-1 | **LIVE-PASS** — create/upsert 3/query `r1,r3,r2`/delete 3, all `vector_ok` | `L30-1_pinecone_parity.json` |
| L30-1b | **LIVE-PASS** — first create `vector_ok`, second create `collection_exists`, 3 records intact (`r1,r2,r3`) | `L30-1b_create_preserves_data.json` |
| L30-2 | **LIVE-PASS** — bogus index → `provider_unreachable`, `records is None`, 0.28 s (< 30 s), not an empty clean search | `L30-2_pinecone_unreachable.json` |
| L30-3 | **LIVE-PASS** — `credential_scope_mismatch` + `org_identity_invalid`, 0 backing-store ops, no secret in refusal form | `L30-3_pinecone_refusal.json` |

Discovered account placement: cloud `aws`, region `us-east-1` (the account had zero existing
indexes; defaults adopted). Cleanup: the throwaway `gw30-live-gate` index was deleted at the end of
the run (`cleanup: {"ran": true, "index_deleted": "gw30-live-gate"}`); a post-run `list_indexes()`
shows it absent.

Offline gate battery (cwd `gateway_v2`), with the new transport in place:

| Command | Outcome |
|---|---|
| `pytest tests/dispatch/test_lgw30_*.py tests/domain/test_lgw30_posture.py tests/runtime/test_lgw30_resources.py -q` | 78 passed |
| `mypy --strict` | Success: no issues found in 274 source files |
| `ruff check gateway_v2` | All checks passed |
| `lint-imports` | 3 contracts kept, 0 broken |
| `python -m lint.check_capacity_literals gateway_v2/gateway_v2` | exit 0 |
| `python -m lint.check_no_module_mutable gateway_v2/gateway_v2` | exit 0 |
| `python -m lint.check_frozen_dataclasses gateway_v2/gateway_v2` | exit 0 |
| `python -m lint.check_http_outside_edge_resolve gateway_v2/gateway_v2` | exit 0 |

KNOWN pre-existing failures that are NOT from this change and do not block:
`tests/lint/test_tenant_scale` (`check_tenant_scale` in `edge/dev_components.py`, GW26) and
`tests/lint/test_console_inventory.py` (frontend JS drift). No new regression was introduced (the
new transport lives in `dispatch`, imports only `dispatch` siblings + `domain.posture`, adds no
forbidden edge, is frozen/slotted with no capacity literal).

## Evidence

`docs/plans/evidence/2026-11-03-gw30-pinecone/` — per-gate JSON
(`L30-1_pinecone_parity.json`, `L30-1b_create_preserves_data.json`,
`L30-2_pinecone_unreachable.json`, `L30-3_pinecone_refusal.json`), `run_live_gates_pinecone.py`,
`live_run.txt`, `SUMMARY.md`. NO key material appears in any evidence file (verified by a key scan).

## FOUND, NOT FIXED

1. **SDK-resolved host → L30-2 forced via a bogus index, not a dead port.** The serverless SDK
   resolves each index's host from its name, so there is no reachable/unreachable TCP endpoint to
   target. The gate drives `provider_unreachable` by pointing at a nonexistent index (host-resolve
   → `NotFoundError`). This proves the fail-closed mapping but is a different mechanism from the
   Milvus/9store dead-port L30-2.
2. **One shared gate index (D4).** Every adapter `collection` maps onto a namespace inside ONE
   throwaway index. The transport never creates one index per collection (serverless index caps), so
   `create_collection` is index-level: a fresh index is `vector_ok`, an existing index is
   `collection_exists`. The L30-1/L30-1b fresh-slate reset is a harness concern, not transport
   behaviour.
3. **`filters` / non-gate namespaces not exercised.** `PineconeTransport.query` does not read
   `req.filter`; only the gate's own per-collection namespace is used. Same open finding as every
   other GW30 connector. The gate does not exercise filters.
4. **Eventual-consistency poll is a harness concern.** The transport does not wait for upserts to
   become queryable; the harness polls before asserting ordering (D5). A production caller of this
   transport gets Pinecone's native read-after-write-eventual semantics.
