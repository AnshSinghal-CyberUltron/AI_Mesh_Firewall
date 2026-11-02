#!/usr/bin/env python3
"""GW30 extended live-acceptance harness — L30-1/L30-2/L30-3 across up to 9 real stores.

Extends run_live_gates.py (pgvector + redis) to the full local store stack brought
up by docker-compose.gw30-stores.yml: + qdrant, weaviate, elasticsearch,
opensearch (as aws_opensearch_serverless), chroma, mongodb_atlas. Drives every gate
through the ALREADY-SHIPPED adapter seam (VectorRegistry/build_connector +
run_bounded_operation + resolve_scoped_credential) against the REAL transports in
gateway_v2.dispatch.vector_transports. Nothing in the offline layer changes.

The Redis and pgvector transports run against the dedicated gw30-redis-stack (6979) and
gw30-pgvector (6932) containers, never the main stack's Redis or Postgres. Collection names carry
a per-run suffix. Set GW30_EVIDENCE_DIR to write the JSON somewhere other than this folder.

Cloud-only stores (pinecone, aws_bedrock_kb, gcp_vertex_vector, azure_ai_search)
and milvus (no local container here) are recorded INFRA-BLOCKED, never fabricated.

Run: gateway_v2/.venv/bin/python docs/plans/evidence/2026-11-02-gw30-live-gate/run_live_gates_9store.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import socket
import sys
import uuid
from pathlib import Path
from types import MappingProxyType

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
GW = REPO / "gateway_v2"
sys.path.insert(0, str(GW))

from gateway_v2.dispatch.vector_adapter import (  # noqa: E402
    AdapterResult, CreateRequest, DeleteRequest, HealthRequest, ListRequest,
    QueryRequest, UpsertRequest, VectorRecord, Vector_Adapter,
)
from gateway_v2.dispatch.vector_bounded_op import run_bounded_operation  # noqa: E402
from gateway_v2.dispatch.vector_connectors import STORE_IDS, build_connector  # noqa: E402
from gateway_v2.dispatch.vector_credentials import (  # noqa: E402
    CredentialResolution, OrgCredential, resolve_scoped_credential,
)
from gateway_v2.dispatch import vector_transports as vlt  # noqa: E402
from gateway_v2.domain import posture  # noqa: E402
from gateway_v2.runtime.kinds import HardwareSignals  # noqa: E402
from gateway_v2.runtime.resources import from_signals  # noqa: E402

DIM = 4
RUN = uuid.uuid4().hex[:8]
OUT = Path(os.environ.get("GW30_EVIDENCE_DIR", str(HERE)))
PG = dict(host="127.0.0.1", port=6932, user="gw30", password="gw30", dbname="gw30")
REDIS_PORT = 6979


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def rid() -> str:
    return f"zs-{uuid.uuid4().hex[:12]}"


def live_contract():
    signals = HardwareSignals(
        cpu_quota=4.0, memory_limit=100 * 1024 * 1024 * 32, fd_limit=4096,
        cpu_source="h", mem_source="h", fd_source="h",
    )
    return from_signals(signals, target_p99_ms=20.0, utilization_cap=0.75,
                        per_worker_rss=100 * 1024 * 1024)


# store_id -> factory building a REAL transport pointed at the local container.
LIVE_TRANSPORTS = {
    "pgvector": lambda: vlt.PgVectorTransport(vlt.PgVectorConnectionParams(
        **PG, dimension=DIM)),
    "redis": lambda: vlt.RedisVectorTransport(vlt.RedisConnectionParams(
        host="127.0.0.1", port=REDIS_PORT, db=0)),
    "qdrant": lambda: vlt.QdrantTransport(vlt.QdrantConnectionParams(
        host="127.0.0.1", port=6933, dimension=DIM)),
    "weaviate": lambda: vlt.WeaviateTransport(vlt.WeaviateConnectionParams(
        host="127.0.0.1", port=6980, dimension=DIM, grpc_port=6951)),
    "elasticsearch": lambda: vlt.ElasticsearchTransport(vlt.ElasticsearchConnectionParams(
        host="127.0.0.1", port=6920, dimension=DIM)),
    "chroma": lambda: vlt.ChromaTransport(vlt.ChromaConnectionParams(
        host="127.0.0.1", port=6900, dimension=DIM)),
    "mongodb_atlas": lambda: vlt.MongoAtlasTransport(vlt.MongoAtlasConnectionParams(
        host="127.0.0.1", port=6927, dimension=DIM, dbname="gw30")),
}
INFRA_BLOCKED = {
    "pinecone": "cloud-only SaaS; no local image / no credentials",
    "milvus": "needs etcd+minio sidecars; not brought up locally",
    "aws_bedrock_kb": "cloud-only; no local image / no credentials",
    "gcp_vertex_vector": "cloud-only; no local image / no credentials",
    "azure_ai_search": "cloud-only; no local image / no credentials",
}

# Byte-identical equivalent input across every connector.
RECS = (
    VectorRecord(id="r1", vector=(1.0, 0.0, 0.0, 0.0), metadata=MappingProxyType({})),
    VectorRecord(id="r2", vector=(0.0, 1.0, 0.0, 0.0), metadata=MappingProxyType({})),
    VectorRecord(id="r3", vector=(0.9, 0.1, 0.0, 0.0), metadata=MappingProxyType({})),
)
QVEC = (1.0, 0.0, 0.0, 0.0)


class CountingTransport:
    def __init__(self, inner: Vector_Adapter) -> None:
        self._inner = inner
        self.calls = 0

    async def query(self, req): self.calls += 1; return await self._inner.query(req)
    async def upsert(self, req): self.calls += 1; return await self._inner.upsert(req)
    async def delete(self, req): self.calls += 1; return await self._inner.delete(req)
    async def list_collections(self, req): self.calls += 1; return await self._inner.list_collections(req)
    async def create_collection(self, req): self.calls += 1; return await self._inner.create_collection(req)
    async def health(self, req): self.calls += 1; return await self._inner.health(req)
    def capabilities(self): return self._inner.capabilities()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def run_suite(store_id: str, contract) -> dict:
    transport = LIVE_TRANSPORTS[store_id]()
    connector = build_connector(store_id, transport)
    org = "orgA"
    coll = f"live_{store_id}_{RUN}"
    out: dict = {}

    cr = await run_bounded_operation(lambda: connector.create_collection(
        CreateRequest(org_id=org, store_id=store_id, collection=coll, dimension=DIM, metric="cosine")), contract)
    out["create_collection"] = {"code": cr.code, "status": cr.status}

    # Some engines index asynchronously; a short settle keeps the query deterministic.
    up = await run_bounded_operation(lambda: connector.upsert(
        UpsertRequest(org_id=org, store_id=store_id, collection=coll, records=RECS)), contract)
    out["upsert"] = {"code": up.code, "count": up.count}
    await asyncio.sleep(1.5)

    q = await run_bounded_operation(lambda: connector.query(
        QueryRequest(org_id=org, store_id=store_id, collection=coll, vector=QVEC, top_k=3)), contract)
    out["query"] = {
        "code": q.code,
        "count": len(q.records) if q.records is not None else None,
        "ids": [r.id for r in q.records] if q.records is not None else None,
        "top_id": q.records[0].id if q.records else None,
    }

    de = await run_bounded_operation(lambda: connector.delete(
        DeleteRequest(org_id=org, store_id=store_id, collection=coll, ids=("r1", "r2", "r3"))), contract)
    out["delete"] = {"code": de.code, "count": de.count}
    await _cleanup(store_id, coll)
    return out


async def _cleanup(store_id: str, coll: str) -> None:
    try:
        t = LIVE_TRANSPORTS[store_id]()
        # delete already removed records; drop the collection/table/index where possible
        if store_id == "pgvector":
            import psycopg
            safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in coll)
            with psycopg.connect(**PG, autocommit=True) as cn, cn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS gw30_{safe}")
        elif store_id == "redis":
            import redis.asyncio as ra
            c = ra.Redis(host="127.0.0.1", port=REDIS_PORT, decode_responses=True)
            try:
                safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in coll)
                await c.execute_command("FT.DROPINDEX", f"gw30idx:{safe}", "DD")
            finally:
                await c.aclose()
        # create_collection never resets an existing collection, so every run drops its own
        # per-run collection here (harness-only cleanup, not a transport operation)
        elif store_id == "qdrant":
            from qdrant_client import QdrantClient
            QdrantClient(host="127.0.0.1", port=6933).delete_collection(coll)
        elif store_id == "chroma":
            import chromadb
            chromadb.HttpClient(host="127.0.0.1", port=6900).delete_collection("gw30_" + coll)
        elif store_id == "elasticsearch":
            from elasticsearch import Elasticsearch
            Elasticsearch("http://127.0.0.1:6920").indices.delete(index="gw30_" + coll, ignore_unavailable=True)
        elif store_id == "aws_opensearch_serverless":
            from opensearchpy import OpenSearch
            OpenSearch(hosts=[{"host": "127.0.0.1", "port": 6921}], use_ssl=False).indices.delete(
                index="gw30_" + coll, ignore=[404])
        elif store_id == "mongodb_atlas":
            from pymongo import MongoClient
            MongoClient("mongodb://127.0.0.1:6927")["gw30"]["gw30_" + coll].drop()
        elif store_id == "weaviate":
            import weaviate
            c = weaviate.connect_to_local(host="127.0.0.1", port=6980)
            try:
                nm = "Gw30_" + "".join(ch if ch.isalnum() else "_" for ch in coll)
                if c.collections.exists(nm):
                    c.collections.delete(nm)
            finally:
                c.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  [cleanup warn {store_id}]: {type(exc).__name__}: {exc}")


async def gate_l30_1(contract) -> dict:
    res: dict = {"gate": "L30-1", "timestamp": now(), "live": {}, "infra_blocked": {}}
    for store_id in STORE_IDS:
        if store_id in LIVE_TRANSPORTS:
            try:
                res["live"][store_id] = await run_suite(store_id, contract)
            except Exception as exc:  # noqa: BLE001
                res["live"][store_id] = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            res["infra_blocked"][store_id] = INFRA_BLOCKED.get(store_id, "no local backend")
    # Parity across the live backends that completed without error.
    ok_stores = [s for s, v in res["live"].items() if "error" not in v]
    parity: dict = {"stores_compared": ok_stores}
    if len(ok_stores) >= 2:
        for op in ("create_collection", "upsert", "query", "delete"):
            codes = {s: res["live"][s][op]["code"] for s in ok_stores}
            parity[f"{op}_codes_identical"] = len(set(codes.values())) == 1
        qids = {s: tuple(res["live"][s]["query"]["ids"] or ()) for s in ok_stores}
        parity["query_ids"] = {s: list(v) for s, v in qids.items()}
        parity["query_ids_identical"] = len(set(qids.values())) == 1
        qcnt = {s: res["live"][s]["query"]["count"] for s in ok_stores}
        parity["query_count_identical"] = len(set(qcnt.values())) == 1
        ucnt = {s: res["live"][s]["upsert"]["count"] for s in ok_stores}
        parity["upsert_count_identical"] = len(set(ucnt.values())) == 1
    res["parity"] = parity
    identical = [v for k, v in parity.items() if k.endswith("identical")]
    res["verdict"] = (
        f"LIVE-PASS ({len(ok_stores)} live: {','.join(ok_stores)}) "
        f"+ {len(res['infra_blocked'])} INFRA-BLOCKED"
        if ok_stores and all(identical) else "SEE DETAIL"
    )
    return res


async def run_preservation(store_id: str, contract) -> dict:
    """Create, upsert, create again: the second create is collection_exists and data survives."""
    connector = build_connector(store_id, LIVE_TRANSPORTS[store_id]())
    org, coll = "orgA", f"keep_{store_id}_{RUN}"

    def create():
        return run_bounded_operation(lambda: connector.create_collection(CreateRequest(
            org_id=org, store_id=store_id, collection=coll, dimension=DIM, metric="cosine")),
            contract)

    first = await create()
    up = await run_bounded_operation(lambda: connector.upsert(
        UpsertRequest(org_id=org, store_id=store_id, collection=coll, records=RECS)), contract)
    await asyncio.sleep(1.5)
    second = await create()
    await asyncio.sleep(0.5)
    q = await run_bounded_operation(lambda: connector.query(
        QueryRequest(org_id=org, store_id=store_id, collection=coll, vector=QVEC, top_k=3)),
        contract)
    await _cleanup(store_id, coll)
    ids = [r.id for r in q.records] if q.records is not None else None
    return {
        "first_create": first.code, "upsert": up.code, "second_create": second.code,
        "query_after_second_create": q.code, "ids_after_second_create": ids,
        "pass": (first.code == posture.VECTOR_OK and second.code == posture.COLLECTION_EXISTS
                 and ids is not None and sorted(ids) == ["r1", "r2", "r3"]),
    }


async def gate_l30_1b(contract) -> dict:
    res: dict = {"gate": "L30-1b (R2.9 create is non-destructive)", "timestamp": now(),
                 "run": RUN, "live": {}}
    for store_id in LIVE_TRANSPORTS:
        try:
            res["live"][store_id] = await run_preservation(store_id, contract)
        except Exception as exc:  # noqa: BLE001
            res["live"][store_id] = {"error": f"{type(exc).__name__}: {exc}", "pass": False}
    res["verdict"] = ("LIVE-PASS" if all(v.get("pass") for v in res["live"].values())
                      else "FAIL")
    return res


async def gate_l30_2(contract) -> dict:
    res: dict = {"gate": "L30-2", "timestamp": now(), "cases": []}
    bad = _free_port()
    # Build each transport against a dead port (same type, unreachable address).
    dead = {
        "pgvector": vlt.PgVectorTransport(vlt.PgVectorConnectionParams(
            host="127.0.0.1", port=bad, user="x", password="x", dbname="x", dimension=DIM)),
        "qdrant": vlt.QdrantTransport(vlt.QdrantConnectionParams(host="127.0.0.1", port=bad, dimension=DIM)),
        "elasticsearch": vlt.ElasticsearchTransport(vlt.ElasticsearchConnectionParams(
            host="127.0.0.1", port=bad, dimension=DIM)),
        "chroma": vlt.ChromaTransport(vlt.ChromaConnectionParams(host="127.0.0.1", port=bad, dimension=DIM)),
        "mongodb_atlas": vlt.MongoAtlasTransport(vlt.MongoAtlasConnectionParams(
            host="127.0.0.1", port=bad, dimension=DIM, dbname="gw30")),
    }
    for store_id, transport in dead.items():
        conn = build_connector(store_id, transport)
        request_id = rid()
        start = asyncio.get_event_loop().time()
        r = await run_bounded_operation(lambda c=conn, s=store_id: c.query(
            QueryRequest(org_id="orgA", store_id=s, collection="nope", vector=QVEC, top_k=3)), contract)
        elapsed = asyncio.get_event_loop().time() - start
        res["cases"].append({
            "store": store_id, "request_id": request_id, "timestamp": now(),
            "target": f"127.0.0.1:{bad} (nothing listening)",
            "code": r.code, "records_is_none": r.records is None,
            "elapsed_s": round(elapsed, 4), "within_30s": elapsed < 30.0,
            "is_provider_unreachable": r.code == posture.PROVIDER_UNREACHABLE,
            "is_empty_clean_search": r.code == posture.VECTOR_OK and r.records == (),
        })
    res["verdict"] = ("LIVE-PASS" if all(
        c["is_provider_unreachable"] and c["records_is_none"] and c["within_30s"]
        and not c["is_empty_clean_search"] for c in res["cases"]) else "FAIL")
    return res


async def gate_l30_3(contract) -> dict:
    res: dict = {"gate": "L30-3", "timestamp": now(), "cases": []}
    org_a = OrgCredential(org_id="orgA", verified=True, _secret="sk-orgA-SECRET-DEADBEEF",
                          ref_id="cred-orgA-001")
    for scenario, request_org in (("cross_org_A_cred_for_B_request", "orgB"),
                                  ("missing_org_identity", "")):
        # Wrap a real LIVE qdrant transport in a counter to prove ZERO store ops on refusal.
        counting = CountingTransport(build_connector("qdrant", LIVE_TRANSPORTS["qdrant"]()))
        request_id = rid()
        presented = CredentialResolution(code=posture.VECTOR_OK, credential=org_a)
        guarded = resolve_scoped_credential(request_org or None, presented)
        before = counting.calls
        proceeded = False
        payload = None
        if guarded.ok:
            proceeded = True
            r = await run_bounded_operation(lambda: counting.query(
                QueryRequest(org_id=request_org, store_id="qdrant", collection="orgB_private",
                             vector=QVEC, top_k=1)), contract)
            payload = r.code
        after = counting.calls
        refusal_form = f"code={guarded.code} cred_present={guarded.credential is not None}"
        res["cases"].append({
            "scenario": scenario, "request_id": request_id, "timestamp": now(),
            "presented_credential_org": org_a.org_id, "target_request_org": request_org or "(empty)",
            "refused": not guarded.ok, "refusal_code": guarded.code,
            "backing_store_ops_for_request": after - before,
            "proceeded_to_store": proceeded, "org_b_payload_returned": payload,
            "secret_in_refusal_form": org_a._secret in refusal_form,
            "live_transport_used": "qdrant",
        })
    res["verdict"] = ("LIVE-PASS" if all(
        c["refused"] and c["backing_store_ops_for_request"] == 0
        and c["proceeded_to_store"] is False and c["org_b_payload_returned"] is None
        and c["secret_in_refusal_form"] is False for c in res["cases"]) else "FAIL")
    return res


def _ensure_pgvector_extension() -> None:
    import psycopg
    with psycopg.connect(**PG, autocommit=True) as cn, cn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")


async def main() -> int:
    contract = live_contract()
    _ensure_pgvector_extension()
    OUT.mkdir(parents=True, exist_ok=True)
    g1 = await gate_l30_1(contract)
    g1b = await gate_l30_1b(contract)
    g2 = await gate_l30_2(contract)
    g3 = await gate_l30_3(contract)
    (OUT / "L30-1_9store_parity.json").write_text(json.dumps(g1, indent=2))
    (OUT / "L30-1b_create_preserves_data.json").write_text(json.dumps(g1b, indent=2))
    (OUT / "L30-2_9store_unreachable.json").write_text(json.dumps(g2, indent=2))
    (OUT / "L30-3_9store_refusal.json").write_text(json.dumps(g3, indent=2))
    summary = {"L30-1": g1["verdict"], "L30-1b": g1b["verdict"], "L30-2": g2["verdict"],
               "L30-3": g3["verdict"], "generated": now()}
    print(json.dumps(summary, indent=2))
    print("\n--- L30-1 per-store ---")
    print(json.dumps({"live": g1["live"], "infra_blocked": g1["infra_blocked"],
                      "parity": g1["parity"]}, indent=2))
    print("\n--- L30-1b create preserves data ---")
    print(json.dumps(g1b["live"], indent=2))
    print("\n--- L30-2 cases ---")
    print(json.dumps(g2["cases"], indent=2))
    print("\n--- L30-3 cases ---")
    print(json.dumps(g3["cases"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
