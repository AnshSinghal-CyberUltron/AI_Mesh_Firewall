# Design Document

## Overview

This feature closes the four header-detection gaps the **C10** re-open names in the
**GW00 card** of the v3 backend-rewrite runbook
(`docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`, §10.9): the secret scan
"misses PKCS#8 'BEGIN PRIVATE KEY', suffixed files, dotfiles and history." The
*history* gap is a declared boundary (the separate `History_Rewrite_Ticket`,
Requirement 9); this spec closes the other three — PKCS#8, suffixed files, dotfiles
— at the two enforcement points, without regressing **LGW00-1** (the forbidden
`ai-mesh-firewall` filename is always a hit) or **LGW00-2** (an extensionless
OpenSSH header is blocked at pre-commit and fails the PR).

Two enforcers carry the gap today and both are changed in lockstep:

- **CI_Scanner** — `gateway_v2/lint/check_secret_headers.py`, run by the
  `secret-headers` job in `.github/workflows/gateway-v2.yml`. Scans every
  `git ls-files` tracked file; reports paths only.
- **Precommit_Guard** — `scripts/ralph/precommit-secret-scan.sh` part (3). Inspects
  the staged *added* diff; reports paths only.

The central design tension (named verbatim in the requirements Introduction) is that
the current repository is **full of legitimate header mentions** — the scanner builds
its own needle, the bash guard assembles `_OS_HDR` at runtime, docs discuss PEM/OpenSSH
headers, and redaction-test fixtures embed fake keys. Evidence gathered while writing
this design: a `git grep` for `BEGIN … PRIVATE KEY` across the tracked tree hits
Python (`control/.../pii_policy_catalog.py`, `gateway_v2/.../windowing.py`, many
`gateway_v2/tests/detect/*.py`, `gateway/.../test_private_key_redaction.py`), Markdown
and plan docs (`docs/plans/**`, `docs/SECTION_10_*.md`, the v2.1 runbook), JSON
fixtures (`docs/plans/evidence/**/canaries.json`), a Go test
(`docs/plans/evidence/**/content_test.go`), **this spec's own `requirements.md`**, and
even tracked **copies of the bash guard** under
`docs/plans/evidence/2026-09-23-.../scripts/ralph/precommit-secret-scan.sh`. None of
those is a key file. The hardening must read real keys in suffixed files and dotfiles
while leaving every one of these mentions un-flagged — so Requirement 4.2 (zero hits on
the current tracked tree) is the sharpest constraint in the design.

The chosen mechanism is **content-first, allowlist-excused**: after this change the
CI_Scanner *reads* a candidate's bytes and decides by header content, and the only
thing that excuses a file is a **path-pattern Content_Allowlist** that covers source,
docs, specs, and test fixtures. The scanner stays a pure function of path + bytes,
keeps `from __future__ import annotations`, the frozen `HeaderHit`, and path-only
reporting (Requirement 8.3).

## Architecture

Three artifacts, one shared definition of a header.

```mermaid
flowchart TD
    subgraph SharedDef["Shared header-form definition (D1)"]
        H1["PKCS#8: BEGIN PRIVATE KEY (empty label)"]
        H2["PEM: BEGIN &lt;LABEL&gt; PRIVATE KEY (RSA/EC/ENCRYPTED/...)"]
        H3["OpenSSH: BEGIN OPENSSH PRIVATE KEY"]
    end

    subgraph CI["CI_Scanner — check_secret_headers.py (full tree)"]
        G["git_tracked_files()"] --> SP["scan_paths()"]
        SP -->|"path matches Content_Allowlist? skip"| AL["Content_Allowlist (D2)"]
        SP -->|"else read bytes"| FH["file_has_private_key_header()"]
        FH --> HIT["HeaderHit(path) — path only"]
    end

    subgraph PC["Precommit_Guard — precommit-secret-scan.sh part (3) (staged diff)"]
        D["git diff --cached added lines"] --> CASE["case: read suffixed + dotfiles"]
        CASE -->|"path matches fixture_re? skip"| FR["fixture allowlist"]
        CASE -->|"else grep added lines"| PEMRE["_OS_HDR | _PEM_RE"]
        PEMRE --> NOTE["note: path only, body omitted"]
    end

    SharedDef --> FH
    SharedDef --> PEMRE
```

Both enforcers detect the **same three header forms** (D1). They differ in *scope* —
the CI_Scanner reads the whole tracked tree, the Precommit_Guard reads only staged
added lines of staged files — and this one structural difference is expected and
acceptable (D4, Requirement 7). The CI_Scanner is authoritative; the Precommit_Guard
is a fast local mirror.

Four design decisions drive the rest of the document: **D1** the header regexes,
**D2** the content-first shape gate + Content_Allowlist, **D3** mention-vs-key honesty,
**D4** bash↔Python consistency.

### D1 — The header regexes (both enforcers)

Current CI regex: `_PEM = rb"BEGIN [A-Z0-9][A-Z0-9 ]{0,64} PRIVATE KEY"`. The leading
`[A-Z0-9]` forces **at least one** label character and the trailing space is literal,
so bare `BEGIN PRIVATE KEY` (unencrypted PKCS#8, zero chars between `BEGIN ` and
`PRIVATE KEY`) never matches — the PKCS#8 gap. The fix makes the **label group
optional**:

- CI_Scanner (Python): `_PEM = re.compile(rb"BEGIN (?:[A-Z0-9][A-Z0-9 ]{0,64} )?PRIVATE KEY")`
- Precommit_Guard (bash ERE): `_PEM_RE="BEGIN ([A-Z0-9][A-Z0-9 ]* )?PRIVATE KEY"`

The non-capturing group now encloses the label **and its trailing space**, and the
whole group is optional. `BEGIN PRIVATE KEY` matches via the empty branch;
`BEGIN RSA PRIVATE KEY` matches via the label branch. OpenSSH keeps its own
assembled-literal needle (`_OPENSSH = b"BEGIN OPEN" + b"SSH PRIVATE KEY"` in Python,
`_OS_HDR="BEGIN OPEN""SSH PRIVATE KEY"` in bash) so this change touches only the PEM
branch and preserves OpenSSH/PEM-with-label behaviour (Requirements 1.3, 1.4).

**Needle hygiene is preserved.** The source of neither enforcer may contain a literal
`BEGIN <something> PRIVATE KEY`, or the file would flag itself. Both already assemble
the OpenSSH needle from fragments; the PEM *regex* contains the words
`BEGIN … PRIVATE KEY` separated by the character class, which is **not** a literal
header string, so it does not self-match (confirmed: the current scanner source is
tracked and is not a hit today). We keep **both** defenses in the house style:
(a) assembled OpenSSH needle + regex-not-literal for PEM, **and** (b) the two scanner
files are additionally covered by the Content_Allowlist pattern (`gateway_v2/lint/**`
and `scripts/ralph/**`). Belt and suspenders, matching existing precedent.

**Regex matrix** (verified by reading each pattern against each string):

| Candidate header bytes            | OpenSSH needle | old `_PEM` | new `_PEM` | Hit after fix? | Requirement |
|-----------------------------------|:--:|:--:|:--:|:--:|---|
| `BEGIN PRIVATE KEY` (PKCS#8)       | no | **no** | **yes** | **yes** | 1.1, 1.2 |
| `BEGIN RSA PRIVATE KEY`            | no | yes | yes | yes | 1.3 |
| `BEGIN EC PRIVATE KEY`             | no | yes | yes | yes | 1.3 |
| `BEGIN ENCRYPTED PRIVATE KEY`     | no | yes | yes | yes | 1.3 |
| `BEGIN OPENSSH PRIVATE KEY`        | **yes** | yes | yes | yes | 1.4 |
| `BEGIN DSA PRIVATE KEY`            | no | yes | yes | yes | 1.3 |
| `BEGIN CERTIFICATE`                | no | no | no | **no** | (non-key) |
| `BEGIN PUBLIC KEY`                 | no | no | no | **no** | (non-key) |
| `BEGIN PGP MESSAGE`                | no | no | no | **no** | (non-key) |

The last three confirm the regex does not widen to non-private-key PEM blocks. (A PEM
body of only `-----BEGIN PRIVATE KEY-----` surrounded by dashes still matches, because
`BEGIN PRIVATE KEY` is a substring of `-----BEGIN PRIVATE KEY-----`.)

### D2 — Content-first shape gate + Content_Allowlist (CI_Scanner)

Today `is_key_shaped_filename` returns `False` for **any** suffixed file
(`if suffix: return False`) and **any** dotfile (`if name.startswith("."): return
False`), so those files are never read — that is the suffixed-file and dotfile gap. The
design flips the model: **a candidate is read for header content unless its path
matches the Content_Allowlist.** Shape is no longer the thing that excuses a file;
the allowlist is.

The gate becomes, in order:

1. `name in FORBIDDEN_KEY_FILENAMES` (`ai-mesh-firewall`) → **always key-shaped**
   (LGW00-1, Requirement 6.1/6.4). Unchanged.
2. `suffix in {.pem, .key, .secret}` → **always key-shaped** (Requirement 2.2).
   Unchanged.
3. **New:** every other file — extensionless, suffixed, or dotfile — is a
   **readable candidate**. The old `if suffix: return False` and
   `if name.startswith("."): return False` early-returns are **removed**.
4. The `_NON_KEY_NAMES` set and the `Dockerfile*`/`Makefile*` prefixes remain a
   *shape* exclusion for the handful of extensionless build files that are not keys
   (`Dockerfile`, `Makefile`, `LICENSE`, `CHANGELOG`, `Jenkinsfile`, …). This is the
   existing precedent the Content_Allowlist *extends, not replaces* (Requirement 4.3).

So `is_key_shaped_filename` after the change returns `True` for essentially every file
except the named build files, and the **Content_Allowlist** — consulted in
`scan_paths` before the shape gate — is what prevents false positives.

**The Content_Allowlist is path-pattern based** (not basename-only, which the current
`allowlist` argument is). It is a sequence of compiled path-regexes matched against the
file's repo-relative POSIX path. It must excuse, by **category** (never one-off
basenames unless unavoidable):

| Category | Pattern (illustrative; finalized against `git ls-files`) | Why | Req |
|---|---|---|---|
| The two enforcers | `^gateway_v2/lint/`, `^scripts/ralph/` | They carry the needle/regex by construction | 4.1 |
| Source code | `\.py$`, `\.sh$`, `\.go$`, `\.ts$`, `\.tsx$`, `\.js$`, `\.jsx$`, `\.rs$` (extensions actually present) | Modules build header strings as data | 4.4 |
| Documentation | `^docs/`, `\.md$` | Docs discuss headers | 4.5 |
| Specs | `^\.kiro/specs/` | This very spec quotes `BEGIN PRIVATE KEY` | 4.5 (spec text is doc) |
| Test fixtures | `(^|/)tests?/`, `/leakhunt/`, `patterns\.py$`, `corpus\.py$`, `ATTACK_LANDSCAPE\.md$`, `SOLUTIONS\.md$`, `fixtures.*\.py$`, `canaries\.json$` | Redaction fixtures embed fake keys | 4.6, 7.3 intent |

The source and doc categories (`.py`, `.sh`, `.md`, `docs/**`, `.kiro/specs/**`) are the
load-bearing ones: making `.py`/`.md` readable is exactly what would otherwise turn the
scanner's own source, this spec's `requirements.md`, the existing
`test_secret_headers.py`, and the evidence-dir copies of the bash guard into hits. The
allowlist covers all of them **by pattern**.

**How zero-hit-on-current-repo (Requirement 4.2) is confirmed — a design obligation on
the implementer, not a hope.** The implementer MUST, before finalizing the patterns:

1. Run the hardened scanner over the real tracked set
   (`python gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist`).
2. For every path reported, triage it into a Content_Allowlist **category** (source,
   doc, spec, fixture) and widen the matching *pattern*, not add a basename, unless a
   file genuinely has no categorical home.
3. Repeat until the run exits 0 with no hits.
4. Lock that in with an automated test that runs `scan_repo` over `git ls-files` and
   asserts zero hits (see Testing Strategy, test T-ZERO). The CI `--fail-allowlist` run
   (Requirement 8.1) is the second line of the same guard.

The known header-bearing tracked paths found during design — all under
`docs/`, `gateway/`, `gateway_v2/.../tests/`, `control/`, `scripts/`, `.kiro/specs/`,
with extensions `.py/.md/.json/.go/.sh` — are all covered by the categories above, so
the expected post-fix tracked-tree result is **zero hits**.

### D3 — Distinguishing a real key from a mention (honest residual)

A path-pattern allowlist buys a **zero false-positive rate on the existing repo** at a
real, declared cost: a genuine private key committed at an allowlisted path — say
`docs/leaked.md`, `foo.py`, or `gateway_v2/tests/evil.key`-mention — would be
**excused**. This design accepts that as a **known, declared residual**, consistent
with the GW00 posture that the header scanner is *one* defense layer: the live-key SHA
fingerprint check (bash part 1, every file, no exclusions) and the separate
`History_Rewrite_Ticket` + rotation (Requirement 9) are the others. The header scanner's
job is to stop a key dropped at a *key-shaped or arbitrary non-source* path; the
fingerprint guard catches the specific runtime key everywhere.

**Alternative considered and rejected for now:** a stricter "real key" rule that only
counts a header as a key when the matching `BEGIN`+`END` pair wraps a non-empty base64
body (so a one-line mention in prose would not count). Tradeoff: it would let the
allowlist shrink (fewer doc/source excusals) and raise recall inside allowlisted paths,
but it adds body/entropy heuristics the GW00 card scope explicitly does **not** ask for
("add a CI job that greps every tracked file for PEM and OpenSSH headers" — header-based,
not body-based), and it risks new false positives on fixtures that *do* embed a full
fake key with a body (e.g. `canaries.json`, `test_private_key_redaction.py`). **Chosen:
the simpler header + path-allowlist**, because the repo evidence shows the path-pattern
approach reaches zero hits cleanly and the card scopes this to header detection. The
residual is documented here rather than engineered away.

### D4 — Bash ↔ Python consistency (Requirement 7)

The Precommit_Guard operates on the **staged added diff**, not the full tree, so it
cannot be byte-identical to the CI_Scanner. Consistency is defined as: the guard
detects the **same header forms** (PKCS#8/OpenSSH/PEM via the same optional-label
regex, D1) and honors a fixture allowlist with the **same intent**, for the staged
files its key-shape logic reaches.

Two changes to part (3):

1. **Regex:** `_PEM_RE` gets the optional label group (D1) so staged
   `BEGIN PRIVATE KEY` is caught (Requirement 1.2, 7.2).
2. **Shape `case`:** today `*.*) continue ;;` skips every suffixed file and
   `.*) continue ;;` skips every dotfile. Both lines are **removed** so staged
   suffixed files and dotfiles are inspected, mirroring the CI content-first decision.
   The guard's existing `fixture_re`
   (`tests?/|/leakhunt/|patterns\.py$|corpus\.py$|precommit-secret-scan\.sh$|ATTACK_LANDSCAPE\.md$|SOLUTIONS\.md$`)
   is the mirror of the Content_Allowlist intent (Requirement 7.3) and is **extended**
   to cover the same source/doc categories as D2 (at minimum add `\.py$`, `\.md$`,
   `\.sh$`, `docs/`, `\.kiro/specs/`) so the two enforcers agree on what a *mention* is.
   `.pem`/`.key`/`.secret` stay always-inspected (kept as the first `case` arm).

The one structural difference — **staged-diff vs full-tree** — is stated as expected
and acceptable (Requirement 7's user story asks the two to *agree on what counts as a
header*, not to scan the same inputs). The live-key fingerprint check (part 1) and the
generic-token check (part 2) are untouched.

## Components and Interfaces

### CI_Scanner — `gateway_v2/lint/check_secret_headers.py`

Changed functions and their post-change contracts:

- **`file_has_private_key_header(path: Path) -> bool`** — unchanged signature. Reads
  bytes; returns `True` if `_OPENSSH in data` or the **new** optional-label `_PEM`
  matches. `except OSError: return False` preserved (unreadable file → no hit, Error
  Handling).
- **`is_key_shaped_filename(path: Path) -> bool`** — unchanged signature, new body
  (D2): forbidden filename → `True`; `.pem/.key/.secret` → `True`; `_NON_KEY_NAMES` /
  `Dockerfile*` / `Makefile*` → `False`; **everything else → `True`** (suffixed files
  and dotfiles are now readable). The `if suffix: return False` and
  `if name.startswith("."): return False` lines are deleted.
- **`scan_paths(paths, allowlist) -> list[HeaderHit]`** — gains Content_Allowlist
  semantics. The `allowlist` parameter is **widened** from a basename set to accept
  the path-pattern allowlist: it skips a path when the path matches a Content_Allowlist
  pattern *or* (back-compat) when `path.name` is in the passed set. The existing CI
  inline check and tests call `scan_paths([blob], allowlist=set())` with an empty set
  and must keep working, so the Content_Allowlist is applied as a module-level default
  (a frozen tuple of compiled patterns) consulted in addition to the passed
  `allowlist`. Order per path: Content_Allowlist match → skip; else `is_key_shaped_filename`
  → if not, skip; else `file_has_private_key_header` → append `HeaderHit`.
- **`scan_repo(repo, *, allow_known) -> tuple[list[HeaderHit], list[str]]`** —
  unchanged behaviour; it already computes `known_present` for `FORBIDDEN_KEY_FILENAMES`
  and calls `scan_paths` over `git_tracked_files`. With the new `scan_paths` it now
  reads suffixed/dotfiles too, excused by the Content_Allowlist. LGW00-1 preserved:
  `known_present` is computed independent of the allowlist, so `ai-mesh-firewall`
  remains a hit even though it is extensionless.
- **`main`** — unchanged. Still prints `LGW00-1 CLOSED residual…` and
  `private-key header (body omitted): {rel}`; returns 1 on any hit or known residual.

**Preserved invariants (Requirement 8.3):** `from __future__ import annotations`,
frozen `HeaderHit`, path-only prints, module stays `ruff`+`mypy --strict` clean (the
`lint` package is in the mypy `files` list; the new compiled-pattern tuple is typed as
`tuple[re.Pattern[str], ...]`).

### Precommit_Guard — `scripts/ralph/precommit-secret-scan.sh` part (3)

- `_PEM_RE` → optional label group (D1).
- The `case "$f"` block loses `*.*) continue ;;` and `.*) continue ;;`; keeps
  `*.pem|*.key|*.secret) ;;` and the build-file `continue` arms.
- The part-(3) loop gains a fixture-allowlist skip using an extended `fixture_re`
  (D4) so staged `.py`/`.md`/`docs/` mentions are not findings.
- Parts (1) and (2) unchanged. Still `note`s path only, body omitted (Requirement 5.2).

### CI workflow — `.github/workflows/gateway-v2.yml` job `secret-headers`

- The `--fail-allowlist` invocation and the inline LGW00-2 throwaway check are
  **retained** (Requirement 8.1, 8.2). The inline check still writes a `/tmp`
  extensionless OpenSSH fixture and fails if `scan_paths` returns no hit; with the
  content-first change this is now a floor, and we may strengthen it with a second
  throwaway fixture carrying a bare PKCS#8 header to prove Requirement 1.1 at the gate.

## Data Models

- **`HeaderHit`** — `@dataclass(frozen=True)` with a single `path: str`. Unchanged.
  It carries **no** byte of key material, satisfying Requirement 5.3 by construction.
- **`FORBIDDEN_KEY_FILENAMES: frozenset[str]`** = `{"ai-mesh-firewall"}`. Unchanged
  (Requirement 6.4).
- **`KNOWN_UNTIL_LGW00_1: frozenset[str]`** = `frozenset()` (empty). Unchanged
  (Requirement 6.4).
- **`_NON_KEY_NAMES: frozenset[str]`** — the extensionless build-file shape exclusion.
  Retained as existing precedent the allowlist extends (Requirement 4.3).
- **Content_Allowlist** — a new module-level `tuple[re.Pattern[str], ...]` of
  path-pattern regexes (D2), applied against the repo-relative POSIX path in
  `scan_paths`. Represented as compiled patterns for `mypy --strict` cleanliness and
  deterministic matching. The bash mirror is the extended `fixture_re` string.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid
executions of a system — a formal statement about what the system should do.*

This feature's input space is a **fixed, small set of header forms** crossed with a
**fixed set of path categories**. It is header-string detection over paths, not a
transformation with an infinite input space, so most criteria are best covered by an
example/edge-case matrix (see Testing Strategy for the honest justification). A few
genuinely universal statements still hold and are stated as properties; they are
validated with small enumerated generators (the finite header-form set × a path
sampler), not 10,000-iteration randomized runs.

### Property 1: Every header form is detected regardless of extension unless allowlisted

*For all* files whose bytes contain any `Private_Key_Header` (PKCS#8, PEM-with-label,
or OpenSSH) and whose path does **not** match the Content_Allowlist, `scan_paths`
reports exactly one `HeaderHit` for that path — independent of whether the file is
extensionless, `.pem/.key/.secret`, otherwise-suffixed, or a dotfile.

**Validates: Requirements 1.1, 1.3, 1.4, 2.1, 2.2, 3.1**

### Property 2: A file with no header is never a hit

*For all* files whose bytes contain no `Private_Key_Header`, `scan_paths` reports no
hit for that path, regardless of extension or dotfile status.

**Validates: Requirements 2.3, 3.2**

### Property 3: Allowlisted paths are never hits

*For all* files whose path matches the Content_Allowlist, `scan_paths` reports no hit
even when the bytes contain a `Private_Key_Header`.

**Validates: Requirements 4.1, 4.4, 4.5, 4.6**

### Property 4: The current tracked tree yields zero hits

Over the actual `git ls-files` tracked set of this repository, `scan_repo(...,
allow_known=False)` reports zero `HeaderHit`s (and the only permissible non-zero exit
is a genuine LGW00-1 forbidden-filename residual, which is not present).

**Validates: Requirements 4.2, 8.1**

### Property 5: No finding carries key material

*For all* findings produced by either enforcer, the emitted output and the finding
object contain the file path only and **no** byte of the matched header or key body
(`repr(hits)` contains no header substring; the bash `note` prints only the path).

**Validates: Requirements 5.1, 5.2, 5.3**

### Property 6: Bash and Python agree on header forms

*For all* strings in the header-form set {PKCS#8, PEM-with-label, OpenSSH}, both the
CI_Scanner regex/needle and the Precommit_Guard regex/needle report a match; and *for
all* non-key strings in {`BEGIN CERTIFICATE`, `BEGIN PUBLIC KEY`, `BEGIN PGP MESSAGE`}
neither does.

**Validates: Requirements 7.1, 7.2**

### Property 7: LGW00-1 and LGW00-2 are preserved

*For all* runs where `ai-mesh-firewall` is tracked with a header, `scan_repo` yields a
hit and `--fail-allowlist` exits non-zero; and an extensionless OpenSSH-header file is
a hit at the CI gate and a non-zero-exit finding at the pre-commit gate.

**Validates: Requirements 6.1, 6.2, 6.3**

## Error Handling

- **Unreadable file** — `file_has_private_key_header` keeps `except OSError: return
  False`: a path that cannot be read (permissions, race with deletion, special file)
  yields **no hit** rather than crashing the gate. This is fail-*open per file* but the
  gate as a whole still fails closed on everything it can read; the GW00 posture treats
  the header scanner as one layer and does not require it to resolve unreadable blobs.
- **Non-UTF-8 / binary bytes** — detection is on raw `bytes` (`read_bytes` +
  `bytes`-pattern search), so binary files are scanned safely; no decode step can
  raise.
- **Missing git / not a repo** — `git_tracked_files` lets `subprocess.check_output`
  raise as today (a broken CI environment should fail loudly, not silently pass).
- **Bash guard** — `set -uo pipefail` retained; `|| true` on the `git diff`/`grep`
  pipelines keeps an empty diff from aborting the hook; a header match sets `fail=1`
  and the hook exits 1 with the path only.

## Testing Strategy

### Why this is example/edge-case testing, not property-based testing — stated honestly

PBT shines when behavior varies meaningfully across a large or infinite input space
(parsers, serializers, algorithms). Here the behavior pivots on a **fixed, enumerable**
set: three header forms, a short list of non-key `BEGIN …` strings, and a handful of
path categories (extensionless, `.pem/.key/.secret`, other-suffix, dotfile, allowlisted
source/doc/spec/fixture). A 10,000-iteration randomized generator would spend all its
iterations re-asserting the same finite truth table. So the "properties" above are
realized as a **small enumerated matrix** of example and edge-case tests (header-form ×
path-category), plus one test that runs against the **real** tracked tree. This is the
appropriate, honest tool for a fixed-input detector; it is not a cost dodge. The one
place with genuine input variation — "any extension" — is covered by sampling several
representative suffixes rather than by randomized fuzzing, because the gate logic treats
all non-allowlisted suffixes identically.

### Unit / example / edge-case tests (co-located in `gateway_v2/tests/gates/test_secret_headers.py`)

Following the existing idiom: `tmp_path` fixtures writing **fake** headers, asserting
path-only via `repr(hits)` carrying no key material. The existing four tests are kept
unchanged (and the no-key-material assertions in them are the Property 5 anchor). New
cases:

| ID | Case | Asserts | Property / Req |
|---|---|---|---|
| T-PKCS8-EXTLESS | bare `BEGIN PRIVATE KEY` in an extensionless file | hit | P1 / 1.1 |
| T-PKCS8-PEM | bare `BEGIN PRIVATE KEY` in a `.pem` file | hit | P1 / 1.1, 2.2 |
| T-PKCS8-SUFFIX | bare `BEGIN PRIVATE KEY` in `leaked.txt` | hit | P1 / 1.1, 2.1 |
| T-PKCS8-DOTFILE | bare `BEGIN PRIVATE KEY` in `.id_pkcs8` | hit | P1 / 1.1, 3.1 |
| T-KEY-TXT | real-shape fake key in `id_rsa.bak` | hit | P1 / 2.1 |
| T-KEY-DOTFILE | OpenSSH header in `.ssh_key` | hit | P1 / 3.1 |
| T-RSA / T-EC / T-ENC | `RSA`/`EC`/`ENCRYPTED` label headers | hit | P1 / 1.3 |
| T-NO-HEADER-SUFFIX | `backup.old` with no header | no hit | P2 / 2.3 |
| T-NO-HEADER-DOTFILE | `.config` with no header | no hit | P2 / 3.2 |
| T-ALLOW-PY | header string inside a `.py` module (header-as-data) | no hit | P3 / 4.4 |
| T-ALLOW-MD | header string inside a `.md` doc | no hit | P3 / 4.5 |
| T-ALLOW-SPEC | header string inside a `.kiro/specs/**` file | no hit | P3 / 4.5 |
| T-ALLOW-FIXTURE | header in a `tests/.../fixtures*.py`-pattern path | no hit | P3 / 4.6 |
| T-NON-KEY-PEM | `BEGIN CERTIFICATE` / `BEGIN PUBLIC KEY` in a key-shaped file | no hit | regex width / D1 |
| T-ZERO | `scan_repo` over real `git ls-files`: assert zero hits | zero hits | P4 / 4.2, 8.1 |
| T-FORBIDDEN | `ai-mesh-firewall` with header is a hit | hit | P7 / 6.1 |
| T-LGW00-2 | extensionless OpenSSH header is a hit (existing, kept) | hit | P7 / 6.2 |
| T-NO-MATERIAL | `repr(hits)` for every hit contains no header substring | no leak | P5 / 5.1, 5.3 |

Note on **T-ZERO**: it runs the scanner over the real repository root
(`scan_repo(Path(repo_root), allow_known=False)`). It is an offline test (no network),
deterministic, and is the automated form of the Requirement 4.2 obligation. If running
`git ls-files` inside the pytest sandbox proves unreliable in a given CI runner, the
fallback is to keep T-ZERO but mark it to run only when `.git` is present and rely on
the CI `--fail-allowlist` job (Requirement 8.1) as the authoritative tree-wide gate;
the design preference is to keep it as a real test.

### Bash guard verification

The bash guard has **no existing automated test** (confirmed by search). The design
adds a minimal shell-invocation test under `gateway_v2/tests/gates/` (a Python test
that drives the script via a temporary `git init` scratch repo, stages fake-header
fixtures, runs the hook, and asserts exit code + path-only stderr). Cases:

- staged extensionless OpenSSH header → exit 1, path in stderr, no body (LGW00-2
  pre-commit, Requirement 6.3, 7.1).
- staged suffixed file (`leaked.txt`) with bare PKCS#8 header → exit 1 (D4, 1.2, 7.2).
- staged dotfile (`.id_rsa`) with header → exit 1 (D4, 3-equivalent at pre-commit).
- staged `.py`/`.md` mention under the fixture allowlist → exit 0 (7.3).
- staged clean file → exit 0.

If the repo's CI cannot run a `git`-driven shell test reliably, the fallback is a
documented manual verification step mirroring the existing
`docs/plans/evidence/.../repro_secret_pkcs8.sh` idiom; the design preference is the
automated scratch-repo test, co-located with the Python gate tests.

### CI gate (Requirement 8)

- `secret-headers` job stays green: `--fail-allowlist` exits 0 over the current tree
  (T-ZERO is the local mirror).
- The inline LGW00-2 throwaway check is kept and strengthened with a second throwaway
  bare-PKCS#8 fixture (Requirement 8.2, 1.1 at the gate).
- `ruff` + `mypy --strict` stay clean on the changed `lint` module (Requirement 8.3);
  no existing assertion is weakened (Requirement 8.4).

### Requirement → design-element coverage map

| Requirement | Design element |
|---|---|
| 1 (PKCS#8 both enforcers) | D1 regex (optional label); regex matrix; T-PKCS8-* |
| 2 (suffixed files, CI) | D2 content-first gate; Content_Allowlist; T-KEY-TXT, T-NO-HEADER-SUFFIX |
| 3 (dotfiles, CI) | D2 (dotfile early-return removed); T-PKCS8-DOTFILE, T-KEY-DOTFILE |
| 4 (allowlist, zero FP) | D2 path-pattern allowlist + zero-hit obligation; P3, P4; T-ALLOW-*, T-ZERO |
| 5 (path-only) | `HeaderHit`; path-only prints; bash `note`; P5; T-NO-MATERIAL |
| 6 (LGW00-1/2 preserved) | `FORBIDDEN_KEY_FILENAMES`, empty `KNOWN_UNTIL_LGW00_1`, `known_present`; P7; T-FORBIDDEN, T-LGW00-2 |
| 7 (bash↔Python) | D1 shared forms + D4 shape/allowlist mirror; P6; bash scratch-repo test |
| 8 (CI green, enforce) | workflow retains `--fail-allowlist` + inline check; mypy/ruff clean; T-ZERO |
| 9 (deferred boundaries) | Overview + D3 residual: history/rotation/branch-protection out of scope |

## References

- **GW00 card** and **C10** re-open: `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`
  §10.9 and the §10.8 C10 row (sole source of truth).
- **LGW00-1 / LGW00-2**: GW00 "Live acceptance tests" table.
- Agent execution note ("Agents must not print key material into logs, PR descriptions
  or task output") — the basis for Requirement 5 and path-only reporting throughout.
