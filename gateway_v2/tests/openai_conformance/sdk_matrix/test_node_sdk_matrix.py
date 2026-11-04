"""L34-1: the stock Node ``openai`` SDK against the genuine ``build_app`` over loopback TCP.

Skips with a named reason when ``node`` or ``node_modules/openai`` does not resolve; it never
installs anything (the install is a separate, network-needing step; see ``Dockerfile.sdk-matrix``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_NODE_DIR = _HERE.parents[0] / "node"
_SCRIPT = _HERE / "node" / "gw34_matrix.mjs"
_GATEWAY_V2 = _HERE.parents[2]
_CELLS = tuple("abcdefghij")


def _skip_reason() -> str | None:
    if shutil.which("node") is None:
        return "node is not installed on this host"
    if not (_NODE_DIR / "node_modules" / "openai" / "package.json").exists():
        return "node_modules/openai is absent (run `npm ci` in tests/openai_conformance/node)"
    return None


@pytest.fixture(scope="module")
def node_results() -> dict[str, dict[str, object]]:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)
    server = subprocess.Popen(
        [sys.executable, "-m", "tests.openai_conformance.sdk_matrix.serve_scripted"],
        cwd=_GATEWAY_V2, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        env={"PYTHONPATH": str(_GATEWAY_V2), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    try:
        assert server.stdout is not None
        line = server.stdout.readline().strip()
        assert line.startswith("PORT="), line
        port = line.removeprefix("PORT=")
        # run from the directory that holds node_modules so the bare `openai` import resolves
        script = _NODE_DIR / "gw34_matrix.mjs"
        script.write_text(_SCRIPT.read_text())
        try:
            done = subprocess.run(
                ["node", str(script)], cwd=_NODE_DIR, capture_output=True, text=True,
                timeout=120, check=False,
                env={"AMF_BASE_URL": f"http://127.0.0.1:{port}", "AMF_API_KEY": "sk-gw34-matrix",
                     "PATH": "/usr/bin:/bin:/usr/local/bin"},
            )
        finally:
            script.unlink(missing_ok=True)
        out: dict[str, dict[str, object]] = {}
        for raw in done.stdout.splitlines():
            if raw.startswith("{"):
                row = json.loads(raw)
                out[row["cell"]] = row
        out["_stderr"] = {"text": done.stderr[-2000:]}
        return out
    finally:
        if server.stdin is not None:
            server.stdin.close()
        server.wait(timeout=15)


#: OPEN FINDING (owner decision, see the exit note): the Node SDK's ``chat.completions.stream()``
#: helper breaks on the gateway's named ``amf.trace`` SSE event ("chunk.choices is not iterable")
#: because the Node ``Stream`` yields EVERY named event as ``{event, data}`` and the helper treats
#: it as a chunk. Fixing it means changing the trace framing (an SSE comment, or an in-band
#: ``choices: []`` chunk as OpenAI's own usage chunk), which changes what console consumers
#: receive, so it is NOT made here. ``strict=True`` flips this to a failure the day it is fixed.
_NODE_TRACE_FRAMING = pytest.mark.xfail(
    strict=True, reason="Node stream() helper cannot parse the named amf.trace event"
)


@pytest.mark.parametrize(
    "cell",
    [pytest.param(c, marks=_NODE_TRACE_FRAMING) if c == "c" else c for c in _CELLS],
)
@pytest.mark.matrix(client="node", cell="x")
def test_node_cell(cell: str, node_results: dict[str, dict[str, object]]) -> None:
    row = node_results.get(cell)
    assert row is not None, f"cell {cell} did not report: {node_results.get('_stderr')}"
    assert row["ok"] is True, row.get("detail")
