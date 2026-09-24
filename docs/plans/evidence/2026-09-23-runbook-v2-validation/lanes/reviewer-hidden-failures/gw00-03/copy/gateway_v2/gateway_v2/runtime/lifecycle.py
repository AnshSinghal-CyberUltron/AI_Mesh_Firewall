"""SIGHUP reload of ResourceContract without a process restart."""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.pools import GatewayRuntime
from gateway_v2.runtime.resources import load_contract


def install_sighup(
    runtime: GatewayRuntime,
    on_reload: Callable[[], None] | None = None,
) -> None:
    def handler(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        runtime.reload()
        if on_reload is not None:
            on_reload()

    signal.signal(signal.SIGHUP, handler)


def emit_snapshot(runtime: GatewayRuntime, path: str | None) -> None:
    text = json.dumps(runtime.as_dict(), indent=2, sort_keys=True)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    if path:
        Path(path).write_text(text + "\n", encoding="utf-8")


def _hold_scanner(runtime: GatewayRuntime, stop: threading.Event) -> None:
    pool = runtime.pools[PoolKind.SCANNER]
    pool.acquire()
    while not stop.is_set():
        time.sleep(0.05)
    pool.release()


def serve_forever(
    runtime: GatewayRuntime,
    *,
    hold_lease: bool = False,
    snapshot_path: str | None = None,
) -> int:
    stop = threading.Event()
    bounced = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    install_sighup(runtime, on_reload=bounced.set)

    if hold_lease:
        threading.Thread(target=_hold_scanner, args=(runtime, stop), daemon=True).start()

    emit_snapshot(runtime, snapshot_path)
    while not stop.is_set():
        if bounced.wait(timeout=0.25):
            bounced.clear()
            emit_snapshot(runtime, snapshot_path)
    return 0


def run_cli(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    serve = "--serve" in args
    try:
        contract, logs = load_contract()
    except Exception as exc:
        sys.stderr.write(json.dumps({"error": str(exc), "refuse_start": True}) + "\n")
        return 2
    runtime = GatewayRuntime(contract, logs)
    if serve:
        path = os.environ.get("AMF_SNAPSHOT_PATH")
        hold = os.environ.get("AMF_HOLD_LEASE") == "1"
        return serve_forever(runtime, hold_lease=hold, snapshot_path=path)
    emit_snapshot(runtime, os.environ.get("AMF_SNAPSHOT_PATH"))
    return 0
