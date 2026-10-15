# Requirements Document

## Introduction

This spec covers card **B7 = R2-20 (partial) "immutable harness release hygiene"** — the locally
buildable half of R2-20. The authority is the R2-20 register row and the §3 v3 AMENDMENT (R2-13,
R2-20, R2-24) in `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`; the owning card for R2-20's
amendment is **T20**. The local-half framing is `docs/LOCAL_WORKLIST_v3.md`, block "B7".

R2-20 records **two** distinct defects, and this card deliberately delivers **only one** of them:

1. **OUT OF SCOPE — host configuration (the `/dev/shm` half).** systemd-logind `RemoveIPC=yes`
   wipes a login user's `/dev/shm` shared memory, which deleted the gateway's shared-memory metrics
   and made `/readyz` flap (3.2% 503). The runbook's correction for this half is operational:
   "Gateways run as a system user or in containers." That is a deployment / host-configuration
   decision owned by the deployer (an owner / T20 concern), **not** `gateway_v2` code, so this card
   does **not** turn it into a code requirement. It is named explicitly as deferred (Requirement 7).

2. **IN SCOPE — immutable harness release hygiene (the local, buildable half).** "Editing shared
   harness scripts in place killed a running measurement." The runbook's correction is that the
   measurement harness "is released immutably (as `bin/v2.N`) and runs from frozen copies." A
   capacity measurement is only trustworthy if it is attributable to a **known, unmodified** harness
   version; a round-2 measurement was invalidated because the harness source was edited mid-run. This
   card builds, for the shipped **B6 `load_harness`** package (B6 / R2-21): a runtime-discoverable
   **version stamp**, a **freeze / release mechanism** that captures the harness source as a named,
   immutable, versioned frozen copy, **byte-stability** of that frozen copy, and an **integrity
   guard** that detects a frozen copy mutated in place since it was frozen — so a mutated harness
   is refused or clearly flagged rather than silently measuring with changed code. A capacity run
   then records the harness release it ran under, so a result is attributable to an exact release.

The thing being versioned is the shipped `gateway_v2/load_harness/` package (modules `config.py`,
`arrivals.py`, `strata/*`, `request_plan.py`, `c4.py`, `ladder.py`, `report.py`, `driver.py`,
`dryrun.py`, `_dryrun_app.py`, `__main__.py`, `README.md`). Today it identifies as "v2.3" only in a
docstring — there is **no** version constant, **no** `bin/`, and **no** freeze / release / integrity
mechanism in the tree yet; this is greenfield.

This card honors the same **house conventions** as B1–B6. The mechanism lives in or alongside
`load_harness/` (which already sits **outside** the import-linter `root_packages = ["gateway_v2"]`),
uses frozen + slots value types where it carries value types, holds **no** capacity-position literal
outside `gateway_v2/runtime/resources.py` (a release version is not a host CPU/RAM/FD/RPS budget),
and keeps `mypy --strict`, `ruff`, the AST gates, and `unscanned_channels() == ()` green. It is
**deterministic and offline** (a freeze plus a content-digest check is pure and server-free), so the
worklist's named local verification — "a frozen harness build is byte-stable and an in-place edit is
rejected / detected" — is exercised with the house test idiom (seeded `random.Random`,
≥ 10,000-iteration property tests where a property fits, no hypothesis).

This is a **MEDIUM**, narrow **Category-1 (small)** card. It depends on B6 (the harness it versions)
and must **reconcile with**, not rebuild, B6's `RunReport` / `dryrun`.

## Glossary

- **Load_Harness**: the shipped `gateway_v2/load_harness/` package (B6 / R2-21) — the measurement
  instrument this card releases immutably.
- **Version_Stamp**: an explicit, machine-readable release identifier (e.g. `v2.3`) carried by the
  harness and discoverable at runtime, as opposed to a value stated only in a docstring.
- **Frozen_Release**: an immutable, named, versioned, read-only copy of the Load_Harness source
  captured by the release mechanism (the `bin/v2.N` artifact) and run from in place of the live tree.
- **Release_Mechanism**: the build / release step that captures the current Load_Harness source into
  a Frozen_Release under a given Version_Stamp.
- **Integrity_Manifest**: the record produced at freeze time that maps each file in the Frozen_Release
  to a content digest (e.g. a SHA-256 per file), used to verify the frozen copy has not changed.
- **Content_Digest**: a cryptographic hash of a file's bytes (and, aggregated, of the whole
  Frozen_Release) used to decide byte-stability and detect mutation.
- **Mutated_Copy**: a Frozen_Release whose on-disk bytes no longer match its Integrity_Manifest —
  i.e. a frozen copy that was edited in place after it was frozen.
- **Byte_Stable**: the property that re-freezing the same Load_Harness source under the same
  Version_Stamp yields a Frozen_Release with identical content (the same per-file and aggregate
  Content_Digest).
- **Integrity_Guard**: the check that verifies a Frozen_Release against its Integrity_Manifest before
  or at run time and refuses / flags a Mutated_Copy.
- **Run_Provenance**: the harness-version information a capacity run records so the resulting
  RunReport is attributable to an exact Frozen_Release.
- **Deferred_Host_Config**: the explicitly out-of-scope `/dev/shm` `RemoveIPC=yes` half of R2-20 — a
  deployment / host-configuration concern (gateways run as a system user or in containers) owned by
  the deployer / T20, not delivered by this card.
- **Deferred_Cloud_Gate**: a capability out of scope here because it requires cloud / fleet resources
  (e.g. the fleet-scale validation environments R2-20's amendment also mentions).

## Requirements

### Requirement 1: Runtime-discoverable version stamp

**User Story:** As a capacity engineer, I want the harness to carry an explicit, machine-readable
release version discoverable at runtime, so that I can tell exactly which released version produced a
measurement rather than inferring it from a docstring.

#### Acceptance Criteria

1. THE Load_Harness SHALL expose a machine-readable Version_Stamp as a value discoverable at runtime.
2. WHEN the Version_Stamp is queried at runtime, THE Load_Harness SHALL return the current released
   version identifier.
3. THE Version_Stamp SHALL be a frozen value type carrying the released version identifier.
4. WHERE the released version is referenced in documentation or a docstring, THE Load_Harness SHALL
   derive that reference from the single runtime Version_Stamp rather than restating the version as a
   separate literal.

### Requirement 2: Freeze / release mechanism producing an immutable versioned copy

**User Story:** As a capacity engineer, I want a build step that captures the harness source into a
named, versioned, read-only frozen copy, so that a measurement runs from a fixed artifact instead of
a live, editable tree.

#### Acceptance Criteria

1. WHEN the Release_Mechanism is invoked with the Load_Harness source and a Version_Stamp, THE
   Release_Mechanism SHALL produce a Frozen_Release that captures the source under that Version_Stamp.
2. THE Frozen_Release SHALL be identified by its Version_Stamp (the `bin/v2.N` naming).
3. THE Release_Mechanism SHALL produce an Integrity_Manifest mapping each file in the Frozen_Release
   to its Content_Digest.
4. WHEN the Release_Mechanism produces a Frozen_Release, THE Release_Mechanism SHALL record the
   Version_Stamp inside the Frozen_Release so the frozen copy reports the version it was released as.
5. IF the Release_Mechanism is invoked for a Version_Stamp that already has a Frozen_Release whose
   content differs from the source being frozen, THEN THE Release_Mechanism SHALL signal an error
   rather than overwrite the existing Frozen_Release.

### Requirement 3: Byte-stable, reproducible freezes

**User Story:** As a capacity engineer, I want re-freezing the same source to yield an identical
artifact, so that a release is reproducible and two freezes of unchanged code are indistinguishable.

#### Acceptance Criteria

1. WHEN the Release_Mechanism freezes the same Load_Harness source under the same Version_Stamp more
   than once, THE Release_Mechanism SHALL produce Frozen_Releases with identical per-file and
   aggregate Content_Digests.
2. IF the Load_Harness source differs between two freezes under the same Version_Stamp, THEN THE
   Release_Mechanism SHALL produce a different aggregate Content_Digest.
3. THE Integrity_Manifest SHALL list its files in a canonical order so that two freezes of identical
   source produce byte-identical manifests.

### Requirement 4: Integrity guard against a mutated frozen copy

**User Story:** As a capacity engineer, I want a run to detect that a frozen copy was edited in place
after it was frozen, so that a mutated harness is refused or flagged instead of silently measuring
with changed code (the exact failure that invalidated a round-2 measurement).

#### Acceptance Criteria

1. WHEN the Integrity_Guard verifies a Frozen_Release, THE Integrity_Guard SHALL recompute each file's
   Content_Digest and compare it against the Integrity_Manifest.
2. WHILE a Frozen_Release matches its Integrity_Manifest, THE Integrity_Guard SHALL report the
   Frozen_Release as intact.
3. IF a file in the Frozen_Release has content that differs from its Integrity_Manifest entry, THEN
   THE Integrity_Guard SHALL report the Frozen_Release as a Mutated_Copy.
4. IF a file listed in the Integrity_Manifest is missing from the Frozen_Release, OR a file present in
   the Frozen_Release is absent from the Integrity_Manifest, THEN THE Integrity_Guard SHALL report the
   Frozen_Release as a Mutated_Copy.
5. IF the Integrity_Guard reports a Frozen_Release as a Mutated_Copy at run time, THEN THE
   Load_Harness SHALL refuse the run or clearly flag the mutation rather than report the result as the
   released version.

### Requirement 5: Run provenance tied to an exact release

**User Story:** As a capacity engineer, I want a capacity run to record and verify the harness version
it ran under, so that a RunReport is attributable to an exact immutable release.

#### Acceptance Criteria

1. WHEN a capacity run executes from a Frozen_Release, THE Load_Harness SHALL record the
   Frozen_Release's Version_Stamp as Run_Provenance on the run's output.
2. WHEN a capacity run executes from a Frozen_Release, THE Load_Harness SHALL verify the
   Frozen_Release with the Integrity_Guard before measuring.
3. IF the Integrity_Guard reports a Mutated_Copy before a capacity run measures, THEN THE Load_Harness
   SHALL refuse the run or flag the result as non-attributable rather than record it as the released
   version.
4. THE Run_Provenance SHALL reconcile with the existing B6 `RunReport` / `dryrun` output rather than
   introduce a parallel report structure.

### Requirement 6: House conventions preserved

**User Story:** As a maintainer, I want the release-hygiene mechanism to honor the gateway_v2 house
rules, so that it integrates with B1–B6 without weakening the import, typing, or capacity invariants.

#### Acceptance Criteria

1. THE Release_Mechanism and Integrity_Guard SHALL reside in or alongside `load_harness/`, which sits
   outside the import-linter `root_packages = ["gateway_v2"]`, so that `gateway_v2` never imports the
   release mechanism.
2. WHERE the mechanism defines value types, THE mechanism SHALL define them as frozen + slots value
   types.
3. THE mechanism SHALL hold no capacity-position literal outside `gateway_v2/runtime/resources.py`.
4. THE mechanism SHALL keep `mypy --strict`, `ruff`, the AST gates, and `unscanned_channels() == ()`
   passing.
5. THE freeze and Content_Digest computation SHALL be deterministic and offline so that a freeze and
   an integrity check are testable without a running server.
6. WHERE a property-based test fits (byte-stability and Mutated_Copy detection), THE mechanism SHALL
   be verified with the house test idiom (a seeded `random.Random`, ≥ 10,000-iteration property test,
   no hypothesis).

### Requirement 7: Explicit out-of-scope boundary

**User Story:** As an owner, I want the host-configuration and cloud halves of R2-20 named as not
delivered here, so that the delivered scope is unambiguous and the deferred work has a clear owner.

#### Acceptance Criteria

1. THE Load_Harness SHALL document that the `/dev/shm` `RemoveIPC=yes` host-configuration half of
   R2-20 (gateways running as a system user or in containers) is Deferred_Host_Config owned by the
   deployer / T20 and is not delivered by this card.
2. THE Load_Harness SHALL document that the cloud validation-environment pieces of R2-20's amendment
   are Deferred_Cloud_Gates and are not delivered by this card.
3. THE Load_Harness SHALL NOT introduce any `/dev/shm`, systemd, or host-configuration behavior as
   part of this card.
