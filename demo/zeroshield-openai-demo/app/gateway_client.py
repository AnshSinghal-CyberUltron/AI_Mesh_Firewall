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
from openai import OpenAI

from app.config import API_KEY, BASE_URL, MAX_RETRIES, TIMEOUT
from app.pipeline import build_pipeline_view


class ZeroShieldClient:
    """Production reference client for ZeroShield OpenAI-compatible gateway."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.api_key = api_key or API_KEY
        if not self.api_key:
            raise ValueError("ZEROSHIELD_API_KEY is required")
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=TIMEOUT,
            max_retries=MAX_RETRIES,
        )

    @staticmethod
    def _meta(resp: Any) -> dict:
        extra = getattr(resp, "model_extra", None) or {}
        return {
            "zeroshield": extra.get("zeroshield") or {},
            "pipeline_trace": extra.get("pipeline_trace") or {},
        }

    def _view(self, meta: dict, *, model: str = "", content: str = "", **kw) -> dict:
        view = build_pipeline_view(
            zeroshield=meta.get("zeroshield"),
            pipeline_trace=meta.get("pipeline_trace"),
            requested_model=model,
        )
        return {"content": content, "model": model, "pipeline": view, **meta, **kw}

    def list_models(self) -> list[dict]:
        return [{"id": m.id, "owned_by": getattr(m, "owned_by", "")} for m in self.client.models.list().data]

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

    def _chat_stream(self, messages, model, max_tokens, extra, headers) -> Iterator[dict]:
        create_kw: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens, stream=True)
        if extra:
            create_kw["extra_body"] = extra
        if headers:
            create_kw["extra_headers"] = headers
        stream = self.client.chat.completions.create(**create_kw)
        last_model = model
        for chunk in stream:
            if chunk.model:
                last_model = chunk.model
            if chunk.choices and chunk.choices[0].delta:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield {"type": "delta", "content": delta, "model": last_model}
            extra = getattr(chunk, "model_extra", None) or {}
            if extra.get("zeroshield"):
                # FULL-PIPELINE-ON-STREAM: the gateway now emits the 9-stage
                # pipeline_trace in the terminal stream frame; build the same view
                # the non-stream path returns so the UI renders the full pipeline.
                yield {
                    "type": "trace",
                    "zeroshield": extra["zeroshield"],
                    "pipeline": build_pipeline_view(
                        zeroshield=extra["zeroshield"],
                        pipeline_trace=extra.get("pipeline_trace") or {},
                        requested_model=model,
                    ),
                }
        yield {"type": "done", "model": last_model}

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
        resp = self.client.responses.create(**kwargs)
        meta = self._meta(resp)
        return self._view(meta, model=resp.model or model, content=resp.output_text or "")

    def _respond_stream(self, **kwargs) -> Iterator[dict]:
        stream = self.client.responses.create(stream=True, **kwargs)
        last_model = kwargs.get("model", "auto")
        for event in stream:
            etype = getattr(event, "type", "")
            if etype == "response.output_text.delta":
                yield {"type": "delta", "content": getattr(event, "delta", "") or ""}
            elif etype == "response.completed":
                r = getattr(event, "response", None)
                if r and getattr(r, "model", None):
                    last_model = r.model
                meta = self._meta(r) if r else {}
                yield {"type": "completed", "content": getattr(r, "output_text", "") if r else "", "model": last_model, **meta}
        yield {"type": "done", "model": last_model}

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
            code = (body or {}).get("code", "") if isinstance(body, dict) else ""
            if code == "rag_access_denied":
                raise RuntimeError(
                    f"RAG collection '{collection}' has no vector policy. "
                    "Run: python scripts/bootstrap_rag.py"
                ) from exc
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
            code = (data or {}).get("code", "")
            if code == "rag_access_denied":
                raise RuntimeError(
                    f"RAG collection '{collection}' has no vector policy. "
                    "Run: python scripts/bootstrap_rag.py"
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
        return self.respond(prompt, model=model, mcp_context=ctx)

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
