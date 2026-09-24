#!/usr/bin/env python3
"""reviewer-observability: independent recomputation of one bench run from raw olg + synthprov records.

Written from the record schemas in SP/harness/USAGE.md, NOT by importing analyze.py / pbu_analyze.py,
so that a shared bug cannot make two computations agree.

Per measured request (olg ph == 2), classification (controller rules):
  policy_block : status in {400,403,422,451}, x-rv-disposition BLOCK, zero provider calls, no stage 'U'
                 (expected if corpus expects BLOCK or class is an attack class, else benign FP)
  qualified    : 200, [DONE]/valid JSON, no transport/stream error, exactly one provider call (provider 200),
                 content sha equal (or accepted output-REDACT), disposition ALLOW|REDACT|FLAG,
                 every required stage 'E' (sut mode)
  infra        : everything else (5xx/503 sheds, timeouts, incomplete, fail-closed BLOCK with a 'U' stage, ...)
Metrics (ns, each on one host's monotonic clock):
  total      = client end_ns - provider recv_to_last_ns
  first_byte = client first_ns - provider recv_to_first_ns          (OLD "first": first content byte)
  first_tok1 = arrival of first client event whose cum bytes >= provider emit_cum[0] - emit_ns[0]
               (RULE b: the event that completes provider token 1; sampled streams only)
  lag        = max_j (first client arrival covering provider piece j) - emit_ns[j]  (sampled streams only)
  hdr        = client hdr_ns - provider recv_to_first_ns  (SSE: headers leave the provider with token 1;
               the gateway forwards them without holdback -> client-side view of input phase + network)
Views:
  PUB   : lane definition: qualified only; SSE = max(total, first_byte, lag if sampled); JSON = total
  C     : rule (c) on PUB: cohort = offered - policy blocks; infra = +inf
  ABC   : rules (a)(b)(c) on the uniform hash subsample fnv1a64(rid) % MOD == 0 (the only requests with
          per-chunk data): SSE = max(total, first_tok1, lag); JSON = total; infra = +inf
  NOHOLD_C : total only (the lanes' "load knee" metric) with rule (c)
Percentiles: nearest rank over merged raw samples of all loadgens.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

import orjson

INF = float("inf")
MASK64 = 0xFFFFFFFFFFFFFFFF
BLOCK_STATUSES = {400, 403, 422, 451}
QUAL_DISP = {"ALLOW", "REDACT", "FLAG"}
ATTACK = {"secret", "injection", "attack", "malicious", "jailbreak"}
STAGES_DEFAULT = "canon,det,sem,resolve,dispatch,out,audit"


def fnv1a64(s: str) -> int:
    h = 14695981039346656037
    for b in s.encode():
        h ^= b
        h = (h * 1099511628211) & MASK64
    return h


def open_any(p: Path):
    s = str(p)
    if s.endswith(".zst"):
        from compression import zstd
        return zstd.open(s, "rb")
    if s.endswith(".gz"):
        import gzip
        return gzip.open(s, "rb")
    return open(s, "rb")


def find(d: Path, name: str) -> Path | None:
    for c in (name, name + ".zst", name + ".gz"):
        if (d / c).exists():
            return d / c
    return None


def pct(xs: list, p: float):
    if not xs:
        return None
    i = max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))
    return xs[i]


def dist(vals: list) -> dict:
    xs = sorted(vals)
    if not xs:
        return {"n": 0}
    f = lambda v: None if v is None else (v if v == INF else round(v / 1e6, 3))
    n_inf = sum(1 for v in xs if v == INF)
    fin = [v for v in xs if v != INF]
    return {"n": len(xs), "n_inf": n_inf, "p50": f(pct(xs, .5)), "p90": f(pct(xs, .9)), "p99": f(pct(xs, .99)),
            "p999": f(pct(xs, .999)), "max_finite": f(fin[-1]) if fin else None,
            "mean_finite": round(sum(fin) / len(fin) / 1e6, 3) if fin else None}


def stages_of(s):
    out = {}
    for part in (s or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def lag_piece(e, P, a, C):
    k, n, best, first = 0, len(a), None, None
    for j in range(len(e)):
        while k < n and C[k] < P[j]:
            k += 1
        if k == n:
            return None, None
        lag = a[k] - e[j]
        if j == 0:
            first = lag
        if best is None or lag > best:
            best = lag
    return best, first


def load_provider(dirs):
    by_rid, calls, nonce2rid, n = {}, Counter(), {}, 0
    for d in dirs:
        f = find(d, "records.jsonl")
        if f is None:
            continue
        with open_any(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = orjson.loads(line)
                n += 1
                rid = r["rid"]
                calls[rid] += 1
                keep = {k: r.get(k) for k in ("status", "stream", "recv_to_first_ns", "recv_to_last_ns",
                                               "content_sha256", "tool_args_sha256", "content_len", "inject",
                                               "inject_applied", "canary_hits", "tokens_out")}
                if r.get("sampled") and r.get("emit_ns"):
                    keep["emit_ns"], keep["emit_cum"] = r["emit_ns"], r["emit_cum"]
                by_rid[rid] = keep
                if r.get("nonce"):
                    nonce2rid[r["nonce"]] = rid
    return by_rid, calls, nonce2rid, n


INJECT_LEN = None


def inject_len(kind):
    global INJECT_LEN
    if INJECT_LEN is None:
        INJECT_LEN = {}
        cf = Path(__file__).resolve().parents[2] / "harness" / "shared" / "canaries.json"
        try:
            cs = {c["id"]: c for c in json.loads(cf.read_text())["canaries"]}
            INJECT_LEN["email"] = 1 + len(cs["out.email"]["value"])
            INJECT_LEN["aws"] = INJECT_LEN["split-aws"] = 1 + len(cs["out.aws"]["value"])
        except (OSError, KeyError):
            pass
    return INJECT_LEN.get(kind or "")


def expectations(corpus):
    exp = {}
    if corpus:
        with open(corpus, "rb") as fh:
            for line in fh:
                if line.strip():
                    e = orjson.loads(line)
                    exp[e["id"]] = e.get("expect") or {}
    return exp


def run(args):
    root = Path(args.run)
    lg_dirs = sorted(p for p in root.glob(args.lg_glob) if p.is_dir())
    prov_dirs = sorted(p for p in root.glob(args.prov_glob) if p.is_dir())
    by_rid, calls, nonce2rid, prov_n = load_provider(prov_dirs)
    exp = expectations(args.corpus)
    stages_req = [s for s in (args.stages or "").split(",") if s] if args.mode == "sut" else []
    cat = Counter()
    reasons = Counter()
    pub, pub_sse, pub_json, nohold, first_b, first_t1_s, first_b_s, lag_s, hdr_sse = ([] for _ in range(9))
    c_view, nohold_c, abc, abc_json, abc_sse, nohold_abc = [], [], [], [], [], []
    lone_first = Counter()
    blocks_lat = []
    offered = 0
    phase = Counter()
    sampled_sse_offered = 0
    sampled_sse_with_lag = 0
    mism = Counter()
    for d in lg_dirs:
        f = find(d, args.requests_name)
        if f is None:
            continue
        with open_any(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = orjson.loads(line)
                ph = c.get("ph", 2)
                phase[ph] += 1
                if ph != 2:
                    continue
                offered += 1
                rid = c["rid"]
                p = by_rid.get(rid)
                ncalls = calls.get(rid, 0)
                if p is None and c.get("nonce") in nonce2rid:
                    prid = nonce2rid[c["nonce"]]
                    p, ncalls = by_rid.get(prid), calls.get(prid, 0)
                status = c.get("status", 0)
                disp = (c.get("disp") or "").upper() if args.mode == "sut" else "ALLOW"
                st = stages_of(c.get("stages"))
                cls = c.get("cls") or "benign"
                in_sample = fnv1a64(rid) % args.sample_mod == 0
                # ---- classify
                k = None
                why = []
                if status in BLOCK_STATUSES and disp == "BLOCK" and p is None:
                    if any(v == "U" for v in st.values()):
                        k, why = "infra", ["block_on_unavailable"]
                    else:
                        e_in = (exp.get(c.get("cid")) or {}).get("input")
                        k = "block_expected" if (e_in == "BLOCK" or cls in ATTACK) else "block_fp"
                        blocks_lat.append(c.get("end_ns") or 0)
                else:
                    if c.get("err"):
                        why.append(c["err"])
                    if status != 200 and not c.get("err"):
                        why.append(f"http_{status}")
                    if not c.get("done_seen"):
                        why.append("incomplete")
                    if p is None:
                        why.append("unjoined")
                    else:
                        if ncalls != 1:
                            why.append(f"calls_{ncalls}")
                        if p.get("status") != 200:
                            why.append(f"prov_{p.get('status')}")
                        ok = (c.get("content_sha256") == p.get("content_sha256") and
                              (c.get("tool_args_sha256") or None) == (p.get("tool_args_sha256") or None))
                        if not ok:
                            il = inject_len(p.get("inject"))
                            leak = [h for h in (c.get("canary_hits") or []) if str(h).startswith("out.")]
                            lok = (il is not None and p.get("content_len") is not None and c.get("content_len") is not None
                                   and 0 <= c["content_len"] - (p["content_len"] - il) <= 64)
                            if not (p.get("inject_applied") and not leak and lok):
                                why.append("content_mismatch")
                    if args.mode == "sut":
                        if disp not in QUAL_DISP:
                            why.append(f"disp_{disp or 'missing'}")
                        for s in stages_req:
                            if st.get(s) != "E":
                                why.append(f"stage_{s}_{st.get(s) or 'missing'}")
                    k = "infra" if why else "qualified"
                cat[k] += 1
                if k == "infra":
                    for w in why:
                        reasons[w] += 1
                # ---- metrics
                m_total = m_first = m_t1 = m_lag = m_hdr = None
                if p is not None and status == 200:
                    if c.get("end_ns") and p.get("recv_to_last_ns") is not None:
                        m_total = c["end_ns"] - p["recv_to_last_ns"]
                    if c.get("first_ns") and p.get("recv_to_first_ns") is not None:
                        m_first = c["first_ns"] - p["recv_to_first_ns"]
                    if c.get("stream") and c.get("hdr_ns") and p.get("recv_to_first_ns") is not None:
                        m_hdr = c["hdr_ns"] - p["recv_to_first_ns"]
                    if c.get("sampled") and p.get("emit_ns") and c.get("arr_ns"):
                        P, C = p["emit_cum"], c["arr_cum"]
                        if P and C and C[-1] == P[-1]:
                            m_lag, m_t1 = lag_piece(p["emit_ns"], P, c["arr_ns"], C)
                            if C[0] == 1 and P[0] > 1:
                                lone_first["first_event_1B_token_gt_1B"] += 1
                            lone_first["mappable"] += 1
                        else:
                            lone_first["unmappable"] += 1
                stream = bool(c.get("stream"))
                if stream and in_sample:
                    sampled_sse_offered += 1
                if k == "qualified":
                    if m_total is None:
                        mism["qualified_without_total"] += 1
                        continue
                    if stream:
                        parts = [m_total] + ([m_first] if m_first is not None else []) + ([m_lag] if m_lag is not None else [])
                        v = max(parts)
                        pub_sse.append(v)
                        if m_first is not None:
                            first_b.append(m_first)
                        if m_hdr is not None:
                            hdr_sse.append(m_hdr)
                    else:
                        v = m_total
                        pub_json.append(v)
                    pub.append(v)
                    nohold.append(m_total)
                    c_view.append(v)
                    nohold_c.append(m_total)
                    if in_sample:
                        if stream:
                            if m_lag is not None and m_t1 is not None:
                                sampled_sse_with_lag += 1
                                w = max(m_total, m_t1, m_lag)
                                abc.append(w)
                                abc_sse.append(w)
                                first_t1_s.append(m_t1)
                                first_b_s.append(m_first)
                                lag_s.append(m_lag)
                            else:
                                mism["sampled_sse_qualified_without_perchunk"] += 1
                        else:
                            abc.append(m_total)
                            abc_json.append(m_total)
                        nohold_abc.append(m_total)
                elif k == "infra":
                    c_view.append(INF)
                    nohold_c.append(INF)
                    if in_sample:
                        abc.append(INF)
                        nohold_abc.append(INF)
                        (abc_sse if stream else abc_json).append(INF)
    res = {
        "run": str(root), "mode": args.mode, "offered": offered, "phase_counts": dict(phase),
        "provider_records": prov_n, "categories": dict(cat), "infra_reasons": dict(reasons.most_common(15)),
        "PUB_T_fw_addon": dist(pub), "PUB_sse": dist(pub_sse), "PUB_json": dist(pub_json),
        "PUB_nohold_T_addon_total": dist(nohold), "PUB_first_byte_sse": dist(first_b),
        "C_T_fw_addon": dist(c_view), "C_nohold": dist(nohold_c),
        "ABC_T_fw_addon": dist(abc), "ABC_sse": dist(abc_sse), "ABC_json": dist(abc_json), "ABC_nohold": dist(nohold_abc),
        "sample_first_tok1_sse": dist(first_t1_s), "sample_first_byte_sse": dist(first_b_s), "sample_lag": dist(lag_s),
        "hdr_addon_sse": dist(hdr_sse), "lone_first_event": dict(lone_first),
        "sampled_sse_offered": sampled_sse_offered, "sampled_sse_qualified_with_perchunk": sampled_sse_with_lag,
        "anomalies": dict(mism), "blocks_client_total": dist(blocks_lat),
    }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--mode", default="sut", choices=["sut", "direct"])
    ap.add_argument("--corpus")
    ap.add_argument("--stages", default=STAGES_DEFAULT)
    ap.add_argument("--sample-mod", type=int, default=10)
    ap.add_argument("--lg-glob", default="*/lg")
    ap.add_argument("--prov-glob", default="*/prov")
    ap.add_argument("--requests-name", default="requests.jsonl")
    ap.add_argument("--out")
    a = ap.parse_args()
    r = run(a)
    s = json.dumps(r, indent=1, default=str)
    if a.out:
        Path(a.out).write_text(s)
    print(s)


if __name__ == "__main__":
    sys.exit(main())
