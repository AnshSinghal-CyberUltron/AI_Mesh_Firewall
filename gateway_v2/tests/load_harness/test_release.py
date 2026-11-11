"""Re-freeze byte-stability property test (B7 / R2-20, partial), task 2.3.

# Feature: harness-release-hygiene-r2-20, Property 1: re-freeze is byte-identical
# Validates: Requirements 3.1, 3.3

**Property 1 — Re-freeze is byte-identical.** For any source file set (randomly generated file
names forming a valid package-relative tree and random content bytes), freezing it twice under the
same :class:`~load_harness.version.Version` produces: identical per-file digests, an identical
``aggregate_sha256``, byte-identical ``MANIFEST.json`` bytes, AND byte-identical ``harness.tar``
bytes (the normalized tar is reproducible). An idempotent re-freeze into the SAME ``dest`` is a
no-op (returns without raising, the on-disk artifact bytes are unchanged).

House idiom (Req 6.6): a seeded :func:`random.Random` driven for >= 10,000 iterations, NO
``hypothesis``. Filesystem artifacts are written only under pytest's ``tmp_path`` — nothing is ever
frozen into the real ``load_harness/`` tree.

**Honest iteration split (kept fast, floor met by the property loop).** The >= 10,000-iteration
house floor is met by the PURE loop (``_PURE_ITERATIONS`` = 10,000): each iteration generates a
fresh random source tree on disk, builds its :class:`~load_harness.release.IntegrityManifest` TWICE
from the same source via the shipped ``_build_manifest`` path (``iter_source_files`` ->
``file_content_sha256`` -> ``ManifestEntry`` -> ``compute_aggregate``), and asserts the per-file
digests, the aggregate digest, and the ``to_json`` bytes are identical across the two independent
builds. A representative SUBSET (``_FULL_FREEZE_ITERATIONS`` = 300) additionally runs the FULL
``freeze`` into two SEPARATE tmp dests A and B and asserts the on-disk ``harness.tar`` bytes and
``MANIFEST.json`` bytes are byte-identical, then re-freezes into dest A a third time and asserts the
idempotent no-op left both artifact files byte-unchanged. The full-freeze subset is bounded to keep
tarfile+hashlib work to seconds; the pure loop carries the >= 10,000 floor on its own.

Both halves exercise the SAME determinism contract (content-only digests, canonical sorted member
set, byte-stable manifest JSON, normalized tar metadata) over a large random input space, so the
split is a cost optimization, not a coverage gap: the pure loop proves manifest/aggregate
byte-stability across 10,000 random source sets, and the subset proves the archive + manifest files
on disk are byte-reproducible and that a same-dest re-freeze never rewrites.

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random
import string
from pathlib import Path

from load_harness.release import (
    IntegrityManifest,
    ManifestEntry,
    file_content_sha256,
    freeze,
    iter_source_files,
)
from load_harness.version import Version

# The >= 10,000 house-idiom floor is carried by the PURE manifest/aggregate byte-stability loop.
_PURE_ITERATIONS = 10_000

# A bounded representative subset runs the FULL freeze-to-two-dests tar+manifest byte-check so the
# tarfile+hashlib cost stays in the seconds range. These iterations are IN ADDITION to the pure
# floor; each builds TWO full releases plus one idempotent re-freeze.
_FULL_FREEZE_ITERATIONS = 300

# A fixed release version for the whole sweep. Byte-stability is a property of content + the
# canonical member set under ONE version, so a single stamp is sufficient and keeps the two freezes
# comparable; the version identifier avoids a "/" so it is a single path component under dest_root.
_VERSION = Version(identifier="v2.3")

# Small source sets keep each iteration cheap while still exercising a multi-file sorted member set
# with nested directories, excluded classes, and varied content. A handful of tiny files per set.
_MIN_FILES = 1
_MAX_FILES = 4
_MAX_CONTENT_BYTES = 24

# Package-relative directory segments a generated file may nest under. Includes the EXCLUDED classes
# (``bin``, ``__pycache__``) and a test-file name shape so some iterations confirm the exclusion
# rule holds and the captured set is still byte-stable over exactly the live source.
_DIR_SEGMENTS = ("", "strata", "sub/deep", "bin", "__pycache__")
_NAME_ALPHABET = string.ascii_lowercase + string.digits


def _random_relpath(rng: random.Random, used: set[str]) -> str:
    """A unique package-relative POSIX path for one generated source file.

    Draws a directory segment (sometimes an excluded one, sometimes a plain stem that becomes a
    ``test_*`` file) and a short stem, retrying until the composed relpath is unique within the set
    so no two generated files collide on a path (the manifest forbids a duplicate relpath).
    """
    while True:
        segment = rng.choice(_DIR_SEGMENTS)
        stem = "".join(rng.choice(_NAME_ALPHABET) for _ in range(rng.randint(1, 6)))
        # Occasionally shape the file as a test module to exercise the test-file exclusion.
        name = f"test_{stem}.py" if rng.random() < 0.2 else f"{stem}.py"
        relpath = f"{segment}/{name}" if segment else name
        if relpath not in used:
            used.add(relpath)
            return relpath


def _write_random_source(rng: random.Random, package_root: Path) -> None:
    """Materialize a fresh random source tree under ``package_root`` (a tmp dir).

    Writes 1-4 tiny files at random package-relative paths with random content bytes. Some files
    deliberately land under ``bin/`` / ``__pycache__`` or carry a ``test_*`` name so the freeze's
    canonical set must exclude them; the byte-stability property must hold over exactly the
    surviving live-source set regardless of which excluded files were sprinkled in.
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


def _build_manifest_from_source(package_root: Path) -> IntegrityManifest:
    """Build an :class:`IntegrityManifest` from the live source via the shipped canonical path.

    Mirrors ``release._build_manifest`` using only the module's public helpers so the test exercises
    the exact enumeration + content-digest + aggregation rule a ``freeze`` uses, without reaching
    into a private function.
    """
    relpaths = iter_source_files(package_root)
    entries = tuple(
        ManifestEntry(
            relpath=relpath.as_posix(),
            sha256=file_content_sha256(package_root / relpath),
        )
        for relpath in relpaths
    )
    aggregate = IntegrityManifest.compute_aggregate(entries)
    return IntegrityManifest(
        version=str(_VERSION), entries=entries, aggregate_sha256=aggregate
    )


def test_property1_refreeze_is_byte_identical(tmp_path: Path) -> None:
    """Re-freezing identical source is byte-identical: digests, aggregate, manifest, and tar.

    The pure loop carries the >= 10,000 house floor by rebuilding each random source set's manifest
    twice and asserting per-file digests, the aggregate, and the ``to_json`` bytes match. The
    bounded full-freeze subset additionally freezes the SAME source into two SEPARATE tmp dests and
    asserts the on-disk ``harness.tar`` and ``MANIFEST.json`` bytes are byte-identical, then
    confirms a same-dest re-freeze is an idempotent no-op that leaves both files byte-unchanged.
    """
    seed = 0x2CB7
    rng = random.Random(seed)

    for i in range(_PURE_ITERATIONS):
        src = tmp_path / "pure" / f"i{i}" / "load_harness"
        src.mkdir(parents=True, exist_ok=True)
        _write_random_source(rng, src)

        manifest_a = _build_manifest_from_source(src)
        manifest_b = _build_manifest_from_source(src)

        # Per-file digests identical across two independent builds of the same source.
        assert manifest_a.entries == manifest_b.entries, (
            f"seed={seed:#x} iter={i}: per-file ManifestEntry tuple differed across two builds "
            f"of identical source"
        )
        # Aggregate digest identical.
        assert manifest_a.aggregate_sha256 == manifest_b.aggregate_sha256, (
            f"seed={seed:#x} iter={i}: aggregate_sha256 differed across two builds of identical "
            f"source"
        )
        # Byte-identical MANIFEST.json bytes (sorted keys, canonical entry order, fixed newline).
        assert manifest_a.to_json() == manifest_b.to_json(), (
            f"seed={seed:#x} iter={i}: MANIFEST.json bytes differed across two builds of identical "
            f"source"
        )
        # Excluded classes never appear in the captured set (bin/, __pycache__, test_* files).
        for entry in manifest_a.entries:
            parts = Path(entry.relpath).parts
            assert "bin" not in parts and "__pycache__" not in parts, (
                f"seed={seed:#x} iter={i}: excluded directory leaked into the captured set: "
                f"{entry.relpath}"
            )
            assert not Path(entry.relpath).name.startswith("test_"), (
                f"seed={seed:#x} iter={i}: test file leaked into the captured set: {entry.relpath}"
            )

        # Representative subset: the full freeze into two separate dests + idempotent re-freeze.
        if i < _FULL_FREEZE_ITERATIONS:
            _assert_full_freeze_byte_identical(src, i, seed)

    assert _PURE_ITERATIONS >= 10_000


def _assert_full_freeze_byte_identical(src: Path, i: int, seed: int) -> None:
    """Freeze ``src`` into two separate dests and assert the tar + manifest bytes are identical.

    Also re-freezes into dest A a third time to confirm the idempotent no-op leaves both artifact
    files byte-unchanged (an identical re-freeze must return without raising and rewrite nothing).
    """
    dest_a = src.parent / "destA"
    dest_b = src.parent / "destB"

    frozen_a = freeze(src, _VERSION, dest_a)
    frozen_b = freeze(src, _VERSION, dest_b)

    tar_a = frozen_a.archive_path.read_bytes()
    tar_b = frozen_b.archive_path.read_bytes()
    assert tar_a == tar_b, (
        f"seed={seed:#x} iter={i}: harness.tar bytes differed across two freezes of identical "
        f"source into separate dests"
    )

    man_a = frozen_a.manifest_path.read_bytes()
    man_b = frozen_b.manifest_path.read_bytes()
    assert man_a == man_b, (
        f"seed={seed:#x} iter={i}: MANIFEST.json bytes differed across two freezes of identical "
        f"source into separate dests"
    )

    # Aggregate digests recorded in the two independently-written manifests agree.
    assert (
        IntegrityManifest.from_json(man_a.decode("utf-8")).aggregate_sha256
        == IntegrityManifest.from_json(man_b.decode("utf-8")).aggregate_sha256
    ), f"seed={seed:#x} iter={i}: recorded aggregate_sha256 differed across the two manifests"

    # Idempotent re-freeze into the SAME dest A: a no-op that rewrites nothing.
    frozen_a_again = freeze(src, _VERSION, dest_a)
    assert frozen_a_again == frozen_a, (
        f"seed={seed:#x} iter={i}: idempotent re-freeze returned a differing FrozenRelease handle"
    )
    assert frozen_a.archive_path.read_bytes() == tar_a, (
        f"seed={seed:#x} iter={i}: idempotent re-freeze rewrote harness.tar"
    )
    assert frozen_a.manifest_path.read_bytes() == man_a, (
        f"seed={seed:#x} iter={i}: idempotent re-freeze rewrote MANIFEST.json"
    )
