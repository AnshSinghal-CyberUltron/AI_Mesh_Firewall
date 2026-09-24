import random, heapq
random.seed(11)
# NVIDIA L4, BERT-base seq384, FP16, AVERAGE latency per batch (ms) [V, derived from B3's seq/s table]
BERT_L4 = {1:2.092, 2:3.139, 4:5.739, 8:11.38, 16:23.02, 32:50.63}
K = 3.00/2.092    # DeBERTa-v3-xsmall@512 / BERT-base@384 scale on L4  [D]
def L_win(w):     # latency for a WINDOW-batch of size w
    ks = sorted(BERT_L4)
    if w in BERT_L4: return K*BERT_L4[w]
    lo = max(k for k in ks if k<=w); hi = min([k for k in ks if k>=w], default=32)
    if lo==hi: return K*BERT_L4[lo]
    f=(w-lo)/(hi-lo); return K*(BERT_L4[lo]+f*(BERT_L4[hi]-BERT_L4[lo]))
print("DeBERTa-v3-xsmall @512 on 1x L4, TRT FP16 -- derived latency & throughput by window-batch [D]")
for w in (1,2,4,8,16,32):
    print(f"  w={w:2d} windows  latency={L_win(w):6.2f} ms  ->{w/L_win(w)*1000:7.1f} win/s = {w/2/L_win(w)*1000:6.1f} req/s (2 win/req)")

def sim(lam_rps, win_per_req=2, max_win_batch=8, n=200000, warm=20000):
    # Poisson arrivals; server takes up to max_win_batch/win_per_req requests per batch
    maxb = max(1, max_win_batch//win_per_req)
    t=0.0; arrivals=[]
    for _ in range(n):
        t += random.expovariate(lam_rps/1000.0)   # ms
        arrivals.append(t)
    q=[]; i=0; free=0.0; soj=[]; served=0
    while i<len(arrivals) or q:
        if not q:
            if i>=len(arrivals): break
            free=max(free, arrivals[i]); q.append(arrivals[i]); i+=1
        while i<len(arrivals) and arrivals[i]<=free and len(q)<maxb:
            q.append(arrivals[i]); i+=1
        b=len(q); dur=L_win(b*win_per_req); done=free+dur
        for a in q: soj.append(done-a)
        served+=b; q=[]; free=done
        while i<len(arrivals) and arrivals[i]<=free and len(q)<maxb:
            q.append(arrivals[i]); i+=1
        if not q and i<len(arrivals): free=max(free,arrivals[i])
    soj=soj[warm:]; soj.sort()
    return soj[len(soj)//2], soj[int(len(soj)*0.95)], soj[int(len(soj)*0.99)], soj[int(len(soj)*0.999)]

CPU_P50=1.6; CPU_P99=3.5   # non-GPU firewall stages, p50 / assumed p99 [D]
print("\n1x L4 with dynamic batching (max window-batch 8), Poisson arrivals, 2 windows/request")
print("  per-L4 RPS | GPU p50 | GPU p95 | GPU p99 | TOTAL tax p50 | TOTAL tax p99 | 8-L4 fleet RPS")
for lam in (25,50,75,100,125,150,175,200,220):
    p50,p95,p99,p999 = sim(lam)
    print(f"  {lam:9d} | {p50:7.2f} | {p95:7.2f} | {p99:7.2f} | {p50+CPU_P50:13.2f} | {p99+CPU_P99:13.2f} | {lam*8:6d}")
