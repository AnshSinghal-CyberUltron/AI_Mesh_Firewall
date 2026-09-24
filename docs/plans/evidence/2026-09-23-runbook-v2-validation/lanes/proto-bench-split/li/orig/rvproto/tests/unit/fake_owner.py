"""A guard owner process with a fake engine (unit tests): windows whose first token is 999 are
held until cancelled (each cancellation is appended to $FAKE_LOG); others answer at once with
p = first_token / 1000 and the remaining budget the owner saw in the detail."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from rvproto.detect.guard import owner
from rvproto.domain.guard import Budget, GuardResult
from rvproto.runtime.config import load_settings


class FakeEngine:
    def __init__(self, *args: object, model_hash: str, **kw: object) -> None:
        self.tokens_per_s = 1024.0
        self.model_hash = model_hash
        self.providers = ["FakeEP"]
        self.detail = "fake"

    async def start(self) -> None:
        return None

    def submit(self, rows: Sequence[Sequence[int]], budget: Budget) -> asyncio.Future[GuardResult]:
        fut: asyncio.Future[GuardResult] = asyncio.get_running_loop().create_future()
        if int(rows[0][0]) == 999:
            fut.add_done_callback(lambda f: f.cancelled() and self._log("cancelled"))
            return fut
        left = budget.deadline_ns - time.perf_counter_ns()
        fut.set_result(GuardResult(True, tuple(int(r[0]) / 1000 for r in rows), 11, 22, len(rows),
                                   f"budget_left_ns={left}"))
        return fut

    def _log(self, what: str) -> None:
        with Path(os.environ["FAKE_LOG"]).open("a") as f:
            f.write(what + "\n")


if __name__ == "__main__":
    owner.LocalOnnxBackend = FakeEngine  # type: ignore[misc,assignment]
    owner.run_owner(load_settings(), int(sys.argv[1]), gpu=False, model_hash="fake-hash")
