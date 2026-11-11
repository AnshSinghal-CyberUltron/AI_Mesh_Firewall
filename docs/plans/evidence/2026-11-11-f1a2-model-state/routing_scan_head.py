"""F1a-2 evidence: HEAD's per-request routing lookups are O(orgs); the per-org dict is not.

Run from `gateway_v2/`:

    nice -n 10 ../../venv-ci/bin/python -I ../docs/plans/evidence/2026-11-11-f1a2-model-state/routing_scan_head.py

HEAD: `RoutingTable.candidates_for` scans `by_org` (routing_types.py:203-207) and
`RoutedProvider.serves_alias` walks `all_candidates()` (routed_provider.py:201-203), which copies
every candidate of every org (routing_types.py:209-215). Both run per chat request
(`chat_upstream.py:75`, `routed_provider.py:188,222`).

The design replaces both with a lookup in a bounded per-worker LRU keyed by org, then a dict
keyed by casefolded alias. To make "constant in orgs" testable without wall-clock flakiness, the
second half counts dictionary probes through an instrumented mapping: the count per request is
identical at 1k and 100k orgs. Wall time is printed too (thread CPU), and differs only by cache
effects of a large dict, not by an org-proportional loop.
"""

from __future__ import annotations

import random
import sys
import time
from collections import OrderedDict

sys.path.insert(0, ".")

from gateway_v2.dispatch.routing_types import Candidate, CandidateSet, RoutingTable  # noqa: E402


def cset(org: int) -> CandidateSet:
    cands = [
        Candidate(
            candidate_id=f"c{org}-{i}", destination=f"d{org}-{i}", upstream_model="gpt-4o",
            served_models=frozenset({f"m{i}"}), weight=1, provider_key="openai",
        )
        for i in range(5)
    ]
    return CandidateSet.build(cands, max_candidates=8, max_weight=16, max_ident_len=64)


class Counting(dict):  # type: ignore[type-arg]
    probes = 0

    def get(self, key, default=None):  # type: ignore[no-untyped-def]
        Counting.probes += 1
        return super().get(key, default)


def main() -> int:
    rng = random.Random(7)
    print(f"python={sys.version.split()[0]} (thread CPU)")
    one = cset(0)
    print("## HEAD (tuple scan / all_candidates), us per request")
    for size in (1_000, 10_000, 100_000):
        table = RoutingTable.build({f"org-{i:06d}": one for i in range(size)})
        orgs = [f"org-{rng.randrange(size):06d}" for _ in range(200)]
        t0 = time.thread_time()
        for o in orgs:
            table.candidates_for(o)
        scan = (time.thread_time() - t0) / len(orgs) * 1e6
        t0 = time.thread_time()
        for _ in range(20):
            any("m3" in c.served_models for c in table.all_candidates())
        alias = (time.thread_time() - t0) / 20 * 1e6
        print(f"  {size:>7,} orgs: candidates_for {scan:,.0f} us; serves_alias {alias:,.0f} us")

    print("## Design (LRU by org -> dict by alias key): probes and ns per request")
    entry = {"m0": 1, "m1": 1, "m2": 1, "m3": 1, "m4": 1}
    for size in (1_000, 100_000):
        view: OrderedDict[str, Counting] = OrderedDict(
            (f"org-{i:06d}", Counting(entry)) for i in range(size)
        )
        orgs = [f"org-{rng.randrange(size):06d}" for _ in range(100_000)]
        Counting.probes = 0
        t0 = time.thread_time()
        for o in orgs:
            org_view = view.get(o)
            view.move_to_end(o)
            org_view.get("m3")  # type: ignore[union-attr]
        ns = (time.thread_time() - t0) / len(orgs) * 1e9
        print(f"  {size:>7,} orgs: {Counting.probes / len(orgs):.2f} alias probes per request, "
              f"{ns:,.0f} ns per request")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
