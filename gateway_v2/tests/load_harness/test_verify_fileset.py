"""Property 3 for the harness integrity guard (B7 / R2-20, partial), task 3.3.

# Feature: harness-release-hygiene-r2-20, Property 3: missing or extra files are detected
# Validates: Requirements 4.2, 4.4

**Property 3 — Missing or extra files are detected.** For ANY frozen release:

* removing ANY manifest-listed file from the ARCHIVE (but leaving its entry in ``MANIFEST.json``)
  makes :func:`~load_harness.release.verify` report ``is_mutated_copy`` with that relpath in
  ``missing_files`` (Req 4.4) and NOT in ``mutated_files``/``extra_files``;
* adding ANY file to the ARCHIVE that is NOT in the manifest makes :func:`verify` report
  ``is_mutated_copy`` with that relpath in ``extra_files`` (Req 4.4) and NOT in
  ``mutated_files``/``missing_files``;
* an UNTOUCHED release verifies as ``intact=True`` (Req 4.2) — exercised every iteration as a
  sanity check, so the "detected" assertions cannot pass vacuously against a ``verify`` that always
  reports a mutation.

**How a missing/extra ARCHIVE is built without touching the manifest.** ``freeze`` recomputes the
manifest from the live source every call, so a source edit can never produce an archive/manifest
MISMATCH. Instead, after a real :func:`freeze` into ``tmp_path``, this test REBUILDS ``harness.tar``
in place with the SAME normalized member metadata the shipped ``release._write_normalized_tar`` uses
(member name ``load_harness/<relpath>``, fixed epoch mtime, fixed ``0o644`` mode, root uid/gid 0,
empty owner names, members in sorted relpath order) — but with one member DROPPED (missing) or one
STRAY member ADDED (extra). ``MANIFEST.json`` is left byte-unchanged, so the archive diverges from
the manifest in exactly the file SET while every surviving member's CONTENT digest still matches. A
drift test (``test_rebuilt_tar_matches_shipped_normalization``) pins the local rebuild helper to the
shipped freeze normalization so a round-trip rebuild with NO change verifies as intact.

House idiom (Req 6.6): a seeded :func:`random.Random` driven for >= 10,000 iterations, NO
``hypothesis``. Every artifact is written only under pytest's ``tmp_path`` — nothing is ever frozen
into the real ``load_harness/`` tree.

**Honest iteration split (kept fast, floor met by the property loop).** The >= 10,000-iteration
house floor is carried by the FAST loop (``_FAST_ITERATIONS`` = 10,000): each iteration generates a
fresh random source set, freezes it ONCE into ``tmp_path`` (building the authoritative normalized
tar + manifest), verifies the untouched release is intact (sanity), then for the chosen op
(drop-member -> missing, add-member -> extra) rebuilds ``harness.tar`` from the SAME source bytes
with the one member dropped/added and asserts the exact classification. Both ops are driven by the
``random.Random`` across the sweep (op and which-file are random), so the full missing/extra space
over varied file sets is exercised. A re-freeze is never needed — the rebuild reuses the already
materialized source bytes — so the per-iteration cost stays in the tar+hashlib range.

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import io
import random
import string
import tarfile
from pathlib import Path

from load_harness.release import (
    FrozenRelease,
    IntegrityManifest,
    freeze,
    iter_source_files,
    verify,
)
from load_harness.version import Version

# The >= 10,000 house-idiom floor is carried by the fast per-iteration loop: one freeze + one
# archive rebuild (drop/add a member) + one verify, all over tmp_path.
_FAST_ITERATIONS = 10_000

# A fixed release version for the whole sweep. The missing/extra classification is a property of the
# archive-vs-manifest file SET under one release, so a single stamp keeps the artifact comparable;
# the identifier avoids a "/" so it is a single path component under dest_root.
_VERSION = Version(identifier="v2.3")

# Small source sets keep each iteration cheap while exercising a multi-file sorted member set with
# nested directories. >= 2 files so a drop always leaves a non-empty surviving set.
_MIN_FILES = 2
_MAX_FILES = 5
_MAX_CONTENT_BYTES = 24

# Package-relative directory segments a generated file may nest under (all LIVE-source segments; the
# excluded classes are covered by the Property 1 / Property 4 sweeps, so this test keeps every
# generated file inside the captured set to focus on the archive file-SET divergence).
_DIR_SEGMENTS = ("", "strata", "sub/deep")
_NAME_ALPHABET = string.ascii_lowercase + string.digits

# The normalized member metadata the shipped ``release._write_normalized_tar`` writes. Pinned here
# so a rebuilt archive is byte-for-byte the same normalization as a shipped freeze (the drift test
# proves a no-change rebuild verifies as intact). These mirror the module's determinism constants.
_TAR_MEMBER_ROOT = "load_harness"
_TAR_MEMBER_MTIME = 0
_TAR_MEMBER_MODE = 0o644
_TAR_MEMBER_UID = 0
_TAR_MEMBER_GID = 0

# A stray relpath for the "extra" op — a name the generator never produces (the generator draws
# lowercase/digit stems ending ``.py``), so an added member can never collide with a manifest entry.
_STRAY_RELPATH = "STRAY_EXTRA_FILE.dat"
_STRAY_CONTENT = b"stray archive member not in the manifest"


def _random_relpath(rng: random.Random, used: set[str]) -> str:
    """A unique package-relative POSIX path for one generated source file.

    Draws a directory segment and a short lowercase/digit stem, retrying until the composed relpath
    is unique within the set so no two generated files collide on a path (the manifest forbids a
    duplicate relpath). The ``.py`` suffix and lowercase/digit stem guarantee the name is NEVER the
    all-caps ``_STRAY_RELPATH``, so the "extra" member cannot accidentally match a manifest entry.
    """
    while True:
        segment = rng.choice(_DIR_SEGMENTS)
        stem = "".join(rng.choice(_NAME_ALPHABET) for _ in range(rng.randint(1, 6)))
        name = f"{stem}.py"
        relpath = f"{segment}/{name}" if segment else name
        if relpath not in used:
            used.add(relpath)
            return relpath


def _write_random_source(rng: random.Random, package_root: Path) -> None:
    """Materialize a fresh random source tree under ``package_root`` (a tmp dir).

    Writes 2-5 tiny files at unique package-relative paths with random content bytes, all inside the
    captured live-source set so the manifest lists every one of them.
    """
    used: set[str] = set()
    count = rng.randint(_MIN_FILES, _MAX_FILES)
    for _ in range(count):
        relpath = _random_relpath(rng, used)
        target = package_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        length = rng.randint(0, _MAX_CONTENT_BYTES)
        content = bytes(rng.randint(0, 255) for _ in range(length))
        target.write_bytes(content)


def _add_normalized_member(tar: tarfile.TarFile, relpath: str, data: bytes) -> None:
    """Write one fully normalized member matching the shipped freeze normalization.

    Mirrors ``release._write_normalized_tar``: member name ``load_harness/<relpath>``, size from the
    content bytes, fixed epoch mtime, fixed ``0o644`` regular-file mode, root uid/gid 0, empty owner
    names. Written with an explicit :class:`tarfile.TarInfo` (never ``tar.add``) so the member bytes
    depend only on content + the canonical name, exactly as a shipped freeze would — a no-change
    rebuild is therefore byte-reproducible and verifies as intact (pinned by the drift test).
    """
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


def _read_archive_members(archive_path: Path) -> dict[str, bytes]:
    """Read a frozen ``harness.tar`` into a ``relpath -> content bytes`` map (root prefix stripped).

    Returns the current archive members keyed by their package-relative POSIX relpath (the
    ``load_harness/`` root stripped), so a rebuild can reproduce the exact member set with one
    member dropped or added.
    """
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
    """Rewrite ``harness.tar`` with ``members`` in canonical sorted relpath order, normalized.

    Each member is written through :func:`_add_normalized_member` so the rebuilt archive uses the
    SAME normalization as the shipped freeze; members are emitted in sorted relpath order to match
    the canonical member order. The manifest is left untouched by the caller, so the only change
    between the shipped archive and this one is the dropped/added member.
    """
    with tarfile.open(archive_path, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for relpath in sorted(members):
            _add_normalized_member(tar, relpath, members[relpath])


def test_property3_missing_or_extra_files_are_detected(tmp_path: Path) -> None:
    """Removing a manifest file (missing) or adding a stray (extra) is detected; intact otherwise.

    The >= 10,000-iteration fast loop freezes a fresh random source set into ``tmp_path``, verifies
    the untouched release is intact (sanity), then rebuilds ``harness.tar`` with either one member
    dropped (-> ``missing_files``) or one stray member added (-> ``extra_files``), leaving
    ``MANIFEST.json`` byte-unchanged, and asserts the exact classification: ``is_mutated_copy`` is
    ``True``, the relpath is in the right bucket and NOT in the other buckets. Op and which-file are
    both driven by the seeded ``random.Random`` so the missing/extra space is covered across varied
    file sets.
    """
    seed = 0x3F15
    rng = random.Random(seed)
    missing_ops = 0
    extra_ops = 0

    for i in range(_FAST_ITERATIONS):
        src = tmp_path / "it" / f"i{i}" / "load_harness"
        src.mkdir(parents=True, exist_ok=True)
        _write_random_source(rng, src)
        dest = src.parent / "bin"

        frozen = freeze(src, _VERSION, dest)

        # Sanity (Req 4.2): an untouched release verifies as intact every iteration, so a
        # "detected" assertion below can never pass vacuously against an always-mutated verify.
        intact = verify(frozen)
        assert intact.intact and not intact.is_mutated_copy, (
            f"seed={seed:#x} iter={i}: freshly frozen untouched release was not intact: {intact}"
        )

        members = _read_archive_members(frozen.archive_path)
        manifest_before = frozen.manifest_path.read_bytes()

        if rng.random() < 0.5:
            missing_ops += 1
            _assert_missing_detected(rng, frozen, members, i, seed)
        else:
            extra_ops += 1
            _assert_extra_detected(frozen, members, i, seed)

        # The manifest is NEVER touched by either op — the divergence is purely in the archive.
        assert frozen.manifest_path.read_bytes() == manifest_before, (
            f"seed={seed:#x} iter={i}: MANIFEST.json was modified by the archive rebuild"
        )

    assert _FAST_ITERATIONS >= 10_000
    assert missing_ops > 0 and extra_ops > 0, (
        f"seed={seed:#x}: one op never ran (missing={missing_ops}, extra={extra_ops}) — the "
        f"missing/extra space was not both exercised"
    )


def _assert_missing_detected(
    rng: random.Random,
    frozen: FrozenRelease,
    members: dict[str, bytes],
    i: int,
    seed: int,
) -> None:
    """Drop one manifest-listed member from the archive and assert it is reported ``missing``.

    Picks a random surviving member to drop, rebuilds the archive without it (manifest unchanged),
    and asserts :func:`verify` reports ``is_mutated_copy`` with exactly that relpath in
    ``missing_files`` and NOT in ``mutated_files`` / ``extra_files`` (every surviving member's
    content still matches its manifest entry, so nothing is mutated or extra).
    """
    dropped = rng.choice(sorted(members))
    remaining = {relpath: body for relpath, body in members.items() if relpath != dropped}
    _rebuild_archive(frozen.archive_path, remaining)

    result = verify(frozen)
    assert result.is_mutated_copy and not result.intact, (
        f"seed={seed:#x} iter={i}: dropping {dropped!r} from the archive was NOT detected: "
        f"{result}"
    )
    assert dropped in result.missing_files, (
        f"seed={seed:#x} iter={i}: dropped member {dropped!r} not reported in missing_files: "
        f"{result.missing_files}"
    )
    assert result.missing_files == (dropped,), (
        f"seed={seed:#x} iter={i}: expected exactly one missing file {dropped!r}, got "
        f"{result.missing_files}"
    )
    assert result.mutated_files == () and result.extra_files == (), (
        f"seed={seed:#x} iter={i}: a dropped member was misclassified as mutated/extra: "
        f"mutated={result.mutated_files} extra={result.extra_files}"
    )


def _assert_extra_detected(
    frozen: FrozenRelease,
    members: dict[str, bytes],
    i: int,
    seed: int,
) -> None:
    """Add a stray member not in the manifest and assert it is reported ``extra``.

    Rebuilds the archive with every original member PLUS a stray relpath the generator never
    produces (manifest unchanged), and asserts :func:`verify` reports ``is_mutated_copy`` with
    exactly that stray relpath in ``extra_files`` and NOT in ``mutated_files`` / ``missing_files``
    (every manifest-listed member is still present with matching content).
    """
    augmented = dict(members)
    augmented[_STRAY_RELPATH] = _STRAY_CONTENT
    _rebuild_archive(frozen.archive_path, augmented)

    result = verify(frozen)
    assert result.is_mutated_copy and not result.intact, (
        f"seed={seed:#x} iter={i}: adding stray {_STRAY_RELPATH!r} to the archive was NOT "
        f"detected: {result}"
    )
    assert _STRAY_RELPATH in result.extra_files, (
        f"seed={seed:#x} iter={i}: stray member {_STRAY_RELPATH!r} not reported in extra_files: "
        f"{result.extra_files}"
    )
    assert result.extra_files == (_STRAY_RELPATH,), (
        f"seed={seed:#x} iter={i}: expected exactly one extra file {_STRAY_RELPATH!r}, got "
        f"{result.extra_files}"
    )
    assert result.mutated_files == () and result.missing_files == (), (
        f"seed={seed:#x} iter={i}: a stray extra member was misclassified as mutated/missing: "
        f"mutated={result.mutated_files} missing={result.missing_files}"
    )


def test_rebuilt_tar_matches_shipped_normalization(tmp_path: Path) -> None:
    """A no-change rebuild verifies as intact — pins the local helper to the shipped normalization.

    Freezes a small source set, reads the archive members, rebuilds ``harness.tar`` from the SAME
    members through :func:`_rebuild_archive` (no drop/add), and asserts :func:`verify` reports the
    release intact. This drift guard proves the local rebuild helper reproduces the shipped
    ``release._write_normalized_tar`` normalization (member name/root, fixed mtime/mode/owner,
    sorted order) so a missing/extra divergence in the property loop is the ONLY difference the
    archive has from the manifest — not an incidental metadata drift.
    """
    src = tmp_path / "pkg" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    (src / "config.py").write_bytes(b"alpha")
    (src / "report.py").write_bytes(b"beta")
    (src / "strata").mkdir()
    (src / "strata" / "keys.py").write_bytes(b"keys")
    dest = tmp_path / "bin"

    frozen = freeze(src, _VERSION, dest)
    # Confirm the captured set matches what we enumerate, so the rebuild round-trips the same files.
    assert {p.as_posix() for p in iter_source_files(src)} == {
        "config.py",
        "report.py",
        "strata/keys.py",
    }

    members = _read_archive_members(frozen.archive_path)
    _rebuild_archive(frozen.archive_path, members)

    result = verify(frozen)
    assert result.intact and not result.is_mutated_copy, (
        f"a no-change rebuild was not intact — the local rebuild helper drifted from the shipped "
        f"freeze normalization: {result}"
    )
    # The manifest records the single-authority version.
    manifest = IntegrityManifest.from_json(frozen.manifest_path.read_text(encoding="utf-8"))
    assert manifest.version == str(_VERSION)
