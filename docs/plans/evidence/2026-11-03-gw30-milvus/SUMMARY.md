# GW30 — Milvus live gate (3 November 2026)

The `milvus` connector had been recorded as infra-blocked: "needs etcd + MinIO sidecars; not
brought up locally" — the previous 14 GiB host could not hold Milvus + etcd + MinIO alongside the
other stores. That constraint is lifted on the current VM (≈1.5 TiB RAM), so Milvus is brought up
and the live gates run for real.

## What was added

- A real `MilvusTransport` (`gateway_v2/gateway_v2/dispatch/vector_transports/milvus_transport.py`)
  over `pymilvus`' `MilvusClient`, following the qdrant/weaviate/mongo idiom: frozen slotted
  `MilvusConnectionParams`, lazy SDK import inside `_client()`, every store/connection failure
  mapped to `VectorTransportError` (→ `provider_unreachable`), non-destructive `create_collection`
  (`has_collection` → `collection_exists`, never reset), and `records_from_scored(...)` for the
  shared `r1, r3, r2` ordering.
- `pymilvus==2.5.16` pinned in a new `[project.optional-dependencies].vectors` group (lazy/optional,
  not a base dependency), plus a `pymilvus.*` mypy override (the SDK ships no `py.typed`).
- Milvus standalone + etcd + S3 object store added to `docker-compose.gw30-stores.yml`.
- A new `run_live_gates_milvus.py` harness that imports the shipped 9store gate machinery and
  overrides only `LIVE_TRANSPORTS` / `INFRA_BLOCKED` for Milvus — the 9store run is untouched.

## Run on 3 November

Milvus 2.5.4 (`gw30-milvus`, 127.0.0.1:6930 gRPC, 4g mem_limit) with its etcd + S3 sidecars,
through `run_live_gates_milvus.py`:

| Gate | Result |
|---|---|
| L30-1 | LIVE-PASS; milvus create / upsert 3 / query `r1, r3, r2` / delete 3, all `vector_ok` |
| L30-1b | LIVE-PASS; second create `collection_exists`, the three records still present (`r1, r2, r3`) |
| L30-2 | LIVE-PASS; milvus on a dead port → `provider_unreachable`, `records is None`, 5.05 s (< 30 s) |
| L30-3 | LIVE-PASS; `credential_scope_mismatch` + `org_identity_invalid`, 0 backing-store ops, no secret leak |

Real wire bytes: the query id ordering `r1, r3, r2` is produced by Milvus's own COSINE AUTOINDEX
search (not a client-side sort of a fabricated set), identical to the order the other eight live
connectors return.

## Compose host ports used

- Milvus gRPC `19530 → 127.0.0.1:6930`, web/health `9091 → 127.0.0.1:6931`
- S3 object store `9000 → 127.0.0.1:6936`
- etcd client `2379 → 127.0.0.1:6938`

All outside the taken 69xx set; no existing service or port was changed.

## Images

- `milvusdb/milvus:v2.5.4`
- `quay.io/coreos/etcd:v3.5.16`
- `chrislusf/seaweedfs:latest` (S3 object store — see FOUND-NOT-FIXED)

## FOUND, NOT FIXED

- **S3 backend is SeaweedFS, not MinIO.** MinIO's Docker Hub images (`minio/minio`) were delisted
  (every tag returns "object not found") and the quay.io replacement (`quay.io/minio/minio`)
  requires an authenticated pull that is not available in this environment (anonymous quay token →
  `UNAUTHORIZED`). SeaweedFS exposes a minio-compatible S3 gateway and IS anonymously pullable, so
  it backs Milvus's object storage with the same `minioadmin`/`minioadmin` credentials
  (`gw30-seaweedfs-s3.json`). This is transparent to the gate: the acceptance wire bytes are the
  Milvus gRPC path, not the object store. If a pullable MinIO image becomes available, swap the
  `gw30-minio` image back to `minio/minio:RELEASE.2023-03-20T20-16-18Z` (the tag the Milvus 2.5
  standalone compose pins) with `command: minio server /minio_data`.
- **Synchronous SDK.** `pymilvus`' `MilvusClient` is synchronous, so `asyncio.wait_for` cannot
  interrupt a call mid-flight; the effective per-op timeout is the SDK's own 5 s connect timeout
  (seen as the 5.05 s L30-2 elapsed). Carried forward from the GW30 open findings
  (`docs/plans/2026-11-02-gw30-gate-fix.md`, finding 9).
- **`filters=True` declared but unread.** `connector_capabilities("milvus")` declares `filters` and
  `namespaces`, but `MilvusTransport.query` does not read `req.filter` / `req.namespace` (same open
  finding 2 as every other GW30 connector). The gate does not exercise filters.

## Still infra-blocked

pinecone, aws_bedrock_kb, gcp_vertex_vector, azure_ai_search — cloud-only, no local image / no
credentials. Their blocked status is unchanged.

## Files

`run_live_gates_milvus.py`, `L30-1_milvus_parity.json`, `L30-1b_create_preserves_data.json`,
`L30-2_milvus_unreachable.json`, `L30-3_milvus_refusal.json`, `live_run.txt`, `memory_before.txt`,
`memory_after.txt`.
