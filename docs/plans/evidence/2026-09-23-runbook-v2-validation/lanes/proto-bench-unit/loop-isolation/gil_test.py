"""Does HF tokenizers release the GIL? (runs ON the unit with the rvproto venv)
1) throughput: 2 threads x N encodes vs 1 thread x 2N (same total work)
2) loop blocking: an asyncio ticker (1 ms period) while encodes run inline vs in a ThreadPoolExecutor
Text: a real HEADLINE prompt from the corpus padded to ~1000 PG2 tokens; tokenizer-22M.json, no truncation/padding."""
import asyncio, json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from tokenizers import Tokenizer

tok = Tokenizer.from_file(sys.argv[1])
tok.no_truncation(); tok.no_padding()
rows = [json.loads(l) for l in open(sys.argv[2])]
text = max((r for r in rows), key=lambda r: r["tokens_in"])["messages"][1]["content"].replace("{{RVNONCE}}", "ref-w1-alpha-bravo-charlie-delta")
n_tok = len(tok.encode(text, add_special_tokens=False).ids)
N = 300
def work(n, fn):
    for _ in range(n):
        fn(text)
enc = lambda t: tok.encode(t, add_special_tokens=False)
encb = lambda t: tok.encode_batch([t], add_special_tokens=False)
for name, fn in (("encode", enc), ("encode_batch", encb)):
    for _ in range(20): fn(text)
    t0 = time.perf_counter(); work(2 * N, fn); seq = time.perf_counter() - t0
    ths = [threading.Thread(target=work, args=(N, fn)) for _ in range(2)]
    t0 = time.perf_counter(); [t.start() for t in ths]; [t.join() for t in ths]; par = time.perf_counter() - t0
    print(json.dumps({"fn": name, "tokens": n_tok, "per_call_ms": round(seq / (2 * N) * 1000, 3),
                      "seq_s": round(seq, 3), "two_threads_s": round(par, 3), "speedup": round(seq / par, 2)}))

async def ticker(stop, lags):
    while not stop.is_set():
        t = time.perf_counter(); await asyncio.sleep(0.001); lags.append((time.perf_counter() - t - 0.001) * 1000)

async def scenario(mode, fn):
    stop, lags = asyncio.Event(), []
    tk = asyncio.create_task(ticker(stop, lags))
    pool = ThreadPoolExecutor(2)
    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    for _ in range(100):
        if mode == "inline":
            fn(text)
        else:
            await loop.run_in_executor(pool, fn, text)
        await asyncio.sleep(0.002)
    wall = time.perf_counter() - t0
    stop.set(); await tk; pool.shutdown()
    lags.sort()
    print(json.dumps({"loop_test": mode, "fn": "encode" if fn is enc else "encode_batch", "wall_s": round(wall, 3),
                      "tick_lag_ms_p50": round(lags[len(lags)//2], 3), "p99": round(lags[int(len(lags)*0.99)], 3),
                      "max": round(lags[-1], 3), "ticks": len(lags)}))

async def main():
    for fn in (enc, encb):
        for mode in ("inline", "thread"):
            await scenario(mode, fn)
asyncio.run(main())
