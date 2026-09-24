"""
Normalize LiteLLM params for org BYOK models on OpenAI-compatible gateways.

OpenRouter, Together, Groq OpenAI-compat, vLLM, etc. expose an OpenAI-style
``/v1/chat/completions`` API.  When ``model_id`` carries a vendor prefix
(``anthropic/...``, ``google/...``), LiteLLM otherwise selects the native SDK
and hits the wrong path against a third-party ``api_base`` (OpenRouter 404 HTML).

Setting ``custom_llm_provider=openai`` forces the OpenAI-compatible client while
preserving the upstream model slug the gateway operator configured.
"""
from __future__ import annotations

from urllib.parse import urlparse

# Providers that always use their own SDK — never force OpenAI compat.
_NATIVE_SDK_PROVIDERS = frozenset(
    {
        "openai",
        "anthropic",
        "azure",
        "google",
        "aws_bedrock",
        "mistral",
        "cohere",
        "deepseek",
        "huggingface",
        "meta",
        "ollama",
        "internal",
    }
)


def is_openrouter_api_base(api_base: str) -> bool:
    """Return True when *api_base* points at OpenRouter."""
    host = (urlparse((api_base or "").strip()).hostname or "").lower()
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def sanitize_api_base(api_base: str) -> str:
    """
    Normalize operator-entered bases (e.g. OpenRouter .../v1/chat/completions → .../v1).
    """
    base = (api_base or "").strip().rstrip("/")
    if not base:
        return ""
    for suffix in ("/chat/completions", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    return base


def needs_openai_compatible_client(*, provider: str, api_base: str) -> bool:
    """
    Decide whether LiteLLM needs ``custom_llm_provider=openai``.

    Rules:
    - ``provider=custom`` with any ``api_base`` → OpenAI-compat proxy (BYOK).
    - Any ``api_base`` on OpenRouter → OpenAI-compat (even if provider mis-set).
    - Native providers without a custom ``api_base`` → keep default SDK routing.
    """
    base = (api_base or "").strip()
    if not base:
        return False
    provider_key = (provider or "").strip().lower()
    if is_openrouter_api_base(base):
        return True
    if provider_key == "custom":
        return True
    if provider_key and provider_key not in _NATIVE_SDK_PROVIDERS:
        return True
    return False


def normalize_litellm_params(params: dict, *, provider: str = "") -> dict:
    """
    Return a copy of *params* with BYOK OpenAI-compat fields applied when needed.

    Idempotent: leaves an existing ``custom_llm_provider`` untouched.
    """
    out = dict(params or {})
    api_base = sanitize_api_base(str(out.get("api_base") or ""))
    if api_base:
        out["api_base"] = api_base
    if not needs_openai_compatible_client(provider=provider, api_base=api_base):
        return out
    if out.get("custom_llm_provider"):
        return out
    out["custom_llm_provider"] = "openai"
    return out
