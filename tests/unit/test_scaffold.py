"""Smoke tests for the shared jobs scaffold (ai_mesh_shared.jobs)."""

from ai_mesh_shared.jobs.envelope import JobEnvelope
from ai_mesh_shared.jobs.queues import GATEWAY_JOBS_QUEUE, TELEMETRY_EVENTS_QUEUE


def test_job_envelope_roundtrip():
    env = JobEnvelope(
        job_type="mcp_audit",
        request_id="req-1",
        org_id=2,
        payload={"tool": "read_file"},
    )
    d = env.to_dict()
    assert d["job_type"] == "mcp_audit"
    assert d["org_id"] == 2
    assert d["idempotency_key"]
    assert d["payload_hash"]
    assert d["payload"] == {"tool": "read_file"}


def test_job_envelope_generates_request_id_when_missing():
    env = JobEnvelope(job_type="tier2_post_scan", request_id="", org_id=None, payload={})
    d = env.to_dict()
    assert d["request_id"].startswith("job-")


def test_job_envelope_idempotency_key_is_stable():
    kwargs = dict(job_type="mcp_audit", request_id="req-1", org_id=2, payload={"a": 1})
    d1 = JobEnvelope(**kwargs).to_dict()
    d2 = JobEnvelope(**kwargs).to_dict()
    assert d1["idempotency_key"] == d2["idempotency_key"]
    assert d1["idempotency_key"].startswith("mcp_audit:req-1:")


def test_queue_constants():
    # These Redis list names are the contract between the gateway producer
    # and the control-plane drainers (core/tasks.py REDIS_* keys).
    assert TELEMETRY_EVENTS_QUEUE == "telemetry:events"
    assert GATEWAY_JOBS_QUEUE == "gateway:jobs"
