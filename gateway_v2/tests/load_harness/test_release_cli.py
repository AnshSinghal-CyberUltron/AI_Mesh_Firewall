"""CLI exit-code unit tests for ``python -m load_harness.release`` (B7 / R2-20, partial), task 4.2.

# Validates: Requirements 2.1, 2.5, 4.5

Representative EXAMPLES (not a property test): these drive :func:`load_harness.release.main`
directly with an ``argv`` list and assert the integer exit code of each ``freeze`` / ``verify``
path. The exit codes ARE the contract — ``_EXIT_OK = 0`` on success/intact, ``_EXIT_FAIL = 1`` on a
fail-closed refuse (a verified Mutated_Copy, Req 4.5; a differing re-freeze under an already-taken
version, Req 2.5) — so a run cannot measure (or re-release) with changed code and exit success.

Cases covered:

* ``freeze`` into a fresh tmp dest returns 0 and writes ``<version>/harness.tar`` plus
  ``MANIFEST.json`` (Req 2.1);
* an identical re-``freeze`` returns 0 (idempotent no-op, Req 3.1 surfaced through the CLI);
* ``verify`` of the just-frozen release returns 0 (intact, Req 4.2);
* ``verify`` of an ABSENT release (fresh tmp, nothing frozen) returns non-zero (fail-closed, Req
  4.5 — a missing artifact is never silently "intact");
* ``verify`` of a one-content-byte-mutated release returns non-zero (Req 4.5): the tar is rewritten
  with the SAME normalized member metadata the production freeze uses, one content byte flipped,
  MANIFEST.json untouched;
* ``freeze`` into an existing release whose SOURCE now differs returns non-zero (``ReleaseExists``
  mapped to exit 1, Req 2.5). Because the CLI freezes the LIVE package root
  (``release._package_root``), this case monkeypatches ``release._package_root`` to point at a tmp
  source we can mutate between the two freezes, so the real ``_cmd_freeze`` → :func:`freeze` →
  ``ReleaseExists`` → ``_EXIT_FAIL`` exit path is driven honestly. (The ``ReleaseExists`` behaviour
  is also covered at the function level by the release unit test, task 3.4.)

House posture: ``tmp_path`` is passed as ``--dest-root`` for EVERY ``freeze`` so NOTHING is ever
written into the real ``load_harness/bin/`` tree; the one monkeypatched case also redirects the
package root to a tmp dir so even the source read is a tmp copy. Test files are not under the
import-linter layer contract.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from load_harness import release
from load_harness.release import _EXIT_FAIL, _EXIT_OK, main
from load_harness.version import HARNESS_VERSION

# The release version the CLI stamps/reads by default (the single authority). Used to locate the
# written ``<version>/`` artifact directory under the tmp dest.
_VERSION = str(HARNESS_VERSION)

# The normalized tar-member format the production freeze writes
# (release._write_normalized_tar) — reproduced EXACTLY by the mutation helper so the only difference
# between the frozen archive and the rewritten one is the single flipped content byte.
_TAR_MEMBER_ROOT = "load_harness"
_TAR_MEMBER_MTIME = 0
_TAR_MEMBER_MODE = 0o644
_TAR_MEMBER_UID = 0
_TAR_MEMBER_GID = 0


def _release_dir(dest_root: Path) -> Path:
    """The ``<dest_root>/<version>/`` directory the CLI writes a release into."""
    return dest_root / _VERSION


def _mutate_one_content_byte(archive_path: Path) -> None:
    """Rewrite ``harness.tar`` with exactly one content byte of its first member flipped.

    Reads every regular member, flips one byte of the first non-empty member's content, and
    rewrites the archive with the SAME normalized member metadata the production freeze uses
    (``USTAR_FORMAT``, ``REGTYPE``, fixed mtime/mode/uid/gid, empty owner names, original member
    order). ``MANIFEST.json`` is left untouched, so the manifest's recorded digest for the mutated
    member no longer matches its recomputed digest and :func:`verify` must report a Mutated_Copy.
    """
    members: list[tuple[str, bytes]] = []
    with tarfile.open(archive_path, mode="r") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            with extracted:
                members.append((member.name, extracted.read()))

    # The live load_harness source always has at least one non-empty member.
    mutated: list[tuple[str, bytes]] = []
    flipped = False
    for name, content in members:
        if not flipped and content:
            body = bytearray(content)
            body[0] ^= 0x01
            mutated.append((name, bytes(body)))
            flipped = True
        else:
            mutated.append((name, content))
    assert flipped, "expected at least one non-empty frozen member to mutate"

    with tarfile.open(archive_path, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, content in mutated:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mtime = _TAR_MEMBER_MTIME
            info.mode = _TAR_MEMBER_MODE
            info.type = tarfile.REGTYPE
            info.uid = _TAR_MEMBER_UID
            info.gid = _TAR_MEMBER_GID
            info.uname = ""
            info.gname = ""
            tar.addfile(info, io.BytesIO(content))


def test_freeze_returns_ok_and_writes_artifact(tmp_path: Path) -> None:
    """``freeze --dest-root <tmp>`` returns 0 and writes the archive + MANIFEST.json (Req 2.1)."""
    dest = tmp_path / "bin"

    code = main(["freeze", "--dest-root", str(dest)])

    assert code == _EXIT_OK
    release_dir = _release_dir(dest)
    assert (release_dir / "harness.tar").is_file()
    assert (release_dir / "MANIFEST.json").is_file()


def test_freeze_is_idempotent_no_op(tmp_path: Path) -> None:
    """A second identical ``freeze`` into the same dest returns 0 (idempotent no-op, Req 3.1)."""
    dest = tmp_path / "bin"

    first = main(["freeze", "--dest-root", str(dest)])
    second = main(["freeze", "--dest-root", str(dest)])

    assert first == _EXIT_OK
    assert second == _EXIT_OK


def test_verify_intact_release_returns_ok(tmp_path: Path) -> None:
    """``verify`` of a just-frozen, untouched release returns 0 (intact, Req 4.2)."""
    dest = tmp_path / "bin"
    assert main(["freeze", "--dest-root", str(dest)]) == _EXIT_OK

    code = main(["verify", "--dest-root", str(dest)])

    assert code == _EXIT_OK


def test_verify_absent_release_fails_closed(tmp_path: Path) -> None:
    """``verify`` of an ABSENT release (nothing frozen) returns non-zero (fail-closed, Req 4.5)."""
    dest = tmp_path / "bin"  # fresh, empty — no freeze performed

    code = main(["verify", "--dest-root", str(dest)])

    assert code == _EXIT_FAIL


def test_verify_mutated_release_fails_closed(tmp_path: Path) -> None:
    """``verify`` of a one-content-byte-mutated release returns non-zero (Req 4.5)."""
    dest = tmp_path / "bin"
    assert main(["freeze", "--dest-root", str(dest)]) == _EXIT_OK

    _mutate_one_content_byte(_release_dir(dest) / "harness.tar")

    code = main(["verify", "--dest-root", str(dest)])

    assert code == _EXIT_FAIL


def test_freeze_differing_release_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``freeze`` of a DIFFERING release under an existing version returns non-zero (Req 2.5).

    The CLI freezes the LIVE package root (``release._package_root``). To exercise the
    refuse-to-overwrite-a-differing-release path through the real ``_cmd_freeze`` →
    :func:`~load_harness.release.freeze` → ``ReleaseExists`` → ``_EXIT_FAIL`` exit mapping, we point
    ``release._package_root`` at a tmp source we control: freeze once (0), mutate the source, freeze
    again under the SAME version/dest — the second freeze must refuse (non-zero) rather than
    overwrite the artifact the first release was attributed to.
    """
    src = tmp_path / "src" / "load_harness"
    src.mkdir(parents=True)
    (src / "config.py").write_text("ORIGINAL = 1\n", encoding="utf-8")
    monkeypatch.setattr(release, "_package_root", lambda: src)

    dest = tmp_path / "bin"
    first = main(["freeze", "--dest-root", str(dest)])
    assert first == _EXIT_OK

    # The source now differs under the already-taken version → ReleaseExists → exit 1.
    (src / "config.py").write_text("MUTATED = 2\n", encoding="utf-8")
    second = main(["freeze", "--dest-root", str(dest)])

    assert second == _EXIT_FAIL
