"""Where does the policy stage's time actually go, against the REAL bundle?

Run inside the gateway container so it sees the same compiled policies the
request path sees:

    docker exec aimeshperf-gateway-1 python /app/scripts/perf/policy/bench_policy_engine.py

Reports, per rule TYPE and per COST COMPONENT, so the optimisation target is
chosen from measurement rather than from reading the code.
"""
from __future__ import annotations

import statistics
import sys
import time

sys.path.insert(0, "/app/gateway")
sys.path.insert(0, "/app")

from ai_mesh_gateway import policy_engine as pe  # noqa: E402
from ai_mesh_gateway.policy_sync import filter_policies_by_domain  # noqa: E402


PROMPT = (
    "Please summarise the quarterly planning notes for the platform team. "
    "We discussed capacity, the migration timeline, and the onboarding backlog. "
    "Nothing here is sensitive; it is ordinary internal prose of the kind a "
    "benign user sends many times a day."
)


def _bundle():
    """Load the compiled bundle straight from Redis — the same bytes PolicySync
    caches — so this runs without the gateway's startup hooks."""
    import json
    import os
    import redis  # sync client is fine for a bench

    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    client = redis.from_url(url)
    for key in client.scan_iter(match="policies:compiled:*", count=100):
        slug = key.decode().split(":", 2)[-1]
        raw = client.get(key)
        if not raw:
            continue
        bundle = json.loads(raw)
        if isinstance(bundle, dict):
            bundle = bundle.get("policies") or bundle.get("bundle") or []
        pipeline = filter_policies_by_domain(bundle, "pipeline")
        if pipeline:
            return slug, pipeline
    raise SystemExit("no compiled bundle in Redis")


def timeit(fn, n):
    # median of n, reported in microseconds
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1e6)
    return statistics.median(ts), min(ts), max(ts)


def main() -> int:
    slug, bundle = _bundle()
    rules = [r for p in bundle for r in (p.get("rules") or [])]
    by_type: dict[str, int] = {}
    for r in rules:
        by_type[r.get("rule_type", "?")] = by_type.get(r.get("rule_type", "?"), 0) + 1
    print(f"org={slug}  policies={len(bundle)}  rules={len(rules)}  by_type={by_type}")
    print(f"prompt len={len(PROMPT)}")

    N = 200

    # ── whole stage ──────────────────────────────────────────────────────────
    med, lo, hi = timeit(lambda: pe.evaluate(PROMPT, "", bundle), N)
    print(f"\nevaluate() WHOLE STAGE      {med:9.1f} us   (min {lo:.1f}  max {hi:.1f})")

    # ── per rule-type, isolated ──────────────────────────────────────────────
    regex_rules = [r for r in rules if r.get("rule_type") in ("regex", "pattern")]
    kw_rules = [r for r in rules if r.get("rule_type") == "keywords"]

    def run_all_regex():
        for r in regex_rules:
            pe._evaluate_rule(r, PROMPT, "")

    def run_all_kw():
        for r in kw_rules:
            pe._evaluate_rule(r, PROMPT, "")

    if regex_rules:
        med_r, _, _ = timeit(run_all_regex, N)
        print(f"  {len(regex_rules):3d} regex rules       {med_r:9.1f} us "
              f"({med_r/max(len(regex_rules),1):6.2f} us/rule)")
    if kw_rules:
        med_k, _, _ = timeit(run_all_kw, N)
        print(f"  {len(kw_rules):3d} keyword rules     {med_k:9.1f} us "
              f"({med_k/max(len(kw_rules),1):6.2f} us/rule)")

    # ── COMPONENT: worker-thread handoff vs inline regex ─────────────────────
    compiled = []
    for r in regex_rules:
        c = (r.get("condition") or {})
        pat = c.get("regex") or c.get("pattern")
        if not pat:
            continue
        try:
            compiled.append(pe._compile_regex(pat))
        except Exception:
            pass

    def inline_all():
        for c in compiled:
            c.search(PROMPT)

    def budgeted_all():
        for c in compiled:
            pe._search_with_budget(c, PROMPT)

    if compiled:
        med_i, _, _ = timeit(inline_all, N)
        med_b, _, _ = timeit(budgeted_all, N)
        n = len(compiled)
        print(f"\n  {n:3d} compiled patterns")
        print(f"    INLINE  .search()        {med_i:9.1f} us  ({med_i/n:6.2f} us/rule)")
        print(f"    VIA WORKER (current)     {med_b:9.1f} us  ({med_b/n:6.2f} us/rule)")
        print(f"    HANDOFF OVERHEAD         {med_b-med_i:9.1f} us  "
              f"({(med_b-med_i)/n:6.2f} us/rule)  <== removable without changing WHAT is matched")

    # ── COMPONENT: repeated text.lower() in keyword rules ────────────────────
    def lower_per_rule():
        for _ in kw_rules:
            PROMPT.lower()

    if kw_rules:
        med_l, _, _ = timeit(lower_per_rule, N)
        print(f"\n  {len(kw_rules):3d} redundant .lower() calls "
              f"{med_l:9.1f} us  <== hoistable to ONE per evaluate()")

    # ── COMPONENT: how many regexes could a literal prefilter skip? ──────────
    import re as _re
    lits = 0
    skippable = 0
    low = PROMPT.lower()
    for r in regex_rules:
        c = (r.get("condition") or {})
        pat = c.get("regex") or c.get("pattern") or ""
        # longest run of literal chars, crude but sound as a lower bound
        runs = _re.findall(r"[A-Za-z0-9 _-]{4,}", pat)
        if not runs:
            continue
        lits += 1
        best = max(runs, key=len)
        if best.lower() not in low:
            skippable += 1
    print(f"\n  literal-prefilterable rules: {lits}/{len(regex_rules)}; "
          f"of those, {skippable} would be SKIPPED on this benign prompt")
    return 0


raise SystemExit(main())
