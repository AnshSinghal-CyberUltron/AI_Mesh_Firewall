"""The two tenant plans of the prototype (opposite postures) + a quota-test tenant.

org-a ENFORCE: injection(PG2)->BLOCK (FAIL_CLOSED), secrets->BLOCK, PII->REDACT,
               output secrets/PII->REDACT; injection heuristics are SIGNAL (MONITOR).
org-b:         injection(PG2)->FLAG (FAIL_OPEN), PII/secrets->MONITOR, output->MONITOR.
Both select every detector, so both run the FULL profile.
"""

from __future__ import annotations

from typing import Any

FAIL_CLOSED = "fail_closed"
FAIL_OPEN = "fail_open"


def _r(rid: str, cat: str, dets: list[str], mode: str, action: str, prio: int, scope: str,
       posture: str, threshold: float | None = None) -> dict[str, Any]:
    return {"rule_id": rid, "category": cat, "detectors": dets, "mode": mode, "action": action,
            "priority": prio, "scope": scope, "on_unavailable": {"kind": posture},
            "threshold": threshold}


def org_a(version: str = "a-1", org_id: str = "org-a") -> dict[str, Any]:
    return {
        "org_id": org_id,
        "version": version,
        "streaming_mode": "incremental",
        "rules": [
            _r("A.inj.sem", "injection", ["injection.pg2"], "enforce", "block", 10, "input",
               FAIL_CLOSED, 0.5),
            _r("A.secret.in", "secret", ["secret.*"], "enforce", "block", 20, "input", FAIL_CLOSED),
            _r("A.pii.in", "pii", ["pii.*"], "enforce", "redact", 30, "input", FAIL_CLOSED),
            _r("A.inj.heur", "injection", ["injection.heuristic"], "monitor", "block", 90, "input",
               FAIL_OPEN),
            _r("A.secret.out", "secret", ["secret.*"], "enforce", "redact", 20, "output", FAIL_CLOSED),
            _r("A.pii.out", "pii", ["pii.*"], "enforce", "redact", 30, "output", FAIL_CLOSED),
        ],
    }


def org_b(version: str = "b-1", org_id: str = "org-b") -> dict[str, Any]:
    return {
        "org_id": org_id,
        "version": version,
        "streaming_mode": "incremental",
        "rules": [
            _r("B.inj.sem", "injection", ["injection.pg2"], "enforce", "flag", 10, "input",
               FAIL_OPEN, 0.5),
            _r("B.secret.in", "secret", ["secret.*"], "monitor", "block", 20, "input", FAIL_OPEN),
            _r("B.pii.in", "pii", ["pii.*"], "monitor", "redact", 30, "input", FAIL_OPEN),
            _r("B.inj.heur", "injection", ["injection.heuristic"], "monitor", "block", 90, "input",
               FAIL_OPEN),
            _r("B.secret.out", "secret", ["secret.*"], "monitor", "redact", 20, "output", FAIL_OPEN),
            _r("B.pii.out", "pii", ["pii.*"], "monitor", "redact", 30, "output", FAIL_OPEN),
        ],
    }


def tenants() -> dict[str, dict[str, Any]]:
    """org -> plan doc. org-q shares org-a's policy; it exists for quota tests."""
    return {"org-a": org_a(), "org-b": org_b(), "org-q": org_a("q-1", "org-q")}
