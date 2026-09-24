"""GW01 pytest defaults. The 77 ASGI cells always target v1 unless AMF_CONFORMANCE_APP=v2."""

from __future__ import annotations

import pytest

from gateway_v2.contracts.openai_conformance.harness import conformance_app_name, tcp_base_url


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    app = conformance_app_name()
    tcp = tcp_base_url()
    skip_v2 = pytest.mark.skip(reason="v2 has no OpenAI surface yet (GW15)")
    skip_asgi_on_tcp = pytest.mark.skip(
        reason="AMF_CONFORMANCE_BASE_URL set; ASGI stub suite is a different job",
    )
    for item in items:
        if app == "v2" and "sdk_app" in item.fixturenames:
            item.add_marker(skip_v2)
        if tcp and "sdk_app" in getattr(item, "fixturenames", ()):
            item.add_marker(skip_asgi_on_tcp)
        if tcp and "sdk_client" in getattr(item, "fixturenames", ()):
            item.add_marker(skip_asgi_on_tcp)
