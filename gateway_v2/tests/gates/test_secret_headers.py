"""LGW00-2: OpenSSH/PEM headers in extensionless files must be rejected (filename only)."""

from __future__ import annotations

from pathlib import Path

from lint.check_secret_headers import KNOWN_UNTIL_LGW00_1, scan_paths

FAKE_OPENSSH = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "LGW00-2-FIXTURE-NOT-A-REAL-KEY\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)


def test_extensionless_openssh_header_is_a_hit(tmp_path: Path) -> None:
    blob = tmp_path / "extensionless-secret"
    blob.write_text(FAKE_OPENSSH, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]
    # Gate must never echo key material.
    dumped = repr(hits)
    assert "BEGIN OPENSSH" not in dumped
    assert "LGW00-2-FIXTURE" not in dumped


def test_known_path_is_reported_not_failed_when_allowlisted(tmp_path: Path) -> None:
    blob = tmp_path / "ai-mesh-firewall"
    blob.write_text(FAKE_OPENSSH, encoding="utf-8")
    hits = scan_paths([blob], allowlist=KNOWN_UNTIL_LGW00_1)
    assert hits == []


def test_known_allowlist_contains_only_the_tracked_filename() -> None:
    assert KNOWN_UNTIL_LGW00_1 == frozenset({"ai-mesh-firewall"})


def test_header_mention_in_python_source_is_not_a_key_file(tmp_path: Path) -> None:
    src = tmp_path / "test_redaction.py"
    src.write_text(
        '"""fixture mentions a header but is not a key file."""\n'
        "NEEDLE = " + repr("BEGIN OPEN" + "SSH PRIVATE KEY") + "\n",
        encoding="utf-8",
    )
    assert scan_paths([src], allowlist=set()) == []
