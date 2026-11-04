# Validates: AC5, P9 (phase 4b before the admission slot and the budget spend)
"""Source-order and behaviour pins for phase 4b in ``ChatSurface.run_lifecycle``."""

from __future__ import annotations

import ast
import asyncio
import inspect

from gateway_v2.edge import openai_chat
from tests.edge._lgw29_app import Harness, chat


def _calls_in_order() -> list[str]:
    tree = ast.parse(inspect.getsource(openai_chat.ChatSurface.run_lifecycle).lstrip())
    names: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            names.append((node.lineno, name))
    return [name for _, name in sorted(names)]


def test_phase_4b_precedes_admission_and_budget_in_the_source() -> None:
    order = _calls_in_order()
    assert order.index("incident_phase") < order.index("_admit") < order.index("_budget")
    assert order.index("incident_phase") < order.index("spent")


def test_phase_4b_follows_the_plan_phase_and_the_request_id() -> None:
    order = _calls_in_order()
    assert order.index("_plan") < order.index("note_request") < order.index("incident_phase")


def test_a_refused_request_takes_no_slot_spends_nothing_and_calls_no_provider() -> None:
    h = Harness()
    h.engage("global")
    result = asyncio.run(h.call("/v1/chat/completions", chat()))
    assert result.status == 503
    assert (h.admission.admitted, h.budget.spent, h.provider.open_calls) == (0, 0, 0)
    assert h.budget.refund_calls == []  # nothing to refund: nothing was charged
