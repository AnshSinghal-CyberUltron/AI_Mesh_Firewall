"""GW06 hook: flip a kill switch. python tools/killswitch.py <redis_url> global|org|model [key] on|off"""
import json
import sys
import time
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rvproto.runtime.store import K_KILLSWITCH, K_KILLSWITCH_MODELS, K_KILLSWITCH_ORGS  # noqa: E402

url, scope, *rest = sys.argv[1:]
state = rest[-1]
assert state in ("on", "off") and scope in ("global", "org", "model")
r = redis.Redis.from_url(url)
val = "1" if state == "on" else "0"
if scope == "global":
    r.set(K_KILLSWITCH, val)
else:
    r.hset(K_KILLSWITCH_ORGS if scope == "org" else K_KILLSWITCH_MODELS, rest[0], val)
print(json.dumps({"scope": scope, "key": rest[0] if scope != "global" else None, "state": state,
                  "set_at": time.time()}))
