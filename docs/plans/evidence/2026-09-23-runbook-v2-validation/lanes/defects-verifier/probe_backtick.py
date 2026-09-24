"""Probe B: runbook P8 (rb.md:2036), §10.2.2 row "Benign inline code" (rb.md:2124),
and rb.md:2676 ("command_injection is outside the explanatory carve-out, so those
blocks are terminal").

Usage: probe_backtick.py <ROOT>. Real modules, production import layout
(PYTHONPATH=<ROOT>/shared:<ROOT>/gateway:<ROOT>/gateway/ai_mesh_gateway;
``ai_mesh_gateway.main`` + top-level ``scanner``/``enforcement`` exactly as gunicorn loads them).
"""
import asyncio
import importlib.util
import os
import re
import sys

ROOT = sys.argv[1]
os.environ["ENABLE_TIER2"] = "false"  # Tier-1 only: no Bedrock client constructed

import ai_mesh_gateway.main as M  # noqa: E402
import enforcement as E  # noqa: E402
import scanner as S  # noqa: E402
import policy_engine as PE  # noqa: E402
import mcp_scan_orchestrator as MO  # noqa: E402
from patterns import compile_pattern  # noqa: E402

for mod in (M, E, S, PE, MO):
    assert mod.__file__.startswith(ROOT), (mod.__name__, mod.__file__)
print("ROOT =", ROOT)
print("modules:", {m.__name__: m.__file__.replace(ROOT, "<ROOT>") for m in (M, E, S, PE, MO)})

BENIGN = [
    "What does `git rebase -i` do?",
    "Why does `list.sort()` return None in Python?",
    "How do I read `process.env.NODE_ENV` inside a Next.js API route?",
    "What's the difference between `String` and `&str` in Rust?",
    "My Dockerfile runs `npm ci && npm run build` but the image is 2 GB. How can I slim it down?",
]
BLANKET = r"`[^`]+`"

print("\n== P8a: ATTACK_PATTERNS['command_injection'] ==")
ci = S.ATTACK_PATTERNS["command_injection"]
for p in ci:
    print("  ", repr(p))
print("blanket backtick pattern present:", BLANKET in ci, "| index:", ci.index(BLANKET) if BLANKET in ci else None)

print("\n== P8b: re.search(r'`[^`]+`', prompt) on 5 benign prompts ==")
hits = 0
for p in BENIGN:
    m = re.search(BLANKET, p)
    hits += bool(m)
    print(f"  match={bool(m)!s:<5} span={m.group(0) if m else None!r:<28} prompt={p!r}")
print(f"TOTAL: {hits}/{len(BENIGN)} match")

print("\n== LIVE v1 Tier-1 input scan (InputScanner.scan_prompt -> _scan_prompt_sync) ==")
print("   (main.py:9198/9238 call INPUT_SCANNER.scan_prompt; /v1/policy/check main.py:16578 too)")
scn = S.InputScanner(thread_pool_size=2, config={})
live_blocks = 0
for p in BENIGN:
    v = asyncio.run(scn.scan_prompt(p))
    live_blocks += v.action == "block"
    print(f"  action={v.action!r:<8} threat={v.threat_type!r:<18} conf={v.confidence:<5} tier={v.tier!r:<9} prompt={p[:45]!r}")
print(f"LIVE scan_prompt blocks: {live_blocks}/{len(BENIGN)}")

print("\n== scan_prompt_with_tier2 Tier-1 stage (Tier-2 off for org) ==")
for p in BENIGN:
    v = asyncio.run(scn.scan_prompt_with_tier2(p, org_tier2_override=False))
    print(f"  action={v.action!r:<8} threat={v.threat_type!r:<10} conf={v.confidence} tier={v.tier!r}")

print("\n== RETAINED-DISABLED reference _scan_prompt_sync_disabled_builtin_default (NOT on live path) ==")
dis = []
for p in BENIGN:
    v = scn._scan_prompt_sync_disabled_builtin_default(p, False, None)
    dis.append(v)
    print(f"  action={v.action!r:<8} threat={v.threat_type!r:<18} conf={v.confidence:<5} tier={v.tier!r:<9} matched={v.matched_patterns}")

print("\n== Would main.py enforce such a Tier-1 command_injection verdict? (adapter main.py:895-948) ==")
for v in dis[:2]:
    kw = M._scanner_kwargs_honoring_pii_toggle(v, recommendation=v.action, pii_detection_enabled=True)
    d = E.resolve_and_enforce(**kw, enforcement_mode="block", tier2_degraded=M._is_tier2_degraded_verdict(v),
                              redaction_possible=True, scan_block_on_injection=True, injection_threshold=0.80)
    print(f"  verdict(block/{v.threat_type}/{v.tier}) -> kwargs all None: {all(x is None for x in kw.values())} -> resolved {d.action!r}")

print("\n== live consumers of ATTACK_PATTERNS (they read only prompt_injection+jailbreak) ==")
for p in BENIGN:
    print(f"  policy_engine._detector_injection_match={PE._detector_injection_match(p)!s:<5} "
          f"_strict={PE._detector_injection_match_strict(p)!s:<5} "
          f"mcp_scan_orchestrator._injection_match={MO._injection_match(p)!s:<5} prompt={p[:40]!r}")

print("\n== carve-out claim (rb.md:2676) ==")
print("  scanner._INJECTION_EXPLANATORY_CATEGORIES =", sorted(S._INJECTION_EXPLANATORY_CATEGORIES))
print("  'command_injection' in carve-out:", "command_injection" in S._INJECTION_EXPLANATORY_CATEGORIES)
for p in ("Explain what `rm -rf /` does.", "Explain what 'ignore all previous instructions' means."):
    v = scn._scan_prompt_sync_disabled_builtin_default(p, False, None)
    lv = asyncio.run(scn.scan_prompt(p))
    print(f"  disabled-ref -> {v.action!r}/{v.threat_type!r}; LIVE scan_prompt -> {lv.action!r}/{lv.threat_type!r}   prompt={p!r}")

print("\n== control-plane built-in pack (the re-homed command_injection family, seeded enabled=False) ==")
spec = importlib.util.spec_from_file_location(
    "builtin_packs_catalog", os.path.join(ROOT, "control/ai_mesh_control/policy/builtin_packs_catalog.py"))
BP = importlib.util.module_from_spec(spec)
sys.modules["builtin_packs_catalog"] = BP  # dataclass processing needs the module registered
spec.loader.exec_module(BP)
fam = next(f for f in BP.FAMILIES if f.key == "command_injection")
print(f"  family={fam.key} default_action={fam.default_action} rules={len(fam.rules)}")
rules = [(r.name, r.regex) for r in fam.rules]
for p in BENIGN:
    hit = [n for n, rx in rules if rx and re.search(rx, p)]
    print(f"  pack rules matching: {hit!s:<60} prompt={p[:48]!r}")
EXTRA = [
    "Should I use `map` or `forEach` here?",
    "Is `for (int i = 0; i < n; i++)` idiomatic in modern C++?",
    "What does `a && b` return in JavaScript when a is falsy?",
]
print("  -- supplementary shapes for the NARROWED rule --")
for p in EXTRA:
    m = re.search(BP._RE_CMD_BACKTICK_NARROWED, p)
    print(f"  narrowed match={bool(m)!s:<5} span={m.group(0) if m else None!r:<34} prompt={p!r}")
