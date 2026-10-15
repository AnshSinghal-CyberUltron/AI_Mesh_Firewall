"""Property 4 for the harness freeze/release mechanism (B7 / R2-20, partial), task 2.4.

Covers the pure, deterministic, offline aggregation + refuse-to-overwrite core of
``gateway_v2.load_harness.release`` (``IntegrityManifest.compute_aggregate`` /
``file_content_sha256`` / ``freeze`` / ``FrozenRelease`` / ``ReleaseExists``) stamped under the
single :data:`~load_harness.version.HARNESS_VERSION` authority — no server, no event loop.

* **Property 4 — differing source yields a differing aggregate digest** (task 2.4): for ANY two
  source file sets that differ in at least one file's CONTENT (a flipped byte) or in the file SET
  (a file added / a file removed), freezing each under the SAME ``Version`` produces DIFFERENT
  ``aggregate_sha256``. The bulk of the sweep exercises the aggregate directly through
  :meth:`IntegrityManifest.compute_aggregate` over :class:`ManifestEntry` tuples built from the two
  sets (fast, pure); a representative subset does a REAL :func:`freeze` into two ``tmp_path`` dests
  and compares the manifests the archives produced, proving the end-to-end freeze path agrees with
  the pure aggregation rule.

* **Refuse-to-overwrite-a-differing-release (Req 2.5)**: representative example assertions —
  freezing source A into ``bin/<version>/`` then freezing a DIFFERING source B under the SAME
  version into the SAME dest raises :class:`ReleaseExists` (never overwrites); re-freezing the
  IDENTICAL source is an idempotent no-op (same :class:`FrozenRelease`, no raise, bytes unchanged).

House idiom: a seeded ``random.Random`` loop of >= 10,000 iterations with the seed logged in each
assertion message; NO ``hypothesis``. The whole mechanism is a pure function of file content + the
canonical member set keyed by the injected ``random.Random``, so it is deterministic and
offline-reproducible. All real freezes write under pytest ``tmp_path`` only — nothing is ever frozen
into the live tree. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

import pytest

from load_harness.release import (
    FrozenRelease,
    IntegrityManifest,
    ManifestEntry,
    ReleaseExists,
    freeze,
)
from load_harness.version import HARNESS_VERSION, Version

# >= 10,000 iterations driven through the pure compute_aggregate path (cheap: no filesystem). A
# small deterministic subset additionally does a real freeze into tmp_path to prove the end-to-end
# archive/manifest path agrees with the pure aggregation rule.
_ITERATIONS = 12_000

# How often an iteration also performs a REAL freeze into two tmp_path dests. Kept to a modest
# representative subset so the sweep stays fast while still exercising the full freeze path many
# times across the run.
_REAL_FREEZE_EVERY = 400

# The single authority every freeze in this test is stamped under (Property 4 fixes the Version and
# varies only the source, so a digest difference can only come from the source).
_VERSION: Version = HARNESS_VERSION

# A tiny alphabet of module-ish relpaths the random source sets draw from. The exact names do not
# matter to the aggregation rule; a small pool guarantees frequent set overlap so "same set,
# different content" and "different set" are both exercised.
_RELPATH_POOL = (
    "config.py",
    "arrivals.py",
    "report.py",
    "driver.py",
    "dryrun.py",
    "ladder.py",
    "c4.py",
    "request_plan.py",
    "README.md",
    "strata/keys.py",
    "strata/prompts.py",
    "strata/retry.py",
)

_MIN_FILES = 1
_MAX_FILES = 8
_MIN_BODY = 0
_MAX_BODY = 48


def _random_body(rng: random.Random) -> bytes:
    """A random file body (possibly empty) for one source file."""
    length = rng.randint(_MIN_BODY, _MAX_BODY)
    return bytes(rng.randrange(256) for _ in range(length))


def _random_source(rng: random.Random) -> dict[str, bytes]:
    """A random source file set: a mapping of POSIX relpath -> content bytes.

    Draws a random subset of the relpath pool (never empty) and a random body for each, so two
    independent draws vary in both the file SET and per-file CONTENT.
    """
    count = rng.randint(_MIN_FILES, _MAX_FILES)
    chosen = rng.sample(_RELPATH_POOL, count)
    return {relpath: _random_body(rng) for relpath in chosen}


def _mutate_source(rng: random.Random, source: dict[str, bytes]) -> dict[str, bytes]:
    """A copy of ``source`` guaranteed to DIFFER by content or by file set.

    Picks one of three mutations, each of which provably changes the frozen content:

    * **flip** — flip one byte of one existing file's content (or, for an empty file, append a
      byte), so that file's content digest changes;
    * **add** — add a relpath not already present (if the set is not already the whole pool), so
      the file set grows;
    * **remove** — drop one existing file (only when more than one remains), so the file set
      shrinks.

    The chosen mutation is constrained to one that is actually applicable to this source, so the
    returned mapping is never accidentally identical to the input.
    """
    mutated = dict(source)
    options: list[str] = ["flip"]
    absent = [relpath for relpath in _RELPATH_POOL if relpath not in mutated]
    if absent:
        options.append("add")
    if len(mutated) > 1:
        options.append("remove")
    choice = rng.choice(options)

    if choice == "add":
        relpath = rng.choice(absent)
        mutated[relpath] = _random_body(rng)
        return mutated
    if choice == "remove":
        victim = rng.choice(sorted(mutated))
        del mutated[victim]
        return mutated

    # "flip": change exactly one byte of one file's content (append a byte to an empty file).
    target = rng.choice(sorted(mutated))
    body = bytearray(mutated[target])
    if body:
        index = rng.randrange(len(body))
        body[index] ^= 1 << rng.randrange(8)
    else:
        body.append(rng.randrange(1, 256))
    mutated[target] = bytes(body)
    return mutated


def _entries_for(source: dict[str, bytes]) -> tuple[ManifestEntry, ...]:
    """Build the canonical sorted :class:`ManifestEntry` tuple for an in-memory source set.

    Digests each file's CONTENT bytes with the same SHA-256 rule the production
    :func:`~load_harness.release.file_content_sha256` applies to a file on disk, so the entries feed
    :meth:`IntegrityManifest.compute_aggregate` exactly as a freeze would. Entries are sorted by
    relpath to satisfy the manifest's canonical-order invariant.
    """
    entries = tuple(
        sorted(
            (
                ManifestEntry(
                    relpath=relpath,
                    sha256=hashlib.sha256(body).hexdigest(),
                )
                for relpath, body in source.items()
            ),
            key=lambda entry: entry.relpath,
        )
    )
    return entries


def _aggregate_for(source: dict[str, bytes]) -> str:
    """The aggregate digest for an in-memory source set via the single production rule."""
    return IntegrityManifest.compute_aggregate(_entries_for(source))


def _materialize(source: dict[str, bytes], package_root: Path) -> None:
    """Write an in-memory source set onto disk under ``package_root`` for a real freeze."""
    for relpath, body in source.items():
        target = package_root / Path(relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)


# --------------------------------------------------------------------------- #
# Task 2.4 — Property 4: differing source yields a differing aggregate digest
# Feature: harness-release-hygiene-r2-20, Property 4: differing source yields a differing aggregate
#   digest
# Validates: Requirements 3.2, 2.5
# --------------------------------------------------------------------------- #


def test_property4_differing_source_yields_differing_aggregate_digest(tmp_path: Path) -> None:
    """For any two source sets that differ, freezing each under the same Version differs (Req 3.2).

    The bulk of the >= 10,000 iterations exercises :meth:`IntegrityManifest.compute_aggregate` over
    entries built from a random base source and a guaranteed mutation of it (flip a byte / add a
    file / remove a file): the two aggregates MUST differ because the source content or file set
    differs. A representative subset additionally performs a REAL :func:`freeze` of each source into
    two ``tmp_path`` dests and asserts the written manifests' ``aggregate_sha256`` both match the
    pure rule AND differ from each other — proving the end-to-end archive/manifest path agrees with
    the aggregation rule and that a source difference reaches the frozen digest (never into the live
    tree; all freezes are under ``tmp_path``).

    A per-iteration negative control records whether an UNCHANGED copy produced an identical
    aggregate; the sweep asserts afterward that this held at least once, so the "differs" assertions
    cannot pass vacuously on an aggregator that returns a constant.
    """
    seed = 0x2_04DE
    rng = random.Random(seed)
    real_freezes = 0
    saw_identical_on_nochange = False

    for i in range(_ITERATIONS):
        base = _random_source(rng)
        mutated = _mutate_source(rng, base)

        base_aggregate = _aggregate_for(base)
        mutated_aggregate = _aggregate_for(mutated)

        # Property 4: a differing source (content or file set) yields a differing aggregate digest.
        assert base != mutated, (
            f"seed={seed:#x} iter={i}: mutation did not change the source — test setup bug"
        )
        assert base_aggregate != mutated_aggregate, (
            f"seed={seed:#x} iter={i}: differing sources produced the SAME aggregate digest "
            f"({base_aggregate}); base={sorted(base)} mutated={sorted(mutated)}"
        )

        # Negative control: re-aggregating the IDENTICAL (unchanged) source must be stable, so the
        # "differs" result above is a genuine consequence of the difference, not noise.
        if base_aggregate == _aggregate_for(dict(base)):
            saw_identical_on_nochange = True

        # Representative subset: prove the real freeze path agrees with the pure aggregation rule.
        if i % _REAL_FREEZE_EVERY == 0:
            real_freezes += 1
            root_a = tmp_path / f"src_a_{i}"
            root_b = tmp_path / f"src_b_{i}"
            dest_a = tmp_path / f"dest_a_{i}"
            dest_b = tmp_path / f"dest_b_{i}"
            _materialize(base, root_a)
            _materialize(mutated, root_b)

            frozen_a = freeze(root_a, _VERSION, dest_a)
            frozen_b = freeze(root_b, _VERSION, dest_b)

            manifest_a = IntegrityManifest.from_json(
                frozen_a.manifest_path.read_text(encoding="utf-8")
            )
            manifest_b = IntegrityManifest.from_json(
                frozen_b.manifest_path.read_text(encoding="utf-8")
            )

            assert manifest_a.aggregate_sha256 == base_aggregate, (
                f"seed={seed:#x} iter={i}: real-freeze aggregate for base disagreed with the "
                f"pure compute_aggregate rule"
            )
            assert manifest_b.aggregate_sha256 == mutated_aggregate, (
                f"seed={seed:#x} iter={i}: real-freeze aggregate for mutated disagreed with the "
                f"pure compute_aggregate rule"
            )
            assert manifest_a.aggregate_sha256 != manifest_b.aggregate_sha256, (
                f"seed={seed:#x} iter={i}: two REAL freezes of differing source produced the same "
                f"aggregate digest"
            )
            assert manifest_a.version == str(_VERSION) == manifest_b.version, (
                f"seed={seed:#x} iter={i}: freeze did not stamp the single authority version"
            )

    assert real_freezes > 0, (
        f"seed={seed:#x}: no real freeze ran across {_ITERATIONS} iterations — the end-to-end "
        f"path was never exercised"
    )
    assert saw_identical_on_nochange, (
        f"seed={seed:#x}: an unchanged source never reproduced its aggregate across "
        f"{_ITERATIONS} iterations — the aggregator may be non-deterministic, making the "
        f"'differs' assertions unreliable"
    )


# --------------------------------------------------------------------------- #
# Task 2.4 — refuse-to-overwrite-a-differing-release (Req 2.5), representative examples (not PBT)
# Feature: harness-release-hygiene-r2-20, Property 4: differing source yields a differing aggregate
#   digest
# Validates: Requirements 3.2, 2.5
# --------------------------------------------------------------------------- #


def test_freeze_into_existing_differing_release_raises_release_exists(tmp_path: Path) -> None:
    """Freezing a DIFFERING source under an already-taken version refuses to overwrite (Req 2.5).

    Freeze source A into ``dest/<version>/``; then materialize a differing source B into the SAME
    package root and freeze it under the SAME version into the SAME dest. Because the recomputed
    aggregate differs from the existing manifest's, :func:`freeze` fails closed with
    :class:`ReleaseExists` and leaves the original artifact bytes untouched.
    """
    package_root = tmp_path / "pkg"
    dest_root = tmp_path / "bin"
    _materialize({"config.py": b"alpha", "report.py": b"beta"}, package_root)

    frozen_a: FrozenRelease = freeze(package_root, _VERSION, dest_root)
    original_archive = frozen_a.archive_path.read_bytes()
    original_manifest = frozen_a.manifest_path.read_text(encoding="utf-8")

    # Mutate the source in place (differing content under the same version) and re-freeze.
    (package_root / "config.py").write_bytes(b"ALPHA-CHANGED")

    with pytest.raises(ReleaseExists):
        freeze(package_root, _VERSION, dest_root)

    # Fail-closed: the original frozen artifact was never overwritten.
    assert frozen_a.archive_path.read_bytes() == original_archive
    assert frozen_a.manifest_path.read_text(encoding="utf-8") == original_manifest


def test_refreeze_identical_source_is_idempotent_no_op(tmp_path: Path) -> None:
    """Re-freezing the IDENTICAL source under the same version is an idempotent no-op (Req 3.1).

    The second :func:`freeze` returns a :class:`FrozenRelease` for the same paths, raises nothing,
    and leaves the archive + manifest bytes byte-identical to the first freeze.
    """
    package_root = tmp_path / "pkg"
    dest_root = tmp_path / "bin"
    _materialize({"config.py": b"stable", "strata/keys.py": b"keys"}, package_root)

    frozen_first: FrozenRelease = freeze(package_root, _VERSION, dest_root)
    archive_bytes = frozen_first.archive_path.read_bytes()
    manifest_text = frozen_first.manifest_path.read_text(encoding="utf-8")

    frozen_second: FrozenRelease = freeze(package_root, _VERSION, dest_root)

    assert frozen_second == frozen_first
    assert frozen_second.archive_path.read_bytes() == archive_bytes
    assert frozen_second.manifest_path.read_text(encoding="utf-8") == manifest_text


def test_added_and_removed_file_change_the_aggregate(tmp_path: Path) -> None:
    """A file added to, or removed from, the source set changes the aggregate digest (Req 3.2).

    Direct example coverage of the file-set mutations the property exercises randomly: an otherwise
    identical source with one file added, and with one file removed, each produce a different
    aggregate from the baseline.
    """
    base = {"config.py": b"x", "report.py": b"y"}
    added = {**base, "driver.py": b"z"}
    removed = {"config.py": b"x"}

    base_aggregate = _aggregate_for(base)
    assert _aggregate_for(added) != base_aggregate
    assert _aggregate_for(removed) != base_aggregate
    assert _aggregate_for(added) != _aggregate_for(removed)
