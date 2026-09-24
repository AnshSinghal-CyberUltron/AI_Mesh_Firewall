"""P07b: same wedged-owner scenario against the CURRENT tree (post-11:40 UTC wire: remaining-budget frames,
worker-side sweeper).  Fake owner: valid hello on every connection, reads frames, never answers (inference
thread wedged, owner event loop alive)."""
import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
from rvproto.runtime.config import load_settings
from rvproto.runtime.metrics import Registry
from rvproto.detect.guard import wire
from rvproto.detect.guard.ipc_client import OwnerClientBackend
from rvproto.domain.guard import Budget, GuardResult

async def main():
    d = tempfile.mkdtemp(prefix="rvh-", dir="/tmp"); path = os.path.join(d, "guard-0.sock")
    env = dict(os.environ, RV_GUARD_TOPOLOGY="owner", RV_GUARD_SOCKET_DIR=d, RV_KS_REFRESH_MS="500", RV_KS_STALE_MS="5000")
    s = load_settings(env); conns = []
    async def owner(reader, writer):
        conns.append(time.perf_counter())
        hello = {"host": "h", "index": 0, "pid": os.getpid(), "tokens_per_s": 240000.0, "model_hash": "h", "provider": "TensorrtExecutionProvider"}
        writer.write(wire.encode_response(0, GuardResult(True, (), 0, 0, 0, json.dumps(hello))))
        try:
            while True:
                (n,) = wire.U32.unpack(await reader.readexactly(4)); await reader.readexactly(n)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
    srv = await asyncio.start_unix_server(owner, path=path)
    be = OwnerClientBackend(s, Registry(0), endpoints=[wire.Endpoint("unix", path)], sharing=1, model_hash="h", name="local_gpu(owner)")
    await be.start()
    t0 = time.perf_counter()
    fut = be.submit([[0, 5, 6, 2]], Budget(time.perf_counter_ns() + int(20 * 1e6)))
    res = await asyncio.wait_for(asyncio.shield(fut), timeout=10.0)
    print(f"current tree: guard future resolved after {1e3*(time.perf_counter()-t0):.0f} ms: ok={res.ok} detail={res.detail!r}")
    ready = []
    for _ in range(14):
        ready.append((round(time.perf_counter() - t0, 1), (await be.readiness()).ready)); await asyncio.sleep(0.5)
    print("readiness over 7 s while the owner stays wedged:", ready, "| owner connections:", len(conns))
    await be.close(); srv.close()
asyncio.run(main())
