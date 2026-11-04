"""Shared end-to-end harness for the GW31 route tests: the dev boot path, one event loop."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

KEY = "dev-key-change-me"
ORG = "dev-org"


def contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


def rule(
    rule_id: str,
    keywords: tuple[str, ...],
    *,
    action: str,
    scope: str,
    surfaces: list[str],
    stage: str = "",
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "category": "exfil",
        "mode": "enforce",
        "action": action,
        "threshold": None,
        "priority": 10,
        "scope": scope,
        "on_unavailable": "fail_closed",
        "surfaces": surfaces,
        "condition": {
            "kind": "keywords",
            "field": "",
            "direction": scope,
            "keywords": list(keywords),
            "pattern": "",
            "detector_class": "",
            "pipeline_stage": stage,
        },
    }


def write_plan(
    path: Path,
    rules: list[dict[str, object]] | None = None,
    rag_settings: Mapping[str, object] | None = None,
) -> None:
    body: dict[str, object] = {
        "org_id": ORG,
        "streaming_mode": "incremental",
        "rules": rules
        or [rule("inert", ("zz-never-matches-zz",), action="block", scope="input",
                 surfaces=["chat"])],
    }
    if rag_settings is not None:
        body["rag_settings"] = dict(rag_settings)
    path.write_text(json.dumps(body), encoding="utf-8")


def build_app(plan: Path, *, mutate: object = None) -> tuple[object, dict[str, object]]:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(plan),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=contract(), env=env)
    if callable(mutate):
        mutate(deps)
    return serve._assemble_app(deps), deps


async def call(
    app: object,
    method: str,
    path: str,
    body: Mapping[str, object] | None = None,
    *,
    key: str | None = KEY,
) -> tuple[int, dict[str, object], bytes]:
    payload = json.dumps(body).encode() if body is not None else b""
    headers = [(b"content-type", b"application/json")]
    if key is not None:
        headers.append((b"authorization", f"Bearer {key}".encode()))
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    raw = b"".join(
        bytes(m.get("body", b"")) for m in sent if m["type"] == "http.response.body"  # type: ignore[arg-type]
    )
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}, raw


async def ingest_and_wait(
    app: object, collection: str, documents: list[dict[str, object]], **extra: object
) -> dict[str, object]:
    status, body, raw = await call(
        app, "POST", "/v1/rag/ingest", {"collection": collection, "documents": documents, **extra}
    )
    assert status == 202, raw
    job_id = str(body["job_id"])
    for _ in range(200):
        _s, job, _r = await call(app, "GET", f"/v1/rag/ingest/{job_id}")
        if job.get("state") in ("stored", "failed"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("ingest job did not finish")
