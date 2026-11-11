# GW33 offline verification (iteration 2)

Branch `gw33-output-parity`, worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw33`.
Run in the repo's container style (`gw29-dev:latest`, the image with pytest/mypy/ruff/import-linter), whole worktree mounted at `/repo`, cwd `/repo/gateway_v2`, `PYTHONPATH=/repo/gateway_v2`.
Mounting the whole worktree matters: tests read repo-root `control/`, `gateway/`, `deploy/` files. A first run that mounted only `gateway_v2/` produced 39 failed / 25 errors purely from missing mounts, and is NOT the result below.
Live Docker/staging gates were NOT run and are NOT claimed.

## Results (verbatim)

### pytest -q (full)
```
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
FAILED tests/gates/test_secret_headers.py::test_current_tracked_tree_yields_zero_hits
2 failed, 3279 passed, 106 skipped, 8 warnings in 632.12s (0:10:32)
```
Both failures are pre-existing and unrelated: re-run on the base commit `1634da0c` (tip of `suraj-revamp`, detached worktree) both fail identically (`2 failed in 0.21s`).
- `test_the_shipped_data_plane_is_clean`: the known tenant-scale finding in `edge/dev_components.py` (GW26 dev profile), recorded in AGENTS.md and the GW30 gate doc.
- `test_current_tracked_tree_yields_zero_hits`: needs `git ls-files`; the mounted worktree's `.git` is a file pointing outside the container (`fatal: not a git repository`).
0 new failures from GW33.

### pytest -q -k "gw33 or lgw33"
```
........................................................................ [ 91%]
....................                                                     [100%]
236 passed, 3151 deselected in 11.35s
```

### mypy --strict
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
Found 9 errors in 6 files (checked 299 source files)
```
All 9 errors are GW30 optional-extra vector client libraries not installed in this image. The base commit gives the identical `Found 9 errors in 6 files` (280 files). Zero errors in any GW33 module; no type errors of any other kind.

### ruff check gateway_v2
```
All checks passed!
```

### lint-imports
```
Analyzed 294 files, 1752 dependencies.
Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT
Contracts: 3 kept, 0 broken.
```
Layer contract kept, so egress does not import detect and resolve imports only domain.

### AST gates (`python -m lint.<gate> gateway_v2`, cwd `/repo/gateway_v2`)
```
check_capacity_literals            exit=0
check_no_module_mutable            exit=0
check_frozen_dataclasses           exit=0
check_http_outside_edge_resolve    exit=0
check_sizes                        exit=0
check_single_authority             exit=0   (new GW33 gate; no stale allow-list entries)
check_console_inventory            exit=0
```
Earlier in this iteration `check_single_authority` printed two "stale allow-list entry" lines. That was a wrong path argument (`gateway_v2/gateway_v2` from inside `gateway_v2/`), which made the scan root not exist. With the right root it exits 0.

## Property tests
Seeded `random.Random` loops (house idiom, no hypothesis), each tagged in-source with `# Feature: gw33-output-parity, Property N: ...`. Tag presence is enforced by `tests/gates/test_gw33_property_tags.py`, which passes. 48 tag lines across 16 distinct property numbers. Per-file iteration constants (all >= 100):

| File | `_ITERATIONS` |
|---|---|
| tests/edge/test_gw33_canary_inject.py | 500 |
| tests/resolve/test_gw33_resolver.py | 2000 |
| tests/resolve/test_gw33_overlap.py | 5000 |
| tests/egress/test_gw33_rewrite_verify.py | 2000 |
| tests/egress/test_gw33_delta_detector.py | 120 |
| tests/plan/test_gw33_template_check.py | 400 |
| tests/plan/test_gw33_compiler.py | 400 |
| tests/plan/test_gw33_publish.py | 400 |
| tests/detect/test_gw33_exfil.py | 1500 |
| tests/detect/test_gw33_holdback.py | 3000 |
| tests/detect/test_gw33_grounding.py | 600 |
| tests/detect/test_gw33_canary.py | 2000 |
| tests/detect/test_gw33_ip_leak.py | 3000 |
| tests/detect/test_gw33_unavailable.py | 400 |
| tests/domain/test_gw33_vocabulary.py | 2000 |
| tests/e2e/test_gw33_streaming.py | 150 |
| tests/lint/test_gw33_single_authority.py | 120 |

## L33 offline equivalents
Covered by the `-k "gw33 or lgw33"` subset above (236 passed): L33-1..3 equivalents, bounded-work, force-release rewrite, canary lifecycle, audit aggregate counts a decision once, grounding timeout routing, control-plane map/publisher/serializer (`tests/plan/test_gw33_publish.py`, 10 tests, pass once the repo-root `control/` is mounted).

## Explicitly deferred (NOT run, NOT claimed)
- Live Docker/staging gate: real-wire SSE rewrite/holdback behavior, live grounding model call, live canary round trip, live control-to-data-plane publish.
- Fleet-scale/cloud gates inherited from GW12b (L12b-2) and GW20.
- Django control-plane test run beyond the offline publish tests listed above.
