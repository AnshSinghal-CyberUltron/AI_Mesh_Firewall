"""LGW00-2: OpenSSH/PEM headers in extensionless files must be rejected (filename only)."""

from __future__ import annotations

from pathlib import Path

from lint.check_secret_headers import FORBIDDEN_KEY_FILENAMES, KNOWN_UNTIL_LGW00_1, scan_paths

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


def test_forbidden_filename_is_always_a_hit(tmp_path: Path) -> None:
    blob = tmp_path / "ai-mesh-firewall"
    blob.write_text(FAKE_OPENSSH, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_lgw00_1_allowlist_is_empty() -> None:
    assert KNOWN_UNTIL_LGW00_1 == frozenset()
    assert "ai-mesh-firewall" in FORBIDDEN_KEY_FILENAMES


def test_header_mention_in_python_source_is_not_a_key_file(tmp_path: Path) -> None:
    src = tmp_path / "test_redaction.py"
    src.write_text(
        '"""fixture mentions a header but is not a key file."""\n'
        "NEEDLE = " + repr("BEGIN OPEN" + "SSH PRIVATE KEY") + "\n",
        encoding="utf-8",
    )
    assert scan_paths([src], allowlist=set()) == []
