"""Env-selected application under test for the frozen OpenAI contract.

AMF_CONFORMANCE_APP: ``v1`` (default) or ``v2``.
AMF_CONFORMANCE_BASE_URL: when set, TCP mode (stock SDK over HTTP). Empty = ASGI.
"""

from __future__ import annotations

import os

_ALLOWED_APPS = frozenset({"v1", "v2"})


def conformance_app_name() -> str:
    raw = os.environ.get("AMF_CONFORMANCE_APP", "v1").strip().lower() or "v1"
    if raw not in _ALLOWED_APPS:
        raise RuntimeError(f"AMF_CONFORMANCE_APP={raw!r} is not in {_ALLOWED_APPS}")
    return raw


def tcp_base_url() -> str | None:
    raw = os.environ.get("AMF_CONFORMANCE_BASE_URL", "").strip()
    return raw or None


def is_tcp_mode() -> bool:
    return tcp_base_url() is not None
