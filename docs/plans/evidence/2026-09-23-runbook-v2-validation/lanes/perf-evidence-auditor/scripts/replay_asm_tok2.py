import time, os
from tokenizers import Tokenizer
t=Tokenizer.from_file("/var/tmp/b2/models/deberta-v3-xsmall/tokenizer.json")
src=[]
for root,_,fs in os.walk("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs"):
    for f in fs:
        if f.endswith(".md"):
            try: src.append(open(os.path.join(root,f),encoding="utf-8",errors="ignore").read())
            except Exception: pass
text=" ".join(src)
for nch in (2048,4096,10000):
    s=text[:nch]
    ids=t.encode(s,add_special_tokens=False).ids
    for _ in range(30): t.encode(s)
    ts=[]
    for _ in range(300):
        a=time.perf_counter_ns(); t.encode(s); ts.append((time.perf_counter_ns()-a)/1e6)
    ts.sort()
    print(f"[M] SP tokenize {nch} chars -> {len(ids)} ids : p50={ts[150]:.4f} ms p99={ts[296]:.4f} ms  ({1000*ts[150]/nch:.4f} us/char)")
