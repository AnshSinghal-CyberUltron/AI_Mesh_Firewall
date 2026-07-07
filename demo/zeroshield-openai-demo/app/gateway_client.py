"""
ZeroShield gateway access — using ONLY the standard OpenAI SDK.

Every AI capability flows through a single ``openai.OpenAI`` instance pointed at
the ZeroShield gateway (base_url + api_key). No provider SDKs.

Gateway-specific endpoints (RAG ingest/query) use ``client.post()`` — the stock
SDK's own HTTP transport, not a separate HTTP client library for inference.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import httpx
from openai import APIStatusError, APIError, OpenAI

from app.config import (
    API_KEY,
    BASE_URL,
    DEFAULT_MODEL,
    MAX_RETRIES,
    READINESS_LITE,
    READINESS_PROBE_TIMEOUT,
    STREAM_TIMEOUT,
    TIMEOUT,
)
from app.pipeline import build_pipeline_view


class ZeroShieldClient:
    """Production reference client for ZeroShield OpenAI-compatible gateway."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.api_key = api_key or API_KEY
        if not self.api_key:
            raise ValueError("ZEROSHIELD_API_KEY is required")
        # Keep demo UX responsive without false negatives on slow providers.
        # Honor env knobs but cap extremes so one path cannot stall for minutes.
        self.timeout = min(max(float(TIMEOUT), 5.0), 120.0)
        self.stream_timeout = min(max(float(STREAM_TIMEOUT), 30.0), 300.0)
        self.max_retries = min(max(int(MAX_RETRIES), 0), 2)
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

    @staticmethod
    def _meta(resp: Any) -> dict:
        extra = getattr(resp, "model_extra", None) or {}
        return {
            "zeroshield": extra.get("zeroshield") or {},
            "pipeline_trace": extra.get("pipeline_trace") or {},
        }

    @staticmethod
    def _support_hint(message: str, body: dict | None = None) -> dict:
        msg = str(message or "").lower()
        body_text = json.dumps(body or {}, ensure_ascii=True).lower()
        merged = f"{msg}\n{body_text}"
        if "certificate_verify_failed" in merged or "certificate verify failed" in merged:
            return {
                "issue": "provider_tls",
                "summary": "The gateway cannot validate the provider TLS certificate.",
                "plain_text": "The app can reach the internet, but it does not trust the provider's security certificate yet.",
                "next_step": "Install the correct CA certificate bundle in the gateway runtime and restart the gateway.",
            }
        if "timed out" in merged or "timeout" in merged:
            return {
                "issue": "upstream_timeout",
                "summary": "Upstream model call timed out.",
                "plain_text": "The gateway waited for the model provider, but no answer arrived in time.",
                "next_step": "Restore provider connectivity, then retry the request.",
            }
        return {
            "issue": "upstream_error",
            "summary": "Gateway request failed upstream.",
            "plain_text": "The demo app is healthy, but the model backend failed while processing this request.",
            "next_step": "Check gateway logs for the matching request_id and fix provider connectivity.",
        }

    @staticmethod
    def _error_body(exc: Exception) -> dict:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            return body
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {"message": body}
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {}

    def _error_view(self, exc: Exception, *, requested_model: str = "auto", default_status: int = 502) -> dict:
        body = self._error_body(exc)
        status = int(getattr(exc, "status_code", default_status) or default_status)
        err_obj = body.get("error") if isinstance(body.get("error"), dict) else {}
        message = (
            err_obj.get("message")
            or body.get("detail")
            or body.get("message")
            or str(exc)
        )
        zeroshield = body.get("zeroshield") if isinstance(body.get("zeroshield"), dict) else {}
        pipeline_trace = body.get("pipeline_trace") if isinstance(body.get("pipeline_trace"), dict) else {}
        if not zeroshield:
            zeroshield = {
                "action": "block" if status in (400, 401, 403, 422, 429, 500, 503) else "error",
                "detail": message,
                "request_id": body.get("request_id"),
                "threat_type": err_obj.get("code") or body.get("code"),
            }
        support = self._support_hint(message, body)
        return self._view(
            {"zeroshield": zeroshield, "pipeline_trace": pipeline_trace},
            model=requested_model,
            content="",
            error=True,
            status=status,
            message=message,
            support=support,
            raw=body or {"message": message},
        )

    @staticmethod
    def _messages_to_input(messages: list[dict]) -> str:
        lines: list[str] = []
        for msg in messages or []:
            role = str(msg.get("role") or "user")
            content = str(msg.get("content") or "")
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines).strip()

    def _view(self, meta: dict, *, model: str = "", content: str = "", **kw) -> dict:
        view = build_pipeline_view(
            zeroshield=meta.get("zeroshield"),
            pipeline_trace=meta.get("pipeline_trace"),
            requested_model=model,
        )
        return {"content": content, "model": model, "pipeline": view, **meta, **kw}

    def _probe_client(self) -> OpenAI:
        """Short-timeout client for readiness checks (does not block the UI for minutes)."""
        probe_timeout = min(max(float(READINESS_PROBE_TIMEOUT), 5.0), 30.0)
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=probe_timeout,
            max_retries=0,
        )

    def list_models(self) -> list[dict]:
        return [{"id": m.id, "owned_by": getattr(m, "owned_by", "")} for m in self.client.models.list().data]

    def readiness_probe(self) -> dict:
        """
        Runtime readiness check:
        - gateway must list models (always)
        - optional deep mode: tiny completion probe per model (DEMO_READINESS_LITE=0)
        """
        try:
            models = [
                {"id": m.id, "owned_by": getattr(m, "owned_by", "")}
                for m in self._probe_client().models.list().data
            ]
        except Exception as exc:
            err = self._error_view(exc, requested_model="models")
            return {
                "ok": False,
                "gateway_reachable": False,
                "models_total": 0,
                "models_healthy": 0,
                "models": [],
                "readiness_mode": "lite" if READINESS_LITE else "deep",
                "issues": [err.get("support") or {}],
            }

        if READINESS_LITE:
            listed = models[:12]
            return {
                "ok": bool(listed),
                "gateway_reachable": True,
                "models_total": len(listed),
                "models_healthy": len(listed),
                "models": [{"model": m.get("id"), "ok": True, "listed_only": True} for m in listed],
                "models_listed": listed,
                "readiness_mode": "lite",
                "issues": [] if listed else [{"summary": "Gateway returned no models."}],
            }

        checks: list[dict] = []
        # Probe the configured OpenAI-facing model — not auto-routing (avoids dead local backends).
        probe_models = [DEFAULT_MODEL] if DEFAULT_MODEL else ["auto"]
        if not models:
            probe_models = []
        probe = self._probe_client()
        for model_id in probe_models:
            try:
                probe.chat.completions.create(
                    model=model_id,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                )
                checks.append({"model": model_id, "ok": True})
            except Exception as exc:
                err = self._error_view(exc, requested_model=model_id)
                checks.append(
                    {
                        "model": model_id,
                        "ok": False,
                        "status": err.get("status"),
                        "message": err.get("message"),
                        "support": err.get("support"),
                    }
                )

        listed = [{"id": m.get("id"), "owned_by": m.get("owned_by", "")} for m in models]

        healthy = sum(1 for item in checks if item.get("ok"))
        issues = [item.get("support") for item in checks if not item.get("ok") and isinstance(item.get("support"), dict)]
        return {
            "ok": healthy > 0,
            "gateway_reachable": True,
            "models_total": len(checks),
            "models_healthy": healthy,
            "models": checks,
            "models_listed": listed,
            "readiness_mode": "deep",
            "issues": issues,
        }

    def _extra_body(
        self,
        *,
        mcp_context: dict | None = None,
        agent_data: dict | None = None,
        routing_preferences: dict | None = None,
    ) -> dict | None:
        body: dict[str, Any] = {}
        if mcp_context:
            body["mcp_context"] = mcp_context
        if agent_data:
            body["agent_data"] = agent_data
        if routing_preferences:
            body["routing_preferences"] = routing_preferences
        return body or None

    # ── Chat (multi-turn) ─────────────────────────────────────────────────────
    def chat(
        self,
        messages: list[dict],
        model: str = "auto",
        *,
        stream: bool = False,
        max_tokens: int = 1024,
        mcp_context: dict | None = None,
        routing_preferences: dict | None = None,
        rag_context_id: str | None = None,
    ) -> dict | Iterator[dict]:
        extra = self._extra_body(mcp_context=mcp_context, routing_preferences=routing_preferences)
        headers = {"X-ZeroShield-RAG-Context-ID": rag_context_id} if rag_context_id else None
        if stream:
            return self._chat_stream(messages, model, max_tokens, extra, headers)
        create_kw: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens)
        if extra:
            create_kw["extra_body"] = extra
        if headers:
            create_kw["extra_headers"] = headers
        try:
            resp = self.client.chat.completions.create(**create_kw)
            choice = resp.choices[0]
            meta = self._meta(resp)
            return self._view(
                meta,
                model=resp.model or model,
                content=choice.message.content or "",
                finish_reason=choice.finish_reason,
                usage=resp.usage.model_dump() if resp.usage else {},
            )
        except APIStatusError as exc:
            # Fallback for transient chat-completions path failures: try the same
            # request via Responses API so the demo remains usable.
            if int(getattr(exc, "status_code", 500) or 500) >= 500:
                try:
                    fallback = self.client.responses.create(
                        model=model,
                        input=self._messages_to_input(messages),
                        extra_body=extra or None,
                        extra_headers=headers or None,
                    )
                    meta = self._meta(fallback)
                    return self._view(
                        meta,
                        model=fallback.model or model,
                        content=fallback.output_text or "",
                        fallback="responses_api",
                    )
                except APIStatusError as fb_exc:
                    return self._error_view(fb_exc, requested_model=model)
            return self._error_view(exc, requested_model=model)
        except Exception as exc:
            return self._error_view(exc, requested_model=model)

    @staticmethod
    def _stream_fallback_text(zeroshield: dict | None) -> str:
        zs = zeroshield or {}
        action = str(zs.get("action") or "").lower()
        detail = str(zs.get("detail") or zs.get("reason") or "").strip()
        if detail:
            label = action.upper() if action else "INFO"
            return f"[{label}] {detail}"
        if action == "block":
            return "[BLOCK] Request was blocked by ZeroShield policy."
        if action == "error":
            return "[ERROR] Gateway returned no model output for this stream."
        return ""

    def _chat_stream(self, messages, model, max_tokens, extra, headers) -> Iterator[dict]:
        create_kw: dict[str, Any] = dict(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            timeout=self.stream_timeout,
        )
        if extra:
            create_kw["extra_body"] = extra
        if headers:
            create_kw["extra_headers"] = headers
        try:
            stream = self.client.chat.completions.create(**create_kw)
            last_model = model
            had_delta = False
            last_trace: dict[str, Any] | None = None
            for chunk in stream:
                if chunk.model:
                    last_model = chunk.model
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        had_delta = True
                        yield {"type": "delta", "content": delta, "model": last_model}
                extra = getattr(chunk, "model_extra", None) or {}
                if extra.get("zeroshield"):
                    # FULL-PIPELINE-ON-STREAM: the gateway now emits the 9-stage
                    # pipeline_trace in the terminal stream frame; build the same view
                    # the non-stream path returns so the UI renders the full pipeline.
                    last_trace = {
                        "type": "trace",
                        "zeroshield": extra["zeroshield"],
                        "pipeline": build_pipeline_view(
                            zeroshield=extra["zeroshield"],
                            pipeline_trace=extra.get("pipeline_trace") or {},
                            requested_model=model,
                        ),
                    }
                    yield last_trace
            if not had_delta:
                fallback = self._stream_fallback_text((last_trace or {}).get("zeroshield"))
                if fallback:
                    yield {
                        "type": "error",
                        "error": True,
                        "message": fallback,
                        "zeroshield": (last_trace or {}).get("zeroshield"),
                        "pipeline": (last_trace or {}).get("pipeline"),
                    }
                else:
                    yield {
                        "type": "error",
                        "error": True,
                        "message": "Gateway returned no streamed text. Check the Request Pipeline panel for block/error details.",
                        "zeroshield": (last_trace or {}).get("zeroshield"),
                        "pipeline": (last_trace or {}).get("pipeline"),
                    }
            yield {"type": "done", "model": last_model}
        except (APIStatusError, APIError) as exc:
            err = self._error_view(exc, requested_model=model)
            yield {"type": "error", **err}
            yield {"type": "done", "model": model}
        except Exception as exc:
            err = self._error_view(exc, requested_model=model)
            yield {"type": "error", **err}
            yield {"type": "done", "model": model}

    # ── Responses API (primary demo surface) ──────────────────────────────────
    def respond(
        self,
        input_text: str,
        model: str = "auto",
        *,
        stream: bool = False,
        instructions: str | None = None,
        mcp_context: dict | None = None,
        routing_preferences: dict | None = None,
        previous_response_id: str | None = None,
        store: bool = False,
        rag_context_id: str | None = None,
    ) -> dict | Iterator[dict]:
        extra = self._extra_body(mcp_context=mcp_context, routing_preferences=routing_preferences)
        headers = {"X-ZeroShield-RAG-Context-ID": rag_context_id} if rag_context_id else None
        kwargs: dict[str, Any] = {"model": model, "input": input_text}
        if extra:
            kwargs["extra_body"] = extra
        if headers:
            kwargs["extra_headers"] = headers
        if instructions:
            kwargs["instructions"] = instructions
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if store:
            kwargs["store"] = True
        if stream:
            return self._respond_stream(**kwargs)
        try:
            resp = self.client.responses.create(**kwargs)
            meta = self._meta(resp)
            return self._view(meta, model=resp.model or model, content=resp.output_text or "")
        except APIStatusError as exc:
            return self._error_view(exc, requested_model=model)
        except Exception as exc:
            return self._error_view(exc, requested_model=model)

    def _respond_stream(self, **kwargs) -> Iterator[dict]:
        model = kwargs.get("model", "auto")
        try:
            kwargs.setdefault("timeout", self.stream_timeout)
            stream = self.client.responses.create(stream=True, **kwargs)
            last_model = model
            had_delta = False
            last_completed: dict[str, Any] | None = None
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        had_delta = True
                        yield {"type": "delta", "content": delta}
                elif etype == "response.completed":
                    r = getattr(event, "response", None)
                    if r and getattr(r, "model", None):
                        last_model = r.model
                    meta = self._meta(r) if r else {}
                    last_completed = {
                        "type": "completed",
                        "content": getattr(r, "output_text", "") if r else "",
                        "model": last_model,
                        **meta,
                    }
                    if last_completed.get("content"):
                        had_delta = True
                    yield last_completed
            if not had_delta:
                zs = (last_completed or {}).get("zeroshield") if last_completed else None
                fallback = self._stream_fallback_text(zs)
                if fallback:
                    yield {
                        "type": "error",
                        "error": True,
                        "message": fallback,
                        "zeroshield": zs,
                        "pipeline": (last_completed or {}).get("pipeline"),
                    }
                else:
                    yield {
                        "type": "error",
                        "error": True,
                        "message": "Gateway returned no streamed text. Check the Request Pipeline panel for block/error details.",
                        "zeroshield": zs,
                        "pipeline": (last_completed or {}).get("pipeline"),
                    }
            yield {"type": "done", "model": last_model}
        except (APIStatusError, APIError) as exc:
            err = self._error_view(exc, requested_model=model)
            yield {"type": "error", **err}
            yield {"type": "done", "model": model}
        except Exception as exc:
            err = self._error_view(exc, requested_model=model)
            yield {"type": "error", **err}
            yield {"type": "done", "model": model}

    # ── RAG (gateway endpoints via SDK transport) ─────────────────────────────
    def rag_ingest(self, collection: str, documents: list[dict], *, vector_db_type: str = "custom") -> dict:
        from app.config import _debug_log

        try:
            raw = self.client.post(
                "/rag/ingest",
                body={"collection": collection, "documents": documents, "vector_db_type": vector_db_type},
                cast_to=httpx.Response,
            )
        except Exception as exc:
            body = getattr(exc, "body", None)
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    body = {"message": body}
            status = getattr(exc, "status_code", 500)
            _debug_log(
                location="gateway_client.py:rag_ingest",
                message="rag ingest SDK error",
                data={"status": status, "collection": collection, "error": body},
                hypothesis_id="H3",
            )
            return {"status": status, "context_id": "", "result": body, "error": True}
        try:
            data = raw.json()
        except Exception:
            data = {"raw": raw.text[:1000]}
        if raw.status_code >= 400:
            _debug_log(
                location="gateway_client.py:rag_ingest",
                message="rag ingest failed",
                data={"status": raw.status_code, "collection": collection, "error": data},
                hypothesis_id="H3",
            )
        return {
            "status": raw.status_code,
            "context_id": raw.headers.get("X-ZeroShield-RAG-Context-ID", ""),
            "result": data,
        }

    def rag_query(self, collection: str, query: str, *, n_results: int = 4, vector_db_type: str = "custom") -> dict:
        from app.config import _debug_log

        try:
            raw = self.client.post(
                "/rag/query",
                body={"collection": collection, "query": query, "n_results": n_results, "vector_db_type": vector_db_type},
                cast_to=httpx.Response,
            )
        except Exception as exc:
            body = getattr(exc, "body", None)
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    body = {"message": body}
            status = getattr(exc, "status_code", 500)
            _debug_log(
                location="gateway_client.py:rag_query",
                message="rag query SDK error",
                data={"status": status, "collection": collection, "error": body},
                hypothesis_id="H3",
            )
            zs = body if isinstance(body, dict) else {}
            return {
                "status": status,
                "context_id": "",
                "documents": [],
                "scan_verdict": {},
                "zeroshield": zs,
                "pipeline": build_pipeline_view(zeroshield=zs),
                "raw": body,
                "error": True,
            }
        try:
            data = raw.json()
        except Exception:
            data = {"raw": raw.text[:1000]}
        if raw.status_code >= 400:
            _debug_log(
                location="gateway_client.py:rag_query",
                message="rag query failed",
                data={"status": raw.status_code, "collection": collection, "error": data},
                hypothesis_id="H3",
            )
        zs = data.get("zeroshield") or {}
        return {
            "status": raw.status_code,
            "context_id": raw.headers.get("X-ZeroShield-RAG-Context-ID", ""),
            "documents": data.get("documents") or data.get("chunks") or [],
            "scan_verdict": data.get("scan_verdict") or {},
            "zeroshield": zs,
            "pipeline": build_pipeline_view(zeroshield=zs),
            "raw": data,
        }

    # ── Scenario helpers ──────────────────────────────────────────────────────
    def scenario_basic_chat(self, prompt: str, model: str = "auto") -> dict:
        return self.respond(prompt, model=model)

    def scenario_streaming(self, prompt: str, model: str = "auto") -> list[dict]:
        events = list(self.respond(prompt, model=model, stream=True))
        return events

    def scenario_rag(self, collection: str, query: str, model: str = "auto") -> dict:
        retrieval = self.rag_query(collection, query)
        if retrieval["status"] >= 400:
            return {"retrieval": retrieval, "answer": None}
        docs = retrieval.get("documents") or []
        context = "\n\n".join(
            f"- {(d.get('content') or d.get('text') or str(d))[:800]}"
            for d in docs[:6]
        )
        prompt = f"Using ONLY the retrieved context below, answer the question.\n\nContext:\n{context}\n\nQuestion: {query}"
        answer = self.respond(prompt, model=model, rag_context_id=retrieval.get("context_id") or None)
        return {"retrieval": retrieval, "answer": answer}

    def scenario_mcp(self, prompt: str, customer_id: str, model: str = "auto") -> dict:
        ctx = {
            "customer_id": customer_id,
            "profile": {
                "name": "Acme Corp",
                "tier": "enterprise",
                "open_tickets": 2,
                "last_order": "ZS-2024-9912",
            },
        }
        # Prefer chat-completions path for MCP scenario in this demo because the
        # responses path has shown prolonged degraded behavior in this stack.
        return self.chat(
            [{"role": "user", "content": prompt}],
            model=model,
            mcp_context=ctx,
        )

    def scenario_routing(self, prompt: str, model: str = "auto", sensitivity: str = "standard") -> dict:
        # Let the gateway's org routing run for model=auto; avoid forcing
        # routing_override which can hard-block when compliance tags mismatch.
        prefs = {"data_sensitivity": sensitivity}
        try:
            return self.respond(prompt, model=model, routing_preferences=prefs)
        except Exception as exc:
            body = getattr(exc, "body", None)
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    body = {"message": body}
            err_body = body if isinstance(body, dict) else {}
            zs = err_body.get("zeroshield") or err_body
            return {
                "error": True,
                "status": getattr(exc, "status_code", 403),
                "body": err_body,
                "content": err_body.get("error", {}).get("message", str(exc)) if isinstance(err_body.get("error"), dict) else "",
                "pipeline": build_pipeline_view(zeroshield=zs),
                "zeroshield": zs,
            }

    def scenario_guardrail_probe(self, prompt: str, model: str = "auto") -> dict:
        try:
            return self.respond(prompt, model=model)
        except Exception as exc:
            body = getattr(exc, "body", None)
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    body = {"message": body}
            return {
                "error": True,
                "status": getattr(exc, "status_code", 403),
                "body": body,
                "pipeline": build_pipeline_view(
                    zeroshield=body if isinstance(body, dict) else {},
                ),
            }
