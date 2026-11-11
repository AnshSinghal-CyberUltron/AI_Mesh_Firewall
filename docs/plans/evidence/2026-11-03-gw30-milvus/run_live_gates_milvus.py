#!/usr/bin/env python3
"""GW30 Milvus live-acceptance harness — L30-1/L30-1b/L30-2/L30-3 against a REAL Milvus server.

This does NOT edit the shipped 9store harness
(``docs/plans/evidence/2026-11-02-gw30-live-gate/run_live_gates_9store.py``). It imports that
module's gate machinery (``gate_l30_1``, ``gate_l30_1b``, ``CountingTransport``, ``live_contract``,
``run_bounded_operation``, ``RECS``, ``QVEC``, ``DIM``, ``now``, ``rid``, ``_free_port``, and the
value types) via an explicit ``importlib`` load, then:

  * overrides its ``LIVE_TRANSPORTS`` to the single Milvus transport pointed at the local
    ``gw30-milvus`` container (127.0.0.1:6930), so L30-1 / L30-1b drive the real Milvus wire;
  * keeps only the four cloud SaaS stores in ``INFRA_BLOCKED`` (pinecone, aws_bedrock_kb,
    gcp_vertex_vector, azure_ai_search) — milvus is NO LONGER blocked;
  * monkeypatches the imported ``_cleanup`` so the per-run ``gw30_*`` Milvus collection is dropped;
  * supplies Milvus-specific L30-2 (a Milvus transport at a dead 127.0.0.1 port →
    ``provider_unreachable``, ``records is None``, within the bound) and L30-3 (a
    ``CountingTransport`` wrapping a REAL Milvus connector proving ZERO backing-store ops on a
    credential-scope / org-identity refusal), because the shipped ``gate_l30_2`` / ``gate_l30_3``
    build other stores.

Writes per-gate JSON + a captured stdout into this evidence dir (``GW30_EVIDENCE_DIR`` overrides).

Run:
  GW30_EVIDENCE_DIR=docs/plans/evidence/2026-11-03-gw30-milvus \
    gateway_v2/.venv/bin/python \
    docs/plans/evidence/2026-11-03-gw30-milvus/run_live_gates_milvus.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
GW = REPO / "gateway_v2"
NINE_STORE = (
    REPO / "docs" / "plans" / "evidence" / "2026-11-02-gw30-live-gate" / "run_live_gates_9store.py"
)
sys.path.insert(0, str(GW))

# Load the shipped 9store harness as a module WITHOUT editing it.
_spec = importlib.util.spec_from_file_location("gw30_9store", NINE_STORE)
assert _spec is not None and _spec.loader is not None
nine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nine)

from gateway_v2.dispatch import vector_transports as vlt  # noqa: E402
from gateway_v2.dispatch.vector_adapter import QueryRequest  # noqa: E402
from gateway_v2.dispatch.vector_bounded_op import run_bounded_operation  # noqa: E402
from gateway_v2.dispatch.vector_connectors import build_connector  # noqa: E402
from gateway_v2.dispatch.vector_credentials import (  # noqa: E402
    CredentialResolution,
    OrgCredential,
    resolve_scoped_credential,
)
from gateway_v2.domain import posture  # noqa: E402

DIM = nine.DIM
MILVUS_PORT = 6930


def _milvus_transport() -> object:
    return vlt.MilvusTransport(
        vlt.MilvusConnectionParams(host="127.0.0.1", port=MILVUS_PORT, dimension=DIM)
    )


# Milvus is the ONLY live store for this run; the four cloud SaaS stores stay infra-blocked.
nine.LIVE_TRANSPORTS = {"milvus": _milvus_transport}
nine.INFRA_BLOCKED = {
    "pinecone": "cloud-only SaaS; no local image / no credentials",
    "aws_bedrock_kb": "cloud-only; no local image / no credentials",
    "gcp_vertex_vector": "cloud-only; no local image / no credentials",
    "azure_ai_search": "cloud-only; no local image / no credentials",
}


# Milvus cleanup: drop the per-run gw30_* collection (harness-only, not a transport op).
async def _cleanup_milvus(store_id: str, coll: str) -> None:
    if store_id != "milvus":
        return
    try:
        from pymilvus import MilvusClient

        name = "gw30_" + "".join(c if (c.isalnum() or c == "_") else "_" for c in coll)
        client = MilvusClient(uri=f"http://127.0.0.1:{MILVUS_PORT}")
        if client.has_collection(name):
            client.drop_collection(name)
    except Exception as exc:  # noqa: BLE001
        print(f"  [cleanup warn milvus]: {type(exc).__name__}: {exc}")


nine._cleanup = _cleanup_milvus


async def gate_l30_2_milvus(contract) -> dict:
    """A Milvus transport at a dead port → provider_unreachable, records absent, within bound."""
    res: dict = {"gate": "L30-2", "store": "milvus", "timestamp": nine.now(), "cases": []}
    bad = nine._free_port()
    transport = vlt.MilvusTransport(
        vlt.MilvusConnectionParams(host="127.0.0.1", port=bad, dimension=DIM)
    )
    conn = build_connector("milvus", transport)
    request_id = nine.rid()
    start = asyncio.get_event_loop().time()
    r = await run_bounded_operation(
        lambda: conn.query(
            QueryRequest(
                org_id="orgA", store_id="milvus", collection="nope", vector=nine.QVEC, top_k=3
            )
        ),
        contract,
    )
    elapsed = asyncio.get_event_loop().time() - start
    res["cases"].append(
        {
            "store": "milvus",
            "request_id": request_id,
            "timestamp": nine.now(),
            "target": f"127.0.0.1:{bad} (nothing listening)",
            "code": r.code,
            "records_is_none": r.records is None,
            "elapsed_s": round(elapsed, 4),
            "within_30s": elapsed < 30.0,
            "is_provider_unreachable": r.code == posture.PROVIDER_UNREACHABLE,
            "is_empty_clean_search": r.code == posture.VECTOR_OK and r.records == (),
        }
    )
    res["verdict"] = (
        "LIVE-PASS"
        if all(
            c["is_provider_unreachable"]
            and c["records_is_none"]
            and c["within_30s"]
            and not c["is_empty_clean_search"]
            for c in res["cases"]
        )
        else "FAIL"
    )
    return res


async def gate_l30_3_milvus(contract) -> dict:
    """Credential-scope / org-identity refusal via the Milvus path with ZERO backing-store ops."""
    res: dict = {"gate": "L30-3", "store": "milvus", "timestamp": nine.now(), "cases": []}
    org_a = OrgCredential(
        org_id="orgA", verified=True, _secret="sk-orgA-SECRET-DEADBEEF", ref_id="cred-orgA-001"
    )
    for scenario, request_org in (
        ("cross_org_A_cred_for_B_request", "orgB"),
        ("missing_org_identity", ""),
    ):
        # Wrap a real LIVE Milvus connector in a counter to prove ZERO store ops on refusal.
        counting = nine.CountingTransport(build_connector("milvus", _milvus_transport()))
        request_id = nine.rid()
        presented = CredentialResolution(code=posture.VECTOR_OK, credential=org_a)
        guarded = resolve_scoped_credential(request_org or None, presented)
        before = counting.calls
        proceeded = False
        payload = None
        if guarded.ok:
            proceeded = True
            r = await run_bounded_operation(
                lambda: counting.query(
                    QueryRequest(
                        org_id=request_org,
                        store_id="milvus",
                        collection="orgB_private",
                        vector=nine.QVEC,
                        top_k=1,
                    )
                ),
                contract,
            )
            payload = r.code
        after = counting.calls
        refusal_form = f"code={guarded.code} cred_present={guarded.credential is not None}"
        res["cases"].append(
            {
                "scenario": scenario,
                "request_id": request_id,
                "timestamp": nine.now(),
                "presented_credential_org": org_a.org_id,
                "target_request_org": request_org or "(empty)",
                "refused": not guarded.ok,
                "refusal_code": guarded.code,
                "backing_store_ops_for_request": after - before,
                "proceeded_to_store": proceeded,
                "org_b_payload_returned": payload,
                "secret_in_refusal_form": org_a._secret in refusal_form,
                "live_transport_used": "milvus",
            }
        )
    res["verdict"] = (
        "LIVE-PASS"
        if all(
            c["refused"]
            and c["backing_store_ops_for_request"] == 0
            and c["proceeded_to_store"] is False
            and c["org_b_payload_returned"] is None
            and c["secret_in_refusal_form"] is False
            for c in res["cases"]
        )
        else "FAIL"
    )
    return res


async def main() -> int:
    contract = nine.live_contract()
    out = Path(os.environ.get("GW30_EVIDENCE_DIR", str(HERE)))
    out.mkdir(parents=True, exist_ok=True)

    g1 = await nine.gate_l30_1(contract)
    g1b = await nine.gate_l30_1b(contract)
    g2 = await gate_l30_2_milvus(contract)
    g3 = await gate_l30_3_milvus(contract)

    (out / "L30-1_milvus_parity.json").write_text(json.dumps(g1, indent=2))
    (out / "L30-1b_create_preserves_data.json").write_text(json.dumps(g1b, indent=2))
    (out / "L30-2_milvus_unreachable.json").write_text(json.dumps(g2, indent=2))
    (out / "L30-3_milvus_refusal.json").write_text(json.dumps(g3, indent=2))

    summary = {
        "L30-1": g1["verdict"],
        "L30-1b": g1b["verdict"],
        "L30-2": g2["verdict"],
        "L30-3": g3["verdict"],
        "generated": nine.now(),
    }
    print(json.dumps(summary, indent=2))
    print("\n--- L30-1 per-store ---")
    print(
        json.dumps(
            {
                "live": g1["live"],
                "infra_blocked": g1["infra_blocked"],
                "parity": g1["parity"],
            },
            indent=2,
        )
    )
    print("\n--- L30-1b create preserves data ---")
    print(json.dumps(g1b["live"], indent=2))
    print("\n--- L30-2 cases ---")
    print(json.dumps(g2["cases"], indent=2))
    print("\n--- L30-3 cases ---")
    print(json.dumps(g3["cases"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
