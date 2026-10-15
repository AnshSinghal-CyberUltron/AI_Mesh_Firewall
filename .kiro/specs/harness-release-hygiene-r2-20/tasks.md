# Implementation Plan: immutable harness release hygiene (B7 / R2-20, partial — local half)

## Overview

Convert the B7 / R2-20 (partial) design into a series of code-generation steps that stand up the
**local, buildable half** of R2-20 for the shipped B6 `gateway_v2/load_harness/` package: a
runtime-discoverable **version stamp**, a **freeze / release mechanism** that captures the harness
source into a named, immutable, versioned frozen copy (`bin/<version>/harness.tar` + `MANIFEST.json`),
**byte-stability** of that frozen copy, an **integrity guard** that detects a frozen copy mutated in
place, a thin `python -m load_harness.release` CLI, and **run provenance** that records the exact
release a capacity run executed under — reconciling with, not rebuilding, B6's `RunReport` / `dryrun`.

The work is built **bottom-up** so each piece is gate-checkable before the next builds on it — the
same shape as the shipped B1–B6 cards (single-authority version type first; pure freeze/manifest/verify
core before the CLI and the run-time wiring; source before its tests; property/unit/pollution tests;
checkpoint Gates; a final full-gate checkpoint):

1. **Version stamp — the single authority** (`version.py`): a frozen `Version` value + the
   `HARNESS_VERSION` constant, with `__init__.py` / `README.md` prose derived from it (Req 1).
2. **Release core — freeze + manifest** (`release.py` pure functions): `iter_source_files` (canonical
   sorted set, excludes `bin/`/`__pycache__`/tests), `ManifestEntry` / `IntegrityManifest` /
   `FrozenRelease`, the normalized-tar + `MANIFEST.json` `freeze`, refuse-overwrite-a-differing-release
   + idempotent re-freeze (Req 2, 3).
3. **Integrity guard** (`release.py`): `IntegrityResult` + `verify` + the `MutatedCopyError` /
   `ReleaseExists` fail-closed posture (Req 4).
4. **The CLI** (`python -m load_harness.release`): a thin `freeze` / `verify` entrypoint returning an
   int exit code (Req 2, 4).
5. **The sensitive packaging/ignore edits** (`pyproject.toml` + `.gitignore`): the setuptools/ruff/mypy
   exclusions for `bin/` + the `.gitignore` entry — isolated so a sub-agent does the shared-build-config
   edit carefully (Req 6.4).
6. **Run provenance — the sensitive shipped-file edit** (`report.py` + `dryrun.py`): add `RunProvenance`
   + the OPTIONAL `provenance` field (additive, default `None`), thread it through `dryrun` with the
   run-time verify/refuse — isolated and flagged as an additive, non-breaking edit to shipped B6 files
   (Req 5).
7. **Tests** — the six property tests (house idiom), the unit edge-case + reconciliation tests, and the
   dedicated **pollution-proof** test (Req 6.4).
8. **Gate checkpoints** after the release core, after the RunReport/dryrun wiring, and a FINAL gate.

Implementation language: **Python** — the design is grounded in concrete `gateway_v2` /
`load_harness` modules and the shipped house idiom (stdlib `hashlib`/`pathlib`/`json`/`tarfile`/
`dataclasses` + the shipped `load_harness.report` value type); the design uses Python throughout, so no
implementation-language question is needed.

### Conventions enforced in EVERY coding task

- **Property tests** use the house idiom: a seeded `random.Random` driven for **≥ 10,000 iterations**,
  **NO hypothesis**; the run-provenance property is driven through `asyncio.run`. Each property test is
  tagged `# Feature: harness-release-hygiene-r2-20, Property N: <text>` and
  `# Validates: Requirements X.Y`, with exactly one design property ↔ one property-based test. The six
  design properties (Properties 1–6) map onto six specific property-test sub-tasks placed next to the
  code they validate.
- **Value types** are frozen slotted dataclasses — `Version`, `ManifestEntry`, `IntegrityManifest`,
  `FrozenRelease`, `IntegrityResult`, `RunProvenance` are all `@dataclass(frozen=True, slots=True)`
  (`check_frozen_dataclasses`); no module-level mutable (`check_no_module_mutable`) — the canonical file
  set is built inside a function, the version constant is a frozen `Version` instance.
- **No capacity-position literal outside `gateway_v2/runtime/resources.py`**
  (`check_capacity_literals`): a version string (`"v2.3"`) and a SHA-256 digest are release-identity /
  content-integrity values, not a host CPU/RAM/FD/RPS budget — the same named-constant class as B6's
  stratum floors, carrying none of the `CAPACITY_KEYWORDS`.
- **No HTTP type introduced** (`check_http_outside_edge_resolve`): the mechanism is offline and
  server-free; the only async touch is the existing `dryrun` wiring.
- **Modules ≤ 800 lines, functions ≤ 120 lines** (`check_sizes`); `check_tenant_scale` /
  `check_secret_headers` untouched; `unscanned_channels() == ()` unchanged (the card adds no channel).
- **No new import edge**: the new modules import only the stdlib plus the already-shipped
  `load_harness.report` value type; `load_harness` stays **outside** `root_packages = ["gateway_v2"]`,
  so `lint-imports` stays **contracts kept** with no new serving-path edge.
- **Determinism**: byte-stability depends only on file **content** and the canonical member set — tar
  members written with fixed mtime/mode/ownership and sorted order, manifest JSON with sorted keys +
  fixed trailing newline, digests over file bytes (never mtimes).
- Each task references its requirement sub-clauses and the design component/section/property it
  satisfies (traceability), matching the B1–B6 `tasks.md` style; every code task is followed by its
  test/gate note.

### The gate (run from cwd `gateway_v2` using the `gateway_v2/.venv` venv)

All gate commands run from cwd `gateway_v2` with the `gateway_v2/.venv` venv (system python lacks
redis). The full gate is:

- `.venv/bin/python -m pytest`
- `.venv/bin/mypy --strict`
- `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`
- `.venv/bin/lint-imports`
- the 6 AST gates: `.venv/bin/python -m lint.check_capacity_literals gateway_v2`,
  `.venv/bin/python -m lint.check_no_module_mutable gateway_v2`,
  `.venv/bin/python -m lint.check_frozen_dataclasses gateway_v2`,
  `.venv/bin/python -m lint.check_http_outside_edge_resolve gateway_v2`,
  `.venv/bin/python -m lint.check_sizes gateway_v2`,
  `.venv/bin/python -m lint.check_tenant_scale gateway_v2`
- the unscanned-channels check
  (`from gateway_v2.domain.channels import unscanned_channels; unscanned_channels() == ()`)

The full suite must stay green: B6's 1631-passed baseline **plus** the new B7 tests, confirming the
additive `report.RunReport` edit broke nothing.

## Tasks

- [x] 1. Version stamp — the single authority (`version.py`)
  - [x] 1.1 Implement the frozen `Version` value + `HARNESS_VERSION`
    - Create `gateway_v2/load_harness/version.py` with a `@dataclass(frozen=True, slots=True)`
      `Version` carrying `identifier: str` (e.g. `"v2.3"`) and a `__str__` returning the identifier;
      declare `HARNESS_VERSION: Version = Version(identifier="v2.3")` as the ONE source of truth,
      importable + discoverable at runtime; stdlib-only, no module-level mutable, module ≤ 800 lines /
      functions ≤ 120
    - _Requirements: 1.1, 1.2, 1.3_
    - _Design: Components → Version stamp (version.py); Data Models (version.py)_

  - [x] 1.2 Derive the `__init__.py` / README prose from the single stamp
    - Edit `gateway_v2/load_harness/__init__.py` to expose the stamp
      (`__version__ = str(version.HARNESS_VERSION)`, add to `__all__`) and state in its module docstring
      that the authoritative version lives in `version.py`; annotate the `README.md` "v2.3" line as
      derived from `HARNESS_VERSION` rather than restating a separate literal — no module hardcodes
      `"v2.3"` a second time
    - _Requirements: 1.4_
    - _Design: Components → Version stamp (prose derives from the stamp, Req 1.4); Correctness Property 6_

- [x] 2. Release core — freeze + manifest (`release.py` pure functions)
  - [x] 2.1 Implement `iter_source_files` + the manifest/release value types
    - Create `gateway_v2/load_harness/release.py` with `iter_source_files(package_root) ->
      tuple[Path, ...]` returning the canonical, sorted POSIX-relative B6 module set and EXCLUDING
      `__pycache__`, the `bin/` artifact tree itself, and any test file (so a freeze captures only live
      source and can never recursively capture a prior `Frozen_Release`); plus frozen+slots
      `ManifestEntry(relpath, sha256)`, `IntegrityManifest(version, entries, aggregate_sha256)` with
      byte-stable `to_json`/`from_json` (sorted keys, fixed trailing newline, sorted entries), and
      `FrozenRelease(version, archive_path, manifest_path)`
    - _Requirements: 2.2, 2.3, 3.3_
    - _Design: Components → Release mechanism (iter_source_files, ManifestEntry, IntegrityManifest,
      FrozenRelease); Architecture → Frozen-artifact location (normalized tar + MANIFEST.json)_

  - [x] 2.2 Implement `freeze` — normalized tar + manifest, refuse-overwrite, idempotent
    - Add `freeze(package_root, version, dest_root) -> FrozenRelease` to `release.py`: enumerate
      `iter_source_files`, write ONE normalized `bin/<version>/harness.tar` (fixed mtime `0`, fixed
      mode, fixed uid/gid/uname/gname, sorted member order, relative POSIX paths under
      `load_harness/<module>`) and a sibling `bin/<version>/MANIFEST.json` mapping each file to its
      content SHA-256 plus an aggregate digest, stamping the `Version` INSIDE the manifest; if
      `bin/<version>/` already exists, recompute the manifest for current source and compare
      `aggregate_sha256` — identical ⇒ idempotent no-op (byte-stable re-freeze), different ⇒ raise
      `ReleaseExists` (never overwrite, fail-closed)
    - _Requirements: 2.1, 2.4, 2.5, 3.1, 3.2_
    - _Design: Components → Release mechanism (freeze, refuse-to-overwrite, byte-stability);
      Error Handling (ReleaseExists, determinism hazards neutralized)_

  - [x]* 2.3 Property test — re-freeze is byte-identical (PBT)
    - Add `gateway_v2/tests/load_harness/test_release.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis — **Property 1: Re-freeze is byte-identical**: for any randomly generated source file
      set (contents + names), freezing it twice under the same `Version` produces identical per-file
      digests, an identical aggregate digest, and byte-identical `IntegrityManifest` JSON
    - Tag `# Feature: harness-release-hygiene-r2-20, Property 1` / `# Validates: Requirements 3.1, 3.3`
    - _Requirements: 3.1, 3.3_
    - _Design: Correctness Properties → Property 1_

  - [x]* 2.4 Property test — differing source yields a differing aggregate digest (PBT)
    - Add `gateway_v2/tests/load_harness/test_release_digest.py`: seeded `random.Random` ≥ 10,000 iters,
      no hypothesis — **Property 4: Differing source yields a differing aggregate digest**: for any two
      source file sets differing in at least one file's content, freezing each under the same `Version`
      produces different aggregate digests, and `freeze` into an existing differing release raises
      `ReleaseExists` rather than overwrites
    - Tag `# Feature: harness-release-hygiene-r2-20, Property 4` / `# Validates: Requirements 3.2, 2.5`
    - _Requirements: 3.2, 2.5_
    - _Design: Correctness Properties → Property 4; Error Handling (refuse-to-overwrite)_

- [x] 3. Integrity guard — detect a mutated frozen copy (`release.py`)
  - [x] 3.1 Implement `IntegrityResult` + `verify` + `MutatedCopyError`
    - Add to `release.py` a frozen+slots `IntegrityResult(intact, mutated_files, missing_files,
      extra_files)` with an `is_mutated_copy` property, and `verify(frozen) -> IntegrityResult` that
      reads the frozen archive + its `MANIFEST.json`, recomputes each member's content SHA-256, and
      compares against the manifest: all match ⇒ `intact=True`; content mismatch ⇒ `mutated_files`;
      manifest file absent from archive ⇒ `missing_files`; archive file absent from manifest ⇒
      `extra_files`; a missing archive or unreadable/invalid `MANIFEST.json` fails closed (never
      silently intact); define `MutatedCopyError` for the run-time refuse posture; `verify` is pure over
      the artifact bytes (offline, server-free)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 6.5_
    - _Design: Components → Integrity guard (IntegrityResult, verify, fail-closed MutatedCopyError);
      Error Handling (malformed/absent artifact fails closed)_

  - [x]* 3.2 Property test — any single-byte mutation is detected (PBT)
    - Add `gateway_v2/tests/load_harness/test_verify.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis — **Property 2: Any single-byte mutation is detected**: for any frozen release and any
      single-byte change to the content of any one frozen file, `verify` reports `is_mutated_copy` with
      that file in `mutated_files` (never `intact`)
    - Tag `# Feature: harness-release-hygiene-r2-20, Property 2` / `# Validates: Requirements 4.1, 4.3`
    - _Requirements: 4.1, 4.3_
    - _Design: Correctness Properties → Property 2_

  - [x]* 3.3 Property test — missing or extra files are detected (PBT)
    - Add `gateway_v2/tests/load_harness/test_verify_fileset.py`: seeded `random.Random` ≥ 10,000 iters,
      no hypothesis — **Property 3: Missing or extra files are detected**: for any frozen release,
      removing any manifest-listed file from the archive, or adding any file not in the manifest, causes
      `verify` to report a `Mutated_Copy` (in `missing_files` / `extra_files` respectively); an
      untouched release verifies as intact
    - Tag `# Feature: harness-release-hygiene-r2-20, Property 3` / `# Validates: Requirements 4.2, 4.4`
    - _Requirements: 4.2, 4.4_
    - _Design: Correctness Properties → Property 3_

  - [x]* 3.4 Unit tests — release/verify edge cases
    - Add `gateway_v2/tests/load_harness/test_release_unit.py`: `ReleaseExists` on a differing
      re-freeze; idempotent no-op on an identical re-freeze; `verify` on an intact release / a content
      mutation / a missing file / an extra file / a malformed-or-absent manifest (fails closed);
      representative examples (not PBT)
    - _Requirements: 2.5, 3.1, 4.2, 4.3, 4.4, 6.5_
    - _Design: Testing Strategy (unit tests); Error Handling_

- [x] 4. Release CLI — `python -m load_harness.release`
  - [x] 4.1 Implement the thin freeze/verify CLI
    - Add a `main()` to `release.py` and wire a `python -m load_harness.release` entrypoint (mirror the
      `state_control` / `audit_control` `__main__` shape): a `freeze` subcommand (reads live source,
      calls `freeze`, returns 0 / non-zero on `ReleaseExists`) and a `verify` subcommand (resolves a
      `bin/<version>/` release, calls `verify`, returns 0 when intact and non-zero + a
      `MutatedCopyError`-style diagnostic on `is_mutated_copy`); honest typing (no `Any` leak), no
      module-level mutable
    - _Requirements: 2.1, 4.1, 4.5_
    - _Design: Module map (release.py:main() + the python -m load_harness.release entry)_

  - [x]* 4.2 Unit test — CLI exit codes
    - Add `gateway_v2/tests/load_harness/test_release_cli.py`: a `freeze` into a tmp `bin/` returns 0
      and is idempotent on re-run; `verify` of an intact release returns 0; `verify` of a mutated copy
      returns non-zero; `freeze` of a differing release under an existing version returns non-zero;
      representative examples (not PBT)
    - _Requirements: 2.1, 2.5, 4.5_
    - _Design: Module map (thin CLI); Error Handling (fail-closed exit codes)_

- [x] 5. SENSITIVE EDIT (a) — packaging/ignore exclusions for `bin/` (shared build config)
  - [x] 5.1 Edit `pyproject.toml` + `.gitignore` to exclude the frozen-artifact tree
    - **Shared build config — edit carefully, additive/exclusionary only.** In
      `gateway_v2/pyproject.toml`: ADD `exclude = ["load_harness.bin*"]` to
      `[tool.setuptools.packages.find]` (leave the `include` line UNCHANGED); ADD `"load_harness/bin"`
      to `[tool.ruff].extend-exclude`; ADD `exclude = ["load_harness/bin/"]` to `[tool.mypy]`; leave
      `[tool.importlinter].root_packages = ["gateway_v2"]` UNCHANGED. Add
      `gateway_v2/load_harness/bin/` to `.gitignore` (a `Frozen_Release` is a reproducible build
      artifact, not committed). Confirm no existing exclusion/include/known-first-party entry is removed
      or reordered
    - _Requirements: 6.1, 6.4_
    - _Design: Architecture → Exact pyproject.toml / ignore edits; Gate posture_

- [x] 6. Gate — version + release core + CLI + packaging edits green
  - Ensure all tests pass, ask the user if questions arise. From cwd `gateway_v2` with the
    `gateway_v2/.venv` venv run the full gate over `version.py`, `release.py`, the CLI, the
    `pyproject.toml`/`.gitignore` edits + their tests: `.venv/bin/python -m pytest`,
    `.venv/bin/mypy --strict`,
    `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
    `.venv/bin/lint-imports` (contracts kept, no new edge), the 6 AST gates
    (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
    and the unscanned-channels check (`unscanned_channels() == ()`). Fix any gate failure before wiring
    run provenance.

- [x] 7. SENSITIVE EDIT (b) — run provenance on the shipped `RunReport` / `dryrun` (additive, non-breaking)
  - [x] 7.1 Add `RunProvenance` + the OPTIONAL `provenance` field to shipped `report.py`
    - **`report.py` is a SHIPPED B6 file — the edit MUST be additive + non-breaking.** Add a frozen+slots
      `RunProvenance(harness_version, aggregate_sha256, integrity_intact)` and add ONE field to the
      shipped `RunReport`: `provenance: RunProvenance | None = None` (optional, default `None` so every
      existing construction site and test that does not supply provenance keeps compiling + passing); the
      15 shipped fields stay unchanged
    - _Requirements: 5.1, 5.2, 5.4, 6.2_
    - _Design: Components → Run provenance (one optional field decision); Data Models (RunProvenance,
      RunReport edited)_

  - [x] 7.2 Thread provenance through shipped `dryrun.py` with run-time verify/refuse
    - **`dryrun.py` is a SHIPPED B6 file — the edit MUST be additive + non-breaking.** Before measuring,
      `run_dry_run_async` resolves the `Frozen_Release` it runs from and calls `verify(...)`: intact ⇒
      pass `provenance=RunProvenance(version, aggregate_sha256, integrity_intact=True)` into
      `_build_report` for each `RunReport`; mutated ⇒ raise `MutatedCopyError` (refuse the run, never
      record a result as the released version). A dry-run from the live tree attaches provenance carrying
      `HARNESS_VERSION` + the live-source aggregate digest with `integrity_intact=True` so a report is
      always attributable. Update `dryrun._build_report` to accept and pass the `provenance=` argument;
      do not change any other `dryrun` behavior
    - _Requirements: 5.1, 5.2, 5.3, 4.5_
    - _Design: Components → Run provenance (dryrun wiring); Error Handling (fail-closed on mutation,
      honest provenance for a non-frozen local run)_

  - [x]* 7.3 Property test — run provenance records the exact release / refuses a mutated copy (PBT, async)
    - Add `gateway_v2/tests/load_harness/test_provenance.py`: seeded `random.Random` ≥ 10,000 iters, no
      hypothesis, driven via `asyncio.run`, using an in-memory / tmp-dir frozen release (no server) —
      **Property 5: Run provenance records the exact release and refuses a mutated copy**: for any run
      from a `Frozen_Release`, if intact every produced `RunReport` carries a `RunProvenance` whose
      `harness_version` + `aggregate_sha256` equal the release's stamp + verified digest; if a
      `Mutated_Copy`, the run is refused (no `RunReport` recorded as the released version)
    - Tag `# Feature: harness-release-hygiene-r2-20, Property 5` /
      `# Validates: Requirements 5.1, 5.2, 5.3, 4.5`
    - _Requirements: 5.1, 5.2, 5.3, 4.5_
    - _Design: Correctness Properties → Property 5; Testing Strategy (Property 5 under asyncio.run)_

  - [x]* 7.4 Unit + reconciliation tests — additive `RunReport` edit broke nothing
    - Add `gateway_v2/tests/load_harness/test_report_provenance.py`: a `RunReport` with `provenance=None`
      (the shipped default path) still constructs; a `RunReport` built with a `RunProvenance` round-trips
      every shipped field unchanged plus the new field; confirm the existing shipped `report` tests
      remain green with no edits beyond the new coverage. Guards the additive, non-breaking edit to the
      shipped dataclass
    - _Requirements: 5.4, 6.2_
    - _Design: Testing Strategy (reconciliation test — additive, non-breaking)_

- [x] 8. Gate — run provenance wiring green (shipped RunReport edit confirmed non-breaking)
  - Ensure all tests pass, ask the user if questions arise. From cwd `gateway_v2` with the
    `gateway_v2/.venv` venv run the full gate over the `report.py`/`dryrun.py` edits + the provenance
    tests, confirming the full suite stays green (B6's 1631-passed baseline + the new B7 tests):
    `.venv/bin/python -m pytest`, `.venv/bin/mypy --strict`,
    `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
    `.venv/bin/lint-imports`, the 6 AST gates
    (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
    and the unscanned-channels check. Confirm the additive `RunReport` edit broke no existing
    construction site or test.

- [x] 9. Pollution-proof test + version single-authority property
  - [x] 9.1 Pollution-proof test — the frozen artifact never pollutes tooling
    - Add `gateway_v2/tests/load_harness/test_pollution_proof.py`: after a `freeze` into a tmp
      `bin/<version>/`, assert (a) setuptools package discovery over the tree does NOT list a
      `load_harness.bin*` package; (b) the `.py`-only AST gates and mypy/ruff file selection do not
      include any path under `bin/`; (c) a second `freeze` reads the LIVE source set (never the `bin/`
      archive), so no recursive self-freeze occurs. Confirm the AST gates, `lint-imports`, and
      `unscanned_channels() == ()` stay green and unchanged
    - _Requirements: 6.4_
    - _Design: Testing Strategy → Gate / pollution proof (Req 6.4); Architecture → Frozen-artifact
      location (invisible to discovery/lint/typing, no recursive self-freeze)_

  - [x]* 9.2 Property test — the version stamp is the single authority (PBT)
    - Add `gateway_v2/tests/load_harness/test_version_authority.py`: seeded `random.Random` ≥ 10,000
      iters, no hypothesis — **Property 6: The version stamp is the single authority**: for any
      reference to the release version in the package (the manifest's `version`, the report's
      `harness_version`, the `__init__`/README-derived prose), the value equals
      `version.HARNESS_VERSION` — no second literal diverges
    - Tag `# Feature: harness-release-hygiene-r2-20, Property 6` /
      `# Validates: Requirements 1.1, 1.2, 1.4, 2.4`
    - _Requirements: 1.1, 1.2, 1.4, 2.4_
    - _Design: Correctness Properties → Property 6_

- [x] 10. Final verification gate
  - [x] 10.1 Run the full gate and map evidence to the card format
    - From cwd `gateway_v2` with the `gateway_v2/.venv` venv, run the FULL gate over the whole
      `load_harness/` package + `gateway_v2/tests/load_harness/`: `.venv/bin/python -m pytest` (full
      offline suite — B6's 1631-passed baseline + the B7 property/unit/pollution subset),
      `.venv/bin/mypy --strict`,
      `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness lint tests`,
      `.venv/bin/lint-imports` (contracts kept, no new serving-path edge), the 6 AST gates
      (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
      and the unscanned-channels check (`unscanned_channels() == ()`); confirm modules stay ≤ 800 lines
      / functions ≤ 120. Fix any gate failure before closing
    - Map the evidence to the card format (why / implementation / local-acceptance / exit / evidence /
      rollback): why = make a capacity measurement attributable to a known, unmodified harness release
      (the exact failure R2-20 cites); implementation = `version.py` + `release.py` (freeze/manifest/
      verify + CLI) + the `pyproject.toml`/`.gitignore` exclusions + the additive `report.py`/`dryrun.py`
      provenance edit + the `tests/load_harness/` suite; local-acceptance = the six property tests
      (Properties 1–6), the unit/reconciliation tests, and the pollution-proof test; exit = full gate
      green with the shipped `RunReport` edit confirmed non-breaking; rollback = the new `version.py`/
      `release.py` + tests are additive, the `pyproject.toml` edits are exclusionary, and the
      `report.py`/`dryrun.py` edits are an optional-field add — reverting removes the mechanism with the
      shipped B6 behavior restored. Record the deferred `/dev/shm` host-config half + cloud validation
      environments as NOT closed here
    - _Requirements: 6.4, 7.1, 7.2, 7.3_
    - _Design: Testing Strategy; Deferred; Traceability_

- [x] 11. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are the test sub-tasks (property, unit, pollution-proof) and are visually
  distinguished; this is a Run-All-Tasks card, so the leaf tasks are schedulable and all are intended to
  be implemented. Core implementation sub-tasks are never optional; top-level tasks are never marked `*`.
- Each task references specific requirement sub-clauses and the design component/section/property it
  satisfies for traceability; collectively the tasks cover all 7 requirements per the design's
  Traceability table.
- The six design properties map one-to-one onto six property-test sub-tasks: P1 (§2.3 re-freeze
  byte-identical), P2 (§3.2 single-byte mutation detected), P3 (§3.3 missing/extra file detected), P4
  (§2.4 differing-source digest + refuse-overwrite), P5 (§7.3 run provenance records exact release /
  refuses mutated copy, async via `asyncio.run`), P6 (§9.2 version-stamp single authority). All property
  tests use a seeded `random.Random` for ≥ 10,000 iterations, no hypothesis; one property ↔ one test;
  tagged `# Feature: harness-release-hygiene-r2-20, Property N` / `# Validates: Requirements X.Y`.
- The two sensitive edits are isolated as their own clearly-scoped tasks so a sub-agent does them
  carefully: Task 5.1 is the shared build-config edit (`pyproject.toml` setuptools/ruff/mypy exclusions
  + `.gitignore`, `root_packages` UNCHANGED), and Tasks 7.1/7.2 are the additive, non-breaking edit to
  the SHIPPED B6 `report.py` / `dryrun.py` (the optional `provenance` field + the `dryrun` wiring,
  default `None` so existing construction sites/tests stay green). Task 7.4 explicitly guards that the
  additive `RunReport` edit broke nothing.
- Gate checkpoints (tasks 6, 8, 10/11) run the full gate from cwd `gateway_v2` using the
  `gateway_v2/.venv` venv (system python lacks redis): `.venv/bin/python -m pytest`,
  `.venv/bin/mypy --strict`, `.venv/bin/ruff check gateway_v2 state_control audit_control load_harness
  lint tests`, `.venv/bin/lint-imports`, the 6 AST gates
  (`.venv/bin/python -m lint.{check_capacity_literals,check_no_module_mutable,check_frozen_dataclasses,check_http_outside_edge_resolve,check_sizes,check_tenant_scale} gateway_v2`),
  and the unscanned-channels check (`unscanned_channels() == ()`). Gate 6 is after the version + release
  core + CLI + packaging edits, Gate 8 after the run-provenance wiring (confirming the shipped
  `RunReport` edit is non-breaking), Gate 10 the FINAL gate. The full suite must stay green: B6's
  1631-passed baseline + the new B7 tests.
- Capacity-position literals live only in `gateway_v2/runtime/resources.py`: a version string and a
  SHA-256 digest are release-identity / content-integrity values, NOT capacity literals (the B6
  named-constant category, no `CAPACITY_KEYWORDS`). All value types are frozen+slots with no
  module-level mutable (the canonical file set built inside a function); no HTTP type is introduced; the
  new modules import only the stdlib + `load_harness.report`; `root_packages` stays `["gateway_v2"]` so
  the harness sits outside the layer contract and adds no serving-path import edge.
- Shared-file coordination: `release.py` is written by §2.1/§2.2 (core) then extended by §3.1 (guard)
  and §4.1 (CLI) — these are serialized into successive waves (append-only to the same module). The
  property tests each write a DISTINCT file (`test_release.py`, `test_release_digest.py`,
  `test_verify.py`, `test_verify_fileset.py`, `test_provenance.py`, `test_version_authority.py`) so they
  do not collide and may run in parallel once their target source exists. `version.py` (§1.1) precedes
  the `__init__`/README derivation (§1.2) and the single-authority property (§9.2); the shipped-file
  edit (§7.1 `report.py` → §7.2 `dryrun.py`) is serialized because `dryrun` reads the new `RunProvenance`
  type.

## Deferred — explicitly NOT tasks (out of scope, local half only)

Per Requirement 7 and the design's Deferred section, the following are named seams with **no**
implementation task here; `tasks.md` delivers only the local, buildable half:

- **The `/dev/shm` `RemoveIPC=yes` host-configuration half of R2-20** (gateways run as a system user or
  in containers) — `Deferred_Host_Config`, a deployment / host-config decision owned by the deployer /
  T20, NOT `gateway_v2` code. This card introduces no `/dev/shm`, systemd, or host-config behavior
  (Req 7.1, 7.3).
- **The cloud validation-environment pieces of R2-20's amendment** — `Deferred_Cloud_Gate`, owned by
  T20 / fleet; named, not delivered here (Req 7.2).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "3.1"] },
    { "id": 4, "tasks": ["3.2", "3.3", "3.4", "4.1"] },
    { "id": 5, "tasks": ["4.2", "5.1", "7.1"] },
    { "id": 6, "tasks": ["7.2"] },
    { "id": 7, "tasks": ["7.3", "7.4", "9.1", "9.2"] },
    { "id": 8, "tasks": ["10.1"] }
  ]
}
```
