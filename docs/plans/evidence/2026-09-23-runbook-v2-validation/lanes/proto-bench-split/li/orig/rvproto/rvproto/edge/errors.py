"""The ONLY place an OpenAI error envelope is built and sent."""

from __future__ import annotations

import math

import orjson

from rvproto.domain.request import ErrorSpec
from rvproto.edge.http import Headers, Send, send_bytes


def envelope(spec: ErrorSpec) -> bytes:
    return orjson.dumps(
        {"error": {"message": spec.message, "type": spec.type, "param": spec.param, "code": spec.code}}
    )


async def send_error(send: Send, spec: ErrorSpec, request_id: str, extra: Headers = ()) -> None:
    headers: list[tuple[bytes, bytes]] = [(b"x-request-id", request_id.encode())]
    if spec.retry_after_s is not None:
        headers.append((b"retry-after", str(max(1, math.ceil(spec.retry_after_s))).encode()))
        headers.append((b"retry-after-ms", str(max(1, math.ceil(spec.retry_after_s * 1000))).encode()))
    headers.extend(extra)
    await send_bytes(send, spec.status, envelope(spec), headers)


def bad_request(message: str, param: str | None = None, code: str = "invalid_request") -> ErrorSpec:
    return ErrorSpec(400, "invalid_request_error", code, message, param)


def too_large(message: str, code: str) -> ErrorSpec:
    return ErrorSpec(413, "invalid_request_error", code, message, "messages")


def not_found(path: str) -> ErrorSpec:
    return ErrorSpec(404, "invalid_request_error", "unknown_url", f"Unknown request URL: {path}")


def method_not_allowed(method: str) -> ErrorSpec:
    return ErrorSpec(405, "invalid_request_error", "method_not_allowed", f"{method} not allowed")


def blocked(rules: tuple[str, ...], code: str = "blocked_by_policy") -> ErrorSpec:
    return ErrorSpec(403, "policy_violation", code,
                     f"Request blocked by organization policy ({', '.join(rules) or 'posture'}).")


def upstream(message: str, code: str = "provider_error") -> ErrorSpec:
    return ErrorSpec(502, "upstream_error", code, message)
