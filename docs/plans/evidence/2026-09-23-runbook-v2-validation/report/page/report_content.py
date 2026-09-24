"""Narrative + numbers for the verdict page. Every number here is copied from the findings ledger / lane summaries.
Run: python3 report_content.py  (writes report_data.json)"""
import json
import os

import mdparse

NREG = len(mdparse.register())
NCRIT = sum(1 for r in mdparse.register() if r["impact"] == "CRITICAL")

M = lambda s: f'<span class="mono">{s}</span>'  # noqa: E731

D = {}
D["date"] = "23 Sep 2026"
D["description"] = ("Evidence-backed verdict on the AI Mesh v2 backend-rewrite runbook: what live GCP measurement, a throwaway "
                    f"v2 prototype and five contradiction reviews proved, and the {NREG} corrections in runbook v2.1.")
D["headline"] = "Not correct as written. Rebuild, but only on the corrected plan."
D["lede"] = ("Every claim in the runbook that could be tested was tested. We executed v1 at the audited baseline and at HEAD, "
             "built and load-tested a throwaway v2 prototype on live GCP, and gave five reviewers the job of overturning each finding. "
             f"The rebuild direction holds. The plan needs {NREG} corrections, {NCRIT} of them critical, and its capacity and cost premises "
             "do not survive measurement.")
D["verdict_stamp"] = "Do not execute v2 as written"
D["meta"] = [
    ("Repo", "revamp @ 52a584e9"), ("Audited baseline", "ansh @ 2a657fad"),
    ("GCP", "ai-mesh-firewall · asia-south1 (G2 fleet in asia-northeast1 after stockouts)"),
    ("Prices", "on-demand list · Cloud Billing Catalog API · 2026-09-23"),
    ("Experiments", "138 VMs · $349.43 on-demand · all deleted"),
]

D["scorecard_lede"] = ("Your objective, split into parts that can each be proven or disproven. \"Prototype\" is the throwaway v2 build of the "
                       "§10.4 lifecycle, measured with the signed FULL profile: Tier-1, PII/secret redaction, PG2-22M input scan, streaming output "
                       "scan, T01 band ≤ 1,024 in / ≤ 400 out, 70/30 SSE/JSON. \"C4\" is the v2.1 streaming metric: the worst chunk of each stream, "
                       "measured at the client, holdback excluded, p99 over all offered requests with errors counted as +∞.")
D["scorecard"] = [
    dict(goal="p99 overhead < 20 ms: JSON", status="MET",
         evidence=f"Prototype JSON p99 {M('15.9–16.6 ms')} at 78 RPS on one g2-standard-24 (3 repeats) and {M('11.3–11.5 ms')} at 191 RPS "
                  f"on the off-box topology. v1 meets it at no rate: JSON alone {M('111–206 ms')} at 1 RPS."),
    dict(goal="p99 < 20 ms: streaming, per chunk (C4)", status="MET WITH CORRECTIONS",
         evidence=f"As built the prototype misses at every load (≈ 20–23 ms): CPU work on the event loops stalls other streams while the "
                  f"GPUs idle, and the gateway's own metrics read PASS. With that work off the loops one g2-standard-24 passes to "
                  f"{M('200 RPS')} (18.8–19.6 ms, 3/3); a C4 gateway + L4 guard VM passes at {M('191 RPS')} per L4 (13.9–14.3 ms). "
                  f"The 1,024-token edge fails on G2 CPUs even with the fix (21.7 ms at 25 RPS) but passes on the off-box fleet at 130 RPS (13.2 ms) (C38, C42)."),
    dict(goal="p99 < 20 ms: streaming including holdback", status="NOT MET",
         evidence=f"Structural, not hardware. A zero-leak redactor must hold each trailing word until the next token: "
                  f"{M('2–3 tokens')} per stream = {M('40 / 60 ms')} (p50 / p99) at a 20 ms inter-token interval, on every build and "
                  f"topology. The runbook's own L03-3 fails any 30 ms mid-stream hold and T01 puts \"all hold\" inside the 20 ms. v1 "
                  f"holds the first ≈ 40 tokens ({M('785 ms')} p50). v2.1 splits the metric and asks you to sign a holdback bound (C4)."),
    dict(goal="Maximize qualified RPS within $5,000/month", status="MET WITH CORRECTIONS",
         evidence=f"The plan's fleet (4 × G2, ≥ 1,064 RPS signed as a floor) costs {M('$8,952.24')}/month on-demand and was never "
                  f"measured. Measured: 3 × c4-highcpu-16 gateways + 4 × g2-standard-4 guards with the loop fix carry {M('520 RPS')} "
                  f"(511 qualified/s, C4 17.4 / 18.2 / 17.9 ms, 3/3) for {M('$4,161.69')}/month ($4,631.81 with the audit store sized, C41); "
                  f"random arrivals pass at 312 RPS. Egress is excluded: at 57 KB per request sustained 24/7 it adds ≈ $8,900/month, and "
                  f"with egress inside the envelope $5,000 buys only ≈ 172–188 sustained RPS."),
    dict(goal="Horizontal scalability", status="MET WITH CORRECTIONS",
         evidence=f"As specified (GW03's per-worker guard cap), 1 / 2 / 4 units carry {M('75 / 80 / 80 RPS')} — efficiency 0.53 / 0.27, "
                  f"GPUs 2–12% busy. With the cap removed and a shared-state edge: {M('150 / 300 / 600 RPS')}, efficiency "
                  f"{M('1.00')}; shared Redis ≤ 0.04 cores and the edge ≤ 1.4 of 16 cores (C10)."),
    dict(goal="Every enabled policy preserved", status="NEEDS CORRECTION",
         evidence="Provider-byte proofs hold for ALLOW / REDACT / BLOCK on the channels the prototype scans (secrets 1,006/1,006 blocked, "
                  "PII 2,084/2,084 redacted, 0 canaries leaked). The plan does not force the rest: 13 request fields and 4 output channels "
                  "went unscanned (logprobs defeated REDACT while audit said REDACT), and §10.5.3 lets a higher-priority REDACT cancel a "
                  "BLOCK on a different finding (C21, C22 — critical)."),
    dict(goal="SSE and OpenAI SDK semantics", status="NEEDS CORRECTION",
         evidence=f"Official Python {M('2.38.0')} and Node {M('4.104.0')} SDKs pass unmodified via a base-URL swap: streaming, tool "
                  "calls, typed errors. Gaps the plan must name: provider 400/429 surfaced as 502 (the SDK retries 3×), context overflow "
                  "returned as 413 not context_length_exceeded, a fixed 120 s timeout that kills long generations."),
    dict(goal="Tenant isolation", status="NEEDS CORRECTION",
         evidence=f"Data isolation holds: 2 tenants × 200 concurrent, {M('0')} leaks; plan pushes reached 72 workers with 0 stale "
                  "responses. Performance isolation fails: one tenant's 16-window prompt shed the other 30/30 (tenant-blind FIFO), per-org "
                  "rate limits multiply by the worker count, and quota leases stranded 84% of a budget (C23, C29)."),
    dict(goal="Failure safety", status="NEEDS CORRECTION",
         evidence=f"Guard death leads to the declared posture, never \"clean\"; the kill switch reaches every worker in ≤ {M('0.46 s')}. "
                  "But a store failover silently disengaged an engaged kill switch and un-revoked a key, the audit stream fills the "
                  "budgeted 1 GiB store in ≈ 41 min, and the guard deadline equals the SLO so any hiccup becomes a block (C36 — critical, "
                  "C41, C10)."),
    dict(goal="Auditable user control", status="NEEDS CORRECTION",
         evidence="One DecisionRecord per phase carries the plan version and deciding rules, and counters reconcile exactly with the wire "
                  "in 62 of 63 runs. But sheds leave no record while completeness reads 1.0, the audit can contradict the wire (logprobs), "
                  "and no card owns the console contract for v2 decisions — new card GW14b (C3, C21, C40)."),
]

D["compare"] = dict(
    lede=("Same load generator, provider stub, corpus and GCP shape (g2-standard-24). v1 ran without a semantic guard — its Tier-2 needs "
          "cloud LLM keys, which were not configured — which favours v1; the prototype ran PG2-22M on the L4s. The prototype omits some "
          "channels v1 scans (C21), so the CPU ratio is not like-for-like; v1's proxy path alone, firewall off, is the fair comparison."),
    head=["Measure", "v1", "v2 prototype"],
    rows=[
        ["JSON p99 overhead", "full profile, headline band", "111–206 ms @ 1 RPS", "14.2–14.9 ms @ 25 · 15.9–16.6 @ 78"],
        ["Streaming, worst chunk per stream (C4) p99", "holdback excluded", "— (first-token hold 785 ms p50)", "20–23 ms as built · 15.0 @ 78, 18.8–19.6 @ 200 with the loop fix"],
        ["Streaming holdback per stream", "ITL 20 ms", "≈ 40 tokens before first release", "2 / 3 tokens (40 / 60 ms) p50 / p99"],
        ["Gateway CPU per request", "cgroup / schedstat", "460–631 ms", "34–36 ms"],
        ["Proxy path alone, firewall off", "v1 pass-through", "327 ms CPU · JSON p99 239 ms", "n/a"],
        ["Highest error-free rate, one node", "infra ≤ 0.1%", "25 RPS (p99 6.9–9.0 s)", "≥ 250 RPS"],
        ["Highest rate meeting p99 < 20 ms, one node", "C4 for streams", "none", "200 RPS (with the loop fix)"],
    ])

D["figures_lede"] = ("Every point is a 5-minute open-loop step with 100% of stream chunks sampled; hollow markers failed the error gate or "
                     "were not repeatable. C4 was recomputed from raw per-request records by the controller and the lanes independently.")
D["figures"] = [
    dict(kind="line", title="Worst chunk per stream (C4), p99 vs offered load",
         x_label="offered load (RPS, constant arrivals)", y_label="C4 p99 (ms)", x_max=750, y_max=30, threshold=20,
         threshold_label="20 ms SLO", x_ticks=[0, 100, 200, 300, 400, 500, 600, 700], y_ticks=[0, 5, 10, 15, 20, 25, 30],
         series=[
             dict(name="all-in-one G2, as built", cls="s2", points=[[25, 19.98, True], [32, 20.31, True], [40, 20.45, True], [50, 20.02, True], [78, 21.53, True]]),
             dict(name="all-in-one G2, loop fix", cls="s1", points=[[50, 14.14, False], [78, 14.99, False], [100, 15.04, False], [150, 16.64, False], [200, 18.89, False], [250, 23.96, True]]),
             dict(name="off-box 1 gw + 1 guard", cls="s4", points=[[50, 12.38, False], [78, 12.43, False], [122, 13.64, False], [153, 13.03, False], [191, 14.24, False], [238, 23.33, True]]),
             dict(name="3 gw + 5 guards, as built", cls="s3", points=[[466, 18.09, False], [582, 20.30, True], [728, 23.50, True]]),
             dict(name="3 gw + 4 guards, loop fix", cls="s5", points=[[466, 16.10, False], [520, 17.89, False], [582, 19.92, True]]),
         ],
         caption=("All-in-one = one g2-standard-24 (2 × L4, 24 vCPU Cascade Lake); \"as built\" points from the unit and fleet lanes "
                  "(25 RPS passed once, then failed its repeat). Loop fix = no periodic metrics dump on the serving loop + tokenizer "
                  "<code>encode_batch</code> in a thread pool. Off-box = c4-highcpu-16 gateways calling g2-standard-4 guard VMs over TCP; "
                  "the fleets sit behind an nginx edge with a shared store. Repeated rates show the median of 3; 582 RPS on the loop-fix "
                  "fleet passed 2 of 3, so it is drawn as a failure. Holdback is excluded from C4 by construction; with it every point "
                  "sits at ≈ 70 ms.")),
    dict(kind="bar", title="Qualified RPS per $1,000 per month at the C4 knee (on-demand, Mumbai list)",
         value_label="qualified RPS per $1,000/month of node cost", v_max=200, unit_fmt="{:.0f}",
         bars=[
             dict(label="v1, g2-standard-24", sub="no rate meets 20 ms", value=0, cls="b-v1", note="0"),
             dict(label="v2 all-in-one, as specified", sub="GW03 per-worker cap", value=0, cls="b-v1", note="fails C4"),
             dict(label="v2 all-in-one + loop fix", sub="g2-standard-24 · 200 RPS", value=129.2, cls="b-v2"),
             dict(label="v2 off-box, 1 guard", sub="c4-hc-16 + g2-std-4 · 188 RPS", value=172.4, cls="b-ok"),
             dict(label="v2 off-box, 2 guards", sub="c4-hc-16 + 2 × g2-std-4 · 293 RPS", value=177.2, cls="b-ok"),
             dict(label="v2 $5k fleet, loop fix", sub="3 × c4-hc-16 + 4 × g2-std-4 · 511 RPS", value=133.3, cls="b-v2b"),
             dict(label="Plan's fleet, never measured", sub="1,064 RPS for $8,952 on-demand", value=118.9, cls="b-ref"),
         ],
         caption=("Node cost includes boot disk and external IP; fixed platform items ($329.50) and egress are excluded here. Off-box "
                  "pairs cost $1,089.14 (1 guard) and $1,653.91 (2 guards) per month, the $5k fleet's nodes $3,832.19, the g2-standard-24 "
                  "$1,548.07. The fleet earns less per dollar than a single pair because independently routed guard calls arrive in "
                  "bursts — queueing at ≈ two-thirds of the random-arrival level at fleet size (C43). The plan's bar uses its own unmeasured 1,064 RPS.")),
]

D["facts_lede"] = ("Parsed directly from runbook v2.1 §0.2, so this page and the corrected document cannot disagree. \"Lane\" names the "
                   "evidence directory in the bundle.")
D["register_lede"] = ("Every correction cites an executed probe, a live measurement or a line-level check, and each survived a reviewer "
                      "whose job was to overturn it. Findings that did not survive were removed or weakened. Open a row for the proof "
                      "and the exact change made in v2.1. Critical and high are shown first.")
D["held_lede"] = "Claims that held up under measurement or execution. The rebuild rests on these."
D["held"] = [
    ("CONFIRMED", f"v1 defects P1, P2, P3, P6 (unit) and P7, re-executed at {M('2a657fad')} and HEAD: an org ALLOW cannot override a "
                  "scanner BLOCK, and MONITOR still blocks on output."),
    ("CONFIRMED", "The degraded-label mismatch, and the adapter that drops flag-level Tier-2 findings (§10.1.2–10.1.3), with narrower "
                  "consequences than stated."),
    ("CONFIRMED", f"PG2-22M at {M('2.15 ms')} per 512-token window on an L4 (the user-reported 2.14 ms), but only with an exact-shape "
                  "TensorRT fp16 engine."),
    ("CONFIRMED", f"An off-box guard is cheap: same-zone RTT {M('0.084 ms')} p50 for 16 KB, so remote_http is a viable backend."),
    ("CONFIRMED", "A CPU-only semantic guard cannot meet a 5–20 ms budget (74–138 ms per window)."),
    ("HELD", "Pure ASGI + a lean provider client is the right stack: 4 × BaseHTTPMiddleware cost 16× throughput per worker; aiohttp "
             "uses 124–238 µs CPU per JSON request vs httpx's 950–2,659 µs."),
    ("HELD", f"≤ 1 shared-state round trip per request (GW06): {M('23,285 / 23,392')} requests made zero."),
    ("HELD", f"Kill switch reaches new admissions in {M('0.21–0.61 s')}; epoch revocation in {M('≤ 0.45 s')}; a missed plan "
             f"notification converges in {M('≤ 2.46 s')}."),
    ("HELD", "Provider time is never charged to the firewall: at 2,000 ms provider TTFT the harness measures 0.515 ms of overhead (T03)."),
    ("HELD", f"No FAIL_OPEN and no skipped guard windows under load up to {M('200 RPS')}; streams of 404 s complete; mid-stream "
             "cancellation is audited."),
]
D["cards_lede"] = ("The corrected dependency table in v2.1 §0.5 is verified acyclic. Every exit artifact is produced inside the "
                   "depending card's closure, and T24 moves before GW23.")
D["method_lede"] = ("Open-loop arrival-rate load only. The load generator never runs on the system under test, and the provider stub "
                    "never shares cores with it. Each rate step runs ≥ 5 minutes after warm-up, with 3 repeats at the knee. Percentiles "
                    "come from merged raw per-request records.")
D["method"] = [
    ("Instrument", "A Go open-loop load generator (olg), a synthetic OpenAI-compatible provider that records every request "
                   "(synthprov), and a delay-injecting honesty proxy (rvproxy). Validated to 6,000 RPS at the worst band with 0 drops "
                   "over 3 × 1.8 M requests (DIRECT floor p99 0.61 ms). Honesty checks passed 13/13: an injected 5.06 ms delay reads "
                   "+5.11 ms, a 30 ms mid-stream hold reads +29.7 ms of release lag, and provider TTFT is never charged to the firewall."),
    ("Metric", "T_fw_addon = client duration − provider duration, per request, with no cross-host clocks. For SSE, \"first\" is the "
               "client event that completes provider token 1, and release lag is sampled on every chunk. p99 is taken over all "
               "offered requests, with errors counted as +∞. Qualified = ALLOW, REDACT or FLAG completions; policy blocks are a "
               "separate stratum; the infra-error gate is ≤ 0.1%."),
    ("Load", "Open-loop constant and Poisson arrivals. Each step runs a 30 s ramp, a 60 s warm-up and a 300 s measurement, with "
             "3 repeats at the knee and the first failing step recorded. A DIRECT (loadgen → provider) floor runs at the same rate. "
             "Loadgen busy ≤ 70% and schedule lateness p99 ≤ 0.11 ms were checked on every run."),
    ("Systems", "v1 at <code>revamp@52a584e9</code> (Redis, Mongo and Postgres co-located, disclosed). The v2 prototype "
                "<code>rvproto-frozen-1</code> (4,651 lines, 49 modules): aiohttp, Hyperscan, and PG2-22M on TensorRT fp16 with one "
                "guard-owner process per L4. The GPU guard bench ran on a g2-standard-8 (L4, 72 W) and C4 CPUs."),
    ("Workload", "The T01 band: HEADLINE corpus (≤ 1,024 in, ≤ 400 out, 70/30 SSE/JSON); WORST-BAND (1,024 in = 3 PG2 windows, "
                 "400 out); a correctness mix of secrets, PII and injections carrying canaries; long streams (400 tokens at ITL 30 ms, "
                 "≈ 1,200 concurrent). Provider TTFT 150 ms; ITL 10, 20 and 30 ms."),
    ("Review", "Nineteen lanes covered claim verification, GPU/CPU guard benches, the v1 bench, the prototype build and benches, and "
               "the harness. Five contradiction reviewers (false positives, hidden failures, state propagation, observability, edge "
               "cases) re-ran every finding on a frozen copy. A devil's-advocate pass then checked each conclusion against the "
               "runbook text and the signed T01 contract."),
    ("Cloud", "Project ai-mesh-firewall, asia-south1 (the all-in-one fleet in asia-northeast1). 138 VMs created and deleted "
               "(Cloud Audit Logs); 39 further creations failed on GPU stock-outs. Spend $349.43 at on-demand list prices per VM region "
               "(≈ $43 of it 17 VMs left idle 5 h after the last run). Raw data (≈ 45 GB) in the private bucket "
               "<code>gs://ai-mesh-firewall-rv-evidence-20260923</code>."),
    ("Evidence", "<code>docs/plans/evidence/2026-09-23-runbook-v2-validation/</code>: MANIFEST.sha256 covers every file. "
                 "EXCLUDED.tsv lists the bulk raw data kept on the controller, with sha256 and reason."),
]
D["risks"] = [
    "G2 capacity is not guaranteed on demand. 36 of 170 VM creations failed with ZONE_RESOURCE_POOL_EXHAUSTED (Cloud Audit Logs): "
    "asia-south1-a/b/c at 07:18–07:20 and 11:40–11:54 UTC, asia-south1-a/c again at 18:40–18:42 UTC, plus asia-southeast1 and asia-east1; "
    "the all-in-one fleet ran in asia-northeast1. Price a reservation (billed at on-demand rates) or qualify a fallback pool and a "
    "degraded-capacity posture (C18).",
    "Egress, not compute, dominates the envelope at sustained load: ≈ 57 KB to the client per request (per-token SSE framing ≈ 300 B per "
    "output token) + 9–10 KB to the provider ⇒ ≈ $17.4 per sustained qualified RPS-month on Premium tier, more than the node cost per RPS. "
    "Decide whether egress sits inside the $5,000 envelope; the bench ran without TLS (≈ +3.5 KB per request).",
    "PG2 is a narrow detector. In-scope recall is 35–40% at 0.5 and 43.6% on the harness injections (591 of 1,048 reached the provider); "
    "paraphrase 19–32%; benign confidentiality system prompts score 0.998 and benign developer SQL 0.99. The default semantic action "
    "should be FLAG until T04 signs per-request FPR and recall floors (C6).",
    "Unexplained ≈ 40 ms single-worker stalls appear from 200 RPS per g2-standard-24 and decide whether the SSE stratum passes there; the "
    "10 Hz loop-lag probe misses them. Instrument GC, the tokenizer thread pools and page faults before signing 200 RPS as a knee.",
    "There is no production traffic, so every capacity and quality number rests on an assumed traffic shape. v2.1 adds a signed "
    "traffic-shape assumption register to T01, which T24 must test.",
    "The prototype is throwaway. It proves the architecture's cost and latency envelope and reproduces the plan's failure classes; it is "
    "not a starting codebase.",
]
D["footer"] = ("Evidence bundle: <code>docs/plans/evidence/2026-09-23-runbook-v2-validation/</code> on branch <code>revamp</code>, commit <code>f29ae862</code> "
               ". The corrected runbook is <code>AI_MESH_MASTER_RUNBOOK_v2.1_BACKEND_REWRITE.docx</code> in that bundle. "
               "Security hygiene (secret rotation) was out of scope by request.")

json.dump(D, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_data.json"), "w", encoding="utf-8"),
          indent=1, ensure_ascii=False)
print("report_data.json written")
