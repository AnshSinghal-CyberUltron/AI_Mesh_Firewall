#!/usr/bin/env python3
"""GW30 Pinecone live-acceptance harness — L30-1/L30-1b/L30-2/L30-3 against REAL Pinecone serverless.

This does NOT edit the shipped 9store harness
(``docs/plans/evidence/2026-11-02-gw30-live-gate/run_live_gates_9store.py``) nor the Milvus harness.
It imports the 9store module's gate machinery (``CountingTransport``, ``live_contract``,
``run_bounded_operation``, ``RECS``, ``QVEC``, ``DIM``, ``now``, ``rid``, ``build_connector``, and
the value types) via an explicit ``importlib`` load, then:

  * overrides its ``LIVE_TRANSPORTS`` to the single Pinecone transport (the SDK resolves each
    index's host from the serverless key), so L30-1 / L30-1b drive the real Pinecone wire;
  * keeps the three cloud SaaS stores (aws_bedrock_kb/gcp_vertex_vector/azure_ai_search) AND milvus
    in ``INFRA_BLOCKED`` — pinecone is NO LONGER blocked;
  * supplies Pinecone-specific L30-1 / L30-1b with an EVENTUAL-CONSISTENCY POLL before the ordering
    query (Pinecone serverless upserts apply asynchronously — a fixed sleep is not enough);
  * supplies Pinecone-specific L30-2 (an invalid-key / bogus-index transport driven through
    ``run_bounded_operation`` → ``provider_unreachable``, ``records is None``, within the bound) and
    L30-3 (a ``CountingTransport`` wrapping a REAL Pinecone connector proving ZERO backing-store ops
    on a credential-scope / org-identity refusal).

The key is loaded ONLY from ``/work/suraj/AI_Mesh_Firewall/.env.gw30-pinecone`` (gitignored) and is
NEVER printed, logged, or written into any evidence file; the credential is referred to by ref id.
Writes per-gate JSON + a captured stdout into this evidence dir (``GW30_EVIDENCE_DIR`` overrides).

Run:
  GW30_EVIDENCE_DIR=docs/plans/evidence/2026-11-03-gw30-pinecone \
    gateway_v2/.venv/bin/python \
    docs/plans/evidence/2026-11-03-gw30-pinecone/run_live_gates_pinecone.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# REPO is this tree's root (the gw30-pinecone worktree when run from there — that tree carries BOTH
# the shipped 9store harness under docs/plans/evidence/ AND the new Pinecone transport under
# gateway_v2/, so there is ONE gateway_v2 class identity in the process and no path gymnastics).
REPO = HERE.parents[3]
GW = REPO / "gateway_v2"
NINE_STORE = (
    REPO / "docs" / "plans" / "evidence" / "2026-11-02-gw30-live-gate" / "run_live_gates_9store.py"
)


def _find_env_file() -> Path:
    """Locate the gitignored credential file.

    It lives in the MAIN repo root (``/work/suraj/AI_Mesh_Firewall/.env.gw30-pinecone``); when this
    harness runs from the gw30-pinecone worktree, ``REPO`` is the worktree root, so also look at the
    main repo root two levels up from a ``.worktrees/<name>`` worktree. Override with GW30_ENV_FILE.
    """
    override = os.environ.get("GW30_ENV_FILE")
    if override:
        return Path(override)
    candidates = [REPO / ".env.gw30-pinecone"]
    # A worktree lives at <main-repo>/.worktrees/<name>; the env file is in <main-repo>.
    if REPO.parent.name == ".worktrees":
        candidates.append(REPO.parent.parent / ".env.gw30-pinecone")
    for cand in candidates:
        if cand.is_file():
            return cand
    return candidates[0]


ENV_FILE = _find_env_file()
sys.path.insert(0, str(GW))

# Load the shipped 9store harness as a module WITHOUT editing it. It imports ``gateway_v2.*`` during
# exec; with GW on the path first that resolves to THIS tree's gateway_v2 (carrying the transport).
_spec = importlib.util.spec_from_file_location("gw30_9store", NINE_STORE)
assert _spec is not None and _spec.loader is not None
nine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nine)

from gateway_v2.dispatch import vector_transports as vlt  # noqa: E402
from gateway_v2.dispatch.vector_adapter import (  # noqa: E402
    CreateRequest,
    DeleteRequest,
    QueryRequest,
    UpsertRequest,
)
from gateway_v2.dispatch.vector_bounded_op import run_bounded_operation  # noqa: E402
from gateway_v2.dispatch.vector_connectors import build_connector  # noqa: E402
from gateway_v2.dispatch.vector_credentials import (  # noqa: E402
    CredentialResolution,
    OrgCredential,
    resolve_scoped_credential,
)
from gateway_v2.domain import posture  # noqa: E402
from gateway_v2.runtime.kinds import HardwareSignals  # noqa: E402
from gateway_v2.runtime.resources import from_signals  # noqa: E402

DIM = nine.DIM


def live_contract():
    """Build the live Resource_Contract from the WORKTREE runtime (same shape as nine.live_contract).

    Built here (not reused from ``nine``) so the contract's ``CapacityUnset``/``CapacityUnavailable``
    classes share identity with the worktree ``run_bounded_operation`` that reads it.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=100 * 1024 * 1024 * 32,
        fd_limit=4096,
        cpu_source="h",
        mem_source="h",
        fd_source="h",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=100 * 1024 * 1024
    )


def _load_env(path: Path) -> dict[str, str]:
    """Tiny inline dotenv parse (no new dependency). Splits ``KEY=VALUE``, ignores blanks/#."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


_ENV = _load_env(ENV_FILE)
PINECONE_API_KEY = _ENV["PINECONE_API_KEY"]  # NEVER printed/written to evidence.
CRED_REF = "cred-pinecone-gw30"  # opaque ref id used in evidence in place of the key.
CLOUD = _ENV.get("PINECONE_CLOUD", "aws")
REGION = _ENV.get("PINECONE_REGION", "us-east-1")
INDEX = _ENV.get("PINECONE_INDEX", "gw30-live-gate")


def _pinecone_transport() -> object:
    return vlt.PineconeTransport(
        vlt.PineconeConnectionParams(
            api_key=PINECONE_API_KEY,
            index_name=INDEX,
            dimension=DIM,
            cloud=CLOUD,
            region=REGION,
        )
    )


# Pinecone is the ONLY live store for this run; the three cloud SaaS stores + milvus stay blocked.
nine.LIVE_TRANSPORTS = {"pinecone": _pinecone_transport}
nine.INFRA_BLOCKED = {
    "milvus": "needs etcd+minio sidecars; not brought up in this run",
    "aws_bedrock_kb": "cloud-only; no local image / no credentials",
    "gcp_vertex_vector": "cloud-only; no local image / no credentials",
    "azure_ai_search": "cloud-only; no local image / no credentials",
}


def _discover_cloud_region() -> dict[str, str]:
    """List existing indexes to confirm the account's real cloud/region; adapt from the gate index.

    Defaults are aws/us-east-1 (D3); if the gate index already exists, adopt its own cloud/region.
    Never prints the key.
    """
    global CLOUD, REGION
    discovered: dict[str, str] = {"default_cloud": CLOUD, "default_region": REGION}
    from pinecone import Pinecone

    pc = Pinecone(api_key=PINECONE_API_KEY)
    names = []
    for idx in pc.list_indexes():
        names.append(idx.name)
        if idx.name == INDEX:
            spec = getattr(idx, "spec", None)
            serverless = getattr(spec, "serverless", None) if spec is not None else None
            if serverless is not None:
                CLOUD = getattr(serverless, "cloud", CLOUD)
                REGION = getattr(serverless, "region", REGION)
    discovered["existing_index_names"] = names
    discovered["resolved_cloud"] = CLOUD
    discovered["resolved_region"] = REGION
    return discovered


def _ensure_index_absent() -> None:
    """Delete the gate index if present and wait (bounded) until it is gone.

    The D4 design uses ONE shared gate index for every adapter collection, so a gate whose
    contract asserts a FRESH ``create_collection`` → ``vector_ok`` (L30-1 and L30-1b's first create)
    must start from a clean slate — otherwise an index left by a prior gate makes the create answer
    ``collection_exists``. This resets the throwaway index to absent before such a gate. Idempotent:
    tolerates an already-absent index.
    """
    from pinecone import Pinecone

    pc = Pinecone(api_key=PINECONE_API_KEY)
    if pc.has_index(INDEX):
        pc.delete_index(INDEX)
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline and pc.has_index(INDEX):
        time.sleep(1.5)


def _wait_index_ready() -> None:
    """Wait (bounded) until the gate index reports ready after a create (serverless provisioning)."""
    from pinecone import Pinecone

    pc = Pinecone(api_key=PINECONE_API_KEY)
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        try:
            desc = pc.describe_index(INDEX)
            status = getattr(desc, "status", None)
            ready = getattr(status, "ready", None) if status is not None else None
            if ready:
                return
        except Exception:  # noqa: BLE001 - not yet visible
            pass
        time.sleep(1.5)


async def _poll_until_queryable(connector, org: str, coll: str, contract, want: int) -> dict:
    """Poll describe/query until the namespace reports >= ``want`` vectors (eventual consistency).

    Returns a small dict recording the poll (attempts, elapsed, final count). Bounded ~30 s.
    """
    deadline = time.monotonic() + 30.0
    attempts = 0
    count = 0
    while time.monotonic() < deadline:
        attempts += 1
        q = await run_bounded_operation(
            lambda: connector.query(
                QueryRequest(
                    org_id=org, store_id="pinecone", collection=coll, vector=nine.QVEC, top_k=want
                )
            ),
            contract,
        )
        count = len(q.records) if q.records is not None else 0
        if count >= want:
            break
        await asyncio.sleep(1.5)
    return {"poll_attempts": attempts, "queryable_count": count}


async def gate_l30_1_pinecone(contract) -> dict:
    """create / upsert(3) / POLL / query asserting r1,r3,r2 / delete(3), all vector_ok."""
    res: dict = {"gate": "L30-1", "store": "pinecone", "timestamp": nine.now(), "live": {}}
    _ensure_index_absent()  # fresh slate so the create below genuinely creates (D4 one-index).
    connector = build_connector("pinecone", _pinecone_transport())
    org = "orgA"
    coll = f"live_pinecone_{nine.RUN}"
    out: dict = {}

    cr = await run_bounded_operation(
        lambda: connector.create_collection(
            CreateRequest(
                org_id=org, store_id="pinecone", collection=coll, dimension=DIM, metric="cosine"
            )
        ),
        contract,
    )
    out["create_collection"] = {"code": cr.code, "status": cr.status}
    _wait_index_ready()

    up = await run_bounded_operation(
        lambda: connector.upsert(
            UpsertRequest(org_id=org, store_id="pinecone", collection=coll, records=nine.RECS)
        ),
        contract,
    )
    out["upsert"] = {"code": up.code, "count": up.count}

    poll = await _poll_until_queryable(connector, org, coll, contract, len(nine.RECS))
    out["poll"] = poll

    q = await run_bounded_operation(
        lambda: connector.query(
            QueryRequest(org_id=org, store_id="pinecone", collection=coll, vector=nine.QVEC, top_k=3)
        ),
        contract,
    )
    ids = [r.id for r in q.records] if q.records is not None else None
    out["query"] = {
        "code": q.code,
        "count": len(q.records) if q.records is not None else None,
        "ids": ids,
        "top_id": q.records[0].id if q.records else None,
    }

    de = await run_bounded_operation(
        lambda: connector.delete(
            DeleteRequest(
                org_id=org, store_id="pinecone", collection=coll, ids=("r1", "r2", "r3")
            )
        ),
        contract,
    )
    out["delete"] = {"code": de.code, "count": de.count}

    await _cleanup_namespace(coll)
    res["live"]["pinecone"] = out
    res["infra_blocked"] = dict(nine.INFRA_BLOCKED)
    ok = (
        out["create_collection"]["code"] == posture.VECTOR_OK
        and out["upsert"]["code"] == posture.VECTOR_OK
        and out["query"]["code"] == posture.VECTOR_OK
        and ids == ["r1", "r3", "r2"]
        and out["delete"]["code"] == posture.VECTOR_OK
    )
    res["ordering_from_store_cosine"] = ids
    res["verdict"] = (
        f"LIVE-PASS (1 live: pinecone) + {len(res['infra_blocked'])} INFRA-BLOCKED"
        if ok
        else "FAIL"
    )
    return res


async def gate_l30_1b_pinecone(contract) -> dict:
    """create / upsert / POLL / create-again -> collection_exists, three records still present."""
    res: dict = {
        "gate": "L30-1b (R2.9 create is non-destructive)",
        "store": "pinecone",
        "timestamp": nine.now(),
        "run": nine.RUN,
        "live": {},
    }
    _ensure_index_absent()  # fresh slate so the FIRST create below is vector_ok (D4 one-index).
    connector = build_connector("pinecone", _pinecone_transport())
    org, coll = "orgA", f"keep_pinecone_{nine.RUN}"

    def create():
        return run_bounded_operation(
            lambda: connector.create_collection(
                CreateRequest(
                    org_id=org, store_id="pinecone", collection=coll, dimension=DIM, metric="cosine"
                )
            ),
            contract,
        )

    first = await create()
    _wait_index_ready()
    up = await run_bounded_operation(
        lambda: connector.upsert(
            UpsertRequest(org_id=org, store_id="pinecone", collection=coll, records=nine.RECS)
        ),
        contract,
    )
    poll = await _poll_until_queryable(connector, org, coll, contract, len(nine.RECS))
    second = await create()
    q = await run_bounded_operation(
        lambda: connector.query(
            QueryRequest(org_id=org, store_id="pinecone", collection=coll, vector=nine.QVEC, top_k=3)
        ),
        contract,
    )
    await _cleanup_namespace(coll)
    ids = [r.id for r in q.records] if q.records is not None else None
    out = {
        "first_create": first.code,
        "upsert": up.code,
        "poll": poll,
        "second_create": second.code,
        "query_after_second_create": q.code,
        "ids_after_second_create": ids,
        "pass": (
            first.code == posture.VECTOR_OK
            and second.code == posture.COLLECTION_EXISTS
            and ids is not None
            and sorted(ids) == ["r1", "r2", "r3"]
        ),
    }
    res["live"]["pinecone"] = out
    res["verdict"] = "LIVE-PASS" if out["pass"] else "FAIL"
    return res


async def gate_l30_2_pinecone(contract) -> dict:
    """An invalid Pinecone transport driven through run_bounded_operation → provider_unreachable.

    The SDK resolves its own host, so there is no dead port to target. Point a transport at a BOGUS
    index name under the real key: ``Index(name=...)`` resolves the host by DESCRIBING the index,
    which raises NotFoundError for a nonexistent index → wrapped to VectorTransportError →
    provider_unreachable, records absent, within the bound — NOT a clean empty vector_ok.
    """
    res: dict = {"gate": "L30-2", "store": "pinecone", "timestamp": nine.now(), "cases": []}
    bogus_index = f"gw30-does-not-exist-{nine.RUN}"
    transport = vlt.PineconeTransport(
        vlt.PineconeConnectionParams(
            api_key=PINECONE_API_KEY,
            index_name=bogus_index,
            dimension=DIM,
            cloud=CLOUD,
            region=REGION,
        )
    )
    conn = build_connector("pinecone", transport)
    request_id = nine.rid()
    start = asyncio.get_event_loop().time()
    r = await run_bounded_operation(
        lambda: conn.query(
            QueryRequest(
                org_id="orgA", store_id="pinecone", collection="nope", vector=nine.QVEC, top_k=3
            )
        ),
        contract,
    )
    elapsed = asyncio.get_event_loop().time() - start
    res["cases"].append(
        {
            "store": "pinecone",
            "request_id": request_id,
            "timestamp": nine.now(),
            "target": f"bogus index '{bogus_index}' (SDK host-resolve → NotFound)",
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


async def gate_l30_3_pinecone(contract) -> dict:
    """Credential-scope / org-identity refusal via the Pinecone path with ZERO backing-store ops."""
    res: dict = {"gate": "L30-3", "store": "pinecone", "timestamp": nine.now(), "cases": []}
    org_a = OrgCredential(
        org_id="orgA", verified=True, _secret="sk-orgA-SECRET-DEADBEEF", ref_id="cred-orgA-001"
    )
    for scenario, request_org in (
        ("cross_org_A_cred_for_B_request", "orgB"),
        ("missing_org_identity", ""),
    ):
        # Wrap a real LIVE Pinecone connector in a counter to prove ZERO store ops on refusal.
        counting = nine.CountingTransport(build_connector("pinecone", _pinecone_transport()))
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
                        store_id="pinecone",
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
                "live_transport_used": "pinecone",
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


async def _cleanup_namespace(coll: str) -> None:
    """Delete the per-run gate namespace's vectors (harness-only, not a transport op)."""
    try:
        from pinecone import Pinecone

        pc = Pinecone(api_key=PINECONE_API_KEY)
        if pc.has_index(INDEX):
            index = pc.Index(name=INDEX)
            ns = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in coll)
            try:
                index.delete(delete_all=True, namespace=ns)
            except Exception:  # noqa: BLE001 - namespace may not exist yet
                pass
    except Exception as exc:  # noqa: BLE001
        print(f"  [cleanup warn pinecone namespace]: {type(exc).__name__}: {exc}")


def _cleanup_index() -> dict:
    """Delete the throwaway gate index. Idempotent (tolerates a missing index)."""
    info: dict = {"ran": True, "index_deleted": None, "already_absent": False}
    try:
        from pinecone import Pinecone

        pc = Pinecone(api_key=PINECONE_API_KEY)
        if pc.has_index(INDEX):
            pc.delete_index(INDEX)
            info["index_deleted"] = INDEX
        else:
            info["already_absent"] = True
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


async def main() -> int:
    contract = live_contract()
    out = Path(os.environ.get("GW30_EVIDENCE_DIR", str(HERE)))
    out.mkdir(parents=True, exist_ok=True)

    discovered = _discover_cloud_region()
    print(f"credential ref: {CRED_REF} (key loaded from gitignored .env, never printed)")
    print(f"discovered: {json.dumps(discovered)}")

    g1 = await gate_l30_1_pinecone(contract)
    g1b = await gate_l30_1b_pinecone(contract)
    g2 = await gate_l30_2_pinecone(contract)
    g3 = await gate_l30_3_pinecone(contract)

    cleanup = _cleanup_index()
    print(f"cleanup: {json.dumps(cleanup)}")

    for g in (g1, g1b, g2, g3):
        g["cleanup"] = cleanup
        g["discovered_cloud"] = CLOUD
        g["discovered_region"] = REGION
        g["credential_ref"] = CRED_REF

    (out / "L30-1_pinecone_parity.json").write_text(json.dumps(g1, indent=2))
    (out / "L30-1b_create_preserves_data.json").write_text(json.dumps(g1b, indent=2))
    (out / "L30-2_pinecone_unreachable.json").write_text(json.dumps(g2, indent=2))
    (out / "L30-3_pinecone_refusal.json").write_text(json.dumps(g3, indent=2))

    summary = {
        "L30-1": g1["verdict"],
        "L30-1b": g1b["verdict"],
        "L30-2": g2["verdict"],
        "L30-3": g3["verdict"],
        "cloud": CLOUD,
        "region": REGION,
        "cleanup": cleanup,
        "generated": nine.now(),
    }
    print(json.dumps(summary, indent=2))
    print("\n--- L30-1 ---")
    print(json.dumps({"live": g1["live"], "ordering": g1["ordering_from_store_cosine"]}, indent=2))
    print("\n--- L30-1b create preserves data ---")
    print(json.dumps(g1b["live"], indent=2))
    print("\n--- L30-2 cases ---")
    print(json.dumps(g2["cases"], indent=2))
    print("\n--- L30-3 cases ---")
    print(json.dumps(g3["cases"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
