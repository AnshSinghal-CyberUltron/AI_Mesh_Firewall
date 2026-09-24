"""Unit tests for analyze.py metric math, qualification, validity and verdict logic.

Run: python -m unittest discover -s tests -v   (from the harness directory)
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402

MS = 1_000_000


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def synth_stream(n=100, ttft=150 * MS, itl=20 * MS, net=300_000, pre=0, hold_at=None, hold=0,
                 coalesce_next=False, split=False, piece=" word"):
    """Provider emission offsets e (from recv) and client arrivals a (from send_start) for one SSE
    stream through a pass-through element.  Request path = net/2 + pre; response path = net/2.
    hold_at (1-based piece index) delays that piece by `hold`; later pieces are forwarded when
    available (like rvproxy).  coalesce_next: the held piece and the next one arrive together.
    split: every piece arrives in two halves, the second half 1 ms later."""
    req_path = net // 2 + pre
    e = [ttft + j * itl for j in range(n)]
    P = [(j + 1) * len(piece) for j in range(n)]
    a, C = [], []
    last = 0
    j = 0
    while j < n:
        t = req_path + e[j] + net // 2
        if hold_at is not None and j + 1 == hold_at:
            t += hold
            if coalesce_next and j + 1 < n:
                t = max(t, req_path + e[j + 1] + net // 2)
                t = max(t, last)
                a.append(t)
                C.append(P[j + 1])
                last = t
                j += 2
                continue
        t = max(t, last)  # in-order delivery
        if split:
            a.append(t)
            C.append(P[j] - len(piece) // 2)
            t2 = t + 1 * MS
            a.append(t2)
            C.append(P[j])
            last = t2
        else:
            a.append(t)
            C.append(P[j])
            last = t
        j += 1
    client_end = a[-1]  # [DONE] arrives with the last piece
    return e, P, a, C, client_end


class TestHashes(unittest.TestCase):
    def test_known_vectors_match_go(self):
        # same vectors asserted in internal/rv/content_test.go
        self.assertEqual(analyze.fnv1a64("abc"), 0xE71FA2190541574B)
        self.assertEqual(analyze.splitmix64(0), 0xE220A8397B1DCDAF)

    def test_sampling_fraction(self):
        n = sum(analyze.is_sampled(f"rid-{i}", 10) for i in range(20000))
        self.assertTrue(1800 < n < 2200, n)


class TestPercentile(unittest.TestCase):
    def test_nearest_rank_against_bruteforce(self):
        r = random.Random(3)
        for _ in range(300):
            xs = sorted(r.randint(-50, 1000) for _ in range(r.randint(1, 300)))
            for p in (0.0, 0.01, 0.5, 0.9, 0.99, 0.999, 1.0):
                got = analyze.pct_sorted(xs, p)
                # definition: smallest x with count(v <= x) >= p*n  (and >= 1 sample)
                need = max(1, math.ceil(p * len(xs)))
                want = next(x for x in xs if sum(v <= x for v in xs) >= need)
                self.assertEqual(got, want, (p, xs))

    def test_empty(self):
        self.assertIsNone(analyze.pct_sorted([], 0.99))
        self.assertEqual(analyze.dist_ms([]), {"n": 0})


class TestReleaseLag(unittest.TestCase):
    def lag(self, **kw):
        e, P, a, C, end = synth_stream(**kw)
        total = end - e[-1]
        first = a[0] - e[0]
        return (analyze.release_lag_per_piece(e, P, a, C), analyze.release_lag_per_arrival(e, P, a, C), total, first)

    def test_floor(self):
        lp, la, total, first = self.lag()
        self.assertEqual(lp, 300_000)
        self.assertEqual(la, 300_000)
        self.assertEqual(total, 300_000)
        self.assertEqual(first, 300_000)

    def test_midstream_hold_visible_in_lag_not_in_total(self):
        # the required synthetic case: 30 ms hold at piece 50 of 100
        lp, la, total, first = self.lag(hold_at=50, hold=30 * MS)
        self.assertEqual(lp, 30 * MS + 300_000)   # hold appears in release lag
        self.assertEqual(la, 30 * MS + 300_000)
        self.assertEqual(total, 300_000)          # ...but NOT in the total (stream catches up)
        self.assertEqual(first, 300_000)

    def test_pre_dispatch_delay_shifts_everything(self):
        lp, la, total, first = self.lag(pre=5 * MS)
        self.assertEqual(total, 5 * MS + 300_000)
        self.assertEqual(first, 5 * MS + 300_000)
        self.assertEqual(lp, 5 * MS + 300_000)

    def test_coalesced_release_spec_formula_underreports(self):
        # held piece 50 released together with piece 51: piece 50 waited 30 ms
        lp, la, total, _ = self.lag(hold_at=50, hold=30 * MS, coalesce_next=True)
        self.assertEqual(lp, 30 * MS + 300_000)            # per-piece: true worst delay
        self.assertEqual(la, 30 * MS + 300_000 - 20 * MS)  # per-arrival: hides 20 ms (one ITL)
        self.assertLess(la, lp)

    def test_split_tokens_spec_formula_overreports(self):
        # every piece split in two events 1 ms apart: true worst delay is 1.3 ms
        lp, la, total, _ = self.lag(split=True)
        self.assertEqual(lp, 1 * MS + 300_000)
        self.assertGreaterEqual(la, 20 * MS)  # per-arrival: first half maps to the previous piece -> ~ITL

    def test_unmappable_when_content_shorter(self):
        e, P, a, C, _ = synth_stream(n=10)
        self.assertIsNone(analyze.release_lag_per_piece(e, P, a[:-1], C[:-1]))


# ------------------------------------------------------------------------------------------------
# end-to-end analyze() on synthetic raw files
# ------------------------------------------------------------------------------------------------
def write_run(tmp: Path, client_recs, prov_recs, scheduled=None, rate=10.0, duration=10.0, cpu_busy=20.0):
    lg = tmp / "lg-0"
    pv = tmp / "prov-0"
    lg.mkdir()
    pv.mkdir()
    with open(lg / "requests.jsonl", "w") as f:
        for r in client_recs:
            f.write(json.dumps(r) + "\n")
    with open(pv / "records.jsonl", "w") as f:
        for r in prov_recs:
            f.write(json.dumps(r) + "\n")
    n = len(client_recs) if scheduled is None else scheduled
    man = {"config": {"rate": rate, "duration_s": duration, "ramp_s": 0, "warmup_s": 0},
           "counts": {"scheduled": n, "recorded": len(client_recs)}, "epoch_offset_s": 0.0, "interrupted": False,
           "host": {"gce": {"machine-type": "test", "zone": "z"}}}
    (lg / "manifest.json").write_text(json.dumps(man))
    (lg / "cpu.jsonl").write_text("\n".join(json.dumps({"t": t + 0.5, "busy": cpu_busy}) for t in range(int(duration))) + "\n")
    return lg, pv


def pair(i, stream=True, n=60, hold_at=None, hold=0, pre=0, cls="benign", sampled=True, **kw):
    rid = f"rid-{i:05d}"
    nonce = f"(ref-a-b-c-d-e-{i})"
    content = "".join(" word" for _ in range(n))
    e, P, a, C, end = synth_stream(n=n, hold_at=hold_at, hold=hold, pre=pre)
    prov = {"rid": rid, "rid_src": "header", "nonce": nonce, "stream": stream, "status": 200,
            "recv_to_first_ns": e[0], "recv_to_last_ns": e[-1], "sched_last_ns": e[-1], "sched_err_last_ns": 40_000,
            "sched_err_max_ns": 60_000, "write_max_ns": 5_000, "content_sha256": sha(content), "content_len": len(content),
            "canary_hits": None, "tokens_out": n, "sampled": sampled}
    cli = {"rid": rid, "lg": 0, "seq": i, "ph": 2, "cls": cls, "cid": f"c-{i}", "stream": stream, "sched_ns": i * 100 * MS,
           "late_ns": 50_000, "status": 200, "done_seen": True, "first_ns": a[0], "end_ns": end,
           "content_sha256": sha(content), "content_len": len(content), "nonce": nonce, "sampled": sampled,
           "disp": "ALLOW", "stages": "proxy:E"}
    if not stream:
        prov["recv_to_first_ns"] = prov["recv_to_last_ns"]
        cli["first_ns"] = end
    if sampled:
        prov["emit_ns"], prov["emit_cum"] = e, P
        cli["arr_ns"], cli["arr_cum"] = a, C
    prov.update(kw.pop("prov", {}))
    cli.update(kw.pop("cli", {}))
    return cli, prov


def run(tmp, client_recs, prov_recs, *extra, **kw):
    lg, pv = write_run(Path(tmp), client_recs, prov_recs, **kw)
    args = ["--client", str(lg), "--provider", str(pv), "--out", str(Path(tmp) / "out"), *extra]
    return analyze.analyze(build_args(args))


def build_args(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", nargs="+")
    ap.add_argument("--provider", nargs="+")
    ap.add_argument("--mode", default="direct")
    ap.add_argument("--policy", default="none")
    ap.add_argument("--corpus", default=None)
    ap.add_argument("--profile-stages", default="")
    ap.add_argument("--disposition-source", default=None)
    ap.add_argument("--qualified-dispositions", default=None)
    ap.add_argument("--block-statuses", default=None)
    ap.add_argument("--block-envelope-re", default=None)
    ap.add_argument("--phases", default="2")
    ap.add_argument("--slo-ms", type=float, default=20.0)
    ap.add_argument("--err-budget", type=float, default=0.001)
    ap.add_argument("--drop-ms", type=float, default=5.0)
    ap.add_argument("--sched-tol-ms", type=float, default=1.0)
    ap.add_argument("--cpu-max", type=float, default=70.0)
    ap.add_argument("--per-second", action="store_true")
    ap.add_argument("--out")
    return ap.parse_args(argv)


class TestAnalyzeEndToEnd(unittest.TestCase):
    def test_direct_floor_passes(self):
        recs = [pair(i, stream=(i % 10 < 7)) for i in range(100)]
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "direct")
        self.assertTrue(s["verdict"]["pass"], s["verdict"])
        self.assertEqual(s["qualified"], 100)
        self.assertEqual(s["T_addon_total"]["p99"], 0.3)
        self.assertEqual(s["T_fw_addon"]["max"], 0.3)
        self.assertEqual(s["provider_sched_err_last"]["p99"], 0.04)

    def test_midstream_hold_fails_slo_via_release_lag_only(self):
        recs = [pair(i, hold_at=30, hold=30 * MS) for i in range(100)]
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "sut", "--profile-stages", "proxy")
        self.assertEqual(s["T_addon_total"]["max"], 0.3)       # totals hide the hold
        self.assertEqual(s["T_release_lag_max"]["p50"], 30.3)  # release lag shows it
        self.assertEqual(s["T_fw_addon"]["p99"], 30.3)
        self.assertFalse(s["verdict"]["checks"]["p99_T_fw_addon_lt_slo"])
        self.assertFalse(s["verdict"]["pass"])

    def test_pre_delay_attributed_to_addon(self):
        recs = [pair(i, pre=5 * MS) for i in range(50)]
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "sut")
        self.assertEqual(s["T_addon_total"]["p50"], 5.3)
        self.assertEqual(s["T_addon_first"]["p50"], 5.3)
        self.assertTrue(s["verdict"]["pass"])

    def test_negative_addon_invalidates_run_never_clamped(self):
        recs = [pair(i) for i in range(20)]
        recs[3][1]["recv_to_last_ns"] = recs[3][0]["end_ns"] + 1_000  # provider claims it finished later
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "direct")
        self.assertFalse(s["valid"])
        self.assertIn("negative_addon_total=1", s["invalid_reasons"])
        self.assertLess(s["T_addon_total"]["min"], 0)  # the negative value is kept, not clamped
        self.assertFalse(s["verdict"]["pass"])

    def test_join_by_nonce_when_rid_not_forwarded(self):
        recs = [pair(i) for i in range(10)]
        for c, p in recs:
            p["rid"] = "gw-generated-" + p["rid"]
            p["rid_src"] = "nonce"
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "direct")
        self.assertEqual(s["join"]["by_nonce_fallback"], 10)
        self.assertEqual(s["qualified"], 10)

    def test_unjoined_duplicate_and_drop_are_errors(self):
        recs = [pair(i) for i in range(30)]
        provs = [p for _, p in recs]
        del provs[0]                     # unjoined
        provs.append(dict(provs[1]))     # duplicate provider call for rid 2
        recs[5][0]["late_ns"] = 6 * MS   # schedule drop
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], provs, "--mode", "direct")
        self.assertEqual(s["error_reasons"].get("unjoined"), 1)
        self.assertEqual(s["error_reasons"].get("provider_calls_2"), 1)
        self.assertEqual(s["schedule"]["drops"], 1)
        self.assertFalse(s["verdict"]["checks"]["zero_schedule_drops"])

    def test_incomplete_records_invalidate(self):
        recs = [pair(i) for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "direct", scheduled=12)
        self.assertFalse(s["valid"])
        self.assertTrue(any(r.startswith("records_incomplete") for r in s["invalid_reasons"]))

    def test_loadgen_cpu_limit(self):
        recs = [pair(i) for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "direct", cpu_busy=75.0)
        self.assertFalse(s["verdict"]["checks"]["loadgen_cpu_max_lt_limit"])

    def test_policy_enforce_safety_and_expected_blocks(self):
        recs = []
        # secret entry correctly blocked: 403, no provider call
        c, p = pair(0, cls="secret")
        c.update(status=403, done_seen=False, err="http_403", disp="BLOCK")
        recs.append((c, None))
        # secret entry NOT blocked: provider saw the canary -> safety failure
        c, p = pair(1, cls="secret", prov={"canary_hits": ["secret.aws"]})
        recs.append((c, p))
        # pii entry redacted properly: provider has no canary, disposition REDACT
        c, p = pair(2, cls="pii", cli={"disp": "REDACT"})
        recs.append((c, p))
        # pii entry whose canary reached the provider -> safety failure
        c, p = pair(3, cls="pii", cli={"disp": "REDACT"}, prov={"canary_hits": ["pii.email"]})
        recs.append((c, p))
        # benign with output injection that leaked to the client -> safety failure
        c, p = pair(4, cli={"inject": "email", "canary_hits": ["out.email"]}, prov={"inject": "email", "inject_applied": True})
        recs.append((c, p))
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs if p], "--mode", "sut", "--policy", "enforce")
        self.assertEqual(s["expected_blocks"], 1)
        self.assertEqual(s["safety_reasons"].get("block_expected_but_provider_called"), 1)
        self.assertEqual(s["safety_reasons"].get("canary_reached_provider"), 2)
        self.assertEqual(s["safety_reasons"].get("output_canary_reached_client"), 1)
        self.assertFalse(s["verdict"]["checks"]["zero_safety_failures"])

    def test_output_redaction_length_check(self):
        inj = analyze.INJECT_LEN["email"]
        # properly redacted: provider content includes the injected piece, client replaced it with "[EMAIL]"
        c, p = pair(0, cli={"inject": "email", "disp": "ALLOW"}, prov={"inject": "email", "inject_applied": True})
        p["content_len"] = c["content_len"] + inj
        p["content_sha256"] = "different"
        c["content_len"] += len(" [EMAIL]")
        c["sampled"] = p["sampled"] = False
        # truncated after the canary: must NOT qualify
        c2, p2 = pair(1, cli={"inject": "email"}, prov={"inject": "email", "inject_applied": True})
        p2["content_len"] = c2["content_len"] + inj
        p2["content_sha256"] = "different"
        c2["content_len"] = 10
        c2["sampled"] = p2["sampled"] = False
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c, c2], [p, p2], "--mode", "sut", "--policy", "enforce")
        self.assertEqual(s["qualified"], 1)
        self.assertEqual(s["error_reasons"].get("content_mismatch"), 1)

    def test_stage_skipped_disqualifies(self):
        recs = [pair(i, cli={"stages": "canon:E,sem:S"}) for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "sut", "--profile-stages", "canon,sem")
        self.assertEqual(s["qualified"], 0)
        self.assertEqual(s["error_reasons"].get("stage_sem_S"), 5)

    def test_percentiles_over_merged_samples_not_averaged(self):
        # 99 requests at 0.3 ms and 1 at 50 ms: p99 is 0.3, max is 50.3; averaging per-worker p99s would differ
        recs = [pair(i, sampled=False) for i in range(100)]
        recs[7][0]["end_ns"] += 50 * MS
        with tempfile.TemporaryDirectory() as tmp:
            s = run(tmp, [c for c, _ in recs], [p for _, p in recs], "--mode", "direct")
        self.assertEqual(s["T_addon_total"]["p99"], 0.3)
        self.assertEqual(s["T_addon_total"]["max"], 50.3)


if __name__ == "__main__":
    unittest.main()


class TestStrata(unittest.TestCase):
    """Qualification strata: qualified / policy (expected, false-positive, other) / infra errors."""

    def run_recs(self, recs, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            return run(tmp, [c for c, _ in recs], [p for _, p in recs if p], "--mode", "sut", *extra)

    def blocked(self, i, cls, status, err_detail=None, disp=None, inject=None):
        c, _ = pair(i, cls=cls, sampled=False)
        c.update(status=status, done_seen=False, err=f"http_{status}", disp=disp, first_ns=0, end_ns=2_000_000)
        if inject:
            c["inject"] = inject
        if err_detail is not None:
            c["err_detail"] = err_detail
        return c, None

    def test_v1_content_filter_400_is_policy_not_infra(self):
        recs = [pair(i, sampled=False) for i in range(8)]
        recs.append(self.blocked(8, "benign", 400, "type=invalid_request_error code=content_filter"))  # benign -> FP
        recs.append(self.blocked(9, "injection", 400, "type=invalid_request_error code=content_filter"))  # expected
        recs.append(self.blocked(10, "pii", 400, "type=invalid_request_error code=content_filter"))  # other
        recs.append(self.blocked(11, "benign", 400, "type=invalid_request_error code=invalid_json"))  # infra
        recs.append(self.blocked(12, "benign", 403, "type=None code=auth_forbidden"))  # infra (no block evidence)
        recs.append(self.blocked(13, "secret", 403, "", disp="BLOCK"))  # expected via disposition
        recs.append(self.blocked(14, "benign", 400, "type=invalid_request_error code=content_filter", inject="email"))  # other
        s = self.run_recs(recs)
        pol = s["policy"]
        self.assertEqual(pol["expected_blocks"], 2)
        self.assertEqual(pol["false_positive_blocks"], 1)
        self.assertEqual(pol["other_blocks"], 2)
        self.assertEqual(pol["benign_offered"], 11)  # 8 pairs + benign blocked(8,11,12); inject=email excluded
        self.assertAlmostEqual(pol["false_positive_rate"], 1 / 11)
        self.assertEqual(s["infra_errors"], 2)
        self.assertEqual(s["error_reasons"].get("http_400"), 1)
        self.assertEqual(s["error_reasons"].get("http_403"), 1)
        self.assertEqual(s["qualified"], 8)
        self.assertEqual(pol["latency_client_total_ms"]["policy_block_expected"]["p50"], 2.0)
        self.assertEqual(s["T_fw_addon"]["n"], 8)  # latency percentiles only over the qualified cohort

    def test_shed_and_5xx_are_infra_errors(self):
        recs = [pair(i, sampled=False) for i in range(996)]
        for i, st in ((996, 429), (997, 503), (998, 500)):
            c, _ = pair(i, sampled=False)
            c.update(status=st, done_seen=False, err=f"http_{st}")
            recs.append((c, None))
        c, p = pair(999, sampled=False)
        c.update(done_seen=False, err="eof_mid_stream")
        recs.append((c, p))
        s = self.run_recs(recs)
        self.assertEqual(s["infra_errors"], 4)
        self.assertAlmostEqual(s["error_rate"], 0.004)
        self.assertFalse(s["verdict"]["checks"]["infra_error_rate_le_budget"])

    def test_policy_blocks_do_not_consume_error_budget(self):
        recs = [pair(i, sampled=False) for i in range(100)]
        recs += [self.blocked(100 + i, "injection", 403, "", disp="BLOCK") for i in range(20)]
        s = self.run_recs(recs, "--policy", "enforce")
        self.assertEqual(s["infra_errors"], 0)
        self.assertEqual(s["policy"]["expected_blocks"], 20)
        self.assertTrue(s["verdict"]["checks"]["infra_error_rate_le_budget"])
        self.assertTrue(s["verdict"]["pass"])

    def test_flag_qualifies_rewrite_configurable(self):
        recs = [pair(0, sampled=False, cli={"disp": "FLAG"}), pair(1, sampled=False, cli={"disp": "REWRITE"})]
        s = self.run_recs(recs)
        self.assertEqual(s["qualified"], 1)
        self.assertEqual(s["error_reasons"].get("disposition_REWRITE"), 1)
        s = self.run_recs(recs, "--qualified-dispositions", "ALLOW,REDACT,FLAG,REWRITE")
        self.assertEqual(s["qualified"], 2)

    def test_policy_miss_is_not_qualified_and_is_a_safety_failure(self):
        c, p = pair(0, cls="injection", sampled=False)
        s = self.run_recs([(c, p)], "--policy", "enforce")
        self.assertEqual(s["outcomes"].get("policy_miss"), 1)
        self.assertEqual(s["qualified"], 0)
        self.assertEqual(s["safety_reasons"].get("block_expected_but_provider_called"), 1)

    def test_v1_extractor_pipeline_trace_and_headers(self):
        stages_ok = [{"name": "auth", "action": "allow"}, {"name": "output_guardrail", "action": "allow"}]
        stages_skip = [{"name": "auth", "action": "allow"}, {"name": "output_guardrail", "action": "skip"}]
        recs = []
        c, p = pair(0, sampled=False, cli={"disp": None, "stages": None})
        c["pipeline_trace"] = {"final_action": "allow", "stages": stages_ok}
        recs.append((c, p))
        c, p = pair(1, sampled=False, cli={"disp": None, "stages": None})
        c["pipeline_trace"] = json.dumps({"final_action": "redact", "stages": stages_ok})
        recs.append((c, p))
        c, p = pair(2, sampled=False, cli={"disp": None, "stages": None})
        c["pipeline_trace"] = {"final_action": "allow", "stages": stages_skip}
        recs.append((c, p))
        c, p = pair(3, sampled=False, cli={"disp": None, "stages": None})
        c["resp_headers"] = {"X-ZeroShield-Action": "redacted"}
        c["v1_stages"] = "auth:allow,output_guardrail:allow"
        recs.append((c, p))
        for src in ("auto", "v1"):
            s = self.run_recs(recs, "--profile-stages", "auth,output_guardrail", "--disposition-source", src)
            self.assertEqual(s["qualified"], 3, src)
            self.assertEqual(s["error_reasons"].get("stage_output_guardrail_S"), 1, src)
        s = self.run_recs(recs, "--profile-stages", "auth,output_guardrail", "--disposition-source", "xrv")
        self.assertEqual(s["qualified"], 0)  # no x-rv-* fields in these records
