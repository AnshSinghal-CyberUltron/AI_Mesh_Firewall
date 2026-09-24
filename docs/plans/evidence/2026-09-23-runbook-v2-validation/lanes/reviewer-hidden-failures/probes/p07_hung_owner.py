"""P07: benchmarked owner-topology client vs a guard owner that is alive but stops answering
(SIGSTOP'd process, wedged CUDA/TRT call, GIL-holding session build, kernel stall).
Real owner code path: the owner replies only when its MicroBatcher resolves the future; a wedged
inference call never resolves it and the socket stays open.  We emulate exactly that: a Unix-socket
server that sends a valid hello and then reads frames but never answers.
Measures: does submit() resolve (UNAVAILABLE) within the guard deadline?  what does readiness() say?"""
import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
from rvproto.runtime.config import load_settings
from rvproto.runtime.metrics import Registry
from rvproto.detect.guard.ipc_client import OwnerClientBackend
from rvproto.detect.guard.owner import encode_response, U32
from rvproto.domain.guard import Budget, GuardResult

async def main():
    d = tempfile.mkdtemp(prefix="rvh-", dir="/tmp")
    env = dict(os.environ, RV_GUARD_TOPOLOGY="owner", RV_GUARD_SOCKET_DIR=d, RV_KS_REFRESH_MS="500", RV_KS_STALE_MS="5000")
    s = load_settings(env)
    received = []
    async def owner(reader, writer):
        hello = {"index": 0, "pid": os.getpid(), "tokens_per_s": 240000.0, "model_hash": "h", "providers": ["TensorrtExecutionProvider"]}
        writer.write(encode_response(0, GuardResult(True, (), 0, 0, 0, json.dumps(hello))))
        while True:  # accept work, never answer (wedged engine)
            (n,) = U32.unpack(await reader.readexactly(4)); received.append(await reader.readexactly(n))
    srv = await asyncio.start_unix_server(owner, path=os.path.join(d, "guard-0.sock"))
    be = OwnerClientBackend(s, Registry(0), owner_index=0, sharing=1, model_hash="h", name="local_gpu(owner)")
    await be.start()
    deadline_ms = 20.0
    fut = be.submit([[0, 5, 6, 2]], Budget(time.perf_counter_ns() + int(deadline_ms * 1e6)))
    t0 = time.perf_counter()
    try:
        res = await asyncio.wait_for(asyncio.shield(fut), timeout=10.0)
        print(f"resolved after {time.perf_counter()-t0:.3f}s: ok={res.ok} detail={res.detail}")
    except asyncio.TimeoutError:
        print(f"guard deadline {deadline_ms} ms; after {time.perf_counter()-t0:.1f} s the request's guard future is STILL PENDING "
              f"(owner received {len(received)} frame(s)); readiness={await be.readiness()}")
    await be.close(); srv.close()

asyncio.run(main())
