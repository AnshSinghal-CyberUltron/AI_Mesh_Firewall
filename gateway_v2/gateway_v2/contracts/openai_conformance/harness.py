"""Env-selected application under test for the frozen OpenAI contract.

AMF_CONFORMANCE_APP: ``v1`` (default) or ``v2``.
AMF_CONFORMANCE_BASE_URL: when set, TCP mode (stock SDK over HTTP). Empty = ASGI.

This module is also the single, resolver-aware reader for the Expected_Diff_Ledger
(``expected_diffs.py``). :func:`expected_diff` consults the ledger and returns the value
expected for the app :func:`conformance_app_name` currently selects, so a conformance
cell never branches on the app name itself — it calls ``expected_diff("<key>")`` and
asserts the returned value, which is correct under both apps (Requirements 6.2, 5.3).
It fails closed on an unknown ledger key (Requirement 5.4 extends to the ledger reader).
"""

from __future__ import annotations

import os

from gateway_v2.contracts.openai_conformance.expected_diffs import LEDGER

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


def expected_diff(key: str) -> object:
    """Return the ledger value expected for the app the resolver currently selects.

    Looks ``key`` up in the Expected_Diff_Ledger and returns the entry's ``.v2`` value
    when :func:`conformance_app_name` selects ``"v2"``, otherwise its ``.v1`` value (the
    ``"v1"`` / default app). The caller — a conformance cell — never branches on the app
    name itself; it asserts against the returned value directly.

    Fails closed on an unknown ledger key with a ``KeyError`` naming the bad key, so a
    typo never silently returns ``None``.
    """

    try:
        entry = LEDGER[key]
    except KeyError:
        raise KeyError(f"unknown Expected_Diff_Ledger key: {key!r}") from None
    return entry.v2 if conformance_app_name() == "v2" else entry.v1
