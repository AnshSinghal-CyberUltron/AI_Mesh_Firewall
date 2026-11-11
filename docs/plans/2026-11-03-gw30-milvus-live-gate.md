# GW30 — Milvus live-acceptance gate (plan + result)

Status date: 3 November 2026, branch `gw30-milvus-live-gate`,
worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw30-milvus`.
A GW card, NOT an R2 item — no four-memory Ruflo/Cursor/AGENTS.md mirror, only this plan doc +
the evidence directory below.

## Context

`milvus` was already wired as one of the 13 named connectors
(`dispatch/vector_connectors.py`: in `STORE_IDS`, `connector_capabilities("milvus")` →
`filters=True, namespaces=True, hybrid_search=True`) but had no real transport and was recorded
INFRA-BLOCKED in the 9store live harness: "needs etcd + MinIO sidecars; not brought up locally".
The 14 GiB host could not hold Milvus + etcd + MinIO alongside the other stores. The current VM
has ≈1.5 TiB RAM, so the constraint is lifted and the live gates run for real.

## What was added

- **Transport module** `gateway_v2/gateway_v2/dispatch/vector_transports/milvus_transport.py` — a
  real `Vector_Transport` over `pymilvus`' `MilvusClient`, following the qdrant/weaviate/mongo
  idiom exactly:
  - frozen slotted `MilvusConnectionParams(host, port, dimension)`;
  - `MilvusTransport` with `__slots__ = ('_params',)` and a lazy `_client()` that imports
    `pymilvus.MilvusClient` inside the method and raises `VectorTransportError` on `ImportError`
    and on connect failure;
  - all six async ops map store/connection failure to `VectorTransportError` (so
    `run_bounded_operation` answers `provider_unreachable`, never an empty success);
  - `create_collection` returns `collection_exists()` for an existing collection and NEVER
    resets it (`has_collection` guard + the store's "already exists" error);
  - `query` builds results via `records_from_scored(...)` so the id order matches `r1, r3, r2`;
  - string record ids preserved via the fast-path `id_type="string"` + `max_length` (VARCHAR PK),
    COSINE AUTOINDEX, `auto_id=False`; collection names via `safe_name` + `gw30_` prefix;
  - posture codes reused verbatim from `gateway_v2.domain.posture` (no new spellings).
- **Export** both names from `dispatch/vector_transports/__init__.py` (and `__all__`, alphabetical).
- **Dependency** `pymilvus==2.5.16` in a new `[project.optional-dependencies].vectors` group
  (lazy/optional, not a base dependency), plus a `pymilvus.*` mypy `ignore_missing_imports`
  override because the SDK ships no `py.typed` marker (the qdrant/weaviate SDKs ship stubs).
- **Compose** `docker-compose.gw30-stores.yml` gains `gw30-milvus` (`milvusdb/milvus:v2.5.4`,
  `milvus run standalone`, 4g mem_limit), `gw30-etcd` (`quay.io/coreos/etcd:v3.5.16`), and
  `gw30-minio` (an S3 object store), wired with `ETCD_ENDPOINTS` + `MINIO_ADDRESS`, each with a
  healthcheck. Host ports (free 69xx): Milvus gRPC `19530→6930`, web/health `9091→6931`, S3
  `9000→6936`, etcd `2379→6938`. No existing service or port changed.
- **Harness** `docs/plans/evidence/2026-11-03-gw30-milvus/run_live_gates_milvus.py` — imports the
  shipped 9store gate machinery via `importlib` and overrides only `LIVE_TRANSPORTS` /
  `INFRA_BLOCKED` for Milvus, plus Milvus-specific L30-2 (dead-port) and L30-3 (CountingTransport
  over a live Milvus connector) because the shipped `gate_l30_2`/`gate_l30_3` build other stores.
  The shipped 9store file is NOT edited.

`vector_connectors.py`, `runtime/resources.py`, and the shipped `run_live_gates_9store.py` were NOT
touched; the four cloud SaaS connectors stay infra-blocked.

## Image-tag deviation (S3 backend)

MinIO's Docker Hub images (`minio/minio`) were delisted — every tag returns "object not found" —
and the quay.io replacement (`quay.io/minio/minio`) requires an authenticated pull not available
here (anonymous quay token → `UNAUTHORIZED`, verified). SeaweedFS exposes a minio-compatible S3
gateway and IS anonymously pullable, so `gw30-minio` runs `chrislusf/seaweedfs:latest` as Milvus's
S3 backend with the same `minioadmin`/`minioadmin` credentials (`gw30-seaweedfs-s3.json`). This is
transparent to the gate: the acceptance wire bytes are the Milvus gRPC path, not the object store.
Recorded as FOUND-NOT-FIXED; the plan's Gaps section explicitly permits adjusting an unpullable
pinned tag.

## Gate results

Live harness (cwd repo root):
`GW30_EVIDENCE_DIR=docs/plans/evidence/2026-11-03-gw30-milvus gateway_v2/.venv/bin/python docs/plans/evidence/2026-11-03-gw30-milvus/run_live_gates_milvus.py`

| Gate | Result | Evidence |
|---|---|---|
| L30-1 | **LIVE-PASS** — create/upsert 3/query `r1,r3,r2`/delete 3, all `vector_ok` | `L30-1_milvus_parity.json` |
| L30-1b | **LIVE-PASS** — second create `collection_exists`, records intact (`r1,r2,r3`) | `L30-1b_create_preserves_data.json` |
| L30-2 | **LIVE-PASS** — dead port → `provider_unreachable`, `records is None`, 5.05 s (< 30 s), not an empty clean search | `L30-2_milvus_unreachable.json` |
| L30-3 | **LIVE-PASS** — `credential_scope_mismatch` + `org_identity_invalid`, 0 backing-store ops, no secret in refusal form | `L30-3_milvus_refusal.json` |

Offline gate battery (cwd `gateway_v2`), with the new transport in place:

| Command | Outcome |
|---|---|
| `pytest tests/dispatch/test_lgw30_*.py tests/domain/test_lgw30_posture.py tests/runtime/test_lgw30_resources.py -q` | 78 passed |
| `mypy --strict` | Success: no issues in 273 source files |
| `ruff check gateway_v2` | All checks passed |
| `lint-imports` | 3 contracts kept, 0 broken |
| `python -m lint.check_capacity_literals gateway_v2/gateway_v2` | exit 0 |
| `python -m lint.check_no_module_mutable gateway_v2/gateway_v2` | exit 0 |
| `python -m lint.check_frozen_dataclasses gateway_v2/gateway_v2` | exit 0 |
| `python -m lint.check_http_outside_edge_resolve gateway_v2/gateway_v2` | exit 0 |

KNOWN pre-existing failures that are NOT from this change and do not block: `check_tenant_scale`
(`edge/dev_components.py`, GW26) and `tests/lint/test_console_inventory.py` (frontend JS drift). No
new regression was introduced (the new transport lives in `dispatch`, imports only `dispatch`
siblings + `domain.posture`, adds no forbidden edge, is frozen/slotted with no capacity literal).

## Evidence

`docs/plans/evidence/2026-11-03-gw30-milvus/` — per-gate JSON, `run_live_gates_milvus.py`,
`live_run.txt`, `SUMMARY.md`, `memory_before.txt`, `memory_after.txt`.

## FOUND, NOT FIXED

1. **S3 backend is SeaweedFS, not MinIO** (image-tag deviation above). Swap back to
   `minio/minio:RELEASE.2023-03-20T20-16-18Z` with `command: minio server /minio_data` if a
   pullable MinIO image becomes available.
2. **Synchronous SDK** — `MilvusClient` is synchronous, so `asyncio.wait_for` cannot interrupt a
   call mid-flight; the effective per-op timeout is the SDK's own 5 s connect timeout (the 5.05 s
   L30-2 elapsed). Carried forward from `docs/plans/2026-11-02-gw30-gate-fix.md` finding 9.
3. **`filters=True` declared but unread** — `MilvusTransport.query` does not read `req.filter` /
   `req.namespace`; same open finding 2 as every other GW30 connector. The gate does not exercise
   filters.
