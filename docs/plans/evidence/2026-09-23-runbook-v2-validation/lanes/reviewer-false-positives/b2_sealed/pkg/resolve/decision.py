"""Best-effort 'by type' sealing in Python: the authorization's constructor demands a capability object whose
class is private to resolve/ and whose only instance is created here."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from typing import final


class Disposition(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition


@final
class _MintCap:
    __slots__ = ()


_CAP = _MintCap()


@final
@dataclass(frozen=True, slots=True)
class DispatchAuthorization:
    decision: Decision
    _cap: _MintCap

    def __post_init__(self) -> None:
        if self._cap is not _CAP or self.decision.disposition is Disposition.BLOCK:
            raise TypeError("DispatchAuthorization is minted only by resolve/ for non-BLOCK decisions")


def mint(decision: Decision) -> DispatchAuthorization | None:
    return None if decision.disposition is Disposition.BLOCK else DispatchAuthorization(decision, _CAP)
