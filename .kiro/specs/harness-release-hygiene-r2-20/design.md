# Design Document — B7 / R2-20 (partial): immutable harness release hygiene

## Overview

B7 (R2-20, partial) delivers the **local, buildable half** of R2-20 for the shipped B6
`gateway_v2/load_harness/` package: a runtime-discoverable **version stamp**, a **freeze / release
mechanism** that captures the harness source into a named, immutable, versioned frozen copy, the
**byte-stability** of that frozen copy, an **integrity guard** that detects a frozen copy mutated in
place, and **run provenance** that records the exact release a capacity run executed under. The
failure this fixes is concrete: "editing shared harness scripts in place killed a running
measurement" — a round-2 measurement was invalidated because the harness source was edited mid-run.
A capacity number is only trustworthy if it is attributable to a **known, unmodified** harness
release; this card makes that attribution a first-class, verifiable property.

**The two-defect split.** R2-20 records two distinct defects and this card deliberately delivers
**only one**:

- **IN SCOPE — immutable harness release hygiene (local, buildable).** The runbook's correction is
  that the measurement harness "is released immutably (as `bin/v2.N`) and runs from frozen copies."
  This is a deterministic, offline, server-free capability (a freeze plus a content-digest check),
  so it is built and verified here with the house test idiom (Req 1–6).
- **OUT OF SCOPE — host configuration (the `/dev/shm` half).** systemd-logind `RemoveIPC=yes` wipes
  a login user's `/dev/shm`, wiping the gateway's shared-memory metrics and flapping `/readyz`. The
  runbook's correction ("gateways run as a system user or in containers") is a **deployment /
  host-configuration** decision owned by the deployer (T20), **not** `gateway_v2` code. It is named
  explicitly as `Deferred_Host_Config` and nothing is built for it (Req 7; §Deferred).

**Authority and source of truth.** The binding authority is the **R2-20** register row and the §3 v3
AMENDMENT (R2-13, R2-20, R2-24) in `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` (owning card
for the amendment: **T20**); the local framing is `docs/LOCAL_WORKLIST_v3.md` block "B7". The 7
approved requirements in `requirements.md` are the proximate contract and every section below traces
to them (see §Traceability). Where the runbook is silent on a purely local detail (the exact freeze
layout, the manifest format), this design makes an in-spec decision and justifies it.

**Design-time reconciliation (greenfield + one correction).** There is **no** version constant,
**no** `bin/`, and **no** freeze / release / integrity mechanism in the tree yet — this is
greenfield **except** one reconciliation: `load_harness/__init__.py` and `README.md` identify the
harness as "v2.3" only in prose today. This card makes a single runtime `Version_Stamp` the
**authority** and has the docstring/README **derive** from it rather than restating a separate
literal (Req 1.4).

**Scope posture.** B7 is a **MEDIUM**, narrow **Category-1 (small)** card. It depends on B6 (the
harness it versions) and must **reconcile with**, not rebuild, B6's `RunReport` / `dryrun`. The
mechanism lives in/alongside `load_harness/` (outside `root_packages = ["gateway_v2"]`), uses
frozen+slots value types, holds no capacity-position literal, and keeps `mypy --strict`, `ruff`, the
AST gates, `lint-imports`, and `unscanned_channels() == ()` green (Req 6).

---

## Architecture

### Placement — inside `load_harness/`, still outside the layer contract (Req 6.1)

The release-hygiene mechanism is part of the measurement instrument, not a serving-path layer, so it
lands as new modules **inside** the existing top-level `load_harness/` package (sibling to
`state_control/` / `audit_control/`), which already sits **outside** the import-linter
`root_packages = ["gateway_v2"]`. No new serving-path import edge is created: `gateway_v2` never
imports `load_harness`, and the new modules import only the stdlib (`hashlib`, `pathlib`, `json`,
`dataclasses`) plus — for the one `RunReport` field — the already-shipped `load_harness.report`
value type. `[tool.importlinter].root_packages` stays `["gateway_v2"]`, **unchanged**.

### The frozen-artifact location — the critical packaging/gate hazard, resolved

**The hazard.** A `bin/<version>/` directory full of **copied `.py` files INSIDE `load_harness/`**
would be swept up by three tools: setuptools `find` (the `load_harness*` include), `ruff` + `mypy`
(both list `load_harness` as a source root / files entry), and would also be re-walked by a later
`freeze` as if it were live source (recursive self-freezing). It would be type-checked and linted as
live source — exactly what an *immutable, byte-frozen snapshot of possibly-older code* must NOT be.

**The decision — freeze to a single `.tar` archive, not a tree of `.py` files.** The
`Release_Mechanism` writes each `Frozen_Release` as **one `bin/<version>/harness.tar`** archive plus
a sibling **`bin/<version>/MANIFEST.json`** (a non-`.py`, data-only integrity manifest). Because the
frozen source is captured as archive **bytes** (not importable `.py` modules on disk), it is
invisible to setuptools package discovery, to ruff's and mypy's `.py` collection, and to the AST
gates (which only parse `.py`), and a re-freeze reads the **live** source tree (never the `bin/`
archive), so recursive self-freezing is impossible by construction. The archive is also the natural
unit of byte-stability: "same source → identical archive bytes → identical aggregate digest" (Req 3).

**Where `bin/` lives.** `gateway_v2/load_harness/bin/<version>/` (co-located with the package it
versions, matching the runbook's `bin/v2.N` phrasing). To guarantee the directory is never
mistaken for source even though it is non-`.py`, the design pins it closed with **belt-and-braces
exclusions** (listed next). The tar members use **relative POSIX paths** under a stable root
(`load_harness/<module>`), stored with **normalized metadata** (fixed mtime `0`, fixed mode, fixed
uid/gid/uname/gname, sorted member order) so byte-stability depends only on file **content** and the
canonical member set, never on filesystem mtimes or walk order (Req 3; determinism hazards called out
in §Error Handling).

### Exact `pyproject.toml` / ignore edits (Req 6.4)

The frozen artifact is data (tar + json), not Python, so no tool *should* pick it up — but the design
makes that explicit and provable rather than incidental:

```toml
# gateway_v2/pyproject.toml

[tool.setuptools.packages.find]
# UNCHANGED include line; ADD an exclude so a bin/<version>/ dir can never be discovered as a package
# even if a stray __init__.py or .py ever lands there.
include = ["gateway_v2*", "state_control*", "audit_control*", "load_harness*", "lint*"]
exclude = ["load_harness.bin*"]

[tool.ruff]
# ADD the frozen-artifact tree to extend-exclude so ruff never lints a frozen copy.
extend-exclude = [ ... existing entries ... , "load_harness/bin" ]

[tool.mypy]
# ADD a per-module exclude so mypy --strict never type-checks a frozen copy.
exclude = ["load_harness/bin/"]

[tool.importlinter]
root_packages = ["gateway_v2"]   # UNCHANGED — load_harness (and its bin/) stay outside the contract
```

```gitignore
# gateway_v2/.gitignore  (or repo-root .gitignore scoped to the path)
# A Frozen_Release is a build artifact: reproducible from source via `python -m load_harness.release`.
# It is NOT committed, so it can never pollute the source tree, review, or the gates.
gateway_v2/load_harness/bin/
```

Rationale for not committing `bin/`: a `Frozen_Release` is **reproducible** from source (Req 3
byte-stability guarantees `re-freeze == freeze`), so it is a generated build artifact. Keeping it out
of git removes the last way a frozen `.py`-less archive could still appear in diffs or tooling scope.
The AST gates run over `gateway_v2` `.py` sources only and never parse tar/json, so they are
unaffected regardless; the mypy/ruff/setuptools excludes above close the remaining theoretical paths.

### Module map (new/edited pieces)

| Module | Status | Responsibility | Key frozen+slots value types |
|---|---|---|---|
| `load_harness/version.py` | **new** | The single source of truth for the harness release identifier: a frozen `Version` value + the `HARNESS_VERSION` constant. Pure, stdlib-only. | `Version` |
| `load_harness/release.py` | **new** | Pure freeze/manifest/verify functions: enumerate the canonical source file set, build the tar + `Integrity_Manifest`, verify a frozen copy, classify a `Mutated_Copy`. No I/O policy decisions beyond reading/writing the artifact. | `ManifestEntry`, `IntegrityManifest`, `IntegrityResult`, `FrozenRelease` |
| `load_harness/__main__release` → `release.py:main()` + a `python -m load_harness.release` entry | **new** | Thin CLI around `release.py` (mirrors `state_control`/`audit_control` `__main__` shape): `freeze` / `verify` subcommands returning an int exit code. | — |
| `load_harness/report.py` | **edited** | Add ONE optional provenance field (see §Run provenance). | `RunProvenance` (new), `RunReport` (one field added) |
| `load_harness/dryrun.py` | **edited** | Verify the release it runs from and thread the `RunProvenance` onto the built reports (see §Run provenance). | — |
| `load_harness/__init__.py`, `README.md` | **edited** | Derive the "v2.3" prose reference from `HARNESS_VERSION` (Req 1.4). | — |

Every new module stays ≤ 800 lines and every function ≤ 120 lines (Req 6; `check_sizes`).

### Gate posture (Req 6.2–6.4)

| Gate | How this card satisfies it |
|---|---|
| `check_capacity_literals.py` | The card introduces no capacity-position literal. A **version string** (`"v2.3"`) and a **SHA-256 digest** are release-identity / content-integrity values, not a host CPU/RAM/FD/RPS budget — the same named-constant class as B6's `_DEFAULT_NUM_REQUESTS` / stratum floors, and carrying none of the `CAPACITY_KEYWORDS` the gate scans for. (The gate parses `gateway_v2` `.py` only and never reads the tar/json artifact.) |
| `check_no_module_mutable.py` | No module-level `list`/`dict`/`set`. The canonical file set is built inside a function; the version constant is a frozen `Version` instance (not a mutable), like B6's `GpuBusy.UNAVAILABLE`. |
| `check_frozen_dataclasses.py` | `Version`, `ManifestEntry`, `IntegrityManifest`, `IntegrityResult`, `FrozenRelease`, `RunProvenance` are all `@dataclass(frozen=True, slots=True)`. |
| `check_http_outside_edge_resolve.py` | No HTTP type is introduced; the mechanism is offline and server-free. |
| `check_sizes.py` | Modules ≤ 800 lines, functions ≤ 120 lines. |
| `check_tenant_scale.py` / `check_secret_headers.py` | Not touched. |
| `lint-imports` | Unchanged — `load_harness` stays outside `root_packages`; new imports are stdlib + `load_harness.report`. |
| `unscanned_channels() == ()` | Unchanged — the card adds no channel. |

---

## Components and Interfaces

### Version stamp — the single authority (Req 1)

```python
# load_harness/version.py
@dataclass(frozen=True, slots=True)
class Version:
    """The harness release identifier as a frozen value (Req 1.3)."""
    identifier: str          # e.g. "v2.3"

    def __str__(self) -> str:
        return self.identifier

#: The ONE source of truth. __init__.py's docstring/README derive from this (Req 1.4);
#: nothing restates the version as a separate literal.
HARNESS_VERSION: Version = Version(identifier="v2.3")
```

- `HARNESS_VERSION` is importable and discoverable at runtime (Req 1.1, 1.2): `__main__`, `dryrun`,
  and `release` all read `version.HARNESS_VERSION` — no module hardcodes `"v2.3"` again.
- `__init__.py` exposes the stamp (e.g. `__version__ = str(version.HARNESS_VERSION)` and adds it to
  `__all__`) and its module docstring states that the authoritative version lives in `version.py`
  (prose derives from the stamp; Req 1.4). The README's "v2.3" line is annotated as derived from
  `HARNESS_VERSION`.

### Release mechanism — freeze + manifest (Req 2, 3)

```python
# load_harness/release.py  (pure functions + thin CLI)

#: Canonical, deterministic source file set — the B6 package modules, in sorted POSIX-relative order.
#: Excludes __pycache__, the bin/ artifact tree itself, and any test file, so a freeze captures ONLY
#: the live harness source and can never recursively capture a prior Frozen_Release (Req 2, 3.3).
def iter_source_files(package_root: Path) -> tuple[Path, ...]: ...

@dataclass(frozen=True, slots=True)
class ManifestEntry:
    relpath: str             # POSIX-relative, under load_harness/
    sha256: str              # hex digest of the file's CONTENT bytes (never metadata)

@dataclass(frozen=True, slots=True)
class IntegrityManifest:
    version: str             # the Version_Stamp this release was frozen as (Req 2.4)
    entries: tuple[ManifestEntry, ...]   # sorted by relpath (canonical order, Req 3.3)
    aggregate_sha256: str    # digest over the canonical concatenation of (relpath, per-file sha256)

    def to_json(self) -> str: ...               # byte-stable: sorted keys, trailing newline fixed
    @classmethod
    def from_json(cls, text: str) -> IntegrityManifest: ...

@dataclass(frozen=True, slots=True)
class FrozenRelease:
    version: str
    archive_path: Path       # bin/<version>/harness.tar
    manifest_path: Path      # bin/<version>/MANIFEST.json

def freeze(package_root: Path, version: Version, dest_root: Path) -> FrozenRelease: ...
```

- `freeze(...)` enumerates `iter_source_files` (canonical sorted set), writes **one normalized tar**
  (fixed mtime/mode/ownership, sorted members — see Architecture) and a sibling `MANIFEST.json`
  mapping each file to its content SHA-256 plus an aggregate digest; it stamps the `Version` **inside**
  the manifest (Req 2.1, 2.3, 2.4). The frozen copy is identified by its version via the
  `bin/<version>/` path (Req 2.2).
- **Refuse-to-overwrite-a-differing-release (Req 2.5).** If `bin/<version>/` already exists, `freeze`
  recomputes the manifest for the current source and compares its `aggregate_sha256` to the existing
  manifest: **identical → idempotent no-op** (byte-stable re-freeze, Req 3.1); **different → raise**
  `ReleaseExists` (never overwrite). This is the house fail-closed posture.
- **Byte-stability (Req 3).** Digests are of **content**, member order is canonical, and tar metadata
  is normalized, so re-freezing identical source yields identical per-file and aggregate digests and a
  byte-identical manifest (Req 3.1, 3.3); any source difference changes the aggregate digest (Req 3.2).

### Integrity guard — detect a mutated frozen copy (Req 4)

```python
@dataclass(frozen=True, slots=True)
class IntegrityResult:
    intact: bool
    mutated_files: tuple[str, ...]   # content mismatch
    missing_files: tuple[str, ...]   # in manifest, absent from the frozen archive
    extra_files: tuple[str, ...]     # in the frozen archive, absent from manifest

    @property
    def is_mutated_copy(self) -> bool:
        return bool(self.mutated_files or self.missing_files or self.extra_files)

def verify(frozen: FrozenRelease) -> IntegrityResult: ...
```

- `verify(...)` reads the frozen archive + its `MANIFEST.json`, recomputes each member's content
  SHA-256, and compares against the manifest (Req 4.1): all match → `intact=True` (Req 4.2); any
  content mismatch → the file is a mutation (Req 4.3); a manifest file missing from the archive, or an
  archive file absent from the manifest → `Mutated_Copy` (Req 4.4). `verify` is **pure** over the
  artifact bytes — offline and server-free (Req 6.5).
- **Fail-closed at run time (Req 4.5).** The chosen behavior is **refuse** (prefer refuse over flag):
  when a run executes from a `Frozen_Release`, the harness verifies it first and raises
  `MutatedCopyError` on `is_mutated_copy` rather than measuring with changed code and reporting it as
  the released version. (See §Run provenance for the run-time wiring and §Error Handling.)

### Run provenance — reconcile with B6's `RunReport`/`dryrun`, do not rebuild (Req 5)

**Decision: add ONE optional field to the shipped `report.RunReport` and thread it through `dryrun`,
rather than carry provenance on `DryRunResult` only.** Rationale: Req 5.1 requires provenance **on the
run's output** and Req 5.4 requires it to **reconcile with the existing `RunReport`/`dryrun`** rather
than introduce a parallel report structure. A per-run `RunReport` is exactly "the run's output"; a
`RunProvenance` on `DryRunResult` alone would be a parallel, run-level structure that does not travel
with each report. The least-invasive change that satisfies both is a single new frozen value carried
on each `RunReport`.

```python
# load_harness/report.py  (new value type + ONE added field)
@dataclass(frozen=True, slots=True)
class RunProvenance:
    """The release a run was measured under (Req 5.1), reconciled onto the existing RunReport."""
    harness_version: str      # the Frozen_Release's Version_Stamp
    aggregate_sha256: str     # the verified aggregate digest of the release that produced the run
    integrity_intact: bool    # the Integrity_Guard verdict at run time (Req 5.2)

@dataclass(frozen=True, slots=True)
class RunReport:
    ...                       # the 15 shipped fields, unchanged
    provenance: RunProvenance | None = None   # NEW, optional; None for an un-provenanced offline run
```

- The field is **optional with a `None` default** so every existing `RunReport` construction site and
  test that does not supply provenance keeps compiling and passing — the only change to shipped code is
  additive. **Design consequence (flagged):** `report.py` gains `RunProvenance` + the field; `dryrun`'s
  `_build_report` gains a `provenance=` argument; the shipped `report` tests gain coverage for the new
  field. These are the exact, bounded edits to a shipped frozen dataclass and its construction site —
  noted so task-planning updates them together.
- **Wiring in `dryrun` (Req 5.1–5.3).** Before measuring, `run_dry_run_async` resolves the
  `Frozen_Release` it runs from, calls `verify(...)`, and:
  - **intact** → builds each `RunReport` with `provenance=RunProvenance(version, aggregate_sha256,
    integrity_intact=True)` (Req 5.1, 5.2);
  - **mutated** → **refuses the run** (`raise MutatedCopyError`) rather than recording a result as the
    released version (Req 5.3, 4.5).
  The default local dry-run (which runs from the live tree, not a frozen copy) attaches a provenance
  carrying `HARNESS_VERSION` and the live-source aggregate digest with `integrity_intact=True`, so a
  report is always attributable; running from an actual `bin/<version>/` archive is the frozen-copy
  path that `verify` guards.

---

## Data Models

All value types are `@dataclass(frozen=True, slots=True)` (Req 6.2). Consolidated index:

| Module | Value type | Notes |
|---|---|---|
| `version.py` | `Version` | the frozen release identifier; `HARNESS_VERSION` is the single authority (Req 1.3, 1.4) |
| `release.py` | `ManifestEntry` | `(relpath, sha256)` — per-file content digest (Req 2.3) |
| `release.py` | `IntegrityManifest` | version + sorted entries + aggregate digest; byte-stable JSON (Req 2.3, 3.3) |
| `release.py` | `FrozenRelease` | the `bin/<version>/` artifact handle (archive + manifest paths) (Req 2.2) |
| `release.py` | `IntegrityResult` | intact flag + mutated/missing/extra file tuples (Req 4.2–4.4) |
| `report.py` | `RunProvenance` | **new** — version + aggregate digest + integrity verdict, carried on `RunReport` (Req 5.1, 5.2) |
| `report.py` | `RunReport` | **edited** — one optional `provenance` field added; 15 shipped fields unchanged (Req 5.4) |

Reused (not redefined): `load_harness.report.RunReport` / `DryRunResult` (B6) — reconciled, not
rebuilt. No capacity/SLO number is a field here (Req 6.3).

## Correctness Properties

*A property is a characteristic that should hold true across all valid executions of the system — a
formal statement bridging the human-readable spec and machine-verifiable correctness.* The freeze,
digest, and verify functions are **pure, deterministic, and offline**, so these are exercised with the
house idiom: seeded `random.Random`, ≥ 10,000 iterations, no hypothesis (Req 6.5, 6.6).

### Property 1: Re-freeze is byte-identical
*For any* source file set (randomly generated file contents and names), freezing it twice under the
same `Version` produces identical per-file digests, an identical aggregate digest, and a byte-identical
`IntegrityManifest` JSON. **Validates: Requirements 3.1, 3.3**

### Property 2: Any single-byte mutation is detected
*For any* frozen release and *any* single-byte change to the content of *any* one frozen file, `verify`
reports `is_mutated_copy` with that file in `mutated_files` (never `intact`).
**Validates: Requirements 4.1, 4.3**

### Property 3: Missing or extra files are detected
*For any* frozen release, removing *any* manifest-listed file from the archive, or adding *any* file not
in the manifest, causes `verify` to report a `Mutated_Copy` (in `missing_files` / `extra_files`
respectively); an untouched release verifies as intact. **Validates: Requirements 4.2, 4.4**

### Property 4: Differing source yields a differing aggregate digest
*For any* two source file sets that differ in at least one file's content, freezing each under the same
`Version` produces different aggregate digests (and `freeze` into an existing differing release raises
rather than overwrites). **Validates: Requirements 3.2, 2.5**

### Property 5: Run provenance records the exact release and refuses a mutated copy
*For any* run that executes from a `Frozen_Release`: if the release is intact, every produced
`RunReport` carries a `RunProvenance` whose `harness_version` and `aggregate_sha256` equal the frozen
release's stamp and verified digest; if the release is a `Mutated_Copy`, the run is refused (no
`RunReport` is recorded as the released version). **Validates: Requirements 5.1, 5.2, 5.3, 4.5**

### Property 6: The version stamp is the single authority
*For any* reference to the release version in the package, the value equals `version.HARNESS_VERSION`
(the manifest's `version`, the report's `harness_version`, and the `__init__`/README prose all derive
from the one stamp; no second literal diverges). **Validates: Requirements 1.1, 1.2, 1.4, 2.4**

## Error Handling

- **Fail-closed on mutation (Req 4.5, 5.3).** A `Mutated_Copy` detected at run time raises
  `MutatedCopyError` and the run is **refused** — the harness never records a measurement from changed
  code as the released version. (Design choice: refuse over flag, since a mutated harness invalidates
  the measurement outright; this is the exact failure R2-20 cites.)
- **Refuse-to-overwrite-a-differing-release (Req 2.5).** `freeze` into an existing `bin/<version>/`
  whose content differs raises `ReleaseExists`; an identical re-freeze is an idempotent no-op (Req 3.1).
- **Determinism hazards, neutralized.** Byte-stability depends only on **content** and the canonical
  member set, never on filesystem metadata: tar members are written with fixed mtime/mode/ownership and
  **sorted** order, the manifest lists files in sorted POSIX-relative order with a fixed trailing
  newline and sorted JSON keys, and digests are over file **bytes** (never mtimes). Any of these left
  to the OS (walk order, real mtimes, trailing-whitespace drift) would break Req 3 — each is pinned.
- **Malformed / absent artifact.** `verify` on a missing archive or unreadable/invalid `MANIFEST.json`
  fails closed (treated as non-attributable: `is_mutated_copy` or a raised error), never silently
  "intact".
- **Honest provenance for a non-frozen local run.** A dry-run from the live tree attaches a provenance
  carrying `HARNESS_VERSION` + the live-source aggregate digest with `integrity_intact=True`, so a
  report is always attributable; it never claims to be a frozen release it is not.

## Testing Strategy

### Property-based testing applies here

Freeze/manifest/verify are **pure logic over a large input space** (arbitrary file contents, names,
mutations, add/remove) — the archetype PBT fits. House idiom: seeded `random.Random`, ≥ 10,000
iterations, **no hypothesis** (Req 6.6). Each property in §Correctness Properties maps to one property
test, tagged **Feature: harness-release-hygiene-r2-20, Property N: {property text}**. The run-provenance
property (Property 5) drives the dry-run wiring under `asyncio.run`, using an in-memory / tmp-dir
frozen release so no server is required (Req 6.5).

### Unit + integration tests
- **Unit tests**: `ReleaseExists` on a differing re-freeze; idempotent no-op on an identical re-freeze;
  `verify` on an intact release / a content mutation / a missing file / an extra file / a malformed
  manifest; `HARNESS_VERSION` discoverable at runtime; `RunReport` with `provenance=None` (the shipped
  default path) still constructs; the `__init__`/README prose equals the stamp.
- **Reconciliation test**: a `RunReport` built with a `RunProvenance` round-trips every shipped field
  unchanged plus the new field (guards the additive, non-breaking edit to the shipped dataclass).

### Gate / pollution proof (Req 6.4)
A dedicated test proves the frozen artifact location does **not** pollute tooling: after a `freeze`
into a tmp `bin/<version>/`, assert (a) setuptools package discovery over the tree does **not** list a
`load_harness.bin*` package, (b) the `.py`-only AST gates and mypy/ruff file selection do not include
any path under `bin/`, and (c) a second `freeze` reads the **live** source set (never the `bin/`
archive), so no recursive self-freeze occurs. The AST gates, `lint-imports`, and
`unscanned_channels() == ()` stay green and unchanged.

## Deferred

Named seams only — nothing below is built or scaffolded by this card.

| Deferred item | Owner | Status |
|---|---|---|
| `/dev/shm` `RemoveIPC=yes` host-configuration half (gateways run as a system user or in containers) — `Deferred_Host_Config` | **deployer / T20** | out of scope; a deployment / host-config decision, not `gateway_v2` code (Req 7.1, 7.3). This card introduces **no** `/dev/shm`, systemd, or host-config behavior. |
| Cloud validation-environment pieces of R2-20's amendment — `Deferred_Cloud_Gate` | **T20 / fleet** | named; not delivered here (Req 7.2) |

## Traceability

| Design section | Requirements |
|---|---|
| Overview (two-defect split, authority, reconciliation) | Req 7, intro of 1–5 |
| Architecture → Placement | Req 6.1 |
| Architecture → Frozen-artifact location + pyproject/ignore edits + gate posture | Req 6.3, 6.4; Req 2.2, 3.3 |
| Components → Version stamp | Req 1.1, 1.2, 1.3, 1.4 |
| Components → Release mechanism | Req 2.1–2.5, 3.1–3.3, 6.5 |
| Components → Integrity guard | Req 4.1–4.5, 6.5 |
| Components → Run provenance | Req 5.1–5.4, 4.5 |
| Data Models | Req 6.2, 6.3 |
| Correctness Properties | Req 1, 2.4, 2.5, 3, 4, 5 (6.6) |
| Error Handling | Req 2.5, 4.5, 5.3, 3 |
| Testing Strategy | Req 6.4, 6.5, 6.6 |
| Deferred | Req 7.1, 7.2, 7.3 |
