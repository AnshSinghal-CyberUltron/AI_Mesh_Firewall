# GW28 final OFFLINE gate (live Docker/staging gate NOT run, NOT claimed)
Run from `gateway_v2/` with V=/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin, HEAD 7b6e9186.
Commands per tasks.md / .github/workflows/gateway-v2.yml (structural-gates + secret-headers).

| Gate | Result |
|---|---|
| check_sizes, check_http_outside_edge_resolve, check_no_module_mutable, check_capacity_literals, check_frozen_dataclasses, check_dispatch_provenance x {gateway_v2, state_control} | all 12 exit 0 |
| check_tenant_scale gateway_v2 | exit 1: `gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)` / `1 tenant-scale violation(s).` PRE-EXISTING (GW30 commit 564e2ecf; identical in baseline_pytest.txt); GW28 does not touch that file |
| lint-imports | exit 0: `Contracts: 3 kept, 0 broken.` (Analyzed 287 files, 1689 dependencies) |
| ruff check gateway_v2 state_control lint tests | exit 0: `All checks passed!` |
| mypy --strict | exit 0: `Success: no issues found in 291 source files` |
| check_console_inventory | exit 0 |
| check_secret_headers --fail-allowlist | exit 0 |
| pytest -q (full offline) | exit 1: `1 failed, 3157 passed, 104 skipped, 8 warnings in 614.24s (0:10:14)`; sole failure `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean` (same pinecone finding). Baseline: `1 failed, 1992 passed, 22 skipped` with the SAME single failure => zero new failures |

Property tests (tagged `# Feature: gw28-multi-model-routing, Property N` in source): iterations
P1-3 10,000; P4 300; P5 250; P6 200; P7 100 per scenario; P8 250; P9 10,000; P10 200; P11 200; P12 structural. All >= 100.
