"""P8 v1 side: the REAL v1 gateway functions (repo, read-only import) against the shared store db 7.
Run with the repo's gateway venv:  PYTHONPATH=gateway:shared gateway/.venv/bin/python p8_v1_side.py <what>
"""

from __future__ import annotations

import asyncio
import json
import sys

import redis.asyncio as aioredis

from ai_mesh_gateway.kill_switch import check_kill_switch
from ai_mesh_gateway.middleware import validate_api_key

URL = "redis://127.0.0.1:26379/7"


async def main() -> None:
    r = aioredis.Redis.from_url(URL, decode_responses=True)
    ks = await check_kill_switch(r, "gpt-4o-mini", org_slug="acme")
    principal, err = await validate_api_key(sys.argv[1], r)
    print(json.dumps({"side": "v1", "kill_switch": {"is_killed": ks.is_killed, "scope": ks.scope, "action": ks.action},
                      "auth": "ok" if err is None else err}, default=str))


if __name__ == "__main__":
    asyncio.run(main())
