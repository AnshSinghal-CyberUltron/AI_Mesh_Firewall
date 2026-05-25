"""
Compliance framework requirements and validation for ZeroShield.

Defines the mapping between compliance frameworks (SOC2, HIPAA, etc.)
and the firewall configuration flags they require. Used by the config
view to return warnings and by the daily compliance report task.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.models import FirewallConfig

logger = logging.getLogger(__name__)

LOG_LEVEL_SEVERITY: dict[str, int] = {
    "minimal": 0,
    "standard": 1,
    "detailed": 2,
    "verbose": 3,
}

COMPLIANCE_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "SOC2": {
        "audit_logging_enabled": True,
        "log_level_minimum": "detailed",
        "pii_detection_enabled": True,
        "alerting_enabled": True,
    },
    "HIPAA": {
        "audit_logging_enabled": True,
        "pii_detection_enabled": True,
        "content_filtering_enabled": True,
        "response_filtering_enabled": True,
        "log_level_minimum": "detailed",
    },
    "PCI-DSS": {
        "audit_logging_enabled": True,
        "content_filtering_enabled": True,
        "pii_detection_enabled": True,
        "log_level_minimum": "detailed",
        "rate_limit_enabled": True,
    },
    "ISO27001": {
        "audit_logging_enabled": True,
        "threat_intel_enabled": True,
        "alerting_enabled": True,
        "log_level_minimum": "standard",
    },
    "GDPR": {
        "pii_detection_enabled": True,
        "content_filtering_enabled": True,
        "response_filtering_enabled": True,
        "audit_logging_enabled": True,
    },
    "NIST": {
        "audit_logging_enabled": True,
        "jailbreak_detection_enabled": True,
        "threat_intel_enabled": True,
        "rate_limit_enabled": True,
        "log_level_minimum": "standard",
    },
}


def validate_compliance_requirements(
    config: "FirewallConfig",
) -> list[dict[str, str]]:
    """
    Check if the current FirewallConfig satisfies all selected compliance
    frameworks' requirements.

    Returns a list of violation dicts:
        [
            {
                "framework": "SOC2",
                "field": "audit_logging_enabled",
                "required": "True",
                "actual": "False",
                "message": "SOC2 requires audit_logging_enabled to be True",
            },
            ...
        ]

    An empty list means full compliance.
    """
    frameworks = config.compliance_frameworks or []
    violations: list[dict[str, str]] = []

    for framework in frameworks:
        requirements = COMPLIANCE_REQUIREMENTS.get(framework)
        if requirements is None:
            logger.debug("Unknown compliance framework: %s", framework)
            continue

        for field, required_value in requirements.items():
            if field == "log_level_minimum":
                current_level = getattr(config, "log_level", "minimal")
                current_severity = LOG_LEVEL_SEVERITY.get(current_level, 0)
                required_severity = LOG_LEVEL_SEVERITY.get(required_value, 0)
                if current_severity < required_severity:
                    violations.append(
                        {
                            "framework": framework,
                            "field": "log_level",
                            "required": f">= {required_value}",
                            "actual": current_level,
                            "message": (
                                f"{framework} requires log_level to be at least "
                                f"'{required_value}' (current: '{current_level}')"
                            ),
                        }
                    )
                continue

            actual_value = getattr(config, field, None)
            if actual_value is None:
                continue

            if isinstance(required_value, bool) and actual_value != required_value:
                violations.append(
                    {
                        "framework": framework,
                        "field": field,
                        "required": str(required_value),
                        "actual": str(actual_value),
                        "message": (f"{framework} requires {field} to be {required_value}"),
                    }
                )

    return violations
