"""Micro-benchmark for the GW29 hot path (N1 per request, N2 per chunk).

Not collected by pytest.

Run from ``gateway_v2/``: ``python -m tests.edge.bench_lgw29_hot_path``. Prints microseconds
per call for the phase 4b decision (clean, rerouted, engaged kill) and the per-chunk control read
(``evaluate``). Output is saved in the GW29 evidence.
"""

from __future__ import annotations

import time

from gateway_v2.domain.incident import ModelProfile, RequestConstraints
from tests.admit._lgw29_support import GateRig, iso_body, ks

LOOPS = 20_000
TAGS = frozenset({"t"})
PROFILES = {"a": ModelProfile("a", "p", TAGS, 2), "b": ModelProfile("b", "p", TAGS, 2)}


def _per_call_us(fn: object) -> float:
    call = fn  # type: ignore[assignment]
    for _ in range(500):
        call()  # type: ignore[operator]
    start = time.perf_counter()
    for _ in range(LOOPS):
        call()  # type: ignore[operator]
    return (time.perf_counter() - start) / LOOPS * 1e6


def main() -> None:
    clean = GateRig(catalog=True, profiles=PROFILES, fallbacks={"a": "b"},
                    constraints=RequestConstraints(True, None, frozenset(), 0))
    rerouted = GateRig(catalog=True, profiles=PROFILES, fallbacks={"a": "b"},
                       constraints=RequestConstraints(True, None, frozenset(), 0))
    rerouted.engage(ks("iso:a", body=iso_body(fallback="b", expires_at=9e9), feed_seq=1))
    killed = GateRig()
    killed.engage(ks("global", feed_seq=1))
    rows = (
        ("N1 gate.decide clean (no incident)", lambda: clean.decide("a")),
        ("N1 gate.decide rerouted (isolation)", lambda: rerouted.decide("a")),
        ("N1 gate.decide refused (global kill)", lambda: killed.decide("a")),
        ("N2 per-chunk switches.evaluate (clean)", lambda: clean.snap.evaluate("org", "a")),
        ("N2 per-chunk switches.evaluate (engaged)", lambda: killed.snap.evaluate("org", "a")),
    )
    print(f"loops={LOOPS} (injected clocks, in-process, single thread)")
    for name, fn in rows:
        print(f"{name:45s} {_per_call_us(fn):8.2f} us/call")


if __name__ == "__main__":
    main()
