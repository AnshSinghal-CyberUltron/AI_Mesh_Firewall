"""GW35 control adapter: ``error`` joins the console vocabulary; ranking, rejection and merge.

The adapter imports nothing from Django or ``gateway_v2``, so this module loads it by path and
runs under plain ``python -m unittest`` as well as the Django runner.
"""
import importlib.util
import itertools
import random
import sys
import unittest
from pathlib import Path

_ADAPTER = Path(__file__).resolve().parents[1] / "v2_audit_adapter.py"
_SPEC = importlib.util.spec_from_file_location("v2_audit_adapter_gw35", _ADAPTER)
adapter = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = adapter
_SPEC.loader.exec_module(adapter)

_STAGES = ("auth", "rate_limit", "input_scan", "model_routing", "model_output", "output_guardrail")


def _console(actions, request_id="zs-1"):
    stages = [
        {"name": n, "action": a, "ran": a != "skip"} for n, a in zip(_STAGES, actions, strict=True)
    ]
    best = "allow"
    for action in actions:
        if adapter._RANK.get(action, -1) > adapter._RANK[best]:
            best = action
    return {
        "source": "gateway_v2",
        "zeroshield": {"action": best, "request_id": request_id},
        "pipeline_trace": {"final_action": best, "plan_version": "p", "stages": stages},
    }


def _payload(console=None, *, outcome="allow", phase="input", detail=None, request_id="zs-1"):
    body = {
        "v": 1, "request_id": request_id, "org_id": "o", "phase": phase,
        "outcome": outcome, "at_ns": 1, "detail": {"route": "chat", **(detail or {})},
    }
    if console is not None:
        body["detail"]["console"] = console
    return body


class RankAndAcceptanceTests(unittest.TestCase):
    def test_error_sits_between_redact_and_block(self):
        rank = adapter._RANK
        self.assertLess(rank["redact"], rank["error"])
        self.assertLess(rank["error"], rank["block"])

    def test_error_console_action_is_accepted(self):
        out = adapter.translate(_payload(_console(["allow"] * 4 + ["error", "skip"])))
        self.assertEqual(out.action, "error")

    def test_a_console_action_outside_the_vocabulary_is_rejected(self):
        console = _console(["allow"] * 6)
        console["zeroshield"]["action"] = "maybe"
        self.assertEqual(adapter.translate(_payload(console)), adapter.RejectedV2("action"))

    def test_no_console_outcome_outside_the_accepted_set_is_rejected(self):
        self.assertEqual(
            adapter.translate(_payload(outcome="weird")), adapter.RejectedV2("action")
        )
        for outcome in ("rejected", "shed", "allow", "block", "redact", "flag", "error"):
            self.assertIsInstance(adapter.translate(_payload(outcome=outcome)), adapter.TranslatedEvent)

    def test_rejected_and_shed_still_rank_below_every_console_action(self):
        for loose in ("rejected", "shed"):
            self.assertEqual(adapter._RANK.get(loose, -1), -1)
            row = adapter.translate(_payload(outcome=loose))
            action, _ = adapter.merge_row("allow", {"phases": []}, row)
            self.assertEqual(action, "allow")
            action, _ = adapter.merge_row(None, None, row)
            self.assertEqual(action, loose)

    def test_a_block_beats_an_error_and_an_error_beats_a_redact(self):
        block = adapter.translate(_payload(_console(["allow", "allow", "allow", "allow", "allow", "block"])))
        error = adapter.translate(_payload(_console(["allow"] * 4 + ["error", "skip"]), phase="output"))
        redact = adapter.translate(_payload(_console(["allow", "allow", "redact"] + ["allow"] * 3)))
        for first, second in itertools.permutations((block, error, redact), 2):
            action, meta = adapter.merge_row(None, None, first)
            action, meta = adapter.merge_row(action, meta, second)
            want = max((first, second), key=lambda e: adapter._RANK[e.action]).action
            self.assertEqual(action, want)


class RefusedAtAndFingerprintTests(unittest.TestCase):
    def test_refused_at_names_the_first_blocking_or_erroring_stage(self):
        row = adapter.translate(_payload(_console(["block"] + ["skip"] * 5), outcome="rejected"))
        self.assertEqual(row.metadata["refused_at"], "auth")
        row = adapter.translate(_payload(_console(["allow", "allow", "allow", "allow", "error", "skip"])))
        self.assertEqual(row.metadata["refused_at"], "model_output")
        row = adapter.translate(_payload(_console(["allow"] * 6)))
        self.assertNotIn("refused_at", row.metadata)

    def test_single_record_merge_keeps_refused_at_and_the_fingerprint(self):
        row = adapter.translate(
            _payload(_console(["block"] + ["skip"] * 5), detail={"key_fingerprint": "0123456789ab"})
        )
        _, stored = adapter.merge_row(None, None, row)
        self.assertEqual(stored["refused_at"], "auth")
        self.assertEqual(stored["key_fingerprint"], "0123456789ab")

    def test_two_phase_merge_recomputes_refused_at_from_the_winning_trace(self):
        inp = adapter.translate(_payload(_console(["allow"] * 3 + ["skip"] * 3), phase="input"))
        out = adapter.translate(
            _payload(_console(["allow"] * 4 + ["error", "skip"]), phase="output")
        )
        action, meta = adapter.merge_row(None, None, inp)
        self.assertNotIn("refused_at", meta)
        action, meta = adapter.merge_row(action, meta, out)
        self.assertEqual((action, meta["refused_at"]), ("error", "model_output"))
        action, meta = adapter.merge_row(action, meta, inp)  # a later weaker phase changes nothing
        self.assertEqual((action, meta["refused_at"]), ("error", "model_output"))

    def test_a_malformed_or_full_length_fingerprint_is_dropped(self):
        for bad in ("a" * 64, "XYZ", 12, "0123456789AB"):
            row = adapter.translate(_payload(_console(["allow"] * 6), detail={"key_fingerprint": bad}))
            self.assertNotIn("key_fingerprint", row.metadata)


class OrgMapAndPropertyTests(unittest.TestCase):
    def test_the_reserved_org_parses_in_the_map(self):
        self.assertEqual(
            adapter.parse_org_map("dev-org=zeroshield,_unauthenticated=zeroshield"),
            {"dev-org": "zeroshield", "_unauthenticated": "zeroshield"},
        )

    # Feature: gw35-console-truth, Property 6: merge order never changes the strictest action
    def test_p6_any_merge_order_yields_the_max_rank_action(self):
        rng = random.Random(0x35_06)
        names = list(adapter._RANK)
        for i in range(500):
            rows = []
            for _ in range(rng.randint(1, 4)):
                actions = [rng.choice(("allow", "skip")) for _ in range(6)]
                actions[rng.randrange(6)] = rng.choice(names)
                rows.append(adapter.translate(_payload(_console(actions))))
            rng.shuffle(rows)
            action, meta = None, None
            for row in rows:
                action, meta = adapter.merge_row(action, meta, row)
            self.assertEqual(action, max((r.action for r in rows), key=adapter._RANK.get), i)

    def test_p6_every_action_outside_the_vocabulary_is_rejected(self):
        rng = random.Random(0x35_66)
        for i in range(200):
            junk = "".join(rng.choice("abcxyz_") for _ in range(rng.randint(1, 9)))
            if junk in adapter._RANK:
                continue
            console = _console(["allow"] * 6)
            console["zeroshield"]["action"] = junk
            self.assertEqual(adapter.translate(_payload(console)), adapter.RejectedV2("action"), i)
            self.assertEqual(adapter.translate(_payload(outcome=junk)) == adapter.RejectedV2("action"),
                             junk not in adapter._ACCEPTED)


if __name__ == "__main__":
    unittest.main()
