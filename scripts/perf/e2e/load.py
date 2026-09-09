"""Concurrent load driver — task 10, maximum RPS per vCPU.

`drive.py` is sequential and measures LATENCY. This measures CAPACITY: it holds a fixed
number of requests in flight so the system self-paces (a slow gateway produces
backpressure rather than an unbounded queue), sweeps that number upward, and reports the
achieved RPS at the highest concurrency where p99 still meets the latency bound.

WHAT THIS NUMBER IS, PRECISELY
------------------------------
The in-gateway stub answers without provider I/O. That is deliberate — it isolates
gateway cost — but it also removes the socket wait that a real BYOK provider imposes, and
an async gateway overlaps OTHER requests during that wait. So per-request CPU cost here is
right while per-core request throughput is understated.

The result is therefore a **CPU-BOUND FLOOR**, never a ceiling, and is labelled so in the
output. Quoting it as "max RPS" would repeat the class of error that produced a 2,638 ms
"firewall tax" earlier in this work.

    python scripts/perf/e2e/load.py --key $PERF_API_KEY --p99-bound-ms 20
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

STAGES = ("auth", "rate_limit", "policy", "input_scan", "kill_switch",
          "model_routing", "model_input", "model_output", "output_guardrail")


def pct(v, q):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, int(q * len(s)))]


def make_prompt(n: int, nonce: str) -> str:
    base = (f"[{nonce}] Summarise the deployment runbook and list the rollback steps. ")
    return (base * (n // len(base) + 1))[:n]


class Sampler(threading.Thread):
    """docker stats per container, plus this process's own CPU (R4/R5)."""

    def __init__(self, containers):
        super().__init__(daemon=True)
        self.containers = containers
        self.rows: list[dict] = []
        self.stop = threading.Event()

    def run(self):
        while not self.stop.wait(1.0):
            try:
                out = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format",
                     "{{.Name}}\t{{.CPUPerc}}", *self.containers],
                    capture_output=True, text=True, timeout=10).stdout
            except Exception:
                continue
            row = {}
            for line in out.strip().splitlines():
                if "\t" in line:
                    name, cpu = line.split("\t", 1)
                    try:
                        row[name] = float(cpu.strip().rstrip("%"))
                    except ValueError:
                        pass
            if row:
                self.rows.append(row)

    def mean(self, name) -> float:
        vals = [r[name] for r in self.rows if name in r]
        return statistics.mean(vals) if vals else 0.0


def one_request(url, key, model, prompt_chars, max_tokens, timeout, stream):
    nonce = uuid.uuid4().hex[:12]
    body = {"model": model,
            "messages": [{"role": "user", "content": make_prompt(prompt_chars, nonce)}],
            "stream": stream}
    if max_tokens > 0:
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not stream:
                raw = r.read()
            else:
                # Read incrementally so TTFT is real. For a streaming request the
                # meaningful capacity bound is time-to-FIRST-token: total wall is
                # dominated by the provider's generation, which no amount of gateway
                # headroom shortens, so bounding on it would measure the upstream.
                raw = b""
                for chunk in r:
                    raw += chunk
                    if ttft is None and b'"content"' in chunk:
                        ttft = (time.perf_counter() - t0) * 1000
    except Exception as e:                      # noqa: BLE001
        return {"ok": False, "err": f"{type(e).__name__}", "ms": (time.perf_counter() - t0) * 1000}
    ms = (time.perf_counter() - t0) * 1000
    trace, nine = None, False
    if not stream:
        try:
            payload = json.loads(raw)
            trace = payload.get("pipeline_trace")
        except json.JSONDecodeError:
            pass
    else:
        for frame in raw.split(b"\n\n"):
            if frame.startswith(b"data: ") and b"pipeline_trace" in frame:
                try:
                    trace = json.loads(frame[6:].strip()).get("pipeline_trace")
                except json.JSONDecodeError:
                    pass
    addon = None
    stages: dict[str, float] = {}
    roots: dict[str, float] = {}
    if trace:
        for st in (trace.get("stages") or []):
            if isinstance(st, dict) and st.get("name"):
                stages[st["name"]] = float(st.get("latency_ms") or 0.0)
        names = set(stages)
        nine = all(n in names for n in STAGES)
        # THE FIREWALL TAX: T_total - T_upstream. This, not wall, is what the <20 ms
        # SLO refers to. Wall includes the provider's generation - 3 s of deliberate
        # stub pacing here, seconds of real generation in production - so bounding
        # capacity on wall would refuse every run for a reason that has nothing to do
        # with the gateway.
        pre = float(trace.get("t_addon_pre_ms") or 0.0)
        post = float(trace.get("t_addon_post_ms") or 0.0)
        addon = pre + post
        # The gateway's OWN accounting of time inside the request but outside any
        # stage, plus the split around the upstream call. `overhead_ms` names the gap
        # the stage-only attribution kept reporting as UNATTRIBUTED; pre/post localise
        # it to before or after the model call. Both were already being collected and
        # thrown away.
        roots = {"overhead_ms": float(trace.get("overhead_ms") or 0.0),
                 "t_addon_pre_ms": pre, "t_addon_post_ms": post}
    return {"ok": True, "ms": ms, "ttft": ttft, "addon": addon, "nine": nine,
            "stages": stages, "roots": roots, "trace_seen": trace is not None}


def run_level(a, conc: int) -> dict:
    stop_at = time.perf_counter() + a.warmup_s + a.window_s
    start_measure = time.perf_counter() + a.warmup_s
    results: list[dict] = []
    lock = threading.Lock()
    cpu0 = time.process_time()

    def worker():
        while True:
            began = time.perf_counter()
            if began >= stop_at:
                return
            r = one_request(a.url, a.key, a.model, a.prompt_chars,
                            a.max_tokens, a.timeout, a.stream)
            # Count by request START, not completion. Counting completions would
            # include requests that began during warmup and finished inside the
            # window — with 3-second streaming requests and a 20-second window that
            # inflates RPS by up to ~15%.
            if began >= start_measure:
                with lock:
                    results.append(r)

    sampler = Sampler(a.containers.split(","))
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(conc)]
    sampler.start()
    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t_start - a.warmup_s
    sampler.stop.set()
    client_cpu = (time.process_time() - cpu0)

    ok = [r for r in results if r["ok"]]
    # Which latency the bound applies to — see --latency-metric.
    key = a.latency_metric
    lat = [r[key] for r in ok if r.get(key) is not None]
    # If TTFT capture missed (a frame boundary splitting the marker, say), `lat` would
    # be EMPTY, pct() would return 0.0, and a 0 ms p99 would sail past any bound — a
    # silent pass that looks like the best result in the run. Count the misses so the
    # caller can refuse instead.
    lat_missing = len(ok) - len(lat)
    return {
        "conc": conc,
        "n": len(results),
        "rps": len(ok) / elapsed if elapsed > 0 else 0.0,
        "p50": pct(lat, .50), "p90": pct(lat, .90), "p99": pct(lat, .99),
        "err_rate": 1 - (len(ok) / len(results)) if results else 1.0,
        "gw_cpu": sampler.mean(a.gateway_container),
        "client_cpu_cores": client_cpu / elapsed if elapsed > 0 else 0.0,
        "lat_missing": lat_missing,
        "wall_p50": pct([r["ms"] for r in ok], .50),
        "ok_n": len(ok),
        "samples": ok,
        "nine_all": all(r.get("nine") for r in ok) if ok else False,
        "nine_missing": sum(1 for r in ok if not r.get("nine")),
    }


def assert_host_idle() -> str | None:
    """Return a warning if another heavy job is running, else None.

    `pgrep -f "[p]ytest"` is NOT reliable here: the bracket trick stops a plain `pgrep`
    matching itself, but it does not stop it matching the *invoking shell's* command
    line, which contains the pattern. It reported "pytest running" during a clean run.
    Matching on the executable's own argv is the check that means what it says.
    """
    try:
        out = subprocess.run(["ps", "-eo", "comm,args"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return None
    busy = [ln for ln in out.splitlines()[1:]
            if ln.split(" ", 1)[0] in ("pytest", "python3", "python")
            and "pytest" in ln.split(" ", 1)[-1]
            and "load.py" not in ln]
    return (f"{len(busy)} pytest process(es) are running; latency numbers from this run "
            f"are contaminated") if busy else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8300/v1/chat/completions")
    ap.add_argument("--key", required=True)
    ap.add_argument("--model", default="perf-stub-model")
    ap.add_argument("--prompt-chars", type=int, default=4096)
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--warmup-s", type=float, default=5.0)
    ap.add_argument("--window-s", type=float, default=20.0)
    ap.add_argument("--p99-bound-ms", type=float, default=20.0)
    ap.add_argument("--repeat", type=int, default=1,
                    help="repeat each concurrency level N times and report the MEDIAN "
                         "with the observed spread. At ~550 samples a run's p99 rests on "
                         "about 5 observations and swings 100-151 ms on an unchanged "
                         "build; a single run cannot resolve anything smaller than that.")
    ap.add_argument("--latency-metric", choices=("addon", "wall", "ttft"), default="addon",
                    help="addon = the FIREWALL TAX (t_addon_pre + t_addon_post), the "
                         "quantity the <20 ms SLO names and the default. wall includes "
                         "the provider's generation and so measures the upstream. ttft "
                         "is the meaningful bound for streaming.")
    ap.add_argument("--concurrency", default="1,2,4,8,16,32,64")
    ap.add_argument("--containers",
                    default="aimeshperf-gateway-1,aimeshperf-control-1,"
                            "aimeshperf-postgres-1,aimeshperf-redis-1")
    ap.add_argument("--gateway-container", default="aimeshperf-gateway-1")
    ap.add_argument("--cpu-limit", type=float, default=4.0,
                    help="the gateway's `cpus` limit — the RPS/vCPU denominator")
    a = ap.parse_args()

    warn = assert_host_idle()
    if warn:
        print(f"WARNING: {warn}")

    levels = [int(x) for x in a.concurrency.split(",")]
    rows, fails = [], []
    metric = a.latency_metric
    print(f"latency bound applies to: {metric} (p99 <= {a.p99_bound_ms} ms)")
    print(f"{'conc':>5}{'rps':>10}{'p50':>9}{'p90':>9}{'p99':>9}"
          f"{'wall_p50':>10}{'gw_cpu%':>9}{'cli_cores':>11}{'err%':>7}  9stage")
    over = 0
    for c in levels:
        reps = [run_level(a, c) for _ in range(max(1, a.repeat))]
        # Report the MEDIAN run, and carry the spread so a reader can see whether a
        # difference is resolvable. Reporting the best run would be cherry-picking;
        # reporting one run and calling a 28% difference a result is what this option
        # exists to stop — that mistake is already in the git history.
        reps.sort(key=lambda r: r["p99"])
        r = reps[len(reps) // 2]
        if len(reps) > 1:
            r = dict(r)
            r["p99_spread"] = f"{reps[0]['p99']:.1f}-{reps[-1]['p99']:.1f}"
            r["p90_spread"] = (f"{min(x['p90'] for x in reps):.1f}-"
                               f"{max(x['p90'] for x in reps):.1f}")
        rows.append(r)
        print(f"{r['conc']:>5}{r['rps']:>10.1f}{r['p50']:>9.2f}{r['p90']:>9.2f}"
              f"{r['p99']:>9.2f}{r['wall_p50']:>10.1f}{r['gw_cpu']:>9.1f}"
              f"{r['client_cpu_cores']:>11.2f}"
              f"{100*r['err_rate']:>7.2f}  {'yes' if r['nine_all'] else 'NO'}"
              + (f"   [{a.repeat} runs: p90 {r['p90_spread']}, p99 {r['p99_spread']}]"
                 if r.get("p99_spread") else ""))
        if not r["nine_all"]:
            fails.append(f"conc {c}: {r['nine_missing']} of {r['n']} samples did not run "
                         f"all nine stages — a shorter pipeline is being measured (R1)")
        if r["ok_n"] == 0 or r["lat_missing"] > 0.1 * r["ok_n"]:
            fails.append(f"conc {c}: no latency sample for {r['lat_missing']} of "
                         f"{r['ok_n']} successful requests. An empty latency list "
                         f"reports p99 = 0.00 and would PASS the bound silently.")
        if r["err_rate"] > 0.005:
            fails.append(f"conc {c}: error rate {100*r['err_rate']:.2f}% > 0.5% — a fast "
                         f"5xx is not throughput (R6)")
        if r["client_cpu_cores"] > 0.8 * c:
            fails.append(f"conc {c}: driver used {r['client_cpu_cores']:.2f} cores for {c} "
                         f"workers — the LOAD GENERATOR is the bottleneck, not the "
                         f"gateway (R5)")
        if r["p99"] > a.p99_bound_ms:
            over += 1
            if over >= 2:
                print(f"      (p99 over {a.p99_bound_ms} ms twice — past the knee, stopping)")
                break
        else:
            over = 0

    # TAIL ATTRIBUTION. A p99 six times p50 at 0.42 vCPU is not contention for a
    # resource — something stalls on SOME requests. Rather than guess which (I already
    # named thread churn once and was wrong), ask the slow requests directly: for the
    # worst decile by firewall tax, which stage differs from the median cohort?
    worst = max(rows, key=lambda r: r["p99"])
    ok = [r for r in worst.get("samples", []) if r.get("stages")]
    if ok and a.latency_metric == "addon":
        ok.sort(key=lambda r: r.get("addon") or 0.0)
        cut = max(1, len(ok) // 10)
        slow, mid = ok[-cut:], ok[len(ok) // 4: 3 * len(ok) // 4]
        if mid:
            print(f"\nTAIL ATTRIBUTION at concurrency {worst['conc']} "
                  f"(slowest {len(slow)} vs median {len(mid)} of {len(ok)} samples)")
            print(f"{'stage':<20}{'median ms':>11}{'tail ms':>10}{'delta':>10}")
            deltas = []
            for name in STAGES:
                m = statistics.median([r["stages"].get(name, 0.0) for r in mid])
                t = statistics.median([r["stages"].get(name, 0.0) for r in slow])
                deltas.append((t - m, name, m, t))
            for d, name, m, t in sorted(deltas, reverse=True):
                flag = "  <== dominates the tail" if d == max(x[0] for x in deltas) and d > 1 else ""
                print(f"{name:<20}{m:>11.2f}{t:>10.2f}{d:>+10.2f}{flag}")
            # Root-level timing the gateway computes for itself.
            print(f"{'-- root timing --':<20}")
            for rk in ("t_addon_pre_ms", "t_addon_post_ms", "overhead_ms"):
                rm = statistics.median([(r.get("roots") or {}).get(rk, 0.0) for r in mid])
                rt = statistics.median([(r.get("roots") or {}).get(rk, 0.0) for r in slow])
                print(f"{rk:<20}{rm:>11.2f}{rt:>10.2f}{rt-rm:>+10.2f}")

            m_add = statistics.median([r["addon"] or 0.0 for r in mid])
            t_add = statistics.median([r["addon"] or 0.0 for r in slow])
            acc = sum(d for d, _, _, _ in deltas)
            print(f"{'firewall tax total':<20}{m_add:>11.2f}{t_add:>10.2f}{t_add-m_add:>+10.2f}")
            print(f"  stage deltas account for {acc:+.2f} ms of the {t_add-m_add:+.2f} ms "
                  f"tail excess ({100*acc/(t_add-m_add) if t_add != m_add else 0:.0f}%)"
                  + ("" if abs(acc - (t_add - m_add)) < 0.5 * max(1e-9, abs(t_add - m_add))
                     else "  <== UNATTRIBUTED: the time is NOT inside any stage"))

    admissible = [r for r in rows if r["p99"] <= a.p99_bound_ms
                  and r["nine_all"] and r["err_rate"] <= 0.005]
    print()
    if fails:
        print("REFUSING TO REPORT A NUMBER:")
        for f in dict.fromkeys(fails):
            print(f"  - {f}")
        return 1
    if not admissible:
        print(f"REFUSING TO REPORT A NUMBER: no concurrency level met p99 <= "
              f"{a.p99_bound_ms} ms with all nine stages running. Lowest p99 observed: "
              f"{min(r['p99'] for r in rows):.2f} ms at concurrency "
              f"{min(rows, key=lambda r: r['p99'])['conc']}.")
        return 1

    best = max(admissible, key=lambda r: r["rps"])
    used = best["gw_cpu"] / 100.0
    if used < 0.6 * a.cpu_limit:
        print(f"REFUSING TO REPORT RPS/vCPU: gateway used {used:.2f} of its "
              f"{a.cpu_limit} vCPU limit at the knee. Something other than the gateway "
              f"binds, so the per-vCPU denominator would be wrong (R4).")
        return 1

    print(f"MAX RPS (p99 {metric} <= {a.p99_bound_ms} ms): {best['rps']:.1f} at concurrency "
          f"{best['conc']}, gateway using {used:.2f} vCPU")
    print(f"  => {best['rps']/used:.1f} RPS per vCPU consumed "
          f"({best['rps']/a.cpu_limit:.1f} per vCPU allocated)")
    print(f"  [CPU-BOUND FLOOR, not a ceiling: the in-gateway stub answers without "
          f"provider I/O, so real traffic's socket wait would let an async gateway "
          f"overlap MORE requests per core. Per-request CPU cost is right; per-core "
          f"throughput is understated.]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
