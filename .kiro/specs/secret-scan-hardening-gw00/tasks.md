# Implementation Plan: Secret-Scan Hardening (GW00 / C10)

## Overview

This plan closes the three locally-buildable C10 secret-scan gaps (PKCS#8
`BEGIN PRIVATE KEY`, suffixed files, dotfiles) at both enforcement points without
regressing LGW00-1 or LGW00-2, and with zero false positives on the current tracked
tree. The GW00 card + C10 row in
`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md` (§10.9/§10.8) is the sole source
of truth.

The implementation order mirrors the design's dependency chain: land the two
content-first production edits (CI_Scanner D1/D2, Precommit_Guard D1/D4) and the CI
workflow strengthening first, then write the enumerated test matrix (one single-file
Python gate task + one separate bash scratch-repo task), then run the authoritative
exit gate, then checkpoint. The CI_Scanner edit (Task 1) is content-first and includes
the mandatory **zero-hit triage loop** from design §D2 — the hardened scanner is run
over the real tracked tree and every reported path is triaged into a Content_Allowlist
*category* (by pattern) until the run exits 0; only after that loop converges can the
T-ZERO test be expected to pass.

Both enforcers detect the same three header forms (D1); they differ only in scope
(full tree vs staged added diff), which is an expected, accepted structural difference
(D4, Requirement 7).

## Tasks

- [x] 1. Harden the CI_Scanner (`gateway_v2/lint/check_secret_headers.py`) to content-first detection with a path-pattern Content_Allowlist
  - Change `_PEM` to the optional-label form `re.compile(rb"BEGIN (?:[A-Z0-9][A-Z0-9 ]{0,64} )?PRIVATE KEY")` so bare PKCS#8 `BEGIN PRIVATE KEY` matches via the empty branch while `BEGIN RSA/EC/ENCRYPTED PRIVATE KEY` still match via the label branch; keep `_OPENSSH` as the assembled-fragment needle (D1).
  - Rewrite `is_key_shaped_filename` body (D2): forbidden filename → `True`; `.pem/.key/.secret` → `True`; `_NON_KEY_NAMES` / `Dockerfile*` / `Makefile*` → `False`; **everything else → `True`**. Delete the `if suffix: return False` and `if name.startswith("."): return False` early-returns so suffixed files and dotfiles become readable candidates.
  - Add a module-level `Content_Allowlist: tuple[re.Pattern[str], ...]` of compiled repo-relative POSIX path-pattern regexes covering the categories in design §D2 (the two enforcers `^gateway_v2/lint/`, `^scripts/ralph/`; source extensions; `^docs/` + `\.md$`; `^\.kiro/specs/`; the test-fixture patterns). Consult it in `scan_paths` *before* the shape gate, in addition to the passed `allowlist` basename set (back-compat: `scan_paths([blob], allowlist=set())` must still work).
  - Keep `scan_repo`, `main`, frozen `HeaderHit`, `FORBIDDEN_KEY_FILENAMES`, empty `KNOWN_UNTIL_LGW00_1`, `_NON_KEY_NAMES`, `from __future__ import annotations`, path-only prints, and `except OSError: return False` intact; `known_present` stays allowlist-independent so `ai-mesh-firewall` remains a hit (LGW00-1).
  - **Mandatory zero-hit triage loop (design §D2):** run `python gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist` over the real tree; for every reported path, widen the matching Content_Allowlist *pattern* by category (never add one-off basenames unless a file has no categorical home); repeat until the run exits 0 with no hits. This loop MUST converge as part of this task — it is the precondition for Task 4's T-ZERO.
  - Keep the module `ruff` + `mypy --strict` clean (the `lint` package is in the mypy `files` list; type the new tuple as `tuple[re.Pattern[str], ...]`).
  - _Requirements: 1.1, 1.3, 1.4, 2.1, 2.2, 2.3, 3.1, 3.2, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.3, 6.1, 6.2, 6.4, 8.3_

- [x] 2. Harden the Precommit_Guard (`scripts/ralph/precommit-secret-scan.sh` part (3))
  - Change `_PEM_RE` to the optional-label ERE form `"BEGIN ([A-Z0-9][A-Z0-9 ]* )?PRIVATE KEY"` so staged bare `BEGIN PRIVATE KEY` is caught; keep `_OS_HDR` assembled from fragments (D1).
  - In the part-(3) `case "$f"` block, remove the `*.*) continue ;;` and `.*) continue ;;` skip arms so staged suffixed files and dotfiles are inspected; keep `*.pem|*.key|*.secret) ;;` as the first arm and keep the build-file `continue` arms.
  - Extend the part-(3) `fixture_re` to mirror the Content_Allowlist intent (D4): add at minimum `\.py$`, `\.md$`, `\.sh$`, `docs/`, `\.kiro/specs/` to the existing fixture patterns so staged source/doc/spec mentions are not findings.
  - Leave parts (1) live-key fingerprint and (2) generic-token untouched; keep `set -uo pipefail`, the `|| true` on the diff/grep pipelines, and path-only `note` output (body omitted).
  - _Requirements: 1.2, 3.1, 5.2, 7.1, 7.2, 7.3_

- [x] 3. Strengthen the `secret-headers` CI job (`.github/workflows/gateway-v2.yml`)
  - Retain the `python gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist` invocation and the existing inline LGW00-2 throwaway OpenSSH-fixture check that calls `scan_paths` and fails if no hit is returned.
  - Add a second throwaway fixture at the gate carrying a bare PKCS#8 `BEGIN PRIVATE KEY` header and assert `scan_paths` reports a hit, proving Requirement 1.1 at the CI gate.
  - _Requirements: 1.1, 8.1, 8.2_

- [x] 4. Extend the single Python gate-test file `gateway_v2/tests/gates/test_secret_headers.py` (ONE task, delivered in one pass)
  - **SINGLE-TEST-FILE LESSON:** all Python gate cases live in this one file. This task is delivered in a single pass; the enumerated cases below are serialized same-file work owned by this one task and MUST NOT be split across parallel sub-agents.
  - Keep the existing four tests unchanged (their no-key-material assertions are the Property 5 anchor).
  - [x]* 4.1 Add the header-detection + path-category matrix (serialized, same file)
    - T-PKCS8-EXTLESS, T-PKCS8-PEM, T-PKCS8-SUFFIX, T-PKCS8-DOTFILE — bare `BEGIN PRIVATE KEY` in extensionless / `.pem` / `leaked.txt` / `.id_pkcs8` → hit (**Property 1**).
    - T-KEY-TXT (`id_rsa.bak`), T-KEY-DOTFILE (`.ssh_key` OpenSSH), T-RSA / T-EC / T-ENC label headers → hit (**Property 1**).
    - T-NO-HEADER-SUFFIX (`backup.old`), T-NO-HEADER-DOTFILE (`.config`) → no hit (**Property 2**).
    - T-ALLOW-PY, T-ALLOW-MD, T-ALLOW-SPEC, T-ALLOW-FIXTURE — header-as-data in `.py` / `.md` / `.kiro/specs/**` / `tests/.../fixtures*.py`-pattern path → no hit (**Property 3**).
    - T-NON-KEY-PEM — `BEGIN CERTIFICATE` / `BEGIN PUBLIC KEY` in a key-shaped file → no hit (regex-width check / D1).
    - T-FORBIDDEN (`ai-mesh-firewall` with header → hit) and T-LGW00-2 (existing extensionless OpenSSH hit, kept) (**Property 7**).
    - All fixtures use **fake** headers via `tmp_path`.
    - _Requirements: 1.1, 1.3, 1.4, 2.1, 2.2, 2.3, 3.1, 3.2, 4.1, 4.4, 4.5, 4.6, 6.1, 6.2_
  - [x]* 4.2 Add T-ZERO and T-NO-MATERIAL (serialized, same file)
    - **T-ZERO:** run `scan_repo(Path(repo_root), allow_known=False)` over the real `git ls-files` tracked set and assert zero `HeaderHit`s (**Property 4**); offline/deterministic, guarded to run only when `.git` is present (CI `--fail-allowlist` is the authoritative fallback per design).
    - **T-NO-MATERIAL:** assert `repr(hits)` for every produced hit contains no header substring — path-only, no key material (**Property 5**).
    - Depends on Task 1's triage loop having converged to zero hits.
    - _Requirements: 4.2, 5.1, 5.3, 8.1_

- [x] 5. Add the bash scratch-repo guard test (new co-located Python file, separate from the main gate file)
  - Create a new Python test under `gateway_v2/tests/gates/` that drives `scripts/ralph/precommit-secret-scan.sh` over a temporary `git init` scratch repo (stage fixtures, run the hook, assert exit code + path-only stderr). It is a separate file so it does not collide with `test_secret_headers.py`.
  - Cases: staged extensionless OpenSSH header → exit 1, path in stderr, no body (LGW00-2 pre-commit); staged suffixed `leaked.txt` bare PKCS#8 → exit 1; staged dotfile `.id_rsa` with header → exit 1; staged `.py`/`.md` fixture-allowlisted mention → exit 0; staged clean file → exit 0 (**Property 6**).
  - _Requirements: 6.3, 7.1, 7.2, 7.3_

- [x] 6. Run the authoritative exit gate
  - Zero-hit `python gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist` over the real tree (exit 0).
  - New Python gate tests (Task 4) and the bash scratch-repo test (Task 5) green; full offline suite stays at baseline **1788 passed / 100 skipped / 1 xfailed** plus the new tests.
  - `mypy --strict`; `ruff check gateway_v2 state_control lint tests`; `lint-imports`; the 6 AST gates; `unscanned_channels() == ()`.
  - Fix any regression surfaced here before proceeding; do not weaken an existing assertion to make a check pass (Requirement 8.4).
  - _Requirements: 8.1, 8.3, 8.4_

- [x] 7. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- **Single-test-file lesson:** every Python gate case for this feature lives in the one file `gateway_v2/tests/gates/test_secret_headers.py` (Task 4). That task is delivered in a single pass; the `*` sub-items (4.1, 4.2) enumerate serialized same-file work and must not be split across parallel sub-agents. The bash scratch-repo test (Task 5) is a *separate* new file precisely so it can proceed independently without colliding with the main file.
- **Zero-hit obligation:** Requirement 4.2 is the sharpest constraint. Task 1 carries the design §D2 triage loop as a precondition — the scanner must reach zero hits over the real tree (triaging every hit into an allowlist *category* by pattern) before Task 4's T-ZERO is expected to pass.
- **Preserved invariants:** `from __future__ import annotations`, frozen `HeaderHit`, path-only reporting, `ruff` + `mypy --strict` cleanliness (the `lint` package is in the mypy `files` list).
- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core production edits (1, 2, 3) and the gate (6) are never optional.
- Property tests here are realized as a small enumerated example/edge-case matrix (header-form × path-category) plus one real-tree test, per the design's honest Testing Strategy — the input space is a fixed, small set, so randomized PBT would re-assert the same finite truth table.
- **Deferred boundaries (Requirement 9), out of scope:** full git-history blob scanning + the `ai-mesh-firewall` history rewrite (separate `History_Rewrite_Ticket`); branch protection / required-checks / reproducible-image digest (LGW00-6) / Node SDK matrix (GW01 CI/infra); credential classification / revocation / rotation (human with infra authority). The scanner continues to scan `git ls-files` tracked files only.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2", "3"] },
    { "id": 2, "tasks": ["4.1", "5"] },
    { "id": 3, "tasks": ["4.2"] },
    { "id": 4, "tasks": ["6"] }
  ]
}
```
