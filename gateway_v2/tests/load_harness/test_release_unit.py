"""Release / verify edge-case EXAMPLE unit tests (B7 / R2-20, partial), task 3.4.

# Feature: harness-release-hygiene-r2-20
# Validates: Requirements 2.5, 3.1, 4.2, 4.3, 4.4, 6.5

These are focused, representative EXAMPLE assertions (NOT property-based — the >= 10,000-iteration
house-idiom sweeps live in ``test_release.py`` / ``test_release_digest.py`` / ``test_verify.py`` /
``test_verify_fileset.py``). They pin the concrete release/verify edge cases the design's Testing
Strategy (unit tests) and Error Handling sections call out:

* ``ReleaseExists`` on a DIFFERING re-freeze, with the original artifact left untouched (Req 2.5);
* an IDENTICAL re-freeze is an idempotent no-op returning an equal :class:`FrozenRelease` (Req 3.1);
* :func:`verify` on an intact release -> ``intact=True`` / ``is_mutated_copy=False`` (Req 4.2);
* :func:`verify` on a content mutation -> the relpath in ``mutated_files`` (Req 4.3);
* :func:`verify` on a missing archive member -> ``missing_files``; an extra member ->
  ``extra_files`` (Req 4.4);
* :func:`verify` on a malformed-or-absent ``MANIFEST.json`` / an absent archive FAILS CLOSED —
  ``is_mutated_copy`` True, NOT ``intact`` (Req 4.5, 6.5 fail-closed);
* :meth:`IntegrityManifest.to_json` / :meth:`from_json` round-trip + the ``__post_init__`` guards
  (unsorted entries raise, duplicate relpath raises);
* :class:`MutatedCopyError` is an ``Exception`` subclass (sanity).

Everything is pure / offline / server-free (Req 6.5). All filesystem artifacts are written only
under pytest's ``tmp_path`` — nothing is ever frozen into the real ``load_harness/`` tree.

**The normalized-tar rewrite helper.** The content-mutation / missing / extra examples need an
archive that diverges from its (unchanged) ``MANIFEST.json`` in a controlled way. ``freeze``
recomputes the manifest from live source every call, so a source edit can never produce such a
divergence; instead the helper rebuilds ``harness.tar`` with the SAME normalized member metadata
the shipped ``release._write_normalized_tar`` writes (member name ``load_harness/<relpath>``, fixed
epoch mtime, ``0o644`` mode, root uid/gid 0, empty owner names, ``USTAR_FORMAT`` / ``REGTYPE``,
members in sorted relpath order) while leaving ``MANIFEST.json`` byte-unchanged. A drift test
(:func:`test_no_change_rebuild_verifies_intact`) pins the helper to the shipped normalization so a
no-change rebuild verifies intact and the deliberate divergence is the ONLY difference.

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from load_harness.release import (
    FrozenRelease,
    IntegrityManifest,
    ManifestEntry,
    MutatedCopyError,
    ReleaseExists,
    freeze,
    verify,
)
from load_harness.version import Version

# A fixed release version for the examples; the identifier has no "/" so it is one path component
# under dest_root.
_VERSION = Version(identifier="v2.3")

# The normalized member metadata the shipped ``release._write_normalized_tar`` writes, pinned here
# so a rebuilt archive is byte-for-byte the same normalization as a shipped freeze (the drift test
# proves a no-change rebuild verifies intact).
_TAR_MEMBER_ROOT = "load_harness"
_TAR_MEMBER_MTIME = 0
_TAR_MEMBER_MODE = 0o644
_TAR_MEMBER_UID = 0
_TAR_MEMBER_GID = 0


def _write_source(package_root: Path, files: dict[str, bytes]) -> None:
    """Materialize ``{relpath: content}`` under ``package_root`` (a tmp ``load_harness/`` dir)."""
    for relpath, content in files.items():
        target = package_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _read_members(archive_path: Path) -> dict[str, bytes]:
    """Read ``harness.tar`` into a ``relpath -> content`` map (``load_harness/`` root stripped)."""
    members: dict[str, bytes] = {}
    prefix = f"{_TAR_MEMBER_ROOT}/"
    with tarfile.open(archive_path, mode="r") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            with extracted:
                data = extracted.read()
            name = member.name
            relpath = name[len(prefix):] if name.startswith(prefix) else name
            members[relpath] = data
    return members


def _rebuild_archive(archive_path: Path, members: dict[str, bytes]) -> None:
    """Rewrite ``harness.tar`` with ``members`` in sorted relpath order, shipped-normalized.

    Uses an explicit :class:`tarfile.TarInfo` per member (never ``tar.add``) so the archive bytes
    depend only on content + the canonical member name, mirroring ``release._write_normalized_tar``.
    """
    with tarfile.open(archive_path, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for relpath in sorted(members):
            data = members[relpath]
            info = tarfile.TarInfo(name=f"{_TAR_MEMBER_ROOT}/{relpath}")
            info.size = len(data)
            info.mtime = _TAR_MEMBER_MTIME
            info.mode = _TAR_MEMBER_MODE
            info.type = tarfile.REGTYPE
            info.uid = _TAR_MEMBER_UID
            info.gid = _TAR_MEMBER_GID
            info.uname = ""
            info.gname = ""
            tar.addfile(info, io.BytesIO(data))


# --------------------------------------------------------------------------------------------------
# freeze — refuse-to-overwrite + idempotent no-op (Req 2.5, 3.1)
# --------------------------------------------------------------------------------------------------


def test_release_exists_on_differing_refreeze_leaves_original_untouched(tmp_path: Path) -> None:
    """A differing re-freeze raises ``ReleaseExists`` and never overwrites the original (Req 2.5).

    Freeze source A into ``bin/<version>/``, capture the archive + manifest bytes, then change one
    file and freeze the SAME version into the SAME dest: ``ReleaseExists`` is raised and both the
    archive and the manifest are byte-identical to the originals (fail-closed, never overwrite).
    """
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "report.py": b"beta"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    archive_before = frozen.archive_path.read_bytes()
    manifest_before = frozen.manifest_path.read_bytes()

    # Change a file's content so the recomputed aggregate differs from the frozen one.
    (src / "report.py").write_bytes(b"beta-CHANGED")

    with pytest.raises(ReleaseExists):
        freeze(src, _VERSION, dest)

    assert frozen.archive_path.read_bytes() == archive_before, "archive was overwritten"
    assert frozen.manifest_path.read_bytes() == manifest_before, "manifest was overwritten"


def test_identical_refreeze_is_idempotent_noop(tmp_path: Path) -> None:
    """An identical re-freeze is a no-op returning an equal ``FrozenRelease`` (Req 3.1).

    Freezing the same source under the same version twice does not raise, returns an equal
    :class:`FrozenRelease` handle, and leaves the archive + manifest bytes byte-identical.
    """
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "strata/keys.py": b"keys"})
    dest = tmp_path / "bin"

    first = freeze(src, _VERSION, dest)
    archive_before = first.archive_path.read_bytes()
    manifest_before = first.manifest_path.read_bytes()

    second = freeze(src, _VERSION, dest)

    assert second == first, "an identical re-freeze returned a differing FrozenRelease handle"
    assert second.archive_path.read_bytes() == archive_before, "archive bytes changed on re-freeze"
    assert second.manifest_path.read_bytes() == manifest_before, (
        "manifest bytes changed on re-freeze"
    )


# --------------------------------------------------------------------------------------------------
# verify — intact / mutated / missing / extra (Req 4.2, 4.3, 4.4)
# --------------------------------------------------------------------------------------------------


def test_verify_intact_release(tmp_path: Path) -> None:
    """An untouched release verifies ``intact=True`` and ``is_mutated_copy=False`` (Req 4.2)."""
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "report.py": b"beta", "strata/keys.py": b"keys"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    result = verify(frozen)

    assert result.intact is True
    assert result.is_mutated_copy is False
    assert result.mutated_files == ()
    assert result.missing_files == ()
    assert result.extra_files == ()


def test_verify_content_mutation_reports_mutated_file(tmp_path: Path) -> None:
    """A changed member's content is reported in ``mutated_files`` only (Req 4.3).

    Rebuild ``harness.tar`` with one member's content changed (manifest untouched): the member set
    is unchanged, so the relpath lands in ``mutated_files`` and NOT in missing/extra.
    """
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "report.py": b"beta"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    members = _read_members(frozen.archive_path)
    members["report.py"] = b"beta-MUTATED"
    _rebuild_archive(frozen.archive_path, members)

    result = verify(frozen)

    assert result.is_mutated_copy is True
    assert result.intact is False
    assert result.mutated_files == ("report.py",)
    assert result.missing_files == ()
    assert result.extra_files == ()


def test_verify_missing_file_reports_missing(tmp_path: Path) -> None:
    """A manifest file absent from the archive is reported in ``missing_files`` (Req 4.4)."""
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "report.py": b"beta"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    members = _read_members(frozen.archive_path)
    del members["report.py"]
    _rebuild_archive(frozen.archive_path, members)

    result = verify(frozen)

    assert result.is_mutated_copy is True
    assert result.intact is False
    assert result.missing_files == ("report.py",)
    assert result.mutated_files == ()
    assert result.extra_files == ()


def test_verify_extra_file_reports_extra(tmp_path: Path) -> None:
    """An archive member absent from the manifest is reported in ``extra_files`` (Req 4.4)."""
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "report.py": b"beta"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    members = _read_members(frozen.archive_path)
    members["STRAY_EXTRA.dat"] = b"not in the manifest"
    _rebuild_archive(frozen.archive_path, members)

    result = verify(frozen)

    assert result.is_mutated_copy is True
    assert result.intact is False
    assert result.extra_files == ("STRAY_EXTRA.dat",)
    assert result.mutated_files == ()
    assert result.missing_files == ()


# --------------------------------------------------------------------------------------------------
# verify — fails closed on an absent / malformed artifact (Req 4.5, 6.5)
# --------------------------------------------------------------------------------------------------


def test_verify_absent_archive_fails_closed(tmp_path: Path) -> None:
    """A missing ``harness.tar`` is non-attributable: is_mutated_copy True, NOT intact (Req 4.5)."""
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    frozen.archive_path.unlink()

    result = verify(frozen)

    assert result.intact is False
    assert result.is_mutated_copy is True


def test_verify_absent_manifest_fails_closed(tmp_path: Path) -> None:
    """A deleted ``MANIFEST.json`` fails closed rather than reporting intact (Req 4.5, 6.5)."""
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    frozen.manifest_path.unlink()

    result = verify(frozen)

    assert result.intact is False
    assert result.is_mutated_copy is True


def test_verify_corrupt_manifest_fails_closed(tmp_path: Path) -> None:
    """A malformed (non-JSON) ``MANIFEST.json`` fails closed, never silently intact (Req 4.5)."""
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    frozen.manifest_path.write_text("}{ not valid json", encoding="utf-8")

    result = verify(frozen)

    assert result.intact is False
    assert result.is_mutated_copy is True


# --------------------------------------------------------------------------------------------------
# IntegrityManifest — JSON round-trip + __post_init__ guards
# --------------------------------------------------------------------------------------------------


def test_manifest_to_json_from_json_round_trip(tmp_path: Path) -> None:
    """``to_json(from_json(text)) == text`` for a manifest a real freeze produced (Req 3.3)."""
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "report.py": b"beta", "strata/keys.py": b"keys"})
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    text = frozen.manifest_path.read_text(encoding="utf-8")

    manifest = IntegrityManifest.from_json(text)
    assert manifest.to_json() == text
    assert manifest.version == str(_VERSION)
    # The parsed entries are in canonical sorted-by-relpath order.
    relpaths = [entry.relpath for entry in manifest.entries]
    assert relpaths == sorted(relpaths)


def test_manifest_rejects_unsorted_entries() -> None:
    """Constructing a manifest with entries out of relpath order raises (byte-stability guard)."""
    entries = (
        ManifestEntry(relpath="b.py", sha256="0" * 64),
        ManifestEntry(relpath="a.py", sha256="1" * 64),
    )
    with pytest.raises(ValueError, match="sorted by relpath"):
        IntegrityManifest(version="v2.3", entries=entries, aggregate_sha256="agg")


def test_manifest_rejects_duplicate_relpath() -> None:
    """Constructing a manifest with a duplicate relpath raises (canonical-set guard)."""
    entries = (
        ManifestEntry(relpath="a.py", sha256="0" * 64),
        ManifestEntry(relpath="a.py", sha256="1" * 64),
    )
    with pytest.raises(ValueError, match="repeat a relpath"):
        IntegrityManifest(version="v2.3", entries=entries, aggregate_sha256="agg")


# --------------------------------------------------------------------------------------------------
# MutatedCopyError sanity + rebuild-helper drift guard
# --------------------------------------------------------------------------------------------------


def test_mutated_copy_error_is_exception_subclass() -> None:
    """``MutatedCopyError`` is a plain ``Exception`` subclass the run-time refuse posture raises."""
    assert issubclass(MutatedCopyError, Exception)
    with pytest.raises(MutatedCopyError):
        raise MutatedCopyError("mutated")


def test_no_change_rebuild_verifies_intact(tmp_path: Path) -> None:
    """A no-change rebuild of ``harness.tar`` verifies intact — pins the local helper normalization.

    Proves :func:`_rebuild_archive` reproduces the shipped ``release._write_normalized_tar``
    normalization so the mutation / missing / extra examples differ from the manifest ONLY in the
    deliberate way, not in incidental tar metadata.
    """
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_source(src, {"config.py": b"alpha", "strata/keys.py": b"keys"})
    dest = tmp_path / "bin"

    frozen: FrozenRelease = freeze(src, _VERSION, dest)
    members = _read_members(frozen.archive_path)
    _rebuild_archive(frozen.archive_path, members)

    result = verify(frozen)
    assert result.intact is True
    assert result.is_mutated_copy is False
