"""ResourceContract — the only module allowed to hold capacity-position literals."""

from __future__ import annotations

import math
import os
import warnings
from dataclasses import dataclass

from gateway_v2.runtime.cgroup import DetectHooks, detect_with_hooks
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import CapacityHint, HardwareSignals, PoolKind

# Deployment-declared defaults. Logged as defaults when env is unset.
_DEFAULT_TARGET_P99_MS = 20.0
_DEFAULT_UTILIZATION_CAP = 0.75
_DEFAULT_PER_WORKER_RSS = 400 * 1024 * 1024
_MS_PER_S = 1000.0

# B4 (GW17-18 / C43) dispatch-routing capacity bounds. These are owner-queue / guard-wait
# positions the `dispatch` layer reads through the contract so no caller holds a literal; the
# derivation lives here because this is the only module `lint/check_capacity_literals.py` exempts.
_OWNER_QUEUE_ENV_KEY = "RV_OWNER_QUEUE_MS"
_DEFAULT_OWNER_QUEUE_MS = 20.0
"""C43 owner-queue bound default in milliseconds (runbook RC-measured `RV_OWNER_QUEUE_MS=20`).

An operational env knob, not a derivation of the SLO: the runbook's load-aware guard routing is
measured against a ~20 ms owner-queue bound ("760 x3 PASS, 190 per guard, 20 ms owner bound").
`owner_queue_bound_s()` reads `RV_OWNER_QUEUE_MS` with this measured default and warns-and-defaults
on an unparseable or non-positive override, the same env to value idiom `load_contract` /
`holdback_config` use. It lives here (not in `dispatch`) so `GuardRouter` holds no literal.
"""
_CODEL_HARD_CAP_MS = 60.0
"""CoDel admission hard cap in milliseconds (GW19 / R2-07).

The admission controller's CoDel hard cap. The GW19 guard-wait budget is `max(100, cap + 40)` ms
with `cap` this hard cap; `guard_wait_budget_s()` derives the budget from this capacity position so
both operands stay in the one capacity-authority module and no caller inlines either number.
"""
_GUARD_WAIT_FLOOR_MS = 100.0  # GW19 guard-wait budget floor: max(floor, cap + headroom)
_GUARD_WAIT_HEADROOM_MS = 40.0  # GW19 guard-wait headroom added above the CoDel hard cap

# GW12 stream-serving derivations. Each timeout/duration/bound is a multiple of the request-path
# SLO (`target_p99_ms`), so there is one capacity authority and no caller holds a literal. This is
# the only module `lint/check_capacity_literals.py` exempts.
_INTER_CHUNK_TIMEOUT_MULT = 50.0  # inter-chunk budget = p99 x mult
_IDLE_TIMEOUT_MULT = 150.0  # downstream idle budget
_WRITE_TIMEOUT_MULT = 25.0  # single downstream write budget
_MAX_STREAM_DURATION_MULT = 15000.0  # wall-clock stream lifetime
_MAX_CUT_LATENCY_MULT = 2.0  # a cut must complete within p99 x mult
_MAX_SNAPSHOT_AGE_MULT = 1.0  # control-plane snapshot freshness vs p99
_CANCEL_BOUND_MULT = 3.0  # disconnect -> full-kill budget

_AUDIT_QUEUE_MEMORY_SHARE = 0.02
"""Share of a worker's usable memory the audit queue may occupy (GW14c).

Audit is not the product. 2% of the usable limit is enough to ride out a store blip of several
SLO periods at realistic drain rates, and small enough that a queue sized by a fast drain rate
cannot OOM the worker it exists to keep serving.
"""

# GW14d observability-contract derivations. The metrics Cardinality_Directory capacity, its
# bounded top-K slot count, the windowed-histogram window length, and the structured event-log
# rate are all functions of existing contract fields, so there is one capacity authority and no
# `edge` module holds a literal. This is the only module `lint/check_capacity_literals.py`
# exempts.
_METRICS_DIRECTORY_SLOTS_MULT = 4.0  # directory capacity = connection_budget x mult
"""Per-tenant cardinality slots as a multiple of the connection budget (R2-10 / R2.7).

The directory sizes "how many distinct tenants may each keep their own metric slot", and a tenant
is observed through a connection, so the connection budget is the natural scale. A small multiple
of it lets the busiest tenants each hold a slot while bounding the directory by construction;
everything beyond it collapses to `other` + the Overflow_Counter rather than growing the series
set, which is exactly the R2-10 defect this closes.
"""
_METRICS_TOP_K_FRACTION = 0.25  # K = floor(fraction x metrics_directory_slots())
"""Fraction of the directory capacity that keeps an INDIVIDUAL per-tenant series (R3.3).

K is the bound on named per-tenant visibility (top-K + one `other`). It is a fixed fraction of the
directory capacity so it never changes with the observed tenant count, and strictly below the
capacity so the directory can admit a tenant without that tenant necessarily earning its own
exported series.
"""
_METRICS_WINDOW_MULT = 15000.0  # window_s = (target_p99_ms / 1000) x mult
"""Windowed-histogram window length as a multiple of the request-path SLO (R4.2).

The server-side C4 add-on histogram must track the client synthetic over the SAME window, so the
window is derived from the one SLO authority rather than a wall-clock literal. The multiple yields
a several-minute window at the default 20 ms SLO — long enough for a stable p99, short enough that
a transient does not haunt the quantile — matching the GW12 `_MAX_STREAM_DURATION_MULT` scale.
"""
_EVENT_LOG_RATE_MULT = 2.0  # lines/window = workers x mult
"""Structured-event-log lines per class per window as a multiple of worker count (R9.3).

Each worker can independently hit a client-error class, so the per-class per-window line budget
scales with the worker count. A small multiple keeps every error class traceable to a concrete log
line while bounding the volume a burst of identical events can produce — the Rate_Limited contract.
"""

# GW15 Pre_Identity_Clock pre-check bounds (C26). The three cheap pre-checks — raw request size,
# a cheap expansion-ratio proxy, and a structural part count — reject oversized / expansion-bomb /
# too-many-parts requests BEFORE the auth-independent heavy canonicalization (an 18x NFKC-expanding
# 1 MiB body cost 7.4 s of CPU in the register). Their bounds derive from existing contract fields
# (or are principled module constants with a documented rationale) so there is one capacity
# authority and `edge` holds no literal — this is the only module `lint/check_capacity_literals.py`
# exempts.
_MAX_REQUEST_BYTES_SHARE = 0.0025
"""Share of a single worker's RSS a raw request body may occupy before the cheap size pre-check
rejects it (C26, R3).

The size pre-check caps the raw bytes a request may cost before any canonicalization. A request
body is transient per-request state, so its ceiling is naturally a small share of the per-request
memory a worker budgets (`per_worker_rss`): at the 400 MiB default this yields a ~1 MiB cap, the
exact scale at which the register's 18x-expanding body became a 7.4 s CPU event — large enough for
a legitimate multi-message chat completion, small enough that an oversized body is rejected before
it can drive heavy work. Derived from the contract rather than a wall-clock byte literal so the
cap tracks the deployment's declared per-worker memory.
"""
_MAX_EXPANSION_RATIO = 4.0
"""Maximum cheap expansion-ratio proxy a request may show before the pre-check rejects it (C26, R3).

The expansion-ratio pre-check caps a cheap O(n) proxy for how much a body would grow under full
canonicalization (NFKC fold), computed BEFORE that canonicalization runs — the register's
pathological case was an 18x NFKC expansion of a 1 MiB body. A dimensionless multiplier (not a
function of CPU/memory): legitimate text, even with heavy multi-byte UTF-8 and some fullwidth or
compatibility code points, stays well under a 4x cheap-proxy estimate, while an expansion bomb
engineered to blow up under NFKC trips far above it. A principled constant with this documented
rationale rather than an arbitrary number; it lives here (not in `edge`) so there is one capacity
authority.
"""
_MAX_REQUEST_PARTS_MULT = 256
"""Structural message/content-part count ceiling as a multiple of the worker count (C26, R3).

The part-count pre-check caps the number of message / content-part elements a cheap structural
scan finds, rejecting a request whose part count alone would make canonicalization + per-channel
folding expensive regardless of total size. It scales with `workers()` — more serving capacity
tolerates a proportionally larger structural fan-out per request — with a multiple sized so a
normal conversation (system + a few hundred user/assistant/tool turns and their content parts)
passes while a part-count flood is rejected before the fold walks every element. Derived from the
contract (via `workers()`) so the ceiling tracks deployment capacity and `edge` holds no literal.
"""


@dataclass(frozen=True, slots=True)
class ResourceContract:
    cpu_quota: float
    memory_limit: int
    fd_limit: int
    guard_capacity: CapacityHint | None
    target_p99_ms: float
    utilization_cap: float
    per_worker_rss: int
    worker_override: int | None
    cpu_source: str
    mem_source: str
    fd_source: str

    def detected_workers(self) -> int:
        cpu_w = math.floor(self.cpu_quota * self.utilization_cap)
        ram_w = math.floor(self.memory_limit * self.utilization_cap / self.per_worker_rss)
        n = min(cpu_w, ram_w)
        if n < 1:
            raise CapacityUnavailable(
                f"derived workers={n} (cpu={cpu_w} ram={ram_w}); refuse to start",
            )
        return n

    def workers(self) -> int:
        if self.worker_override is not None:
            if self.worker_override < 1:
                raise CapacityUnavailable("WEB_CONCURRENCY override below one")
            return self.worker_override
        return self.detected_workers()

    def binding_signal(self) -> str:
        cpu_w = math.floor(self.cpu_quota * self.utilization_cap)
        ram_w = math.floor(self.memory_limit * self.utilization_cap / self.per_worker_rss)
        if ram_w < cpu_w:
            return "ram"
        return "cpu"

    def offered_service_rate(self) -> float:
        return self.workers() / (self.target_p99_ms / _MS_PER_S)

    def queue_depth(self, service_rate: float) -> int:
        if service_rate <= 0:
            raise CapacityUnavailable("service_rate must be positive")
        slo = math.ceil(
            service_rate * (self.target_p99_ms / _MS_PER_S) / self.utilization_cap,
        )
        ram_slots = math.floor(self.memory_limit * self.utilization_cap / self.per_worker_rss)
        n = min(slo, ram_slots)
        if n < 1:
            raise CapacityUnavailable("queue_depth below minimum to serve")
        return n

    def connection_budget(self) -> int:
        usable = self.fd_limit - self.workers()
        n = math.floor(max(usable, 0) * self.utilization_cap)
        return n

    def pool_size(self, kind: PoolKind) -> int:
        if kind is PoolKind.GUARD:
            return self._guard_pool()
        w = self.workers()
        if kind is PoolKind.SCANNER:
            n = math.floor(self.cpu_quota * self.utilization_cap)
        elif kind is PoolKind.PROVIDER:
            n = w
        elif kind is PoolKind.VAULT:
            n = w
        elif kind is PoolKind.REDIS:
            n = self.connection_budget()
        else:
            raise CapacityUnavailable(f"unknown pool kind {kind}")
        if n < 1:
            raise CapacityUnavailable(f"{kind} pool below minimum to serve")
        return n

    def _guard_pool(self) -> int:
        if self.guard_capacity is None:
            raise CapacityUnset("guard-derived bound unset")
        n = math.floor(
            self.guard_capacity.tokens_per_second * (self.target_p99_ms / _MS_PER_S),
        )
        if n < 1:
            raise CapacityUnavailable("guard pool below minimum to serve")
        return n

    def stream_buffer_bytes(self, active_streams: int) -> int:
        streams = active_streams if active_streams > 0 else 1
        n = math.floor(self.memory_limit * self.utilization_cap / streams)
        if n < 1:
            raise CapacityUnavailable("stream buffer below minimum to serve")
        return n

    def _stream_timeout_s(self, mult: float) -> float:
        """GW12: a stream timeout/duration/bound derived from the request-path SLO.

        value = p99_s x mult, clamped up to a derived floor of one p99 period (no stream bound
        may be shorter than a single SLO unit of work), and fails closed with
        `CapacityUnavailable` on a non-positive result -- matching the shipped
        `stream_buffer_bytes` contract. `target_p99_ms` is positive by construction
        (`from_signals` refuses to start otherwise), so the floor is positive; the guard is the
        same defensive fail-closed stance the rest of the contract takes.
        """
        p99_s = self.target_p99_ms / _MS_PER_S
        value = max(p99_s * mult, p99_s)
        if value <= 0:
            raise CapacityUnavailable("stream timeout below minimum to serve")
        return value

    def inter_chunk_timeout_s(self) -> float:
        return self._stream_timeout_s(_INTER_CHUNK_TIMEOUT_MULT)

    def idle_timeout_s(self) -> float:
        return self._stream_timeout_s(_IDLE_TIMEOUT_MULT)

    def write_timeout_s(self) -> float:
        return self._stream_timeout_s(_WRITE_TIMEOUT_MULT)

    def max_stream_duration_s(self) -> float:
        return self._stream_timeout_s(_MAX_STREAM_DURATION_MULT)

    def max_cut_latency_s(self) -> float:
        return self._stream_timeout_s(_MAX_CUT_LATENCY_MULT)

    def max_snapshot_age_s(self) -> float:
        return self._stream_timeout_s(_MAX_SNAPSHOT_AGE_MULT)

    def cancellation_bound_s(self) -> float:
        return self._stream_timeout_s(_CANCEL_BOUND_MULT)

    def owner_queue_bound_s(self) -> float:
        """B4 (C43): the `RV_OWNER_QUEUE_MS` owner-queue bound, in seconds.

        An operational env knob read with the runbook RC-measured default (~20 ms), returned in
        seconds (20 ms -> 0.020) so `GuardRouter` consumes it alongside `cancellation_bound_s()`
        and holds no literal. An unset key uses the measured default; a present-but-unparseable or
        non-positive value warns naming the rejected value and falls back to the default -- the
        same warn-and-default stance `load_contract` / `holdback_config` take. Reads
        `os.environ` directly (no new contract field) because this is a dispatch-routing env knob,
        not a hardware/SLO signal threaded through `from_signals`.
        """
        return _parse_owner_queue_ms(os.environ.get(_OWNER_QUEUE_ENV_KEY)) / _MS_PER_S

    def guard_wait_budget_s(self) -> float:
        """B4 (GW19 / R2-07): the guard-wait budget `max(100, cap + 40)` ms, in seconds.

        `cap` is the CoDel admission hard cap (`_CODEL_HARD_CAP_MS`); the budget is the larger of a
        floor and the hard cap plus a fixed headroom. Both operands are capacity positions declared
        in this module, so the derivation lives with the one capacity authority and `dispatch`
        inlines neither number. At the signed defaults this is `max(0.100, 0.060 + 0.040) = 0.100`.
        """
        budget_ms = max(_GUARD_WAIT_FLOOR_MS, _CODEL_HARD_CAP_MS + _GUARD_WAIT_HEADROOM_MS)
        return budget_ms / _MS_PER_S

    def audit_queue_depth(self, drain_rate_per_s: float, bytes_per_record: int) -> int:
        """GW14c: how many audit records the producer's queue may hold.

        GW14 states the bound as "the contract's `queue_depth()` at the writer's measured drain
        rate", and it is deliberately NOT `queue_depth()` itself: that one is bounded by
        `per_worker_rss`, because its slots are in-flight REQUESTS. An audit record is a few KB,
        so sizing a record queue by a worker's RSS would give a depth of tens and drop audit
        under any load at all.

        Two bounds, and the smaller wins:

        * **Time.** `drain_rate x stall_budget`, where the stall budget is the request-path SLO
          divided by the utilisation cap. A queue depth IS a stall budget: it is how long the
          writer may be stuck before `emit` starts dropping, and a store blip longer than that
          is a loss event that has to be counted rather than absorbed.
        * **Memory.** The share of the worker's memory the queue may occupy, over the bytes a
          record costs. Without this a high drain rate would authorise a queue that OOMs the
          worker it is protecting — trading a counted audit loss for an uncounted request loss.

        `drain_rate_per_s` comes from `AuditSink.calibrate()` against the real store, so the
        depth is measured rather than assumed.
        """
        if drain_rate_per_s <= 0:
            raise CapacityUnavailable("audit drain rate must be positive")
        if bytes_per_record < 1:
            raise CapacityUnavailable("audit bytes per record must be positive")
        stall_budget_s = (self.target_p99_ms / _MS_PER_S) / self.utilization_cap
        by_time = math.ceil(drain_rate_per_s * stall_budget_s)
        queue_memory = self.memory_limit * self.utilization_cap * _AUDIT_QUEUE_MEMORY_SHARE
        by_memory = math.floor(queue_memory / bytes_per_record)
        n = min(by_time, by_memory)
        if n < 1:
            raise CapacityUnavailable(
                f"audit queue depth below minimum to serve (time={by_time} memory={by_memory}): "
                "the worker cannot hold even one record, so every record would be dropped and "
                "counted -- fix the memory limit or the record size before starting",
            )
        return n

    def metrics_directory_slots(self) -> int:
        """GW14d: Cardinality_Directory capacity — how many tenants may each hold a slot.

        Derived from `connection_budget()` (a tenant is observed through a connection), not a
        literal, so the capacity authority stays in this module (R2-10 / R2.7, R3.3). Fails closed
        with `CapacityUnavailable` if the derived value is below one slot — a directory that cannot
        hold even a single tenant could never admit one, matching the fail-closed stance every
        shipped contract method takes.
        """
        n = math.floor(self.connection_budget() * _METRICS_DIRECTORY_SLOTS_MULT)
        if n < 1:
            raise CapacityUnavailable("metrics directory slots below minimum to serve")
        return n

    def metrics_top_k(self) -> int:
        """GW14d: K — the bounded count of INDIVIDUAL per-tenant series (R3.3).

        A fixed fraction of `metrics_directory_slots()`, so K never changes with the observed
        tenant count, and `metrics_top_k() <= metrics_directory_slots()` holds by construction
        (fraction in (0, 1]). Fails closed with `CapacityUnavailable` if the derived K is below
        one, matching every shipped contract method.
        """
        n = math.floor(self.metrics_directory_slots() * _METRICS_TOP_K_FRACTION)
        if n < 1:
            raise CapacityUnavailable("metrics top-K below minimum to serve")
        return n

    def metrics_window_s(self) -> float:
        """GW14d: windowed-histogram window length in seconds (R4.2).

        value = p99_s x mult. The server-side C4 add-on window is derived from the one request-path
        SLO authority so it can track the client synthetic over the SAME window. `target_p99_ms` is
        positive by construction (`from_signals` refuses to start otherwise); the fail-closed guard
        on a non-positive window is the same defensive stance the rest of the contract takes.
        """
        value = (self.target_p99_ms / _MS_PER_S) * _METRICS_WINDOW_MULT
        if value <= 0:
            raise CapacityUnavailable("metrics window below minimum to serve")
        return value

    def event_log_rate(self) -> int:
        """GW14d: structured-event-log lines per class per window (R9.3).

        value = workers x mult. Each worker can independently hit a client-error class, so the
        per-class per-window line budget scales with the worker count. Fails closed with
        `CapacityUnavailable` if the derived rate is below one line — a class that could never emit
        even one line would be invisible — matching every shipped contract method.
        """
        n = math.floor(self.workers() * _EVENT_LOG_RATE_MULT)
        if n < 1:
            raise CapacityUnavailable("event log rate below minimum to serve")
        return n

    def max_request_bytes(self) -> int:
        """GW15 C26: maximum raw request-body bytes the cheap size pre-check admits (R3).

        A small share of a single worker's RSS (`per_worker_rss x _MAX_REQUEST_BYTES_SHARE`): a
        request body is transient per-request state, so its ceiling scales with the per-request
        memory a worker budgets, not a wall-clock byte literal. Fails closed with
        `CapacityUnavailable` if the derived cap is below one byte — a surface that could not admit
        even a single byte could never serve — matching every shipped contract method.
        """
        n = math.floor(self.per_worker_rss * _MAX_REQUEST_BYTES_SHARE)
        if n < 1:
            raise CapacityUnavailable("max request bytes below minimum to serve")
        return n

    def max_expansion_ratio(self) -> float:
        """GW15 C26: maximum cheap expansion-ratio proxy the pre-check admits (R3).

        A dimensionless multiplier (`_MAX_EXPANSION_RATIO`) bounding a cheap O(n) estimate of how
        much a body would grow under full canonicalization, checked BEFORE that canonicalization
        runs — the register's expansion bomb was an 18x NFKC fold. Not a function of CPU/memory;
        a principled constant with its rationale documented at the constant. The accessor exists so
        `edge` reads the bound through the contract and holds no literal of its own.
        """
        return _MAX_EXPANSION_RATIO

    def max_request_parts(self) -> int:
        """GW15 C26: maximum message/content-part count the structural pre-check admits (R3).

        `workers() x _MAX_REQUEST_PARTS_MULT`: the structural fan-out a request may carry scales
        with serving capacity. Fails closed with `CapacityUnavailable` if the derived ceiling is
        below one part — a surface that could not admit even a single part could never serve a
        request — matching every shipped contract method.
        """
        n = self.workers() * _MAX_REQUEST_PARTS_MULT
        if n < 1:
            raise CapacityUnavailable("max request parts below minimum to serve")
        return n


def _env_map(env: dict[str, str] | None) -> dict[str, str]:
    if env is not None:
        return env
    return dict(os.environ)


def _parse_override(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    return int(raw.strip())


def _parse_owner_queue_ms(raw: str | None) -> float:
    """Parse `RV_OWNER_QUEUE_MS` in milliseconds, warn-and-default on a rejected value.

    An unset / blank key returns the measured default silently. A non-numeric or non-positive
    value falls back to the default and emits a `warnings.warn` naming the rejected value, the same
    stance `load_contract` / `holdback_config` take for an invalid env knob.
    """
    if raw is None or not raw.strip():
        return _DEFAULT_OWNER_QUEUE_MS
    stripped = raw.strip()
    try:
        value = float(stripped)
    except ValueError:
        warnings.warn(
            f"{_OWNER_QUEUE_ENV_KEY}={stripped!r} is not a number; "
            f"using default owner-queue bound {_DEFAULT_OWNER_QUEUE_MS} ms",
            stacklevel=2,
        )
        return _DEFAULT_OWNER_QUEUE_MS
    if value <= 0 or math.isnan(value) or math.isinf(value):
        warnings.warn(
            f"{_OWNER_QUEUE_ENV_KEY}={value} is not positive-finite; "
            f"using default owner-queue bound {_DEFAULT_OWNER_QUEUE_MS} ms",
            stacklevel=2,
        )
        return _DEFAULT_OWNER_QUEUE_MS
    return value


def from_signals(
    signals: HardwareSignals,
    *,
    target_p99_ms: float,
    utilization_cap: float,
    per_worker_rss: int,
    worker_override: int | None = None,
    guard_capacity: CapacityHint | None = None,
) -> ResourceContract:
    if target_p99_ms <= 0 or utilization_cap <= 0 or per_worker_rss < 1:
        raise CapacityUnavailable("deployment SLO / rss invalid")
    contract = ResourceContract(
        cpu_quota=signals.cpu_quota,
        memory_limit=signals.memory_limit,
        fd_limit=signals.fd_limit,
        guard_capacity=guard_capacity,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
        worker_override=worker_override,
        cpu_source=signals.cpu_source,
        mem_source=signals.mem_source,
        fd_source=signals.fd_source,
    )
    contract.detected_workers()
    if worker_override is not None:
        contract.workers()
    return contract


def load_contract(
    *,
    env: dict[str, str] | None = None,
    guard_capacity: CapacityHint | None = None,
    hooks: DetectHooks | None = None,
) -> tuple[ResourceContract, tuple[str, ...]]:
    src = _env_map(env)
    p99_raw = src.get("AMF_TARGET_P99_MS")
    util_raw = src.get("AMF_UTILIZATION_CAP")
    rss_raw = src.get("AMF_PER_WORKER_RSS_MB")
    p99 = float(p99_raw) if p99_raw else _DEFAULT_TARGET_P99_MS
    util = float(util_raw) if util_raw else _DEFAULT_UTILIZATION_CAP
    rss = int(float(rss_raw) * 1024 * 1024) if rss_raw else _DEFAULT_PER_WORKER_RSS
    override = _parse_override(src.get("WEB_CONCURRENCY") or src.get("AMF_WEB_CONCURRENCY"))
    signals = detect_with_hooks(env=src, hooks=hooks)
    contract = from_signals(
        signals,
        target_p99_ms=p99,
        utilization_cap=util,
        per_worker_rss=rss,
        worker_override=override,
        guard_capacity=guard_capacity,
    )
    logs = _startup_logs(contract, p99_raw, util_raw, rss_raw)
    return contract, logs


def _startup_logs(
    contract: ResourceContract,
    p99_raw: str | None,
    util_raw: str | None,
    rss_raw: str | None,
) -> tuple[str, ...]:
    lines = [
        (
            f"cpu_quota={contract.cpu_quota} source={contract.cpu_source} "
            f"memory_limit={contract.memory_limit} source={contract.mem_source} "
            f"fd_limit={contract.fd_limit} source={contract.fd_source}"
        ),
        (
            f"binding={contract.binding_signal()} workers={contract.workers()} "
            f"detected_workers={contract.detected_workers()}"
        ),
        (
            f"target_p99_ms={contract.target_p99_ms} "
            f"utilization_cap={contract.utilization_cap} "
            f"per_worker_rss={contract.per_worker_rss} "
            f"declared={'env' if (p99_raw or util_raw or rss_raw) else 'defaults'}"
        ),
    ]
    if contract.worker_override is not None:
        lines.append(
            f"WEB_CONCURRENCY override={contract.worker_override} "
            f"detected={contract.detected_workers()} (deviation)",
        )
    return tuple(lines)


def snapshot(contract: ResourceContract, logs: tuple[str, ...]) -> dict[str, object]:
    pools: dict[str, int | None] = {}
    for kind in PoolKind:
        if kind is PoolKind.GUARD:
            try:
                pools[kind.value] = contract.pool_size(kind)
            except CapacityUnset:
                pools[kind.value] = None
            continue
        pools[kind.value] = contract.pool_size(kind)
    rate = contract.offered_service_rate()
    return {
        "cpu_quota": contract.cpu_quota,
        "cpu_source": contract.cpu_source,
        "memory_limit": contract.memory_limit,
        "mem_source": contract.mem_source,
        "fd_limit": contract.fd_limit,
        "fd_source": contract.fd_source,
        "workers": contract.workers(),
        "detected_workers": contract.detected_workers(),
        "worker_override": contract.worker_override,
        "binding": contract.binding_signal(),
        "queue_depth": contract.queue_depth(rate),
        "connection_budget": contract.connection_budget(),
        "stream_buffer_bytes": contract.stream_buffer_bytes(1),
        "inter_chunk_timeout_s": contract.inter_chunk_timeout_s(),
        "idle_timeout_s": contract.idle_timeout_s(),
        "write_timeout_s": contract.write_timeout_s(),
        "max_stream_duration_s": contract.max_stream_duration_s(),
        "max_cut_latency_s": contract.max_cut_latency_s(),
        "max_snapshot_age_s": contract.max_snapshot_age_s(),
        "cancellation_bound_s": contract.cancellation_bound_s(),
        "pools": pools,
        "logs": list(logs),
    }
