"""Smoke tests for standalone product scaffold."""

from ai_mesh_shared.jobs.envelope import JobEnvelope
from ai_mesh_shared.jobs.queues import ALL_QUEUES, PLATFORM_BATCH


def test_job_envelope_roundtrip():
    env = JobEnvelope(
        job_type="mcp_audit",
        request_id="req-1",
        org_id=2,
        payload={"tool": "read_file"},
    )
    env.validate()
    d = env.to_dict()
    assert d["job_type"] == "mcp_audit"
    assert d["org_id"] == 2
    assert d["idempotency_key"]


def test_queue_constants():
    assert PLATFORM_BATCH in ALL_QUEUES
    assert len(ALL_QUEUES) == 6
