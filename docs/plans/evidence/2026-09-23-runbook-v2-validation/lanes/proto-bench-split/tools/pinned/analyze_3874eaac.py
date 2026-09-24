#!/usr/bin/env python3
"""Recompute every harness metric from raw olg + synthprov JSONL (HARNESS_SPEC.md §3).

Re-runnable from the raw files alone: no network, no clocks, deterministic output.

Definitions (all DURATIONS on one host's monotonic clock; never cross-host absolute timestamps)
  client_total      = end_ns           (olg: send_start -> [DONE] arrival / JSON body complete)
  provider_total    = recv_to_last_ns  (synthprov: body fully read -> last content token ready)
  T_addon_total     = client_total - provider_total
  T_addon_first     = first_ns - recv_to_first_ns
  T_release_lag_max (sampled SSE requests) = max over provider content pieces j of
                      (arrival of the FIRST client event whose cumulative payload bytes cover piece j)
                      - (provider emission offset of piece j)
      i.e. the worst added delay suffered by any piece of content.  The spec's literal
      per-arrival formula (arrival k minus emission of the last piece fully covered at k) is also
      reported as T_release_lag_max_arrival; it under-reports when a gateway releases several held
      tokens together and over-reports (by ~ITL) when a gateway splits tokens across events
      (tests/test_analyze.py demonstrates both), so the per-piece form is the primary metric.
  T_fw_addon        = T_addon_total (JSON); max(T_addon_total, T_addon_first, T_release_lag_max
                      where sampled) (SSE).  GROSS: includes both network hops; the DIRECT run at
                      the same rate is the network/HTTP floor.  Floors are never subtracted per request.
  Negative T_addon_* values are NEVER clamped: any negative value marks the run INVALID.

Percentiles: nearest-rank over the merged raw samples of all loadgens (never averaged per worker).
Outcome strata (runbook T01 app_infra_error_rate; §1.1 "policy blocks are correctness traffic and are
reported separately"):
  QUALIFIED   HTTP 200, complete (SSE [DONE] / valid JSON), no transport/stream error, joined to exactly
              one provider call, provider 200, content sha equal to the provider's (or, for an expected
              output REDACT: the provider really injected, no output canary reached the client, and client
              length == provider length - injected piece + 0..64 replacement bytes), unique nonce; in
              --mode sut additionally disposition in --qualified-dispositions (default ALLOW,REDACT,FLAG;
              FLAG is transport-allow) and every --profile-stages stage EXECUTED.
  POLICY      a policy BLOCK: status in --block-statuses (default 400,403,422,451) AND block evidence
              (disposition BLOCK, or an error envelope matching --block-envelope-re: v1 content blocks are
              HTTP 400 code=content_filter). Split into expected (prompt labelled attack/secret),
              FALSE-POSITIVE (prompt labelled benign, no output injection) and other; FP rate per step;
              their client latency is reported separately. Not qualified, not infra errors.
  POLICY MISS (--policy enforce) a BLOCK-expected prompt served 200 (also a safety failure).
  INFRA ERROR everything else not qualified (5xx, non-policy 4xx, incomplete stream, timeout, 429/503
              shed, content mismatch, skipped stage, ...). Only these count toward --err-budget.
  Disposition and stages come from a pluggable extractor (--disposition-source auto|xrv|v1): xrv =
  x-rv-disposition / x-rv-stages recorded by olg; v1 = pipeline_trace.final_action + stages (EXECUTED iff
  action != skip), final_action / v1_stages fields, or resp_headers x-zeroshield-action, as written into an
  augmented requests.jsonl (olg itself does not capture v1 bodies or X-ZeroShield headers).
Rate step PASS (mode sut): p99 T_fw_addon < --slo-ms over the qualified cohort, infra errors <= --err-budget
  of offered, 0 schedule drops, 0 safety failures, run VALID.
Harness validation PASS (mode direct): 0 drops, 0 errors, provider p99 sched_err_last within
  --sched-tol-ms, loadgen CPU max < --cpu-max (measurement phase), run VALID.

Usage:
  analyze.py --client RUN/lg-* --provider RUN/prov-* --mode direct --out RUN/analysis
  analyze.py --client ... --provider ... --mode sut --profile-stages canon,det,sem,resolve,dispatch,out,audit \
             --corpus corpus.jsonl --policy enforce --out ...
"""
from __future__ import annotations

import argparse
import array
import bisect
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import orjson

    def loads(b):
        return orjson.loads(b)
except ImportError:  # pragma: no cover
    def loads(b):
        return json.loads(b)

MASK64 = 0xFFFFFFFFFFFFFFFF


# ----------------------------------------------------------------------------------------------
# hashing (mirrors internal/rv/content.go)
# ----------------------------------------------------------------------------------------------
def fnv1a64(s: str) -> int:
    h = 14695981039346656037
    for b in s.encode():
        h ^= b
        h = (h * 1099511628211) & MASK64
    return h


def splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
    return x ^ (x >> 31)


def is_sampled(rid: str, mod: int) -> bool:
    return mod > 0 and fnv1a64(rid) % mod == 0


# ----------------------------------------------------------------------------------------------
# statistics
# ----------------------------------------------------------------------------------------------
def pct_sorted(xs: list, p: float):
    """Nearest-rank percentile of an ascending list: the smallest value with at least p of the
    samples <= it.  p in [0,1]; p=1 -> max."""
    if not xs:
        return None
    i = max(0, min(len(xs) - 1, math.ceil(p * len(xs)) - 1))
    return xs[i]


def dist_ms(values_ns) -> dict:
    xs = sorted(values_ns)
    if not xs:
        return {"n": 0}
    f = lambda v: None if v is None else round(v / 1e6, 4)
    return {"n": len(xs), "min": f(xs[0]), "p50": f(pct_sorted(xs, 0.50)), "p90": f(pct_sorted(xs, 0.90)),
            "p99": f(pct_sorted(xs, 0.99)), "p999": f(pct_sorted(xs, 0.999)), "max": f(xs[-1]),
            "mean": round(sum(xs) / len(xs) / 1e6, 4)}


# ----------------------------------------------------------------------------------------------
# release lag
# ----------------------------------------------------------------------------------------------
def release_lag_per_piece(e, P, a, C):
    """Max over provider pieces j of (first client arrival covering P[j]) - e[j].
    e/P: provider emission offsets (ns from recv) / cumulative payload bytes per piece.
    a/C: client arrival offsets (ns from send_start) / cumulative payload bytes per content event.
    Returns None when some piece is never covered (client payload shorter than provider's)."""
    k, n, best = 0, len(a), None
    for j in range(len(e)):
        need = P[j]
        while k < n and C[k] < need:
            k += 1
        if k == n:
            return None
        lag = a[k] - e[j]
        if best is None or lag > best:
            best = lag
    return best


def release_lag_per_arrival(e, P, a, C):
    """HARNESS_SPEC §3 literal form: max over client arrivals k of a[k] - e[m], m = last provider
    piece fully covered by C[k].  Arrivals that cover no complete piece are skipped."""
    best = None
    for k in range(len(a)):
        m = bisect.bisect_right(P, C[k]) - 1
        if m < 0:
            continue
        lag = a[k] - e[m]
        if best is None or lag > best:
            best = lag
    return best


# ----------------------------------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------------------------------
def open_any(path):
    """Open a raw file that may be zstd (.zst) or gzip (.gz) compressed; returns a binary reader."""
    p = str(path)
    if p.endswith(".zst"):
        from compression import zstd  # Python >= 3.14
        return zstd.open(p, "rb")
    if p.endswith(".gz"):
        import gzip
        return gzip.open(p, "rb")
    return open(p, "rb")


def find_raw(d: Path, name: str) -> Path | None:
    for cand in (name, name + ".zst", name + ".gz"):
        if (d / cand).exists():
            return d / cand
    return None


def read_jsonl(path) -> list:
    with open_any(path) as fh:
        return [loads(l) for l in fh if l.strip()]


def expand(paths: list[str], name: str) -> list[Path]:
    out = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            f = find_raw(pp, name)
            if f is not None:
                out.append(f)
        elif pp.exists():
            out.append(pp)
    return out


PROV_KEYS = ("status", "stream", "recv_to_first_ns", "recv_to_last_ns", "sched_err_last_ns", "sched_err_max_ns",
             "write_max_ns", "content_sha256", "tool_args_sha256", "content_len", "canary_hits", "tokens_out",
             "inject", "inject_applied", "fault", "rid_src", "err", "client_gone", "body_len", "ttft_ms", "itl_ms",
             "sched_last_ns")


class Provider:
    def __init__(self):
        self.by_rid: dict[str, dict] = {}
        self.calls: Counter = Counter()
        self.nonce_to_rid: dict[str, str] = {}
        self.n = 0
        self.files: list[str] = []
        self.stats: list[dict] = []
        self.stat_dirs: list[str] = []
        self.cpu: list[dict] = []

    def load(self, paths: list[str]):
        for f in expand(paths, "records.jsonl"):
            self.files.append(str(f))
            with open_any(f) as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    r = loads(line)
                    self.n += 1
                    rid = r["rid"]
                    self.calls[rid] += 1
                    d = {k: r.get(k) for k in PROV_KEYS}
                    if r.get("sampled") and r.get("emit_ns"):
                        d["emit_ns"] = array.array("q", r["emit_ns"])
                        d["emit_cum"] = array.array("q", r["emit_cum"])
                    self.by_rid[rid] = d
                    if r.get("nonce"):
                        self.nonce_to_rid[r["nonce"]] = rid
        for p in paths:
            pp = Path(p)
            d = pp if pp.is_dir() else pp.parent
            s = d / "stats.json"
            if s.exists():
                self.stats.append(json.loads(s.read_text()))
                self.stat_dirs.append(str(d))
            c = find_raw(d, "cpu.jsonl")
            if c is not None:
                self.cpu.extend(read_jsonl(c))


NSTAT_KEYS = ("TcpRetransSegs", "TcpExtTCPFastRetrans", "TcpExtTCPLossProbes", "TcpExtTCPTimeouts", "TcpOutSegs", "TcpInSegs")


def nstat_delta(vm_run_dir: Path) -> dict | None:
    """TCP counter deltas (nstat.after - nstat.before) saved by deploy/step.sh in the VM's run dir."""
    b, a = vm_run_dir / "nstat.before", vm_run_dir / "nstat.after"
    if not (b.exists() and a.exists()):
        return None

    def load(f):
        out = {}
        for line in f.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                out[parts[0]] = int(parts[1])
        return out
    bb, aa = load(b), load(a)
    return {k: aa.get(k, 0) - bb.get(k, 0) for k in NSTAT_KEYS}


def load_expectations(corpus: str | None) -> dict:
    exp = {}
    if not corpus:
        return exp
    with open(corpus, "rb") as fh:
        for line in fh:
            if line.strip():
                e = loads(line)
                exp[e["id"]] = (e.get("expect") or {}, e.get("canaries") or [])
    return exp


CLASS_DEFAULT = {"benign": "ALLOW", "pii": "REDACT", "secret": "BLOCK", "injection": "BLOCK"}
ATTACK_CLASSES = {"secret", "injection", "attack", "malicious", "jailbreak"}
# Policy blocks are recognised by status AND block evidence. v1 turns CONTENT blocks into HTTP 400 with
# error.code=content_filter / type=invalid_request_error (gateway main.py _resolve_content_block_status,
# GATEWAY_BLOCK_STATUS default "400"); auth/actor blocks keep 403 and their own code; a size rejection
# carries context_length_exceeded (not a policy judgement). So a bare 4xx is NOT a policy block.
BLOCK_STATUSES_DEFAULT = "400,403,422,451"
BLOCK_ENVELOPE_RE_DEFAULT = r"(?i)content_filter|content_polic|polic(y|ies)_|blocked|block_|guard|violation|unsafe|threat"
QUALIFIED_DISPOSITIONS_DEFAULT = "ALLOW,REDACT,FLAG"
DISP_ALIASES = {"ALLOWED": "ALLOW", "PASS": "ALLOW", "REDACTED": "REDACT", "BLOCKED": "BLOCK",
                "FLAGGED": "FLAG", "REWRITTEN": "REWRITE"}
REDACT_REPLACEMENT_MAX = 64  # bytes a redaction placeholder may occupy


def norm_disp(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().upper()
    return DISP_ALIASES.get(s, s) if s else None


def _pairs(text: str) -> dict:
    out = {}
    for part in (text or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _extract_xrv(c: dict):
    """x-rv-disposition / x-rv-stages response headers as recorded by olg ("canon:E,det:E,...")."""
    return norm_disp(c.get("disp")), (_pairs(c["stages"]) if c.get("stages") else None)


def _extract_v1(c: dict):
    """v1 (gateway at 52a584e9): disposition = pipeline_trace.final_action (allow|redact|block|flag|rewrite),
    stage EXECUTED iff its action != "skip"; fallbacks: record fields final_action / v1_stages
    ("auth:allow,...,output_guardrail:skip") / resp_headers["x-zeroshield-action"] (redacted|flag).
    olg does not capture v1 bodies or X-ZeroShield headers itself: these fields come from an
    augmented copy of requests.jsonl (e.g. joined from v1's audit events)."""
    pt = c.get("pipeline_trace")
    if isinstance(pt, str):
        try:
            pt = json.loads(pt)
        except ValueError:
            pt = None
    disp, stages = None, None
    if isinstance(pt, dict):
        disp = norm_disp(pt.get("final_action"))
        if isinstance(pt.get("stages"), list):
            stages = {}
            for st in pt["stages"]:
                if isinstance(st, dict) and st.get("name"):
                    act = str(st.get("action") or "").strip().lower()
                    stages[str(st["name"])] = "S" if act in ("", "skip", "skipped") else "E"
    if disp is None and c.get("final_action"):
        disp = norm_disp(c["final_action"])
    if stages is None and c.get("v1_stages"):
        stages = {k: ("S" if v.lower() in ("", "skip", "skipped") else "E") for k, v in _pairs(c["v1_stages"]).items()}
    if disp is None:
        hdrs = {str(k).lower(): v for k, v in (c.get("resp_headers") or {}).items()}
        zs = str(hdrs.get("x-zeroshield-action") or "").strip().lower()
        if zs:
            disp = {"redacted": "REDACT", "flag": "FLAG", "blocked": "BLOCK", "block": "BLOCK"}.get(zs, norm_disp(zs))
    return disp, stages


DISPOSITION_EXTRACTORS = {"xrv": _extract_xrv, "v1": _extract_v1}


def extract_disposition(c: dict, source: str):
    """Pluggable disposition/stage source. auto = x-rv-* fields when present, else the v1 fields."""
    if source == "auto":
        source = "xrv" if (c.get("disp") or c.get("stages")) else "v1"
    return DISPOSITION_EXTRACTORS[source](c)


def is_policy_block(c: dict, disp: str | None, args) -> bool:
    """Policy BLOCK = a block status AND block evidence (BLOCK disposition, or an error envelope that
    names a content/policy block). Anything else non-200 is an infra error."""
    if c.get("status", 0) not in args.block_status_set:
        return False
    if disp == "BLOCK":
        return True
    return bool(args.block_re.search(c.get("err_detail") or ""))


def _inject_lengths() -> dict:
    """Byte length of the piece synthprov inserts per inject kind (" " + canary value)."""
    try:
        cs = {c["id"]: c for c in json.loads((Path(__file__).resolve().parent / "shared" / "canaries.json").read_text())["canaries"]}
    except OSError:
        return {}
    out = {}
    if "out.email" in cs:
        out["email"] = 1 + len(cs["out.email"]["value"])
    if "out.aws" in cs:
        out["aws"] = out["split-aws"] = 1 + len(cs["out.aws"]["value"])
    return out


INJECT_LEN = _inject_lengths()


# ----------------------------------------------------------------------------------------------
# per-request evaluation
# ----------------------------------------------------------------------------------------------
def evaluate(c: dict, p: dict | None, calls: int, args, exp: dict, stages_req: list[str], dup_nonce: bool):
    """Classify one offered request. Returns (outcome, reasons, metrics, safety_reasons), outcome in
      qualified | policy_block_expected | policy_block_fp | policy_block_other | policy_miss | infra_error
    (runbook T01 / §1.1: policy blocks are correctness traffic reported separately; only infra errors
    count toward the error budget; latency percentiles use the qualified cohort)."""
    reasons: list[str] = []
    safety: list[str] = []
    m: dict = {}
    cls = c.get("cls") or "benign"
    e_input, e_output = "ALLOW", "ALLOW"
    if args.policy == "enforce":
        ce = exp.get(c.get("cid"))
        if ce:
            e_input = ce[0].get("input", CLASS_DEFAULT.get(cls, "ALLOW"))
            e_output = ce[0].get("output", "ALLOW")
        else:
            e_input = CLASS_DEFAULT.get(cls, "ALLOW")
            e_output = "REDACT" if c.get("inject") in ("email", "aws", "split-aws") else "ALLOW"
    disp, stages = extract_disposition(c, args.disposition_source)

    # ---- safety (enforce policy only) ----
    if args.policy == "enforce":
        if e_input == "BLOCK" and p is not None:
            safety.append("block_expected_but_provider_called")
        if p is not None and p.get("canary_hits"):
            if e_input in ("REDACT", "BLOCK"):
                safety.append("canary_reached_provider:" + ",".join(p["canary_hits"]))
        out_hits = [h for h in (c.get("canary_hits") or []) if h.startswith("out.")]
        if out_hits and e_output == "REDACT":
            safety.append("output_canary_reached_client:" + ",".join(out_hits))
    else:
        if p is not None and p.get("canary_hits"):
            m["canary_at_provider"] = p["canary_hits"]

    status = c.get("status", 0)
    # ---- policy stratum ----
    if is_policy_block(c, disp, args):
        if e_input == "BLOCK" or cls in ATTACK_CLASSES:
            kind = "policy_block_expected"
        elif cls == "benign" and not c.get("inject"):
            kind = "policy_block_fp"
        else:
            kind = "policy_block_other"
        if c.get("end_ns"):
            m["block_client_total"] = c["end_ns"]
        return kind, [], m, safety
    if args.policy == "enforce" and e_input == "BLOCK":
        if status == 200:
            return "policy_miss", ["expected_block_not_blocked"], m, safety
        return "infra_error", [c.get("err") or f"http_{status}"], m, safety

    # ---- qualified vs infra error ----
    if c.get("err"):
        reasons.append(c["err"])
    if status != 200:
        if not c.get("err"):
            reasons.append(f"http_{status}")
    if not c.get("done_seen"):
        reasons.append("incomplete")
    if p is None:
        reasons.append("unjoined")
    else:
        if calls != 1:
            reasons.append(f"provider_calls_{calls}")
        if p.get("status") != 200:
            reasons.append(f"provider_status_{p.get('status')}")
        content_ok = (c.get("content_sha256") == p.get("content_sha256")
                      and (c.get("tool_args_sha256") or None) == (p.get("tool_args_sha256") or None))
        if not content_ok:
            # Output REDACT (provider-injected canary) legitimately changes the content.  SSE headers
            # are sent before content, so the disposition header cannot carry an output decision;
            # accept iff the provider really injected, no output canary reached the client, and the
            # client content length equals provider length minus the injected piece plus a bounded
            # replacement (catches truncated / mangled streams).
            no_leak = not [h for h in (c.get("canary_hits") or []) if h.startswith("out.")]
            inj_len = INJECT_LEN.get(p.get("inject") or "", None)
            len_ok = (inj_len is not None and p.get("content_len") is not None and c.get("content_len") is not None
                      and 0 <= c["content_len"] - (p["content_len"] - inj_len) <= REDACT_REPLACEMENT_MAX)
            if e_output == "REDACT" and p.get("inject_applied") and no_leak and len_ok:
                m["output_redacted"] = True
            else:
                reasons.append("content_mismatch")
        # metrics
        if c.get("end_ns") and p.get("recv_to_last_ns") is not None and status == 200:
            m["addon_total"] = c["end_ns"] - p["recv_to_last_ns"]
        if c.get("first_ns") and p.get("recv_to_first_ns") is not None and status == 200:
            m["addon_first"] = c["first_ns"] - p["recv_to_first_ns"]
        if c.get("sampled") and "emit_ns" in p and c.get("arr_ns"):
            e, P = p["emit_ns"], p["emit_cum"]
            a, C = c["arr_ns"], c["arr_cum"]
            if P and C and C[-1] == P[-1]:
                m["lag_piece"] = release_lag_per_piece(e, P, a, C)
                m["lag_arrival"] = release_lag_per_arrival(e, P, a, C)
            else:
                m["lag_unmappable"] = True
        m["sched_err_last"] = p.get("sched_err_last_ns")
        m["sched_err_max"] = p.get("sched_err_max_ns")
        m["write_max"] = p.get("write_max_ns")
    if dup_nonce:
        reasons.append("duplicate_nonce")
    if args.mode == "sut":
        if disp not in args.qualified_disp_set:
            reasons.append(f"disposition_{disp or 'missing'}")
        for st in stages_req:
            v = (stages or {}).get(st)
            if v != "E":
                reasons.append(f"stage_{st}_{v or 'missing'}")
    if e_input == "REDACT" and args.policy == "enforce" and args.mode == "sut":
        if disp != "REDACT":
            reasons.append("redact_expected_disposition_" + (disp or "missing"))
    # T_fw_addon
    if "addon_total" in m:
        if c.get("stream"):
            parts = [m["addon_total"]]
            if "addon_first" in m:
                parts.append(m["addon_first"])
            if m.get("lag_piece") is not None:
                parts.append(m["lag_piece"])
            m["fw_addon"] = max(parts)
        else:
            m["fw_addon"] = m["addon_total"]
    if reasons:
        return "infra_error", reasons, m, safety
    return "qualified", [], m, safety


# ----------------------------------------------------------------------------------------------
# main analysis
# ----------------------------------------------------------------------------------------------
def prepare_args(args):
    """Derived settings (tolerates callers that built args without the newer options)."""
    args.disposition_source = getattr(args, "disposition_source", None) or "auto"
    if args.disposition_source not in ("auto", *DISPOSITION_EXTRACTORS):
        raise SystemExit(f"--disposition-source must be auto|{'|'.join(DISPOSITION_EXTRACTORS)}")
    args.block_status_set = {int(x) for x in (getattr(args, "block_statuses", None) or BLOCK_STATUSES_DEFAULT).split(",") if x}
    args.block_re = re.compile(getattr(args, "block_envelope_re", None) or BLOCK_ENVELOPE_RE_DEFAULT)
    args.qualified_disp_set = {x.strip().upper() for x in
                               (getattr(args, "qualified_dispositions", None) or QUALIFIED_DISPOSITIONS_DEFAULT).split(",") if x}
    return args


def analyze(args) -> dict:
    prepare_args(args)
    prov = Provider()
    prov.load(args.provider)
    exp = load_expectations(args.corpus)
    stages_req = [s for s in (args.profile_stages or "").split(",") if s]
    phases = {int(x) for x in args.phases.split(",")}

    client_files = expand(args.client, "requests.jsonl")
    manifests = []
    for p in args.client:
        pp = Path(p)
        d = pp if pp.is_dir() else pp.parent
        mf = d / "manifest.json"
        cf = find_raw(d, "cpu.jsonl")
        manifests.append({"dir": str(d), "manifest": json.loads(mf.read_text()) if mf.exists() else None,
                          "cpu": read_jsonl(cf) if cf is not None else []})

    # pass 1: nonce duplicates across all loadgens (unique-prompt requirement)
    nonce_count: Counter = Counter()
    total_records = 0
    for f in client_files:
        with open_any(f) as fh:
            for line in fh:
                if line.strip():
                    nonce_count[loads(line).get("nonce")] += 1
                    total_records += 1

    outcomes: Counter = Counter()
    reasons_c: Counter = Counter()
    safety_c: Counter = Counter()
    safety_examples: list = []
    error_examples: list = []
    late, addon_total, addon_first, lag_piece, lag_arrival, fw = [], [], [], [], [], []
    fw_json, fw_sse = [], []
    ttft_client, sched_last, sched_max, write_max = [], [], [], []
    by_class: Counter = Counter()
    drops = 0
    negatives = Counter()
    lag_unmappable = 0
    sampled_joined = 0
    joined = 0
    join_by_nonce = 0
    offered = 0
    phase_counts: Counter = Counter()
    per_sec: dict = defaultdict(lambda: [0, 0, 0])  # sched second -> offered, qualified, infra errors
    block_lat: dict = defaultdict(list)  # policy cohort: client total (end_ns) per kind
    benign_offered = 0
    seen_rids = set()
    for f in client_files:
        with open_any(f) as fh:
            for line in fh:
                if not line.strip():
                    continue
                c = loads(line)
                ph = c.get("ph", 2)
                phase_counts[ph] += 1
                if ph not in phases:
                    continue
                offered += 1
                by_class[c.get("cls") or "?"] += 1
                if (c.get("cls") or "benign") == "benign" and not c.get("inject"):
                    benign_offered += 1
                late.append(c.get("late_ns", 0))
                if c.get("late_ns", 0) > args.drop_ms * 1e6 or c.get("err") == "inflight_cap":
                    drops += 1
                rid = c["rid"]
                seen_rids.add(rid)
                p = prov.by_rid.get(rid)
                calls = prov.calls.get(rid, 0)
                if p is None and c.get("nonce") in prov.nonce_to_rid:
                    prid = prov.nonce_to_rid[c["nonce"]]
                    p = prov.by_rid.get(prid)
                    calls = prov.calls.get(prid, 0)
                    join_by_nonce += 1
                if p is not None:
                    joined += 1
                outcome, reasons, m, safety = evaluate(c, p, calls, args, exp, stages_req,
                                                        nonce_count[c.get("nonce")] > 1)
                outcomes[outcome] += 1
                sec = int(c.get("sched_ns", 0) // 1_000_000_000)
                per_sec[sec][0] += 1
                if outcome == "qualified":
                    per_sec[sec][1] += 1
                elif outcome == "infra_error":
                    per_sec[sec][2] += 1
                if "block_client_total" in m:
                    block_lat[outcome].append(m["block_client_total"])
                for r in reasons:
                    reasons_c[r.split(":")[0]] += 1
                if reasons and len(error_examples) < 20:
                    error_examples.append({"rid": rid, "reasons": reasons, "status": c.get("status"),
                                           "err_detail": c.get("err_detail")})
                for s in safety:
                    safety_c[s.split(":")[0]] += 1
                    if len(safety_examples) < 20:
                        safety_examples.append({"rid": rid, "cls": c.get("cls"), "cid": c.get("cid"), "safety": s})
                for k in ("addon_total", "addon_first", "lag_piece", "lag_arrival"):
                    v = m.get(k)
                    if v is not None and v < 0:
                        negatives[k] += 1
                if m.get("lag_unmappable"):
                    lag_unmappable += 1
                if m.get("lag_piece") is not None:
                    sampled_joined += 1
                if m.get("sched_err_last") is not None:
                    sched_last.append(m["sched_err_last"])
                    sched_max.append(m["sched_err_max"])
                    write_max.append(m["write_max"] or 0)
                if outcome == "qualified":
                    addon_total.append(m["addon_total"])
                    if "addon_first" in m:
                        addon_first.append(m["addon_first"])
                    if m.get("lag_piece") is not None:
                        lag_piece.append(m["lag_piece"])
                        lag_arrival.append(m["lag_arrival"])
                    fw.append(m["fw_addon"])
                    (fw_sse if c.get("stream") else fw_json).append(m["fw_addon"])
                    if c.get("stream") and c.get("first_ns"):
                        ttft_client.append(c["first_ns"])

    # provider records whose rid never appeared on the client (e.g. retries, other runs)
    prov_orphans = sum(1 for rid in prov.by_rid if rid not in seen_rids) if args.phases == "0,1,2" else None
    dup_calls = sum(1 for rid, n in prov.calls.items() if n > 1)

    errors = outcomes["infra_error"]
    err_rate = errors / offered if offered else None
    policy_blocks = outcomes["policy_block_expected"] + outcomes["policy_block_fp"] + outcomes["policy_block_other"]
    policy = {
        "blocks_total": policy_blocks,
        "expected_blocks": outcomes["policy_block_expected"],
        "false_positive_blocks": outcomes["policy_block_fp"],
        "other_blocks": outcomes["policy_block_other"],
        "benign_offered": benign_offered,
        "false_positive_rate": (outcomes["policy_block_fp"] / benign_offered) if benign_offered else None,
        "policy_misses": outcomes["policy_miss"],
        "latency_client_total_ms": {k: dist_ms(v) for k, v in block_lat.items()},
        "block_rule": {"statuses": sorted(args.block_status_set), "envelope_re": args.block_re.pattern},
    }
    valid_reasons = []
    for k, v in negatives.items():
        valid_reasons.append(f"negative_{k}={v}")
    # completeness: every scheduled request must be recorded
    completeness = []
    for mf in manifests:
        man = mf["manifest"]
        if man is None:
            valid_reasons.append(f"missing_manifest:{mf['dir']}")
            continue
        cnt = man.get("counts", {})
        if cnt.get("scheduled") != cnt.get("recorded"):
            valid_reasons.append(f"records_incomplete:{mf['dir']}:{cnt.get('scheduled')}!={cnt.get('recorded')}")
        completeness.append({"dir": mf["dir"], "scheduled": cnt.get("scheduled"), "recorded": cnt.get("recorded"),
                             "interrupted": man.get("interrupted")})
        if man.get("interrupted"):
            valid_reasons.append(f"interrupted:{mf['dir']}")
    if total_records != sum(phase_counts.values()):
        valid_reasons.append("record_count_mismatch")

    # loadgen CPU during the measurement phase
    lg_cpu = []
    offered_rate = 0.0
    for mf in manifests:
        man = mf["manifest"] or {}
        conf = man.get("config", {})
        offered_rate += conf.get("rate", 0) or 0
        start = (man.get("epoch_offset_s") or 0) + (conf.get("ramp_s") or 0) + (conf.get("warmup_s") or 0)
        end = (man.get("epoch_offset_s") or 0) + (conf.get("ramp_s") or 0) + (conf.get("warmup_s") or 0) + (conf.get("duration_s") or 0)
        busy = [s["busy"] for s in mf["cpu"] if start <= s["t"] <= end]
        lg_cpu.append({"dir": mf["dir"], "samples": len(busy), "busy_max": max(busy) if busy else None,
                       "busy_mean": round(sum(busy) / len(busy), 2) if busy else None,
                       "machine": ((man.get("host") or {}).get("gce") or {}).get("machine-type"),
                       "zone": ((man.get("host") or {}).get("gce") or {}).get("zone")})
    prov_cpu_busy = sorted(s["busy"] for s in prov.cpu)
    # instrument health: GC cycles and scheduler latency per process, TCP retransmits per VM
    health = []
    for mf in manifests:
        rt = (mf["manifest"] or {}).get("runtime") or {}
        health.append({"proc": "olg", "dir": mf["dir"], "gc_cycles": rt.get("/gc/cycles/total:gc-cycles"),
                       "sched_latency_max_ms": (rt.get("/sched/latencies:seconds") or {}).get("max_ms"),
                       "tcp": nstat_delta(Path(mf["dir"]).parent)})
    for st, d in zip(prov.stats, prov.stat_dirs):
        rt = st.get("runtime") or {}
        health.append({"proc": "synthprov", "dir": d, "gc_cycles": rt.get("/gc/cycles/total:gc-cycles"),
                       "sched_latency_max_ms": (rt.get("/sched/latencies:seconds") or {}).get("max_ms"),
                       "tcp": nstat_delta(Path(d).parent)})
    measure_s = None
    if manifests and manifests[0]["manifest"]:
        measure_s = manifests[0]["manifest"]["config"].get("duration_s")

    p99_fw = dist_ms(fw).get("p99")
    sched_d = dist_ms(sched_last)
    lg_cpu_max = max([x["busy_max"] for x in lg_cpu if x["busy_max"] is not None], default=None)
    verdict = {}
    if args.mode == "sut":
        checks = {
            "p99_T_fw_addon_lt_slo": p99_fw is not None and p99_fw < args.slo_ms,
            "infra_error_rate_le_budget": err_rate is not None and err_rate <= args.err_budget,
            "zero_schedule_drops": drops == 0,
            "zero_safety_failures": sum(safety_c.values()) == 0,
            "run_valid": not valid_reasons,
            "qualified_nonempty": len(fw) > 0,
        }
    else:
        checks = {
            "zero_schedule_drops": drops == 0,
            "zero_errors": errors == 0 and policy_blocks == 0 and outcomes["policy_miss"] == 0,
            "provider_p99_sched_err_within_tol": sched_d.get("p99") is not None and abs(sched_d["p99"]) <= args.sched_tol_ms,
            "loadgen_cpu_max_lt_limit": lg_cpu_max is not None and lg_cpu_max < args.cpu_max,
            "run_valid": not valid_reasons,
            "all_joined": joined == offered,
        }
    verdict = {"pass": all(checks.values()), "checks": checks}

    summary = {
        "mode": args.mode, "policy": args.policy, "phases": sorted(phases),
        "inputs": {"client_files": [str(f) for f in client_files], "provider_files": prov.files,
                   "corpus": args.corpus},
        "offered": offered, "offered_rate_config": offered_rate, "measure_s": measure_s,
        "achieved_offered_rps": round(offered / measure_s, 2) if measure_s else None,
        "qualified": outcomes["qualified"], "expected_blocks": outcomes["policy_block_expected"], "errors": errors,
        "infra_errors": errors, "policy": policy, "outcomes": dict(outcomes),
        "disposition_source": args.disposition_source,
        "qualified_dispositions": sorted(args.qualified_disp_set),
        "qualified_rps": round(outcomes["qualified"] / measure_s, 2) if measure_s else None,
        "error_rate": err_rate, "error_reasons": dict(reasons_c.most_common()),
        "error_examples": error_examples,
        "safety_failures": sum(safety_c.values()), "safety_reasons": dict(safety_c), "safety_examples": safety_examples,
        "schedule": {"drops": drops, "drop_threshold_ms": args.drop_ms, "lateness": dist_ms(late)},
        "join": {"joined": joined, "by_nonce_fallback": join_by_nonce, "provider_records": prov.n,
                 "provider_rids_with_multiple_calls": dup_calls, "provider_orphans": prov_orphans},
        "classes": dict(by_class), "phase_counts": {str(k): v for k, v in sorted(phase_counts.items())},
        "T_addon_total": dist_ms(addon_total),
        "T_addon_first": dist_ms(addon_first),
        "T_release_lag_max": dist_ms(lag_piece),
        "T_release_lag_max_arrival": dist_ms(lag_arrival),
        "T_fw_addon": dist_ms(fw), "T_fw_addon_sse": dist_ms(fw_sse), "T_fw_addon_json": dist_ms(fw_json),
        "release_lag_sampled_joined": sampled_joined, "release_lag_unmappable": lag_unmappable,
        "client_ttft_sse": dist_ms(ttft_client),
        "provider_sched_err_last": sched_d, "provider_sched_err_max_per_stream": dist_ms(sched_max),
        "provider_write_max": dist_ms(write_max),
        "loadgen_cpu": lg_cpu,
        "provider_cpu": {"samples": len(prov_cpu_busy), "busy_max": prov_cpu_busy[-1] if prov_cpu_busy else None,
                         "busy_p95": pct_sorted(prov_cpu_busy, 0.95)},
        "provider_stats": prov.stats,
        "instrument_health": health,
        "completeness": completeness,
        "valid": not valid_reasons, "invalid_reasons": valid_reasons,
        "per_second": {str(k): v for k, v in sorted(per_sec.items())} if args.per_second else None,
        "thresholds": {"slo_ms": args.slo_ms, "err_budget": args.err_budget, "drop_ms": args.drop_ms,
                       "sched_tol_ms": args.sched_tol_ms, "cpu_max": args.cpu_max},
        "verdict": verdict,
    }
    return summary


def fmt(d: dict) -> str:
    if not d or not d.get("n"):
        return "n=0"
    return (f"n={d['n']} p50={d['p50']} p90={d['p90']} p99={d['p99']} p99.9={d['p999']} max={d['max']} "
            f"mean={d['mean']}")


def write_md(s: dict, path: Path):
    L = []
    v = s["verdict"]
    L.append(f"# Harness analysis ({s['mode']}, policy={s['policy']}) — {'PASS' if v['pass'] else 'FAIL'}")
    L.append("")
    L.append("| check | result |")
    L.append("|---|---|")
    for k, ok in v["checks"].items():
        L.append(f"| {k} | {'PASS' if ok else 'FAIL'} |")
    L.append("")
    L.append(f"- offered: {s['offered']} over {s['measure_s']} s = {s['achieved_offered_rps']} req/s "
             f"(configured {s['offered_rate_config']})")
    pol = s.get("policy") or {}
    L.append(f"- qualified: {s['qualified']} ({s['qualified_rps']} req/s; dispositions {s.get('qualified_dispositions')}, "
             f"source {s.get('disposition_source')})")
    L.append(f"- infra errors (the error-budget gate): {s['errors']} (rate {s['error_rate']}); reasons: {s['error_reasons']}")
    L.append(f"- policy stratum (not qualified, not infra errors): expected blocks {pol.get('expected_blocks')}, "
             f"FALSE-POSITIVE blocks {pol.get('false_positive_blocks')} of {pol.get('benign_offered')} benign "
             f"(FP rate {pol.get('false_positive_rate')}), other blocks {pol.get('other_blocks')}, "
             f"policy misses {pol.get('policy_misses')}")
    for k, d in (pol.get("latency_client_total_ms") or {}).items():
        L.append(f"- policy cohort latency {k} (client total ms): {fmt(d)}")
    L.append(f"- safety failures: {s['safety_failures']} {s['safety_reasons']}")
    L.append(f"- schedule drops (>{s['schedule']['drop_threshold_ms']} ms): {s['schedule']['drops']}; "
             f"lateness ms: {fmt(s['schedule']['lateness'])}")
    L.append(f"- join: {s['join']}")
    L.append(f"- valid: {s['valid']} {s['invalid_reasons']}")
    L.append("")
    L.append("| metric (ms) | distribution |")
    L.append("|---|---|")
    for k in ("T_addon_total", "T_addon_first", "T_release_lag_max", "T_release_lag_max_arrival", "T_fw_addon",
              "T_fw_addon_sse", "T_fw_addon_json", "client_ttft_sse", "provider_sched_err_last",
              "provider_sched_err_max_per_stream", "provider_write_max"):
        L.append(f"| {k} | {fmt(s[k])} |")
    L.append("")
    L.append(f"- release lag: {s['release_lag_sampled_joined']} sampled streams joined, "
             f"{s['release_lag_unmappable']} unmappable (content length differs)")
    L.append(f"- loadgen CPU (measurement phase): {s['loadgen_cpu']}")
    L.append(f"- provider CPU: {s['provider_cpu']}")
    L.append(f"- completeness: {s['completeness']}")
    for h in s.get("instrument_health") or []:
        L.append(f"- health {h['proc']} {Path(h['dir']).parent.name}: gc_cycles={h['gc_cycles']} "
                 f"sched_latency_max_ms={h['sched_latency_max_ms']} tcp={h['tcp']}")
    path.write_text("\n".join(L) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", nargs="+", required=True, help="olg output dirs or requests.jsonl files")
    ap.add_argument("--provider", nargs="+", required=True, help="synthprov dirs or records.jsonl files")
    ap.add_argument("--mode", choices=["direct", "sut"], required=True)
    ap.add_argument("--policy", choices=["none", "enforce", "monitor"], default="none")
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--profile-stages", default="")
    ap.add_argument("--disposition-source", default="auto", choices=["auto", "xrv", "v1"],
                    help="where disposition/stages come from (x-rv-* headers or v1 pipeline_trace fields)")
    ap.add_argument("--qualified-dispositions", default=QUALIFIED_DISPOSITIONS_DEFAULT)
    ap.add_argument("--block-statuses", default=BLOCK_STATUSES_DEFAULT)
    ap.add_argument("--block-envelope-re", default=BLOCK_ENVELOPE_RE_DEFAULT)
    ap.add_argument("--phases", default="2", help="olg phases to include (0 ramp,1 warm,2 measure)")
    ap.add_argument("--slo-ms", type=float, default=20.0)
    ap.add_argument("--err-budget", type=float, default=0.001)
    ap.add_argument("--drop-ms", type=float, default=5.0)
    ap.add_argument("--sched-tol-ms", type=float, default=1.0)
    ap.add_argument("--cpu-max", type=float, default=70.0)
    ap.add_argument("--per-second", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    s = analyze(args)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(s, indent=1, default=str) + "\n")
    write_md(s, out / "summary.md")
    print((out / "summary.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
