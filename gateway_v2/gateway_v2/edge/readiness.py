"""``/readyz`` readiness that EXTENDS ``state_ready(view)`` with the metrics directory (GW14d, 9.1).

R2-11 closed one last blind spot: a worker could pass ``/readyz`` while serving blind to its own
metrics. GW14d's rule (Requirement 10) is that readiness composes the EXISTING
``runtime.state_stamp.state_ready(view)`` state-dependency gate with the Cardinality_Directory
availability gate — it extends ``state_ready`` rather than replacing it, so there is still exactly
ONE definition of "state ready" (``state_stamp`` owns it) and the metrics directory is layered on
top.

:func:`make_metrics_readiness` returns a :data:`~gateway_v2.edge.app.ReadinessProbe` closure — the
``Callable[[], tuple[bool, str | None]]`` shape ``edge/app.py`` already accepts and ``_readyz``
already renders (``(True, None)`` ⇒ ``200``; ``(False, reason)`` ⇒ ``503`` with ``reason`` as the
body naming the unavailable dependency). The closure's contract, which Property 11 pins:

* **State is gated FIRST (R10.4).** The probe calls ``state_ready(view)`` and, when it reports not
  ready, returns ``(False, state_reason)`` immediately — the state reason, regardless of the
  directory's availability. So when BOTH gates are unavailable, exactly ONE reason is returned, and
  it names the state dependency (R10.4): the directory gate is not even consulted.
* **Then the directory (R10.1/R10.3).** Only once state is ready does the probe check
  ``directory.available()``; when it is False the probe returns ``(False, "metrics_directory")``,
  the single reserved reason string that names the metrics directory as the unavailable dependency.
* **Ready ⇔ BOTH ready (R10.2/R10.5).** ``(True, None)`` is returned only when ``state_ready(view)``
  is ready AND ``directory.available()`` is True. Equivalently ``ready ⇔ (state_ready.ready ∧
  directory.available)``.
* **Both gates evaluated every call (R10.5).** The closure holds no cached verdict; each invocation
  re-reads ``state_ready(view)`` and (when state is ready) ``directory.available()``, so a
  dependency that recovers or fails between scrapes is reflected on the next ``/readyz``.

``view`` is a :class:`~gateway_v2.runtime.state_stamp.StampView` or ``None`` (``state_ready(None)``
reports ready — an unwired deployment, parity with ``edge/app.py``'s default probe). ``directory``
is typed to the minimal :class:`DirectoryGate` protocol — anything with ``available() -> bool`` —
so :class:`~gateway_v2.edge.cardinality.CardinalityDirectory` satisfies it structurally and a test
can inject a trivial stub. ``edge`` may import ``runtime`` (``state_stamp``) and its sibling
``edge.cardinality``, so this composition sits inside the layers contract.

The reason string is a fixed status identifier, not a capacity-position literal; the module holds
no module-level mutable and no dataclass. The closure is tiny and pure beyond the two gate reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from gateway_v2.runtime.state_stamp import StampView, state_ready

if TYPE_CHECKING:
    from gateway_v2.edge.app import ReadinessProbe

__all__ = ("DirectoryGate", "METRICS_DIRECTORY_REASON", "make_metrics_readiness")

#: The single reserved ``/readyz`` reason that NAMES the metrics directory as the unavailable
#: dependency (R10.1/R10.3). ``edge/app.py``'s ``_readyz`` renders it as the 503 body verbatim.
METRICS_DIRECTORY_REASON = "metrics_directory"


class DirectoryGate(Protocol):
    """The minimal availability gate the probe composes — anything with ``available() -> bool``.

    :class:`~gateway_v2.edge.cardinality.CardinalityDirectory` satisfies this structurally (its
    ``available()`` returns the initialized/available flag). Typing to this protocol rather than to
    the concrete directory keeps the probe testable with a trivial stub and keeps the directory
    gate's surface honest: readiness depends on availability alone, nothing more.
    """

    def available(self) -> bool:
        """True when the Cardinality_Directory is initialized and available (R10.1)."""
        ...


def make_metrics_readiness(
    view: StampView | None, directory: DirectoryGate
) -> ReadinessProbe:
    """Build the ``/readyz`` probe that gates ``state_ready(view)`` FIRST, then the directory.

    Returns a :data:`~gateway_v2.edge.app.ReadinessProbe` closure (``Callable[[], tuple[bool, str |
    None]]``). On each call the closure:

    1. evaluates ``state_ready(view)`` and, when not ready, returns ``(False, state_reason)`` — the
       state reason, regardless of the directory (R10.4: state gated first, one reason);
    2. otherwise evaluates ``directory.available()`` and, when False, returns ``(False,
       METRICS_DIRECTORY_REASON)`` (R10.1/R10.3);
    3. otherwise returns ``(True, None)`` — ready, because BOTH gates are ready (R10.2).

    Both gates are evaluated every call (R10.5): the closure caches no verdict.
    """

    def probe() -> tuple[bool, str | None]:
        ready, reason = state_ready(view)
        if not ready:
            # State gated FIRST: its reason wins even if the directory is also unavailable, so
            # exactly one reason is returned and it names the state dependency (R10.4).
            return False, reason
        if not directory.available():
            return False, METRICS_DIRECTORY_REASON
        return True, None

    return probe
