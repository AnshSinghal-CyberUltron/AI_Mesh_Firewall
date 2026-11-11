"""Single-byte-mutation detection property test (B7 / R2-20, partial), task 3.2.

# Feature: harness-release-hygiene-r2-20, Property 2: any single-byte mutation is detected
# Validates: Requirements 4.1, 4.3

**Property 2 — Any single-byte mutation is detected.** For ANY frozen release and ANY single-byte
change to the CONTENT of ANY one frozen file, :func:`~load_harness.release.verify` reports
``is_mutated_copy`` with that file in ``mutated_files`` (never ``intact``), and the mutated relpath
appears in NEITHER ``missing_files`` NOR ``extra_files`` (the member set is unchanged — only one
member's content byte differs). An untouched release verifies as ``intact`` (per-iteration sanity).

House idiom (Req 6.6): a seeded :func:`random.Random` driven for >= 10,000 iterations, NO
``hypothesis``. Everything is pure/offline/server-free. Filesystem artifacts are written only under
pytest's ``tmp_path`` — nothing is ever frozen into the real ``load_harness/`` tree.

**The tar-mutation helper.** ``verify`` recomputes each tar member's CONTENT digest and compares it
against ``MANIFEST.json``. To make a Mutated_Copy that differs ONLY in content (so the mutation
lands in ``mutated_files`` and not in missing/extra), the helper rewrites ``harness.tar`` with the
SAME normalized member metadata the production freeze uses — member name ``load_harness/<relpath>``,
fixed mtime ``0`` / mode ``0o644`` / uid/gid ``0`` / empty uname/gname, ``USTAR_FORMAT`` /
``REGTYPE``, members in the original sorted order — but with exactly ONE byte of ONE member's
content flipped. ``MANIFEST.json`` is left UNCHANGED, so the manifest's recorded digest for the
mutated member no longer matches its recomputed digest → ``mutated_files``. A member with empty
content cannot have a byte flipped, so the helper only ever targets a member with >= 1 content byte
(the source generator guarantees at least one such member per release).

**Honest iteration split (floor met by the property loop).** The >= 10,000-iteration house floor is
carried ENTIRELY by the property loop (``_ITERATIONS`` = 10,000): each iteration builds a small
random source set, materializes it under ``tmp_path``, performs a REAL :func:`freeze` into a
``tmp_path`` dest, verifies it intact (sanity), then rewrites the tar with one content byte flipped
and asserts the mutated relpath is in ``mutated_files`` and not in missing/extra. Every iteration
runs the FULL freeze + full verify (no pure-vs-full split is used — the floor is met by real
freezes); source sets are kept tiny (1-4 files, <= 24 content bytes) so each real freeze+verify is
cheap. The in-memory tar rewrite reuses the exact normalized-member format, so the rebuilt archive
is byte-for-byte what the freeze would have produced for the mutated content.

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
    freeze,
    verify,
)
from load_harness.version import Version

# The >= 10,000 house-idiom floor, carried entirely by this property loop. Every iteration runs a
# real freeze + a real verify of both an intact and a one-byte-mutated archive, so no pure-vs-full
# split is needed; the small source sets keep each iteration cheap.
_ITERATIONS = 10_000

# A fixed release version for the whole sweep. Property 2 fixes the Version and the member SET and
# varies only ONE content byte, so a verify difference can only come from the mutated content. The
# identifier avoids a "/" so it is a single path component under dest_root.
_VERSION = Version(identifier="v2.3")

# Small source sets keep each iteration's real freeze + verify in the microsecond-to-millisecond
# range while still exercising a multi-file sorted member set with nested directories.
_MIN_FILES = 1
_MAX_FILES = 4
_MAX_CONTENT_BYTES = 24

# The normalized tar-member format the production freeze writes (release._write_normalized_tar). The
# mutation helper reproduces it EXACTLY so the only difference between the frozen archive and the
# rewritten one is the single flipped content byte — the member metadata is byte-identical.
_TAR_MEMBER_ROOT = "load_harness"
_TAR_MEMBER_MTIME = 0
_TAR_MEMBER_MODE = 0o644
_TAR_MEMBER_UID = 0
_TAR_MEMBER_GID = 0

# Package-relative directory segments a generated file may nest under (all live-source classes — no
# excluded bin/ / __pycache__ / test_* here, since Property 2 is about mutating a CAPTURED member).
_DIR_SEGMENTS = ("", "strata", "sub/deep")
_NAME_ALPHABET = string.ascii_lowercase + string.digits


def _random_relpath(rng: random.Random, used: set[str]) -> str:
    """A unique package-relative POSIX path for one generated source file.

    Draws a directory segment and a short stem, retrying until the composed relpath is unique within
    the set so no two generated files collide on a path (the manifest forbids a duplicate relpath).
    All names are plain ``.py`` modules (no ``test_`` prefix) so every generated file is a captured
    live-source member eligible to be mutated.
    """
    while True:
        segment = rng.choice(_DIR_SEGMENTS)
        stem = "".join(rng.choice(_NAME_ALPHABET) for _ in range(rng.randint(1, 6)))
        name = f"{stem}.py"
        relpath = f"{segment}/{name}" if segment else name
        if relpath not in used:
            used.add(relpath)
            return relpath


def _write_random_source(rng: random.Random, package_root: Path) -> list[str]:
    """Materialize a fresh random source tree under ``package_root`` (a tmp dir).

    Writes 1-4 tiny files at random package-relative paths with random content bytes, GUARANTEEING
    at least one file has >= 1 content byte so a byte can always be flipped. Returns the sorted
    POSIX relpaths of the files whose content is non-empty (the mutation candidates).
    """
    used: set[str] = set()
    count = rng.randint(_MIN_FILES, _MAX_FILES)
    non_empty: list[str] = []
    for _ in range(count):
        relpath = _random_relpath(rng, used)
        target = package_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        length = rng.randint(0, _MAX_CONTENT_BYTES)
        content = bytes(rng.randint(0, 255) for _ in range(length))
        target.write_bytes(content)
        if length > 0:
            non_empty.append(relpath)

    # Guarantee at least one non-empty member exists to mutate: if every drawn file was empty,
    # force one file to carry a single byte.
    if not non_empty:
        victim = sorted(used)[0]
        (package_root / victim).write_bytes(bytes([rng.randint(1, 255)]))
        non_empty.append(victim)

    return sorted(non_empty)


def _read_members(archive_path: Path) -> list[tuple[str, bytes]]:
    """Read every regular tar member as ``(member_name, content_bytes)`` in archive order.

    The member name is the FULL stored name (``load_harness/<relpath>``); the content is the raw
    extracted bytes. Preserving archive order lets the rewrite reproduce the original member
    sequence so only the one flipped byte differs from the frozen archive.
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
    return members


def _write_normalized_members(
    members: list[tuple[str, bytes]], archive_path: Path
) -> None:
    """Rewrite ``harness.tar`` from ``(name, content)`` pairs using the production member format.

    Reproduces ``release._write_normalized_tar`` byte-for-byte: ``USTAR_FORMAT``, each member a
    ``REGTYPE`` with fixed mtime/mode/uid/gid and empty owner names, size taken from the content,
    members in the given (original) order. Because the only input that changed is one content byte,
    the rewritten archive differs from the frozen one solely in that byte and its member's digest.
    """
    with tarfile.open(archive_path, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, content in members:
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


def _flip_one_content_byte(
    rng: random.Random, members: list[tuple[str, bytes]], target_name: str
) -> list[tuple[str, bytes]]:
    """Return a copy of ``members`` with exactly ONE byte of ``target_name``'s content flipped.

    Picks a random byte index of the target member's content and XORs a single random bit, so the
    member's length and the overall member SET are unchanged — only its content digest changes. The
    target member is guaranteed non-empty by the caller.
    """
    mutated: list[tuple[str, bytes]] = []
    for name, content in members:
        if name == target_name:
            body = bytearray(content)
            index = rng.randrange(len(body))
            body[index] ^= 1 << rng.randrange(8)
            mutated.append((name, bytes(body)))
        else:
            mutated.append((name, content))
    return mutated


def test_property2_any_single_byte_mutation_is_detected(tmp_path: Path) -> None:
    """A single flipped content byte in any one frozen member is reported as a Mutated_Copy.

    Each of the >= 10,000 iterations: generates a tiny random source set (>= 1 non-empty file),
    materializes it under ``tmp_path``, performs a REAL :func:`freeze`, verifies it ``intact``
    (sanity — the untouched release must verify), then rewrites ``harness.tar`` with exactly one
    content byte of a randomly chosen non-empty member flipped (MANIFEST.json untouched) and
    asserts :func:`verify` reports:

    * ``is_mutated_copy`` is ``True`` and ``intact`` is ``False`` (Req 4.3);
    * the mutated relpath (package-relative, root prefix stripped) is in ``mutated_files``;
    * the mutated relpath is in NEITHER ``missing_files`` NOR ``extra_files`` (the member set is
      unchanged — only one content byte differs, Req 4.1 recompute-and-compare).
    """
    seed = 0x2_02B7
    rng = random.Random(seed)

    for i in range(_ITERATIONS):
        src = tmp_path / "src" / f"i{i}" / "load_harness"
        src.mkdir(parents=True, exist_ok=True)
        non_empty = _write_random_source(rng, src)

        dest = tmp_path / "dest" / f"i{i}"
        frozen: FrozenRelease = freeze(src, _VERSION, dest)

        # Sanity: the untouched release verifies intact, so the mutation (not a freeze bug) is what
        # the assertions below detect.
        intact_result = verify(frozen)
        assert intact_result.intact, (
            f"seed={seed:#x} iter={i}: freshly frozen untouched release was not intact: "
            f"mutated={intact_result.mutated_files} missing={intact_result.missing_files} "
            f"extra={intact_result.extra_files}"
        )
        assert not intact_result.is_mutated_copy, (
            f"seed={seed:#x} iter={i}: freshly frozen untouched release reported is_mutated_copy"
        )

        # Mutate exactly one content byte of one non-empty member, re-packing the tar with the SAME
        # normalized member metadata so ONLY the content (and thus its digest) differs.
        target_rel = rng.choice(non_empty)
        target_name = f"{_TAR_MEMBER_ROOT}/{target_rel}"
        members = _read_members(frozen.archive_path)
        mutated_members = _flip_one_content_byte(rng, members, target_name)
        _write_normalized_members(mutated_members, frozen.archive_path)

        result = verify(frozen)

        assert result.is_mutated_copy, (
            f"seed={seed:#x} iter={i}: a single flipped content byte in {target_rel!r} was NOT "
            f"reported as a Mutated_Copy (intact={result.intact})"
        )
        assert not result.intact, (
            f"seed={seed:#x} iter={i}: verify reported intact=True after a single-byte mutation "
            f"of {target_rel!r}"
        )
        assert target_rel in result.mutated_files, (
            f"seed={seed:#x} iter={i}: mutated file {target_rel!r} not in mutated_files="
            f"{result.mutated_files}"
        )
        assert target_rel not in result.missing_files, (
            f"seed={seed:#x} iter={i}: content mutation of {target_rel!r} wrongly reported as "
            f"missing (missing_files={result.missing_files})"
        )
        assert target_rel not in result.extra_files, (
            f"seed={seed:#x} iter={i}: content mutation of {target_rel!r} wrongly reported as "
            f"extra (extra_files={result.extra_files})"
        )
        # Only the one member's content changed: the member SET is unchanged, so there are no
        # missing or extra members at all.
        assert not result.missing_files and not result.extra_files, (
            f"seed={seed:#x} iter={i}: a content-only mutation produced missing/extra members "
            f"(missing={result.missing_files} extra={result.extra_files})"
        )

    assert _ITERATIONS >= 10_000
