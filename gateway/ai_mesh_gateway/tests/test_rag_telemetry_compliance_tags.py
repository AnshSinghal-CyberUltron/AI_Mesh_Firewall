"""#20: RAG telemetry/audit events were emitted with EMPTY compliance_tags while
chat/embeddings/output passed the org's compliance_frameworks (HIPAA/GDPR/PCI)
explicitly — a compliance-reporting gap (an auditor filtering telemetry by HIPAA
would miss RAG events). Fix: _emit_telemetry auto-injects compliance_tags from the
per-request org config (set by _set_request_org_context), only when the caller
did not pass them (explicit wins). These tests lock that.
"""
import main


class _RecTelemetry:
    def __init__(self):
        self.events = []

    def emit(self, ev):
        self.events.append(ev)


class _StubConfigSync:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_config(self, slug):
        return self._cfg


def _tags(ev):
    return ev.get("compliance_tags") if isinstance(ev, dict) else getattr(ev, "compliance_tags", None)


def test_emit_telemetry_auto_injects_org_compliance_tags(monkeypatch):
    rec = _RecTelemetry()
    monkeypatch.setattr(main, "TELEMETRY", rec, raising=False)
    monkeypatch.setattr(
        main, "CONFIG_SYNC",
        _StubConfigSync({"compliance_frameworks": ["HIPAA", "GDPR"], "telemetry_enabled": True}),
        raising=False,
    )
    tok = main._REQUEST_ORG_SLUG.set("acme")
    try:
        main._emit_telemetry(event_type="rag_query", action="allow", project_id="p1")
    finally:
        main._REQUEST_ORG_SLUG.reset(tok)
    assert rec.events, "no telemetry emitted"
    assert _tags(rec.events[0]) == ["HIPAA", "GDPR"], "compliance_tags not auto-injected for RAG"


def test_emit_telemetry_explicit_compliance_tags_win(monkeypatch):
    rec = _RecTelemetry()
    monkeypatch.setattr(main, "TELEMETRY", rec, raising=False)
    monkeypatch.setattr(
        main, "CONFIG_SYNC",
        _StubConfigSync({"compliance_frameworks": ["HIPAA"], "telemetry_enabled": True}),
        raising=False,
    )
    tok = main._REQUEST_ORG_SLUG.set("acme")
    try:
        main._emit_telemetry(event_type="chat", action="block", compliance_tags=["PCI"])
    finally:
        main._REQUEST_ORG_SLUG.reset(tok)
    assert rec.events
    assert _tags(rec.events[0]) == ["PCI"], "explicit caller compliance_tags must not be overwritten"


def test_emit_telemetry_no_frameworks_leaves_empty(monkeypatch):
    rec = _RecTelemetry()
    monkeypatch.setattr(main, "TELEMETRY", rec, raising=False)
    monkeypatch.setattr(
        main, "CONFIG_SYNC",
        _StubConfigSync({"telemetry_enabled": True}),  # no compliance_frameworks
        raising=False,
    )
    tok = main._REQUEST_ORG_SLUG.set("acme")
    try:
        main._emit_telemetry(event_type="rag_query", action="allow")
    finally:
        main._REQUEST_ORG_SLUG.reset(tok)
    assert rec.events
    assert _tags(rec.events[0]) == []  # build_telemetry_event default
