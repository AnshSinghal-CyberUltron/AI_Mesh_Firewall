#!/usr/bin/env python3
"""
Synthetic Bedrock latency probe for Tier-2 and adjudicator call sites.

Usage (from repo root, with AWS creds + model access):
  python gateway/scripts/bedrock_latency_probe.py --runs 50

Logs p50/p90/p99 per call_site to stdout and structured JSON lines.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))

from ai_mesh_gateway.bedrock_client import default_bedrock_client  # noqa: E402
from ai_mesh_gateway.bedrock_scanner import BedrockScanner, build_tier2_system_prompt  # noqa: E402
from ai_mesh_gateway.platform_models import (  # noqa: E402
    default_adjudicator_model,
    default_tier2_scanner_model,
)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def _summarize(label: str, samples: list[float]) -> dict:
    if not samples:
        return {"call_site": label, "count": 0}
    return {
        "call_site": label,
        "count": len(samples),
        "p50_ms": round(_percentile(samples, 50) * 1000, 1),
        "p90_ms": round(_percentile(samples, 90) * 1000, 1),
        "p99_ms": round(_percentile(samples, 99) * 1000, 1),
        "mean_ms": round(statistics.mean(samples) * 1000, 1),
        "min_ms": round(min(samples) * 1000, 1),
        "max_ms": round(max(samples) * 1000, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bedrock latency probe")
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument("--skip-tier2", action="store_true")
    parser.add_argument("--skip-adjudicator", action="store_true")
    args = parser.parse_args()

    client = default_bedrock_client()
    tier2_model = default_tier2_scanner_model()
    adj_model = default_adjudicator_model()
    tier2_samples: list[float] = []
    adj_samples: list[float] = []

    print(f"Region={client.region} tier2_model={tier2_model} adjudicator_model={adj_model}")

    if not args.skip_tier2:
        scanner = BedrockScanner(client=client, model=tier2_model)
        prompt = "Hello, summarize quarterly revenue trends."
        for i in range(args.runs):
            start = time.time()
            scanner.scan(prompt)
            elapsed = time.time() - start
            tier2_samples.append(elapsed)
            print(json.dumps({"event": "probe_sample", "call_site": "tier2_scan", "run": i + 1, "elapsed_s": round(elapsed, 3)}))

    if not args.skip_adjudicator:
        system_text = build_tier2_system_prompt()[:500]
        user_text = json.dumps(
            {
                "preferred_model": "auto",
                "candidate_models": [{"model_name": "org-haiku", "score": 0.9}],
            }
        )
        for i in range(args.runs):
            start = time.time()
            client.converse(
                model=adj_model,
                system_text=system_text,
                user_text=user_text,
                max_tokens=200,
                call_site="adjudicator",
            )
            elapsed = time.time() - start
            adj_samples.append(elapsed)
            print(json.dumps({"event": "probe_sample", "call_site": "adjudicator", "run": i + 1, "elapsed_s": round(elapsed, 3)}))

    summaries = []
    if tier2_samples:
        summaries.append(_summarize("tier2_scan", tier2_samples))
    if adj_samples:
        summaries.append(_summarize("adjudicator", adj_samples))

    for summary in summaries:
        print(json.dumps({"event": "probe_summary", **summary}))

    tier2_p50 = summaries[0]["p50_ms"] if tier2_samples else 0
    ok = not tier2_samples or tier2_p50 < 500
    if tier2_samples:
        print(f"Tier-2 p50 target <500ms: {'PASS' if ok else 'FAIL'} (p50={tier2_p50}ms)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
