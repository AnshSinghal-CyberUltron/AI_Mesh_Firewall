"""GW35 property tests: seeded ``random.Random``, house idiom, no hypothesis.

Each property runs through :func:`run_property` so the negative controls can call it.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import random
from collections.abc import Callable

from gateway_v2.audit.console_schema import SchemaError, final_action, validate_console
from gateway_v2.domain import posture
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.domain.failure_class import (
    NO_ACTION,
    OPERATIONAL_CODES,
    is_operational_failure,
)
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.console_stages import ChatFacts, build_stages, stages_to_console
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.unauth_audit import UnauthAuditGate
from tests.edge import _gw35_harness as h

_DISPOSITIONS = ("allow", "flag", "redact", "block")
_POLICY = ("output_blocked", "scan_failure", "stream_key_revoked", "holdback_overflow", "odd_code")
_OPERATIONAL = tuple(sorted(OPERATIONAL_CODES - {posture.PLAN_UNAVAILABLE, "lifecycle_incomplete"}))
_PRE_DISPATCH = (
    posture.INVALID_API_KEY, posture.SHARED_STATE_UNAVAILABLE, posture.PLAN_UNAVAILABLE,
    posture.OVERLOAD_SHED, posture.RATE_LIMITED, posture.BUDGET_UNAVAILABLE, "scan_failure",
    "input_blocked", "input_unmaskable",
)


def run_property(fn: Callable[[random.Random], None], n: int, seed: int) -> None:
    rng = random.Random(seed)
    for i in range(n):
        try:
            fn(rng)
        except AssertionError as exc:
            raise AssertionError(f"iteration {i} seed {seed}: {exc}") from exc


def _facts_for_refusal(rng: random.Random, code: str) -> ChatFacts:
    ident = code not in (posture.INVALID_API_KEY, posture.SHARED_STATE_UNAVAILABLE)
    reached_budget = code in ("scan_failure", "input_blocked", "input_unmaskable")
    scanned = code in ("input_blocked", "input_unmaskable")
    return ChatFacts(
        refuse_code=code,
        identity_resolved=ident,
        plan_version="p1" if ident and code != posture.PLAN_UNAVAILABLE else "",
        admitted=reached_budget or code in (posture.RATE_LIMITED, posture.BUDGET_UNAVAILABLE),
        spent=reached_budget,
        input_action=(rng.choice(("block", "redact")) if scanned else ""),
        input_rule="r" if scanned else "",
    )


# Feature: gw35-console-truth, Property 1: refusing stage is first non-allow; later stages skip
def test_p1_a_refusal_stops_every_later_stage() -> None:
    def check(rng: random.Random) -> None:
        facts = _facts_for_refusal(rng, rng.choice(_PRE_DISPATCH))
        stages = build_stages(facts)
        assert [s.name for s in stages] == [
            "auth", "rate_limit", "input_scan", "model_routing", "model_output", "output_guardrail",
        ]
        first = next(i for i, s in enumerate(stages) if s.action not in ("allow", "skip"))
        assert all(s.action == "skip" and not s.ran for s in stages[first + 1:]), facts
        console = stages_to_console("zs-1", facts)
        assert console is not None
        validate_console(console)

    run_property(check, 1000, 0x35_01)


# Feature: gw35-console-truth, Property 2: final is error iff provider failed and no block
def test_p2_error_iff_provider_failed_and_no_block() -> None:
    def check(rng: random.Random) -> None:
        in_act = rng.choice(_DISPOSITIONS[:3])
        failure = rng.choice(("",) + _OPERATIONAL + _POLICY)
        out_act = rng.choice(("",) + _DISPOSITIONS)
        facts = ChatFacts(
            identity_resolved=True, plan_version="p1", admitted=True, spent=True,
            input_action=in_act, input_rule="r" if in_act != "allow" else "",
            dispatched=True, failure_code=failure, output_action=out_act,
            output_rule="r" if out_act not in ("", "allow") else "",
        )
        console = stages_to_console("zs-1", facts)
        assert console is not None
        final = console["zeroshield"]["action"]  # type: ignore[index]
        stages = {s.name: s for s in build_stages(facts)}
        policy = bool(failure) and not is_operational_failure(failure)
        policy_cut = policy and out_act in ("", "allow")
        blocked = out_act == "block" or policy_cut
        operational = bool(failure) and is_operational_failure(failure)
        assert (final == "error") == (operational and not blocked), (facts, final)
        assert (stages["output_guardrail"].action == "block") == bool(blocked), facts
        if failure and failure not in NO_ACTION:
            assert final != "allow", facts

    run_property(check, 1000, 0x35_02)


def _random_console(rng: random.Random) -> dict[str, object]:
    code = rng.choice(_PRE_DISPATCH)
    facts = _facts_for_refusal(rng, code)
    console = stages_to_console("zs-prop", facts)
    assert console is not None
    return console


# Feature: gw35-console-truth, Property 3: generated consoles validate; one-field mutations fail
def test_p3_valid_consoles_validate_and_mutations_fail() -> None:
    def check(rng: random.Random) -> None:
        console = _random_console(rng)
        validate_console(console)
        bad = copy.deepcopy(console)
        trace = bad["pipeline_trace"]
        stages = trace["stages"]  # type: ignore[index]
        pick = rng.choice(("name", "ran", "final", "action_unknown"))
        stage = rng.choice(stages)
        if pick == "name":
            stage["name"] = "kill_switch"
        elif pick == "ran":
            stage["ran"] = not stage["ran"]
        elif pick == "final":
            current = trace["final_action"]  # type: ignore[index]
            trace["final_action"] = "allow" if current != "allow" else "block"  # type: ignore[index]
        else:
            stage["action"] = "nope"
        try:
            validate_console(bad)
        except SchemaError:
            return
        raise AssertionError(f"mutation {pick} was accepted")

    run_property(check, 500, 0x35_03)


# Feature: gw35-console-truth, Property 5: the gate admits at most `rate` per window
def test_p5_the_gate_is_bounded_and_conserves_offers() -> None:
    def check(rng: random.Random) -> None:
        rate, window = rng.randint(1, 8), rng.choice((1.0, 5.0, 60.0))
        now = [0.0]
        gate = UnauthAuditGate(rate_per_window=rate, window_s=window, clock=lambda: now[0])
        offered, per_window = 0, {}
        for _ in range(rng.randint(1, 60)):
            now[0] += rng.random() * window / 3
            offered += 1
            if gate.admit():
                bucket = int(now[0] // window)
                per_window[bucket] = per_window.get(bucket, 0) + 1
        assert gate.accepted + gate.dropped == offered
        # A fixed window anchored at the first admit can straddle two calendar buckets, so the
        # honest bound is two windows' worth in any calendar bucket.
        assert all(count <= 2 * rate for count in per_window.values())

    run_property(check, 1000, 0x35_05)


# Feature: gw35-console-truth, Property 7: console_from_holds never raises on garbage ledgers
def test_p7_console_from_holds_never_raises() -> None:
    codes = ("",) + _PRE_DISPATCH + _OPERATIONAL + _POLICY + tuple(NO_ACTION)

    def check(rng: random.Random) -> None:
        holds = RequestHolds()
        if rng.random() < 0.7:
            holds.note_request("zs-" + str(rng.randint(0, 99)))
        if rng.random() < 0.5:
            holds.note_identity_resolved()
        if rng.random() < 0.5:
            holds.note_input(
                Decision(
                    disposition=Disposition(rng.choice(_DISPOSITIONS)), per_finding=(),
                    transformations=(), findings=(), plan_version="p",
                    deciding_rules=("r",) if rng.random() < 0.5 else (), unavailable_detectors=(),
                )
            )
        if rng.random() < 0.5:
            holds.mark_dispatched()
        holds.note_failure(rng.choice(codes))
        holds.note_output_action(rng.choice(("", "error", "weird") + _DISPOSITIONS))
        console = console_from_holds(holds, rng.choice(codes) or None)
        if console is not None:
            validate_console(console)
            actions = [s["action"] for s in console["pipeline_trace"]["stages"]]  # type: ignore[index]
            assert console["zeroshield"]["action"] == final_action(actions)  # type: ignore[index]

    run_property(check, 1000, 0x35_07)


# Feature: gw35-console-truth, Property 4: a token never reaches any output channel
def test_p4_random_tokens_never_appear_and_fingerprint_is_sha256_prefix() -> None:
    app, records, deps = h.build_app(h.ScriptedProvider(h.ok_events()))
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    rng = random.Random(0x35_04)

    async def go() -> None:
        for i in range(200):
            token = "".join(rng.choice(alphabet) for _ in range(48))
            res = await h.post(app, records, auth=f"Bearer {token}")
            dump = res.body.decode() + json.dumps([dict(r.as_mapping()) for r in res.records])
            assert token not in dump, i
            want = hashlib.sha256(token.encode()).hexdigest()[:12]
            got = {r.detail.get("key_fingerprint") for r in res.records}
            assert got <= {want}, i
        assert token not in deps["metrics_registry"].exposition()  # type: ignore[attr-defined]

    asyncio.run(go())
