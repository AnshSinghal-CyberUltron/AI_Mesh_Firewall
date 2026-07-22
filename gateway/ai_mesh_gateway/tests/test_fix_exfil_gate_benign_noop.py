"""NO-OP LOCK for the exfil-gate fix — benign content must never be mutated.

The fix restored zero-click beacon defang by narrowing the render-leak gate back to
"explicit operator monitor" (6359c049 had widened it to a helper that also matched the
server-default `tag`, which silently skipped the whole pass).

Restoring an enforcement gate carries a specific risk: over-defanging legitimate content is
its own defect. Structurally it should be impossible — the posture gate decides only
WHETHER TO ASK, while `_url_smuggles_data` decides WHETHER IT IS A BEACON, so restoring the
gate cannot widen detection by a byte. This file is the empirical proof of that claim,
driven through the REAL floor.

POSTURE CORRECTED 2026-07-21 (strict-operator-control product rule). This file used to say
the fix restored the defang "under the DEFAULT `tag` posture", and drove every cell with
`enabled_info=None`. Both are now wrong, and the second was the dangerous one:

  * WRONG PREMISE: enforcement is strictly what the operator selected for that org. `tag`
    and `monitor` are observe-only — findings are still emitted, but the payload is never
    mutated and the call is never blocked. The defang is a static hardening floor and fires
    only under an operator-selected ENFORCING posture (`redact` or `block`).
  * WHY IT MATTERED HERE: on the default posture nothing mutates ANYTHING, so a "byte
    identical" assertion passed vacuously — it would have stayed green against a defang that
    mangled every benign link it touched. A false-positive safeguard has to run where the
    defang is live, so every cell is now driven under BOTH enforcing postures.

The presigned-S3 cell matters most: a long opaque signature in a query string is the classic
false-positive shape for a data-smuggling heuristic.
"""
import json, pytest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_proxy

BENIGN = [
    ("cdn_image",   "See ![logo](https://cdn.example.com/logo.png) here"),
    ("svg_logo",    '<img src="https://cdn.example.com/brand.svg">'),
    ("plain_link",  "Docs at https://docs.example.com/guide please"),
    ("no_url",      "Just prose with **bold** and `code` and _emphasis_."),
    ("presigned_s3","https://b.s3.amazonaws.com/k?X-Amz-Signature=abc123def456789abcdef0123456789abcdef01&X-Amz-Expires=900"),
    ("img_params",  '<img src="https://cdn.example.com/i.png?w=100&h=50">'),
    ("markdown_ops","2*3 and a_b_c and **quarterly** *strong* growth"),
    ("npm",         "`npm install` then `npm run build`"),
]

# The operator-selected ENFORCING postures — the only ones under which the defang runs at
# all, and therefore the only ones under which a no-op claim means anything.
ENFORCING = ["redact", "block"]


@pytest.mark.asyncio
@pytest.mark.parametrize("posture", ENFORCING)
@pytest.mark.parametrize("name,text", BENIGN)
async def test_benign_is_byte_identical(name, text, posture):
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info={"default_scan_action": posture},
        org_slug="o", server_slug="s", actor=None)
    # Compare the DECODED leaf, not the JSON blob: json.dumps escapes the quotes in
    # <img src="..."> so a raw-substring check false-fails on any HTML cell.
    out = json.loads(json.dumps(scanned))["content"][0]["text"]
    assert out == text, (
        f"{name} under {posture}: benign content MUTATED\n  in : {text}\n  out: {out}")
    assert not any(f.get("threat_type") == "exfil" for f in findings), f"{name}: false exfil finding"
    assert not blocked, f"{name} under {posture}: benign content blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("name,text", BENIGN)
async def test_benign_is_byte_identical_under_observe_only_too(name, text):
    """The companion half of the product rule: an observe-only posture must not mutate
    benign content either — for the different reason that it must not mutate ANY content.
    Kept separate from the enforcing sweep above so that the two reasons cannot be confused,
    and so that a regression in either one is unambiguous about which contract it broke."""
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info=None, org_slug="o", server_slug="s", actor=None)
    out = json.loads(json.dumps(scanned))["content"][0]["text"]
    assert out == text, f"{name}: observe-only posture MUTATED content"
    assert not blocked, f"{name}: observe-only posture must never block"
