# Feature: harness-release-hygiene-r2-20, Property 5: run provenance records the exact release and refuses a mutated copy  # noqa: E501
# Validates: Requirements 5.1, 5.2, 5.3, 4.5
"""Run-provenance property test (B7 / R2-20, partial), task 7.3.

**Property 5 — Run provenance records the exact release and refuses a mutated copy.** For ANY run
that executes from a :class:`~load_harness.release.FrozenRelease`:

* if the release is INTACT, every produced :class:`~load_harness.report.RunReport` carries a
  :class:`~load_harness.report.RunProvenance` whose ``harness_version`` equals the frozen release's
  stamped version and whose ``aggregate_sha256`` equals the manifest's recorded aggregate digest,
  with ``integrity_intact=True`` (Req 5.1, 5.2);
* if the release is a Mutated_Copy, the run is REFUSED — resolving provenance raises
  :class:`~load_harness.release.MutatedCopyError` and no report is recorded as the released version
  (Req 5.3, 4.5).

**Where the >= 10,000 floor lives.** The run-provenance decision the whole property turns on is
:func:`load_harness.dryrun._resolve_provenance` — a pure-ish function (one ``verify`` + one manifest
read, no running gateway). The house >= 10,000-iteration floor is carried ENTIRELY by a loop over
``_resolve_provenance`` (``_PURE_ITERATIONS`` = 10,000): each iteration freezes a tiny random source
set into ``tmp_path`` and asserts that

* ``_resolve_provenance(frozen)`` on the INTACT release returns a ``RunProvenance`` whose
  ``harness_version`` == the frozen release's version, ``aggregate_sha256`` == the manifest's
  ``aggregate_sha256``, and ``integrity_intact`` is ``True`` (the attributable path); and
* after a random MUTATION of the frozen archive (flip a content byte / drop a member / add a stray
  member — reusing the shipped normalized-tar rebuild), ``_resolve_provenance(frozen)`` RAISES
  :class:`MutatedCopyError` (the refuse path).

A representative ASYNC subset (``_ASYNC_ITERATIONS``, tiny — it boots a real gateway) drives the
property END TO END through :func:`load_harness.dryrun.run_dry_run_async` under ``asyncio.run``:
an INTACT frozen release yields a :class:`DryRunResult` whose every ``RunReport.provenance`` is not
``None`` with the frozen version + ``integrity_intact=True``, and a MUTATED frozen release makes
``run_dry_run_async`` raise :class:`MutatedCopyError` BEFORE any report is produced (the run is
refused — nothing is recorded as the released version). The async subset needs ``httpx`` (the
``conformance`` extra), so the whole module ``pytest.importorskip``s it — a framework-free tree
SKIPS cleanly at collection, exactly like the ``test_dryrun`` conformance subset.

House idiom (Req 6.6): a seeded :func:`random.Random` driven for >= 10,000 iterations, NO
``hypothesis``; the run-provenance property is driven through ``asyncio.run`` for the subset.
Everything is written only under pytest's ``tmp_path`` — nothing is ever frozen into the real
``load_harness/`` tree.

**The live-tree default (``frozen=None``).** A handful of assertions (not 10k) confirm the honest
non-frozen path: ``_resolve_provenance(None)`` returns ``harness_version == str(HARNESS_VERSION)``,
``integrity_intact=True``, and an ``aggregate_sha256`` equal to a freeze of the live source's
aggregate (attributable to the exact live source it ran against, Req 5.1).

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import asyncio
import io
import random
import string
import tarfile
from pathlib import Path

import pytest

# The END-TO-END async subset drives run_dry_run_async against a booted gateway over the in-process
# httpx.ASGITransport, so httpx (the conformance extra) is required for it. A tree without the extra
# SKIPS the whole module at collection — the test_dryrun conformance-subset idiom. The pure
# >= 10,000-iteration floor also lives in this module, so it skips with the module when httpx is
# absent; httpx ships in the gateway_v2 .venv the gate runs under, so the floor runs in the gate.
pytest.importorskip("httpx")

from gateway_v2.runtime.resources import load_contract  # noqa: E402
from load_harness.arrivals import build_schedule  # noqa: E402
from load_harness.config import (  # noqa: E402
    ArrivalMode,
    RunSpec,
    StrataConfig,
    default_ladder_config,
    default_tolerances,
)
from load_harness.dryrun import DryRunResult, _resolve_provenance, run_dry_run_async  # noqa: E402
from load_harness.release import (  # noqa: E402
    FrozenRelease,
    IntegrityManifest,
    MutatedCopyError,
    freeze,
    verify,
)
from load_harness.report import RunProvenance  # noqa: E402
from load_harness.request_plan import Populations, plan_requests  # noqa: E402
from load_harness.strata import keys as keys_stratum  # noqa: E402
from load_harness.strata import prompts as prompts_stratum  # noqa: E402
from load_harness.strata import retry as retry_stratum  # noqa: E402
from load_harness.strata import tenants as tenants_stratum  # noqa: E402
from load_harness.version import HARNESS_VERSION  # noqa: E402

# The >= 10,000 house-idiom floor is carried ENTIRELY by the pure _resolve_provenance loop: each
# iteration performs a real freeze into tmp_path, resolves provenance on the intact release, then
# mutates the archive and asserts the resolve refuses. Small source sets keep each iteration cheap.
_PURE_ITERATIONS = 10_000

# The END-TO-END async subset boots a real gateway and drives the full (short) Poisson ladder, so it
# is deliberately TINY: a couple of iterations prove run_dry_run_async threads provenance onto every
# report for an intact release and refuses a mutated one. The >= 10,000 floor is NOT carried here.
_ASYNC_ITERATIONS = 2

# A fixed release version for the whole sweep. Provenance is a property of the release the run ran
# under; one stamp keeps the frozen artifact comparable. The identifier avoids a "/" so it is a
# single path component under dest_root. It intentionally matches HARNESS_VERSION so the frozen
# version equals the single authority.
_VERSION = HARNESS_VERSION

# Small source sets keep each pure iteration's real freeze + resolve in the millisecond range while
# still exercising a multi-file sorted member set with nested directories.
_MIN_FILES = 1
_MAX_FILES = 4
_MAX_CONTENT_BYTES = 24

# Package-relative directory segments a generated file may nest under (all live-source classes — no
# excluded bin/ / __pycache__ / test_* here, since every generated file must be a captured member
# the manifest lists so a mutation lands on an attributable release).
_DIR_SEGMENTS = ("", "strata", "sub/deep")
_NAME_ALPHABET = string.ascii_lowercase + string.digits

# The normalized tar-member format the shipped release._write_normalized_tar writes. Reproduced here
# so a mutated-archive rebuild is byte-for-byte the shipped normalization — only the intended
# mutation (one flipped byte / a dropped member / a stray member) differs from the frozen archive.
_TAR_MEMBER_ROOT = "load_harness"
_TAR_MEMBER_MTIME = 0
_TAR_MEMBER_MODE = 0o644
_TAR_MEMBER_UID = 0
_TAR_MEMBER_GID = 0

# A stray relpath for the "add a member" mutation — a name the generator never produces (lowercase
# /digit stems ending ``.py``), so a stray member can never collide with a manifest entry.
_STRAY_RELPATH = "STRAY_EXTRA_FILE.dat"
_STRAY_CONTENT = b"stray archive member not in the manifest"


def _random_relpath(rng: random.Random, used: set[str]) -> str:
    """A unique package-relative POSIX path for one generated source file.

    Draws a directory segment and a short lowercase/digit stem, retrying until the composed relpath
    is unique within the set (the manifest forbids a duplicate relpath). All names are plain ``.py``
    modules so every generated file is a captured, manifest-listed member.
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

    Writes 1-4 tiny files at unique package-relative paths with random content bytes, GUARANTEEING
    at least one file has >= 1 content byte so a byte can always be flipped for the flip-byte
    mutation. Returns the sorted POSIX relpaths of the non-empty files (the flip candidates).
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

    if not non_empty:
        victim = sorted(used)[0]
        (package_root / victim).write_bytes(bytes([rng.randint(1, 255)]))
        non_empty.append(victim)

    return sorted(non_empty)


def _read_archive_members(archive_path: Path) -> dict[str, bytes]:
    """Read a frozen ``harness.tar`` into a ``relpath -> content bytes`` map (root prefix stripped).

    Keys are package-relative POSIX relpaths (the ``load_harness/`` root stripped) so a rebuild can
    reproduce the exact member set with one member flipped / dropped / added.
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

    Reproduces ``release._write_normalized_tar`` byte-for-byte (``USTAR_FORMAT``, each member a
    ``REGTYPE`` with fixed mtime/mode/uid/gid and empty owner names, size from the content, members
    in sorted relpath order). The manifest is left untouched by the caller, so the only change from
    the shipped archive is the intended mutation.
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


def _mutate_archive(rng: random.Random, frozen: FrozenRelease, non_empty: list[str]) -> str:
    """Mutate ``frozen``'s archive in place by one random op; return a short description.

    One of three content/file-set mutations, chosen uniformly, each leaving ``MANIFEST.json``
    byte-unchanged so the archive diverges from the manifest in exactly the mutated dimension:

    * ``flip`` — flip one content bit of a random non-empty member (-> mutated digest);
    * ``drop`` — remove a random manifest-listed member from the archive (-> missing);
    * ``add``  — add a stray member not in the manifest (-> extra).

    Every op produces an ``is_mutated_copy`` release, so ``verify``/``_resolve_provenance`` must
    refuse. The ``drop`` op is only chosen when >= 2 members exist so a non-empty set survives.
    """
    members = _read_archive_members(frozen.archive_path)
    ops = ["flip", "add"]
    if len(members) >= 2:
        ops.append("drop")
    op = rng.choice(ops)

    if op == "flip":
        target = rng.choice(non_empty)
        body = bytearray(members[target])
        index = rng.randrange(len(body))
        body[index] ^= 1 << rng.randrange(8)
        members[target] = bytes(body)
    elif op == "drop":
        dropped = rng.choice(sorted(members))
        del members[dropped]
    else:  # add
        members[_STRAY_RELPATH] = _STRAY_CONTENT

    _rebuild_archive(frozen.archive_path, members)
    return op


def _manifest_aggregate(frozen: FrozenRelease) -> str:
    """The ``aggregate_sha256`` recorded in the frozen release's ``MANIFEST.json``."""
    return IntegrityManifest.from_json(
        frozen.manifest_path.read_text(encoding="utf-8")
    ).aggregate_sha256


def test_property5_resolve_provenance_records_release_or_refuses_mutation(
    tmp_path: Path,
) -> None:
    """>= 10,000 freezes: intact resolves to the exact release; any mutation is refused.

    The pure >= 10,000-iteration loop carries the house floor. Each iteration freezes a tiny random
    source set into ``tmp_path`` and asserts:

    * ``_resolve_provenance(frozen)`` on the INTACT release returns a ``RunProvenance`` whose
      ``harness_version`` == ``frozen.version``, ``aggregate_sha256`` == the manifest's recorded
      aggregate, and ``integrity_intact`` is ``True`` (Req 5.1, 5.2); and
    * after a random mutation (flip / drop / add) ``_resolve_provenance(frozen)`` RAISES
      ``MutatedCopyError`` — the run is refused, nothing attributed to the released version
      (Req 5.3, 4.5). A pre-mutation ``verify`` intact-sanity ensures the refusal is caused by the
      mutation, not a freeze bug.
    """
    seed = 0x5F0C
    rng = random.Random(seed)
    ops_seen: dict[str, int] = {"flip": 0, "drop": 0, "add": 0}

    for i in range(_PURE_ITERATIONS):
        src = tmp_path / "src" / f"i{i}" / "load_harness"
        src.mkdir(parents=True, exist_ok=True)
        non_empty = _write_random_source(rng, src)

        dest = tmp_path / "dest" / f"i{i}"
        frozen = freeze(src, _VERSION, dest)

        # Intact release: provenance records the exact release (Req 5.1, 5.2).
        intact = verify(frozen)
        assert intact.intact and not intact.is_mutated_copy, (
            f"seed={seed:#x} iter={i}: freshly frozen untouched release was not intact: {intact}"
        )
        prov = _resolve_provenance(frozen)
        assert isinstance(prov, RunProvenance)
        assert prov.harness_version == frozen.version, (
            f"seed={seed:#x} iter={i}: provenance version {prov.harness_version!r} != frozen "
            f"version {frozen.version!r}"
        )
        assert prov.aggregate_sha256 == _manifest_aggregate(frozen), (
            f"seed={seed:#x} iter={i}: provenance aggregate != the manifest's recorded aggregate"
        )
        assert prov.integrity_intact is True, (
            f"seed={seed:#x} iter={i}: intact release resolved integrity_intact != True"
        )

        # Mutated release: the run is REFUSED (Req 5.3, 4.5).
        op = _mutate_archive(rng, frozen, non_empty)
        ops_seen[op] += 1
        assert verify(frozen).is_mutated_copy, (
            f"seed={seed:#x} iter={i}: {op} mutation did not register as a mutated copy"
        )
        with pytest.raises(MutatedCopyError):
            _resolve_provenance(frozen)

    assert _PURE_ITERATIONS >= 10_000
    assert all(count > 0 for count in ops_seen.values()), (
        f"seed={seed:#x}: not every mutation op was exercised across the sweep: {ops_seen}"
    )


def test_resolve_provenance_live_tree_default_is_attributable(tmp_path: Path) -> None:
    """``frozen=None`` (the live-tree default) attributes to the single stamp + live aggregate.

    A handful of assertions (not 10k) confirm the honest non-frozen path: ``_resolve_provenance``
    with no frozen release returns ``harness_version == str(HARNESS_VERSION)``, ``integrity_intact``
    ``True``, and an ``aggregate_sha256`` that is a real hex digest (the live-source aggregate the
    report is attributable to, Req 5.1), never claiming to be a frozen release it is not.
    """
    prov = _resolve_provenance(None)
    assert prov.harness_version == str(HARNESS_VERSION)
    assert prov.integrity_intact is True
    # The live-source aggregate is a 64-char hex SHA-256 — a real, attributable digest, not blank.
    assert len(prov.aggregate_sha256) == 64
    assert all(ch in "0123456789abcdef" for ch in prov.aggregate_sha256)


def _async_run_spec(seed: int) -> RunSpec:
    """A minimal RunSpec for the end-to-end async subset (modest rate, small count).

    Enables a couple of strata at modest shares so a plan is representative; the SLO pass bound is
    read from ``ResourceContract.target_p99_ms`` (never declared here). Kept small so the booted-
    gateway ladder runs in well under a second per iteration.
    """
    strata = StrataConfig(
        multi_key=True,
        long_prompt=True,
        long_prompt_share=0.01,
        retry=True,
        retry_share=0.10,
        aggressive_retry_share=0.05,
        secret_output=True,
        secret_output_share=0.20,
        tenant_scale=True,
    )
    return RunSpec(
        offered_rate_per_s=800.0,
        arrival_mode=ArrivalMode.POISSON,
        seed=seed,
        strata=strata,
        tolerances=default_tolerances(),
        ladder=default_ladder_config(),
    )


def _async_planned(run_spec: RunSpec, count: int) -> tuple[object, ...]:
    """Build a tiny planned-request tuple; its length sets the per-rate offered count."""
    rng = random.Random(run_spec.seed)
    schedule = build_schedule(
        mode=ArrivalMode.POISSON,
        offered_rate_per_s=run_spec.offered_rate_per_s,
        count=count,
        rng=rng,
        clock=lambda: 0.0,
    )
    populations = Populations(
        key_population=keys_stratum.build_key_population(2_000, 200),
        tenant_population=tenants_stratum.build_tenant_population(10_000),
        prompt_corpus=prompts_stratum.default_corpus(),
        retry_policy=retry_stratum.RetryPolicy(
            base_share=run_spec.strata.retry_share,
            aggressive_share=run_spec.strata.aggressive_retry_share,
        ),
    )
    return plan_requests(run_spec, schedule, rng, populations=populations)


def _frozen_live_release(tmp_path: Path) -> FrozenRelease:
    """Freeze the LIVE ``load_harness/`` package into a tmp dest and return the handle.

    The async subset must run from a real, verifiable Frozen_Release of the actual harness source
    (the frozen tree is what the run attributes to), so this freezes the installed package root
    under ``_VERSION`` into ``tmp_path`` — never into the repo tree.
    """
    from load_harness import release as release_module

    package_root = Path(release_module.__file__).resolve().parent
    dest = tmp_path / "bin"
    return freeze(package_root, _VERSION, dest)


def test_property5_async_run_dry_run_threads_provenance_or_refuses(tmp_path: Path) -> None:
    """END-TO-END (async subset): intact run stamps every report; mutated run is refused.

    A tiny number of iterations boot a real gateway and drive the short Poisson ladder under
    ``asyncio.run`` from a frozen LIVE release:

    * INTACT frozen release — ``run_dry_run_async(..., frozen=intact)`` returns a ``DryRunResult``
      whose every ``RunReport.provenance`` is not ``None``, carries ``harness_version ==
      frozen.version`` and ``integrity_intact=True``, and whose ``aggregate_sha256`` equals the
      manifest's recorded aggregate (Req 5.1, 5.2);
    * MUTATED frozen release — ``run_dry_run_async(..., frozen=mutated)`` RAISES
      ``MutatedCopyError`` before any report is produced, so the run is refused and nothing is
      recorded as the released version (Req 5.3, 4.5).

    The >= 10,000 floor is NOT carried here (the pure ``_resolve_provenance`` loop carries it); this
    is the representative end-to-end proof that the resolve verdict actually governs the run output.
    """
    contract, _logs = load_contract()

    for i in range(_ASYNC_ITERATIONS):
        run_spec = _async_run_spec(seed=0xA5 + i)
        planned = _async_planned(run_spec, count=64)

        # --- INTACT: every produced report carries the frozen release's provenance. ---
        intact_release = _frozen_live_release(tmp_path / f"intact{i}")
        expected_aggregate = _manifest_aggregate(intact_release)
        result: DryRunResult = asyncio.run(
            run_dry_run_async(
                run_spec,
                planned,
                contract=contract,
                max_steps=1,
                frozen=intact_release,
            )
        )
        assert result.reports, f"iter={i}: intact run produced no reports"
        for report in result.reports:
            assert report.provenance is not None, (
                f"iter={i}: an intact-run report carried provenance=None"
            )
            assert report.provenance.harness_version == intact_release.version
            assert report.provenance.integrity_intact is True
            assert report.provenance.aggregate_sha256 == expected_aggregate

        # --- MUTATED: the run is refused before any report is recorded. ---
        mutated_release = _frozen_live_release(tmp_path / f"mutated{i}")
        members = _read_archive_members(mutated_release.archive_path)
        victim = sorted(members)[0]
        body = bytearray(members[victim]) or bytearray(b"\x00")
        body[0] ^= 0x01
        members[victim] = bytes(body)
        _rebuild_archive(mutated_release.archive_path, members)
        assert verify(mutated_release).is_mutated_copy, (
            f"iter={i}: the mutated live release did not register as a mutated copy"
        )
        with pytest.raises(MutatedCopyError):
            asyncio.run(
                run_dry_run_async(
                    run_spec,
                    planned,
                    contract=contract,
                    max_steps=1,
                    frozen=mutated_release,
                )
            )

    assert _ASYNC_ITERATIONS >= 1
