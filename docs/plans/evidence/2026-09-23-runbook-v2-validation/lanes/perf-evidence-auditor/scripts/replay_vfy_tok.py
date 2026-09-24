import time, os, statistics
from tokenizers import Tokenizer
import tokenizers
t=Tokenizer.from_file("/var/tmp/b2/models/deberta-v3-xsmall/tokenizer.json")
print("tokenizers", tokenizers.__version__)

# Corpus A: the assembler's corpus (repo markdown)
src=[]
for root,_,fs in os.walk("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs"):
    for f in fs:
        if f.endswith(".md"):
            try: src.append(open(os.path.join(root,f),encoding="utf-8",errors="ignore").read())
            except Exception: pass
mdtext=" ".join(src)

# Corpus B: plain English prose (no markdown/code) - closer to a real chat prompt
prose=("The quarterly revenue report shows a significant increase in customer acquisition "
"across the enterprise segment, driven primarily by expansion in the financial services "
"vertical. Our analysis suggests that the improvement stems from the new onboarding flow "
"introduced last quarter, which reduced time to first value from eleven days to three. "
"Please summarise the key drivers and recommend three actions for the leadership team, "
"taking into account the competitive dynamics we discussed in the previous meeting and "
"the constraints imposed by the current hiring freeze across all non-engineering roles. ")*80

def bench(label, text, nch):
    s=text[:nch]
    ids=t.encode(s, add_special_tokens=False).ids
    for _ in range(30): t.encode(s)
    ts=[]
    for _ in range(300):
        a=time.perf_counter_ns(); t.encode(s); ts.append((time.perf_counter_ns()-a)/1e6)
    ts.sort()
    n=len(ids)
    wins=-(-n//512)
    print(f"  {label:9s} {nch:6d} chars -> {n:5d} ids  ({nch/n:.2f} ch/tok)  "
          f"p50={ts[150]:.4f} ms p99={ts[296]:.4f}  => {wins} x 512-tok windows")
    return n, ts[150]

print("\n=== Corpus A: repo markdown (the assembler's corpus) ===")
for nch in (1024,2048,4096,10000):
    bench("markdown", mdtext, nch)
print("\n=== Corpus B: plain English prose (realistic chat prompt) ===")
for nch in (1024,2048,4096,10000):
    bench("prose", prose, nch)

print("\n=== Token-band timing (fixed token counts, markdown corpus) ===")
def band(ntok, text):
    s=text[:ntok*8]
    ids=t.encode(s, add_special_tokens=False).ids
    while len(ids)>ntok:
        s=s[:int(len(s)*ntok/len(ids))]
        ids=t.encode(s, add_special_tokens=False).ids
    return s,len(ids)
for ntok in (512,1024):
    s,n=band(ntok, mdtext)
    for _ in range(30): t.encode(s)
    ts=[]
    for _ in range(300):
        a=time.perf_counter_ns(); t.encode(s); ts.append((time.perf_counter_ns()-a)/1e6)
    ts.sort()
    print(f"  {ntok} tok = {len(s)} chars -> {n} ids : p50={ts[150]:.4f} ms p99={ts[296]:.4f}")
