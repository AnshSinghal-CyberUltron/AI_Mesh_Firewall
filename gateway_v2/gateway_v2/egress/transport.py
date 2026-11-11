"""Structural protocols for the INJECTED scanner / token counter (GW13 extract).

These three structural protocols were lifted out of ``egress/stream.py`` so that
module stays under the ``check_sizes`` module-length gate; the behaviour is
unchanged and ``egress.stream`` re-exports all three so every existing
``from gateway_v2.egress.stream import ScanProtocol/ScanResultLike/TokenCounterProtocol``
keeps resolving.

They describe exactly what ``StreamPipeline`` needs from ``gateway_v2.detect``
WITHOUT importing it: the import-linter ``layers`` contract forbids an
``egress -> detect`` edge, so the scanner, its result, and the token counter are
INJECTED and described here only structurally. The real
``detect.holdback.ScanResult`` / ``scan`` / ``TokenIndex`` satisfy these
structurally and are injected by the tests. This module imports only stdlib
``typing`` (``Protocol`` / ``runtime_checkable``) and adds no ``gateway_v2``
import, so it introduces no layer edge.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = (
    "ScanProtocol",
    "ScanResultLike",
    "TokenCounterProtocol",
)


@runtime_checkable
class ScanResultLike(Protocol):
    """The structural shape of a scanner result the pipeline reads.

    Mirrors ``detect.holdback.ScanResult`` field-for-field without importing it.
    ``hold_index`` is the earliest held byte (``buf[:hold_index]`` is releasable);
    ``held_class`` is the pattern class forcing the hold (or ``None``);
    ``is_relaxed`` is whether that class is relaxed; ``forced_release`` /
    ``forced_release_index`` carry the word-class cap force-release accounting;
    ``overflow`` signals a relaxed-class ceiling breach (handled by task 9).
    """

    @property
    def hold_index(self) -> int: ...
    @property
    def held_class(self) -> str | None: ...
    @property
    def is_relaxed(self) -> bool: ...
    @property
    def forced_release(self) -> bool: ...
    @property
    def forced_release_index(self) -> int: ...
    @property
    def overflow(self) -> bool: ...


class ScanProtocol(Protocol):
    """The injected scanner: a callable matching the pure holdback-scan core.

    Mirrors ``detect.holdback.scan`` exactly (a pure function of the pending
    buffer, the final flag, the hold cap, the injected token counter, and the
    window). The ``gateway_v2.detect.holdback.scan`` *function* satisfies this
    structurally, so a test injects it directly.

    It is a plain callable, not an object with a ``.scan`` attribute, on purpose:
    the data-plane tenant-scale gate forbids an attribute call named ``scan`` (a
    Redis ``SCAN`` over a whole collection), and the holdback scan is not a store
    read. The Callable form describes the same surface free of that false-positive.
    """

    def __call__(
        self,
        buf: str,
        *,
        final: bool,
        hold_cap_tokens: int,
        token_index: TokenCounterProtocol,
        window: int,
    ) -> ScanResultLike: ...


@runtime_checkable
class TokenCounterProtocol(Protocol):
    """The injected upstream-token counter.

    Mirrors ``detect.holdback.TokenIndex``: ``tokens_in`` counts the held
    Upstream_Tokens in a slice (the quantity the cap and histogram bound), and
    ``token_boundaries`` gives the start index of each token run so the oldest
    tokens can be force-released.
    """

    def tokens_in(self, s: str) -> int: ...
    def token_boundaries(self, s: str) -> list[int]: ...
