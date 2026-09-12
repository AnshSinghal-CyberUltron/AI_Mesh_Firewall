"""Gemini Developer API transport for platform Tier-2 (input scan + output guard).

Selected by ``TIER2_PROVIDER=gemini`` (aliases: google, google_genai, vertex,
vertex_ai). Uses ``google-genai`` ``Client(api_key=GOOGLE_API_KEY)`` — not Vertex
ADC / ``vertexai=True``.

``ascan_prompt`` / ``scan_prompt`` return the same OpenAI-shaped dict
BedrockClient returns so ``BedrockScanner._extract_content`` is unchanged.

Never logs the API key.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

LOG = logging.getLogger("ai_mesh_shared.tier2_gemini")

GEMINI_PROVIDERS = frozenset({"gemini", "google", "google_genai", "vertex", "vertex_ai"})
# Flash-Lite: ultra-low latency, no thinking overhead. 3.6-flash thinking_budget=0
# returns 400 INVALID_ARGUMENT; T2 classification does not need thinking.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"

_SAFETY_CATEGORIES_CORE = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)
_SAFETY_CIVIC = "HARM_CATEGORY_CIVIC_INTEGRITY"
# Kept for optional retry-disable if an operator forces a thinking model.
_THINKING_BUDGET = 1

_THINKING_UNSUPPORTED_LOGGED = False
_CIVIC_UNSUPPORTED_LOGGED = False


def tier2_provider() -> str:
    return (os.getenv("TIER2_PROVIDER") or "bedrock").strip().lower()


def is_gemini_tier2_provider(provider: str | None = None) -> bool:
    return (provider if provider is not None else tier2_provider()) in GEMINI_PROVIDERS


def gemini_tier2_model() -> str:
    return (os.getenv("VERTEX_TIER2_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL


def _new_genai_client(api_key: str) -> Any:
    from google import genai

    return genai.Client(api_key=api_key)


def _require_api_key() -> str:
    key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GOOGLE_API_KEY is required when TIER2_PROVIDER=gemini")
    return key


def _split_messages(prompt_payload: dict[str, Any]) -> tuple[str, str]:
    system_text = ""
    user_text = ""
    if isinstance(prompt_payload.get("system"), str):
        system_text = prompt_payload["system"]
    for msg in prompt_payload.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system" and isinstance(content, str):
            system_text = content
        elif role == "user":
            user_text = str(content)
    return system_text, user_text


def extract_gemini_text(response: Any) -> str:
    """Join ``parts[].text`` only. Skip thought parts and thoughtSignature."""
    if response is None:
        return ""
    candidates = getattr(response, "candidates", None) or []
    chunks: list[str] = []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            if getattr(part, "thought", False):
                continue
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
    return "".join(chunks)


def _usage_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0
    tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
    tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
    return tokens_in, tokens_out


def _is_invalid_argument(exc: BaseException) -> bool:
    msg = str(exc).lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    looks_400 = code in (400, "400") or "400" in msg
    return looks_400 and ("invalid" in msg or "thinking" in msg)


def _generate_config(
    system_text: str,
    max_tokens: int,
    temperature: float,
    *,
    thinking: bool,
    include_civic: bool = True,
) -> Any:
    from google.genai import types

    cats = list(_SAFETY_CATEGORIES_CORE)
    if include_civic:
        cats.append(_SAFETY_CIVIC)
    safety = [types.SafetySetting(category=cat, threshold="BLOCK_NONE") for cat in cats]
    kwargs: dict[str, Any] = {
        "system_instruction": system_text or None,
        "max_output_tokens": max_tokens,
        "temperature": temperature,
        "response_mime_type": "application/json",
        "safety_settings": safety,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if thinking:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=_THINKING_BUDGET)
    return types.GenerateContentConfig(**kwargs)


def _openai_shaped(
    *,
    content: str,
    tokens_in: int,
    tokens_out: int,
    elapsed_s: float,
    model_id: str,
    call_site: str,
    request_id: str | None,
) -> dict[str, Any]:
    return {
        "raw": {"choices": [{"message": {"content": content}}]},
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "elapsed_s": elapsed_s,
        "model_id": model_id,
        "call_site": call_site,
        "api_method": "generateContent",
        "ran_inference": True,
        "gateway_request_id": request_id,
    }


class GeminiTier2Client:
    """Duck-typed drop-in for BedrockClient.scan_prompt / ascan_prompt."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._model = model or gemini_tier2_model()
        # Scanner JSON classification: never send ThinkingConfig by default.
        self._thinking_enabled = False
        self._civic_enabled = True
        self._client = _new_genai_client(api_key or _require_api_key())
        LOG.info(
            "GeminiTier2Client initialized: model=%s, api_key_set=%s",
            self._model,
            True,
        )

    def is_available(self) -> bool:
        """Duck-type BedrockClient.is_available. Key was required at construct."""
        return True

    def _max_tokens(self, prompt_payload: dict[str, Any]) -> int:
        return int(
            prompt_payload.get("max_tokens")
            or prompt_payload.get("maxTokens")
            or os.getenv("BEDROCK_MAX_TOKENS", "1024")
        )

    def _temperature(self, prompt_payload: dict[str, Any]) -> float:
        try:
            return float(prompt_payload.get("temperature", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _effective_model(self, model: str | None) -> str:
        return (model or self._model or gemini_tier2_model()).strip()

    def _note_config_retry(self, kind: str) -> None:
        global _THINKING_UNSUPPORTED_LOGGED, _CIVIC_UNSUPPORTED_LOGGED
        if kind == "thinking":
            self._thinking_enabled = False
            if not _THINKING_UNSUPPORTED_LOGGED:
                _THINKING_UNSUPPORTED_LOGGED = True
                LOG.warning(
                    "Gemini ThinkingConfig rejected (budget=%s); retrying without it",
                    _THINKING_BUDGET,
                )
            return
        self._civic_enabled = False
        if not _CIVIC_UNSUPPORTED_LOGGED:
            _CIVIC_UNSUPPORTED_LOGGED = True
            LOG.warning("Gemini civic-integrity safety setting rejected; retrying without it")

    def _call_sync(self, model: str, system_text: str, user_text: str, max_tokens: int, temperature: float) -> Any:
        while True:
            config = _generate_config(
                system_text, max_tokens, temperature,
                thinking=self._thinking_enabled, include_civic=self._civic_enabled,
            )
            try:
                return self._client.models.generate_content(
                    model=model, contents=user_text or " ", config=config,
                )
            except Exception as exc:
                if self._thinking_enabled and _is_invalid_argument(exc):
                    self._note_config_retry("thinking")
                    continue
                if self._civic_enabled and _is_invalid_argument(exc):
                    self._note_config_retry("civic")
                    continue
                raise

    async def _call_async(
        self, model: str, system_text: str, user_text: str, max_tokens: int, temperature: float,
    ) -> Any:
        while True:
            config = _generate_config(
                system_text, max_tokens, temperature,
                thinking=self._thinking_enabled, include_civic=self._civic_enabled,
            )
            try:
                return await self._client.aio.models.generate_content(
                    model=model, contents=user_text or " ", config=config,
                )
            except Exception as exc:
                if self._thinking_enabled and _is_invalid_argument(exc):
                    self._note_config_retry("thinking")
                    continue
                if self._civic_enabled and _is_invalid_argument(exc):
                    self._note_config_retry("civic")
                    continue
                raise

    def scan_prompt(
        self,
        model: str,
        prompt_payload: dict[str, Any],
        deployment_path: str | None = None,
        request_id: str | None = None,
        call_site: str = "tier2_scan",
    ) -> dict[str, Any]:
        effective = self._effective_model(model)
        system_text, user_text = _split_messages(prompt_payload)
        max_tokens = self._max_tokens(prompt_payload)
        temperature = self._temperature(prompt_payload)
        LOG.info(
            "Gemini generate_content START: model=%s, call_site=%s, request_id=%s",
            effective, call_site, request_id or "",
        )
        start = time.time()
        try:
            response = self._call_sync(effective, system_text, user_text, max_tokens, temperature)
        except Exception as exc:
            elapsed = time.time() - start
            LOG.error(
                "Gemini generate_content FAILED: model=%s, call_site=%s, elapsed=%.3fs, error=%s",
                effective, call_site, elapsed, exc,
            )
            raise
        elapsed = time.time() - start
        content = extract_gemini_text(response)
        tokens_in, tokens_out = _usage_tokens(response)
        LOG.info(
            "Gemini generate_content DONE: model=%s, call_site=%s, elapsed=%.3fs, "
            "tokens_in=%d, tokens_out=%d",
            effective, call_site, elapsed, tokens_in, tokens_out,
        )
        return _openai_shaped(
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            elapsed_s=elapsed,
            model_id=effective,
            call_site=call_site,
            request_id=request_id,
        )

    async def ascan_prompt(
        self,
        model: str,
        prompt_payload: dict[str, Any],
        deployment_path: str | None = None,
        request_id: str | None = None,
        call_site: str = "tier2_scan",
    ) -> dict[str, Any]:
        effective = self._effective_model(model)
        system_text, user_text = _split_messages(prompt_payload)
        max_tokens = self._max_tokens(prompt_payload)
        temperature = self._temperature(prompt_payload)
        LOG.info(
            "Gemini generate_content START: model=%s, call_site=%s, request_id=%s",
            effective, call_site, request_id or "",
        )
        start = time.time()
        try:
            response = await self._call_async(
                effective, system_text, user_text, max_tokens, temperature,
            )
        except Exception as exc:
            elapsed = time.time() - start
            LOG.error(
                "Gemini generate_content FAILED: model=%s, call_site=%s, elapsed=%.3fs, error=%s",
                effective, call_site, elapsed, exc,
            )
            raise
        elapsed = time.time() - start
        content = extract_gemini_text(response)
        tokens_in, tokens_out = _usage_tokens(response)
        LOG.info(
            "Gemini generate_content DONE: model=%s, call_site=%s, elapsed=%.3fs, "
            "tokens_in=%d, tokens_out=%d",
            effective, call_site, elapsed, tokens_in, tokens_out,
        )
        return _openai_shaped(
            content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            elapsed_s=elapsed,
            model_id=effective,
            call_site=call_site,
            request_id=request_id,
        )
