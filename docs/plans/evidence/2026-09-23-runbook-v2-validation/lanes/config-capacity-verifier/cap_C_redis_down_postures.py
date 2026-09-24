"""Redis fully down (connection refused): posture of each chat-prefix component, in order."""
import asyncio, socket
import redis.asyncio as aioredis
s = socket.socket(); s.bind(("127.0.0.1", 0)); DEAD = s.getsockname()[1]; s.close()   # nothing listens
URL = f"redis://127.0.0.1:{DEAD}/0"
from middleware import validate_api_key
from kill_switch import check_kill_switch
from model_state import check_model_state
from rate_limit_enforcement import enforce_org_burst_rpm
from rate_limiter import RateLimiter
class _Sync:
    def get_config(self, slug): return {"rate_limit_enabled": True, "enforcement_mode": "block"}
class _Ctx: org_slug = "acme"; prefix = "zs_test1"; key_hash = "h"
async def main():
    c = aioredis.Redis.from_url(URL, decode_responses=True, socket_connect_timeout=0.5)
    ctx, err = await validate_api_key("zs_test1_" + "x" * 32, c)
    print("1 auth (middleware.validate_api_key):", err and (err["status_code"], err["error"]))
    ks = await check_kill_switch(c, "m", org_slug="acme", key_prefix="zs_test1")
    print("2 kill-switch:", {"is_killed": ks.is_killed, "action": ks.action, "scope": ks.scope})
    ms = await check_model_state(c, "m", org_slug="acme")
    print("3 model_state:", ms.status)
    r = await enforce_org_burst_rpm(_Ctx(), redis_client=c, config_sync=_Sync(), gateway_config={}, metrics={},
                                    emit_telemetry=lambda **k: None, event_type="chat")
    print("4 burst/RPM:", "None (request proceeds = fail-OPEN)" if r is None else r.status_code)
    rl = RateLimiter(redis_url=URL)
    print("5 org TPM:", await rl.check_org_rate_limit("acme", 1000), " 6 key TPM:", await rl.check_rate_limit("h", 1000))
asyncio.run(main())
