# GW30 — OpenSearch live gate (3 November 2026)

The `aws_opensearch_serverless` connector had been recorded as infra-blocked: "running both the
Elasticsearch and OpenSearch JVMs alongside the main stack exceeds this 14 GiB host's free RAM
(OpenSearch was SIGTERM/OOM'd)". That claim is corrected here.

## What the 2 November container shows

`gw30-stores-gw30-opensearch-1` (started 10:01:03Z, finished 10:06:52Z): `OOMKilled=false`, exit
code 143, last log lines `stopping ...` / `stopped` — a graceful shutdown on SIGTERM (a
`docker stop`), not a kernel kill. The previous boot's kernel log has no out-of-memory or
"killed process" line. Same settings as today: 512m heap, no container memory limit. So there
was no evidence of an out-of-memory kill; it was stopped.

## Run on 3 November

OpenSearch 2.17.1 (`gw30-opensearch`, 127.0.0.1:6921, 512m heap) next to the main stack and the
other seven stores, through `run_live_gates_9store.py` (OpenSearch added to its live transports
and to the unreachable-port check):

| Gate | Result |
|---|---|
| L30-1 | LIVE-PASS, 8 live stores; OpenSearch create / upsert 3 / query `r1, r3, r2` / delete 3, identical to the other seven |
| L30-1b | LIVE-PASS; OpenSearch second create `collection_exists`, records intact |
| L30-2 | LIVE-PASS; OpenSearch on a dead port `provider_unreachable` in 1.4 ms |
| L30-3 | LIVE-PASS |

Memory (`free -h`): available 3.7 GiB before and 3.6 GiB after, free 429–784 MiB, about 8 GiB of
swap in use. Thin headroom, no failure.

Still infra-blocked: pinecone, aws_bedrock_kb, gcp_vertex_vector, azure_ai_search (cloud only, no
credentials) and milvus (needs etcd + MinIO sidecars).

Files: `live_run.txt`, `L30-1_9store_parity.json`, `L30-1b_create_preserves_data.json`,
`L30-2_9store_unreachable.json`, `L30-3_9store_refusal.json`, `memory_before.txt`,
`memory_after.txt`.
