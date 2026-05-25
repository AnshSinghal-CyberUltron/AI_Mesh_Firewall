"""
Intent Classifier for the Gateway Data Plane.

Classifies the intent of incoming queries to determine if they are
legitimate information retrieval vs manipulation attempts.
Uses pattern-based heuristics with optional LLM escalation.

Intent categories:
- information_retrieval: Normal search/question
- data_extraction: Attempting to extract sensitive data
- system_manipulation: Attempting to alter system behavior
- reconnaissance: Probing system capabilities/boundaries
- benign_conversation: General chat
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger("gateway.intent_classifier")


@dataclass
class IntentVerdict:
    """Result of intent classification."""
    intent: str = "information_retrieval"  # primary intent
    confidence: float = 0.0
    secondary_intents: list[str] = field(default_factory=list)
    risk_factors: list[str] = field(default_factory=list)
    is_suspicious: bool = False


# Pattern-based intent signals
EXTRACTION_PATTERNS = [
    (r"(?:list|show|dump|export|give)\s+(?:me\s+)?(?:all|every)\s+(?:data|record|file|document|entry|user|password|key|secret|credential)", "bulk_data_request"),
    (r"(?:what|where)\s+(?:are|is)\s+(?:the|your)\s+(?:api|secret|private|internal)\s+(?:key|token|password|credential)", "credential_extraction"),
    (r"(?:show|reveal|display|print)\s+(?:the\s+)?(?:database|schema|table|collection)\s+(?:structure|contents|data)", "schema_extraction"),
    (r"(?:enumerate|scan|map)\s+(?:all\s+)?(?:endpoint|route|api|service|port|host)", "reconnaissance"),
    (r"(?:how\s+(?:do|can|to)\s+)?(?:bypass|circumvent|evade|disable|override)\s+(?:security|auth|filter|guard|firewall|detection)", "evasion_intent"),
    (r"(?:what\s+(?:security|protection|filter|guard))\s+(?:do you|are)\s+(?:have|use|running)", "reconnaissance"),
]

MANIPULATION_PATTERNS = [
    (r"(?:change|modify|alter|update|set)\s+(?:your|the|my)\s+(?:role|behavior|instruction|rule|permission|policy)", "behavior_modification"),
    (r"(?:disable|turn off|deactivate|remove)\s+(?:the\s+)?(?:filter|guard|scanner|firewall|protection|security|safety)", "security_disable"),
    (r"(?:grant|give|elevate|escalate)\s+(?:me\s+)?(?:admin|root|superuser|full)\s+(?:access|privilege|permission|right)", "privilege_escalation"),
    (r"(?:delete|drop|truncate|destroy|wipe)\s+(?:all\s+)?(?:data|record|file|collection|table|database|log)", "destructive_intent"),
]

RECONNAISSANCE_PATTERNS = [
    (r"(?:what|which)\s+(?:model|version|framework|library|system)\s+(?:are you|do you|is)\s+(?:using|running|on)", "tech_stack_probe"),
    (r"(?:what|how)\s+(?:is|does)\s+(?:your|the)\s+(?:architecture|infrastructure|deployment|backend|system)", "architecture_probe"),
    (r"(?:are you|do you)\s+(?:connected to|have access to|running on|deployed)", "capability_probe"),
    (r"(?:test|check|verify|probe)\s+(?:if|whether)\s+(?:you|the system)\s+(?:can|will|would)", "boundary_probe"),
]


class IntentClassifier:
    """Classifies the intent of incoming queries."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._extraction_patterns = [(re.compile(p, re.IGNORECASE), label) for p, label in EXTRACTION_PATTERNS]
        self._manipulation_patterns = [(re.compile(p, re.IGNORECASE), label) for p, label in MANIPULATION_PATTERNS]
        self._reconnaissance_patterns = [(re.compile(p, re.IGNORECASE), label) for p, label in RECONNAISSANCE_PATTERNS]
        LOG.info("IntentClassifier initialized (enabled=%s)", enabled)

    def classify(self, text: str) -> IntentVerdict:
        """Classify the intent of a query."""
        if not self._enabled or not text.strip():
            return IntentVerdict()

        risk_factors: list[str] = []
        intents: dict[str, float] = {}

        # Check extraction patterns
        for pattern, label in self._extraction_patterns:
            if pattern.search(text):
                risk_factors.append(label)
                intents["data_extraction"] = intents.get("data_extraction", 0) + 0.3

        # Check manipulation patterns
        for pattern, label in self._manipulation_patterns:
            if pattern.search(text):
                risk_factors.append(label)
                intents["system_manipulation"] = intents.get("system_manipulation", 0) + 0.4

        # Check reconnaissance patterns
        for pattern, label in self._reconnaissance_patterns:
            if pattern.search(text):
                risk_factors.append(label)
                intents["reconnaissance"] = intents.get("reconnaissance", 0) + 0.25

        if not intents:
            return IntentVerdict(
                intent="information_retrieval",
                confidence=0.8,
            )

        # Determine primary intent
        primary_intent = max(intents, key=intents.get)
        primary_confidence = min(intents[primary_intent], 1.0)
        secondary = [k for k in intents if k != primary_intent]

        is_suspicious = primary_confidence >= 0.3 or len(risk_factors) >= 2

        if is_suspicious:
            LOG.info(
                "Suspicious intent detected: %s (confidence=%.2f, factors=%s)",
                primary_intent, primary_confidence, risk_factors,
            )

        return IntentVerdict(
            intent=primary_intent,
            confidence=round(primary_confidence, 3),
            secondary_intents=secondary,
            risk_factors=risk_factors,
            is_suspicious=is_suspicious,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled
