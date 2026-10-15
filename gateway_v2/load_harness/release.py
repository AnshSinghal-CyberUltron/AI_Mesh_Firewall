"""Freeze / manifest / verify value types + source enumeration (B7 / R2-20, partial).

R2-20 requires a capacity measurement to be attributable to a *known, unmodified* harness release:
"editing shared harness scripts in place killed a running measurement." The fix is an immutable,
named, versioned frozen copy of the ``load_harness/`` source (``bin/<version>/harness.tar`` plus a
``bin/<version>/MANIFEST.json`` integrity manifest) whose byte-stability and in-place-mutation
detection are first-class, verifiable properties.

This module lands the freeze/manifest core (tasks 2.1/2.2) plus the integrity guard (task 3.1):
the pure value types, the canonical source-file enumeration, :func:`freeze`, and now
:class:`IntegrityResult` / :func:`verify` / :class:`MutatedCopyError`. The
``python -m load_harness.release`` CLI (task 4.1) APPENDS to this module next; each piece is
gate-checkable before the next builds on it.

The integrity guard is the run-time safety the whole card exists for (Req 4). :func:`verify` reads
a :class:`FrozenRelease`'s ``harness.tar`` + its ``MANIFEST.json``, recomputes each tar member's
CONTENT digest through the SAME rule :func:`freeze` used, and classifies the frozen copy as intact
or a Mutated_Copy. It is PURE over the artifact bytes — offline, server-free (Req 6.5) — and
FAILS CLOSED (Req 4.5): a missing archive, a missing/unreadable/invalid ``MANIFEST.json``, or an
unreadable tar never returns ``intact=True``; it returns an :class:`IntegrityResult` that is
``is_mutated_copy`` so a caller's refuse path triggers. :class:`MutatedCopyError` is the exception
the run-time refuse posture raises (wired into ``dryrun`` by task 7.2; defined here).

Determinism is the whole point (Req 3). A :class:`ManifestEntry` digests a file's CONTENT bytes
(never its mtime/mode/owner), :meth:`IntegrityManifest.to_json` is byte-stable (sorted JSON keys,
entries pre-sorted by POSIX-relative path, a fixed trailing newline), and the aggregate digest is a
hash over the canonical concatenation of ``(relpath, per-file sha256)`` pairs. Two freezes of
identical source therefore produce byte-identical per-file digests, an identical aggregate digest,
and a byte-identical manifest (Req 3.1, 3.3); any source difference changes the aggregate (Req 3.2).

:func:`iter_source_files` returns the canonical, SORTED, POSIX-relative set of the LIVE B6
``load_harness/`` source files, EXCLUDING ``__pycache__``, the ``bin/`` artifact tree itself, and
any test file. Excluding ``bin/`` is what makes recursive self-freezing impossible by construction
(a re-freeze reads the live source, never a prior frozen copy, Req 2, 3.3).

House posture (Req 6). Every value type is ``@dataclass(frozen=True, slots=True)``
(``check_frozen_dataclasses``); there is NO module-level mutable — the source-file set is built
inside :func:`iter_source_files`, not held as a module global (``check_no_module_mutable``). A
version string and a SHA-256 digest are release-identity / content-integrity values, not a host
CPU/RAM/FD/RPS budget, so the module holds no capacity-position literal
(``check_capacity_literals``). It is stdlib-only (``hashlib``, ``pathlib``, ``json``,
``dataclasses``), introduces no HTTP type, is pure/offline/server-free, and stays well under the
800-line / 120-line gate bounds (``check_sizes``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from load_harness.version import HARNESS_VERSION, Version

__all__ = (
    "FrozenRelease",
    "IntegrityManifest",
    "IntegrityResult",
    "ManifestEntry",
    "MutatedCopyError",
    "ReleaseExists",
    "bytes_content_sha256",
    "file_content_sha256",
    "freeze",
    "iter_source_files",
    "main",
    "verify",
)

#: The artifact-tree directory name under ``load_harness/`` that holds frozen releases
#: (``bin/<version>/``). Excluded from :func:`iter_source_files` so a freeze never captures a prior
#: Frozen_Release (no recursive self-freeze, Req 2, 3.3). This is a path-name constant, not a
#: capacity-position literal.
_ARTIFACT_DIR_NAME = "bin"

#: The bytecode-cache directory name excluded from the canonical source set — compiled artifacts,
#: never live source.
_PYCACHE_DIR_NAME = "__pycache__"

#: The file-name markers that identify a test module, excluded from the canonical source set so a
#: freeze captures only the shipped harness source (tests are not part of the released instrument).
_TEST_PREFIX = "test_"
_TEST_SUFFIX = "_test.py"

#: The normalized-tar archive name inside a ``bin/<version>/`` release.
_ARCHIVE_NAME = "harness.tar"

#: The sibling integrity-manifest name inside a ``bin/<version>/`` release.
_MANIFEST_NAME = "MANIFEST.json"

#: The stable POSIX root every tar member is stored under so the archive bytes depend only on
#: content + the canonical member set, never on where the freeze ran from. A member for the
#: package-relative ``strata/foo.py`` is stored as ``load_harness/strata/foo.py``.
_TAR_MEMBER_ROOT = "load_harness"

#: Fully normalized tar member metadata (Req 3). Fixed epoch mtime, a fixed regular-file mode, and
#: root-owned (uid/gid 0) with empty owner names, so two freezes of identical content on different
#: filesystems / at different times produce byte-identical archives. These are determinism
#: constants, not host-capacity budgets.
_TAR_MEMBER_MTIME = 0
_TAR_MEMBER_MODE = 0o644
_TAR_MEMBER_UID = 0
_TAR_MEMBER_GID = 0


def bytes_content_sha256(data: bytes) -> str:
    """The hex SHA-256 of CONTENT bytes — the ONE digest rule freeze and verify share (Req 4.1).

    Both the on-disk freeze path (:func:`file_content_sha256`) and the in-memory verify path (a tar
    member's extracted bytes) route through this one function, so a frozen file and its tar member
    produce the SAME digest for identical content and :func:`verify` can compare a recomputed member
    digest directly against its :class:`ManifestEntry`.
    """
    return hashlib.sha256(data).hexdigest()


def file_content_sha256(path: Path) -> str:
    """The hex SHA-256 of a file's CONTENT bytes — never its metadata (Req 2.3, 3.1).

    The digest is taken over the file's raw bytes only; mtime, mode, and ownership are deliberately
    not read, because byte-stability must depend on content alone (two freezes of identical content
    on different filesystems must agree, Req 3). Read in bounded chunks so an arbitrarily large file
    never has to be held in memory at once, then folded into the SAME :func:`bytes_content_sha256`
    rule verify uses.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_source_files(package_root: Path) -> tuple[Path, ...]:
    """The canonical, SORTED, POSIX-relative live ``load_harness/`` source set (Req 2, 3.3).

    ``package_root`` is the ``load_harness/`` package directory. The returned paths are relative to
    ``package_root`` with POSIX ("/") separators, in deterministic sorted order — this is EXACTLY
    the set a freeze captures and the manifest lists, so two freezes of identical source enumerate
    the same canonical set in the same order (Req 3.3).

    Three things are excluded so the set is only live source:

    - ``__pycache__`` (compiled bytecode, never source);
    - the ``bin/`` artifact tree itself (a prior Frozen_Release — excluding it is what makes
      recursive self-freezing impossible by construction, Req 2, 3.3);
    - any test file (``test_*`` or ``*_test.py``) — the released instrument is the harness source,
      not its tests.

    README.md and non-``.py`` package data are intentionally INCLUDED (the harness identifies itself
    in ``README.md``, which the release derives from the single version stamp): the enumeration
    captures every live file under the package except the three excluded classes.
    """
    relpaths: list[str] = []
    for candidate in package_root.rglob("*"):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(package_root)
        if _is_excluded(relative):
            continue
        relpaths.append(relative.as_posix())
    return tuple(Path(relpath) for relpath in sorted(relpaths))


def _is_excluded(relative: Path) -> bool:
    """Whether a package-relative path is outside the canonical source set.

    Excludes anything under the ``bin/`` artifact tree or a ``__pycache__`` directory (checked over
    every path part so a nested occurrence is caught), and any test file by its name. Factored out
    of :func:`iter_source_files` so the exclusion rule is one named predicate rather than inline
    logic.
    """
    parts = relative.parts
    if _ARTIFACT_DIR_NAME in parts or _PYCACHE_DIR_NAME in parts:
        return True
    name = relative.name
    return name.startswith(_TEST_PREFIX) or name.endswith(_TEST_SUFFIX)


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One frozen file's content digest — a ``(relpath, sha256)`` pair (Req 2.3).

    ``relpath`` is the POSIX-relative path under ``load_harness/`` (the canonical key the manifest
    sorts by); ``sha256`` is the hex digest of the file's CONTENT bytes (never metadata). Frozen +
    slots so a manifest entry is an immutable value (Req 6.2).
    """

    relpath: str
    sha256: str


@dataclass(frozen=True, slots=True)
class IntegrityManifest:
    """Each file's content digest plus the aggregate — the freeze-time record (Req 2.3).

    - ``version`` — the :class:`~load_harness.version.Version` identifier this release was frozen
      as, recorded INSIDE the manifest so the frozen copy reports the version it was released as
      (Req 2.4).
    - ``entries`` — the per-file :class:`ManifestEntry` tuple, kept SORTED by ``relpath`` so the
      manifest is in canonical order and two freezes of identical source produce byte-identical
      manifests (Req 3.3).
    - ``aggregate_sha256`` — the digest over the canonical concatenation of every ``(relpath,
      sha256)`` pair; any single-file content change (or any add/remove) changes it (Req 3.2).

    :meth:`to_json` is byte-stable (sorted JSON keys, pre-sorted entries, a fixed trailing newline);
    :meth:`from_json` is its inverse. :meth:`compute_aggregate` derives the aggregate digest from a
    set of entries so a freeze and a verify agree on exactly one aggregation rule.
    """

    version: str
    entries: tuple[ManifestEntry, ...]
    aggregate_sha256: str

    def __post_init__(self) -> None:
        """Enforce the canonical entry order so a constructed manifest is already byte-stable.

        Entries MUST be sorted by ``relpath`` with no duplicate path; a manifest built out of order
        would serialize to different bytes than one built in order for the same content, breaking
        the byte-identical-re-freeze guarantee (Req 3.3). Rejecting it at construction keeps the
        invariant local to the value rather than relying on every caller to pre-sort.
        """
        relpaths = [entry.relpath for entry in self.entries]
        if relpaths != sorted(relpaths):
            raise ValueError("IntegrityManifest entries must be sorted by relpath")
        if len(set(relpaths)) != len(relpaths):
            raise ValueError("IntegrityManifest entries must not repeat a relpath")

    @staticmethod
    def compute_aggregate(entries: tuple[ManifestEntry, ...]) -> str:
        """The aggregate digest over the canonical concatenation of ``(relpath, sha256)`` pairs.

        Entries are read in SORTED ``relpath`` order and each contributes its path and per-file
        digest to one running SHA-256, with a separator byte between fields so no two distinct
        ``(relpath, sha256)`` layouts can collide. This is the single aggregation rule both
        ``freeze`` and ``verify`` use, so they always agree on the aggregate for the same content
        (Req 3.1, 3.2).
        """
        digest = hashlib.sha256()
        for entry in sorted(entries, key=lambda item: item.relpath):
            digest.update(entry.relpath.encode("utf-8"))
            digest.update(b"\0")
            digest.update(entry.sha256.encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()

    def to_json(self) -> str:
        """Byte-stable JSON for the ``MANIFEST.json`` artifact (Req 3.3).

        Sorted JSON keys, entries serialized in their canonical ``relpath`` order, and a FIXED
        single trailing newline, so the same manifest value always produces the same bytes (and two
        freezes
        of identical source produce byte-identical manifests). ``ensure_ascii=False`` keeps the
        bytes the natural UTF-8 encoding; a relpath/digest never contains a non-ASCII character in
        practice, but the setting makes the encoding explicit rather than implicit.
        """
        body = {
            "version": self.version,
            "aggregate_sha256": self.aggregate_sha256,
            "entries": [
                {"relpath": entry.relpath, "sha256": entry.sha256} for entry in self.entries
            ],
        }
        return (
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        )

    @classmethod
    def from_json(cls, text: str) -> IntegrityManifest:
        """Reconstruct an :class:`IntegrityManifest` from :meth:`to_json` bytes (the inverse).

        Entries are re-sorted by ``relpath`` on the way in so a manifest read from disk is in the
        same canonical order it was written, and ``to_json(from_json(text)) == text`` holds for any
        text this class produced. A malformed structure raises (fail-closed) rather than yielding a
        half-formed manifest a verify could mistake for intact.
        """
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("manifest JSON must be an object")
        version = parsed["version"]
        aggregate = parsed["aggregate_sha256"]
        raw_entries = parsed["entries"]
        if not isinstance(version, str) or not isinstance(aggregate, str):
            raise ValueError("manifest version and aggregate_sha256 must be strings")
        if not isinstance(raw_entries, list):
            raise ValueError("manifest entries must be a list")
        entries = tuple(
            sorted(
                (_entry_from_mapping(raw) for raw in raw_entries),
                key=lambda item: item.relpath,
            )
        )
        return cls(version=version, entries=entries, aggregate_sha256=aggregate)


def _entry_from_mapping(raw: object) -> ManifestEntry:
    """Build one :class:`ManifestEntry` from a parsed JSON object, failing closed on a bad shape.

    Factored out of :meth:`IntegrityManifest.from_json` so each entry's structural validation is a
    single named step; a non-object entry or a non-string field raises rather than producing a
    half-formed entry.
    """
    if not isinstance(raw, dict):
        raise ValueError("manifest entry must be an object")
    relpath = raw["relpath"]
    sha256 = raw["sha256"]
    if not isinstance(relpath, str) or not isinstance(sha256, str):
        raise ValueError("manifest entry relpath and sha256 must be strings")
    return ManifestEntry(relpath=relpath, sha256=sha256)


@dataclass(frozen=True, slots=True)
class FrozenRelease:
    """The ``bin/<version>/`` artifact handle — the archive + manifest path pair (Req 2.2).

    ``version`` is the release's :class:`~load_harness.version.Version` identifier; ``archive_path``
    is the normalized ``bin/<version>/harness.tar`` and ``manifest_path`` the sibling
    ``bin/<version>/MANIFEST.json``. A :class:`FrozenRelease` is only a handle to the on-disk
    artifact — ``freeze`` (task 2.2) produces one and ``verify`` (task 3.1) checks it. Frozen +
    slots so the handle is an immutable value (Req 6.2).
    """

    version: str
    archive_path: Path
    manifest_path: Path


def _version_identifier(version: Version) -> str:
    """The identifier string a release records for a :class:`Version` (Req 2.4).

    A thin helper so the ``freeze`` slice (task 2.2) and the manifest both stamp the version through
    one place and never restate the identifier logic. Kept here with the types this slice owns.
    """
    return str(version)


class ReleaseExists(Exception):
    """Raised when :func:`freeze` is asked to overwrite a DIFFERING existing release (Req 2.5).

    The house posture is fail-closed: a ``bin/<version>/`` whose frozen aggregate digest differs
    from the source being frozen is NEVER overwritten (that would silently replace the artifact a
    prior measurement was attributed to). An IDENTICAL re-freeze is not an error — it is an
    idempotent no-op (Req 3.1) and never raises; only a genuine content divergence under an
    already-taken version does.
    """


def _build_manifest(
    package_root: Path, version: Version
) -> tuple[IntegrityManifest, tuple[Path, ...]]:
    """Enumerate the canonical source and build its :class:`IntegrityManifest` (Req 2.3, 2.4).

    Returns the manifest (version stamped INSIDE it, entries sorted by relpath, aggregate derived
    through the one :meth:`IntegrityManifest.compute_aggregate` rule) alongside the canonical sorted
    relpath tuple ``freeze`` writes into the archive — computed once so the manifest and the archive
    always describe the exact same member set.
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
    manifest = IntegrityManifest(
        version=_version_identifier(version),
        entries=entries,
        aggregate_sha256=aggregate,
    )
    return manifest, relpaths


def _write_normalized_tar(
    package_root: Path, relpaths: tuple[Path, ...], archive_path: Path
) -> None:
    """Write ONE fully normalized tar whose bytes depend only on content + member set (Req 3).

    Members are written in the canonical SORTED relpath order under a stable POSIX root
    (``load_harness/<relpath>``), each with an explicit :class:`tarfile.TarInfo` whose
    mtime/mode/uid/gid/uname/gname are set to fixed values rather than captured from the filesystem.
    ``tar.add`` is deliberately NOT used — its default ``TarInfo`` records the real mtime, mode, and
    owner of each file, which would make the archive bytes depend on walk-time metadata and break
    byte-stability. The member size and payload come from the file's content bytes alone, read in
    the same bounded fashion the digest uses.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for relpath in relpaths:
            source = package_root / relpath
            info = tarfile.TarInfo(name=f"{_TAR_MEMBER_ROOT}/{relpath.as_posix()}")
            info.size = source.stat().st_size
            info.mtime = _TAR_MEMBER_MTIME
            info.mode = _TAR_MEMBER_MODE
            info.type = tarfile.REGTYPE
            info.uid = _TAR_MEMBER_UID
            info.gid = _TAR_MEMBER_GID
            info.uname = ""
            info.gname = ""
            with source.open("rb") as handle:
                tar.addfile(info, handle)


def freeze(package_root: Path, version: Version, dest_root: Path) -> FrozenRelease:
    """Capture the live ``load_harness/`` source as an immutable ``bin/<version>/`` release (Req 2).

    ``package_root`` is the ``load_harness/`` package directory whose canonical source set
    (:func:`iter_source_files`) is frozen; ``version`` is the :class:`~load_harness.version.Version`
    the release is stamped as; ``dest_root`` is the artifact root under which ``<version>/`` is
    created (``bin/`` in production). Produces a normalized ``<version>/harness.tar`` plus a sibling
    ``<version>/MANIFEST.json`` and returns the :class:`FrozenRelease` handle to the pair.

    The archive is NORMALIZED — fixed mtime/mode/root-ownership, members in sorted relpath order
    under the stable ``load_harness/`` root — so its bytes depend only on file content and the
    canonical member set (Req 3.1, 3.3); the manifest stamps the version INSIDE it and digests
    content, never metadata (Req 2.3, 2.4).

    **Refuse-to-overwrite-a-differing-release (Req 2.5, 3.1).** If ``<version>/`` already holds a
    ``MANIFEST.json``, the manifest for the CURRENT source is recomputed and its aggregate digest
    compared to the existing one: identical aggregate ⇒ an idempotent no-op (the existing
    :class:`FrozenRelease` is returned, nothing is rewritten); a differing aggregate ⇒
    :class:`ReleaseExists` is raised (fail-closed — the artifact is never overwritten).
    """
    release_dir = dest_root / _version_identifier(version)
    archive_path = release_dir / _ARCHIVE_NAME
    manifest_path = release_dir / _MANIFEST_NAME
    frozen = FrozenRelease(
        version=_version_identifier(version),
        archive_path=archive_path,
        manifest_path=manifest_path,
    )

    manifest, relpaths = _build_manifest(package_root, version)

    existing = _existing_aggregate(manifest_path)
    if existing is not None:
        if existing == manifest.aggregate_sha256:
            return frozen
        raise ReleaseExists(
            f"release {frozen.version!r} already exists with a differing aggregate digest; "
            "refusing to overwrite"
        )

    _write_normalized_tar(package_root, relpaths, archive_path)
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")
    return frozen


def _existing_aggregate(manifest_path: Path) -> str | None:
    """The aggregate digest of an existing release manifest, or ``None`` if there is none.

    Returns the already-frozen ``aggregate_sha256`` when ``manifest_path`` exists and parses, so
    :func:`freeze` can distinguish an idempotent re-freeze (identical aggregate) from a forbidden
    overwrite (differing aggregate). A manifest present but unreadable/invalid is treated as a
    genuine existing release that cannot be shown identical — it returns a sentinel digest that can
    never equal a freshly computed one, so :func:`freeze` fails closed with :class:`ReleaseExists`
    rather than silently overwriting a corrupt artifact.
    """
    if not manifest_path.exists():
        return None
    try:
        text = manifest_path.read_text(encoding="utf-8")
        return IntegrityManifest.from_json(text).aggregate_sha256
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return "<unreadable-existing-manifest>"


class MutatedCopyError(Exception):
    """Raised by the run-time refuse posture when a Frozen_Release is a Mutated_Copy (Req 4.5).

    R2-20's exact failure is "editing shared harness scripts in place killed a running measurement."
    The house posture is fail-closed: a run that executes from a Frozen_Release verifies it first
    and, on :attr:`IntegrityResult.is_mutated_copy`, RAISES this rather than measuring with changed
    code and reporting the result as the released version. :func:`verify` itself never raises for a
    mutated/absent/corrupt artifact (it returns a non-intact :class:`IntegrityResult`); this
    exception is what a caller — ``dryrun``'s run-time guard (task 7.2) — raises on that verdict.
    """


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    """The Integrity_Guard verdict for a Frozen_Release (Req 4.2-4.4).

    - ``intact`` — ``True`` only when every manifest file is present in the archive with a matching
      content digest and the archive holds no extra member (Req 4.2);
    - ``mutated_files`` — manifest-listed relpaths whose archived content digest DIFFERS from the
      manifest entry (Req 4.3);
    - ``missing_files`` — manifest-listed relpaths ABSENT from the archive (Req 4.4);
    - ``extra_files`` — archive members ABSENT from the manifest (Req 4.4).

    Each tuple is SORTED so the verdict is a deterministic value. :attr:`is_mutated_copy` is the one
    predicate a caller branches on: any mutated/missing/extra file means the frozen copy diverged
    from what it was frozen as. Frozen + slots so the verdict is an immutable value (Req 6.2).
    """

    intact: bool
    mutated_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    extra_files: tuple[str, ...]

    @property
    def is_mutated_copy(self) -> bool:
        """Whether the frozen copy diverged from its manifest (Req 4.3, 4.4).

        ``True`` when any file was mutated, is missing from the archive, or is an extra archive
        member not in the manifest — the single predicate a run-time refuse path keys off.
        """
        return bool(self.mutated_files or self.missing_files or self.extra_files)


#: The sentinel :class:`IntegrityResult` returned when a Frozen_Release cannot be read at all — a
#: missing archive, or a missing/unreadable/invalid ``MANIFEST.json``, or an unreadable tar. It is
#: NOT intact and reports ``is_mutated_copy`` (via a non-empty ``missing_files``) so a caller's
#: refuse path triggers; a corrupt/absent artifact is treated as non-attributable, never silently
#: "intact" (Req 4.5, design Error Handling). The relpath marker names the fail-closed reason
#: without pretending to be a real source file.
_UNREADABLE_ARTIFACT_MARKER = "<unreadable-frozen-release>"


def _unreadable_result() -> IntegrityResult:
    """The fail-closed verdict for an absent/corrupt artifact (Req 4.5).

    Returns a non-intact :class:`IntegrityResult` carrying a single sentinel ``missing_files``
    entry, so :attr:`IntegrityResult.is_mutated_copy` is ``True`` and the artifact is treated as
    non-attributable rather than silently intact. Built in a function (not held as a module global)
    so there is no module-level mutable.
    """
    return IntegrityResult(
        intact=False,
        mutated_files=(),
        missing_files=(_UNREADABLE_ARTIFACT_MARKER,),
        extra_files=(),
    )


def _archive_member_digests(archive_path: Path) -> dict[str, str]:
    """Recompute every tar member's CONTENT digest, keyed by its package-relative POSIX path.

    Each regular member is read in-memory via ``extractfile`` and hashed through the SAME
    :func:`bytes_content_sha256` rule :func:`freeze` used, so a member's digest matches the on-disk
    file digest for identical content. Member names are stored under the stable ``load_harness/``
    root (see :func:`_write_normalized_tar`); the root prefix is stripped back to the manifest's
    package-relative relpath so the two sets compare directly. Non-regular members (there are none
    in a normalized freeze) are ignored. Raises on an unreadable/invalid tar so :func:`verify` can
    fail closed.
    """
    digests: dict[str, str] = {}
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
            digests[relpath] = bytes_content_sha256(data)
    return digests


def _verify_members(
    manifest: IntegrityManifest, member_digests: dict[str, str]
) -> IntegrityResult:
    """Classify a frozen archive against its manifest (Req 4.2-4.4).

    Compares the manifest's ``(relpath -> sha256)`` map against the recomputed archive member
    digests: a manifest relpath absent from the archive is ``missing``; an archive member absent
    from the manifest is ``extra``; a shared relpath whose digests differ is ``mutated``. All match
    and no extra member ⇒ ``intact=True`` (Req 4.2). Pure over the two maps — the only I/O is done
    by :func:`verify`.
    """
    manifest_digests = {entry.relpath: entry.sha256 for entry in manifest.entries}
    manifest_relpaths = set(manifest_digests)
    archive_relpaths = set(member_digests)

    missing = tuple(sorted(manifest_relpaths - archive_relpaths))
    extra = tuple(sorted(archive_relpaths - manifest_relpaths))
    mutated = tuple(
        sorted(
            relpath
            for relpath in manifest_relpaths & archive_relpaths
            if manifest_digests[relpath] != member_digests[relpath]
        )
    )
    intact = not (missing or extra or mutated)
    return IntegrityResult(
        intact=intact,
        mutated_files=mutated,
        missing_files=missing,
        extra_files=extra,
    )


def verify(frozen: FrozenRelease) -> IntegrityResult:
    """Verify a Frozen_Release against its Integrity_Manifest (Req 4.1-4.5).

    Reads ``frozen.manifest_path`` (``MANIFEST.json``) and ``frozen.archive_path``
    (``harness.tar``), recomputes each tar member's CONTENT digest through the one
    :func:`bytes_content_sha256` rule
    :func:`freeze` used, and classifies the result via :func:`_verify_members`: all present and
    matching ⇒ ``intact=True`` (Req 4.2); a content mismatch ⇒ ``mutated_files`` (Req 4.3); a
    manifest file absent from the archive ⇒ ``missing_files`` and an archive member absent from the
    manifest ⇒ ``extra_files`` (Req 4.4).

    **Fail-closed (Req 4.5, design Error Handling).** A missing archive, a
    missing/unreadable/invalid ``MANIFEST.json``, or an unreadable/invalid tar never yields
    ``intact=True`` — :func:`verify`
    returns :func:`_unreadable_result` (a non-intact :class:`IntegrityResult` with
    ``is_mutated_copy`` ``True``) so a caller's refuse path triggers; the corrupt/absent artifact is
    treated as non-attributable, never silently intact. :func:`verify` is PURE over the artifact
    bytes — offline and server-free (Req 6.5).
    """
    if not frozen.manifest_path.exists() or not frozen.archive_path.exists():
        return _unreadable_result()
    try:
        manifest = IntegrityManifest.from_json(
            frozen.manifest_path.read_text(encoding="utf-8")
        )
        member_digests = _archive_member_digests(frozen.archive_path)
    except (OSError, ValueError, KeyError, tarfile.TarError, json.JSONDecodeError):
        return _unreadable_result()
    return _verify_members(manifest, member_digests)


# --------------------------------------------------------------------------------------------------
# Thin freeze/verify CLI — ``python -m load_harness.release`` (task 4.1, Req 2.1, 4.1, 4.5)
# --------------------------------------------------------------------------------------------------
#
# The CLI is the operator surface over the pure freeze/verify core above. It mirrors the
# ``state_control``/``audit_control``/``load_harness.__main__`` shape: a thin ``main()`` returning
# an int exit code, with ``raise SystemExit(main())`` under ``__main__`` so ``python -m
# load_harness.release`` works. There are two subcommands:
#
# - ``freeze`` — resolve the LIVE ``load_harness/`` package root (the directory holding this module)
#   and a dest root (default ``<package>/bin/``), call :func:`freeze` under
#   :data:`~load_harness.version.HARNESS_VERSION`, print the frozen version + artifact paths, and
#   return 0. A :class:`ReleaseExists` (a differing release already taken under that version) prints
#   a diagnostic to stderr and returns a non-zero code — the fail-closed refuse-to-overwrite
#   posture (Req 2.5) surfaced as an exit code.
# - ``verify`` — resolve the frozen release for a version (default :data:`HARNESS_VERSION`) under
#   the dest root, build the :class:`FrozenRelease` handle for ``<dest>/<version>/`` and call
#   :func:`verify`: intact ⇒ print OK + return 0; a Mutated_Copy ⇒ print the mutated/missing/extra
#   files to stderr and return a non-zero code (the fail-closed exit, Req 4.5).
#
# Honest typing throughout (``argparse.Namespace`` fields are read through typed locals; no ``Any``
# leaks into the public exit path), stdlib-only (``argparse``/``sys`` added), no module-level
# mutable (the parser is built inside a function), and no capacity-position literal.

#: The default artifact-root directory name under the ``load_harness/`` package root. ``freeze``
#: writes ``bin/<version>/`` and ``verify`` reads it; the smoke/tests override it with a tmp path so
#: the repo tree is never touched. A path-name constant, not a capacity-position literal.
_DEFAULT_DEST_DIR_NAME = _ARTIFACT_DIR_NAME

#: Exit codes. 0 = success/intact; 1 = a fail-closed refuse (``ReleaseExists`` on a differing
#: re-freeze, or a verified Mutated_Copy). Named so the two subcommands share one vocabulary.
_EXIT_OK = 0
_EXIT_FAIL = 1


def _package_root() -> Path:
    """The LIVE ``load_harness/`` package directory — the source a freeze captures (Req 2.1).

    Resolved from this module's own location (``Path(__file__).parent``) so the CLI always freezes
    the installed/checked-out package it is running from, never a guessed path. :func:`freeze`
    enumerates its canonical source set via :func:`iter_source_files` (which already excludes the
    ``bin/`` artifact tree, so a freeze never captures a prior release).
    """
    return Path(__file__).parent


def _default_dest_root() -> Path:
    """The default artifact root — ``<package>/bin/`` — co-located with the package (Req 2.2)."""
    return _package_root() / _DEFAULT_DEST_DIR_NAME


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``freeze`` / ``verify`` subcommand parser (built inside a function; no mutable).

    Both subcommands accept ``--dest-root`` (default ``<package>/bin/``) so the smoke and the unit
    tests can target a tmp directory and never write into the repo tree; ``verify`` additionally
    accepts ``--version`` (default :data:`HARNESS_VERSION`) to name the release to check.
    """
    parser = argparse.ArgumentParser(
        prog="python -m load_harness.release",
        description="Freeze the load_harness source to an immutable bin/<version>/ release and "
        "verify a frozen release against its integrity manifest.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    freeze_cmd = sub.add_parser(
        "freeze", help="freeze the live load_harness source to bin/<version>/."
    )
    freeze_cmd.add_argument(
        "--dest-root",
        default=None,
        help="artifact root under which <version>/ is written (default: <package>/bin/).",
    )

    verify_cmd = sub.add_parser(
        "verify", help="verify a frozen bin/<version>/ release against its manifest."
    )
    verify_cmd.add_argument(
        "--dest-root",
        default=None,
        help="artifact root under which <version>/ is read (default: <package>/bin/).",
    )
    verify_cmd.add_argument(
        "--version",
        default=str(HARNESS_VERSION),
        help="the release version identifier to verify (default: the harness version stamp).",
    )
    return parser


def _resolve_dest_root(raw: str | None) -> Path:
    """The dest root to use — the ``--dest-root`` value if given, else ``<package>/bin/``."""
    if raw is None:
        return _default_dest_root()
    return Path(raw)


def _cmd_freeze(dest_root_arg: str | None) -> int:
    """Run the ``freeze`` subcommand — capture live source to ``<dest>/<version>/`` (Req 2.1, 2.5).

    Freezes the live package root under :data:`HARNESS_VERSION`; on success prints the frozen
    version and the archive/manifest paths and returns :data:`_EXIT_OK`. A :class:`ReleaseExists`
    (an existing differing release under that version) is the fail-closed refuse-to-overwrite case:
    it prints a diagnostic to stderr and returns :data:`_EXIT_FAIL` rather than overwriting (Req
    2.5).
    """
    dest_root = _resolve_dest_root(dest_root_arg)
    try:
        frozen = freeze(_package_root(), HARNESS_VERSION, dest_root)
    except ReleaseExists as exc:
        print(f"freeze refused: {exc}", file=sys.stderr)
        return _EXIT_FAIL
    print(f"froze {frozen.version}")
    print(f"  archive:  {frozen.archive_path}")
    print(f"  manifest: {frozen.manifest_path}")
    return _EXIT_OK


def _frozen_handle(dest_root: Path, version: str) -> FrozenRelease:
    """The :class:`FrozenRelease` handle for ``<dest>/<version>/`` — archive + manifest paths.

    Mirrors the layout :func:`freeze` writes (``<version>/harness.tar`` +
    ``<version>/MANIFEST.json``) so ``verify`` reads exactly what ``freeze`` produced.
    """
    release_dir = dest_root / version
    return FrozenRelease(
        version=version,
        archive_path=release_dir / _ARCHIVE_NAME,
        manifest_path=release_dir / _MANIFEST_NAME,
    )


def _cmd_verify(dest_root_arg: str | None, version: str) -> int:
    """Run the ``verify`` subcommand — check a frozen release against its manifest (Req 4.1, 4.5).

    Builds the :class:`FrozenRelease` handle for ``<dest>/<version>/`` and calls :func:`verify`:
    intact ⇒ print OK and return :data:`_EXIT_OK`; a Mutated_Copy (any mutated/missing/extra file,
    or an absent/corrupt artifact) ⇒ print the divergence to stderr and return :data:`_EXIT_FAIL`
    — the fail-closed exit the run-time refuse posture mirrors (Req 4.5).
    """
    dest_root = _resolve_dest_root(dest_root_arg)
    frozen = _frozen_handle(dest_root, version)
    result = verify(frozen)
    if not result.is_mutated_copy:
        print(f"verify OK: {frozen.version} is intact")
        return _EXIT_OK
    print(f"verify FAILED: {frozen.version} is a mutated copy", file=sys.stderr)
    if result.mutated_files:
        print(f"  mutated: {', '.join(result.mutated_files)}", file=sys.stderr)
    if result.missing_files:
        print(f"  missing: {', '.join(result.missing_files)}", file=sys.stderr)
    if result.extra_files:
        print(f"  extra:   {', '.join(result.extra_files)}", file=sys.stderr)
    return _EXIT_FAIL


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point for ``python -m load_harness.release``. Returns the exit code.

    Parses ``argv`` (defaults to ``sys.argv[1:]``) into a ``freeze`` or ``verify`` subcommand and
    dispatches to :func:`_cmd_freeze` / :func:`_cmd_verify`. The ``--dest-root`` value is read as a
    typed ``str | None`` and ``--version`` as a ``str`` so no ``Any`` leaks into the exit path.
    Returns :data:`_EXIT_OK` on success/intact and :data:`_EXIT_FAIL` on a fail-closed refuse
    (``ReleaseExists`` or a verified Mutated_Copy).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = str(args.command)
    dest_root_arg_raw = getattr(args, "dest_root", None)
    dest_root_arg = None if dest_root_arg_raw is None else str(dest_root_arg_raw)
    if command == "freeze":
        return _cmd_freeze(dest_root_arg)
    version = str(getattr(args, "version", str(HARNESS_VERSION)))
    return _cmd_verify(dest_root_arg, version)


if __name__ == "__main__":
    raise SystemExit(main())
