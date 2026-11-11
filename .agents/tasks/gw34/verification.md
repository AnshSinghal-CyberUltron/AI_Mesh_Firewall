# GW34 verification (offline gates; the live Docker/staging gate was NOT run)

Environment: host venv `/work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin` (Python 3.12, openai 3.42.1,
httpx 0.28.1, uvicorn 0.54.0, node v22.23.4, `openai` npm 4.104.0 from the committed lockfile).
Worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw34`, branch `gw34-sdk-compat`, run from `gateway_v2/`.

## Baseline (untouched tree, before any GW34 change)

```
=========================== short test summary info ============================
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3046 passed, 104 skipped, 8 warnings in 583.92s (0:09:43)
```
(mypy: 225 source files clean; ruff clean; import-linter 3 kept; the one failure is pre-existing:
`dispatch/vector_transports/pinecone_transport.py:140 keys`, untouched by GW34.)

## Final gates (verbatim)

```
$ mypy --strict gateway_v2
Success: no issues found in 238 source files

$ ruff check .
All checks passed!

$ lint-imports
Analyzed 289 files, 1708 dependencies.
--------------------------------------

Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT

Contracts: 3 kept, 0 broken.

$ python -m lint.check_capacity_literals
exit=0
$ python -m lint.check_no_module_mutable
exit=0
$ python -m lint.check_frozen_dataclasses
exit=0
$ python -m lint.check_http_outside_edge_resolve
exit=0
$ python -m lint.check_tenant_scale
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
exit=0
$ python -m lint.check_sizes
exit=0
```

Real exit codes of the six AST gates (the `exit=` lines above are `tail`'s):

```
check_capacity_literals exit=0
check_no_module_mutable exit=0
check_frozen_dataclasses exit=0
check_http_outside_edge_resolve exit=0
check_tenant_scale exit=1
check_sizes exit=0
```
`check_tenant_scale` exits 1 on exactly the pre-existing finding above (`pinecone_transport.py:140`); it
is identical to the baseline and no baseline entry was added. The other five exit 0.

## Full offline suite, container-run style: `python -m pytest -q -p no:cacheprovider`

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
1 failed, 3321 passed, 104 skipped, 1 xfailed, 9 warnings in 594.64s (0:09:54)
```

* Baseline: 3046 passed, 1 failed, 104 skipped. Final: 3321 passed, 1 failed (the same pre-existing
  `test_check_tenant_scale` failure), 104 skipped, 1 xfailed (Node SDK cell `c`, strict, see
  exit-note "Open findings"). +275 tests, 0 regressions, no test weakened except where R7.8 / R5
  required a changed expectation (listed in the commit messages and in deviations.md).
* The Docker SDK image (`Dockerfile.sdk-matrix`) builds (`sdk-image-build.json`); the matrix was run on
  the host venv, which resolves the same pinned `openai` Python and Node packages.
