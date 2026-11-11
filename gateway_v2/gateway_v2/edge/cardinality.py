"""Bounded Top-K + ``other`` + Overflow_Counter directory (GW14d, task 4.1) — the R2-10 core.

R2-10 is the CRITICAL correction this module exists for. A 64 KiB per-tenant metric name directory
meant metric registration ran ON the serving request path and could RAISE: roughly 40% of a new
tenant's first requests returned HTTP 500 at ~480 UUID-named tenants, and per-org series were
silently truncated at 512 per worker. The correction is a request-path operation that NEVER raises:
a size check BEFORE insert, overflow degraded to a counted + exported process-local counter, and
per-tenant label cardinality bounded by Top-K + a single ``other`` bucket — with the capacity
DERIVED from the :class:`~gateway_v2.runtime.resources.ResourceContract` rather than a literal.

Three invariants, each inherited for a measured reason:

* **``observe()`` NEVER raises (Property 1, R2.1/R2.2).** Its whole body is wrapped in a guard: any
  internal failure — allocation, type, or an unexpected internal error — routes the observation to
  the Overflow_Counter and returns. Turning a successful request into a 500 because a metric slot
  could not be taken is precisely the R2-10 defect, so the request path can never see an exception
  out of this module.

* **Size-check-BEFORE-insert (R2.6, R2.3/R2.4).** When the key is absent AND ``len(_slots)`` already
  equals the derived ``_capacity``, the observation is routed to overflow — the Overflow_Counter is
  incremented by EXACTLY one and NO slot is allocated or modified — before any insert is attempted.
  The allocate-or-overflow decision is reached before the directory is touched. An EXISTING key
  updates its magnitude in place (no new slot, never overflows), so a tenant that already has a slot
  can never be dropped by capacity exhaustion of some other tenant.

* **Bounded, deterministic Top-K (R3, Property 3).** :meth:`top_k_series` selects the ``_k`` highest
  magnitudes under the deterministic tie-break ``(magnitude desc, tenant_key asc)``, so repeated
  exposition of the same input set yields the SAME selection (R3.6). The remaining slots collapse
  into the single ``other`` value (R3.5), and conservation holds: ``Σ(top-K) + other == Σ(all
  observed slots)`` (Property 3). ``other`` is an explicit zero from the first exposition (R3.7),
  and the Overflow_Counter is a real process-local ``_total`` of zero before the first overflow
  (R2.5).
  The exported per-tenant series count is at most ``K + 1`` for every exposition, invariant to the
  observed tenant count (R3.8, R15.3): nothing here grows the exported series set with the estate.

The capacity authority stays in ``runtime/resources.py``: ``_capacity`` and ``_k`` are injected
(``metrics_directory_slots()`` / ``metrics_top_k()``), so this module holds NO capacity-position
literal (R2.7, capacity-literals AST gate). ``observe()`` is O(1); the only work proportional to the
slot count is :meth:`top_k_series` / :meth:`other_value`, bounded by ``_capacity`` (a constant), and
reached only on the scrape path — never proportional to the number of tenants observed
(tenant-scale AST gate). The directory is a mutable slotted recorder, matching the shipped producer
idiom (``AdmissionMetrics`` et al.); its readings are plain values a frozen reading can carry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gateway_v2.runtime.resources import ResourceContract

__all__ = ("CardinalityDirectory",)


@dataclass(slots=True)
class CardinalityDirectory:
    """A bounded in-memory directory of per-tenant magnitude slots. ``observe()`` never raises.

    Capacity (``_capacity``) and the individual-series bound (``_k``) derive from the
    ``ResourceContract`` — use :meth:`from_contract` in production. The direct field constructor is
    kept for testability (a test can build a capacity-1 directory without a contract), but a test or
    caller MUST honour ``_k <= _capacity`` just as the contract guarantees.

    Fields:
      * ``_capacity`` — the maximum number of per-tenant slots (= ``metrics_directory_slots()``).
      * ``_k`` — the bounded count of INDIVIDUAL exported series (= ``metrics_top_k()``, ``<=
        _capacity``).
      * ``_slots`` — ``tenant_key -> magnitude``; ``len(_slots) <= _capacity`` always.
      * ``_overflow_total`` — the Overflow_Counter, a real zero from construction (R2.5).
      * ``_available`` — the initialized/available flag the ``/readyz`` probe reads (R10.1); True by
        construction (a constructed directory IS the "metrics shared memory present" signal).
    """

    _capacity: int
    _k: int
    _slots: dict[str, int] = field(default_factory=dict)
    _overflow_total: int = 0
    _available: bool = True

    @classmethod
    def from_contract(cls, contract: ResourceContract) -> CardinalityDirectory:
        """Build a directory whose capacity + K derive from the contract (the production path).

        ``_capacity = contract.metrics_directory_slots()`` and ``_k = contract.metrics_top_k()``;
        both already fail closed with ``CapacityUnavailable`` below their minimum, and the contract
        guarantees ``metrics_top_k() <= metrics_directory_slots()`` by construction, so no capacity
        literal lives here (R2.7).
        """
        return cls(_capacity=contract.metrics_directory_slots(), _k=contract.metrics_top_k())

    def observe(self, tenant_key: str, magnitude: int) -> None:
        """Register or update a per-tenant magnitude. NEVER raises (R2.1/R2.2 — Property 1).

        The whole body is guarded: ANY internal failure (allocation, type, or an unexpected
        internal error) is caught, routed to the Overflow_Counter, and control returns to the
        serving request without an exception (R2.2). On the normal path:

        * EXISTING key ⇒ update its magnitude in place. No new slot is taken, so an existing tenant
          never overflows (and ``len(_slots)`` is unchanged).
        * ABSENT key AND ``len(_slots) == _capacity`` ⇒ size-check-before-insert fires: increment
          the Overflow_Counter by EXACTLY one, modify NO slot, and return (R2.3/R2.4/R2.6).
        * ABSENT key AND a free slot ⇒ allocate the slot at ``magnitude``.
        """
        try:
            if tenant_key in self._slots:
                # Existing key: update in place, no new slot, cannot overflow.
                self._slots[tenant_key] = magnitude
                return
            # Size-check BEFORE insert: reach the allocate-or-overflow decision before touching the
            # directory, so a full directory can never attempt (and fail) an insert.
            if len(self._slots) >= self._capacity:
                self._overflow_total += 1
                return
            self._slots[tenant_key] = magnitude
        except Exception:  # noqa: BLE001 — the never-raise contract: any internal failure overflows.
            # A failure mid-observation must not reach the serving request. Route to the
            # Overflow_Counter and return; the dropped observation is counted, never silent.
            self._overflow_total += 1

    def magnitude(self, tenant_key: str) -> int:
        """The tenant's current magnitude, 0 when it holds no slot. Never allocates a slot."""
        return self._slots.get(tenant_key, 0)

    def top_k_series(self) -> list[tuple[str, int]]:
        """The ``_k`` highest-magnitude tenants, deterministic tie-break ``(mag desc, key asc)``.

        Returns ``(tenant_key, magnitude)`` pairs for the up-to-``_k`` retained individual series,
        ordered highest magnitude first and, on a tie, by ascending ``tenant_key`` — so repeated
        exposition of the SAME input set yields the SAME selection and the SAME order (R3.6). When
        fewer than ``_k`` tenants are observed, every observed tenant is returned (R3.4). The
        selection is O(slots) ≤ O(_capacity), a bounded constant reached only on the scrape path.
        """
        ordered = sorted(self._slots.items(), key=lambda item: (-item[1], item[0]))
        return ordered[: self._k]

    def other_value(self) -> int:
        """Sum of the magnitudes NOT in the Top-K — the single ``other`` bucket (R3.5).

        Conservation holds by construction: ``Σ(top_k_series) + other_value() == Σ(all slots)``
        (Property 3). With ``<= _k`` tenants observed there is no remainder, so ``other`` is an
        explicit zero (R3.4/R3.7) rather than an absent series.
        """
        retained = {key for key, _ in self.top_k_series()}
        return sum(mag for key, mag in self._slots.items() if key not in retained)

    def overflow_total(self) -> int:
        """The Overflow_Counter — a process-local ``_total``, zero before any overflow (R2.5).

        Included in every scrape so "no overflow" (present, value 0) is distinguishable from
        "series absent". Incremented by exactly one per dropped or internally-failed observation.
        """
        return self._overflow_total

    def available(self) -> bool:
        """Whether the directory is initialized/available — the ``/readyz`` metrics gate (R10.1).

        A constructed directory models the "metrics shared memory present" signal; this returns the
        ``_available`` flag the ``MetricsReadinessProbe`` composes with ``state_ready(view)``.
        """
        return self._available
