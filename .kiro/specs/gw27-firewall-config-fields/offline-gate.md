# GW27 offline gate (re-run at HEAD e4198e68)

Env: host venv /tmp/gw27v (Python 3.12.3, gateway_v2[dev,serve,conformance] editable from this worktree). All commands run from `gateway_v2/`, mirroring `.github/workflows/gateway-v2.yml` `structural-gates`. Live Docker/staging gate NOT run, NOT claimed.

| Command | Result |
|---|---|
| `/tmp/gw27v/bin/python -m pytest -q -p no:cacheprovider` | 1 failed, 3208 passed, 107 skipped (616.99s) |
| `/tmp/gw27v/bin/ruff check gateway_v2 state_control lint tests` | All checks passed |
| `/tmp/gw27v/bin/mypy --strict` | 9 errors in 6 files (285 checked), ALL `import-not-found` for optional vector-DB client libs (weaviate, qdrant_client, pinecone, pymongo, elasticsearch, opensearchpy, chromadb) not installed in this venv; 0 errors in GW27 files |
| `/tmp/gw27v/bin/lint-imports` | Contracts: 3 kept, 0 broken |
| `python -m lint.check_sizes / check_http_outside_edge_resolve / check_no_module_mutable / check_capacity_literals / check_frozen_dataclasses / check_dispatch_provenance` for `gateway_v2` and `state_control` | all 12 exit 0 |
| `python -m lint.check_tenant_scale gateway_v2` | exit 1: 1 violation, `dispatch/vector_transports/pinecone_transport.py:140` (pre-existing) |
| `python -m lint.check_console_inventory` | exit 0 |
| `python -m pytest -q -p no:cacheprovider tests/gates/test_gw27_capacity_gate_allowlist.py` (D-13 two-file allowlist gate) | 4 passed |

## Pre-existing, unrelated to GW27 (not hidden)
- `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean` fails because of the tenant_scale violation above; the file is in the vector_transports tree, not a GW27 change, and the same failure is in the Task 0 baseline.
- mypy 9 errors: missing optional third-party packages in the venv (env), identical to the earlier run.
- Baseline had 5 errors in `tests/edge/test_edge_live_l16b.py` (live sockets); they did not recur.

## Not run
Control-tree Django tests, live Docker/staging gates (L27-1, L27-2), live Postgres arm, owner sign-offs D-8/D-12.

## Verdict
No GW27-caused failures. No code changes needed.
