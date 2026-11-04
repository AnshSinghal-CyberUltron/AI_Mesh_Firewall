"""Wire-profile suite (D13): documented client request shapes, served or declared, never a 5xx."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.openai_conformance.sdk_matrix.harness import (  # noqa: E402
    API_KEY,
    LoopbackServer,
    build_matrix_app,
    matrix_contract,
)
from tests.openai_conformance.sdk_matrix.scripts import scripted_provider  # noqa: E402
from tests.openai_conformance.sdk_matrix.wire_profiles import PROFILES, WireProfile  # noqa: E402


@pytest.fixture(scope="module")
def gateway() -> Iterator[LoopbackServer]:
    app = build_matrix_app(contract=matrix_contract(), provider=scripted_provider())
    with LoopbackServer(app) as server:
        yield server


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: f"{p.client}:{p.shape}")
def test_profile(profile: WireProfile, gateway: LoopbackServer) -> None:
    headers = {"content-type": "application/json", **dict(profile.headers)}
    if "api-key" not in headers:
        headers["authorization"] = f"Bearer {API_KEY}"
    with httpx.Client(base_url=gateway.base_url, timeout=30) as client:
        response = client.post(profile.path, json=profile.body, headers=headers)
    assert response.status_code == profile.status, (profile.shape, response.text[:300])
    assert response.status_code < 500
    if profile.error_param is not None:
        error = response.json()["error"]
        assert error["param"] == profile.error_param


def test_the_profile_table_is_frozen_and_covers_the_named_shapes() -> None:
    shapes = {p.shape for p in PROFILES}
    for needed in ("stream_options.include_usage", "tool_choice auto", "tool_choice required",
                   "tool_choice named", "parallel_tool_calls false", "text-part arrays",
                   "assistant+tool round trip"):
        assert needed in shapes
    with pytest.raises(AttributeError):
        PROFILES[0].status = 500  # type: ignore[misc]
