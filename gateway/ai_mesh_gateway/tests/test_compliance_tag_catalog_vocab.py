"""CHG-0059: the shared compliance-tag module must (a) cover internal-infra leak
keys and (b) normalize gateway-granular tags onto the ComplianceTag catalog vocab.

WHY: ``MCPEvent.compliance_tags`` is documented as a list of ``ComplianceTag.code``
values (GDPR-PII / HIPAA-PHI / PCI-CARD / SOC2-CONF / ...). The control-plane
enforcement path already emits catalog codes (via ``tags_for_preset_or_entity``),
but the gateway scan path emits a GRANULAR vocabulary (GDPR / PII / HIPAA / PHI /
PCI-DSS / SECRET / INFRA / SOC2), so the same audit field held two vocabularies
depending on which plane recorded the event. ``to_catalog_codes`` reconciles them;
the extended ``PRESET_TO_TAGS`` gives internal-infra keys a catalog tag (item 5's
"extend mcp_compliance_tags.py to ... IP ...").
"""
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[2].parent / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import pytest  # noqa: E402

from ai_mesh_shared.mcp_compliance_tags import (  # noqa: E402
    CATALOG_TAG_CODES,
    PRESET_TO_TAGS,
    tags_for_preset_or_entity,
    to_catalog_codes,
)


# ---- (a) internal-infra coverage (item 5: "extend to ... IP ...") ------------
@pytest.mark.parametrize(
    "key",
    ["internal_ipv4", "internal_hostname", "internal_url",
     "file_path_unix", "file_path_windows", "ip_leakage"],
)
def test_internal_infra_keys_map_to_soc2_conf(key):
    assert tags_for_preset_or_entity(key) == ["SOC2-CONF"]


def test_public_ip_address_stays_gdpr_pii_not_infra():
    # generic/public dotted-quad is a personal-data identifier, NOT internal infra
    assert tags_for_preset_or_entity("ip_address") == ["GDPR-PII"]


def test_every_preset_tag_is_a_catalog_code():
    for key, tags in PRESET_TO_TAGS.items():
        for t in tags:
            assert t in CATALOG_TAG_CODES, f"{key} -> {t} is not a catalog code"


# ---- (b) gateway-granular -> catalog normalization ---------------------------
@pytest.mark.parametrize(
    ("granular", "expected"),
    [
        (["GDPR", "PII"], ["GDPR-PII"]),                       # collapse to one code
        (["HIPAA", "PHI"], ["HIPAA-PHI"]),
        (["PCI-DSS"], ["PCI-CARD"]),
        (["SECRET"], ["SOC2-CONF"]),
        (["INFRA"], ["SOC2-CONF"]),                            # internal-infra -> SOC2-CONF
        (["SOC2"], ["SOC2-CONF"]),
        (["GDPR", "HIPAA", "PII"], ["GDPR-PII", "HIPAA-PHI"]),  # the live CHG-0017 case
        (["PII", "PCI-DSS", "INFRA"], ["GDPR-PII", "PCI-CARD", "SOC2-CONF"]),
    ],
)
def test_gateway_granular_normalizes_to_catalog(granular, expected):
    assert to_catalog_codes(granular) == expected


def test_case_insensitive_granular_tokens():
    assert to_catalog_codes(["gdpr", "pii", "Infra"]) == ["GDPR-PII", "SOC2-CONF"]


@pytest.mark.parametrize(
    "catalog", [["GDPR-PII"], ["HIPAA-PHI"], ["PCI-CARD"], ["SOC2-CONF"],
                ["GDPR-PII", "HIPAA-PHI"], ["ITAR"], ["FERPA"]],
)
def test_already_catalog_codes_are_idempotent(catalog):
    assert to_catalog_codes(catalog) == sorted(set(catalog))
    # normalizing twice is stable
    assert to_catalog_codes(to_catalog_codes(catalog)) == to_catalog_codes(catalog)


def test_unknown_tags_pass_through_never_dropped():
    # an unrecognized signal must be preserved (renamed onto canon, never discarded)
    assert to_catalog_codes(["OWASP-MCP", "PII"]) == ["GDPR-PII", "OWASP-MCP"]


@pytest.mark.parametrize("bad", [None, [], ["", "   "], [123, None], ["  "]])
def test_empty_or_nonstring_inputs_are_safe(bad):
    out = to_catalog_codes(bad)
    assert isinstance(out, list)
    assert all(isinstance(x, str) and x for x in out)


def test_normalization_dedupes_and_sorts():
    assert to_catalog_codes(["PII", "GDPR", "GDPR-PII"]) == ["GDPR-PII"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
