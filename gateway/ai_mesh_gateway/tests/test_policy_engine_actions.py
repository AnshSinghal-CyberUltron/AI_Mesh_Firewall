import unittest

from ai_mesh_gateway.policy_engine import evaluate


class PolicyEngineActionParityTests(unittest.TestCase):
    def test_rewrite_action_outranks_monitor_in_local_policy_engine(self):
        compiled_policies = [
            {
                "policy": {
                    "id": 1,
                    "code": "OUT-REWRITE",
                    "name": "Output Rewrite",
                    "severity": "medium",
                    "category": "content_safety",
                },
                "rules": [
                    {
                        "id": 10,
                        "name": "Monitor suspicious phrasing",
                        "description": "Lower-severity monitor rule.",
                        "rule_type": "keywords",
                        "action": "monitor",
                        "condition": {"field": "response", "keywords": ["fabricated citation"]},
                    },
                    {
                        "id": 11,
                        "name": "Rewrite fabricated citations",
                        "description": "Rewrite hallucination-like claims instead of blocking.",
                        "rule_type": "keywords",
                        "action": "rewrite",
                        "condition": {"field": "response", "keywords": ["fabricated citation"]},
                    }
                ],
            }
        ]

        result = evaluate(
            prompt="user prompt",
            response_text="This answer contains a fabricated citation that must be corrected.",
            compiled_policies=compiled_policies,
        )

        self.assertEqual(result.action, "rewrite")
        self.assertIn("Rewrite fabricated citations", result.matched_rule_names)


if __name__ == "__main__":
    unittest.main()
