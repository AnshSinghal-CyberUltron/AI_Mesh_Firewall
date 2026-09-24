import json, time, statistics, os
from tokenizers import Tokenizer
TOK="/var/tmp/b2/models/deberta-v3-xsmall/tokenizer.json"
t=Tokenizer.from_file(TOK)
# real English prose corpus from repo markdown
src=[]
for root,_,fs in os.walk("/home/contact_cyberultron_com/AI_Mesh_Firewall/docs"):
    for f in fs:
        if f.endswith(".md"):
            try: src.append(open(os.path.join(root,f),encoding="utf-8",errors="ignore").read())
            except Exception: pass
text=" ".join(src)
if len(text)<200000: text=(text*10)[:400000]
def band(ntok):
    # slice text so that it encodes to ~ntok tokens
    lo,hi=0,len(text)
    s=text[:ntok*6]
    ids=t.encode(s, add_special_tokens=False).ids
    # trim by characters until token count matches
    while len(ids)>ntok:
        s=s[:int(len(s)*ntok/len(ids))]
        ids=t.encode(s, add_special_tokens=False).ids
    return s,len(ids)
res={}
for ntok in (256,512,1024,2048):
    s,n=band(ntok)
    ts=[]
    for _ in range(30): t.encode(s)
    for _ in range(300):
        a=time.perf_counter_ns(); t.encode(s); ts.append((time.perf_counter_ns()-a)/1e6)
    ts.sort()
    res[ntok]={"chars":len(s),"tokens":n,"p50":ts[len(ts)//2],"p99":ts[int(len(ts)*0.99)],"min":ts[0]}
    print(f"[M] DeBERTa-v3 SP tokenize  ~{ntok} tok  ({len(s)} chars, {n} ids): p50={ts[len(ts)//2]:.4f} ms  p99={ts[int(len(ts)*0.99)]:.4f}  min={ts[0]:.4f}")
# batch encode 2 windows
print("tokenizers version:", __import__("tokenizers").__version__)
json.dump(res, open("/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/perf-evidence-auditor/out_asm_tok_TODAY.json","w"), indent=1)
