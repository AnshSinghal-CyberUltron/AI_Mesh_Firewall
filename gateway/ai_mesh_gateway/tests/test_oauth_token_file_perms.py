"""CHG-0085: _write_mcp_remote_tokens persisted OAuth access/refresh tokens +
client_secret + PKCE code_verifier to /tmp/mcp-orgs/{org}/mcp-auth/... using
Path.write_text / mkdir defaults (0644 world-readable files, 0755 world-traversable
dirs) — so a co-located process / tenant on the shared host could read another org's
OAuth credentials at rest. The org credential tree is now owner-only (0700 dirs / 0600
files), and files are created with the restrictive mode at open() time (no
world-readable window).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_oauth_proxy as oauth  # noqa: E402


@pytest.fixture()
def _org():
    org = f"permtest-{uuid.uuid4().hex[:12]}"
    yield org
    shutil.rmtree(Path(f"/tmp/mcp-orgs/{org}"), ignore_errors=True)


def test_oauth_token_files_are_owner_only(_org):
    server_url = "https://srv.example/mcp"
    oauth._write_mcp_remote_tokens(
        _org, server_url,
        {"access_token": "AT-secret", "refresh_token": "RT-secret", "token_type": "bearer"},
        {"client_id": "cid", "client_secret": "CS-secret",
         "callback_url": "https://cb", "code_verifier": "CV-secret"},
    )
    base = Path(f"/tmp/mcp-orgs/{_org}")
    h = hashlib.md5(server_url.encode()).hexdigest()

    # NO path anywhere under the org tree may be group/world accessible (mode & 0o077)
    offenders = []
    for root, _dirs, files in os.walk(base):
        if os.stat(root).st_mode & 0o077:
            offenders.append(("dir", root, oct(os.stat(root).st_mode & 0o777)))
        for f in files:
            p = os.path.join(root, f)
            if os.stat(p).st_mode & 0o077:
                offenders.append(("file", p, oct(os.stat(p).st_mode & 0o777)))
    assert not offenders, f"group/world-accessible OAuth cred paths: {offenders}"

    # the org dir specifically is 0700 (blocks traversal by other users)
    assert (os.stat(base).st_mode & 0o777) == 0o700

    # content is intact (the secrets are actually written)
    vdir = base / "mcp-auth" / "mcp-remote-0.1.43"
    tokens = (vdir / f"{h}_tokens.json").read_text()
    assert "AT-secret" in tokens and "RT-secret" in tokens
    assert (os.stat(vdir / f"{h}_tokens.json").st_mode & 0o777) == 0o600
    assert "CV-secret" in (vdir / f"{h}_code_verifier.txt").read_text()
    assert "CS-secret" in (vdir / f"{h}_client_info.json").read_text()


def test_secure_write_overrides_preexisting_loose_perms(_org):
    # a file left behind world-readable by an older build must be tightened on re-write
    base = Path(f"/tmp/mcp-orgs/{_org}") / "mcp-auth" / "mcp-remote-0.1.43"
    base.mkdir(parents=True, exist_ok=True)
    victim = base / "leftover.txt"
    victim.write_text("old")
    os.chmod(victim, 0o644)
    oauth._write_secure_text(victim, "new-secret")
    assert (os.stat(victim).st_mode & 0o777) == 0o600
    assert victim.read_text() == "new-secret"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
