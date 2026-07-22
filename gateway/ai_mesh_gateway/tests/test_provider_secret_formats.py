"""CHG-0071: real-world provider credential formats that egressed UNMASKED and were
NOT flagged by detect_secrets (found via an adversarial redact_all secret-format sweep).

Anthropic API keys (``sk-ant-…``) were missed while the OpenAI ``sk-`` family was
caught; SendGrid / GitLab PAT / Slack incoming-webhook had no SECRET_PATTERNS entry at
all — so such a key in an MCP tool RESULT (or chat text) would (a) not trigger the
tier-1 redact/block decision (detect_secrets drives it) and (b) egress verbatim. Now
all four detect + tag SECRET + mask.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from patterns import detect_secrets, get_compliance_tags, redact_all  # noqa: E402

# FAKE / example credential values (structurally valid, not real).
_CASES = {
    "anthropic_key": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789-ABCDEFGH",
    "sendgrid_key": "SG.abcdefghijklmnopqrstuv.abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHI",
    "gitlab_pat": "glpat-ABCDEF1234567890abcd",
    "slack_webhook": "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
}


@pytest.mark.parametrize(("key", "value"), list(_CASES.items()))
def test_new_secret_format_detected_tagged_and_masked(key, value):
    # (a) detect_secrets flags it under the right key (drives the tier-1 enforcement)
    found = detect_secrets(value)
    assert key in found, f"{key} not detected: {found}"
    # (b) tagged SECRET (compliance)
    assert "SECRET" in get_compliance_tags(list(found.keys()))
    # (c) redact_all masks it — the raw secret does not survive
    red = redact_all(value)
    assert value not in red


def test_secret_in_larger_text_is_masked():
    for value in _CASES.values():
        red = redact_all(f"here is the key: {value} — keep it safe")
        assert value not in red


@pytest.mark.parametrize(
    "benign",
    [
        "SG.short.short",                         # segments too short for a SendGrid key
        "the glpatterns and sglist are here",     # glpat/SG substrings, not the format
        "visit https://hooks.slack.com/help now", # slack.com but not a /services/ webhook
        "sk-ant is a prefix with no key body",    # no key material
        "just normal text with no secrets at all",
    ],
)
def test_no_false_positive_on_benign_text(benign):
    assert redact_all(benign) == benign
    assert not detect_secrets(benign)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
