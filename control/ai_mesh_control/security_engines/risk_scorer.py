"""
Risk Scoring Algorithm
Calculates overall risk score (0-100) based on all threat detections
"""

import logging
from dataclasses import dataclass

LOG = logging.getLogger("security_engines.risk_scorer")


@dataclass
class RiskScore:
    overall_score: int  # 0-100
    risk_level: str  # "none", "low", "medium", "high", "critical"
    contributing_factors: list[dict]
    recommended_action: str


class RiskScorer:
    """
    Calculate risk scores from threat detection results
    """

    def __init__(self):
        # Severity to score mapping
        self.severity_scores = {"critical": 50, "high": 30, "medium": 18, "low": 8, "none": 0}

        # Threat category weights
        self.category_weights = {
            "owasp_llm": 1.2,
            "owasp_mcp": 1.0,
            "owasp_agentic": 1.1,
            "pii": 1.3,
            "behavioral": 0.8,
            # legacy llm_guard key and new 'bedrock' signals should be weighted similarly
            "llm_guard": 1.15,
            "bedrock": 1.15,
        }

    def calculate_risk(self, detection_results: dict) -> RiskScore:
        """
        Calculate overall risk score

        Args:
            detection_results: {
                "owasp_llm": {scan_results, summary},
                "owasp_mcp": {scan_results, summary},
                "owasp_agentic": {scan_results, summary},
                "pii": PIIResult,
                "behavioral": {...}
            }

        Returns:
            RiskScore object
        """
        total_score = 0
        contributing_factors = []

        # Process OWASP LLM results
        if "owasp_llm" in detection_results:
            llm_data = detection_results["owasp_llm"]
            llm_score = self._calculate_category_score(llm_data, "owasp_llm")
            total_score += llm_score

            if llm_score > 0:
                contributing_factors.append(
                    {
                        "category": "OWASP LLM",
                        "score": llm_score,
                        "threats": llm_data.get("summary", {}).get("total_threats", 0),
                    }
                )

        # Process OWASP MCP results
        if "owasp_mcp" in detection_results:
            mcp_data = detection_results["owasp_mcp"]
            mcp_score = self._calculate_category_score(mcp_data, "owasp_mcp")
            total_score += mcp_score

            if mcp_score > 0:
                contributing_factors.append(
                    {
                        "category": "OWASP MCP",
                        "score": mcp_score,
                        "threats": mcp_data.get("summary", {}).get("total_threats", 0),
                    }
                )

        # Process OWASP Agentic results
        if "owasp_agentic" in detection_results:
            agentic_data = detection_results["owasp_agentic"]
            agentic_score = self._calculate_category_score(agentic_data, "owasp_agentic")
            total_score += agentic_score

            if agentic_score > 0:
                contributing_factors.append(
                    {
                        "category": "OWASP Agentic AI",
                        "score": agentic_score,
                        "threats": agentic_data.get("summary", {}).get("total_threats", 0),
                    }
                )

        # Process PII results
        if "pii" in detection_results:
            pii_data = detection_results["pii"]
            pii_score = self._calculate_pii_score(pii_data)
            total_score += pii_score

            if pii_score > 0:
                contributing_factors.append(
                    {"category": "PII/PHI/PCI", "score": pii_score, "severity": pii_data.severity}
                )

        # Process LLM-Guard / Bedrock results (accept either key for backward compatibility)
        guard_key = (
            "llm_guard" if "llm_guard" in detection_results else ("bedrock" if "bedrock" in detection_results else None)
        )
        if guard_key:
            guard_data = detection_results[guard_key]
            guard_score = self._calculate_llm_guard_score(guard_data)
            total_score += guard_score

            if guard_score > 0:
                contributing_factors.append(
                    {
                        "category": "LLM-Guard/Bedrock ML",
                        "score": guard_score,
                        "valid": guard_data.get("is_valid", True),
                    }
                )

        # Cap at 100
        total_score = min(100, total_score)

        # Determine risk level
        risk_level = self._score_to_level(total_score)

        # Determine recommended action
        recommended_action = self._determine_action(total_score, contributing_factors)

        LOG.info(
            "RiskScorer RESULT: overall_score=%d, risk_level=%s, action=%s, factors=%s",
            total_score,
            risk_level,
            recommended_action,
            [f"{f['category']}:{f['score']}" for f in contributing_factors],
        )

        return RiskScore(
            overall_score=total_score,
            risk_level=risk_level,
            contributing_factors=contributing_factors,
            recommended_action=recommended_action,
        )

    def _calculate_category_score(self, category_data: dict, category_name: str) -> int:
        """Calculate score for a threat category"""
        if not category_data or "summary" not in category_data:
            return 0

        summary = category_data["summary"]
        severity = summary.get("overall_severity", "none")

        base_score = self.severity_scores.get(severity, 0)
        weight = self.category_weights.get(category_name, 1.0)

        # Apply weight
        weighted_score = int(base_score * weight)

        # Bonus for multiple threats (2+ distinct threat types = strong signal)
        threat_count = summary.get("total_threats", 0)
        if threat_count >= 2:
            weighted_score += 10

        return weighted_score

    def _calculate_pii_score(self, pii_data) -> int:
        """Calculate score for PII detection"""
        if not pii_data or not pii_data.detected:
            return 0

        severity = pii_data.severity
        base_score = self.severity_scores.get(severity, 0)

        # PII is weighted higher
        weighted_score = int(base_score * self.category_weights["pii"])

        # Count entity types
        total_entities = sum(len(entities) for entities in pii_data.entities.values())

        # Bonus for multiple PII types
        if total_entities > 3:
            weighted_score += 10

        return weighted_score

    def _calculate_llm_guard_score(self, guard_data: dict) -> int:
        if not guard_data or guard_data.get("degraded"):
            return 0

        raw_score = guard_data.get("score", 0.0)
        is_valid = guard_data.get("is_valid", True)

        if is_valid and raw_score < 0.2:
            return 0

        weight = self.category_weights.get("llm_guard", 1.0)

        if not is_valid:
            base_score = self.severity_scores["high"]
            severity_label = "high(invalid)"
        elif raw_score >= 0.8:
            base_score = self.severity_scores["critical"]
            severity_label = "critical"
        elif raw_score >= 0.5:
            base_score = self.severity_scores["high"]
            severity_label = "high"
        elif raw_score >= 0.3:
            base_score = self.severity_scores["medium"]
            severity_label = "medium"
        else:
            base_score = self.severity_scores["low"]
            severity_label = "low"

        weighted_score = int(base_score * weight)
        LOG.debug(
            "RiskScorer: bedrock/llm_guard score=%.2f -> weighted_score=%d (weight=%.2f, base=%s/%d)",
            raw_score,
            weighted_score,
            weight,
            severity_label,
            base_score,
        )
        return weighted_score

    def _score_to_level(self, score: int) -> str:
        """Convert numeric score to risk level."""
        if score >= 85:
            return "critical"
        elif score >= 70:
            return "high"
        elif score >= 50:
            return "medium"
        elif score >= 25:
            return "low"
        else:
            return "none"

    def _determine_action(self, score: int, factors: list[dict]) -> str:
        """Determine recommended action based on score and factors.

        PII-aware thresholds: when PII is detected and no significant
        non-PII security threat exists, prefer *redact* so the prompt is
        sanitised and forwarded instead of being hard-blocked.

          redact            : PII present AND non-PII score < 70 AND no agentic threat
          block_immediately : score >= 85
          block_and_alert   : score >= 70  OR  agentic threat detected
          warn_and_log      : score >= 50
          monitor           : score >= 25
          allow             : score < 25
        """
        has_pii = any(f["category"] == "PII/PHI/PCI" for f in factors)
        has_agentic = any(f["category"] == "OWASP Agentic AI" for f in factors)

        if has_pii:
            pii_score = sum(f.get("score", 0) for f in factors if f["category"] == "PII/PHI/PCI")
            non_pii_score = score - pii_score
            if non_pii_score < 70 and not has_agentic:
                return "redact"

        if score >= 85:
            return "block_immediately"
        elif score >= 70 or has_agentic:
            return "block_and_alert"
        elif score >= 50:
            return "warn_and_log"
        elif score >= 25:
            return "monitor"
        else:
            return "allow"

    def calculate_user_risk_profile(self, historical_scores: list[int], current_score: int) -> dict:
        """
        Calculate user's risk profile over time

        Args:
            historical_scores: Past risk scores for this user
            current_score: Current risk score

        Returns:
            User risk profile
        """
        if not historical_scores:
            return {"average_score": current_score, "trend": "new_user", "risk_profile": "unknown"}

        avg_score = sum(historical_scores) / len(historical_scores)

        # Calculate trend
        if len(historical_scores) >= 3:
            recent_avg = sum(historical_scores[-3:]) / 3
            if recent_avg > avg_score * 1.5:
                trend = "escalating"
            elif recent_avg < avg_score * 0.7:
                trend = "improving"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        # Determine profile
        if avg_score >= 70:
            risk_profile = "high_risk_user"
        elif avg_score >= 40:
            risk_profile = "moderate_risk_user"
        else:
            risk_profile = "low_risk_user"

        return {
            "average_score": int(avg_score),
            "current_score": current_score,
            "trend": trend,
            "risk_profile": risk_profile,
            "total_incidents": len(historical_scores),
        }


# ============================================
# TESTING
# ============================================

if __name__ == "__main__":
    scorer = RiskScorer()

    # Test 1: High risk scenario
    print("=" * 60)
    print("TEST 1: High Risk Scenario")
    print("=" * 60)

    detection_results = {
        "owasp_llm": {"summary": {"total_threats": 3, "critical_threats": 2, "overall_severity": "critical"}},
        "pii": type(
            "obj",
            (object,),
            {
                "detected": True,
                "severity": "critical",
                "entities": {"PCI": ["CREDIT_CARD", "CREDIT_CARD"]},
                "category": "PCI",
            },
        )(),
    }

    risk = scorer.calculate_risk(detection_results)

    print(f"Overall Score: {risk.overall_score}/100")
    print(f"Risk Level: {risk.risk_level}")
    print(f"Recommended Action: {risk.recommended_action}")
    print("\nContributing Factors:")
    for factor in risk.contributing_factors:
        print(f"  - {factor['category']}: {factor['score']} points")

    # Test 2: Medium risk scenario
    print("\n" + "=" * 60)
    print("TEST 2: Medium Risk Scenario")
    print("=" * 60)

    detection_results = {
        "owasp_mcp": {"summary": {"total_threats": 1, "critical_threats": 0, "overall_severity": "medium"}}
    }

    risk = scorer.calculate_risk(detection_results)

    print(f"Overall Score: {risk.overall_score}/100")
    print(f"Risk Level: {risk.risk_level}")
    print(f"Recommended Action: {risk.recommended_action}")

    # Test 3: User risk profile
    print("\n" + "=" * 60)
    print("TEST 3: User Risk Profile")
    print("=" * 60)

    historical_scores = [20, 25, 30, 35, 60, 75, 80]
    current_score = 85

    profile = scorer.calculate_user_risk_profile(historical_scores, current_score)

    print(f"Average Score: {profile['average_score']}")
    print(f"Current Score: {profile['current_score']}")
    print(f"Trend: {profile['trend']}")
    print(f"Risk Profile: {profile['risk_profile']}")
    print(f"Total Incidents: {profile['total_incidents']}")

    print("\n✅ Risk Scoring Testing Complete!")
