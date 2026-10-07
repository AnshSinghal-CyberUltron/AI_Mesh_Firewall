"""Admission: identity, quota, killswitch, grant."""
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

__all__ = (
    "CacheStats",
    "IdentityCache",
    "KillSwitchSnapshot",
    "KillSwitchState",
    "KillSwitchView",
    "engaged_scopes",
    "identity_applier",
    "killswitch_adopter",
    "killswitch_applier",
    "principal_of",
    "scope_is_on",
)
