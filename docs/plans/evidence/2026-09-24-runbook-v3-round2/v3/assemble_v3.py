#!/usr/bin/env python3
"""Assemble the v3 runbook: v2.1 text + new Part 0 (v3) + inline v3 AMENDMENT blocks.

Every insertion is anchored on an exact heading line; the script fails if any anchor is
missing or duplicated, so a changed v2.1 source cannot silently drop an amendment.
"""
import sys
from pathlib import Path

REPO = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall")
SRC = REPO / "docs/AI_MESH_MASTER_RUNBOOK_v2.1_BACKEND_REWRITE.md"
DST = REPO / "docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md"
PART0 = Path(__file__).with_name("part0_v3.md")

V21_BANNER = "> **VERSION 2.1 — validated corrections, 23 September 2026.**"
V3_BANNER = (
    "> **VERSION 3 — round-2 validated plan, 24 September 2026.** This edition keeps the v2.1 text "
    "(and through it the v2 text) and adds a new Part 0 with the round-2 results: measured facts, the "
    "v3 corrections register (R2-01 … R2-24), new cards, open gates and the v3 cost basis. Inline "
    "**\"v3 AMENDMENT\"** blocks sit under the headings they change. **Precedence:** a v3 AMENDMENT "
    "block beats a v2.1 CORRECTION block, which beats the original v2 text. v2.1's Part 0 is kept "
    "as Part 0-A."
)

OLD_PART0 = "# Part 0 — Validation result and corrections (v2.1)"
NEW_PART0A = "# Part 0-A — Round-1 validation result and corrections (v2.1)"
PART0A_NOTE = (
    "> **Note (v3).** This is v2.1's Part 0, unchanged except for its section numbers (0.1–0.5 are now "
    "0-A.1–0-A.5, and references to them elsewhere in the v2.1 text were renumbered to match). Where it "
    "and Part 0 (v3) disagree, Part 0 wins."
)
FIRST_BODY_HEADING = "# 1. Program definition and release equation"

# v2.1 cross-references to its own Part 0, renumbered to Part 0-A (exact strings, counted).
XREFS = [
    ("register §0.3", "register §0-A.3", 1),
    ("§0.5 (Part 0) is the single authoritative table", "§0-A.5 (Part 0-A) is the single authoritative table", 1),
    ("GW13, GW14 (§0.5)", "GW13, GW14 (§0-A.5)", 1),
    ("as cohort 1 (§0.5)", "as cohort 1 (§0-A.5)", 1),
]

A = {}

A["### GW16b — Production edge (C16, C20; absorbs T16)"] = (
    "> **v3 AMENDMENT (R2-12, R2-16).** Option A is selected: a regional external passthrough NLB in front "
    "of the gateways. It measured the same as a plain external IP (Δ C4 p99 −0.12 … +0.27 ms against an "
    "internal path). The regional ALB added 8–10 ms p99 and is rejected for token streams. The NLB "
    "balances per connection, so connection recycling (`RV_CONN_MAX_AGE_S`) is mandatory: without it a "
    "scaled-out gateway received 0–0.2 req/s while the old one kept 40. On MIG scale-in the NLB drains "
    "for 300 s before VM deletion and SIGTERM, so streams of up to ≈ 375 s survive. Open gates: TLS cost "
    "(G-10), internet-vantage latency (G-11) and a store outage behind the health-checked NLB (G-13)."
)

A["## 1.2 Latency contract"] = (
    "> **v3 AMENDMENT (R2-06, R2-11, R2-12).** (1) The signed holdback bound (≤ 3 upstream tokens held) "
    "is not implemented in any build measured so far: a UUID was held for 36 tokens and base64 blobs "
    "were held whole. GW12b makes it a hard cap with a histogram, and C4 results count as SLO evidence "
    "only once GW12b's content-shape run passes. (2) The SLO is certified from the client side, as the "
    "firewall-added delta against a direct path over the same network (C4 from a black-box synthetic "
    "client, C39). The gateway's own histogram under-read C4 by 3.2–5.8 ms and cannot certify it "
    "(GW14d). (3) Rare ≈ 40 ms lost-segment tails appear on every external path, including a direct "
    "external IP with no firewall in it (0–33 streams per run in-region); the delta method in (2) keeps "
    "them out of the firewall's account."
)

A["## 1.3 Capacity and cost contract"] = (
    "> **v3 AMENDMENT (R2-01, R2-15; §0.6).** Capacity is signed on Poisson arrivals at fleet scale, not "
    "on constant arrivals. The measured RC2 planning figure is **100 RPS per L4** (split topology; 124 "
    "fails), about half the constant-arrival 190–210. The v3 planning fleets within $5,000/month are in "
    "§0.6; fleet A (on-demand: 2 gateways + 4 guards, $4,475.30/month) gives 400 Poisson RPS by "
    "arithmetic on measured units. The Poisson pass point at fleet scale on the final build is gate "
    "G-09. GPU supply in Mumbai is a capacity constraint in its own right (4.9% of on-demand L4 creates "
    "succeeded in one persistent loop), so the baseline is reserved (GW25)."
)

A["## 2.1 Non-negotiable scalable-code rules"] = (
    "> **v3 ADDITIONS (R2-02, R2-09, R2-10, R2-14).** Rules proven by round 2: (1) **No work proportional "
    "to the number of tenants, keys or records on a serving event loop.** RC2's periodic whole-set "
    "refresh took C4 p99 from 12 ms (3 tenants) to 145–153 ms (10,000 tenants) (GW05c). (2) **No "
    "shared-store call on the request path that the declared outage semantics do not allow.** "
    "Budget-lease refills on the request path turned a store outage into 503s after 0.15 s instead of "
    "5 s (GW06). (3) **Monitoring never fails a request.** A full metrics directory made 40% of new "
    "tenants' first requests fail with HTTP 500 (GW14d). (4) **Store timeouts account for TCP loss "
    "recovery.** About 1e-4 of cross-zone round trips waited out the 200 ms minimum RTO (GW06)."
)

A["# 3. Required live environments and proof levels"] = (
    "> **v3 AMENDMENT (R2-13, R2-20, R2-24).** Gateways run as a system user or in containers: "
    "systemd-logind `RemoveIPC=yes` deleted a login user's shared-memory metrics and made `/readyz` "
    "flap. Measurement harnesses are released immutably and run from frozen copies. Every cloud "
    "validation environment has an idle watchdog, a burn-rate monitor, a store-memory alarm, an audit "
    "guard and an owner-visible teardown rule that still works if the operator's session ends; round 2 "
    "lost ≈ $120 to idle burn without one. Store drills use flush, partition and Redis forced failover, "
    "because Memorystore for Valkey has no manual failover."
)

A["## 10.3 Target backend architecture"] = (
    "> **v3 AMENDMENT (R2-12, R2-16; GW16b, GW25).** Production topology as measured in round 2: a "
    "regional external passthrough NLB → the gateway tier (c4-highcpu-16, ≥ 570 RPS each at constant "
    "arrivals) → guard owners over TCP (g2-standard-4, one owner process per L4) with load-aware "
    "routing (C43). Stores: Cloud SQL PostgreSQL 16 HA as the source of truth and Memorystore for "
    "Valkey 8 HA as the serving store, with ≥ 2 re-hydrators in ≥ 2 zones. Primary platform: MIG. GKE "
    "is qualified at the same per-guard limit (measured 3.3 ms slower). ALBs are not used for token "
    "streams (+8–10 ms p99)."
)

A["## 10.6 Capacity from the environment"] = (
    "> **v3 AMENDMENT (R2-07).** The per-worker guard cap derived by GW03 is removed: with it (C10 off), "
    "admitted requests ran at 23.5 ms p99 under overload and never recovered. Overload is decided "
    "once, at the guard owner, by CoDel (target 5 ms, interval 100 ms, hard backlog cap 60 ms; the "
    "guard wait budget defaults to max(100, cap + 40) ms and is a separate knob). Sizing uses the "
    "Poisson figures in §0.6, not constant-arrival knees."
)

A["## 10.11 Cutover gate and stop conditions"] = (
    "> **v3 ADDITIONS.** Gate rows: **v3 blockers** — R2-02, R2-03, R2-04, R2-05, R2-06 and R2-10 "
    "implemented, with GW05b, GW05c, GW12b, GW14c and GW14d live tests green. **Open gates** — G-01 … "
    "G-18 (§0.5) passed, or waived in writing by the owner. **Capacity** — T01 re-signed from a "
    "Poisson fleet measurement (GW20b). **Console** — R2C-01 … R2C-05 fixed and regression-tested "
    "(G-18). **GPU** — the reserved L4 baseline exists in the production project (GW25)."
)

A["## GW05 — Build the plan compiler, distribution and snapshot with three distinct plan states"] = (
    "> **v3 AMENDMENT (R2-02, R2-03, R2-04, R2-17).** The C36 design held: 0 violations in 15 RC2 fault "
    "runs (flush, re-hydrator gap, versions, Cloud SQL failover and Valkey planned-maintenance failover, "
    "3 each). Three "
    "gaps were proven live and go to new cards that extend this one: GW05b (state freshness and HA "
    "re-hydration — a fresh process on a lagging replica admitted a revoked key 359× in 30 s, and one "
    "idle row lock stalled re-hydration for 38 s) and GW05c (incremental propagation — C4 p99 "
    "145–153 ms at 10,000 tenants). GW06 and GW20 depend on both (§0.8)."
)

A["## GW06 — Build the admission layer with bounded shared state and one round trip"] = (
    "> **v3 AMENDMENT (R2-09, R2-14, R2-19).** (1) Budget-lease refills move off the request path "
    "(asynchronous refill at a low watermark). During a store outage: identity, kill switch and plan "
    "are served from RAM for the declared window; budget uses the remaining lease, then fails with a "
    "distinct `budget_unavailable` reason. (2) One idempotent read may retry once on timeout, and "
    "gateways are placed in the store primary's zone where possible: about 1e-4 of cross-zone round "
    "trips hit the 200 ms minimum TCP RTO. (3) The store-connection boundary keeps the D1 / D2 fixes "
    "(a keepalive ETIMEDOUT is an error, not \"no message\"; dead pooled sockets reconnect), with the "
    "45 s partition regression test."
)

A["## GW08 — Build the detector framework and the GuardBackend interface"] = (
    "> **v3 AMENDMENT (R2-07, R2-23).** C43 load-aware routing is proven (owner queue-wait ratio "
    "0.16–0.20 with it on vs 0.48–0.50 off). Its push fan-out costs 72 pushes per request at 36 "
    "workers (+2.3 gateway CPU-ms per request). The owner coalesces pushes, bounds the push rate per "
    "subscriber and declares the complexity. Exit evidence adds an owner-CPU microbenchmark at 36 / 120 "
    "/ 240 subscribers and a targeted knee re-measure (G-09)."
)

A["## GW12 — Build the SSE egress pipeline with bounded buffers, backpressure and real cancellation"] = (
    "> **v3 AMENDMENT (R2-06).** Holdback is capped by GW12b (≤ 3 upstream tokens by default, tail "
    "rescans bounded to a window). As built, the scanner held a UUID for 36 tokens and base64 blobs "
    "whole, with 17 ms loop blocks per chunk at 16 KB. What happens to a live stream when its org's "
    "kill switch is engaged (finish, or cut at the next chunk) is an owner decision (§0.7-1, G-17)."
)

A["## GW14 — Build async bounded audit and the honest timing instrument"] = (
    "> **v3 AMENDMENT (R2-05, R2-10, R2-11).** Audit gets a global memory budget in the store and a "
    "durable sink with a visible loss counter (GW14c). Monitoring gets a contract under which it never "
    "fails a request and can see the SLO (GW14d). Round-2 evidence: audit streams grew from 1.48 to "
    "6.42 GiB in 51 minutes on a shared 10.4 GiB store; a flush erased 36% of audit records while "
    "completeness read 1.0; the metrics directory overflowed at ≈ 480 UUID-named tenants."
)

A["## GW19 — Implement admission control, overload semantics and graceful drain"] = (
    "> **v3 AMENDMENT (R2-07, R2-08, R2-18).** Owner-level CoDel is the admission rule. Measured: "
    "admitted C4 p99 17.8 ms under a 4.3× burst, every shed declared, recovery in 5 s. The 12 ms "
    "instantaneous bound shed long prompts first and is removed. Overload sheds carry a minimum "
    "Retry-After (proposed ≥ 1 s, jittered) instead of 6–11 ms, because the OpenAI SDK retries twice "
    "on retry-after-ms; retry amplification is gate G-06. After a store partition heals, C4 must be "
    "back inside the SLO within 10 s (G-15); round 2 measured 10–180 s."
)

A["## GW20 — Measure one v2 serving unit and prove horizontal scaling"] = (
    "> **v3 AMENDMENT (R2-01, R2-16, R2-21; GW20b).** Knees are signed only by GW20b: Poisson "
    "arrivals, fleet scale, harness v2.3 or later with multi-key, long-prompt, retry and "
    "secret-shaped-output strata, and three repeats at the pass point. Until then the round-2 "
    "measurements (§0.2) are the planning basis: 100 RPS per L4 with Poisson arrivals (split), and "
    "190–210 per guard and ≥ 570 per gateway at constant arrivals. The autoscaling step tests (E / H) "
    "were not run: gate G-01."
)


def main() -> int:
    src = SRC.read_text(encoding="utf-8")
    part0 = PART0.read_text(encoding="utf-8").rstrip("\n")
    lines = src.split("\n")

    def one(pred, what):
        idx = [i for i, ln in enumerate(lines) if pred(ln)]
        if len(idx) != 1:
            sys.exit(f"anchor {what!r}: found {len(idx)} matches, expected 1")
        return idx[0]

    i_banner = one(lambda ln: ln.startswith(V21_BANNER), "v2.1 banner")
    i_part0 = one(lambda ln: ln == OLD_PART0, "v2.1 Part 0 heading")
    i_body = one(lambda ln: ln == FIRST_BODY_HEADING, "first body heading")
    assert i_banner < i_part0 < i_body

    for heading in A:
        one(lambda ln, h=heading: ln == heading, heading)

    out = []
    for i, ln in enumerate(lines):
        if i == i_banner:
            out += [V3_BANNER, ""]
        if i == i_part0:
            out += [part0, "", NEW_PART0A, "", PART0A_NOTE]
            continue
        if i_part0 < i < i_body and ln.startswith("## 0."):
            ln = "## 0-A." + ln[len("## 0."):]
        out.append(ln)
        if ln in A:
            out += ["", A[ln]]

    text = "\n".join(out)
    for old, new, n in XREFS:
        c = text.count(old)
        if c != n:
            sys.exit(f"xref {old!r}: found {c}, expected {n}")
        text = text.replace(old, new)

    DST.write_text(text, encoding="utf-8")
    print(f"wrote {DST} ({len(text.splitlines())} lines; {len(A)} amendment blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
