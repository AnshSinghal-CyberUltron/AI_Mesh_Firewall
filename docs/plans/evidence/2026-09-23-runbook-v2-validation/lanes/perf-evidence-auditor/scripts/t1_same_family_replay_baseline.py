"""Replay the 2026-08-27 devil's-advocate probe (src_da_t1_vs_potion_line79.txt, same 20 prompts,
same call) against the BASELINE 2a657fad scanner, to test whether rb.md:2123 '3/10 same-family,
0/10 paraphrased' still describes v1 at the runbook's baseline."""
import sys, re
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/baseline-2a657fad/gateway/ai_mesh_gateway")
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/baseline-2a657fad/gateway")
sys.path.insert(0, "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/baseline-2a657fad/shared")
src = open("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/perf-evidence-auditor/src_da_t1_vs_potion_line79.txt").read()
PARA = eval(re.search(r"PARA = (\[.*?\])\n", src, re.S).group(1))
SAME = eval(re.search(r"SAME = (\[.*?\])\n", src, re.S).group(1))
import scanner as S
print("scanner module:", S.__file__)
cls = getattr(S, "PromptScanner", None) or getattr(S, "InputScanner")
sc = cls()
print("scanner class:", type(sc))
def verdict(t):
    v = sc._scan_prompt_sync(t, False)
    return (getattr(v,'action',None), getattr(v,'threat_type',None), round(float(getattr(v,'confidence',0) or 0),3))
for name, corpus in (("SAME-FAMILY", SAME), ("DISJOINT-VOCAB PARAPHRASE", PARA)):
    hits = 0
    print(f"\n### {name}")
    for t in corpus:
        a, th, c = verdict(t)
        if a in ("block", "redact") or (th and th != "none"):
            hits += 1
        print(f"  {a:8s} {str(th):18s} {c:5.2f}  {t[:64]}")
    print(f"  ==> baseline _scan_prompt_sync detects {hits}/{len(corpus)}")
