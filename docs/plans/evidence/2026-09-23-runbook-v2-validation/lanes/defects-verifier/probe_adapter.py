"""Probe D: runbook §10.1.3 (rb.md:2062-2071) — the enforcement adapter and the divergent
injection threat sets. Real main.py adapter + real resolver; Tier-2 verdicts produced by the
REAL scanner from fake guard-model JSON (only the network client is faked). Usage: <ROOT>.
"""
import asyncio
import inspect
import json
import os
import sys

ROOT = sys.argv[1]
os.environ["ENABLE_TIER2"] = "false"
os.environ.setdefault("TIER2_PROVIDER", "bedrock")

import ai_mesh_gateway.main as M  # noqa: E402
import enforcement as E  # noqa: E402
import scanner as S  # noqa: E402

for mod in (M, E, S):
    assert mod.__file__.startswith(ROOT), (mod.__name__, mod.__file__)
print("ROOT =", ROOT)
lines, start = inspect.getsourcelines(M._scanner_kwargs_for_enforcement)
print(f"_scanner_kwargs_for_enforcement spans main.py:{start}-{start + len(lines) - 1}")

print("\n== threat-type sets ==")
print("main._PLATFORM_FLOOR_SCANNER_THREATS =", sorted(M._PLATFORM_FLOOR_SCANNER_THREATS))
print("main._TIER2_INJECTION_THREATS        =", sorted(M._TIER2_INJECTION_THREATS))
print("enforcement._INJECTION_THREAT_TYPES  =", sorted(E._INJECTION_THREAT_TYPES))
print("present in main only:", sorted(M._TIER2_INJECTION_THREATS - E._INJECTION_THREAT_TYPES),
      "| present in enforcement only:", sorted(E._INJECTION_THREAT_TYPES - M._TIER2_INJECTION_THREATS))
_src = open(E.__file__).read().splitlines()
print("enforcement.py:272-279 (the confidence/threshold gate):")
for n in range(272, 280):
    print(f"   {n}: {_src[n-1]}")


class GuardJSONClient:
    region = "probe"

    def __init__(self, payload):
        self.content = json.dumps(payload)

    async def ascan_prompt(self, **_kw):
        return {"raw": {"choices": [{"message": {"content": self.content}}]}, "tokens_in": 10, "tokens_out": 30}


def real_tier2_verdict(payload, slug):
    scn = S.InputScanner(thread_pool_size=2, config={})
    scn.tier2_enabled = True
    scn._bedrock_scanner = S.BedrockScanner(client=GuardJSONClient(payload), model="global.anthropic.claude-haiku-probe")
    return asyncio.run(scn.scan_prompt_with_tier2("Please review this paragraph for me.", org_tier2_override=True,
                                                  org_slug=slug, org_tier2_strict=True))


def main_py_pipeline(verdict, threshold=0.80):
    deg = M._is_tier2_degraded_verdict(verdict)
    sm = getattr(verdict, "scan_meta", None) or {}
    rec = (sm.get("recommended_action") if isinstance(sm, dict) else None) or ""
    guard_rec = max((rec, verdict.action or "", "allow"), key=E.action_rank) or "allow"
    kw = M._scanner_kwargs_honoring_pii_toggle(verdict, recommendation=guard_rec, pii_detection_enabled=True)
    d = E.resolve_and_enforce(**kw, org_policy_action=None, matched_rules=[], matched_policy_names=[],
                              enforcement_mode="block", tier2_degraded=deg, tier1_pii_detected=False,
                              redaction_possible=True, pii_detection_enabled=True,
                              scan_block_on_injection=True, injection_threshold=threshold)
    return guard_rec, kw, d


def finding(cat, conf, rule_id="LLM01", severity="medium"):
    return {"rule_id": rule_id, "category": cat, "severity": severity, "evidence": "x", "confidence": conf}


print("\n== (1) Tier-2 guard responses through the REAL scanner (bedrock_scanner._complete_scan escalates any")
print("       threat-category / LLM01-class / high-severity finding to block, bedrock_scanner.py:205-246,907-911) ==")
cases = [
    ("monitor rec, category obfuscation (medium)", {"findings": [finding("obfuscation", 0.6, "OBF-01", "medium")], "risk_score": 20, "recommended_action": "monitor"}),
    ("allow rec + obfuscation finding (low)", {"findings": [finding("obfuscation", 0.3, "OBF-01", "low")], "risk_score": 10, "recommended_action": "allow"}),
    ("score 0.55, no findings (risk_score flag)", {"findings": [], "risk_score": 55, "recommended_action": "allow"}),
    ("monitor rec, category data_leakage [escalated by scanner]", {"findings": [finding("data_leakage", 0.9)], "risk_score": 20, "recommended_action": "monitor"}),
    ("CONTROL monitor rec, category pii (medium, floor)", {"findings": [finding("pii", 0.8, "PII-01", "medium")], "risk_score": 20, "recommended_action": "monitor"}),
]
for i, (label, payload) in enumerate(cases):
    v = real_tier2_verdict(payload, f"probe-a{i}")
    guard_rec, kw, d = main_py_pipeline(v)
    print(f"[{label}]\n   scanner verdict: {v.action!r}/{v.threat_type!r}/{v.tier!r} conf={v.confidence} rec={v.scan_meta.get('recommended_action')!r}"
          f"\n   adapter all-None={all(x is None for x in kw.values())} -> resolve_and_enforce action={d.action!r}")

print("\n== (2) same-concept labels, identical low-confidence Tier-2 BLOCK (conf 0.30 < threshold 0.80) ==")
for cat in ("injection", "prompt_injection", "jailbreak", "goal_hijacking"):
    v = real_tier2_verdict({"findings": [finding(cat, 0.30)], "risk_score": 30, "recommended_action": "block"}, f"probe-b-{cat}")
    guard_rec, kw, d = main_py_pipeline(v)
    print(f"   {cat:<17} scanner verdict {v.action!r}/{v.threat_type!r}/conf={v.confidence} -> adapter admits="
          f"{not all(x is None for x in kw.values())} -> resolve_and_enforce {d.action!r} (blocked_by={d.blocked_by!r})")

print("\n== (3) constructed-verdict grid (ScanVerdict, tier='tier_2') through adapter + resolver ==")
print(f"   {'threat_type':<34}{'action':<9}{'adapter':<10}resolved")
for tt in ("toxicity", "data_leakage", "policy_violation", "risk_score", "sensitive_information_disclosure",
           "scanner_degraded", "user_pii_like", "jailbreak", "injection"):
    for act in ("flag", "monitor"):
        v = S.ScanVerdict(action=act, threat_type=tt, confidence=0.9, tier="tier_2")
        kw = M._scanner_kwargs_for_enforcement(v, recommendation=act)
        d = E.resolve_and_enforce(**kw, enforcement_mode="block")
        print(f"   {tt:<34}{act:<9}{'DROPPED' if all(x is None for x in kw.values()) else 'kept':<10}{d.action}")
