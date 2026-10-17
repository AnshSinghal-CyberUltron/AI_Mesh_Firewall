# Validates: Requirements 1.1, 1.2, 1.3, 1.4
"""GW01 resolver-driven collection gate for the ported OpenAI conformance suite.

Three rules, all keyed off the App_Resolver
(``gateway_v2.contracts.openai_conformance.harness``) and the ``sdk_app`` /
``sdk_client`` fixtures that build the v1 FastAPI app
(``ai_mesh_gateway.main.app``):

1. **Resolver-driven, no stale skip.** There is NO skip that references the
   absence of a v2 OpenAI surface — GW15/GW16/GW17/GW18 have shipped it. The
   switch is now ``conformance_app_name()`` (Requirements 1.1, 1.2).
2. **TCP-vs-ASGI separation (kept verbatim).** When ``tcp_base_url()`` is set
   the in-process ASGI cells (the ``sdk_app`` / ``sdk_client`` fixtures) are a
   different job and are skipped, so a TCP run exercises only the real-socket
   cells (Requirement 1.3).
3. **v1 arm gated under v2.** When the resolver selects ``v2``, the v1-app cells
   — identified by the ``sdk_app`` / ``sdk_client`` fixtures, which drive the v1
   app that has no v2 equivalent to target — collect cleanly but are skipped so
   they do not run the v1 app under a v2 run; the v2 wire contract is proved by
   the v2 arm (``build_app``-driven cells, which do not use these fixtures).
   Under ``v1`` / unset these cells run unchanged (Requirement 1.4).
"""

from __future__ import annotations

import pytest

from gateway_v2.contracts.openai_conformance.harness import conformance_app_name, tcp_base_url

_V1_ARM_FIXTURES = ("sdk_app", "sdk_client")


def _targets_v1_app(item: pytest.Item) -> bool:
    """A cell drives the v1 app iff it carries a ``sdk_app`` / ``sdk_client`` fixture."""
    fixtures = getattr(item, "fixturenames", ())
    return any(name in fixtures for name in _V1_ARM_FIXTURES)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    app = conformance_app_name()
    tcp = tcp_base_url()
    skip_asgi_on_tcp = pytest.mark.skip(
        reason="AMF_CONFORMANCE_BASE_URL set; ASGI stub suite is a different job",
    )
    skip_v1_arm_under_v2 = pytest.mark.skip(
        reason="v1-app cell; v2 wire contract proved by the v2 arm",
    )
    for item in items:
        if not _targets_v1_app(item):
            continue
        if tcp:
            item.add_marker(skip_asgi_on_tcp)
        elif app == "v2":
            item.add_marker(skip_v1_arm_under_v2)
