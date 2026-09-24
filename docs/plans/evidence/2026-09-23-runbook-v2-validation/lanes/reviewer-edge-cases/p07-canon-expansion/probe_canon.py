"""P07: canonicalization expansion / non-ASCII cost on the INPUT path.

Part A (in-process, snapshot code): canonicalize() wall time, output length and expansion ratio
for crafted inputs that each fit in rvproto's 1 MiB body limit.
Part B (end-to-end): send the same bodies through rvproto (org-a) while a bystander tenant (org-b)
sends tiny requests to the same single worker; record status, elapsed, worker CPU, bystander max.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import tracemalloc

import aiohttp

SNAP, PIDFILE, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, SNAP)
from rvproto.detect.canon import canonicalize  # noqa: E402

TICK = os.sysconf("SC_CLK_TCK")
LIMIT = 1 << 20
HINDI = "नमस्ते दुनिया, यह एक परीक्षण वाक्य है और इसमें कोई गुप्त जानकारी नहीं है। "  # ~70 chars


def fit(unit: str, overhead: int = 200) -> str:
    """Largest repetition of unit whose JSON body (ensure_ascii=False) stays under 1 MiB."""
    per = len(json.dumps(unit, ensure_ascii=False).encode()) - 2
    return unit * ((LIMIT - overhead) // per)


INPUTS = {
    "ascii_1MiB": fit("hello world "),
    "hindi_plain_1MiB": fit(HINDI),
    "hindi_with_zwj_1MiB": fit(HINDI.replace("्", "्‍")),   # ZWJ after virama (legit Indic usage)
    "emoji_zwj_family_1MiB": fit("\U0001F468‍\U0001F469‍\U0001F467 "),
    "nfkc_expander_U+FDFA_1MiB": fit("ﷺ"),                  # 1 char -> 18 chars under NFKC
    "nfkc_expander_U+3316_1MiB": fit("㌖"),                  # ㌖ -> 6 chars
    "percent_4096_escapes": "%41" * 4096 + " tail",
    "percent_4097_escapes": "%41" * 4097 + " tail",
}


def cpu_s(pid: int) -> float:
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / TICK


def part_a() -> list[dict]:
    rows = []
    for name, s in INPUTS.items():
        tracemalloc.start()
        t = time.perf_counter()
        try:
            c = canonicalize(s, 4096)
            out_len, err = len(c.text), None
        except Exception as e:  # noqa: BLE001
            out_len, err = None, f"{type(e).__name__}: {e}"
        dt = time.perf_counter() - t
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append({"input": name, "in_chars": len(s), "in_bytes_utf8": len(s.encode()),
                     "canon_chars": out_len, "expansion": None if out_len is None else round(out_len / len(s), 2),
                     "canon_s": round(dt, 3), "peak_py_alloc_MB": round(peak / 2**20, 1), "error": err})
        print(json.dumps(rows[-1]), flush=True)
    return rows


async def part_b() -> list[dict]:
    launcher = int(open(PIDFILE).read().strip())
    pids = [int(k) for k in os.popen(f"pgrep -P {launcher}").read().split()]
    rows = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
        for name in ("ascii_1MiB", "hindi_plain_1MiB", "hindi_with_zwj_1MiB", "emoji_zwj_family_1MiB",
                     "nfkc_expander_U+FDFA_1MiB"):
            body = json.dumps({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": INPUTS[name]}]},
                              ensure_ascii=False).encode()
            lat: list[float] = []
            stop = asyncio.Event()

            async def bystander() -> None:
                while not stop.is_set():
                    t = time.perf_counter()
                    async with s.post("http://127.0.0.1:8480/v1/chat/completions",
                                      json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
                                      headers={"authorization": "Bearer sk-rv-org-b-0001", "x-synth-ttft-ms": "0",
                                               "x-synth-tokens": "2"}) as r:
                        await r.read()
                    lat.append(time.perf_counter() - t)
                    await asyncio.sleep(0.05)

            c0 = sum(cpu_s(p) for p in pids)
            bt = asyncio.create_task(bystander())
            await asyncio.sleep(0.3)
            t = time.perf_counter()
            async with s.post("http://127.0.0.1:8480/v1/chat/completions", data=body,
                              headers={"authorization": "Bearer sk-rv-org-a-0001", "content-type": "application/json",
                                       "x-synth-ttft-ms": "0", "x-synth-tokens": "2"}) as r:
                txt = await r.text()
                status = r.status
            dt = time.perf_counter() - t
            await asyncio.sleep(0.3)
            stop.set()
            await bt
            row = {"input": name, "body_bytes": len(body), "status": status,
                   "error_code": (json.loads(txt).get("error") or {}).get("code") if status >= 400 else None,
                   "elapsed_s": round(dt, 3), "worker_cpu_s": round(sum(cpu_s(p) for p in pids) - c0, 3),
                   "bystander_n": len(lat), "bystander_max_ms": round(max(lat) * 1e3, 1) if lat else None}
            rows.append(row)
            print(json.dumps(row), flush=True)
    return rows


if __name__ == "__main__":
    a = part_a()
    b = asyncio.run(part_b())
    json.dump({"part_a_inprocess": a, "part_b_e2e": b}, open(OUT, "w"), indent=1)
