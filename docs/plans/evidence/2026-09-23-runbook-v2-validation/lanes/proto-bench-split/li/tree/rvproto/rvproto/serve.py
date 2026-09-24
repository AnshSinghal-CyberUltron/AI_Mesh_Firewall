"""Entry point (outside the layered packages): launchers + worker + guard owner.

    python -m rvproto.serve                    # launcher: contract.workers() HTTP workers
                                               #   (+ local owners when RV_GUARD_TOPOLOGY=owner)
    python -m rvproto.serve --guard-node       # guard-only node (split topology): owners only
    python -m rvproto.serve --worker           # one worker (RV_WORKER_INDEX set by the launcher)
    python -m rvproto.serve --guard-owner <i>  # the process that owns GPU i

Workers bind the same port with SO_REUSEPORT (kernel load-balances connections);
worker i gets RV_WORKER_INDEX=i so local_gpu can pin GPU i % n_gpus (in_process) or
use owner i % n_owners (owner). A gateway-only node is the plain launcher with
RV_GUARD_TOPOLOGY=remote (workers only; owners on guard nodes, RV_GUARD_OWNER_ADDRS).
Launchers start owners before workers and restart an owner that dies; while it is down,
guard calls that need it are UNAVAILABLE.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from rvproto.runtime import contract as rc
from rvproto.runtime.config import Settings, describe, load_settings


def _socket(host: str, port: int, backlog: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind((host, port))
    sock.listen(backlog)
    sock.set_inheritable(True)
    return sock


def worker() -> None:
    import uvicorn

    from rvproto.edge.app import RvApp

    s = load_settings()
    contract, _ = rc.load()
    bounds = rc.derive(contract, s)
    sock = _socket(s.host, s.port, bounds.listen_backlog)
    config = uvicorn.Config(
        RvApp(),
        loop="uvloop",
        http="httptools",
        lifespan="on",
        access_log=False,
        log_level="warning",
        limit_concurrency=bounds.limit_concurrency,
        backlog=bounds.listen_backlog,
        timeout_keep_alive=int(os.environ.get("RV_KEEPALIVE_S", "75")),
        server_header=False,
        date_header=False,
    )
    uvicorn.Server(config).run(sockets=[sock])


def guard_owner(index: int) -> None:
    from rvproto.detect.guard.factory import model_hash
    from rvproto.detect.guard.owner import run_owner

    s = load_settings()
    run_owner(s, index, gpu=s.guard_backend == "local_gpu", model_hash=model_hash(s))


def _log(event: str, **kw: object) -> None:
    sys.stderr.write(json.dumps({"event": event, **kw}, default=str) + "\n")


def _env(s: Settings) -> dict[str, str]:
    env = dict(os.environ)
    if s.guard_model and "RV_GUARD_MODEL_SHA256" not in env:
        from rvproto.detect.semantic import file_hash

        env["RV_GUARD_MODEL_SHA256"] = file_hash(s.guard_model)
    return env


class Owners:
    """Supervisor of this node's guard owners (one per GPU; one for local_cpu)."""

    def __init__(self, s: Settings, env: dict[str, str]) -> None:
        from rvproto.detect.guard.factory import owner_count

        self.s = s
        self.n = owner_count(s)
        self.env = dict(env)
        if s.guard_backend == "local_gpu":
            self.env["RV_GUARD_N_GPUS"] = str(self.n)  # owners and workers agree on the GPU count
        sock_dir = Path(s.guard_socket_dir)
        sock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for stale in sock_dir.glob("guard-*"):  # a stale .ready would admit workers to a dead owner
            stale.unlink()
        self.spawned: dict[int, float] = {}
        self.procs = {i: self._spawn(i) for i in range(self.n)}

    def _spawn(self, i: int) -> subprocess.Popen[bytes]:
        (Path(self.s.guard_socket_dir) / f"guard-{i}.sock.ready").unlink(missing_ok=True)
        self.spawned[i] = time.monotonic()
        return subprocess.Popen([sys.executable, "-m", "rvproto.serve", "--guard-owner", str(i)], env=self.env)

    def poll(self) -> None:
        """An owner death is an outage (UNAVAILABLE -> posture), not a stop: restart it, at most
        once per staleness period (crash-loop guard)."""
        for i, o in list(self.procs.items()):
            rc_ = o.poll()
            if rc_ is not None and time.monotonic() - self.spawned[i] >= self.s.ks_stale_ms / 1000:
                _log("guard_owner_exited", owner=i, pid=o.pid, returncode=rc_)
                self.procs[i] = self._spawn(i)
                _log("guard_owner_restarted", owner=i, pid=self.procs[i].pid)

    def stop(self) -> None:
        for o in self.procs.values():
            if o.poll() is None:
                o.send_signal(signal.SIGTERM)
        for o in self.procs.values():
            try:
                o.wait(timeout=10)
            except subprocess.TimeoutExpired:
                o.kill()


def guard_node() -> int:
    """Guard-only node (split topology): owners listening per RV_GUARD_OWNER_LISTEN, no workers."""
    s = load_settings()
    contract, logs = rc.load()
    owners = Owners(s, _env(s))
    _log("guard_node", guard_owners=owners.n, listen_unix=s.guard_owner_listen_unix,
         listen_tcp=s.guard_owner_listen_tcp, settings=describe(s),
         contract=rc.describe(contract, logs, rc.derive(contract, s)))
    stopping = False

    def _stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not stopping:
        owners.poll()
        time.sleep(0.2)
    owners.stop()
    return 0


def launcher() -> int:
    s = load_settings()
    contract, logs = rc.load()
    bounds = rc.derive(contract, s)
    n = bounds.workers
    env = _env(s)
    owners = None
    if s.guard_topology == "owner" and s.guard_backend in ("local_cpu", "local_gpu"):
        owners = Owners(s, env)
        env = owners.env
    _log("launcher", workers=n, guard_topology=s.guard_topology, guard_owners=owners.n if owners else 0,
         owner_addrs=s.guard_owner_addrs, settings=describe(s), contract=rc.describe(contract, logs, bounds))
    procs = []
    for i in range(n):
        wenv = dict(env, RV_WORKER_INDEX=str(i), RV_WORKER_COUNT=str(n))
        procs.append(subprocess.Popen([sys.executable, "-m", "rvproto.serve", "--worker"], env=wenv))
    stopping = False

    def _stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    code = 0
    while procs:
        for p in list(procs):
            rc_ = p.poll()
            if rc_ is not None:
                procs.remove(p)
                if rc_ != 0:
                    code = rc_
                    _stop(signal.SIGTERM, None)
        if owners is not None and not stopping:
            owners.poll()
        time.sleep(0.2)
    if owners is not None:
        owners.stop()
    return code


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker()
    elif "--guard-owner" in sys.argv:
        guard_owner(int(sys.argv[sys.argv.index("--guard-owner") + 1]))
    elif "--guard-node" in sys.argv:
        raise SystemExit(guard_node())
    else:
        raise SystemExit(launcher())
