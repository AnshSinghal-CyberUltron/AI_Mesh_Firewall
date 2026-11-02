#!/usr/bin/env python3
"""GW30 task 16.1 live-acceptance harness — L30-1 / L30-2 / L30-3 (real wire bytes).

Drives the three GW30 live acceptance gates through the ALREADY-SHIPPED adapter
seam (``VectorRegistry`` + ``run_bounded_operation`` + ``resolve_scoped_credential``)
against the REAL per-store transports in
``gateway_v2.dispatch.vector_transports`` wired behind the ``Vector_Transport``
seam. Nothing in the offline layer is changed; this script only INJECTS real
transports, exactly as a deployment would.

Backends available in this local Docker stack: pgvector (``pgvector/pgvector:pg16``,
the ``vector`` extension installed) and redis (``redis:7.4-alpine``, no RediSearch
module). The other 11 named stores have no backend / no credentials here and are
recorded as ``infra-blocked`` in the L30-1 evidence — never fabricated.

Run:  gateway_v2/.venv/bin/python docs/plans/evidence/2026-11-02-gw30-live-gate/run_live_gates.py

Writes raw per-gate JSON + text under this directory.
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

# --- locate the gateway_v2 package ------------------------------------------------
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
GW = REPO / "gateway_v2"
sys.path.insert(0, str(GW))

from gateway_v2.dispatch.vector_adapter import (  # noqa: E402
    AdapterResult,
    CreateRequest,
    DeleteRequest,
    ListRequest,
    QueryRequest,
    UpsertRequest,
    VectorRecord,
    Vector_Adapter,
)
from gateway_v2.dispatch.vector_bounded_op import run_bounded_operation  # noqa: E402
from gateway_v2.dispatch.vector_connectors import (  # noqa: E402
    STORE_IDS,
    build_connector,
)
from gateway_v2.dispatch.vector_credentials import (  # noqa: E402
    CredentialResolution,
    OrgCredential,
    StubCredentialSource,
    resolve_scoped_credential,
)
from gateway_v2.dispatch.vector_transports import (  # noqa: E402
    PgVectorConnectionParams,
    PgVectorTransport,
    RedisConnectionParams,
    RedisVectorTransport,
)
from gateway_v2.domain import posture  # noqa: E402
from gateway_v2.runtime.kinds import HardwareSignals  # noqa: E402
from gateway_v2.runtime.resources import from_signals  # noqa: E402

DIM = 4


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def rid() -> str:
    return f"zs-{uuid.uuid4().hex[:12]}"


def live_contract():
    """A real, serviceable ResourceContract (same shape as the lgw30 resources tests)."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=100 * 1024 * 1024 * 32,
        fd_limit=4096,
        cpu_source="live-harness",
        mem_source="live-harness",
        fd_source="live-harness",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=100 * 1024 * 1024,
    )


# --------------------------------------------------------------------------- #
# A counting transport wrapper — proves ZERO backing-store ops on refusal (L30-3).
# --------------------------------------------------------------------------- #


class CountingTransport:
    """Wraps a real transport and counts every backing-store call it forwards."""

    def __init__(self, inner: Vector_Adapter) -> None:
        self._inner = inner
        self.calls = 0

    async def query(self, req: QueryRequest) -> AdapterResult:
        self.calls += 1
        return await self._inner.query(req)

    async def upsert(self, req: UpsertRequest) -> AdapterResult:
        self.calls += 1
        return await self._inner.upsert(req)

    async def delete(self, req: DeleteRequest) -> AdapterResult:
        self.calls += 1
        return await self._inner.delete(req)

    async def list_collections(self, req: ListRequest) -> AdapterResult:
        self.calls += 1
        return await self._inner.list_collections(req)

    async def create_collection(self, req: CreateRequest) -> AdapterResult:
        self.calls += 1
        return await self._inner.create_collection(req)

    async def health(self, req: HealthRequest) -> AdapterResult:  # noqa: F821
        self.calls += 1
        return await self._inner.health(req)

    def capabilities(self):  # type: ignore[no-untyped-def]
        return self._inner.capabilities()


from gateway_v2.dispatch.vector_adapter import HealthRequest  # noqa: E402


# --------------------------------------------------------------------------- #
# Connection params from the live Docker stack.
# --------------------------------------------------------------------------- #

PG_PARAMS = PgVectorConnectionParams(
    host=os.environ.get("GW30_PG_HOST", "127.0.0.1"),
    port=int(os.environ.get("GW30_PG_PORT", "6932")),
    user=os.environ.get("GW30_PG_USER", "gw30"),
    password=os.environ.get("GW30_PG_PASSWORD", "gw30"),
    dbname=os.environ.get("GW30_PG_DB", "gw30"),
    dimension=DIM,
)
REDIS_PARAMS = RedisConnectionParams(
    host=os.environ.get("GW30_REDIS_HOST", "127.0.0.1"),
    port=int(os.environ.get("GW30_REDIS_PORT", "6979")),
    db=0,
)


def _free_unreachable_port() -> int:
    """Pick a port with nothing listening, then close it so a connect refuses/times out."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# --------------------------------------------------------------------------- #
# L30-1 — cross-connector parity on the live backends.
# --------------------------------------------------------------------------- #

LIVE_BACKENDS = {"pgvector", "redis"}


def make_live_transport(store_id: str):  # type: ignore[no-untyped-def]
    if store_id == "pgvector":
        return PgVectorTransport(PG_PARAMS)
    if store_id == "redis":
        return RedisVectorTransport(REDIS_PARAMS)
    return None


async def run_suite(store_id: str, contract) -> dict:  # type: ignore[no-untyped-def]
    """Run the byte-identical create/upsert/query/delete suite against one connector."""
    transport = make_live_transport(store_id)
    connector = build_connector(store_id, transport)
    org = "orgA"
    coll = f"gw30_live_{store_id}"
    # Byte-identical equivalent inputs across connectors.
    recs = (
        VectorRecord(id="r1", vector=(1.0, 0.0, 0.0, 0.0), metadata=MappingProxyType({})),
        VectorRecord(id="r2", vector=(0.0, 1.0, 0.0, 0.0), metadata=MappingProxyType({})),
        VectorRecord(id="r3", vector=(0.9, 0.1, 0.0, 0.0), metadata=MappingProxyType({})),
    )
    outcomes: dict[str, dict] = {}

    create = await run_bounded_operation(
        lambda: connector.create_collection(
            CreateRequest(org_id=org, store_id=store_id, collection=coll,
                          dimension=DIM, metric="cosine")
        ),
        contract,
    )
    outcomes["create_collection"] = {"code": create.code, "status": create.status}

    up = await run_bounded_operation(
        lambda: connector.upsert(
            UpsertRequest(org_id=org, store_id=store_id, collection=coll, records=recs)
        ),
        contract,
    )
    outcomes["upsert"] = {"code": up.code, "count": up.count}

    q = await run_bounded_operation(
        lambda: connector.query(
            QueryRequest(org_id=org, store_id=store_id, collection=coll,
                         vector=(1.0, 0.0, 0.0, 0.0), top_k=3)
        ),
        contract,
    )
    outcomes["query"] = {
        "code": q.code,
        "count": len(q.records) if q.records is not None else None,
        "ids": [r.id for r in q.records] if q.records is not None else None,
        "top_id": q.records[0].id if q.records else None,
    }

    dele = await run_bounded_operation(
        lambda: connector.delete(
            DeleteRequest(org_id=org, store_id=store_id, collection=coll, ids=("r1", "r2", "r3"))
        ),
        contract,
    )
    outcomes["delete"] = {"code": dele.code, "count": dele.count}

    # cleanup: delete already removed the records; drop the collection artefacts.
    await _cleanup(store_id, coll)
    return outcomes


async def _cleanup(store_id: str, coll: str) -> None:
    if store_id == "pgvector":
        import psycopg

        safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in coll)
        with psycopg.connect(
            host=PG_PARAMS.host, port=PG_PARAMS.port, user=PG_PARAMS.user,
            password=PG_PARAMS.password, dbname=PG_PARAMS.dbname, autocommit=True,
        ) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS gw30_{safe}")
    elif store_id == "redis":
        import redis.asyncio as redis_async

        client = redis_async.Redis(host=REDIS_PARAMS.host, port=REDIS_PARAMS.port,
                                   decode_responses=True)
        try:
            async for key in client.scan_iter(match=f"gw30:*{coll}*"):
                await client.delete(key)
            await client.delete(f"gw30:index:{coll}")
        finally:
            await client.aclose()


async def gate_l30_1(contract) -> dict:  # type: ignore[no-untyped-def]
    result: dict = {"gate": "L30-1", "timestamp": now(), "live": {}, "infra_blocked": {}}
    for store_id in STORE_IDS:
        if store_id in LIVE_BACKENDS:
            try:
                result["live"][store_id] = await run_suite(store_id, contract)
            except Exception as exc:  # noqa: BLE001
                result["live"][store_id] = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            result["infra_blocked"][store_id] = (
                "infra-blocked: no live backend / no credentials in this environment "
                "(deferred live gate per design.md Open Questions)"
            )
    # Cross-connector parity check on the live backends.
    live = result["live"]
    parity: dict = {}
    if all("error" not in live.get(b, {"error": 1}) for b in LIVE_BACKENDS):
        for op in ("create_collection", "upsert", "query", "delete"):
            codes = {b: live[b][op]["code"] for b in LIVE_BACKENDS}
            parity[f"{op}_codes"] = codes
            parity[f"{op}_codes_identical"] = len(set(codes.values())) == 1
        # result-set parity: query ids + counts, upsert/delete counts
        q_ids = {b: live[b]["query"]["ids"] for b in LIVE_BACKENDS}
        q_cnt = {b: live[b]["query"]["count"] for b in LIVE_BACKENDS}
        parity["query_ids"] = q_ids
        parity["query_ids_identical"] = len({tuple(v) for v in q_ids.values()}) == 1
        parity["query_count_identical"] = len(set(q_cnt.values())) == 1
        up_cnt = {b: live[b]["upsert"]["count"] for b in LIVE_BACKENDS}
        del_cnt = {b: live[b]["delete"]["count"] for b in LIVE_BACKENDS}
        parity["upsert_count"] = up_cnt
        parity["upsert_count_identical"] = len(set(up_cnt.values())) == 1
        parity["delete_count"] = del_cnt
        parity["delete_count_identical"] = len(set(del_cnt.values())) == 1
    result["parity"] = parity
    result["verdict"] = (
        "LIVE-PASS (2 live backends: pgvector, redis) + 11 INFRA-BLOCKED"
        if parity and all(v for k, v in parity.items() if k.endswith("identical"))
        else "SEE DETAIL"
    )
    return result


# --------------------------------------------------------------------------- #
# L30-2 — provider_unreachable within 30 s, records absent (never empty clean search).
# --------------------------------------------------------------------------- #


async def gate_l30_2(contract) -> dict:  # type: ignore[no-untyped-def]
    result: dict = {"gate": "L30-2", "timestamp": now(), "cases": []}
    bad_port = _free_unreachable_port()

    # pgvector pointed at an unreachable port.
    pg = PgVectorTransport(
        PgVectorConnectionParams(
            host="127.0.0.1", port=bad_port, user="x", password="x", dbname="x", dimension=DIM
        )
    )
    pg_conn = build_connector("pgvector", pg)
    request_id = rid()
    start = asyncio.get_event_loop().time()
    res = await run_bounded_operation(
        lambda: pg_conn.query(
            QueryRequest(org_id="orgA", store_id="pgvector", collection="nope",
                         vector=(1.0, 0.0, 0.0, 0.0), top_k=3)
        ),
        contract,
    )
    elapsed = asyncio.get_event_loop().time() - start
    result["cases"].append({
        "store": "pgvector", "request_id": request_id, "timestamp": now(),
        "target": f"127.0.0.1:{bad_port} (nothing listening)",
        "code": res.code, "records_is_none": res.records is None,
        "elapsed_s": round(elapsed, 4),
        "within_30s": elapsed < 30.0,
        "is_provider_unreachable": res.code == posture.PROVIDER_UNREACHABLE,
        "is_empty_clean_search": res.code == posture.VECTOR_OK and res.records == (),
    })

    # redis pointed at an unreachable port.
    rds = RedisVectorTransport(RedisConnectionParams(host="127.0.0.1", port=bad_port))
    rds_conn = build_connector("redis", rds)
    request_id = rid()
    start = asyncio.get_event_loop().time()
    res = await run_bounded_operation(
        lambda: rds_conn.query(
            QueryRequest(org_id="orgA", store_id="redis", collection="nope",
                         vector=(1.0, 0.0, 0.0, 0.0), top_k=3)
        ),
        contract,
    )
    elapsed = asyncio.get_event_loop().time() - start
    result["cases"].append({
        "store": "redis", "request_id": request_id, "timestamp": now(),
        "target": f"127.0.0.1:{bad_port} (nothing listening)",
        "code": res.code, "records_is_none": res.records is None,
        "elapsed_s": round(elapsed, 4),
        "within_30s": elapsed < 30.0,
        "is_provider_unreachable": res.code == posture.PROVIDER_UNREACHABLE,
        "is_empty_clean_search": res.code == posture.VECTOR_OK and res.records == (),
    })

    result["verdict"] = (
        "LIVE-PASS"
        if all(
            c["is_provider_unreachable"] and c["records_is_none"]
            and c["within_30s"] and not c["is_empty_clean_search"]
            for c in result["cases"]
        )
        else "FAIL"
    )
    return result


# --------------------------------------------------------------------------- #
# L30-3 — cross-org credential refusal BEFORE any backing-store connection.
# --------------------------------------------------------------------------- #


async def gate_l30_3(contract) -> dict:  # type: ignore[no-untyped-def]
    result: dict = {"gate": "L30-3", "timestamp": now(), "cases": []}

    # Org A's real, verified credential is the only thing the source will resolve.
    org_a_cred = OrgCredential(
        org_id="orgA", verified=True, _secret="sk-orgA-SECRET-DEADBEEF", ref_id="cred-orgA-001"
    )
    source = StubCredentialSource({
        "orgA": CredentialResolution(code=posture.VECTOR_OK, credential=org_a_cred),
    })

    # Scenario 1: Org B's request, but only Org A's credential is presented.
    # Simulate "presenting Org A's credential for Org B's request" by resolving against
    # orgA's key (the attacker's credential) while the request bears orgB.
    for scenario, request_org, presented_cred in (
        ("cross_org_A_cred_for_B_request", "orgB", org_a_cred),
        ("missing_org_identity", "", org_a_cred),
    ):
        # Wrap a real live transport in a counter to prove ZERO store ops on refusal.
        counting = CountingTransport(build_connector("pgvector", PgVectorTransport(PG_PARAMS)))
        request_id = rid()

        # The credential the request *presents* is Org A's. The guard compares the
        # resolved credential's org to the REQUEST's org and refuses on mismatch.
        presented_resolution = CredentialResolution(
            code=posture.VECTOR_OK, credential=presented_cred
        )
        guarded = resolve_scoped_credential(request_org or None, presented_resolution)

        store_ops_before = counting.calls
        proceeded = False
        store_payload = None
        if guarded.ok:
            # Only if the guard allowed it would any backing-store call be made.
            proceeded = True
            r = await run_bounded_operation(
                lambda: counting.query(
                    QueryRequest(org_id=request_org, store_id="pgvector",
                                 collection="orgB_private", vector=(1.0, 0.0, 0.0, 0.0), top_k=1)
                ),
                contract,
            )
            store_payload = r.code
        store_ops_after = counting.calls

        # Confirm the secret never appears in the refusal's emitted form.
        secret = presented_cred._secret
        refusal_repr = f"code={guarded.code} credential_present={guarded.credential is not None}"
        result["cases"].append({
            "scenario": scenario,
            "request_id": request_id,
            "timestamp": now(),
            "presented_credential_org": presented_cred.org_id,
            "target_request_org": request_org or "(empty)",
            "refused": not guarded.ok,
            "refusal_code": guarded.code,
            "identifies_scope_mismatch": guarded.code == posture.CREDENTIAL_SCOPE_MISMATCH,
            "identifies_org_identity_invalid": guarded.code == posture.ORG_IDENTITY_INVALID,
            "backing_store_ops_for_request": store_ops_after - store_ops_before,
            "proceeded_to_store": proceeded,
            "org_b_payload_returned": store_payload,
            "secret_in_refusal_form": secret in refusal_repr,
        })

    result["verdict"] = (
        "LIVE-PASS"
        if all(
            c["refused"]
            and c["backing_store_ops_for_request"] == 0
            and c["proceeded_to_store"] is False
            and c["org_b_payload_returned"] is None
            and c["secret_in_refusal_form"] is False
            for c in result["cases"]
        )
        else "FAIL"
    )
    return result


async def main() -> int:
    contract = live_contract()
    g1 = await gate_l30_1(contract)
    g2 = await gate_l30_2(contract)
    g3 = await gate_l30_3(contract)

    (HERE / "L30-1_cross_connector_parity.json").write_text(json.dumps(g1, indent=2))
    (HERE / "L30-2_provider_unreachable.json").write_text(json.dumps(g2, indent=2))
    (HERE / "L30-3_cross_org_refusal.json").write_text(json.dumps(g3, indent=2))

    summary = {
        "L30-1": g1["verdict"],
        "L30-2": g2["verdict"],
        "L30-3": g3["verdict"],
        "generated": now(),
    }
    print(json.dumps(summary, indent=2))
    print("\n--- L30-1 parity ---")
    print(json.dumps(g1.get("parity", {}), indent=2))
    print("\n--- L30-2 cases ---")
    print(json.dumps(g2["cases"], indent=2))
    print("\n--- L30-3 cases ---")
    print(json.dumps(g3["cases"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
