# GW33 final OFFLINE gate run

Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw33` (branch `gw33-output-parity`, HEAD `ac44590a`).
Container: `gw29-dev:latest`, whole worktree mounted at `/repo`, cwd `/repo/gateway_v2`, `PYTHONPATH=/repo/gateway_v2`.
Base for comparison: `suraj-revamp` tip `1634da0c` (main checkout at `/work/suraj/AI_Mesh_Firewall`).
Live Docker/staging gates were NOT run and are NOT claimed.

No GW33-caused failure was found anywhere below (confirmed by re-running two gates against the base commit, identical output), so nothing was fixed or committed in this pass. This re-run reproduces `.agents/tasks/gw33/verification.md` (iteration 2) exactly and adds `check_tenant_scale` to the recorded AST-gate set.

## Commands and verbatim results

### 1. `python -m pytest -q -p no:cacheprovider` (full suite, cwd `gateway_v2`)
```
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
FAILED tests/gates/test_secret_headers.py::test_current_tracked_tree_yields_zero_hits
2 failed, 3279 passed, 106 skipped, 8 warnings in 635.17s (0:10:35)
```
Pre-existing, unrelated to GW33 — both failures also reproduce on a container run against `suraj-revamp` tip `1634da0c` (same test names, same shape):
- `test_the_shipped_data_plane_is_clean`: the documented tenant-scale finding in GW30's `dispatch/vector_transports/pinecone_transport.py:140` (whole-collection store read scaling with changes), also flagged directly by gate 5 below. Not touched by GW33 (GW33 touches `domain/`, `runtime/resources_output.py`, `egress/`, `dispatch/verify.py` docstrings only, `resolve/`, `detect/holdback.py` + `detect/output/`, `plan/`, `edge/`, `control/`).
- `test_current_tracked_tree_yields_zero_hits`: needs `git ls-files`; the mounted worktree's `.git` is a file pointing outside the container, so the gate can't resolve a git repo (`fatal: not a git repository`).
0 new failures from GW33.

### 2. `python -m pytest -q -k "gw33 or lgw33" -p no:cacheprovider`
```
........................................................................ [ 30%]
........................................................................ [ 61%]
........................................................................ [ 91%]
....................                                                     [100%]
236 passed, 3151 deselected in 11.23s
```

### 3. `python -m pytest -q tests/gates/test_gw33_property_tags.py -p no:cacheprovider`
```
.                                                                        [100%]
1 passed in 0.02s
```
Confirms all 16 properties are tagged (`# Feature: gw33-output-parity, Property N: ...`) with iteration constants >= 100, per the table already recorded in `verification.md`.

### 4. `python -m mypy --strict gateway_v2`
```
gateway_v2/dispatch/vector_transports/weaviate_transport.py:46: error: Cannot find implementation or library stub for module named "weaviate"  [import-not-found]
gateway_v2/dispatch/vector_transports/weaviate_transport.py:139: error: Cannot find implementation or library stub for module named "weaviate.classes.config"  [import-not-found]
gateway_v2/dispatch/vector_transports/qdrant_transport.py:48: error: Cannot find implementation or library stub for module named "qdrant_client"  [import-not-found]
gateway_v2/dispatch/vector_transports/qdrant_transport.py:122: error: Cannot find implementation or library stub for module named "qdrant_client.http.exceptions"  [import-not-found]
gateway_v2/dispatch/vector_transports/pinecone_transport.py:63: error: Cannot find implementation or library stub for module named "pinecone"  [import-not-found]
gateway_v2/dispatch/vector_transports/mongo_transport.py:52: error: Cannot find implementation or library stub for module named "pymongo"  [import-not-found]
gateway_v2/dispatch/vector_transports/elastic_transport.py:137: error: Cannot find implementation or library stub for module named "elasticsearch"  [import-not-found]
gateway_v2/dispatch/vector_transports/elastic_transport.py:203: error: Cannot find implementation or library stub for module named "opensearchpy"  [import-not-found]
gateway_v2/dispatch/vector_transports/chroma_transport.py:45: error: Cannot find implementation or library stub for module named "chromadb"  [import-not-found]
Found 9 errors in 6 files (checked 243 source files)
```
All 9 errors are GW30 optional-extra vector client libraries not installed in this image (pre-existing). Base commit `1634da0c` gives the identical `Found 9 errors in 6 files` (checked 225 source files — file count differs only because GW33 added files; the 9 errors themselves are byte-identical, same files/lines/messages). Zero errors in any GW33 module.

### 5. `ruff check gateway_v2`
```
All checks passed!
```

### 6. `lint-imports`
```
Analyzed 294 files, 1752 dependencies.
--------------------------------------
Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT
Contracts: 3 kept, 0 broken.
```

### 7. AST gates (`python -m lint.<gate> gateway_v2`, cwd `/repo/gateway_v2`)
```
check_capacity_literals            exit=0
check_no_module_mutable            exit=0
check_frozen_dataclasses           exit=0
check_http_outside_edge_resolve    exit=0
check_sizes                        exit=0
check_single_authority             exit=0   (GW33 gate; no stale allow-list entries)
check_console_inventory            exit=0
check_tenant_scale                 exit=1
```
`check_tenant_scale` verbatim output:
```
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
```
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
