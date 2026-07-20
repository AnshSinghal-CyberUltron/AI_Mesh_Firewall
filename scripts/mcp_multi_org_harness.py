#!/usr/bin/env python3
"""P8/P9 multi-org MCP harness — parallel tool calls across ALL provisioned
(org x server) MCPs via the gateway, with per-call audit + tenant-isolation
assertions.

Consumes the git-ignored manifest written by ``scripts/mcp_scale_provision.py``
(org slug -> gateway key -> server slugs). For every provisioned target it fires,
concurrently:

  * ``tools/list``  — capability probe (expect the deterministic Everything set)
  * ``echo``        — a UNIQUE per-target canary; the response MUST echo exactly
                      that canary and carry the exact JSON-RPC id we sent. Firing
                      all targets at once and matching canary+id proves there is
                      no cross-request/cross-tenant response mixing under load.
  * ``get-sum``     — deterministic arithmetic (a+b) whose operands are derived
                      from the target, proving each call is routed to the right
                      sandbox and returns the right answer.

Then it runs a cross-tenant NEGATIVE matrix: each org's gateway key is used
against every OTHER org's gateway path. Every such call MUST be rejected
(401/403) — an accepted call is a cross-tenant isolation breach (fail closed).

Audit: every call records org, server, tool, the JSON-RPC id sent + returned,
HTTP status, latency, and a pass/fail verdict. This is the per-call audit trail
(item #27). Byte-level network egress lockdown is enforced separately in the
broker (``docker_manager`` egress controls, P4.12) and is not re-proven here.

Pure stdlib (urllib + ThreadPoolExecutor) so it runs with a bare ``python3``.

  GATEWAY_URL=http://127.0.0.1:8300 \\
  SCALE_MANIFEST=scripts/ralph/.mcp_scale_manifest.json \\
  ROUNDS=1 python scripts/mcp_multi_org_harness.py
"""
from __future__ import annotations

import json
import os
import time
import uuid
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
MANIFEST = os.environ.get(
    "SCALE_MANIFEST",
    os.path.join(os.path.dirname(__file__), "ralph", ".mcp_scale_manifest.json"),
)
ROUNDS = int(os.environ.get("ROUNDS", "1"))
MAX_WORKERS = int(os.environ.get("HARNESS_WORKERS", "32"))
TIMEOUT = float(os.environ.get("HARNESS_TIMEOUT", "60"))
REPORT_PATH = os.environ.get(
    "REPORT_PATH",
    os.path.join(
        os.path.dirname(__file__), "..", "mcp-parallel", "findings", "p8-26",
        "multi_org_harness_report.json",
    ),
)
EXPECTED_TOOLS = int(os.environ.get("EXPECTED_TOOLS", "13"))

# P9.28 high-concurrency storm: DEPTH in-flight calls per target, all targets
# fired at once (15 * DEPTH simultaneous). Each call carries a globally-unique
# canary + JSON-RPC id so a dropped, duplicated, or cross-wired response is
# caught by canary/id mismatch (broker stdio id-demux correctness under load).
CONCURRENCY_DEPTH = int(os.environ.get("CONCURRENCY_DEPTH", "12"))
CONCURRENCY_ROUNDS = int(os.environ.get("CONCURRENCY_ROUNDS", "3"))
# Fire the whole storm at once: pool wide enough that all 15*DEPTH calls are in
# flight together (broker/sandbox stdio demux is the thing under test, not the
# client thread pool). Capped so we don't fork unbounded threads.
CONCURRENCY_WORKERS = int(os.environ.get("CONCURRENCY_WORKERS", "240"))

# P9.29 SUSTAINED LOAD (Cursor angle). Opt-in via SUSTAINED=1 so the default #28
# gate behaviour is unchanged. Instead of a single burst, this holds a STEADY
# in-flight concurrency (SUSTAINED_INFLIGHT) for SUSTAINED_SECONDS, continuously
# refilling as calls complete, to prove: (a) the per-org sandbox is REUSED — no
# duplicate containers, no runaway process/pid growth (pooling holds); (b) no 503
# storm — 503s (if any) stay bounded and never cluster; (c) the fleet RECOVERS
# after load. Default in-flight (90 = ~6/target across 15) sits below the
# ~180-wide control/gateway saturation ceiling documented in p9-28, so at this
# depth the Cursor-owned broker path should be clean; an optional short overload
# micro-burst (SUSTAINED_OVERLOAD=1) then probes 503/error handling + recovery.
SUSTAINED = os.environ.get("SUSTAINED", "0") == "1"
SUSTAINED_SECONDS = float(os.environ.get("SUSTAINED_SECONDS", "90"))
SUSTAINED_INFLIGHT = int(os.environ.get("SUSTAINED_INFLIGHT", "90"))
SUSTAINED_WORKERS = int(os.environ.get("SUSTAINED_WORKERS", "120"))
# Gate thresholds for the sustained (sub-saturation) window.
SUSTAINED_MAX_ERR_RATE = float(os.environ.get("SUSTAINED_MAX_ERR_RATE", "0.02"))
SUSTAINED_MAX_503_RATE = float(os.environ.get("SUSTAINED_MAX_503_RATE", "0.01"))
SUSTAINED_MAX_503_BURST = int(os.environ.get("SUSTAINED_MAX_503_BURST", "20"))
# Optional overload micro-burst (503/retry probe) after the steady window.
SUSTAINED_OVERLOAD = os.environ.get("SUSTAINED_OVERLOAD", "0") == "1"
SUSTAINED_OVERLOAD_WIDTH = int(os.environ.get("SUSTAINED_OVERLOAD_WIDTH", "300"))


@dataclass
class Target:
    org: str
    key: str
    server: str


@dataclass
class CallRecord:
    org: str
    server: str
    tool: str
    phase: str
    rpc_id_sent: str
    rpc_id_recv: str | None
    http_status: int
    latency_ms: float
    ok: bool
    detail: str = ""
    # Concurrency-storm forensics. canary_sent = the exact result text we expect;
    # canary_recv = the exact result text actually returned. cross_target = the
    # response carried a DIFFERENT (org, server)'s canary (a cross-tenant mix).
    # errored = the response was a well-formed JSON-RPC *error* (correct id, no
    # result payload) — a reliability signal under saturation, NOT a drop or mix.
    canary_sent: str = ""
    canary_recv: str = ""
    cross_target: bool = False
    errored: bool = False
    # Wall-clock completion time (epoch seconds) — used only by the sustained
    # phase to bucket 503s per second and detect a 503 storm/cluster.
    ts_done: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Summary:
    rounds: int = 0
    targets: int = 0
    calls: list[CallRecord] = field(default_factory=list)

    def add(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def failures(self) -> list[CallRecord]:
        return [c for c in self.calls if not c.ok]


def _post(target_org: str, server: str, key: str, payload: dict) -> tuple[int, dict | None, float]:
    url = f"{GATEWAY_URL}/gateway/{target_org}/mcp/{server}"
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    req = urllib.request.Request(url, method="POST", data=data, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = json.load(r)
            return r.status, body, (time.perf_counter() - t0) * 1000
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {"error": e.read().decode()[:300]}
        return e.code, body, (time.perf_counter() - t0) * 1000
    except Exception as exc:  # noqa: BLE001 — network/timeout captured as a failed call
        return 0, {"error": str(exc)}, (time.perf_counter() - t0) * 1000


def _text(body: dict | None) -> str:
    if not body:
        return ""
    result = body.get("result") or {}
    for item in result.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            return item["text"]
    return ""


def call_tools_list(t: Target) -> CallRecord:
    rid = str(uuid.uuid4())
    status, body, ms = _post(t.org, t.server, t.key, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": {}})
    tools = ((body or {}).get("result") or {}).get("tools") or []
    ok = status == 200 and len(tools) >= EXPECTED_TOOLS and (body or {}).get("error") is None
    return CallRecord(t.org, t.server, "tools/list", "capability", rid,
                      (body or {}).get("id"), status, ms, ok,
                      detail=f"tools={len(tools)}")


def call_echo(t: Target, rnd: int) -> CallRecord:
    rid = str(uuid.uuid4())
    canary = f"echo-{t.org}-{t.server}-r{rnd}-{rid[:8]}"
    status, body, ms = _post(t.org, t.server, t.key, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": canary}}})
    txt = _text(body)
    id_ok = (body or {}).get("id") == rid
    ok = status == 200 and txt == f"Echo: {canary}" and id_ok
    return CallRecord(t.org, t.server, "echo", "echo", rid, (body or {}).get("id"),
                      status, ms, ok, detail=f"canary_ok={txt == f'Echo: {canary}'} id_ok={id_ok}")


def call_get_sum(t: Target, rnd: int) -> CallRecord:
    rid = str(uuid.uuid4())
    a = (hash((t.org, t.server, rnd)) % 900) + 100
    b = (hash((t.server, t.org, rnd)) % 900) + 100
    status, body, ms = _post(t.org, t.server, t.key, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/call",
        "params": {"name": "get-sum", "arguments": {"a": a, "b": b}}})
    txt = _text(body)
    expected = f"The sum of {a} and {b} is {a + b}."
    id_ok = (body or {}).get("id") == rid
    ok = status == 200 and txt == expected and id_ok
    return CallRecord(t.org, t.server, "get-sum", "sum", rid, (body or {}).get("id"),
                      status, ms, ok, detail=f"a={a} b={b} sum_ok={txt == expected} id_ok={id_ok}")


def _split_canary(canary: str) -> tuple[str, str] | None:
    """Canary layout is ``conc~<org>~<server>~...``. Split on ``~`` (org slugs and
    server slugs contain ``-``, so ``~`` is the only unambiguous delimiter)."""
    parts = canary.split("~")
    if len(parts) >= 3 and parts[0] == "conc":
        return parts[1], parts[2]
    return None


def _err_msg(body: dict) -> str:
    err = body.get("error")
    return err.get("message", "") if isinstance(err, dict) else ""


def call_echo_conc(t: Target, rnd: int, seq: int) -> CallRecord:
    """One echo in the concurrency storm. The canary embeds the target so a
    response wired to the wrong sandbox is detectable as a cross-target mix."""
    rid = str(uuid.uuid4())
    canary = f"conc~{t.org}~{t.server}~r{rnd}~s{seq}~{rid[:8]}"
    expected = f"Echo: {canary}"
    status, body, ms = _post(t.org, t.server, t.key, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": canary}}})
    body = body or {}
    txt = _text(body)
    errored = isinstance(body.get("error"), dict)
    id_ok = body.get("id") == rid
    content_ok = txt == expected
    cross = False
    if txt.startswith("Echo: "):
        owner = _split_canary(txt[len("Echo: "):])
        if owner and (owner[0] != t.org or owner[1] != t.server):
            cross = True
    ok = status == 200 and content_ok and id_ok and not cross and not errored
    return CallRecord(t.org, t.server, "echo", "concurrency", rid, body.get("id"),
                      status, ms, ok,
                      detail=f"content_ok={content_ok} id_ok={id_ok} cross={cross} "
                             f"errored={errored} {(_err_msg(body))[:80]}",
                      canary_sent=expected, canary_recv=txt,
                      cross_target=cross, errored=errored)


def call_get_sum_conc(t: Target, rnd: int, seq: int) -> CallRecord:
    """One get-sum in the concurrency storm. Operands are target-unique
    (org+server salted) so a cross-target response mix yields a wrong sum string
    (content mismatch), not a coincidental match."""
    rid = str(uuid.uuid4())
    a = 100 + abs(hash((t.org, t.server, "a", rnd, seq))) % 800
    b = 100 + abs(hash((t.org, t.server, "b", rnd, seq))) % 800
    status, body, ms = _post(t.org, t.server, t.key, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/call",
        "params": {"name": "get-sum", "arguments": {"a": a, "b": b}}})
    body = body or {}
    txt = _text(body)
    expected = f"The sum of {a} and {b} is {a + b}."
    errored = isinstance(body.get("error"), dict)
    id_ok = body.get("id") == rid
    content_ok = txt == expected
    ok = status == 200 and content_ok and id_ok and not errored
    return CallRecord(t.org, t.server, "get-sum", "concurrency", rid, body.get("id"),
                      status, ms, ok,
                      detail=f"a={a} b={b} content_ok={content_ok} id_ok={id_ok} "
                             f"errored={errored} {(_err_msg(body))[:80]}",
                      canary_sent=expected, canary_recv=txt, errored=errored)


def call_echo_sustained(t: Target, seq: int) -> CallRecord:
    """One echo in the sustained steady-state stream (phase='sustained').

    Identical wire semantics to ``call_echo_conc`` (unique canary naming its owning
    target so a cross-wired reply is caught), but tagged 'sustained' and stamped
    with a completion timestamp for the 503-storm/per-second bucket analysis."""
    rec = call_echo_conc(t, 0, seq)
    rec.phase = "sustained"
    rec.ts_done = time.time()
    return rec


def call_get_sum_sustained(t: Target, seq: int) -> CallRecord:
    """One get-sum in the sustained steady-state stream (phase='sustained')."""
    rec = call_get_sum_conc(t, 0, seq)
    rec.phase = "sustained"
    rec.ts_done = time.time()
    return rec


def run_sustained(
    targets: list[Target], seconds: float, inflight: int, workers: int
) -> list[CallRecord]:
    """Hold a STEADY ``inflight`` concurrency across all targets for ``seconds``.

    Continuously refills as calls complete (a true sustained stream, not a single
    burst) so we measure steady-state pooling/reuse, not cold-start. Calls are
    round-robined evenly across every (org, server) target and alternate
    echo/get-sum. Returns every CallRecord produced within the window (plus the
    in-flight tail drained at the deadline)."""
    from concurrent.futures import FIRST_COMPLETED, wait

    records: list[CallRecord] = []
    deadline = time.time() + seconds
    n = len(targets)
    seq = 0

    def _submit(ex: ThreadPoolExecutor):
        nonlocal seq
        t = targets[seq % n]
        s = seq
        seq += 1
        if s % 2 == 0:
            return ex.submit(call_echo_sustained, t, s)
        return ex.submit(call_get_sum_sustained, t, s)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        pending = {_submit(ex) for _ in range(inflight)}
        while time.time() < deadline and pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for fut in done:
                records.append(fut.result())
            while len(pending) < inflight and time.time() < deadline:
                pending.add(_submit(ex))
        for fut in as_completed(pending):
            records.append(fut.result())
    return records


def analyze_sustained(records: list[CallRecord]) -> dict:
    """Classify the sustained stream and detect a 503 storm.

    Reuses the item-#28 zero-tolerance isolation classes (dropped / id_mismatch /
    mixed / cross_target) and, on top, splits reliability into HTTP-503 (broker /
    gateway backpressure) vs JSON-RPC error body (correct id, no result). A 503
    STORM = 503s clustered in time; we bucket completions per wall-clock second
    and take the max 503-per-second as the burst metric."""
    def _cls(c: CallRecord) -> str:
        if c.http_status == 0 or c.rpc_id_recv is None:
            return "dropped"
        if c.rpc_id_recv != c.rpc_id_sent:
            return "id_mismatch"
        if c.cross_target:
            return "cross_target"
        if c.http_status == 503:
            return "http_503"
        if c.errored:
            return "errored"
        if c.canary_recv != "" and c.canary_recv != c.canary_sent:
            return "mixed"
        return "passed"

    classes = [(_cls(c), c) for c in records]
    total = len(records)
    counts = {k: sum(1 for cl, _ in classes if cl == k) for k in (
        "passed", "errored", "http_503", "dropped", "id_mismatch",
        "mixed", "cross_target")}
    isolation_violations = (counts["dropped"] + counts["id_mismatch"]
                            + counts["mixed"] + counts["cross_target"])
    # error rate = anything that wasn't a clean pass (reliability, load-facing).
    err_rate = (total - counts["passed"]) / total if total else 0.0
    rate_503 = counts["http_503"] / total if total else 0.0

    # 503 storm detection: bucket 503 completions by integer second.
    per_sec: dict[int, int] = {}
    for cl, c in classes:
        if cl == "http_503":
            per_sec[int(c.ts_done)] = per_sec.get(int(c.ts_done), 0) + 1
    max_503_burst = max(per_sec.values()) if per_sec else 0

    lat = sorted(c.latency_ms for c in records if c.ok)
    def _pct(p: float) -> float | None:
        if not lat:
            return None
        return lat[min(len(lat) - 1, int(len(lat) * p))]

    return {
        "total": total,
        "passed": counts["passed"],
        "errored_jsonrpc": counts["errored"],
        "http_503": counts["http_503"],
        "dropped": counts["dropped"],
        "id_mismatch": counts["id_mismatch"],
        "mixed": counts["mixed"],
        "cross_target": counts["cross_target"],
        "isolation_violations": isolation_violations,
        "err_rate": round(err_rate, 5),
        "rate_503": round(rate_503, 5),
        "max_503_per_sec": max_503_burst,
        "latency_ms": {"p50": _pct(0.50), "p95": _pct(0.95), "p99": _pct(0.99),
                       "max": (lat[-1] if lat else None)},
    }


def call_cross_tenant(attacker: Target, victim_org: str) -> CallRecord:
    """attacker.key against victim_org's path — MUST be rejected (401/403)."""
    rid = str(uuid.uuid4())
    status, body, ms = _post(victim_org, attacker.server, attacker.key, {
        "jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": {}})
    # PASS = call was rejected. 401/403 rejection. Anything 2xx = breach.
    rejected = status in (401, 403)
    return CallRecord(f"{attacker.org}->{victim_org}", attacker.server, "tools/list",
                      "cross-tenant", rid, (body or {}).get("id"), status, ms, rejected,
                      detail=("rejected" if rejected else "BREACH: cross-tenant call accepted"))


WARM_WORKERS = int(os.environ.get("HARNESS_WARM_WORKERS", "6"))
WARM_ATTEMPTS = int(os.environ.get("HARNESS_WARM_ATTEMPTS", "15"))
WARM_DELAY = float(os.environ.get("HARNESS_WARM_DELAY", "2.0"))


def warmup(t: Target) -> CallRecord:
    """Prime a target until it reports the full tool set (npx cold-start can lag).

    Mirrors the documented model (MCP_CONCURRENT_INITS cap): pre-warm before the
    steady-state concurrency phases so first-touch server starts don't race the
    graded assertions. Bounded retry — returns the last record if never ready.
    """
    rec = call_tools_list(t)
    for _ in range(WARM_ATTEMPTS - 1):
        if rec.ok:
            return rec
        time.sleep(WARM_DELAY)
        rec = call_tools_list(t)
    return rec


def load_targets() -> list[Target]:
    with open(MANIFEST, encoding="utf-8") as f:
        m = json.load(f)
    targets: list[Target] = []
    for org in m.get("orgs", []):
        for srv in org.get("servers", []):
            if srv and not str(srv).startswith("ERR"):
                targets.append(Target(org["slug"], org["gateway_key"], srv))
    return targets


def run_parallel(fns: list, workers: int | None = None) -> list[CallRecord]:
    out: list[CallRecord] = []
    with ThreadPoolExecutor(max_workers=(workers or MAX_WORKERS)) as ex:
        futs = [ex.submit(fn) for fn in fns]
        for fut in as_completed(futs):
            out.append(fut.result())
    return out


def main() -> int:
    targets = load_targets()
    orgs = sorted({t.org for t in targets})
    summary = Summary(rounds=ROUNDS, targets=len(targets))
    print(f"gateway={GATEWAY_URL} targets={len(targets)} orgs={orgs} rounds={ROUNDS}")

    # Phase 0: WARMUP (bounded retry, limited concurrency to respect the broker
    # init cap). Not graded here — its job is to eliminate cold-start races so the
    # graded steady-state phases below measure real behavior, not npx fetch lag.
    warm_ok = 0
    with ThreadPoolExecutor(max_workers=WARM_WORKERS) as ex:
        futs = {ex.submit(warmup, t): t for t in targets}
        for fut in as_completed(futs):
            if fut.result().ok:
                warm_ok += 1
    print(f"warmup: {warm_ok}/{len(targets)} targets ready")

    # Phase 1: capability probe (all targets in parallel, now warm)
    for rec in run_parallel([lambda t=t: call_tools_list(t) for t in targets]):
        summary.add(rec)

    # Phases 2+3: echo + get-sum across all targets, all rounds, fired together.
    # Skipped in SUSTAINED mode — the sustained stream (below) subsumes them and
    # keeps a re-run fast enough to grade 3× consecutively.
    if not SUSTAINED:
        for rnd in range(1, ROUNDS + 1):
            fns: list = []
            for t in targets:
                fns.append(lambda t=t, r=rnd: call_echo(t, r))
                fns.append(lambda t=t, r=rnd: call_get_sum(t, r))
            for rec in run_parallel(fns):
                summary.add(rec)

    # Phase 3.5: HIGH-CONCURRENCY STORM (P9.28) — for each round, fire
    # CONCURRENCY_DEPTH calls per target for ALL 15 targets at once
    # (15 * DEPTH simultaneous), interleaving echo (unique canary) and get-sum
    # (unique operands). Every response is matched on BOTH the JSON-RPC id and
    # the exact payload it should carry; the echo canary additionally names its
    # owning (org, server) so a cross-wired response is flagged cross_target.
    conc_submitted = 0
    conc_records: list[CallRecord] = []
    if not SUSTAINED:
        for rnd in range(1, CONCURRENCY_ROUNDS + 1):
            fns = []
            for t in targets:
                for seq in range(CONCURRENCY_DEPTH):
                    if seq % 2 == 0:
                        fns.append(lambda t=t, r=rnd, s=seq: call_echo_conc(t, r, s))
                    else:
                        fns.append(lambda t=t, r=rnd, s=seq: call_get_sum_conc(t, r, s))
            conc_submitted += len(fns)
            recs = run_parallel(fns, workers=min(len(fns), CONCURRENCY_WORKERS))
            for rec in recs:
                summary.add(rec)
                conc_records.append(rec)

    # Concurrency integrity — classify every stormed call precisely. The item
    # #28 invariants (zero-tolerance) are: DROPPED (no JSON-RPC response came
    # back), ID-MISMATCH (a response carried a different call's id — demux mix),
    # MIXED (a result payload that isn't this call's — response body mix), and
    # CROSS-TARGET (a result carrying another (org,server)'s canary). An ERRORED
    # call (well-formed JSON-RPC error, correct id, no result) is NOT a drop or a
    # mix — it is a reliability signal handed to item #29 (load/saturation).
    def _cls(c: CallRecord) -> str:
        if c.http_status == 0 or c.rpc_id_recv is None:
            return "dropped"
        if c.rpc_id_recv != c.rpc_id_sent:
            return "id_mismatch"
        if c.cross_target:
            return "cross_target"
        if c.errored:
            return "errored"
        if c.canary_recv != "" and c.canary_recv != c.canary_sent:
            return "mixed"
        return "passed"

    conc_cls = [(_cls(c), c) for c in conc_records]
    counts = {k: sum(1 for cl, _ in conc_cls if cl == k)
              for k in ("passed", "errored", "dropped", "id_mismatch",
                        "mixed", "cross_target")}
    gate_violations = [c for cl, c in conc_cls
                       if cl in ("dropped", "id_mismatch", "mixed", "cross_target")]
    concurrency_integrity = {
        "depth_per_target": CONCURRENCY_DEPTH,
        "rounds": CONCURRENCY_ROUNDS,
        "targets": len(targets),
        "submitted": conc_submitted,
        "returned": len(conc_records),
        "passed": counts["passed"],
        "errored_under_load": counts["errored"],
        "dropped": counts["dropped"],
        "id_mismatch": counts["id_mismatch"],
        "mixed": counts["mixed"],
        "cross_target": counts["cross_target"],
        "gate_violations": len(gate_violations),
    }
    print("concurrency integrity:", json.dumps(concurrency_integrity))

    # Phase 3.6: SUSTAINED LOAD (P9.29, Cursor angle). Opt-in. Hold a steady
    # in-flight concurrency for a sustained window, then (optionally) an overload
    # micro-burst to probe 503/error handling, then a RECOVERY probe. The gate is
    # zero isolation violations + bounded error/503 rate + no 503 storm +
    # full recovery. Out-of-band docker reuse/cgroup checks are captured by the
    # findings runner alongside this (broker sandbox pooling is Cursor-owned).
    sustained_report: dict | None = None
    if SUSTAINED:
        print(f"sustained: inflight={SUSTAINED_INFLIGHT} for {SUSTAINED_SECONDS}s "
              f"across {len(targets)} targets ...")
        s_recs = run_sustained(targets, SUSTAINED_SECONDS, SUSTAINED_INFLIGHT,
                               SUSTAINED_WORKERS)
        for rec in s_recs:
            summary.add(rec)
        s_analysis = analyze_sustained(s_recs)
        wall = max((c.ts_done for c in s_recs), default=0.0) - \
            min((c.ts_done for c in s_recs), default=0.0)
        s_analysis["throughput_rps"] = round(len(s_recs) / wall, 2) if wall > 0 else None

        overload = None
        if SUSTAINED_OVERLOAD:
            print(f"sustained: overload micro-burst {SUSTAINED_OVERLOAD_WIDTH}-wide "
                  f"(503/retry probe) ...")
            ofns = []
            per = max(1, SUSTAINED_OVERLOAD_WIDTH // len(targets))
            for t in targets:
                for s in range(per):
                    if s % 2 == 0:
                        ofns.append(lambda t=t, s=s: call_echo_sustained(t, 10_000 + s))
                    else:
                        ofns.append(lambda t=t, s=s: call_get_sum_sustained(t, 10_000 + s))
            orecs = run_parallel(ofns, workers=min(len(ofns), CONCURRENCY_WORKERS))
            for rec in orecs:
                summary.add(rec)
            overload = analyze_sustained(orecs)

        # RECOVERY probe: after load, every target must serve tools/list again —
        # proves no lasting exhaustion, no wedged pool, sandbox still reused.
        rec_probe = run_parallel([lambda t=t: call_tools_list(t) for t in targets])
        for rec in rec_probe:
            summary.add(rec)
        recovery_ok = sum(1 for c in rec_probe if c.ok)

        s_ok = (
            s_analysis["isolation_violations"] == 0
            and s_analysis["err_rate"] <= SUSTAINED_MAX_ERR_RATE
            and s_analysis["rate_503"] <= SUSTAINED_MAX_503_RATE
            and s_analysis["max_503_per_sec"] <= SUSTAINED_MAX_503_BURST
            and recovery_ok == len(targets)
        )
        sustained_report = {
            "config": {
                "inflight": SUSTAINED_INFLIGHT, "seconds": SUSTAINED_SECONDS,
                "workers": SUSTAINED_WORKERS,
                "thresholds": {
                    "max_err_rate": SUSTAINED_MAX_ERR_RATE,
                    "max_503_rate": SUSTAINED_MAX_503_RATE,
                    "max_503_burst": SUSTAINED_MAX_503_BURST,
                },
            },
            "steady": s_analysis,
            "overload_burst": overload,
            "recovery": {"ready": recovery_ok, "targets": len(targets)},
            "pass": s_ok,
        }
        print("sustained:", json.dumps({
            "steady": {k: s_analysis[k] for k in (
                "total", "passed", "errored_jsonrpc", "http_503",
                "isolation_violations", "err_rate", "rate_503",
                "max_503_per_sec", "throughput_rps", "latency_ms")},
            "recovery": sustained_report["recovery"],
            "pass": s_ok,
        }, indent=2))

    # Phase 4: cross-tenant negative matrix — one server per org attacking every other org
    cross_fns: list = []
    by_org: dict[str, Target] = {}
    for t in targets:
        by_org.setdefault(t.org, t)
    for atk_org, atk in by_org.items():
        for victim in by_org:
            if victim != atk_org:
                cross_fns.append(lambda a=atk, v=victim: call_cross_tenant(a, v))
    for rec in run_parallel(cross_fns):
        summary.add(rec)

    # Report. The gate = every non-concurrency failure + every concurrency
    # #28-invariant violation (drop/mix/cross/id). errored_under_load is a
    # reliability signal (item #29), reported but not a #28 gate failure unless
    # CONCURRENCY_STRICT_ERRORS=1 is set (used by the #29 load gate).
    strict_errors = os.environ.get("CONCURRENCY_STRICT_ERRORS", "0") == "1"
    # The 'sustained' phase is graded by its own bounded-error/503/recovery gate
    # (sustained_report["pass"]) — its individual errored-under-load records are a
    # reliability signal, not a per-call gate failure, so exclude them here.
    non_conc_fails = [c for c in summary.calls
                      if c.phase not in ("concurrency", "sustained") and not c.ok]
    fails = non_conc_fails + gate_violations
    if strict_errors:
        fails = fails + [c for cl, c in conc_cls if cl == "errored"]
    sustained_fail = bool(sustained_report) and not sustained_report["pass"]
    lat = [c.latency_ms for c in summary.calls
           if c.phase in ("echo", "sum", "concurrency", "sustained") and c.ok]
    _phases = ("capability", "echo", "sum", "concurrency", "sustained", "cross-tenant")
    report = {
        "gateway": GATEWAY_URL,
        "orgs": orgs,
        "targets": len(targets),
        "warm_ready": warm_ok,
        "rounds": ROUNDS,
        "total_calls": len(summary.calls),
        "failures": len(fails),
        "concurrency_integrity": concurrency_integrity,
        "sustained": sustained_report,
        "phase_counts": {
            p: sum(1 for c in summary.calls if c.phase == p) for p in _phases
        },
        "phase_pass": {
            p: sum(1 for c in summary.calls if c.phase == p and c.ok)
            for p in _phases
        },
        "latency_ms": {
            "p50": (sorted(lat)[len(lat) // 2] if lat else None),
            "max": (max(lat) if lat else None),
        },
        "failed_calls": [c.to_dict() for c in fails][:50],
        "calls": [c.to_dict() for c in summary.calls],
    }
    os.makedirs(os.path.dirname(os.path.abspath(REPORT_PATH)), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    print(json.dumps({k: report[k] for k in (
        "targets", "rounds", "total_calls", "failures", "concurrency_integrity",
        "phase_counts", "phase_pass", "latency_ms")}, indent=2))
    print(f"report -> {REPORT_PATH}")
    verdict_fail = bool(fails) or sustained_fail
    verdict = "GREEN" if not verdict_fail else "RED"
    print("HARNESS:", verdict)
    if fails:
        for c in fails[:10]:
            print(f"  FAIL {c.phase} {c.org}/{c.server} {c.tool} status={c.http_status} {c.detail}")
    if sustained_fail:
        print(f"  FAIL sustained gate: {json.dumps(sustained_report['steady'])} "
              f"recovery={sustained_report['recovery']}")
    return 0 if not verdict_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
