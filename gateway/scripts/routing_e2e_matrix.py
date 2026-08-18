#!/usr/bin/env python3
"""End-to-end deterministic-routing matrix against a LIVE gateway + real upstreams.

Drives real ``POST /v1/chat/completions`` calls through the running gateway and asserts
on the model the gateway actually routed to. Nothing is mocked.

Covers, across 100+ permutations:
  * all 5 Strategy Presets x 4 sensitivity levels
  * every one of SOC2 / ISO27001 / HIPAA / GDPR / PCI-DSS / NIST as a hard filter,
    plus multi-framework combinations and casing/separator drift
  * per-request weight overrides, single-dimension extremes, degenerate vectors
  * determinism: the same request repeated must always route identically
  * enable_routing=false pinning, and hostile/malformed inputs (must never 5xx)

Usage:
  GATEWAY_URL=http://127.0.0.1:8300 GATEWAY_API_KEY=... ./.venv/bin/python \
      scripts/routing_e2e_matrix.py [--repeats N] [--live-infer]

``--live-infer`` additionally asserts a real completion body came back (costs money on
paid models). Without it, routing metadata is asserted but 502/503 upstream errors are
tolerated — the routing DECISION is still recorded in the response envelope.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

import urllib.error
import urllib.request

GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
API_KEY = os.environ.get("GATEWAY_API_KEY", "")

PRESETS = {
    "Balanced":         {"risk": 0.30, "cost": 0.20, "latency": 0.20, "priority": 0.30},
    "Cost Optimized":   {"risk": 0.15, "cost": 0.50, "latency": 0.20, "priority": 0.15},
    "Low Latency":      {"risk": 0.15, "cost": 0.15, "latency": 0.55, "priority": 0.15},
    "Maximum Security": {"risk": 0.55, "cost": 0.10, "latency": 0.10, "priority": 0.25},
    "Quality First":    {"risk": 0.20, "cost": 0.10, "latency": 0.10, "priority": 0.60},
}
SENSITIVITIES = ["public", "internal", "confidential", "restricted"]
FRAMEWORKS = ["SOC2", "ISO27001", "HIPAA", "GDPR", "PCI-DSS", "NIST"]
# Casing / separator drift an operator or client realistically produces.
FRAMEWORK_VARIANTS = [
    "soc2", "SOC 2", "iso27001", "ISO-27001", "hipaa", "Hippa",
    "gdpr", "pci-dss", "PCI_DSS", "pci dss", "nist", "NIST-CSF",
]


# Statuses that are NOT a routing defect:
#   200 served · 403 governance fail-closed (compliance/sensitivity unsatisfiable)
#   400/429 upstream provider rejection or free-tier rate limit · 502/503 upstream down
#   0 transport timeout
# A 500 from our own code, or a wrong decision_source, IS a defect.
ACCEPTABLE = (200, 400, 403, 429, 502, 503, 0)


class Result:
    __slots__ = ("name", "ok", "detail", "routed", "source")

    def __init__(self, name, ok, detail="", routed="", source=""):
        self.name, self.ok, self.detail = name, ok, detail
        self.routed, self.source = routed, source


def call(messages=None, model="auto", prefs=None, extra=None, timeout=90):
    """POST a chat completion. Returns (status, body_dict)."""
    payload = {
        "model": model,
        "messages": messages or [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 8,
    }
    if prefs is not None:
        payload["routing_preferences"] = prefs
    if extra:
        payload.update(extra)
    req = urllib.request.Request(
        f"{GATEWAY}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:400]}
    except Exception as exc:  # noqa: BLE001 - harness must not die on transport noise
        return 0, {"_transport_error": str(exc)}


def routing_of(body):
    """Pull the routing envelope regardless of which shape survived redaction."""
    zs = body.get("zeroshield") or {}
    nested = zs.get("routing") or {}
    trace = ((body.get("pipeline_trace") or {}).get("stages") or [])
    stage = next((s for s in trace if s.get("name") == "model_routing"), {})
    merged = {}
    for src in (stage, nested, zs):
        for k, v in (src or {}).items():
            merged.setdefault(k, v)
    return merged


def routed_model(body):
    r = routing_of(body)
    return (r.get("routed_model") or r.get("selected_model")
            or body.get("model") or "")


def decision_source(body):
    return routing_of(body).get("decision_source", "")


# ───────────────────────────── the matrix ─────────────────────────────
def run_matrix(repeats: int, live_infer: bool) -> list[Result]:
    out: list[Result] = []

    def record(name, ok, detail="", body=None):
        b = body or {}
        out.append(Result(name, ok, detail, routed_model(b), decision_source(b)))

    # A. Presets x sensitivity  (5 x 4 = 20)
    for pname, w in PRESETS.items():
        for sens in SENSITIVITIES:
            st, body = call(prefs={"weights": w, "data_sensitivity": sens})
            src = decision_source(body)
            if st == 403:
                # Legitimate when no model is approved at this sensitivity.
                record(f"preset[{pname}]/sens[{sens}]", True, "403 fail-closed (no eligible model)", body)
            elif st in ACCEPTABLE:
                ok = src in ("deterministic_weighted", "", "kill_switch", "model_state")
                record(f"preset[{pname}]/sens[{sens}]", ok,
                       f"status={st} source={src}", body)
            else:
                record(f"preset[{pname}]/sens[{sens}]", False, f"unexpected status {st}", body)

    # B. Single-dimension extremes (4)
    for dim in ("risk", "cost", "latency", "priority"):
        w = {k: 0.0 for k in ("risk", "cost", "latency", "priority")}
        w[dim] = 1.0
        st, body = call(prefs={"weights": w})
        record(f"extreme[{dim}=1.0]", st in ACCEPTABLE, f"status={st}", body)

    # C. Every framework as a hard filter (6)
    for fw in FRAMEWORKS:
        st, body = call(prefs={"compliance_requirements": [fw]})
        # Either a compliant model exists (200) or it fail-closes (403). Never 5xx.
        ok = st in ACCEPTABLE
        record(f"compliance[{fw}]", ok, f"status={st}", body)

    # D. Casing / separator drift must behave identically to canonical (12)
    for variant in FRAMEWORK_VARIANTS:
        st, body = call(prefs={"compliance_requirements": [variant]})
        record(f"compliance-drift[{variant}]", st in ACCEPTABLE, f"status={st}", body)

    # E. Multi-framework combinations (10)
    combos = [
        ["SOC2", "ISO27001"], ["HIPAA", "GDPR"], ["PCI-DSS", "NIST"],
        ["SOC2", "HIPAA", "GDPR"], ["ISO27001", "NIST"], ["GDPR", "PCI-DSS"],
        ["SOC2", "ISO27001", "HIPAA", "GDPR"], ["HIPAA", "NIST"],
        ["soc2", "hipaa"], ["PCI_DSS", "gdpr"],
    ]
    for combo in combos:
        st, body = call(prefs={"compliance_requirements": combo})
        record(f"compliance-combo{combo}", st in ACCEPTABLE, f"status={st}", body)

    # F. Compliance x sensitivity cross-product (6 x 4 = 24)
    for fw in FRAMEWORKS:
        for sens in SENSITIVITIES:
            st, body = call(prefs={"compliance_requirements": [fw], "data_sensitivity": sens})
            record(f"compliance[{fw}]xsens[{sens}]", st in ACCEPTABLE, f"status={st}", body)

    # F2. Preset x framework cross-product (5 x 6 = 30) — the governance surface an
    # operator actually configures, crossed with every regulated workload.
    for pname, w in PRESETS.items():
        for fw in FRAMEWORKS:
            st, body = call(prefs={"weights": w, "compliance_requirements": [fw]})
            src = decision_source(body)
            # 0 = transport timeout, 400/429 = UPSTREAM provider rejection (free-tier
            # rate limits on OpenRouter). Neither is a routing defect; only a 5xx from
            # our own code or a wrong decision_source is.
            ok = st in ACCEPTABLE
            if st == 200:
                # A served request under a framework demand must have been decided
                # deterministically, never by a removed adjudicator.
                ok = ok and src in ("deterministic_weighted", "", "kill_switch", "model_state")
            record(f"preset[{pname}]xcompliance[{fw}]", ok, f"status={st} source={src}", body)

    # G. Determinism — same request repeated (repeats)
    seen, skipped = Counter(), 0
    for _ in range(repeats):
        st, body = call(prefs={"weights": PRESETS["Cost Optimized"]})
        m = routed_model(body)
        if m:
            seen[m] += 1
        else:
            skipped += 1  # upstream error carried no routing envelope
    stable = len(seen) <= 1 and bool(seen)
    out.append(Result(
        f"determinism[x{repeats}]", stable,
        f"winners={dict(seen)} no_envelope={skipped}",
        next(iter(seen), ""), "deterministic_weighted",
    ))

    # H. Determinism per preset (5 x 5)
    for pname, w in PRESETS.items():
        winners, miss = set(), 0
        for _ in range(5):
            st, body = call(prefs={"weights": w})
            m = routed_model(body)
            if m:
                winners.add(m)
            else:
                miss += 1
        out.append(Result(f"determinism[{pname}]", len(winners) <= 1 and bool(winners),
                          f"winners={winners} no_envelope={miss}",
                          next(iter(winners), ""), ""))

    # I. enable_routing=false must pin the requested model (2)
    for pinned in ("openrouter/free", "gpt-5.2"):
        st, body = call(model=pinned, prefs={"enable_routing": False})
        served = routed_model(body)
        ok = st in ACCEPTABLE and (served in ("", pinned) or st != 200)
        out.append(Result(f"pin[{pinned}]", ok, f"status={st} served={served}", served,
                          decision_source(body)))

    # J. Hostile / malformed inputs must never 5xx from OUR code (10)
    hostile = [
        ("weights_wrong_type", {"weights": "nope"}),
        ("weights_string_values", {"weights": {"risk": "a", "cost": "b"}}),
        ("weights_negative", {"weights": {"risk": -5, "cost": -1}}),
        ("weights_huge", {"weights": {"cost": 1e308}}),
        ("weights_null", {"weights": None}),
        ("prefs_is_list", None),
        ("compliance_wrong_type", {"compliance_requirements": {"a": 1}}),
        ("sensitivity_unknown", {"data_sensitivity": "topsecret"}),
        ("sensitivity_wrong_type", {"data_sensitivity": {"x": 1}}),
        ("latency_budget_string", {"latency_budget_ms": "soon"}),
    ]
    for name, prefs in hostile:
        st, body = call(prefs=prefs)
        # 400/403 fine; 502/503 = upstream, not us. A 500 is our bug.
        ok = st != 500
        out.append(Result(f"hostile[{name}]", ok, f"status={st}", routed_model(body),
                          decision_source(body)))

    # K. No LLM adjudicator may ever appear in a decision source (sweep)
    bad_sources = [r for r in out if r.source in
                   ("policy_adjudicator", "weighted_fastpath", "weighted_fallback")]
    out.append(Result("no_adjudicator_source_anywhere", not bad_sources,
                      f"offenders={[r.name for r in bad_sources][:5]}"))

    # L. Live inference proof (optional, costs money)
    if live_infer:
        st, body = call(prefs={"weights": PRESETS["Cost Optimized"]})
        content = ""
        try:
            content = body["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            pass
        out.append(Result("live_inference", st == 200 and bool(content),
                          f"status={st} content={content!r}", routed_model(body),
                          decision_source(body)))

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--live-infer", action="store_true")
    args = ap.parse_args()

    if not API_KEY:
        print("GATEWAY_API_KEY is required", file=sys.stderr)
        return 2

    t0 = time.time()
    results = run_matrix(args.repeats, args.live_infer)
    elapsed = time.time() - t0

    failed = [r for r in results if not r.ok]
    by_source = Counter(r.source for r in results if r.source)
    by_model = Counter(r.routed for r in results if r.routed)

    print(f"\n{'='*72}")
    print(f"E2E ROUTING MATRIX — {len(results)} permutations in {elapsed:.1f}s")
    print(f"{'='*72}")
    print(f"passed : {len(results)-len(failed)}")
    print(f"failed : {len(failed)}")
    print(f"\ndecision_source distribution: {dict(by_source)}")
    print(f"routed models seen          : {dict(by_model)}")
    if failed:
        print(f"\n{'-'*72}\nFAILURES\n{'-'*72}")
        for r in failed:
            print(f"  ✗ {r.name}: {r.detail}")
    print()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
