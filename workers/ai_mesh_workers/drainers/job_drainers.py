from core.tasks import drain_gateway_jobs_from_redis


def process_gateway_jobs_batch(batch_size: int = 200) -> int:
    """Compatibility wrapper for gateway job draining."""
    return drain_gateway_jobs_from_redis(batch_size=batch_size)
