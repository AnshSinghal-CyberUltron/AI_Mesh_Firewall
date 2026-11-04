"""Suite-wide GW29 wiring: the incident seams default to FAIL CLOSED, so tests opt in by name.

Before GW29 an unwired route streamed and answered. Now an unwired ``ChatRoute`` / ``MiscRoute``
refuses with cause ``unwired`` (phase 4b, the attempt recheck, the org gate) and an unwired
in-flight control source cuts every stream. The pre-GW29 suites exercise the pipeline, not the
incident response, so this autouse fixture gives every route constructed WITHOUT an explicit
incident wiring the named dev/test pass-through (:class:`PassThroughIncidentGate` and
:class:`DevNoControlSource`). A test that passes its own gate / recheck / control keeps it, and a
test that needs the real fail-closed defaults marks itself ``real_incident_defaults``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from gateway_v2.domain.incident import fail_closed_gate, fail_closed_org, fail_closed_recheck
from gateway_v2.edge.incident_passthrough import PassThroughIncidentGate
from gateway_v2.edge.inflight_source import DevNoControlSource, RefusingControlSource
from gateway_v2.edge.openai_misc import MiscRoute
from gateway_v2.edge.routes import ChatRoute

_GATE = PassThroughIncidentGate()




@pytest.fixture(autouse=True)
def _dev_incident_wiring(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if request.node.get_closest_marker("real_incident_defaults") is not None:
        yield
        return
    chat_init = ChatRoute.__init__
    misc_init = MiscRoute.__init__

    def chat(self: Any, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("incident_gate") in (None, fail_closed_gate):
            kwargs["incident_gate"] = _GATE
        if kwargs.get("incident_recheck") in (None, fail_closed_recheck):
            kwargs["incident_recheck"] = _GATE.recheck
        if isinstance(kwargs.get("control"), RefusingControlSource):
            kwargs["control"] = DevNoControlSource()
        chat_init(self, *args, **kwargs)

    def misc(self: Any, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("incident_gate") in (None, fail_closed_gate):
            kwargs["incident_gate"] = _GATE
        if kwargs.get("incident_org_gate") in (None, fail_closed_org):
            kwargs["incident_org_gate"] = _GATE.decide_org
        if kwargs.get("incident_recheck") in (None, fail_closed_recheck):
            kwargs["incident_recheck"] = _GATE.recheck
        misc_init(self, *args, **kwargs)

    monkeypatch.setattr(ChatRoute, "__init__", chat)
    monkeypatch.setattr(MiscRoute, "__init__", misc)
    yield
