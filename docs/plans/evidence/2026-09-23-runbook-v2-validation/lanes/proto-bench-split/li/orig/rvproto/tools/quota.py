"""GW06 LGW06-3 hook: org token budget. python tools/quota.py <redis_url> set|get <org> [tokens]
Lease chunk size: RV_LEASE_CHUNK_TOKENS on the units (logged as a deviation from the contract)."""
import json
import sys
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rvproto.runtime.store import K_BUDGET_PREFIX  # noqa: E402

url, op, org, *rest = sys.argv[1:]
r = redis.Redis.from_url(url)
if op == "set":
    r.set(K_BUDGET_PREFIX + org, int(rest[0]))
print(json.dumps({"org": org, "remaining_tokens": int(r.get(K_BUDGET_PREFIX + org) or 0)}))
