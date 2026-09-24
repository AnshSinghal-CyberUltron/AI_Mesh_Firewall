"""Frozen OpenAI SDK conformance — env-selected app resolver (GW01)."""

from gateway_v2.contracts.openai_conformance.harness import (
    conformance_app_name,
    is_tcp_mode,
    tcp_base_url,
)

__all__ = ("conformance_app_name", "is_tcp_mode", "tcp_base_url")
