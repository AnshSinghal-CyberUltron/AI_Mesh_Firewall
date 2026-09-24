"""P11/P12: (a) healthy stream > 350 s (16,000 tokens @ 25 ms ITL) through rvproto, with worker RSS
sampled every 10 s; (b) healthy stream whose provider is SILENT for 125 s before the first token
(reasoning-model TTFT shape) through rvproto (default RV_PROVIDER_TIMEOUT_S=120) and direct.
Records per stream: status, first-content time, end time, [DONE] seen, error frames, chars."""
import asyncio, json, os, sys, time, uuid
import aiohttp
OUT, PIDFILE = sys.argv[1], sys.argv[2]
async def stream(s, base, key, hdr, label):
    rid = f"p11-{label}-{uuid.uuid4().hex[:6]}"
    h = {"authorization": f"Bearer {key}", "x-request-id": rid, **hdr}
    body = {"model": "gpt-4o-mini", "stream": True, "messages": [{"role": "user", "content": "write a long story"}]}
    t0 = time.perf_counter(); first = None; chars = 0; done = False; errs = []; buf = b""
    try:
        async with s.post(f"{base}/v1/chat/completions", json=body, headers=h) as r:
            st = r.status
            async for data in r.content.iter_any():
                buf += data
                while b"\n\n" in buf:
                    ev, buf = buf.split(b"\n\n", 1)
                    if ev == b"data: [DONE]": done = True; continue
                    if not ev.startswith(b"data: "): continue
                    d = json.loads(ev[6:])
                    if "error" in d: errs.append(d["error"]); continue
                    for ch in d.get("choices") or []:
                        c = (ch.get("delta") or {}).get("content") or ""
                        if c and first is None: first = time.perf_counter() - t0
                        chars += len(c)
    except Exception as e:
        errs.append(f"client exception {type(e).__name__}: {e}")
        st = None
    return {"label": label, "rid": rid, "status": st, "t_first_content_s": None if first is None else round(first, 2),
            "t_end_s": round(time.perf_counter() - t0, 2), "done_seen": done, "chars": chars, "errors": errs}
async def rss_sampler(stop, samples):
    launcher = int(open(PIDFILE).read().strip())
    pids = [int(k) for k in os.popen(f"pgrep -P {launcher}").read().split()]
    t0 = time.perf_counter()
    while not stop.is_set():
        rss = 0
        for p in pids:
            for line in open(f"/proc/{p}/status"):
                if line.startswith("VmRSS:"): rss += int(line.split()[1])
        samples.append((round(time.perf_counter() - t0, 1), rss // 1024))
        await asyncio.sleep(10)
async def main():
    samples = []; stop = asyncio.Event()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1200, sock_read=600)) as s:
        smp = asyncio.create_task(rss_sampler(stop, samples))
        res = await asyncio.gather(
            stream(s, "http://127.0.0.1:8480", "sk-rv-org-b-0001", {"x-synth-tokens": "16000", "x-synth-itl-ms": "25", "x-synth-ttft-ms": "100"}, "long400s-via-gw"),
            stream(s, "http://127.0.0.1:8480", "sk-rv-org-b-0001", {"x-synth-tokens": "5", "x-synth-ttft-ms": "125000"}, "silent125s-via-gw"),
            stream(s, "http://127.0.0.1:18481", "direct", {"x-synth-tokens": "5", "x-synth-ttft-ms": "125000"}, "silent125s-direct"))
        stop.set(); await smp
    out = {"streams": res, "worker_rss_mb_samples": samples}
    print(json.dumps(out, indent=1)); json.dump(out, open(OUT, "w"), indent=1)
asyncio.run(main())
