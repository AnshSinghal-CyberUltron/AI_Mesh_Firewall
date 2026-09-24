"""Injected clock for C2 replay — time is never read from the wall."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrozenClock:
    epoch: int

    def now(self) -> int:
        return self.epoch
