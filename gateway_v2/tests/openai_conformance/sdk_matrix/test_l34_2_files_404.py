"""L34-2: ``/v1/files`` is ``404 endpoint_not_found``; the SDK raises ``NotFoundError`` after
exactly ONE request (no retry storm on a deliberately unsupported endpoint)."""

from __future__ import annotations

import pytest

openai = pytest.importorskip("openai")
pytest.importorskip("uvicorn")

from tests.openai_conformance.sdk_matrix.harness import (  # noqa: E402
    API_KEY,
    LoopbackServer,
    build_matrix_app,
    matrix_contract,
)
from tests.openai_conformance.sdk_matrix.scripts import scripted_provider  # noqa: E402


def test_files_is_a_single_404_endpoint_not_found() -> None:
    inner = build_matrix_app(contract=matrix_contract(), provider=scripted_provider())
    seen: list[str] = []

    async def counting(scope: dict[str, object], receive: object, send: object) -> None:
        if scope.get("type") == "http":
            seen.append(str(scope["path"]))
        await inner(scope, receive, send)  # type: ignore[operator]

    with LoopbackServer(counting) as server:
        client = openai.OpenAI(base_url=f"{server.base_url}/v1", api_key=API_KEY, max_retries=0)
        with pytest.raises(openai.NotFoundError) as raised:
            client.files.list()
        client.close()
    assert raised.value.status_code == 404
    assert isinstance(raised.value.body, dict) and raised.value.body["code"] == "endpoint_not_found"
    assert seen == ["/v1/files"], seen
