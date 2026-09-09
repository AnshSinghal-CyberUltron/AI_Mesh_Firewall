"""Highest arrival rate at which the nine-stage pipeline holds p99 under a bound.

The closed-loop driver (load.py) cannot answer this. It holds N requests in flight and
issues a new one only when one completes, so it can only sample the discrete rates that
integer concurrencies happen to produce — measured, concurrency 1 gave 13.6 RPS (p99 17.60)
and concurrency 2 gave 27.0 RPS (p99 28.40), with the bound crossed somewhere in between and
no way to ask about 18. It also conflates offered load with queue depth: raising concurrency
raises both at once.

Here requests depart on a schedule regardless of whether earlier ones have come back, which
is what a real client does.

    python scripts/perf/e2e/openloop.py --key K --rates 10,14,18,22,26 --p99-bound-ms 20
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

STAGES = ("auth", "kill_switch", "rate_limit", "policy", "input_scan",
          "model_routing", "model_input", "model_output", "output_guardrail")


def make_prompt(n: int, nonce: str) -> str:
    base = f"[{nonce}] Summarise the deployment runbook and list the rollback steps. "
    return (base * (n // len(base) + 1))[:n]


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))]


class Result:
    __slots__ = ("lat", "err", "nine", "not_nine", "sent", "late", "saturated", "wall")

    def __init__(self):
        self.lat: list[float] = []
        self.err = 0
        self.nine = 0
        self.not_nine = 0
        self.sent = 0
        self.late: list[float] = []   # how far behind schedule each departure was
        self.saturated = False        # driver could not sustain the offered rate
        self.wall: list[float] = []   # client-side wall, reported to expose driver overhead


def one(url, key, model, prompt_chars, timeout, res: Result, lock, seq, target_t):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": make_prompt(prompt_chars, f"o{seq:07d}")}],
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    # R2 measured where it matters: how late the request ACTUALLY departed, not how late
    # the scheduling loop was. pool.submit() queues instead of blocking, so the loop can
    # keep perfect time while every worker is busy and real sends drift minutes behind.
    with lock:
        res.late.append((t0 - target_t) * 1000)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            e.read()
        except Exception:
            pass
        with lock:
            res.err += 1
        return
    except Exception:
        with lock:
            res.err += 1
        return
    wall = (time.perf_counter() - t0) * 1000
    tr = payload.get("pipeline_trace") or {}
    stages = {s.get("name"): s for s in tr.get("stages", [])}
    ran = [n for n in STAGES if n in stages and stages[n].get("action") != "skip"]
    # addon from the GATEWAY'S OWN TRACE — t_addon_pre_ms + t_addon_post_ms — exactly as
    # load.py defines it, so the two drivers measure the same quantity and can be compared.
    #
    # NOT client wall time. A cross-check at 13.6 RPS had this driver reporting p50 22.15 ms
    # against the closed-loop driver's 14.60 ms on the same gateway at the same moment: the
    # ~7.5 ms difference was this Python client's own thread-pool and GIL overhead. A load
    # generator that adds 7.5 ms cannot adjudicate a 20 ms bound.
    addon = (float(tr.get("t_addon_pre_ms") or 0.0)
             + float(tr.get("t_addon_post_ms") or 0.0))
    with lock:
        if len(ran) == len(STAGES):
            res.nine += 1
        else:
            res.not_nine += 1
        res.wall.append(wall)
        if addon > 0:
            res.lat.append(addon)


def drive(a, rate: float) -> Result:
    res = Result()
    lock = threading.Lock()
    interval = 1.0 / rate
    # Enough workers that a slow response cannot delay a departure (R1). Sized from the
    # rate and a generous latency assumption, then bounded.
    workers = max(8, min(512, int(rate * a.assumed_latency_s * 4) + 8))
    started = time.perf_counter()
    deadline = started + a.warmup_s + a.window_s
    inflight = [0]

    def _done(_f):
        with lock:
            inflight[0] -= 1

    # NOT a `with` block. ThreadPoolExecutor's context exit waits for EVERY queued task, so
    # an unachievable rate leaves tens of thousands of futures to drain and the driver hangs
    # instead of reporting that the rate was unachievable. Shut down explicitly, cancelling
    # what never departed.
    pool = ThreadPoolExecutor(max_workers=workers)
    seq = 0
    try:
        while True:
            target = started + seq * interval
            now = time.perf_counter()
            if target > deadline:
                break
            if target > now:
                time.sleep(target - now)
            with lock:
                busy = inflight[0]
            if busy >= a.max_inflight:
                # Every worker is occupied and the backlog is growing: the DRIVER cannot
                # sustain this rate, so anything measured past here describes the client.
                res.saturated = True
                break
            measuring = time.perf_counter() >= started + a.warmup_s
            with lock:
                inflight[0] += 1
            fut = pool.submit(one, a.url, a.key, a.model, a.prompt_chars, a.timeout,
                              res if measuring else Result(), lock, seq, target)
            fut.add_done_callback(_done)
            res.sent += 1
            seq += 1
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return res


def container_cpu(name: str) -> float | None:
    try:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", name],
                             capture_output=True, text=True, timeout=15)
        return float(out.stdout.strip().rstrip("%"))
    except Exception:
        return None


def assert_stub(expect_s, container) -> str | None:
    try:
        out = subprocess.run(["docker", "exec", container, "printenv",
                              "GATEWAY_LOADTEST_STUB_DURATION_S"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        actual = float((out.stdout or "").strip())
    except Exception:
        return None
    if abs(actual - expect_s) > 1e-6:
        return (f"gateway stub is {actual}s, --expect-stub-s says {expect_s}s. A run at a "
                f"different stub duration is not comparable (R7/R3).")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8300/v1/chat/completions")
    ap.add_argument("--model", default="auto")
    ap.add_argument("--rates", default="10,14,18,22,26,30")
    ap.add_argument("--warmup-s", type=float, default=3.0)
    ap.add_argument("--window-s", type=float, default=10.0)
    ap.add_argument("--settle-s", type=float, default=3.0)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--prompt-chars", type=int, default=4096)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--p99-bound-ms", type=float, default=20.0)
    ap.add_argument("--assumed-latency-s", type=float, default=0.15)
    ap.add_argument("--max-inflight", type=int, default=2000,
                    help="stop offering when this many requests are outstanding — past it "
                         "the driver, not the gateway, is the constraint (R2)")
    ap.add_argument("--expect-stub-s", type=float, default=None)
    ap.add_argument("--gateway-container", default="aimeshperf-gateway-1")
    a = ap.parse_args()

    if a.expect_stub_s is not None:
        bad = assert_stub(a.expect_stub_s, a.gateway_container)
        if bad:
            print("REFUSING TO RUN:\n  - " + bad)
            return 1

    print(f"open loop — arrival rate is independent of completion (bound: addon p99 "
          f"<= {a.p99_bound_ms} ms)")
    print(f"{'target':>7}{'achieved':>10}{'p50':>8}{'p90':>8}{'p99':>9}"
          f"{'wall50':>9}{'gw_cpu%':>9}{'err%':>7}{'9stage':>8}  verdict")

    best = None
    for rate in [float(x) for x in a.rates.split(",") if x.strip()]:
        reps = []
        for _ in range(a.repeat):
            # Sample CPU DURING the window. Sampling after it returns an idle gateway —
            # the first version of this reported 1.3% while the closed-loop driver saw
            # 34.2% on the same run.
            cpu_box = {}

            def _sample(box=cpu_box):
                time.sleep(a.warmup_s + a.window_s * 0.5)
                box["cpu"] = container_cpu(a.gateway_container)

            t = threading.Thread(target=_sample, daemon=True)
            t.start()
            r = drive(a, rate)
            t.join(timeout=5)
            cpu = cpu_box.get("cpu") or 0.0
            n = len(r.lat) + r.err
            reps.append({
                "p50": statistics.median(r.lat) if r.lat else 0.0,
                "p90": pct(r.lat, 90), "p99": pct(r.lat, 99),
                "err": r.err / max(n, 1),
                "nine_ok": r.not_nine == 0 and r.nine > 0,
                "achieved": len(r.lat) / a.window_s,
                "cpu": cpu,
                "late_p99": pct(r.late, 99) if r.late else 0.0,
                "saturated": r.saturated,
                "wall50": statistics.median(r.wall) if r.wall else 0.0,
            })
            time.sleep(a.settle_s)

        p99 = statistics.median([x["p99"] for x in reps])
        p50 = statistics.median([x["p50"] for x in reps])
        p90 = statistics.median([x["p90"] for x in reps])
        ach = statistics.median([x["achieved"] for x in reps])
        err = max(x["err"] for x in reps)
        cpu = statistics.median([x["cpu"] for x in reps])
        nine = all(x["nine_ok"] for x in reps)
        late = max(x["late_p99"] for x in reps)
        saturated = any(x["saturated"] for x in reps)
        wall50 = statistics.median([x["wall50"] for x in reps])

        refusals = []
        if not nine:
            refusals.append("not all nine stages ran (R3)")
        if err > 0.005:
            refusals.append(f"error rate {100*err:.2f}% > 0.5% (R3)")
        if late > 50.0:
            refusals.append(f"driver fell {late:.0f} ms behind its own schedule — this "
                            f"measures the CLIENT, not the gateway (R2)")
        if saturated:
            refusals.append("driver hit its in-flight cap — it could not offer this rate (R2)")
        if ach < 0.9 * rate:
            refusals.append(f"achieved {ach:.1f} of {rate:.1f} target RPS (R2)")

        verdict = "; ".join(refusals) if refusals else (
            "PASS" if p99 <= a.p99_bound_ms else f"p99 over {a.p99_bound_ms:.0f} ms")
        print(f"{rate:7.1f}{ach:10.1f}{p50:8.2f}{p90:8.2f}{p99:9.2f}"
              f"{wall50:9.1f}{cpu:9.1f}{100*err:7.2f}{'yes' if nine else 'NO':>8}  {verdict}")

        if not refusals and p99 <= a.p99_bound_ms:
            best = (rate, ach, p99, cpu)

    print()
    if best is None:
        print(f"REFUSING TO REPORT A RATE: no arrival rate held addon p99 <= "
              f"{a.p99_bound_ms} ms with all nine stages.")
        return 1
    rate, ach, p99, cpu = best
    print(f"HIGHEST RATE MEETING THE BOUND: {ach:.1f} RPS (target {rate:.1f}), "
          f"p99 {p99:.2f} ms, gateway at {cpu:.1f}% of one vCPU-equivalent.")
    if cpu < 200:
        print(f"  NOTE: the gateway was NOT saturated at this rate ({cpu:.1f}% CPU), so this "
              f"is a LATENCY-bounded rate, not the machine's capacity (R5).")
    return 0


raise SystemExit(main())
