"""CHG-0055 regression: api_key / apikey / access_key assignments must be redacted.

The secret inventory covered ``password=`` / ``secret=`` / ``token=`` assignments but
NOT ``api_key=`` / ``apikey=`` / ``access_key=`` — so an ``API_KEY=<token>`` whose value
did not match a provider-specific format (e.g. below the OpenAI 32-char threshold)
egressed UNMASKED. The new ``api_key_assignment`` pattern closes that, reusing the same
``_TOKEN_VALUE`` false-positive guard as ``token_assignment``.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from patterns import detect_secrets, redact_all  # noqa: E402


@pytest.mark.parametrize(
    "text",
    [
        "api_key=abcd1234efgh5678ijkl",
        "API_KEY=abcd1234efgh5678ijkl",
        "apikey: mytok3n-value-here",
        "access_key=AKIAsomethingCustom99",
        "api-key = zZ9longvalue123456",
        'api_key: "sk-xyz1234567890abc"',
    ],
)
def test_api_key_assignment_value_masked(text):
    red = redact_all(text)
    assert red != text
    # the value is gone; the key name + separator are preserved (===***)
    assert "***" in red
    assert "api_key_assignment" in detect_secrets(text)


@pytest.mark.parametrize(
    "text",
    [
        "api_key=none",                     # prose value (FP guard)
        "api key: forgotten?",              # prose, no direct assignment
        "the access key is stored securely",  # prose, no assignment operator
        "api_key=",                         # empty value
        "DEBUG=true",                       # not an api_key assignment
        "api_key: your",                    # prose word
    ],
)
def test_api_key_assignment_false_positives_not_masked(text):
    assert redact_all(text) == text


def test_api_key_in_env_dump_masked_alongside_conn_string():
    env = (
        "DATABASE_URL=postgres://u:p4ss@h/db\n"
        "API_KEY=sk-abcdef0123456789ABCDEFxyz\n"
        "DEBUG=true"
    )
    red = redact_all(env)
    assert "sk-abcdef0123456789ABCDEFxyz" not in red   # api key value gone
    assert "p4ss" not in red                            # db password gone (conn string)
    assert "DEBUG=true" in red                          # benign line preserved


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
