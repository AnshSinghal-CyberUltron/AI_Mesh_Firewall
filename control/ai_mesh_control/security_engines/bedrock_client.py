"""
AWS Bedrock client for platform ML (legacy Tier-2 invoke_model, health).

Tier-2 inference is selected by TIER2_PROVIDER. This client remains the Bedrock
transport when the provider is bedrock. Embeddings/judge stay on Bedrock.

Authentication uses standard AWS IAM credentials from environment:
  - AWS_ACCESS_KEY_ID
  - AWS_SECRET_ACCESS_KEY

Configuration:
  - BEDROCK_REGION: AWS region (default: ap-south-1)
    - BEDROCK_MODEL: model ID (default: openai.gpt-oss-120b-1:0)
  - BEDROCK_TIMEOUT: request timeout in seconds (default: 60)
  - BEDROCK_VERIFY_SSL: set to "false" to disable SSL verification (dev-only, default: true)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass

_BEDROCK_SSL_DISABLED = os.getenv("BEDROCK_VERIFY_SSL", "true").lower() == "false"
if _BEDROCK_SSL_DISABLED:
    os.environ.setdefault("CURL_CA_BUNDLE", "")
    os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
    os.environ.setdefault("AWS_CA_BUNDLE", "")

try:
    import boto3
    import botocore.httpsession
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None  # type: ignore[assignment]
    botocore = None  # type: ignore[assignment]
    BotoConfig = None  # type: ignore[assignment,misc]

LOG = logging.getLogger("backend.bedrock_client")

_ANTHROPIC_BEDROCK_VERSION = "bedrock-2023-05-31"


def _to_anthropic_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate an OpenAI chat-completion payload into the Anthropic
    Messages body that Bedrock's Claude models require: ``system`` is a
    top-level string (Claude rejects a ``system`` message role) and the
    ``messages`` list carries only user/assistant turns.
    """
    messages = payload.get("messages") or []
    system_parts: list[str] = []
    chat_messages: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str) else json.dumps(content))
            continue
        chat_messages.append({"role": role, "content": content})
    body: dict[str, Any] = {
        "anthropic_version": _ANTHROPIC_BEDROCK_VERSION,
        "messages": chat_messages,
        "max_tokens": int(payload.get("max_tokens", 1024)),
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if "temperature" in payload:
        body["temperature"] = payload["temperature"]
    return body


def _from_anthropic_body(response_body: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Claude (Anthropic Messages) response into the OpenAI
    chat-completion schema (``choices`` + ``usage.prompt_tokens``) so the
    rest of the scanner can parse it model-agnostically.
    """
    content_blocks = response_body.get("content") or []
    text_parts = [
        b.get("text", "")
        for b in content_blocks
        if isinstance(b, dict) and b.get("type") == "text"
    ]
    text = "".join(text_parts)
    usage = response_body.get("usage") or {}
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": response_body.get("stop_reason"),
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": (usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
        },
    }


class BedrockClient:
    """
    AWS Bedrock runtime client using boto3 invoke_model.

    Expects:
      - AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (standard AWS env vars)
      - BEDROCK_REGION: AWS region (e.g. ap-south-1, us-west-2)
    - BEDROCK_MODEL: model ID (e.g. openai.gpt-oss-120b-1:0)
    """

    def __init__(
        self,
        region: str | None = None,
        model_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        if boto3 is None:
            raise ImportError("boto3 is required for Bedrock provider. Install with: pip install boto3")

        self.region: str = region or os.getenv("BEDROCK_REGION", "ap-south-1")
        self.model_id: str = model_id or os.getenv("BEDROCK_MODEL", "openai.gpt-oss-120b-1:0")
        self.timeout: float = timeout

        boto_config = BotoConfig(
            region_name=self.region,
            read_timeout=int(timeout),
            connect_timeout=10,
            retries={"max_attempts": 2, "mode": "adaptive"},
        )

        verify_ssl = not _BEDROCK_SSL_DISABLED
        session = boto3.Session()
        self._client = session.client(
            "bedrock-runtime",
            region_name=self.region,
            config=boto_config,
            verify=verify_ssl,
        )
        if not verify_ssl:
            LOG.warning(
                "BEDROCK_VERIFY_SSL=false: SSL verification disabled for Bedrock (dev-only; never use in production)"
            )
        aws_key = os.getenv("AWS_ACCESS_KEY_ID", "")
        aws_key_prefix = aws_key[:4] if aws_key else "(not set)"
        LOG.info(
            "BedrockClient initialized: model=%s, region=%s, verify_ssl=%s, timeout=%.1fs, aws_key_prefix=%s",
            self.model_id,
            self.region,
            verify_ssl,
            self.timeout,
            aws_key_prefix,
        )

    def scan_prompt(
        self,
        model: str,
        prompt_payload: dict[str, Any],
        deployment_path: str | None = None,
    ) -> dict[str, Any]:
        """
        Send prompt to Bedrock via invoke_model and return normalized result.

        Returns dict with keys: raw, tokens_in, tokens_out, elapsed_s.
        The ``raw`` value contains the parsed model response body which
        should follow the OpenAI chat-completion schema (with ``choices``).
        """
        effective_model = model or self.model_id
        is_anthropic = "anthropic" in effective_model.lower() or "claude" in effective_model.lower()
        request_payload = (
            _to_anthropic_body(prompt_payload) if is_anthropic else prompt_payload
        )
        body = json.dumps(request_payload)
        payload_bytes = len(body)

        LOG.info(
            "Bedrock invoke_model START: model=%s, region=%s, payload_bytes=%d",
            effective_model,
            self.region,
            payload_bytes,
        )

        start = time.time()
        try:
            response = self._client.invoke_model(
                modelId=effective_model,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
        except Exception as exc:
            elapsed = time.time() - start
            LOG.error(
                "Bedrock invoke_model FAILED: model=%s, region=%s, payload_bytes=%d, elapsed=%.3fs, error=%s",
                effective_model,
                self.region,
                payload_bytes,
                elapsed,
                exc,
                exc_info=True,
            )
            raise
        elapsed = time.time() - start

        response_body = json.loads(response["body"].read())
        if is_anthropic:
            response_body = _from_anthropic_body(response_body)

        usage = response_body.get("usage") or {}
        tokens_in = usage.get("prompt_tokens") or usage.get("total_tokens") or 0
        tokens_out = usage.get("completion_tokens") or 0

        LOG.info(
            "Bedrock invoke_model DONE: model=%s, elapsed=%.3fs, tokens_in=%d, tokens_out=%d, response_keys=%s",
            effective_model,
            elapsed,
            int(tokens_in),
            int(tokens_out),
            list(response_body.keys()),
        )
        LOG.debug(
            "Bedrock raw response body (first 500 chars): %.500s",
            json.dumps(response_body, default=str),
        )

        return {
            "raw": response_body,
            "tokens_in": int(tokens_in),
            "tokens_out": int(tokens_out),
            "elapsed_s": elapsed,
        }

    def is_available(self) -> bool:
        """Health check: verify Bedrock connectivity by listing foundation models."""
        LOG.info("Bedrock health check START: region=%s", self.region)
        try:
            verify_ssl = not _BEDROCK_SSL_DISABLED
            bedrock_mgmt = boto3.client(
                "bedrock",
                region_name=self.region,
                verify=verify_ssl,
            )
            bedrock_mgmt.list_foundation_models()
            LOG.info("Bedrock health check PASSED: region=%s", self.region)
            return True
        except Exception as exc:
            LOG.warning(
                "Bedrock health check FAILED: region=%s, error=%s",
                self.region,
                exc,
                exc_info=True,
            )
            return False


def default_bedrock_client() -> BedrockClient:
    """Convenience factory using environment configuration."""
    return BedrockClient(
        region=os.getenv("BEDROCK_REGION", "ap-south-1"),
        model_id=os.getenv("BEDROCK_MODEL", "openai.gpt-oss-120b-1:0"),
        timeout=float(os.getenv("BEDROCK_TIMEOUT", "10.0")),
    )
