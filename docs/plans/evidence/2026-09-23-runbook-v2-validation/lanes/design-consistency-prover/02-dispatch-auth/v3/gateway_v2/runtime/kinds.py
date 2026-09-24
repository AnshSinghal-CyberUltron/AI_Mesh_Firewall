"""ResourceContract enumerations and hardware-signal records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PoolKind(StrEnum):
    SCANNER = "scanner"
    PROVIDER = "provider"
    VAULT = "vault"
    REDIS = "redis"
    GUARD = "guard"


@dataclass(frozen=True, slots=True)
class CapacityHint:
    tokens_per_second: float


@dataclass(frozen=True, slots=True)
class HardwareSignals:
    cpu_quota: float
    cpu_source: str
    memory_limit: int
    mem_source: str
    fd_limit: int
    fd_source: str
