"""Regression: Tier-2 Bedrock circuit-breaker state machine.

Pins the security-critical availability transitions (was zero-tested). In
particular the OPEN + strict=True → Tier2UnavailableStrict behavior is the
mechanism the chat path uses to fail CLOSED for a strict org on a Bedrock
outage (see finding #27 — the MCP path's failure to honor it is the bug; the
breaker itself is correct). Stdlib-only → runs standalone.
"""

import time
import unittest

import bedrock_tier2_breaker as b


class Tier2BreakerStateMachineTests(unittest.TestCase):
    def _fresh(self):
        return type(b.BREAKER)()

    def test_fresh_is_closed_and_allows(self):
        br = self._fresh()
        self.assertEqual(br.state_of("o", "m"), b.STATE_CLOSED)
        self.assertTrue(br.allow("o", "m", strict=True))

    def test_repeated_failures_open_the_breaker(self):
        br = self._fresh()
        for _ in range(b.MIN_CALLS + 2):
            br.record_result("o", "m", failure=True)
        self.assertEqual(br.state_of("o", "m"), b.STATE_OPEN)

    def test_open_strict_raises_tier2_unavailable_strict(self):
        br = self._fresh()
        for _ in range(b.MIN_CALLS + 2):
            br.record_result("o", "m", failure=True)
        with self.assertRaises(b.Tier2UnavailableStrict):
            br.allow("o", "m", strict=True)

    def test_open_non_strict_passes_through_false(self):
        br = self._fresh()
        for _ in range(b.MIN_CALLS + 2):
            br.record_result("o", "m", failure=True)
        self.assertFalse(br.allow("o", "m", strict=False))

    def test_cooldown_transitions_open_to_half_open(self):
        br = self._fresh()
        for _ in range(b.MIN_CALLS + 2):
            br.record_result("o", "m", failure=True)
        br._states[("o", "m")].opened_at = time.time() - b.COOLDOWN_SECONDS - 5
        self.assertTrue(br.allow("o", "m", strict=True))
        self.assertEqual(br.state_of("o", "m"), b.STATE_HALF_OPEN)

    def test_half_open_success_closes(self):
        br = self._fresh()
        for _ in range(b.MIN_CALLS + 2):
            br.record_result("o", "m", failure=True)
        br._states[("o", "m")].opened_at = time.time() - b.COOLDOWN_SECONDS - 5
        br.allow("o", "m", strict=True)  # -> HALF_OPEN
        br.record_result("o", "m", failure=False)
        self.assertEqual(br.state_of("o", "m"), b.STATE_CLOSED)


if __name__ == "__main__":
    unittest.main()
