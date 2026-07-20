import json
import logging
import os
from typing import Any

import redis.asyncio as aioredis

from ai_mesh_shared.jobs.envelope import JobEnvelope
from ai_mesh_shared.jobs.queues import GATEWAY_JOBS_QUEUE

LOG = logging.getLogger("gateway.jobs")

_REDIS_URL = os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0")

# Reuse ONE aioredis client (and its connection pool) across all enqueue_job
# calls. Previously each call did aioredis.from_url(...) and NEVER closed it, so
# every enqueued job (chat/MCP/RAG async ingest dispatch — a hot path) leaked a
# client + its connection pool, accumulating Redis connections until exhaustion
# under load. The pool auto-reconnects on transient drops, so caching is safe.
_CLIENT: "aioredis.Redis | None" = None


def _get_client() -> "aioredis.Redis":
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _CLIENT


async def close_jobs_client() -> None:
    """Close the shared enqueue client — call on graceful gateway shutdown."""
    global _CLIENT
    if _CLIENT is not None:
        try:
            await _CLIENT.aclose()
        except Exception:  # noqa: BLE001
            pass
        _CLIENT = None


async def enqueue_job(
    job_type: str,
    request_id: str,
    org_id: int | None,
    payload: dict[str, Any],
) -> bool:
    envelope = JobEnvelope(
        job_type=job_type,
        request_id=request_id,
        org_id=org_id,
        payload=payload,
    ).to_dict()

    try:
        client = _get_client()
        await client.lpush(GATEWAY_JOBS_QUEUE, json.dumps(envelope, default=str))
        return True
    except Exception as exc:
        LOG.warning("enqueue_job failed for %s: %s", job_type, exc)
        return False
