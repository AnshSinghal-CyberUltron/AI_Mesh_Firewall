#!/usr/bin/env python3
"""Execute the REAL mcp_scan_orchestrator.scan_mcp_payload over the matrix
tier2 scan-control row {absent, enabled=False, enabled=True} x enabled_info.mcp_tier2_enabled
{absent, None, False, True}, with a recording stub scanner injected as main.INPUT_SCANNER.
Tier-2 "ran" iff the stub's scan_prompt_with_tier2 was awaited.
Run: env -i PATH=/usr/bin:/bin PYTHONPATH=<root>/gateway/ai_mesh_gateway:<root>/gateway:<root>/shared <py> exec_tier2_gate.py
"""
import asyncio, json, sys, types
from types import SimpleNamespace

calls = []
class StubScanner:
    async def scan_prompt_with_tier2(self, text, **kw):
        calls.append({"text": text[:40], **{k: kw.get(k) for k in ("org_tier2_override", "org_tier2_strict")}})
        return SimpleNamespace(tier="tier_2", action="allow", threat_type=None, confidence=0.0, detail="")

fake_main = types.ModuleType("main")
fake_main.INPUT_SCANNER = StubScanner()
fake_main.POLICY_SYNC = None
fake_main.CONFIG = {}
sys.modules["main"] = fake_main
import mcp_scan_orchestrator as orch

T2_ROWS = {"no_tier2_row": None, "tier2.enabled=False": False, "tier2.enabled=True": True}
MCP_T2 = {"absent": "__absent__", "None": None, "False": False, "True": True}
rows = []
for dir_ in ("input", "output"):
    for t2name, t2val in T2_ROWS.items():
        for mname, mval in MCP_T2.items():
            calls.clear()
            eff = {f"tier1_{dir_}": {"enabled": True}}
            if t2val is not None:
                eff[f"tier2_{dir_}"] = {"enabled": t2val}
            info = {} if mval == "__absent__" else {"mcp_tier2_enabled": mval}
            out, res = asyncio.run(orch.scan_mcp_payload(
                {"arg": "please summarise this document"}, scan_direction=dir_, enforcement="block",
                effective_controls=eff, enabled_info=info, org_slug="org-a", server_slug="srv", tool_name="t"))
            stages = [s.get("scan_stage") + (":" + s["reason"] if s.get("reason") else "") for s in res.scan_trace]
            rows.append({"direction": dir_, "tier2_ctrl": t2name, "mcp_tier2_enabled": mname,
                         "tier2_invoked": bool(calls), "n_tier2_calls": len(calls), "trace": stages})
print(json.dumps({"orchestrator_file": orch.__file__, "rows": rows}, indent=1))
print("\nSUMMARY (tier2_invoked):", file=sys.stderr)
for r in rows:
    print(f'{r["direction"]:6s} {r["tier2_ctrl"]:20s} mcp_tier2_enabled={r["mcp_tier2_enabled"]:6s} -> tier2_invoked={r["tier2_invoked"]}  trace={r["trace"]}', file=sys.stderr)
