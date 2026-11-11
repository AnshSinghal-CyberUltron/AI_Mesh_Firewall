"""Control plane for durable shared state. Postgres is the truth; the store holds a copy.

Separate from the `gateway_v2` package on purpose (§2.1 control/data separation): nothing in the
data plane imports this, and this imports only `gateway_v2.domain.state` and
`gateway_v2.runtime.state_sig` so that exactly ONE signing implementation exists across both
planes. A signature cannot drift between writer and reader if there is only one of it.
"""
from state_control.db import (
    ZERO_COUNTERS,
    CommitUnknown,
    ControlDB,
    ControlTx,
    KindCounters,
    LoggedWrite,
    MemoryControlDB,
    advance,
)
from state_control.pg import (
    BOUND_NAMES,
    ControlPlaneBoundsNotApplied,
    PostgresControlDB,
    PostgresTx,
)
from state_control.publisher import (
    BrokenPublisher,
    MemoryStore,
    StatePublisher,
    StoredHead,
)
from state_control.rehydrate import (
    ALL_KINDS,
    ControlPlaneBoundExceeded,
    ControlPlaneUnavailable,
    Rehydrator,
    RepairEvent,
    RoundSummary,
)
from state_control.schema import RECORD_COLUMNS, SCHEMA
from state_control.valkey import ValkeyPublisher
from state_control.writer import (
    ERROR,
    OK,
    OK_PUBLISH_PENDING,
    UNKNOWN,
    StateWriter,
    WriteOutcome,
)

__all__ = (
    "ALL_KINDS",
    "BOUND_NAMES",
    "ERROR",
    "OK",
    "OK_PUBLISH_PENDING",
    "RECORD_COLUMNS",
    "SCHEMA",
    "UNKNOWN",
    "ZERO_COUNTERS",
    "BrokenPublisher",
    "CommitUnknown",
    "ControlDB",
    "ControlPlaneBoundExceeded",
    "ControlPlaneBoundsNotApplied",
    "ControlPlaneUnavailable",
    "ControlTx",
    "KindCounters",
    "LoggedWrite",
    "MemoryControlDB",
    "MemoryStore",
    "PostgresControlDB",
    "PostgresTx",
    "Rehydrator",
    "RepairEvent",
    "RoundSummary",
    "StatePublisher",
    "StateWriter",
    "StoredHead",
    "ValkeyPublisher",
    "WriteOutcome",
    "advance",
)
