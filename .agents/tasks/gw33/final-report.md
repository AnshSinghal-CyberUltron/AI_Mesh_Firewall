# GW33 final report (output parity)

Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw33`, branch `gw33-output-parity`.
Not fast-forwarded, not merged into `suraj-revamp`, not pushed. Worktree and branch kept.

## 1. What landed

- Base: `suraj-revamp` tip `1634da0c`. The branch was already based on it, so the rebase was a no-op (no conflicts, no code change). Quick gates were therefore not re-run; the offline gate run below applies unchanged. The only commit since that run is docs-only (`20feebe5`).
- Commit range: `1634da0c..20feebe5` (22 commits, first `4a824f21`, last `20feebe5`). 120 files changed, +9396 / -281.
- Spec: `.kiro/specs/gw33-output-parity/{requirements,design,tasks}.md` (committed).
- Task artifacts under `.agents/tasks/gw33/` are tracked by this repo, so they are committed.
- Modules touched, by commit:
  - `domain/`: vocabulary (REWRITE, review flag, codes); capacity gate allow-list.
  - `runtime/`: `resources_output.py` (OutputBoundsMixin, DEFAULT_OUTPUT_BOUNDS).
  - `egress/`: rewrite verification, typed errors, final check, delta detector, trade-off REWRITE, proposer, `stream_publish` extraction.
  - `resolve/`: REWRITE disposition, review flag, edit normalisation, identity join, single-authority gate.
  - `dispatch/verify.py`: request-side rewrite verification, MCP rescan, console action.
  - `detect/holdback.py` (canary holdback class) and `detect/output/` (ip_leak, exfil, canary, grounding detectors, ruleset, structure canonicalisation, corpus).
  - `plan/`: compiler/document REWRITE, `allow_hosts`, built-in refusal.
  - `edge/`: composition (canary inject, output detectors, length bound, audit fields), passthrough final check, MCP rewrite.
  - `control/`: map, publisher and serializer for rewrite, grounding, exfil, canary.
  - Tests: offline L33-1..3 equivalents, bounded-work, property-tag gate (16 properties), runner.
  - Docs: changelog pointers, plan note.

## 2. Gate results (verbatim from `.agents/tasks/gw33/gate-report.md`)

```
# GW33 final OFFLINE gate run

Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw33` (branch `gw33-output-parity`, HEAD `ac44590a`).
Container: `gw29-dev:latest`, whole worktree mounted at `/repo`, cwd `/repo/gateway_v2`, `PYTHONPATH=/repo/gateway_v2`.
Base for comparison: `suraj-revamp` tip `1634da0c` (main checkout at `/work/suraj/AI_Mesh_Firewall`).
Live Docker/staging gates were NOT run and are NOT claimed.

No GW33-caused failure was found anywhere below (confirmed by re-running two gates against the base commit, identical output), so nothing was fixed or committed in this pass. This re-run reproduces `.agents/tasks/gw33/verification.md` (iteration 2) exactly and adds `check_tenant_scale` to the recorded AST-gate set.

## Commands and verbatim results

### 1. `python -m pytest -q -p no:cacheprovider` (full suite, cwd `gateway_v2`)
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
FAILED tests/gates/test_secret_headers.py::test_current_tracked_tree_yields_zero_hits
2 failed, 3279 passed, 106 skipped, 8 warnings in 635.17s (0:10:35)

Pre-existing, unrelated to GW33 — both failures also reproduce on a container run against `suraj-revamp` tip `1634da0c` (same test names, same shape):
- `test_the_shipped_data_plane_is_clean`: the documented tenant-scale finding in GW30's `dispatch/vector_transports/pinecone_transport.py:140` (whole-collection store read scaling with changes), also flagged directly by gate 5 below. Not touched by GW33 (GW33 touches `domain/`, `runtime/resources_output.py`, `egress/`, `dispatch/verify.py` docstrings only, `resolve/`, `detect/holdback.py` + `detect/output/`, `plan/`, `edge/`, `control/`).
- `test_current_tracked_tree_yields_zero_hits`: needs `git ls-files`; the mounted worktree's `.git` is a file pointing outside the container, so the gate can't resolve a git repo (`fatal: not a git repository`).
0 new failures from GW33.

### 2. `python -m pytest -q -k "gw33 or lgw33" -p no:cacheprovider`
236 passed, 3151 deselected in 11.23s

### 3. `python -m pytest -q tests/gates/test_gw33_property_tags.py -p no:cacheprovider`
1 passed in 0.02s
Confirms all 16 properties are tagged (`# Feature: gw33-output-parity, Property N: ...`) with iteration constants >= 100, per the table already recorded in `verification.md`.

### 4. `python -m mypy --strict gateway_v2`
Found 9 errors in 6 files (checked 243 source files)
(all import-not-found for weaviate, qdrant_client, pinecone, pymongo, elasticsearch, opensearchpy, chromadb in gateway_v2/dispatch/vector_transports/*)
All 9 errors are GW30 optional-extra vector client libraries not installed in this image (pre-existing). Base commit `1634da0c` gives the identical `Found 9 errors in 6 files` (checked 225 source files — file count differs only because GW33 added files; the 9 errors themselves are byte-identical, same files/lines/messages). Zero errors in any GW33 module.

### 5. `ruff check gateway_v2`
All checks passed!

### 6. `lint-imports`
Analyzed 294 files, 1752 dependencies.
Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT
Contracts: 3 kept, 0 broken.

### 7. AST gates (`python -m lint.<gate> gateway_v2`, cwd `/repo/gateway_v2`)
check_capacity_literals            exit=0
check_no_module_mutable            exit=0
check_frozen_dataclasses           exit=0
check_http_outside_edge_resolve    exit=0
check_sizes                        exit=0
check_single_authority             exit=0   (GW33 gate; no stale allow-list entries)
check_console_inventory            exit=0
check_tenant_scale                 exit=1
`check_tenant_scale` verbatim output:
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
Pre-existing, unrelated to GW33: re-ran the identical command against the `suraj-revamp` base checkout (`1634da0c`) and got a byte-identical result (`diff` empty). This is the GW30 Pinecone vector-transport finding, not touched by GW33's file list. This is the same finding behind gate-1 failure `test_the_shipped_data_plane_is_clean` above.

## Summary

| Gate | Result |
|---|---|
| Full offline pytest | 3279 passed, 106 skipped, 2 failed (both pre-existing, reproduced on base) |
| GW33/LGW33-filtered pytest | 236 passed |
| Property-tag gate | 1 passed |
| mypy --strict | 9 pre-existing errors (optional vector-SDK stubs, reproduced on base), 0 GW33 errors |
| ruff | All checks passed |
| import-linter | All 3 contracts kept |
| AST gates (capacity/mutable/frozen/http/sizes/single-authority/console-inventory) | all exit 0 |
| AST gate (tenant-scale) | 1 pre-existing violation (GW30 Pinecone transport, reproduced on base) |

No GW33-caused failure in any gate. No fix was needed; no commit was made in this pass.
```

Note: the verbatim block above elides only the raw per-line mypy listing and pytest progress dots (summary lines kept). The full original is in `gate-report.md`.

## 3. What remains for the human

- Live Docker/staging gates `L33-*` were NOT run. Nothing here claims live parity.
- Owner decision (design-review L-1): exfil protection ships default-off. `V2_EXFIL_ORGS` is empty, so L33-1 exfil parity is live only for orgs listed there. Do not read the parity claim as on-by-default.
- Pre-existing failures, not caused by GW33 (reproduced on `suraj-revamp` `1634da0c`):
  - `check_tenant_scale` / `test_the_shipped_data_plane_is_clean`: GW30 `pinecone_transport.py:140`.
  - `test_secret_headers.py::test_current_tracked_tree_yields_zero_hits`: container-only (worktree `.git` is a file); should pass outside the container.
  - mypy: 9 import-not-found errors for optional vector SDKs (GW30 extras not installed in the image).
- Design review (rev 5): APPROVED, 0 HIGH, 0 MEDIUM, 1 LOW (L-1 above), 1 NIT (D-2 wording is long; no behavioural ambiguity). Unverified by the reviewer: the four `compile_plan` call-site enumeration and publisher `_category` fallback line numbers from round 3, taken from the prior review.
- Integration: merge into `suraj-revamp` is left to the human. Nothing was merged or pushed.
