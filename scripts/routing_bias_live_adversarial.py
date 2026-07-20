#!/usr/bin/env python3
"""Live adversarial suite: weight extremes × sensitivity must diversify routing.

Requires a running gateway with differentiated catalog + ROUTING_ADJUDICATOR_ALWAYS.
Env:
  GATEWAY_URL   default http://127.0.0.1:8300
  GATEWAY_KEY   Bearer key (required)
  ORG_SLUG      default zeroshield
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY = os.getenv("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
KEY = os.getenv("GATEWAY_KEY", "").strip()
ORG = os.getenv("ORG_SLUG", "zeroshield").strip()


PROMPTS = {
    "benign": "Write a two-line haiku about clouds.",
    "compliance_ish": "Summarize best practices for handling patient records under HIPAA.",
    # Avoid ultra-short prompts that occasionally trip content_filter under load.
    "latency": "Respond briefly with exactly: OK — ready.",
}

WEIGHT_EXTREMES = {
    "cost": {"risk": 0, "cost": 1, "latency": 0, "priority": 0},
    "risk": {"risk": 1, "cost": 0, "latency": 0, "priority": 0},
    "latency": {"risk": 0, "cost": 0, "latency": 1, "priority": 0},
    "priority": {"risk": 0, "cost": 0, "latency": 0, "priority": 1},
}

SENSITIVITY = ["public", "internal", "confidential", "restricted"]


def chat(prompt: str, weights: dict, sensitivity: str, *, retries: int = 2) -> dict:
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "routing_preferences": {
            "enable_routing": True,
            "data_sensitivity": sensitivity,
            # Prefer top-level *_weight keys (gateway canonical) + nested alias.
            "risk_weight": weights.get("risk", 0),
            "cost_weight": weights.get("cost", 0),
            "latency_weight": weights.get("latency", 0),
            "priority_weight": weights.get("priority", 0),
            "weights": weights,
        },
    }
    last: dict = {"status": 0, "body": {}}
    for attempt in range(max(1, retries)):
        req = urllib.request.Request(
            f"{GATEWAY}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {KEY}",
                "Content-Type": "application/json",
                "X-Org-Slug": ORG,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode())
                return {"status": resp.status, "body": payload}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw}
            last = {"status": exc.code, "body": payload}
            # Retry transient upstream/content blips; never retry hard compliance 403.
            code = str(payload.get("code") or "")
            if exc.code == 403 and "compliance" in code:
                return last
            if attempt + 1 < retries and exc.code in (400, 429, 502, 503):
                continue
            return last
        except Exception as exc:  # noqa: BLE001 — live harness resilience
            last = {"status": 0, "body": {"error": str(exc)}}
            if attempt + 1 < retries:
                continue
            return last
    return last


def extract_routing(body: dict) -> dict:
    zs = body.get("zeroshield") or {}
    routing = zs.get("routing") if isinstance(zs.get("routing"), dict) else {}
    if not routing and isinstance(zs, dict):
        routing = {
            "selected_model": zs.get("selected_model"),
            "decision_source": zs.get("decision_source"),
            "decision_factors": zs.get("decision_factors"),
        }
    return routing or {}


def main() -> int:
    if not KEY:
        print("GATEWAY_KEY required", file=sys.stderr)
        return 2

    results = []
    winners_by_weight = {}
    failures = []

    for wname, weights in WEIGHT_EXTREMES.items():
        for pname, prompt in PROMPTS.items():
            r = chat(prompt, weights, "public")
            routing = extract_routing(r.get("body") or {})
            selected = routing.get("selected_model") or routing.get("routed_model") or ""
            source = routing.get("decision_source") or ""
            row = {
                "case": f"weight={wname}/prompt={pname}",
                "status": r["status"],
                "selected": selected,
                "decision_source": source,
                "sensitivity_fallback": routing.get("sensitivity_fallback"),
                "score_tie": routing.get("score_tie"),
                "factors": routing.get("decision_factors") or [],
            }
            results.append(row)
            if r["status"] >= 400:
                failures.append(f"{row['case']} HTTP {r['status']}")
                continue
            winners_by_weight.setdefault(wname, set()).add(selected)
            factors = [str(f) for f in (routing.get("decision_factors") or [])]
            weight_extreme = any("weight_extreme" in f for f in factors)
            single = any("single_candidate" in f for f in factors) or int(
                routing.get("candidate_count") or 0
            ) <= 1
            if (
                source == "weighted_fastpath"
                and os.getenv("ROUTING_ADJUDICATOR_ALWAYS", "true").lower()
                in ("1", "true", "yes")
                and not weight_extreme
                and not single
            ):
                failures.append(
                    f"{row['case']} unexpected weighted_fastpath "
                    "(expected adjudicator or weight-extreme / single-candidate skip)"
                )

    # Sensitivity ladder must not 403
    for sens in SENSITIVITY:
        r = chat(PROMPTS["benign"], WEIGHT_EXTREMES["cost"], sens)
        routing = extract_routing(r.get("body") or {})
        row = {
            "case": f"sensitivity={sens}",
            "status": r["status"],
            "selected": routing.get("selected_model") or routing.get("routed_model") or "",
            "decision_source": routing.get("decision_source") or "",
            "sensitivity_fallback": routing.get("sensitivity_fallback"),
            "factors": routing.get("decision_factors") or [],
            "error": (r.get("body") or {}).get("code") or (r.get("body") or {}).get("error"),
        }
        results.append(row)
        if r["status"] == 403 and str(row["error"]).find("compliance_routing_unsatisfiable") >= 0:
            failures.append(f"sensitivity={sens} still 403 compliance_routing_unsatisfiable")
        if r["status"] >= 500:
            failures.append(f"sensitivity={sens} HTTP {r['status']}")

    # Weight extremes should not all collapse to one winner across cost vs risk
    cost_w = winners_by_weight.get("cost") or set()
    risk_w = winners_by_weight.get("risk") or set()
    if cost_w and risk_w and cost_w == risk_w and len(cost_w) == 1:
        failures.append(
            f"cost and risk extremes both only selected {cost_w} — catalog may not be differentiated"
        )
    elif cost_w and risk_w and cost_w.isdisjoint(risk_w):
        pass  # diversified — acceptance met
    elif cost_w and risk_w and not (cost_w <= risk_w and risk_w <= cost_w):
        pass  # partial overlap but not identical sets — OK

    out = {
        "ok": not failures,
        "failures": failures,
        "winners_by_weight": {k: sorted(v) for k, v in winners_by_weight.items()},
        "results": results,
    }
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
