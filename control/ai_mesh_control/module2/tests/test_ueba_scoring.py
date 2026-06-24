"""Unit tests for UEBA v2 pure scoring functions."""

from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from module2.ueba_metrics import hourly_counts_chronological, latest_hourly_count
from module2.ueba_scoring import (
    MIN_BASELINE_SAMPLES,
    MIN_CURRENT_EVENTS,
    apply_llm_blend,
    blend_final_score,
    graduation_progress,
    graduation_thresholds,
    is_graduated,
    risk_band_for_score,
    score_active_mode,
    score_learning_mode,
)


def _mature_baseline(**overrides):
    defaults = dict(
        avg_block_rate=0.02,
        avg_redact_rate=0.0,
        avg_requests_per_hour=10.0,
        std_requests_per_hour=2.0,
        typical_models=["gpt-4o"],
        sample_count=MIN_BASELINE_SAMPLES,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class UebaScoringTests(SimpleTestCase):
    def test_learning_high_block_rate_scores_high(self):
        metric = {
            "total": 100,
            "blocked": 80,
            "redacted": 0,
            "policy_escalations": 0,
            "threat_types": {"prompt_injection": 40},
            "hourly": {"h1": 50, "h2": 50},
            "models": {"gpt-4o"},
        }
        score, breakdown = score_learning_mode(metric)
        self.assertGreaterEqual(score, 0.5)
        self.assertEqual(breakdown["mode"], "learning")

    def test_learning_scanner_caps_band(self):
        metric = {
            "total": 100,
            "blocked": 80,
            "redacted": 0,
            "policy_escalations": 0,
            "threat_types": {"prompt_injection": 40},
            "hourly": {"h1": 50, "h2": 50},
            "models": {"gpt-4o"},
        }
        _, breakdown = score_learning_mode(metric, key_purpose="scanner")
        self.assertEqual(breakdown["band_cap"], "medium")
        self.assertTrue(breakdown.get("scanner_learning_cap"))

    def test_learning_steady_redact_not_high_alone(self):
        metric = {
            "total": 100,
            "blocked": 0,
            "redacted": 40,
            "policy_escalations": 0,
            "threat_types": {},
            "hourly": {"h1": 50, "h2": 50},
            "models": set(),
        }
        score, _ = score_learning_mode(metric)
        self.assertLess(score, 0.35)

    def test_active_baseline_block_spike_high(self):
        metric = {
            "total": 100,
            "blocked": 30,
            "redacted": 0,
            "threat_types": {},
            "hourly": {"h1": 10},
            "models": {"gpt-4o"},
        }
        score, breakdown = score_active_mode(metric, _mature_baseline())
        self.assertGreater(score, 0.5)
        self.assertIn("block_rate_deviation", breakdown.get("anomaly_flags", []))

    def test_active_small_block_deviation_low(self):
        metric = {
            "total": 100,
            "blocked": 42,
            "redacted": 0,
            "threat_types": {},
            "hourly": {"h1": 10},
            "models": {"doc-scan"},
        }
        baseline = _mature_baseline(avg_block_rate=0.40, typical_models=["doc-scan"])
        score, _ = score_active_mode(metric, baseline)
        self.assertLess(score, 0.45)

    def test_active_clean_baseline_single_block_stays_low(self):
        metric = {
            "total": 100,
            "blocked": 1,
            "redacted": 0,
            "threat_types": {},
            "hourly": {"h1": 10},
            "models": {"gpt-4o"},
        }
        baseline = _mature_baseline(avg_block_rate=0.0, typical_models=["gpt-4o"])
        score, breakdown = score_active_mode(metric, baseline)
        self.assertEqual(breakdown["block_deviation"], 0.0)
        self.assertLess(score, 0.40)

    def test_active_empty_typical_models_novelty_zero(self):
        metric = {
            "total": 100,
            "blocked": 0,
            "redacted": 0,
            "threat_types": {},
            "hourly": {"h1": 10},
            "models": {"new-model"},
        }
        baseline = _mature_baseline(typical_models=[], sample_count=0)
        _, breakdown = score_active_mode(metric, baseline)
        self.assertEqual(breakdown["model_novelty"], 0.0)
        self.assertTrue(breakdown.get("baseline_immature"))

    def test_active_block_dev_suppressed_below_min_events(self):
        metric = {
            "total": MIN_CURRENT_EVENTS - 1,
            "blocked": 5,
            "redacted": 0,
            "threat_types": {},
            "hourly": {"h1": 5},
            "models": {"gpt-4o"},
        }
        _, breakdown = score_active_mode(metric, _mature_baseline())
        self.assertEqual(breakdown["block_deviation"], 0.0)

    def test_scanner_graduation_thresholds(self):
        key = SimpleNamespace(
            key_purpose="scanner",
            ueba_graduation_requests=None,
            ueba_graduation_days=None,
        )
        org_settings = SimpleNamespace(
            scanner_graduation_min_requests=10,
            scanner_graduation_min_days=1.0,
            graduation_min_requests=50,
            graduation_min_days=7.0,
        )
        req, days = graduation_thresholds(key, org_settings)
        self.assertEqual(req, 10)
        self.assertEqual(days, 1.0)

    def test_scanner_graduates_at_10_requests(self):
        key = SimpleNamespace(
            key_purpose="scanner",
            ueba_graduation_requests=None,
            ueba_graduation_days=None,
            ueba_lifetime_request_count=10,
            created_at=timezone.now(),
        )
        org_settings = SimpleNamespace(
            scanner_graduation_min_requests=10,
            scanner_graduation_min_days=1.0,
            graduation_min_requests=50,
            graduation_min_days=7.0,
        )
        self.assertTrue(is_graduated(key, org_settings))

    def test_graduation_49_requests_still_learning(self):
        key = SimpleNamespace(
            key_purpose="production",
            ueba_graduation_requests=None,
            ueba_graduation_days=None,
            ueba_lifetime_request_count=49,
            created_at=timezone.now(),
        )
        org_settings = SimpleNamespace(
            graduation_min_requests=50,
            graduation_min_days=7.0,
            scanner_graduation_min_requests=10,
            scanner_graduation_min_days=1.0,
        )
        self.assertFalse(is_graduated(key, org_settings))

    def test_graduation_50_requests_active(self):
        key = SimpleNamespace(
            key_purpose="production",
            ueba_graduation_requests=None,
            ueba_graduation_days=None,
            ueba_lifetime_request_count=50,
            created_at=timezone.now(),
        )
        org_settings = SimpleNamespace(
            graduation_min_requests=50,
            graduation_min_days=7.0,
            scanner_graduation_min_requests=10,
            scanner_graduation_min_days=1.0,
        )
        self.assertTrue(is_graduated(key, org_settings))

    def test_llm_blend_explicit_delta(self):
        traditional, llm_score, final, weighted = apply_llm_blend(0.6, 0.2)
        self.assertAlmostEqual(weighted, 0.09, places=2)
        self.assertAlmostEqual(final, 0.69, places=2)
        self.assertAlmostEqual(llm_score, 0.8, places=2)

    def test_llm_blend_legacy_equivalent(self):
        traditional, _, final, _ = apply_llm_blend(0.6, 0.2)
        legacy = blend_final_score(0.6, 0.8)
        self.assertAlmostEqual(final, legacy, places=3)

    def test_graduation_progress_pct(self):
        key = SimpleNamespace(
            key_purpose="production",
            ueba_graduation_requests=None,
            ueba_graduation_days=None,
            ueba_lifetime_request_count=25,
            created_at=timezone.now() - timezone.timedelta(days=3),
        )
        org_settings = SimpleNamespace(
            graduation_min_requests=50,
            graduation_min_days=7.0,
            scanner_graduation_min_requests=10,
            scanner_graduation_min_days=1.0,
        )
        progress = graduation_progress(key, org_settings)
        self.assertEqual(progress["requests"], 25)
        self.assertGreater(progress["pct_complete"], 40)

    def test_hourly_bucket_uses_chronological_latest(self):
        counts = hourly_counts_chronological(
            {
                "2024-06-02T12:00:00": 2,
                "2024-06-01T10:00:00": 5,
                "2024-06-02T10:00:00": 100,
            }
        )
        self.assertEqual(counts, [5, 100, 2])
        self.assertEqual(latest_hourly_count({"2024-06-02T12:00:00": 2, "2024-06-02T10:00:00": 100}), 2)

    def test_learning_velocity_spike_uses_latest_chronological_hour(self):
        metric = {
            "total": 120,
            "blocked": 0,
            "redacted": 0,
            "policy_escalations": 0,
            "threat_types": {},
            "hourly": {
                "2024-06-01T10:00:00": 5,
                "2024-06-02T12:00:00": 2,
                "2024-06-02T10:00:00": 100,
            },
            "models": set(),
        }
        _, breakdown = score_learning_mode(metric)
        self.assertLess(breakdown["velocity_spike"], 2.5)
        self.assertNotIn("velocity_spike", breakdown.get("anomaly_flags", []))

    def test_no_snapshot_risk_band_from_score(self):
        org_settings = SimpleNamespace(high_risk_threshold=0.7, medium_risk_threshold=0.35)
        score = 0.75
        self.assertEqual(
            risk_band_for_score(score, org_settings.high_risk_threshold, org_settings.medium_risk_threshold),
            "high",
        )
