"""Is the p99 stall inside the firewall, or is the process just unable to schedule?

Every measurement so far has conflated the two: a request's latency includes both the work
the firewall does AND the delay before the process gets round to running it. They are fixed
by opposite means, so telling them apart decides where the remaining effort goes.

Method: drive a TRIVIAL endpoint (one that does no firewall work) from one thread while the
chat load runs, and look at ITS distribution.

  * no-op p99 ~ chat p99  -> the constraint is scheduling. No stage optimisation reaches
                             the target; the process cannot answer promptly under this load.
  * no-op p99 stays flat  -> the stall is genuinely inside the chat path, and the search
                             continues there.

The no-op client is deliberately ONE sequential thread at a modest rate, so it cannot itself
add meaningful load — it is an observer, not a second load generator.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
import urllib.error
import urllib.request


def sample(url: str, timeout: float) -> float | None:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read()
    except urllib.error.HTTPError:
        pass                      # a 4xx/5xx still measures scheduling
    except Exception:
        return None
    return (time.perf_counter() - t0) * 1000


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8300/health")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--interval-ms", type=float, default=20.0)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    lat: list[float] = []
    errs = 0
    end = time.perf_counter() + a.seconds
    while time.perf_counter() < end:
        ms = sample(a.url, a.timeout)
        if ms is None:
            errs += 1
        else:
            lat.append(ms)
        time.sleep(a.interval_ms / 1000.0)

    if not lat:
        print(f"{a.label} NO SAMPLES ({errs} errors)")
        return 1
    print(f"{a.label:28} n={len(lat):5d} err={errs:3d}  "
          f"p50={statistics.median(lat):7.2f}  p90={pct(lat,90):7.2f}  "
          f"p99={pct(lat,99):8.2f}  max={max(lat):8.2f} ms")
    return 0


raise SystemExit(main())
