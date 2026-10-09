"""Admission: identity, quota, killswitch, grant, and the admission-control surface.

Re-exports the admission-control public API (R2-07 / R2-08, card GW19) alongside the existing
identity/killswitch surface so ``edge`` and other callers consume one stable ``gateway_v2.admit``
import path. Only names each sibling module lists in its own ``__all__`` are re-exported here —
nothing private — and ``admit`` never imports a layer above itself (the import-linter ``layers``
contract keeps ``admit`` below ``edge``/``resolve`` and above ``runtime``/``domain``).
"""
from gateway_v2.admit.admission import (
    AdmissionController,
    DeclaredTermination,
    DrainReport,
    DrainState,
    InFlightStream,
)
from gateway_v2.admit.codel import (
    CoDelController,
    CoDelDecision,
    CoDelParams,
    CoDelReason,
)
from gateway_v2.admit.grant import (
    Admitted,
    ResourceGrant,
    ShedVerdict,
    shed_retry_after_s,
    shed_verdict,
)
from gateway_v2.admit.identity import (
    CacheStats,
    IdentityCache,
    identity_applier,
    principal_of,
)
from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    KillSwitchState,
    KillSwitchView,
    engaged_scopes,
    killswitch_adopter,
    killswitch_applier,
    scope_is_on,
)
from gateway_v2.admit.metrics import (
    AdmissionMetrics,
    AdmissionReading,
    QueueReport,
    ShedReason,
)
from gateway_v2.admit.quota import (
    AdmissionBounds,
    derive_bounds,
)
from gateway_v2.admit.supervisor import (
    ExitOutcome,
    Worker,
    WorkerSupervisor,
)

__all__ = (
    "AdmissionBounds",
    "AdmissionController",
    "AdmissionMetrics",
    "AdmissionReading",
    "Admitted",
    "CacheStats",
    "CoDelController",
    "CoDelDecision",
    "CoDelParams",
    "CoDelReason",
    "DeclaredTermination",
    "DrainReport",
    "DrainState",
    "ExitOutcome",
    "IdentityCache",
    "InFlightStream",
    "KillSwitchSnapshot",
    "KillSwitchState",
    "KillSwitchView",
    "QueueReport",
    "ResourceGrant",
    "ShedReason",
    "ShedVerdict",
    "Worker",
    "WorkerSupervisor",
    "derive_bounds",
    "engaged_scopes",
    "identity_applier",
    "killswitch_adopter",
    "killswitch_applier",
    "principal_of",
    "scope_is_on",
    "shed_retry_after_s",
    "shed_verdict",
)
