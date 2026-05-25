"""
Canary Token System for the Gateway Data Plane.

Injects invisible marker tokens into prompts before sending to the LLM.
After receiving the LLM response, checks whether the canary token leaked
into the output — indicating the LLM is blindly repeating context.

Inspired by Rebuff layer 4 and Vigil canary token systems.
"""

import logging
import secrets
import re
from dataclasses import dataclass

LOG = logging.getLogger("gateway.canary_tokens")

# Canary format: invisible Unicode wrapper around a random hex token
CANARY_PREFIX = "\u200b\u200c"  # Zero-width space + zero-width non-joiner
CANARY_SUFFIX = "\u200c\u200b"


@dataclass
class CanaryResult:
    """Result of canary token verification."""
    leaked: bool = False
    canary_word: str = ""
    detail: str = ""


class CanaryTokenManager:
    """Manages canary token injection and verification."""

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        LOG.info("CanaryTokenManager initialized (enabled=%s)", enabled)

    def generate_canary(self) -> str:
        """Generate a unique canary token."""
        token = secrets.token_hex(8)  # 16-char hex string
        return f"{CANARY_PREFIX}{token}{CANARY_SUFFIX}"

    def inject_canary(self, context_text: str) -> tuple[str, str]:
        """Inject a canary token into context text.

        Returns (modified_text, canary_word).
        The canary is placed at the boundary between context and instructions.
        """
        if not self._enabled or not context_text:
            return context_text, ""

        canary = self.generate_canary()
        # Insert canary at the start and end of context
        modified = f"{canary}\n{context_text}\n{canary}"
        return modified, canary

    def check_leakage(self, response_text: str, canary_word: str) -> CanaryResult:
        """Check if the canary token appears in the LLM response.

        If the canary leaked, it means the LLM is echoing context verbatim,
        which indicates potential prompt injection success or context leakage.
        """
        if not self._enabled or not canary_word or not response_text:
            return CanaryResult()

        # Check for exact canary match
        if canary_word in response_text:
            LOG.warning("Canary token LEAKED in LLM response")
            return CanaryResult(
                leaked=True,
                canary_word=canary_word,
                detail="Canary token found verbatim in LLM output — context leakage detected",
            )

        # Check for partial canary (hex portion only, without invisible chars)
        hex_token = canary_word.replace(CANARY_PREFIX, "").replace(CANARY_SUFFIX, "")
        if hex_token and hex_token in response_text:
            LOG.warning("Canary token hex portion leaked in LLM response")
            return CanaryResult(
                leaked=True,
                canary_word=canary_word,
                detail=f"Canary hex token '{hex_token}' found in LLM output — context leakage detected",
            )

        # Check for zero-width character clusters (potential obfuscated leak)
        zwc_pattern = re.compile(r"[\u200b\u200c\u200d\ufeff]{4,}")
        if zwc_pattern.search(response_text):
            LOG.info("Zero-width character cluster found in response (potential obfuscated canary)")
            return CanaryResult(
                leaked=True,
                canary_word=canary_word,
                detail="Suspicious zero-width character cluster in response — possible canary leak",
            )

        return CanaryResult()

    @property
    def enabled(self) -> bool:
        return self._enabled
