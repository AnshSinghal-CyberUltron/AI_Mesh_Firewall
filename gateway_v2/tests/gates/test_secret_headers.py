"""LGW00-2: OpenSSH/PEM headers in extensionless files must be rejected (filename only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lint.check_secret_headers import (
    FORBIDDEN_KEY_FILENAMES,
    KNOWN_UNTIL_LGW00_1,
    scan_paths,
    scan_repo,
)

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


# ---------------------------------------------------------------------------
# Task 4 — enumerated header-detection + path-category matrix (design Testing
# Strategy). Fixtures use FAKE headers assembled from fragments so this test
# file carries no literal ``BEGIN <...> PRIVATE KEY`` needle (needle hygiene),
# and hit fixtures are written to tmp_path names that do NOT match the
# Content_Allowlist. Assertions are path-only (Property 5 anchor).
# ---------------------------------------------------------------------------

# Bare PKCS#8 header (empty label branch of _PEM). Assembled from fragments so the
# literal ``BEGIN PRIVATE KEY`` never appears verbatim in this source.
_FAKE_BODY = "GW00-FIXTURE-NOT-A-REAL-KEY\n"
FAKE_PKCS8 = (
    "-----" + "BEGIN " + "PRIVATE KEY" + "-----\n" + _FAKE_BODY + "-----END " + "PRIVATE KEY-----\n"
)


def _labelled_pem(label: str) -> str:
    """A PEM header with a non-empty label, assembled from fragments."""
    return (
        "-----" + "BEGIN " + label + " " + "PRIVATE KEY" + "-----\n"
        + _FAKE_BODY
        + "-----END " + label + " " + "PRIVATE KEY-----\n"
    )


FAKE_RSA = _labelled_pem("RSA")
FAKE_EC = _labelled_pem("EC")
FAKE_ENC = _labelled_pem("ENCRYPTED")
# A non-private-key PEM block: must NOT widen the regex (D1 / regex width).
FAKE_CERT = (
    "-----" + "BEGIN " + "CERTIFICATE" + "-----\n" + _FAKE_BODY + "-----END " + "CERTIFICATE-----\n"
)
FAKE_PUBKEY = (
    "-----" + "BEGIN " + "PUBLIC KEY" + "-----\n" + _FAKE_BODY + "-----END " + "PUBLIC KEY-----\n"
)


# --- Property 1: every header form is a hit regardless of extension/dotfile ----


def test_pkcs8_header_in_extensionless_file_is_a_hit(tmp_path: Path) -> None:
    """T-PKCS8-EXTLESS — bare PKCS#8 in an extensionless file. Property 1.
    # Validates: Requirements 1.1"""
    blob = tmp_path / "secret-blob"
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_pkcs8_header_in_pem_file_is_a_hit(tmp_path: Path) -> None:
    """T-PKCS8-PEM — bare PKCS#8 in a .pem file. Property 1.
    # Validates: Requirements 1.1, 2.2"""
    blob = tmp_path / "leaked.pem"
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_pkcs8_header_in_suffixed_file_is_a_hit(tmp_path: Path) -> None:
    """T-PKCS8-SUFFIX — bare PKCS#8 in leaked.txt (non-key suffix). Property 1.
    # Validates: Requirements 1.1, 2.1"""
    blob = tmp_path / "leaked.txt"
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_pkcs8_header_in_dotfile_is_a_hit(tmp_path: Path) -> None:
    """T-PKCS8-DOTFILE — bare PKCS#8 in a dotfile. Property 1.
    # Validates: Requirements 1.1, 3.1"""
    blob = tmp_path / ".id_pkcs8"
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_key_header_in_bak_suffixed_file_is_a_hit(tmp_path: Path) -> None:
    """T-KEY-TXT — OpenSSH header in id_rsa.bak (suffixed). Property 1.
    # Validates: Requirements 2.1"""
    blob = tmp_path / "id_rsa.bak"
    blob.write_text(FAKE_OPENSSH, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_openssh_header_in_dotfile_is_a_hit(tmp_path: Path) -> None:
    """T-KEY-DOTFILE — OpenSSH header in .ssh_key dotfile. Property 1.
    # Validates: Requirements 3.1"""
    blob = tmp_path / ".ssh_key"
    blob.write_text(FAKE_OPENSSH, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_rsa_label_header_is_a_hit(tmp_path: Path) -> None:
    """T-RSA — BEGIN RSA PRIVATE KEY label header. Property 1.
    # Validates: Requirements 1.3"""
    blob = tmp_path / "rsa-blob"
    blob.write_text(FAKE_RSA, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_ec_label_header_is_a_hit(tmp_path: Path) -> None:
    """T-EC — BEGIN EC PRIVATE KEY label header. Property 1.
    # Validates: Requirements 1.3"""
    blob = tmp_path / "ec-blob"
    blob.write_text(FAKE_EC, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_encrypted_label_header_is_a_hit(tmp_path: Path) -> None:
    """T-ENC — BEGIN ENCRYPTED PRIVATE KEY label header. Property 1.
    # Validates: Requirements 1.3"""
    blob = tmp_path / "enc-blob"
    blob.write_text(FAKE_ENC, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


# --- Property 2: a file with no header is never a hit --------------------------


def test_no_header_suffixed_file_is_not_a_hit(tmp_path: Path) -> None:
    """T-NO-HEADER-SUFFIX — backup.old with no header. Property 2.
    # Validates: Requirements 2.3"""
    blob = tmp_path / "backup.old"
    blob.write_text("just an ordinary backup, no secrets here\n", encoding="utf-8")
    assert scan_paths([blob], allowlist=set()) == []


def test_no_header_dotfile_is_not_a_hit(tmp_path: Path) -> None:
    """T-NO-HEADER-DOTFILE — .config with no header. Property 2.
    # Validates: Requirements 3.2"""
    blob = tmp_path / ".config"
    blob.write_text("setting = value\n", encoding="utf-8")
    assert scan_paths([blob], allowlist=set()) == []


# --- Property 3: allowlisted paths are never hits even with a real header ------


def test_header_in_python_source_is_allowlisted(tmp_path: Path) -> None:
    """T-ALLOW-PY — header-as-data in a .py module. Property 3.
    # Validates: Requirements 4.4"""
    blob = tmp_path / "mentions.py"
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    assert scan_paths([blob], allowlist=set()) == []


def test_header_in_markdown_doc_is_allowlisted(tmp_path: Path) -> None:
    """T-ALLOW-MD — header-as-data in a .md doc. Property 3.
    # Validates: Requirements 4.5"""
    blob = tmp_path / "notes.md"
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    assert scan_paths([blob], allowlist=set()) == []


def test_header_in_kiro_spec_is_allowlisted(tmp_path: Path) -> None:
    """T-ALLOW-SPEC — header-as-data under .kiro/specs/**. Property 3.

    The ``^\\.kiro/specs/`` pattern is dir-prefix anchored, so it matches only the
    repo-relative POSIX form; the fixture is placed under a constructed repo root
    and ``scan_paths`` is given ``repo=`` so ``_match_text`` yields that form.
    # Validates: Requirements 4.5
    """
    repo = tmp_path / "repo"
    blob = repo / ".kiro" / "specs" / "some-spec" / "requirements.md.txt"
    blob.parent.mkdir(parents=True)
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    assert scan_paths([blob], allowlist=set(), repo=repo) == []


def test_header_in_test_fixture_path_is_allowlisted(tmp_path: Path) -> None:
    """T-ALLOW-FIXTURE — header in a tests/.../fixtures*.py-pattern path. Property 3.

    Matched both by the ``(^|/)tests?/`` segment pattern and ``fixtures.*\\.py$``;
    ``repo=`` yields the repo-relative POSIX form exercising the category.
    # Validates: Requirements 4.6
    """
    repo = tmp_path / "repo"
    blob = repo / "gateway_v2" / "tests" / "detect" / "fixtures_fake_keys.py"
    blob.parent.mkdir(parents=True)
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    assert scan_paths([blob], allowlist=set(), repo=repo) == []


# --- regex width (D1): non-private-key PEM blocks must not match ---------------


def test_certificate_and_public_key_are_not_hits(tmp_path: Path) -> None:
    """T-NON-KEY-PEM — BEGIN CERTIFICATE / BEGIN PUBLIC KEY in a key-shaped file.
    The optional-label regex must not widen to non-private-key PEM blocks. D1.
    # Validates: Requirements 1 (regex width)"""
    cert = tmp_path / "cert-blob"
    cert.write_text(FAKE_CERT, encoding="utf-8")
    pub = tmp_path / "pub-blob"
    pub.write_text(FAKE_PUBKEY, encoding="utf-8")
    assert scan_paths([cert], allowlist=set()) == []
    assert scan_paths([pub], allowlist=set()) == []


# --- Property 7: LGW00-1 / LGW00-2 preserved -----------------------------------


def test_forbidden_filename_with_pkcs8_header_is_a_hit(tmp_path: Path) -> None:
    """T-FORBIDDEN — ai-mesh-firewall (forbidden) with a header is a hit. Property 7.
    # Validates: Requirements 6.1"""
    blob = tmp_path / "ai-mesh-firewall"
    blob.write_text(FAKE_PKCS8, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


def test_lgw00_2_extensionless_openssh_header_is_a_hit(tmp_path: Path) -> None:
    """T-LGW00-2 — extensionless OpenSSH header is a hit (LGW00-2 preserved).
    Property 7.
    # Validates: Requirements 6.2"""
    blob = tmp_path / "extensionless-openssh"
    blob.write_text(FAKE_OPENSSH, encoding="utf-8")
    hits = scan_paths([blob], allowlist=set())
    assert [h.path for h in hits] == [str(blob)]


# ---------------------------------------------------------------------------
# Task 4.2 — T-ZERO (real tracked tree) and T-NO-MATERIAL (path-only).
# ---------------------------------------------------------------------------


def _repo_root() -> Path | None:
    """Walk up from this file to the directory that contains a ``.git`` entry."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def test_current_tracked_tree_yields_zero_hits() -> None:
    """T-ZERO — scan_repo over the real git ls-files set yields zero hits and no
    LGW00-1 residual. Offline/deterministic; runs only when .git is present
    (the CI --fail-allowlist job is the authoritative fallback). Property 4.
    # Validates: Requirements 4.2, 8.1"""
    repo_root = _repo_root()
    if repo_root is None:
        pytest.skip("no .git present; CI --fail-allowlist is the authoritative gate")
    hits, known = scan_repo(repo_root, allow_known=False)
    assert [h.path for h in hits] == []
    assert known == []


def test_no_finding_carries_key_material(tmp_path: Path) -> None:
    """T-NO-MATERIAL — repr(hits) for every produced hit contains the path only and
    no byte of the matched header or fake body. Property 5.
    # Validates: Requirements 5.1, 5.3"""
    pkcs8 = tmp_path / "pkcs8-blob"
    pkcs8.write_text(FAKE_PKCS8, encoding="utf-8")
    openssh = tmp_path / "openssh-blob"
    openssh.write_text(FAKE_OPENSSH, encoding="utf-8")
    rsa = tmp_path / "rsa-blob"
    rsa.write_text(FAKE_RSA, encoding="utf-8")

    hits = scan_paths([pkcs8, openssh, rsa], allowlist=set())
    assert len(hits) == 3
    dumped = repr(hits)
    assert "PRIVATE KEY" not in dumped
    assert "BEGIN OPENSSH" not in dumped
    assert "BEGIN OPEN" not in dumped
    assert _FAKE_BODY.strip() not in dumped
    # The path of every hit is still present (path-only reporting, not silence).
    for blob in (pkcs8, openssh, rsa):
        assert str(blob) in dumped
