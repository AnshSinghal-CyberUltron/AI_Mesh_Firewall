"""Tier-2 input-scan integrity after the routing adjudicator was removed.

This module used to cover the wall-clock OVERLAP between the Tier-2 input scan and the
Bedrock routing adjudicator (asyncio.gather + a prefetched-route accept/discard gate).
Routing is now fully deterministic — there is no second awaitable to overlap — so that
machinery (`_should_overlap_input_t2_and_routing`, `_maybe_overlap_t2_and_routing`,
`_accept_prefetched_route`, `_routing_candidate_ids`, `_PREFETCH_RISK_DELTA`) is gone.

What remains here is the guard for the hazard that removal created:

    The Tier-2 scan coroutine was CREATED at one site and awaited ONLY inside the
    overlap helper's `overlap=False` branch. Deleting the helper without re-awaiting it
    would silently skip Tier-2 input scanning entirely — a fail-OPEN on the security
    path that surfaces as nothing louder than a RuntimeWarning.

These tests pin that the scan is still awaited, still fail-closes under the strict
breaker, and still short-circuits on a Tier-1 block.
"""
import inspect
import unittest

import ai_mesh_gateway.main as gw


class Tier2StillRunsTests(unittest.TestCase):
    """B-H1: proxy_chat must still consume the Tier-2 scan on the force_sync path."""

    def test_proxy_chat_awaits_tier2_scan(self):
        src = inspect.getsource(gw.proxy_chat)
        self.assertIn(
            "await INPUT_SCANNER.scan_prompt_with_tier2(",
            src,
            "Tier-2 input scan is no longer awaited — input scanning silently disabled",
        )

    def test_tier2_scan_is_not_left_as_an_unawaited_coroutine(self):
        """A bare `_t2_scan_coro = ...` with no await is the exact fail-open shape."""
        src = inspect.getsource(gw.proxy_chat)
        self.assertNotIn(
            "_t2_scan_coro =",
            src,
            "Tier-2 scan coroutine is assigned but may never be awaited",
        )

    def test_strict_tier2_unavailable_still_fails_closed(self):
        """The plain await must still propagate Tier2UnavailableStrict to the handler.

        gather(return_exceptions=False) used to guarantee this; a plain await has the
        same semantics, and the 503 handler below depends on it.
        """
        src = inspect.getsource(gw.proxy_chat)
        self.assertIn("_is_tier2_unavailable_strict", src)
        self.assertIn("tier2_unavailable", src)

    def test_tier1_block_still_short_circuits_tier2(self):
        from ai_mesh_gateway.scanner import InputScanner

        src = inspect.getsource(InputScanner.scan_prompt_with_tier2)
        self.assertIn('if tier1.action == "block":', src)
        self.assertIn("return tier1", src)


class OverlapMachineryIsGoneTests(unittest.TestCase):
    """The prefetch/overlap surface must not come back by accident."""

    def test_overlap_helpers_are_removed(self):
        for name in (
            "_should_overlap_input_t2_and_routing",
            "_maybe_overlap_t2_and_routing",
            "_accept_prefetched_route",
            "_routing_candidate_ids",
            "_PREFETCH_RISK_DELTA",
        ):
            self.assertFalse(hasattr(gw, name), f"{name} should have been deleted")

    def test_routing_is_selected_synchronously_before_the_model_stamp(self):
        src = inspect.getsource(gw.proxy_chat)
        select_at = src.find("LLM_ROUTER.select_model(")
        self.assertGreater(select_at, 0, "proxy_chat must call the deterministic selector")
        stamp_at = src.find('body["model"] = requested_model', select_at)
        self.assertGreater(stamp_at, select_at, "model stamp must follow selection")

    def test_isolation_filter_is_applied_exactly_once(self):
        """The duplicate call inside the deleted prefetch must not linger."""
        src = inspect.getsource(gw.proxy_chat)
        self.assertEqual(
            src.count("await _drop_isolated_or_killed_candidates("),
            1,
            "isolation/kill-switch filter should run exactly once per request",
        )


if __name__ == "__main__":
    unittest.main()
