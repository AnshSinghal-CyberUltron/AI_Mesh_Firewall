# Requirements Document

## Introduction

This feature closes the locally-buildable secret-scan gaps named in the GW00 card
of the AI_Mesh_Firewall v3 backend rewrite runbook
(`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`, §10.9,
`## GW00 — Re-pin the baseline, resolve the credential exposure, stand up the v2
workspace and dependency gates`). It is "Option A" of the rewrite: the subset of
GW00 that can be built and verified entirely offline.

The runbook is the sole source of truth. The **C10 re-open correction** states,
verbatim, that "the secret scan misses PKCS#8 'BEGIN PRIVATE KEY', suffixed files,
dotfiles and history." The GW00 acceptance test **LGW00-2** requires that
committing a file containing an OpenSSH private-key header **with no extension**
is blocked locally (pre-commit) and fails the PR (CI). The GW00 evidence package
requires "CI run showing each gate failing on its negative fixture." The GW00
agent execution note requires that agents "must not print key material into logs,
PR descriptions or task output."

Two enforcement points exist today and both carry the C10 gaps:

- **CI scanner** — `gateway_v2/lint/check_secret_headers.py`, wired into
  `.github/workflows/gateway-v2.yml` as the `secret-headers` job (invoked as
  `python gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist`,
  plus an inline LGW00-2 throwaway-fixture check calling `scan_paths`).
- **Pre-commit guard** — `scripts/ralph/precommit-secret-scan.sh`, part (3)
  "GW00 — OpenSSH / PEM private-key headers in staged added lines."

The design tension that constrains the fix: the filename shape-gate exists so the
scanner does NOT flag the many source, documentation, and test-fixture files that
legitimately mention a PEM/OpenSSH header as a string (the scanner constructs the
needle itself; the bash guard assembles `_OS_HDR` at runtime; docs discuss headers;
redaction-test fixtures embed fake keys). The hardening must catch REAL private
keys in suffixed files and dotfiles WITHOUT lighting up on documentation, source,
or test fixtures.

Three concerns named in C10 and the GW00 card are DECLARED BOUNDARIES — named and
deferred here, not silently resolved (see Requirement 9).

## Glossary

- **GW00 card**: The GW00 task card in runbook §10.9. The authority for this spec.
- **C10**: The v2.1 CORRECTION (re-open) note at the head of the GW00 card and the
  C10 row of the §10.8 correction register, which names the four secret-scan gaps
  (PKCS#8, suffixed files, dotfiles, history).
- **LGW00-1**: GW00 live acceptance test — the exposed key filename
  (`ai-mesh-firewall`) must never be tracked again; it is always a hit.
- **LGW00-2**: GW00 live acceptance test — committing a file containing an OpenSSH
  private-key header with no extension must be blocked by pre-commit locally and
  fail the PR in CI.
- **CI_Scanner**: `gateway_v2/lint/check_secret_headers.py` — the authoritative
  Python scanner. Scans `git ls-files` tracked files. Reports paths only.
- **Precommit_Guard**: `scripts/ralph/precommit-secret-scan.sh` part (3) — the bash
  pre-commit guard that inspects the staged added diff for private-key headers.
- **Secret_Scanner**: Either enforcer when a requirement applies to both.
- **PKCS8_Header**: The byte sequence `BEGIN PRIVATE KEY` (unencrypted PKCS#8),
  with zero characters between `BEGIN ` and `PRIVATE KEY`.
- **OpenSSH_Header**: The byte sequence `BEGIN OPENSSH PRIVATE KEY`.
- **PEM_Header**: A header of the form `BEGIN <LABEL> PRIVATE KEY` where `<LABEL>`
  is one or more uppercase/space/digit characters (e.g. `RSA`, `EC`, `ENCRYPTED`).
- **Private_Key_Header**: Any of PKCS8_Header, OpenSSH_Header, or PEM_Header.
- **Key_Shaped_Candidate**: A file the CI_Scanner reads for header content. After
  this feature, this includes extensionless files, `.pem`/`.key`/`.secret` files,
  suffixed files with other extensions, and dotfiles — unless the path matches the
  Content_Allowlist.
- **Content_Allowlist**: The precise, tested set of path patterns (source, docs,
  and test-fixture paths) that are excused from being flagged even when they
  contain a Private_Key_Header string, because they only mention a header.
- **Suffixed_File**: A file whose name has an extension other than
  `.pem`/`.key`/`.secret` (e.g. `id_rsa.bak`, `leaked.txt`, `backup.old`,
  `key.json`).
- **Dotfile**: A file whose name begins with `.` (e.g. `.ssh_key`, `.id_rsa`).
- **Path_Only_Reporting**: Reporting a finding by file path with the body omitted;
  no byte of key material is emitted to any output channel.
- **History_Rewrite_Ticket**: The separate GW00 ticket the card directs be opened
  for git-history blob rewriting ("Open a history-rewrite ticket separately;
  deleting the file does not revoke access").

## Requirements

### Requirement 1: Detect unencrypted PKCS#8 `BEGIN PRIVATE KEY` headers (both enforcers)

**User Story:** As a security owner, I want the secret scan to detect the
unencrypted PKCS#8 `BEGIN PRIVATE KEY` header, so that the C10 gap "misses PKCS#8
'BEGIN PRIVATE KEY'" is closed at both enforcement points.

#### Acceptance Criteria

1. WHEN a Key_Shaped_Candidate contains the PKCS8_Header `BEGIN PRIVATE KEY` with
   zero characters between `BEGIN ` and `PRIVATE KEY`, THE CI_Scanner SHALL report
   a header hit for that file.
2. WHEN a staged added line in a key-shaped staged file contains the PKCS8_Header
   `BEGIN PRIVATE KEY`, THE Precommit_Guard SHALL report a header finding for that
   file and exit with a non-zero status.
3. WHEN a file contains a PEM_Header with a non-empty label (for example
   `BEGIN RSA PRIVATE KEY` or `BEGIN EC PRIVATE KEY`), THE Secret_Scanner SHALL
   report a header hit, preserving the pre-existing PEM detection behaviour.
4. WHEN a file contains the OpenSSH_Header `BEGIN OPENSSH PRIVATE KEY`, THE
   Secret_Scanner SHALL report a header hit, preserving the pre-existing OpenSSH
   detection behaviour.

### Requirement 2: Content-first scanning of suffixed files (CI scanner)

**User Story:** As a security owner, I want the CI scanner to read and flag files
with arbitrary extensions when they contain a real private-key header, so that the
C10 gap "misses suffixed files" is closed.

#### Acceptance Criteria

1. WHEN a Suffixed_File contains a Private_Key_Header AND the file path does not
   match the Content_Allowlist, THE CI_Scanner SHALL report a header hit for that
   file.
2. WHERE a file has one of the extensions `.pem`, `.key`, or `.secret`, THE
   CI_Scanner SHALL continue to treat the file as a Key_Shaped_Candidate and
   report a header hit when the file contains a Private_Key_Header.
3. IF a Suffixed_File does not contain a Private_Key_Header, THEN THE CI_Scanner
   SHALL report no hit for that file.

### Requirement 3: Content-first scanning of dotfiles (CI scanner)

**User Story:** As a security owner, I want the CI scanner to read and flag
dotfiles when they contain a real private-key header, so that the C10 gap "misses
dotfiles" is closed.

#### Acceptance Criteria

1. WHEN a Dotfile contains a Private_Key_Header AND the file path does not match
   the Content_Allowlist, THE CI_Scanner SHALL report a header hit for that file.
2. IF a Dotfile does not contain a Private_Key_Header, THEN THE CI_Scanner SHALL
   report no hit for that file.

### Requirement 4: False-positive avoidance via a precise, tested allowlist

**User Story:** As a backend engineer, I want documentation, source, and test
files that merely mention a private-key header to pass the scan, so that hardening
the scanner does not produce false positives on the existing repository.

#### Acceptance Criteria

1. WHERE a file path matches the Content_Allowlist (source, documentation, and
   test-fixture paths), THE CI_Scanner SHALL report no hit for that file even when
   the file contains a Private_Key_Header string.
2. WHEN the CI_Scanner runs against the current tracked file set of the repository,
   THE CI_Scanner SHALL report zero header hits.
3. THE Content_Allowlist SHALL extend, not replace, the existing precedent (the
   `_NON_KEY_NAMES` set in the CI_Scanner and the fixture allowlist pattern in the
   Precommit_Guard).
4. WHEN a Python source file (for example a file matching `test_*.py` or a module
   that constructs a header needle as a string) contains a Private_Key_Header
   string, THE CI_Scanner SHALL report no hit for that file.
5. WHEN a Markdown documentation file contains a Private_Key_Header string, THE
   CI_Scanner SHALL report no hit for that file.
6. WHEN a known test-fixture file (a path matching the test-fixture allowlist
   pattern) contains a Private_Key_Header string, THE CI_Scanner SHALL report no
   hit for that file.

### Requirement 5: Path-only reporting; no key material in output

**User Story:** As a security owner, I want the scanner to report findings by path
only, so that the GW00 agent constraint "must not print key material into logs, PR
descriptions or task output" is upheld.

#### Acceptance Criteria

1. WHEN the CI_Scanner reports a header hit, THE CI_Scanner SHALL emit the file
   path with the body omitted and SHALL emit no byte of the matched header or key
   material.
2. WHEN the Precommit_Guard reports a header finding, THE Precommit_Guard SHALL
   emit the file path with the body omitted and SHALL emit no byte of the matched
   header or key material.
3. THE CI_Scanner SHALL represent each finding as a path-only value (the frozen
   `HeaderHit` dataclass carrying only `path`), such that no key material is
   retained in a finding object.

### Requirement 6: Preserve LGW00-1 and LGW00-2 behaviour

**User Story:** As a security owner, I want the existing GW00 acceptance behaviour
preserved, so that hardening the scanner does not regress LGW00-1 or LGW00-2.

#### Acceptance Criteria

1. WHEN the forbidden key filename `ai-mesh-firewall` is tracked AND contains a
   Private_Key_Header, THE CI_Scanner SHALL report a header hit and the
   `--fail-allowlist` run SHALL exit with a non-zero status (LGW00-1 preserved).
2. WHEN an extensionless file contains the OpenSSH_Header, THE CI_Scanner SHALL
   report a header hit (LGW00-2 preserved at the CI gate).
3. WHEN an extensionless file containing the OpenSSH_Header is staged for commit,
   THE Precommit_Guard SHALL report a finding and exit with a non-zero status
   (LGW00-2 preserved at the pre-commit gate).
4. THE `KNOWN_UNTIL_LGW00_1` allowlist SHALL remain empty and
   `FORBIDDEN_KEY_FILENAMES` SHALL continue to contain `ai-mesh-firewall`.

### Requirement 7: Consistency between the bash guard and the Python scanner

**User Story:** As a security owner, I want the pre-commit bash guard and the CI
Python scanner to agree on what counts as a private-key header, so that LGW00-2
holds at both gates and the two enforcers do not diverge.

#### Acceptance Criteria

1. WHEN a staged added file reachable by the Precommit_Guard's key-shape logic
   contains a Private_Key_Header that the CI_Scanner would flag, THE Precommit_Guard
   SHALL also report a finding for that file.
2. THE Precommit_Guard SHALL detect the same set of header forms as the CI_Scanner
   (PKCS8_Header, OpenSSH_Header, and PEM_Header) for the staged files its key-shape
   logic reaches.
3. WHERE a file path matches the Precommit_Guard's fixture allowlist, THE
   Precommit_Guard SHALL report no finding for that file, mirroring the
   Content_Allowlist intent of the CI_Scanner.

### Requirement 8: CI gate stays green and continues to enforce

**User Story:** As a backend engineer, I want the `secret-headers` CI job and the
offline test suite to stay green with the hardening in place, so that the GW00 gate
keeps blocking real keys while admitting the existing repository.

#### Acceptance Criteria

1. WHEN the `secret-headers` job runs
   `python gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist`
   against the current repository, THE CI_Scanner SHALL exit with a zero status.
2. THE `secret-headers` job SHALL retain (or strengthen) the inline LGW00-2
   throwaway-fixture check that calls `scan_paths` and fails if no hit is reported.
3. WHEN the CI_Scanner source is checked by `ruff` and `mypy --strict`, THE
   CI_Scanner SHALL pass both with no new errors, retaining
   `from __future__ import annotations`, the frozen `HeaderHit` dataclass, and the
   path-only reporting style.
4. THE feature SHALL add tests that keep the offline suite green and SHALL NOT
   weaken any existing assertion to make a check pass.

### Requirement 9: Non-goals and declared deferred boundaries

**User Story:** As a security owner, I want the concerns outside this local
hardening named and deferred explicitly, so that no reader mistakes a declared
boundary for a silently resolved gap.

#### Acceptance Criteria

1. THE feature SHALL treat full git-history blob scanning and the history rewrite
   of the already-removed `ai-mesh-firewall` key as the separate GW00
   History_Rewrite_Ticket, out of scope for this spec (the GW00 card directs:
   "Open a history-rewrite ticket separately; deleting the file does not revoke
   access").
2. THE feature SHALL treat branch protection, required-checks configuration,
   reproducible-image digest comparison (LGW00-6), and the Node SDK conformance
   matrix as CI/infra and GW01 concerns, out of scope for this local hardening.
3. THE feature SHALL treat credential classification, revocation, and rotation
   (LGW00-1 live procedure) as requiring a human with infrastructure authority,
   out of scope for the coding agent (the GW00 card: "Agents must not print key
   material"; the agent execution note names classification and revocation as the
   human-only exception).
4. THE CI_Scanner SHALL continue to scan `git ls-files` tracked files only and
   SHALL NOT attempt to scan deleted-in-history blobs within this feature.
