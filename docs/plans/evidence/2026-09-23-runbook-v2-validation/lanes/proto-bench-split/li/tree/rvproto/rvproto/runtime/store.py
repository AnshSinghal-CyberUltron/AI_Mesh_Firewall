"""Shared-state client (Redis). Pool size and op timeout derive from the contract."""

from __future__ import annotations

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from rvproto.runtime.contract import Bounds, ResourceContract

StoreError = (RedisError, OSError, TimeoutError)

# Redis key layout (prototype-owned; unfrozen per runbook §10.2.1).
K_KEYS = "rv:keys"  # hash: sha256(api key) -> principal json
K_AUTH_EPOCH = "rv:auth_epoch"
K_KILLSWITCH = "rv:killswitch"  # "1" => serve nothing
K_KILLSWITCH_ORGS = "rv:killswitch:org"  # hash: org -> "1" => that org is disabled
K_KILLSWITCH_MODELS = "rv:killswitch:model"  # hash: model -> "1" => that model is disabled
K_PLAN_VERSIONS = "rv:plan_versions"  # hash: org -> version
K_PLAN_PREFIX = "rv:plan:"  # string json per org
K_PLAN_CHANNEL = "rv:plan:updates"
K_BUDGET_PREFIX = "rv:budget:"  # int tokens remaining per org
K_AUDIT_PREFIX = "rv:audit:"  # stream per org

LEASE_LUA = """
local remaining = tonumber(redis.call('GET', KEYS[1]) or '0')
if remaining <= 0 then return 0 end
local grant = tonumber(ARGV[1])
if remaining < grant then grant = remaining end
redis.call('DECRBY', KEYS[1], grant)
return grant
"""


def connect(url: str, contract: ResourceContract, bounds: Bounds) -> aioredis.Redis:
    timeout_s = contract.target_p99_ms / 1000.0
    pool = aioredis.BlockingConnectionPool.from_url(
        url,
        max_connections=bounds.redis_connections,
        timeout=timeout_s,
        socket_timeout=timeout_s,
        socket_connect_timeout=timeout_s,
        decode_responses=False,
    )
    return aioredis.Redis(connection_pool=pool)


def connect_background(url: str, contract: ResourceContract, bounds: Bounds) -> aioredis.Redis:
    """Background refreshers/writers: bounded by the same pool, longer op timeout
    (a multiple of the refresh period is not a capacity value)."""
    pool = aioredis.BlockingConnectionPool.from_url(
        url, max_connections=bounds.redis_connections, decode_responses=False
    )
    return aioredis.Redis(connection_pool=pool)
