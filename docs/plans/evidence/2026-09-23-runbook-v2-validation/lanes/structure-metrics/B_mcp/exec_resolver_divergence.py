#!/usr/bin/env python3
"""Side-by-side execution: chat-path enforcement.py resolver vs the MCP orchestrator's own action
resolution on the same inputs, plus a static import scan proving no MCP module imports enforcement.py.
Run: env -i PATH=/usr/bin:/bin PYTHONPATH=<root>/gateway/ai_mesh_gateway:<root>/gateway:<root>/shared <py> exec_resolver_divergence.py <pkgdir>"""
import ast, asyncio, json, os, sys, types
from types import SimpleNamespace
pkg = sys.argv[1]
fake_main = types.ModuleType("main"); fake_main.INPUT_SCANNER = None; fake_main.POLICY_SYNC = None; fake_main.CONFIG = {}
sys.modules["main"] = fake_main
import enforcement as enf
import mcp_scan_orchestrator as orch
import config_sync as cs

out = {}
# 1) static: which modules import enforcement / which resolver-ish names each MCP module defines
imports = {}
for f in sorted(os.listdir(pkg)):
    if not f.endswith(".py"):
        continue
    t = ast.parse(open(os.path.join(pkg, f), encoding="utf-8").read())
    mods = set()
    for n in ast.walk(t):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[-1])
        elif isinstance(n, ast.Import):
            for a in n.names:
                mods.add(a.name.split(".")[-1])
    if "enforcement" in mods:
        imports.setdefault("modules_importing_enforcement", []).append(f)
out.update(imports)
out["mcp_modules_importing_enforcement"] = [m for m in imports.get("modules_importing_enforcement", []) if m.startswith("mcp_")]

# 2) vocabulary: MCP posture 'tag' vs enforcement lattice
out["enf.normalize_action('tag')"] = enf.normalize_action("tag")
out["enf.action_rank('tag')"] = enf.action_rank("tag")
out["enf.action_rank('allow')"] = enf.action_rank("allow")
out["enf.action_rank('monitor')"] = enf.action_rank("monitor")
out["orch._is_observe_only_posture('tag')"] = orch._is_observe_only_posture("tag")
out["orch._resolve_tier_action({'action':'inherit'}, 'tag')"] = orch._resolve_tier_action({"action": "inherit"}, "tag")

# 3) same verdict, same posture: Tier-2 verdict 'flag' under posture/enforcement 'block'
out["enf.resolve_enforcement('flag', enforcement_mode='block')"] = enf.resolve_enforcement("flag", enforcement_mode="block")
out["enf.resolve_enforcement('redact', enforcement_mode='block')"] = enf.resolve_enforcement("redact", enforcement_mode="block")

orch.redact_all = lambda t: "<<REDACT_ALL>>"  # sentinel so the redact branch is observable

class Stub:
    def __init__(self, action): self.action = action
    async def scan_prompt_with_tier2(self, text, **kw):
        return SimpleNamespace(tier="tier_2", action=self.action, threat_type="prompt_injection", confidence=0.9, detail="stub")

for verdict in ("flag", "redact", "block"):
    for posture in ("block", "redact", "monitor"):
        txt, findings, blocked, fb = asyncio.run(orch._scan_text_tier2(
            "some tool text", scan_direction="input", enforcement=posture, scanner=Stub(verdict),
            org_slug="o", org_tier2_override=True, org_tier2_strict=True, strict_mode="strict", policy_only=False))
        mcp = "block" if blocked else ("redact" if txt == "<<REDACT_ALL>>" else ("allow+finding(flag)" if findings else "allow"))
        chat = enf.resolve_enforcement(verdict, enforcement_mode=posture)
        out[f"verdict={verdict} posture={posture}"] = {"mcp_orchestrator(legacy, policy_only=False)": mcp, "enforcement.resolve_enforcement": chat}

# 4) Tier-2 enablement resolver duplication
out["orch._resolve_mcp_tier2_enabled is config_sync.resolve_mcp_tier2_enabled"] = orch._resolve_mcp_tier2_enabled is cs.resolve_mcp_tier2_enabled
out["cs.resolve_mcp_tier2_enabled.__code__ == cs.resolve_tier2_enabled.__code__ (bytecode)"] = cs.resolve_mcp_tier2_enabled.__code__.co_code == cs.resolve_tier2_enabled.__code__.co_code
print(json.dumps(out, indent=1))
