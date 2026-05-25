import json
import logging
import os
from typing import Any

import redis.asyncio as aioredis

from ai_mesh_shared.jobs.envelope import JobEnvelope
from ai_mesh_shared.jobs.queues import GATEWAY_JOBS_QUEUE

LOG = logging.getLogger("gateway.jobs")

_REDIS_URL = os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0")


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
        client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await client.lpush(GATEWAY_JOBS_QUEUE, json.dumps(envelope, default=str))
        return True
    except Exception as exc:
        LOG.warning("enqueue_job failed for %s: %s", job_type, exc)
        return False
