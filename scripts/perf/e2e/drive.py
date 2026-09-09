#!/usr/bin/env python3
"""Nine-stage E2E driver — tasks 1.3/1.4/1.5 of hot-path-latency-20ms.

Drives POST /v1/chat/completions over real HTTP against the Docker stack with
**unique prompts**, parses SSE for streaming runs, and reports the firewall tax
per stage.

The measured quantity is `t_addon_pre_ms + t_addon_post_ms` — the gateway's own
work, excluding `model_output` (the upstream). Both come from `pipeline_trace`,
which since `honest-stream-latency-metric` is re-anchored on the request epoch
rather than `provider_start_ts`; before that fix `addon` collapsed onto provider
TTFT and every streaming number was the provider's.

REFUSAL CONDITIONS (fail loudly rather than report a comfortable number):
  * upstream emitted no tokens                      -> R7.7
  * a stage reports 0 ms without an explicit skip   -> R7.5
  * prompts were not unique                         -> R7.2
  * reconciliation residual exceeds epsilon         -> R7.3

Usage:
    python drive.py --n 30 --key $PERF_API_KEY --model perf-stub-model
    python drive.py --n 30 --stream --prompt-chars 4096
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid

STAGES = ("auth", "rate_limit", "policy", "input_scan", "kill_switch",
          "model_routing", "model_input", "model_output", "output_guardrail")

_WORDS = ("the deployment manifest records each artifact digest so reviewers can "
          "confirm provenance before a release is promoted between environments "
          "while operators watch error budgets and latency percentiles closely ").split()


def make_prompt(nchars: int, nonce: str) -> str:
    """Unique prompt of ~nchars. The nonce defeats any cache and lets the run
    prove uniqueness (R7.2)."""
    out = [f"[{nonce}]"]
    i = 0
    while len(" ".join(out)) < nchars:
        out.append(_WORDS[i % len(_WORDS)])
        i += 1
    return " ".join(out)[:nchars]


def _pct(v: list[float], q: float) -> float:
    if not v:
        return float("nan")
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def post(url: str, key: str, body: dict, timeout: float):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read())
    return (time.perf_counter() - t0) * 1000, payload


def post_stream(url: str, key: str, body: dict, timeout: float):
    """Drive a streaming completion; return (wall_ms, ttft_ms, tokens, trace)."""
    req = urllib.request.Request(
        url, data=json.dumps({**body, "stream": True}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.perf_counter()
    ttft = None
    tokens = 0
    trace = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = b""
        for raw in r:
            buf += raw
            while b"\n\n" in buf:
                frame, buf = buf.split(b"\n\n", 1)
                if not frame.startswith(b"data: "):
                    continue
                p = frame[6:].decode(errors="replace").strip()
                if p == "[DONE]":
                    continue
                try:
                    d = json.loads(p)
                except json.JSONDecodeError:
                    continue
                # The terminal frame carries the trace.
                if isinstance(d, dict) and d.get("pipeline_trace"):
                    trace = d["pipeline_trace"]
                for ch in d.get("choices") or []:
                    if (ch.get("delta") or {}).get("content"):
                        if ttft is None:
                            ttft = (time.perf_counter() - t0) * 1000
                        tokens += 1
    return (time.perf_counter() - t0) * 1000, ttft, tokens, trace


def stages_of(trace: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in trace.get("stages") or []:
        if isinstance(s, dict) and s.get("name"):
            out[s["name"]] = float(s.get("latency_ms") or 0.0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8300/v1/chat/completions")
    ap.add_argument("--key", required=True)
    ap.add_argument("--model", default="perf-stub-model")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--prompt-chars", type=int, default=4096)
    ap.add_argument("--stream", action="store_true")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--epsilon-ms", type=float, default=25.0,
                    help="reconciliation tolerance |total - (sum stages + overhead)|")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args()

    seen: set[str] = set()
    rows: list[dict] = []
    fails: list[str] = []

    for i in range(a.n + a.warmup):
        nonce = uuid.uuid4().hex[:12]
        prompt = make_prompt(a.prompt_chars, nonce)
        if nonce in seen:
            fails.append("prompt nonce repeated — prompts are not unique (R7.2)")
        seen.add(nonce)
        body = {"model": a.model, "messages": [{"role": "user", "content": prompt}]}
        try:
            if a.stream:
                wall, ttft, tokens, trace = post_stream(a.url, a.key, body, a.timeout)
                payload = None
            else:
                body["stream"] = False
                wall, payload = post(a.url, a.key, body, a.timeout)
                trace = payload.get("pipeline_trace")
                tokens = int(((payload.get("usage") or {}).get("completion_tokens")) or 0)
                ttft = None
        except urllib.error.HTTPError as e:
            fails.append(f"HTTP {e.code}: {e.read()[:200]!r}")
            continue
        except Exception as e:  # noqa: BLE001
            fails.append(f"{type(e).__name__}: {e}")
            continue

        if i < a.warmup:
            continue
        if not trace:
            fails.append("no pipeline_trace in response")
            continue

        st = stages_of(trace)
        pre = float(trace.get("t_addon_pre_ms") or 0.0)
        post_ms = float(trace.get("t_addon_post_ms") or 0.0)
        rows.append({
            "wall_ms": wall, "ttft_ms": ttft, "tokens": tokens,
            "total_ms": float(trace.get("total_latency_ms") or 0.0),
            "sum_ms": float(trace.get("stage_latency_sum_ms") or 0.0),
            "overhead_ms": float(trace.get("overhead_ms") or 0.0),
            "addon_pre_ms": pre, "addon_post_ms": post_ms, "addon_ms": pre + post_ms,
            "t2_ms": float(trace.get("t_t2_ms") or 0.0),
            "stages": st,
        })

    if not rows:
        print("FATAL: no successful samples", file=sys.stderr)
        for f in fails[:5]:
            print(f"  {f}", file=sys.stderr)
        return 2

    # ── Refusals ────────────────────────────────────────────────────────────
    if all(r["tokens"] == 0 for r in rows):
        fails.append("upstream emitted ZERO tokens across every sample — the output "
                     "path never ran; this is a six-stage measurement (R7.7)")
    if any(r["tokens"] == 1 for r in rows):
        fails.append("upstream emitted a ONE-token reply — GATEWAY_LOADTEST_STUB_DURATION_S "
                     "is probably 0 (its default), which makes output_guardrail ~0 ms (R7.7)")
    for name in ("input_scan", "output_guardrail"):
        vals = [r["stages"].get(name, 0.0) for r in rows]
        if vals and max(vals) <= 0.0:
            fails.append(f"stage {name!r} reported 0 ms on every sample — scans did not run, "
                         f"so this is not a nine-stage result (R7.5)")

    # THE ATTRIBUTION INVARIANT. The firewall tax is defined by subtracting
    # `model_output` from the total. If the upstream demonstrably produced tokens
    # but `model_output` is 0, there is nothing to subtract and the provider's
    # generation time silently lands in `addon` — the tax then reads as seconds
    # and looks like a catastrophic gateway regression that is not real.
    #
    # This is the same CLASS of defect as `addon = TTFT` (fixed by
    # honest-stream-latency-metric for the anchor, but `model_output` attribution
    # on the streaming path is a separate hole). Found on the first streaming run:
    # 200 tokens emitted, model_output 0.00 ms, "tax" 2638 ms.
    #
    # An earlier version of this driver did NOT have this check and happily
    # printed that 2638 ms as a firewall tax with "All honesty checks passed".
    emitted = [r for r in rows if r["tokens"] > 0]
    if emitted:
        mo = [r["stages"].get("model_output", 0.0) for r in emitted]
        if max(mo) <= 0.0:
            fails.append(
                f"upstream emitted tokens (p50 {_pct([float(r['tokens']) for r in emitted], .5):.0f}) "
                f"but stage 'model_output' is 0 ms on every sample. The firewall tax is "
                f"total - model_output, so provider time is being attributed to the gateway "
                f"and the reported tax ({_pct([r['addon_ms'] for r in rows], .5):.1f} ms p50) "
                f"is NOT the gateway's. Refusing to report it.")
    # RECONCILIATION — additive only when stages are actually sequential.
    #
    # Non-stream: stages run one after another, so total == sum(stages) + overhead.
    #
    # Stream: they do NOT. `output_guardrail` scans each chunk WHILE the provider is
    # still generating, so its time is CONCURRENT with `model_output`. Summing them
    # double-counts and the residual is meaningless — measured 407 ms against a
    # 25 ms epsilon on a run whose stage values were individually correct.
    # Applying the additive check to streams would reject good data.
    #
    # The honest streaming quantity is the ADDED WALL CLOCK: what the caller waited
    # beyond the provider's own generation. Overlapped guard time is reported
    # separately as `guard_accum` because it is work done, not latency added.
    if a.stream:
        added = [r["wall_ms"] - r["stages"].get("model_output", 0.0) for r in rows]
        print(f"\n{'streaming-specific':<22}{'p50':>10}{'p90':>10}{'p99':>10}")
        print(f"{'ADDED WALL CLOCK':<22}{_pct(added,.5):>10.2f}{_pct(added,.9):>10.2f}"
              f"{_pct(added,.99):>10.2f}   <- wall - model_output")
        print(f"{'guard_accum (overlap)':<22}{_pct([r['addon_post_ms'] for r in rows],.5):>10.2f}"
              f"{_pct([r['addon_post_ms'] for r in rows],.9):>10.2f}"
              f"{_pct([r['addon_post_ms'] for r in rows],.99):>10.2f}   <- concurrent, NOT added latency")
        if _pct(added, 0.5) < 0:
            fails.append("wall < model_output — provider time exceeds the whole request; "
                         "attribution is broken")
    else:
        resid = [abs(r["total_ms"] - (r["sum_ms"] + r["overhead_ms"])) for r in rows]
        if _pct(resid, 0.5) > a.epsilon_ms:
            fails.append(f"reconciliation residual p50 {_pct(resid,0.5):.2f} ms exceeds "
                         f"epsilon {a.epsilon_ms} ms (R7.3)")

    # ── Report ──────────────────────────────────────────────────────────────
    mode = "STREAM" if a.stream else "NON-STREAM"
    print(f"\n=== {mode} · n={len(rows)} · prompt={a.prompt_chars} chars · "
          f"unique prompts={len(seen)} ===")
    print(f"{'metric':<22}{'p50':>10}{'p90':>10}{'p99':>10}")
    for label, kf in (("wall", "wall_ms"), ("total (trace)", "total_ms"),
                      ("FIREWALL TAX", "addon_ms"), ("  addon_pre", "addon_pre_ms"),
                      ("  addon_post", "addon_post_ms"), ("  tier2", "t2_ms"),
                      ("overhead", "overhead_ms")):
        v = [r[kf] for r in rows if r[kf] is not None]
        print(f"{label:<22}{_pct(v,.5):>10.2f}{_pct(v,.9):>10.2f}{_pct(v,.99):>10.2f}")

    print(f"\n{'stage':<22}{'p50 ms':>10}{'share%':>9}")
    tot = _pct([r["total_ms"] for r in rows], .5) or 1.0
    for name in STAGES:
        v = [r["stages"].get(name, 0.0) for r in rows]
        p50 = _pct(v, .5)
        print(f"{name:<22}{p50:>10.2f}{100*p50/tot:>8.1f}%")

    print(f"\ntokens/sample p50: {_pct([float(r['tokens']) for r in rows], .5):.0f}")
    if not a.stream:
        print(f"reconciliation residual p50: {_pct(resid,.5):.3f} ms (epsilon {a.epsilon_ms})")

    if a.json_out:
        with open(a.json_out, "w") as fh:
            json.dump({"mode": mode, "n": len(rows), "prompt_chars": a.prompt_chars,
                       "rows": rows, "failures": fails}, fh, indent=2)
        print(f"wrote {a.json_out}")

    if fails:
        print("\n*** RUN REFUSED ***", file=sys.stderr)
        for f in dict.fromkeys(fails):
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nAll honesty checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
