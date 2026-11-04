# GW31 offline gate (no live Docker/staging gate)

Run from gateway_v2/ with /work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin (CI invocation per .github/workflows/gateway-v2.yml). Output trimmed to summary lines.

## pytest -q -p no:randomly (full offline suite)
```
=========================== short test summary info ============================
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3175 passed, 105 skipped, 7 warnings in 605.61s (0:10:05)
57:E       AssertionError: assert [TenantScaleV... O(changes)')] == []
59:E         Left contains one more item: TenantScaleViolation(path='/work/suraj/AI_Mesh_Firewall/.worktrees/gw31/gateway_v2/gateway_v2/dispatch/vector_transports/pinecone_transport.py', symbol='keys', line=140, reason='whole-collection store read: cannot be O(changes)')
```
## mypy --strict
```
Success: no issues found in 299 source files
```
## ruff check gateway_v2 state_control lint tests
```
All checks passed!
```
## lint-imports
```

Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT

Contracts: 3 kept, 0 broken.
```
## AST gate CLIs (trees: gateway_v2, state_control) - all exit 0
```
python -m lint.check_sizes gateway_v2 -> exit 0
python -m lint.check_http_outside_edge_resolve gateway_v2 -> exit 0
python -m lint.check_no_module_mutable gateway_v2 -> exit 0
python -m lint.check_capacity_literals gateway_v2 -> exit 0
python -m lint.check_frozen_dataclasses gateway_v2 -> exit 0
python -m lint.check_dispatch_provenance gateway_v2 -> exit 0
python -m lint.check_sizes state_control -> exit 0
python -m lint.check_http_outside_edge_resolve state_control -> exit 0
python -m lint.check_no_module_mutable state_control -> exit 0
python -m lint.check_capacity_literals state_control -> exit 0
python -m lint.check_frozen_dataclasses state_control -> exit 0
python -m lint.check_dispatch_provenance state_control -> exit 0
```
## check_tenant_scale gateway_v2 -> exit 1
```
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
```

**PRE-EXISTING, not GW31:** the single violation is in dispatch/vector_transports/pinecone_transport.py (GW30 commit 564e2ecf, in this branch's base; GW31 diff touches no file in vector_transports). The same violation is the one failing pytest test (tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean). No GW31 code was changed by this gate.
