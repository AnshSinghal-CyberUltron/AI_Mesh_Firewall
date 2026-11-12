# Reviewer A's probe (PR #115 review 5869763490); fp/unavailable import switched to tests.edge.test_f4_route_surfaces. Run from gateway_v2/ with venv-ci.
"""Reviewer A #115: decode-budget UNAVAILABLE findings now join fail_closed extension rows."""
import asyncio, base64, os, tempfile, json
from pathlib import Path
from tests.edge import test_f4_route_surfaces as t

big_text = ("lorem ipsum dolor sit amet " * 11000)[:300_000]          # 300 KB plain text, no PII
img = base64.b64encode(os.urandom(150_000)).decode()                    # ~200 KB base64 PNG-like blob
html = "<p>" + "&amp; " * 5000 + "</p>"                                 # 5,000 HTML entities, 30 KB
cases = {
    "text_300KB": {"content": [{"type": "text", "text": big_text}]},
    "image_b64_200KB": {"content": [{"type": "image", "data": img, "mimeType": "image/png"}]},
    "html_5000_entities": {"content": [{"type": "text", "text": html}]},
}
for mode in ("off", "monitor", "enforce"):
    for name, result in cases.items():
        broker = t.mf.Broker(t._ok(result))
        app, _, _ = t._app(Path(tempfile.mkdtemp()), mode, broker=broker)
        st, body = asyncio.run(t._rpc(app, "/v1/mcp/search", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fetch", "arguments": {}}}))
        stages = [s for s in (body.get("pipeline_trace") or {}).get("stages", []) if s.get("name") == "output_guardrail"]
        print(f"{mode:8} mcp_result {name:20} -> {st} {t._code(body)!r} {stages}")
    # the same big text through a RAG chunk (vector upsert then vector query)
    d = Path(tempfile.mkdtemp())
    print(f"{mode:8} vector_upsert html_5000_entities -> {t.vector_upsert(d, mode, html)[:2]}")
