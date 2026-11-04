"""GW27: the canonical JSON shape of ``OrgControls`` (encode side, no validation).

Shared by ``plan.document`` (the signed body) and ``plan.compiler`` (the content hash), so the two
cannot disagree on bytes. Members that are unset are omitted; ``version`` is always written.
"""
from __future__ import annotations

import json

from gateway_v2.domain.plan import UNSET_CONTROLS, OrgControls

CONTROLS_VERSION = 1
REQUIRES_CONTROLS = "controls/1"


def controls_body(controls: OrgControls) -> dict[str, object]:
    body: dict[str, object] = {"version": CONTROLS_VERSION}
    if controls.rate is not None:
        body["rate"] = {
            "enabled": controls.rate.enabled,
            "requests_per_minute": controls.rate.requests_per_minute,
            "burst": controls.rate.burst,
        }
    if controls.org_tpm_limit:
        body["org_tpm_limit"] = controls.org_tpm_limit
    if controls.max_response_tokens:
        body["max_response_tokens"] = controls.max_response_tokens
    if controls.audit is not None:
        body["audit"] = {
            "enabled": controls.audit.enabled,
            "incident_logging": controls.audit.incident_logging,
            "retention_days": controls.audit.retention_days,
        }
    if controls.compliance_frameworks:
        body["compliance"] = {"frameworks": list(controls.compliance_frameworks)}
    return body


def controls_token(controls: OrgControls) -> str:
    """The hash line for non-UNSET controls; empty for UNSET (plans hash as before)."""
    if controls == UNSET_CONTROLS:
        return ""
    return "controls|" + json.dumps(controls_body(controls), sort_keys=True, separators=(",", ":"))
