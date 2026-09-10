"""Skipping discarded trace redaction must not weaken redaction — task 8.

MEASURED (py-spy, 121,169 samples): 78% of on-CPU time is `redact_all` and its transport
decoders, called by `_redact_trace_text` on four fields per request while BUILDING the
trace. In `metrics` mode those fields are projected away immediately afterwards.

THE TRAP (R3): `_redact_trace_text` serves two unrelated purposes. At main.py:4935 it is a
DETECTION PREDICATE — `_redact_trace_text(raw) != raw` answers "did redaction change
anything?". A blanket change to that helper would silently invert that answer while every
text-formatting use still looked fine.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_mesh_gateway"))

import trace_projection  # noqa: E402

PII = "Contact alex.morgan@corp-secrets.example.com or 4111-1111-1111-1111"
CLEAN = "The deployment runbook lists three rollback steps in order."


def _main():
    import ai_mesh_gateway.main as m  # noqa: PLC0415
    return m


@pytest.mark.parametrize("mode", ["full", "metrics"])
@pytest.mark.parametrize("text,expect_changed", [(PII, True), (CLEAN, False)])
def test_redaction_predicate_is_unaffected_by_trace_mode(monkeypatch, mode, text,
                                                         expect_changed):
    """R3: the predicate must answer the same in BOTH modes.

    If `_trace_text` were implemented by changing `_redact_trace_text` itself, metrics
    mode would make this return False for PII — reporting "nothing was redacted" about
    text that is full of PII.
    """
    monkeypatch.setenv("GATEWAY_PIPELINE_TRACE_MODE", mode)
    m = _main()
    changed = m._redact_trace_text(text) != text
    assert changed is expect_changed, (
        f"in {mode!r} mode the redaction predicate said changed={changed} for "
        f"{text[:40]!r}; it must not depend on the trace mode")


def test_full_mode_trace_text_is_unchanged(monkeypatch):
    """R1: `full` is the mode whose text reaches the operator UI."""
    monkeypatch.setenv("GATEWAY_PIPELINE_TRACE_MODE", "full")
    m = _main()
    assert m._trace_text(PII) == m._redact_trace_text(PII)
    assert m._trace_text(CLEAN) == m._redact_trace_text(CLEAN)


def test_metrics_mode_skips_building_text_that_is_projected_away(monkeypatch):
    monkeypatch.setenv("GATEWAY_PIPELINE_TRACE_MODE", "metrics")
    m = _main()
    assert m._trace_text(PII) == ""
    assert m._trace_text(CLEAN) == ""


def test_the_skipped_keys_are_exactly_the_ones_the_projection_drops():
    """The skip is only sound because the projection removes these keys anyway.

    If someone later adds one of them back to the kept set, this test fails and the skip
    must be revisited — otherwise the trace would carry an empty string where the UI
    expects text.
    """
    for k in ("input_text", "output_text", "final_response", "prompt_submitted",
              "prompt_preview"):
        assert k not in trace_projection._KEEP_ROOT_KEYS, (
            f"{k!r} is now KEPT by the metrics projection, so blanking it in "
            f"_trace_text would ship an empty field to a consumer that wants text")


def test_trace_text_never_raises_on_odd_input(monkeypatch):
    monkeypatch.setenv("GATEWAY_PIPELINE_TRACE_MODE", "full")
    m = _main()
    for bad in (None, "", 0, [], {}):
        m._trace_text(bad)
