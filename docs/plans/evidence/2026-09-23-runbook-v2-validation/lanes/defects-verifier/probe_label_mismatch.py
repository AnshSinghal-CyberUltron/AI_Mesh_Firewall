"""Probe C: runbook §10.1.2 (rb.md:2059-2061) — the degraded-label mismatch.

Drives the REAL scanner Tier-2 input path (InputScanner.scan_prompt_with_tier2 ->
BedrockScanner.ascan -> _scan_client_error / _complete_scan -> scanner degraded branch)
with ONLY the network client replaced by a fake (raises, or returns unparseable/empty
content). The produced verdict is then fed to the REAL main.py helpers and the REAL
resolver with the exact kwargs main.py:9416-9456 builds. Usage: <ROOT>.
"""
import asyncio
import inspect
import os
import sys

ROOT = sys.argv[1]
os.environ["ENABLE_TIER2"] = "false"      # don't build a real network client in __init__
os.environ.setdefault("TIER2_PROVIDER", "bedrock")

import ai_mesh_gateway.main as M  # noqa: E402
import enforcement as E  # noqa: E402
import scanner as S  # noqa: E402

for mod in (M, E, S):
    assert mod.__file__.startswith(ROOT), (mod.__name__, mod.__file__)
print("ROOT =", ROOT, "| TIER2_PROVIDER =", os.environ["TIER2_PROVIDER"])
print("main._is_tier2_degraded_verdict source (main.py:%d):" % inspect.getsourcelines(M._is_tier2_degraded_verdict)[1])
print("".join("    " + l for l in inspect.getsource(M._is_tier2_degraded_verdict).splitlines(True)))


class RaisingClient:
    region = "probe"

    def __init__(self, exc):
        self.exc = exc

    async def ascan_prompt(self, **_kw):
        raise self.exc


class ContentClient:
    region = "probe"

    def __init__(self, content):
        self.content = content

    async def ascan_prompt(self, **_kw):
        return {"raw": {"choices": [{"message": {"content": self.content}}]}, "tokens_in": 10, "tokens_out": 3}


def make_scanner(client, fail_closed=False):
    scn = S.InputScanner(thread_pool_size=2, config={"tier2_input_fail_closed": fail_closed})
    scn.tier2_enabled = True
    scn._bedrock_scanner = S.BedrockScanner(client=client, model="global.anthropic.claude-haiku-probe")
    return scn


def main_py_pipeline(verdict, *, tier1_pii_detected=False, override_degraded=None):
    """Exactly what main.py:9306-9456 does with a scanner verdict (org enforcement_mode=block,
    no matched org policy, PII toggle ON, default prompt_injection_threshold)."""
    deg = M._is_tier2_degraded_verdict(verdict) if override_degraded is None else override_degraded
    scan_meta = getattr(verdict, "scan_meta", None) or {}
    rec_candidate = (scan_meta.get("recommended_action") if isinstance(scan_meta, dict) else None) or ""
    guard_rec = max((rec_candidate, verdict.action or "", "allow"), key=E.action_rank) or "allow"
    kw = M._scanner_kwargs_honoring_pii_toggle(verdict, recommendation=guard_rec, pii_detection_enabled=True)
    d = E.resolve_and_enforce(
        **kw, org_policy_action=None, matched_rules=[], matched_policy_names=[],
        enforcement_mode="block", tier2_degraded=deg, tier1_pii_detected=tier1_pii_detected,
        redaction_possible=True, pii_detection_enabled=True, scan_block_on_injection=True,
        injection_threshold=0.80,
    )
    return deg, guard_rec, kw, d


try:
    from botocore.exceptions import ClientError
    throttle = ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "Converse")
except Exception:  # pragma: no cover
    throttle = None

SCENARIOS = [
    ("client_error: ConnectionError", RaisingClient(ConnectionError("simulated: endpoint unreachable"))),
    ("client_error: TimeoutError", RaisingClient(TimeoutError("simulated: read timeout"))),
]
if throttle is not None:
    SCENARIOS.append(("client_error: botocore ThrottlingException", RaisingClient(throttle)))
SCENARIOS += [
    ("unparseable guard output", ContentClient("The analysis could not be completed for this request.")),
    ("empty guard output", ContentClient("")),
]
PROMPT = "Summarise the attached quarterly planning notes in three bullet points."

print("\n== real scanner degraded verdicts -> main.py helpers -> resolver ==")
for i, (label, client) in enumerate(SCENARIOS):
    scn = make_scanner(client)
    v = asyncio.run(scn.scan_prompt_with_tier2(PROMPT, org_tier2_override=True, org_slug=f"probe-{i}",
                                               org_tier2_strict=True, request_id="probe"))
    deg, guard_rec, kw, d = main_py_pipeline(v)
    print(f"[{label}]")
    print(f"   verdict: action={v.action!r} threat_type={v.threat_type!r} tier={v.tier!r} reason_code={v.reason_code!r} "
          f"conf={v.confidence} scan_meta.recommended_action={v.scan_meta.get('recommended_action')!r} "
          f"scan_meta.decision_reason={v.scan_meta.get('decision_reason')!r}")
    print(f"   main._is_tier2_degraded_verdict -> {deg}")
    print(f"   _guard_rec (main.py:9420) -> {guard_rec!r}; adapter kwargs all None -> {all(x is None for x in kw.values())}")
    print(f"   resolve_and_enforce (as main.py:9440-9456) -> action={d.action!r} degraded={d.degraded}")
    _, _, _, d2 = main_py_pipeline(v, override_degraded=True)
    _, _, _, d3 = main_py_pipeline(v, override_degraded=True, tier1_pii_detected=True)
    print(f"   counterfactual tier2_degraded=True -> {d2.action!r} degraded={d2.degraded}; "
          f"+tier1_pii_detected=True -> {d3.action!r}")

print("\n== tier2_input_fail_closed=True variant (env GATEWAY_TIER2_INPUT_FAIL_CLOSED; default false) ==")
scn = make_scanner(RaisingClient(ConnectionError("simulated")), fail_closed=True)
v = asyncio.run(scn.scan_prompt_with_tier2(PROMPT, org_tier2_override=True, org_slug="probe-fc", org_tier2_strict=True))
deg, guard_rec, kw, d = main_py_pipeline(v)
print(f"   verdict {v.action!r}/{v.threat_type!r}/{v.reason_code!r} -> degraded? {deg} -> resolved {d.action!r}")

print("\n== what _is_tier2_degraded_verdict DOES accept (constructed verdicts) ==")
for kw_ in ({"threat_type": "bedrock_degraded"}, {"threat_type": "scanner_degraded", "reason_code": "degraded_client_error"},
            {"threat_type": "scanner_degraded", "reason_code": "bedrock_degraded"},
            {"threat_type": "scanner_degraded", "reason_code": "client_error"}):
    vv = S.ScanVerdict(action="flag", tier="tier_2", **kw_)
    print(f"   ScanVerdict(tier='tier_2', {kw_}) -> {M._is_tier2_degraded_verdict(vv)}")

print("\n== sustained outage: circuit breaker (defaults MIN_CALLS=5, FAILURE_THRESHOLD=0.5) ==")
for strict in (True, False):
    scn = make_scanner(RaisingClient(ConnectionError("simulated")))
    outs = []
    for n in range(1, 8):
        try:
            v = asyncio.run(scn.scan_prompt_with_tier2(PROMPT, org_tier2_override=True,
                                                       org_slug=f"probe-breaker-{strict}", org_tier2_strict=strict))
            _, _, _, d = main_py_pipeline(v)
            outs.append(f"#{n}:{v.threat_type or 'tier1'}->{d.action}")
        except Exception as exc:  # Tier2UnavailableStrict -> main.py:9262-9300 returns HTTP 503
            outs.append(f"#{n}:{type(exc).__name__}")
    print(f"   org_tier2_strict={strict}: " + "  ".join(outs))
