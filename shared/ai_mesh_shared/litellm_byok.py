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

import os
from urllib.parse import urlparse

# Llama 3.1 70B is not offered in every Bedrock region (e.g. ap-south-1).
_REGION_BEDROCK_MODEL_FALLBACKS: dict[str, dict[str, str]] = {
    "ap-south-1": {
        "bedrock/meta.llama3-1-70b-instruct-v1:0": "bedrock/meta.llama3-70b-instruct-v1:0",
    },
}


def resolve_bedrock_model_id(model_id: str, *, region: str = "") -> str:
    """
    Map a configured Bedrock model id to one that exists in *region*.

    Honors ``BEDROCK_LLAMA_MODEL_ID`` when set (full LiteLLM model slug).
    """
    mid = (model_id or "").strip()
    if not mid:
        return mid
    env_override = os.environ.get("BEDROCK_LLAMA_MODEL_ID", "").strip()
    if env_override:
        return env_override
    region_key = (
        (region or "").strip().lower()
        or os.environ.get("BEDROCK_REGION", "").strip().lower()
        or os.environ.get("AWS_DEFAULT_REGION", "").strip().lower()
    )
    fallback = (_REGION_BEDROCK_MODEL_FALLBACKS.get(region_key) or {}).get(mid)
    return fallback or mid

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


def _apply_bedrock_aws_fields(
    params: dict,
    *,
    access_id: str = "",
    secret: str = "",
    default_region: str = "",
) -> dict:
    """Map IAM credentials into LiteLLM Bedrock boto3 fields (never ``api_key``)."""
    out = dict(params or {})
    out.pop("api_key", None)
    if access_id:
        out["aws_access_key_id"] = access_id
    if secret:
        out["aws_secret_access_key"] = secret
    region = str(out.get("aws_region_name") or "").strip() or (default_region or "").strip()
    if region:
        out["aws_region_name"] = region
    return out


def apply_bedrock_env_credentials(
    params: dict,
    *,
    env_access_key: str = "",
    env_secret_key: str = "",
    default_region: str = "",
) -> dict:
    """
    Apply gateway ``.env`` AWS credentials for Bedrock models (development default).

    Bedrock inference uses ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``, and
    ``BEDROCK_REGION`` from the gateway environment — not a generic LiteLLM
    ``api_key`` field.
    """
    return _apply_bedrock_aws_fields(
        params,
        access_id=(env_access_key or "").strip(),
        secret=(env_secret_key or "").strip(),
        default_region=default_region,
    )


def apply_bedrock_byok_credentials(
    params: dict,
    raw_credential: str,
    *,
    env_access_key: str = "",
    env_secret_key: str = "",
    default_region: str = "",
) -> dict:
    """
    Map org BYOK credentials into LiteLLM Bedrock boto3 fields.

    Supported ``raw_credential`` formats:
    - ``AKIA...:secret`` — access key id and secret access key
    - ``AKIA...`` only — access key id; secret taken from *env_secret_key* when set

    Passing only an access key id as LiteLLM ``api_key`` makes Bedrock return
    "Invalid API Key format: Must start with pre-defined prefix".
    """
    raw = (raw_credential or "").strip()
    if not raw:
        return apply_bedrock_env_credentials(
            params,
            env_access_key=env_access_key,
            env_secret_key=env_secret_key,
            default_region=default_region,
        )

    access_id = ""
    secret = ""

    if ":" in raw:
        left, right = raw.split(":", 1)
        access_id = left.strip()
        secret = right.strip()
    elif raw.startswith("AKIA"):
        access_id = raw
        secret = (env_secret_key or "").strip()
    else:
        out = dict(params or {})
        out["api_key"] = raw
        return out

    return _apply_bedrock_aws_fields(
        params,
        access_id=access_id,
        secret=secret,
        default_region=default_region,
    )
