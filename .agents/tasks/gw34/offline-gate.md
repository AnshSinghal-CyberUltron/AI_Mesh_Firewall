# GW34 offline gate (fresh run)

Worktree: /work/suraj/AI_Mesh_Firewall/.worktrees/gw34 (branch gw34-sdk-compat, HEAD d38cadcc)
Environment: host venv /work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin (Python 3.12.3), run from /work/suraj/AI_Mesh_Firewall/.worktrees/gw34/gateway_v2.
Not run, not claimed: the live Docker/staging gate (L34-1 against a real v2 boot, L34-2 live).

## Summary
- mypy --strict: PASS; ruff: PASS; import-linter: PASS (3 kept, 0 broken)
- AST gates: capacity_literals, no_module_mutable, frozen_dataclasses, http_outside_edge_resolve, sizes PASS; tenant_scale FAIL (1 finding, file from GW30 commit 564e2ecf, not touched by GW34)
- Full pytest: 1 failed (tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean, same finding), 3321 passed, 104 skipped, 1 xfailed

## $ mypy --strict gateway_v2
```
Success: no issues found in 238 source files
EXIT 0
```

## $ ruff check .
```
All checks passed!
EXIT 0
```

## $ lint-imports
```

╔══╗─────────▶╔╗ ╔╗      ╔╗◀───┐
╚╣╠╝◀─────┐  ╔╝╚╗║║────▶╔╝╚╗   │
 ║║   ╔══╦══╦╩╗╔╝║║  ╔╦═╩╗╔╝╔═╦══╗
 ║║╔══╣╔╗║╔╗║╔╣║ ║║ ╔╬╣╔╗║║ ║│║╔═╝
╔╣╠╣║║║╚╝║╚╝║║║╚╗║╚═╝║║║║║╚╗║═╣║
╚══╩╩╩╣╔═╩══╩╝╚═╝╚═══╩╩╝╚╩═╩╩═╩╝
  └──▶║║                    ▲ 
      ╚╝────────────────────┘


---------
Contracts
---------

Analyzed 289 files, 1708 dependencies.
--------------------------------------

Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT

Contracts: 3 kept, 0 broken.
EXIT 0
```

## $ python -m lint.check_capacity_literals
```
EXIT 0
```

## $ python -m lint.check_no_module_mutable
```
EXIT 0
```

## $ python -m lint.check_frozen_dataclasses
```
EXIT 0
```

## $ python -m lint.check_http_outside_edge_resolve
```
EXIT 0
```

## $ python -m lint.check_tenant_scale
```
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
EXIT 1
```

## $ python -m lint.check_sizes
```
EXIT 0
```

## $ python -m pytest -q -p no:cacheprovider (final 15 lines, verbatim)
```
tests/admit/test_v2_leaks_lease_properties.py::test_property12_token_lease_conservation
  /work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/lib/python3.12/site-packages/fakeredis/_core/_database.py:147: RuntimeWarning: coroutine 'BudgetLease._refill' was never awaited
    def __init__(self, key: bytes, db: Database, item: CommandItem | None = None, default: Any = None) -> None:
  Enable tracemalloc to get traceback where the object was allocated.
  See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings for more info.

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
----------------- L34-1 matrix (ran/passed per client x cell) ------------------
python-sync   a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
python-async  a:1/1 b:1/1 c:1/1 d:2/2 e:2/2 f:1/1 g:1/1 h:1/1 i:1/1 j:2/2
node          a:1/1 b:1/1 c:0/1 d:1/1 e:1/1 f:1/1 g:1/1 h:1/1 i:1/1 j:1/1
=========================== short test summary info ============================
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3321 passed, 104 skipped, 1 xfailed, 9 warnings in 595.99s (0:09:55)
EXIT 1
```

## Failure analysis
The only failure is check_tenant_scale at gateway_v2/dispatch/vector_transports/pinecone_transport.py:140. `git log main..HEAD` on that file shows only 564e2ecf (GW30 Pinecone live-acceptance); GW34 commits do not touch it. tasks.md only documents a known state_control finding for this gate, so this one is new relative to that note but belongs to GW30. Fixing means changing GW30 dispatch code or the gate, which is out of GW34 scope; left unfixed, no baseline added.

**Decision (recorded):** accepted as pre-existing for GW34. No GW30 code or gate edit made in this lane. This finding belongs to the GW30 lane to fix.
