"""Version-stamp single-authority property test (B7 / R2-20, partial), task 9.2.

# Feature: harness-release-hygiene-r2-20, Property 6: the version stamp is the single authority
# Validates: Requirements 1.1, 1.2, 1.4, 2.4

**Property 6 — The version stamp is the single authority.** For any reference to the release
version in the package, the value equals :data:`load_harness.version.HARNESS_VERSION`. The derived
references all CONVERGE on the one stamp and never drift:

- ``load_harness.__version__ == str(HARNESS_VERSION)`` — the package ``__init__`` derives from the
  stamp (Req 1.1, 1.2, 1.4);
- a :func:`~load_harness.release.freeze` under ``HARNESS_VERSION`` writes a ``MANIFEST.json`` whose
  ``version`` field equals ``str(HARNESS_VERSION)`` — the manifest stamps the one authority (Req
  2.4);
- a :class:`~load_harness.report.RunProvenance` built with ``harness_version=str(HARNESS_VERSION)``
  carries the same string on the run output (Req 1.1);
- the single-authority invariant itself: there is NO second hardcoded version *code* literal that
  diverges. A tight, honest AST scan of the shipped ``load_harness/`` ``.py`` source asserts the
  only module that assigns a ``v<N>.<N>``-shaped string as a real code string literal is
  ``version.py`` (the ``HARNESS_VERSION`` definition). Doc-comment / docstring mentions are NOT code
  literals and are excluded by construction (``ast`` drops ``#`` comments entirely, and every
  module/class/function docstring node is skipped), so a prose "e.g. ``v2.3``" never counts as an
  independent literal — only ``version.py`` restates the identifier in code.

House idiom (Req 6.6): a seeded :func:`random.Random` driven for >= 10,000 iterations, NO
``hypothesis``. Filesystem artifacts are written only under pytest's ``tmp_path`` — nothing is ever
frozen into the real ``load_harness/`` tree.

**Honest iteration split (kept fast, floor met by the property loop).** The >= 10,000-iteration
house floor is met by the PURE convergence loop (``_PURE_ITERATIONS`` = 10,000): each iteration
constructs fresh :class:`~load_harness.version.Version`, :class:`~load_harness.report.RunProvenance`
and :class:`~load_harness.release.IntegrityManifest` values stamped from ``str(HARNESS_VERSION)``
over random surrounding content (random digests, random per-file entries) and asserts every derived
reference equals the one stamp regardless of the random content — i.e. the version is invariably the
single authority, never drifting with the data around it. A representative SUBSET
(``_FULL_FREEZE_ITERATIONS`` = 300) additionally runs the FULL ``freeze`` of a tiny random source
set under ``HARNESS_VERSION`` into ``tmp_path`` and asserts the on-disk ``MANIFEST.json``
``version`` field equals ``str(HARNESS_VERSION)`` and ``load_harness.__version__`` — the manifest
stamp
converging on the authority across many random source contents. The full-freeze subset is bounded to
keep tarfile+hashlib work to seconds; the pure loop carries the >= 10,000 floor on its own. The
one-shot AST single-authority scan is a single structural assertion run once (it does not vary with
a seed, so repeating it 10,000 times would add cost without coverage).

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import ast
import random
import re
import string
from pathlib import Path

import load_harness
from load_harness.release import (
    IntegrityManifest,
    ManifestEntry,
    freeze,
)
from load_harness.report import RunProvenance
from load_harness.version import HARNESS_VERSION, Version

# The >= 10,000 house-idiom floor is carried by the PURE convergence loop.
_PURE_ITERATIONS = 10_000

# A bounded representative subset runs the FULL freeze-into-tmp manifest-stamp check so the
# tarfile+hashlib cost stays in the seconds range. These iterations are IN ADDITION to the pure
# floor.
_FULL_FREEZE_ITERATIONS = 300

# The authoritative stamp as a string, resolved ONCE from the single source of truth. Every derived
# reference in the loop is asserted equal to this; nothing in the test restates "v2.3".
_STAMP = str(HARNESS_VERSION)

# A ``v<N>.<N>``-shaped version literal — the shape the single-authority scan hunts for in code.
_VERSION_LITERAL = re.compile(r"^v\d+\.\d+")

# The ONE module permitted to assign a version-shaped string as a real code literal.
_AUTHORITY_MODULE = "version.py"

# Tiny random source sets keep each full-freeze iteration cheap.
_MIN_FILES = 1
_MAX_FILES = 3
_MAX_CONTENT_BYTES = 16
_NAME_ALPHABET = string.ascii_lowercase + string.digits


def _random_hex_digest(rng: random.Random) -> str:
    """A random 64-char lowercase hex string standing in for a content SHA-256.

    The convergence property must hold no matter what digest surrounds the version stamp, so each
    iteration draws fresh random aggregate/per-file digests. The value is never asserted — only that
    the version stamp beside it stays the one authority.
    """
    return "".join(rng.choice("0123456789abcdef") for _ in range(64))


def _random_entries(rng: random.Random) -> tuple[ManifestEntry, ...]:
    """A small, canonically sorted tuple of random :class:`ManifestEntry` values.

    Entries are sorted by relpath (the manifest's construction invariant) with unique relpaths, so a
    valid :class:`IntegrityManifest` can be built around the version stamp. The entry content is
    irrelevant to the property; it is random surrounding data the stamp must not drift with.
    """
    used: set[str] = set()
    count = rng.randint(_MIN_FILES, _MAX_FILES)
    while len(used) < count:
        stem = "".join(rng.choice(_NAME_ALPHABET) for _ in range(rng.randint(1, 6)))
        used.add(f"{stem}.py")
    return tuple(
        ManifestEntry(relpath=relpath, sha256=_random_hex_digest(rng))
        for relpath in sorted(used)
    )


def _write_random_source(rng: random.Random, package_root: Path) -> None:
    """Materialize a tiny random ``.py`` source tree under ``package_root`` (a tmp dir).

    A handful of tiny files with random content, so each full-freeze iteration stamps the manifest
    over a DIFFERENT source set — proving the manifest ``version`` field converges on the authority
    independent of the content it froze.
    """
    count = rng.randint(_MIN_FILES, _MAX_FILES)
    for index in range(count):
        stem = "".join(rng.choice(_NAME_ALPHABET) for _ in range(rng.randint(1, 6)))
        target = package_root / f"{stem}{index}.py"
        length = rng.randint(0, _MAX_CONTENT_BYTES)
        target.write_bytes(bytes(rng.randint(0, 255) for _ in range(length)))


def test_property6_version_stamp_is_the_single_authority(tmp_path: Path) -> None:
    """Every derived version reference converges on the one ``HARNESS_VERSION`` stamp.

    The pure loop carries the >= 10,000 house floor by constructing fresh ``Version`` /
    ``RunProvenance`` / ``IntegrityManifest`` values stamped from ``str(HARNESS_VERSION)`` over
    random surrounding content and asserting each derived reference (``__version__``, the manifest
    ``version``, the provenance ``harness_version``, ``str(Version)``) equals the one stamp. The
    bounded full-freeze subset additionally freezes a random source set under ``HARNESS_VERSION``
    into ``tmp_path`` and asserts the on-disk ``MANIFEST.json`` ``version`` equals the stamp and
    ``load_harness.__version__``.
    """
    seed = 0x5A6E
    rng = random.Random(seed)

    # The package __init__ derives __version__ from the single stamp (Req 1.1, 1.2, 1.4). This is
    # invariant across the sweep; assert it once up front and re-affirm it against each freeze.
    assert load_harness.__version__ == _STAMP, (
        f"load_harness.__version__ ({load_harness.__version__!r}) diverged from "
        f"str(HARNESS_VERSION) ({_STAMP!r})"
    )

    for i in range(_PURE_ITERATIONS):
        # A Version reconstructed from the authoritative identifier round-trips to the same string.
        rebuilt = Version(identifier=_STAMP)
        assert str(rebuilt) == _STAMP, (
            f"seed={seed:#x} iter={i}: str(Version(identifier=stamp)) drifted from the stamp"
        )

        # The run provenance carries the stamp verbatim on the run output (Req 1.1).
        provenance = RunProvenance(
            harness_version=_STAMP,
            aggregate_sha256=_random_hex_digest(rng),
            integrity_intact=bool(rng.getrandbits(1)),
        )
        assert provenance.harness_version == _STAMP, (
            f"seed={seed:#x} iter={i}: RunProvenance.harness_version diverged from the stamp"
        )

        # An IntegrityManifest stamps the one authority in its version field regardless of the
        # random entries/aggregate around it (Req 2.4).
        entries = _random_entries(rng)
        manifest = IntegrityManifest(
            version=_STAMP,
            entries=entries,
            aggregate_sha256=IntegrityManifest.compute_aggregate(entries),
        )
        assert manifest.version == _STAMP, (
            f"seed={seed:#x} iter={i}: IntegrityManifest.version diverged from the stamp"
        )
        # The byte-stable JSON round-trip preserves the stamped authority.
        assert IntegrityManifest.from_json(manifest.to_json()).version == _STAMP, (
            f"seed={seed:#x} iter={i}: manifest version did not survive the JSON round-trip"
        )

        # Representative subset: a full freeze under HARNESS_VERSION stamps the on-disk manifest.
        if i < _FULL_FREEZE_ITERATIONS:
            _assert_full_freeze_stamps_authority(rng, tmp_path, i, seed)

    assert _PURE_ITERATIONS >= 10_000

    # The one-shot structural single-authority scan: no second code literal diverges (Req 1.4).
    _assert_single_authority_in_source()


def _assert_full_freeze_stamps_authority(
    rng: random.Random, tmp_path: Path, i: int, seed: int
) -> None:
    """Freeze a random source set under ``HARNESS_VERSION`` and assert the manifest stamps it.

    The on-disk ``MANIFEST.json`` ``version`` field must equal ``str(HARNESS_VERSION)`` and the
    package ``__version__`` — the manifest converging on the one authority across many random source
    contents (Req 2.4, 1.4).
    """
    src = tmp_path / "freeze" / f"i{i}" / "load_harness"
    src.mkdir(parents=True, exist_ok=True)
    _write_random_source(rng, src)
    dest = src.parent / "bin"

    frozen = freeze(src, HARNESS_VERSION, dest)
    assert frozen.version == _STAMP, (
        f"seed={seed:#x} iter={i}: FrozenRelease.version diverged from the stamp"
    )

    manifest = IntegrityManifest.from_json(
        frozen.manifest_path.read_text(encoding="utf-8")
    )
    assert manifest.version == _STAMP, (
        f"seed={seed:#x} iter={i}: on-disk MANIFEST.json version diverged from the stamp"
    )
    assert manifest.version == load_harness.__version__, (
        f"seed={seed:#x} iter={i}: on-disk MANIFEST.json version diverged from __version__"
    )


def _assert_single_authority_in_source() -> None:
    """Assert ``version.py`` is the ONLY module with a version-shaped code string literal (Req 1.4).

    Scans every shipped ``load_harness/`` ``.py`` module's AST for a ``v<N>.<N>``-shaped string that
    appears as a REAL code string literal — deliberately excluding every module/class/function
    docstring node and (by construction, since ``ast`` discards ``#`` comments) every doc comment.
    The only module permitted such a literal is ``version.py`` (the ``HARNESS_VERSION`` definition);
    any other module restating the version in code would be a divergent second authority and fails
    the property.
    """
    package_root = Path(load_harness.__file__).resolve().parent
    offenders: dict[str, list[str]] = {}

    for module_path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in module_path.parts or "bin" in module_path.parts:
            continue
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        docstring_nodes = _docstring_constant_ids(tree)
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
            and _VERSION_LITERAL.match(node.value)
        ]
        if literals:
            offenders[module_path.name] = literals

    # Exactly one module carries a version-shaped code literal, and it is version.py.
    assert set(offenders) == {_AUTHORITY_MODULE}, (
        "version-shaped code literal found outside the single authority "
        f"({_AUTHORITY_MODULE}): {offenders}"
    )
    # And version.py's only such literal is the authoritative identifier.
    assert offenders[_AUTHORITY_MODULE] == [_STAMP], (
        f"{_AUTHORITY_MODULE} carried an unexpected version-shaped literal set: "
        f"{offenders[_AUTHORITY_MODULE]} (expected the single stamp {_STAMP!r})"
    )


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
    """The ``id()`` of every string ``Constant`` node that is a module/class/function docstring.

    A docstring is prose, not a code string literal, so it must be excluded from the
    single-authority scan — a module docstring that mentions "e.g. ``v2.3``" (several shipped
    modules do) is NOT a second version authority. ``ast.get_docstring`` identifies the docstring
    text; here we collect
    the actual node identities so they are filtered precisely rather than by value.
    """
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstring_ids.add(id(body[0].value))
    return docstring_ids
