"""Claim 2(c) SUT: event-loop lag while each request emits EMITS log records through v1's RedisLogPublisher.

Logging is wired like v1 (main.py:6716-6737 @ baseline 2a657fad): a RedisLogPublisher (baseline code, verbatim)
on logger "gateway" with the level set to GW_LEVEL (v1: DEBUG), formatter "%(message)s"; a second publisher on
"bedrock" (propagate=False, level DEBUG as BEDROCK_LOG_LEVEL's default). Each request replays the next EMITS
records captured from the REAL v1 request path in claim2/a (same logger names, levels, messages), so the
publish sizes and logger fan-out are v1's. Root/stderr propagation is disabled to isolate the Redis publish.
LOGMODE=sync  : v1 behaviour (publish runs inside Handler.emit on the event-loop thread)
LOGMODE=queue : counterfactual fix (logging.handlers.QueueHandler -> QueueListener thread -> same publisher)

Two loop-lag probes run for the whole process lifetime (samples kept in RAM, dumped via GET /_lag):
  timer : t0=perf_counter(); await asyncio.sleep(0.001); lag = elapsed - 1 ms   (classic heartbeat; uvloop/libuv
          timers have 1 ms granularity, so the idle floor is ~0-1 ms -- compare against the EMITS=0 run)
  xthread: a separate OS thread wakes every ~1 ms, stamps t0 and posts loop.call_soon_threadsafe(cb); lag = time until
          the loop runs cb = how long the loop takes to react to an external event (us precision, no timer quantum).
          (Replaced an in-loop call_soon probe, which by construction cannot fire while a handler blocks the loop.)
Per request the handler also times its own emit block (blocked_us) -> directly measured blocking per request.
"""
import array, asyncio, contextlib, json, logging, logging.handlers, os, queue, sys, threading, time
sys.path.insert(0, os.path.expanduser("~/v1shared"))
from ai_mesh_shared.redis_log_handler import RedisLogPublisher  # baseline v1 code
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

EMITS = int(os.environ.get("EMITS", "0"))
MODE = os.environ.get("LOGMODE", "sync")
REDIS = os.environ["REDIS_URL"]
GW_LEVEL = os.environ.get("GW_LEVEL", "DEBUG")
REPLAY = os.path.expanduser(os.environ.get("REPLAY", "~/replay_payloads_P2t2_DEBUG.jsonl"))

# per-request sequence of captured records, in capture order (request by request)
RECS = []
for line in open(REPLAY):
    p = json.loads(line)
    RECS.append((p["logger"], getattr(logging, p["levelname"]), json.loads(p["payload"])["message"]))

def _publisher(service):
    h = RedisLogPublisher(redis_url=REDIS, service_name=service)
    h.setFormatter(logging.Formatter("%(message)s"))
    return h

gw_pub, bd_pub = _publisher("Gateway"), _publisher("Bedrock")
if MODE == "queue":
    q1, q2 = queue.SimpleQueue(), queue.SimpleQueue()
    listeners = [logging.handlers.QueueListener(q1, gw_pub), logging.handlers.QueueListener(q2, bd_pub)]
    gw_handler, bd_handler = logging.handlers.QueueHandler(q1), logging.handlers.QueueHandler(q2)
    for li in listeners: li.start()
else:
    gw_handler, bd_handler = gw_pub, bd_pub
gateway_logger = logging.getLogger("gateway"); gateway_logger.addHandler(gw_handler)
gateway_logger.setLevel(getattr(logging, GW_LEVEL)); gateway_logger.propagate = False
bedrock_logger = logging.getLogger("bedrock"); bedrock_logger.addHandler(bd_handler)
bedrock_logger.setLevel(logging.DEBUG); bedrock_logger.propagate = False
LOGGERS = {name: logging.getLogger(name) for name, _, _ in RECS}

COMPLETION = {"id": "chatcmpl-rvlag", "object": "chat.completion", "created": 1700000000, "model": "rv-micro",
              "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
TIMER, SOON, BLOCKED = array.array("d"), array.array("d"), array.array("d")
STATE = {"i": 0, "reqs": 0, "emitted": 0}

async def chat(request):
    await request.json()
    i = STATE["i"]; t0 = time.perf_counter()
    for k in range(EMITS):
        name, lvl, msg = RECS[(i + k) % len(RECS)]
        LOGGERS[name].log(lvl, msg)
    BLOCKED.append((time.perf_counter() - t0) * 1e6)
    STATE["i"] = (i + EMITS) % len(RECS); STATE["reqs"] += 1; STATE["emitted"] += EMITS
    return JSONResponse(COMPLETION)

def _pct(a, q):
    if not a: return None
    s = sorted(a); return round(s[min(len(s) - 1, int(q * len(s)))], 1)

async def lag(request):
    if request.query_params.get("reset"):   # pure clear: no percentile work on the loop at window start
        for a in (TIMER, SOON, BLOCKED): del a[:]
        STATE["reqs"] = STATE["emitted"] = 0
        return JSONResponse({"reset": True, "t": time.time()})
    out = {"pid": os.getpid(), "emits_per_req": EMITS, "mode": MODE, "gw_level": GW_LEVEL, "reqs": STATE["reqs"],
           "emitted": STATE["emitted"]}
    for nm, a in (("timer_lag_us", TIMER), ("xthread_lag_us", SOON), ("blocked_us_per_req", BLOCKED)):
        out[nm] = {"n": len(a), "p50": _pct(a, .5), "p90": _pct(a, .9), "p99": _pct(a, .99), "p999": _pct(a, .999),
                   "max": round(max(a), 1) if a else None, "mean": round(sum(a) / len(a), 1) if a else None}
    if request.query_params.get("dump"):
        with open(os.path.expanduser(request.query_params["dump"]), "w") as f:
            json.dump({"meta": out, "timer_lag_us": list(TIMER), "xthread_lag_us": list(SOON),
                       "blocked_us_per_req": list(BLOCKED)}, f)
    return JSONResponse(out)

async def _timer_probe():
    while True:
        t0 = time.perf_counter(); await asyncio.sleep(0.001)
        TIMER.append((time.perf_counter() - t0 - 0.001) * 1e6)

def _xthread_probe(loop, stop):
    def cb(t0):
        SOON.append((time.perf_counter() - t0) * 1e6)
    while not stop.is_set():
        time.sleep(0.001)
        loop.call_soon_threadsafe(cb, time.perf_counter())

@contextlib.asynccontextmanager
async def _lifespan(app):
    loop = asyncio.get_running_loop(); stop = threading.Event()
    tasks = [loop.create_task(_timer_probe())]
    threading.Thread(target=_xthread_probe, args=(loop, stop), daemon=True, name="lag-xthread").start()
    yield
    stop.set()
    for t in tasks: t.cancel()

app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]), Route("/_lag", lag)],
                lifespan=_lifespan)
