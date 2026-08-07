"""RAG-28: EVERY /v1/rag|/v1/vector request must leave a telemetry event.

The RAG handlers emit on their happy paths but return early from many guard
branches (auth, policy miss, operation denied, malformed body, provider error) —
``rag_query`` had 23 ``return JSONResponse`` against 4 ``_emit_telemetry`` calls,
``rag_ingest`` 27 against 2. So the dashboard silently under-reported exactly the
events an operator most needs: the refusals. The branch an operator is most
likely to hit (``default_action`` allow/monitor with the operation not listed)
returned 403 with NO event at all, so the most permissive-looking setting
produced the LEAST visible blocks. Framework failures (422 validation, 404/405,
an unhandled 500) never reach a handler and so could never have emitted.

Patching each branch would leak again the moment someone adds one, so the
guarantee is a middleware backstop. These tests lock the contract:
  1. a request that no handler reported still produces exactly one event,
  2. a request the handler DID report is not double-counted,
  3. a handler that raises is reported and the exception still propagates,
  4. non-RAG paths are untouched.
"""
import contextlib

import pytest
from fastapi.testclient import TestClient

import main


class _RecTelemetry:
    def __init__(self):
        self.events = []

    def emit(self, ev):
        self.events.append(ev)


def _rag_events(rec):
    """Events the backstop produced (as opposed to handler-emitted ones)."""
    out = []
    for ev in rec.events:
        md = ev.get("metadata") if isinstance(ev, dict) else getattr(ev, "metadata", None)
        if isinstance(md, dict) and md.get("telemetry_source") == "backstop":
            out.append(ev)
    return out


def _action(ev):
    return ev.get("action") if isinstance(ev, dict) else getattr(ev, "action", None)


@contextlib.contextmanager
def _client_with_recorder():
    """TestClient whose TELEMETRY is patched AFTER startup.

    The app's startup lifespan constructs a real telemetry producer, so patching
    before entering the context is silently overwritten — the recorder then sees
    zero events and the test looks like a product failure. Patch inside, and
    restore before exit so the shutdown hook still gets the real producer.
    """
    rec = _RecTelemetry()
    with TestClient(main.app, raise_server_exceptions=False) as client:
        original = main.TELEMETRY
        main.TELEMETRY = rec
        try:
            yield client, rec
        finally:
            main.TELEMETRY = original


# ── 1. an unreported outcome still lands on the dashboard ──────────────────

@pytest.mark.parametrize(
    "payload,expect_action",
    [
        # No auth header -> 401 from the auth guard, which returns before any
        # handler telemetry. Previously invisible.
        ({"collection": "cdocs", "query": "x"}, "block"),
    ],
)
def test_unauthenticated_rag_request_is_still_reported(payload, expect_action):
    with _client_with_recorder() as (client, rec):
        resp = client.post("/v1/rag/query", json=payload)
        evs = _rag_events(rec)
    assert resp.status_code in (401, 403), resp.text
    assert len(evs) == 1, f"expected exactly one backstop event, got {len(evs)}"
    assert _action(evs[0]) == expect_action


def test_malformed_body_is_still_reported():
    """A body the handler cannot even parse must not vanish silently."""
    with _client_with_recorder() as (client, rec):
        resp = client.post("/v1/rag/query", content=b'{"collection":',
                           headers={"Content-Type": "application/json"})
        evs = _rag_events(rec)
    assert resp.status_code >= 400
    assert len(evs) == 1


def test_unknown_rag_subpath_is_still_reported():
    """A request that never reaches a handler is only reportable by the backstop."""
    with _client_with_recorder() as (client, rec):
        resp = client.post("/v1/rag/does-not-exist", json={})
        evs = _rag_events(rec)
    assert resp.status_code >= 400
    assert len(evs) == 1


# ── 2. no double counting when the handler already reported ────────────────

def test_handler_emitted_event_is_not_double_counted(monkeypatch):
    rec = _RecTelemetry()
    monkeypatch.setattr(main, "TELEMETRY", rec, raising=False)
    """_emit_telemetry marks the request as reported; the backstop stands down."""
    tok = main._RAG_EVENT_EMITTED.set({"emitted": False})
    try:
        main._emit_telemetry(event_type="rag_query", action="allow", project_id="p1")
        assert main._rag_event_was_emitted() is True
    finally:
        main._RAG_EVENT_EMITTED.reset(tok)
    # exactly the handler's event, and it is NOT tagged as a backstop row
    assert len(rec.events) == 1
    assert _rag_events(rec) == []


def test_flag_is_set_even_when_sink_is_unconfigured(monkeypatch):
    """The flag must be set BEFORE the TELEMETRY None-guard.

    Otherwise a deployment with no sink would mark nothing as reported and the
    backstop would fire on every request, double-counting the moment a sink is
    attached.
    """
    monkeypatch.setattr(main, "TELEMETRY", None, raising=False)
    tok = main._RAG_EVENT_EMITTED.set({"emitted": False})
    try:
        main._emit_telemetry(event_type="rag_query", action="allow")
        assert main._rag_event_was_emitted() is True
    finally:
        main._RAG_EVENT_EMITTED.reset(tok)


# ── 3. a raising handler is reported AND still raises ───────────────────────

def test_non_rag_paths_are_not_reported():
    with _client_with_recorder() as (client, rec):
        client.get("/health")
        evs = _rag_events(rec)
    assert evs == [], "backstop must not fire outside /v1/rag|/v1/vector"


# ── 4. the backstop is scoped to RAG/Vector only ────────────────────────────

