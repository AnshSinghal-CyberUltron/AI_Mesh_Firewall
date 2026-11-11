# GW35 offline gates report
Branch `gw35-console-truth`, worktree `.worktrees/gw35`, base `suraj-revamp` 1634da0c. Offline gates only. NOT run and NOT claimed: live Docker/staging gate (L35-2), Playwright sweep (L35-1), browser walk (L14b-3), owner signatures.
Python gates ran in the venv `/tmp/gw35venv` (`pip install -e .[dev,conformance]` + uvicorn, httpx). Frontend ran in `node:22-alpine` with `npm ci`. Detail of what landed: `docs/plans/evidence/2026-11-04-gw35/SUMMARY.md`.

## What landed (grouped by defect)
1. Console stages invented/fabricated: one builder `edge/console_stages.py::build_stages(ChatFacts)` over recorded request facts; auth and rate_limit are real stages; no invented stages or latency; body, SSE `amf.trace` and audit read the same facts; `source: "gateway_v2"` carrier.
2. Operational failure shown as BLOCK/allow: honest `error` action end to end (`console_schema.py` vocabulary and severity, `domain/failure_class.py` table, gateway trace, control drain rank, frontend "Upstream error").
3. Failed credentials left no trace: one `admission` audit row per rejected credential under reserved org `_unauthenticated`, 12-hex key fingerprint only, rate-bounded by `UnauthAuditGate`, never delaying the 401/503.
4. Control plane truth: `v2_audit_adapter` error rank, `refused_at`, v2 totals (`refused_total`, `error_total`), retired routes answer 410 `retired_on_gateway_v2` under `AMF_GATEWAY_BACKEND=v2` (default v1 unchanged).
5. Frontend/lint drift: Scan Detail renders v2 bodies as returned, six "Retired on gateway v2" panels; stage-vocabulary parity lint, contract diff, signed-row ledger reporting, GW14b ledgers regenerated.
(Defect numbering is my grouping of the card's changes; the card text itself was not available to this step.)

## Gate results (verbatim, tail-trimmed where marked)
### Full offline pytest: `cd gateway_v2 && python -m pytest tests -q`
```

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3188 passed, 107 skipped, 8 warnings in 589.09s (0:09:49)
exit=1
```
### ruff / mypy --strict / import-linter / AST gates / console inventory / ledger
```
$ ruff check .
All checks passed!
exit=0
$ python -m mypy --strict gateway_v2
gateway_v2/dispatch/vector_transports/weaviate_transport.py:46: error: Cannot find implementation or library stub for module named "weaviate"  [import-not-found]
gateway_v2/dispatch/vector_transports/weaviate_transport.py:139: error: Cannot find implementation or library stub for module named "weaviate.classes.config"  [import-not-found]
gateway_v2/dispatch/vector_transports/qdrant_transport.py:48: error: Cannot find implementation or library stub for module named "qdrant_client"  [import-not-found]
gateway_v2/dispatch/vector_transports/qdrant_transport.py:122: error: Cannot find implementation or library stub for module named "qdrant_client.http.exceptions"  [import-not-found]
gateway_v2/dispatch/vector_transports/pinecone_transport.py:63: error: Cannot find implementation or library stub for module named "pinecone"  [import-not-found]
gateway_v2/dispatch/vector_transports/mongo_transport.py:52: error: Cannot find implementation or library stub for module named "pymongo"  [import-not-found]
gateway_v2/dispatch/vector_transports/elastic_transport.py:137: error: Cannot find implementation or library stub for module named "elasticsearch"  [import-not-found]
gateway_v2/dispatch/vector_transports/elastic_transport.py:203: error: Cannot find implementation or library stub for module named "opensearchpy"  [import-not-found]
gateway_v2/dispatch/vector_transports/chroma_transport.py:45: error: Cannot find implementation or library stub for module named "chromadb"  [import-not-found]
gateway_v2/dispatch/vector_transports/chroma_transport.py:45: note: See https://mypy.readthedocs.io/en/stable/running_mypy.html#missing-imports
Found 9 errors in 6 files (checked 231 source files)
exit=1
$ lint-imports
---------
Contracts
---------
Analyzed 282 files, 1639 dependencies.
--------------------------------------
Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT
Contracts: 3 kept, 0 broken.
exit=0
$ python -m lint.check_sizes
exit=0
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
exit=1
$ python -m lint.check_console_inventory
exit=0
$ python -m lint.admin_ledger
/v1/observability: pending signature
/v1/policy/check: pending signature
/v1/usage: pending signature
exit=0
$ python -m pytest tests/lint/test_console_inventory.py -q
..                                                                       [100%]
2 passed in 3.78s
exit=0
```
### Frontend (node:22-alpine, `npm ci`), tails of `npm run lint`, `npm run test:unit`, `npm run build`
```
# tests 208
# pass 208
# fail 0
# skipped 0
lint exit=0
# tests 208
# pass 208
# fail 0
# skipped 0
dist/assets/index-DjHMDlUR.css    224.66 kB │ gzip:  28.53 kB
dist/assets/index-Cf2Ruvf_.js   2,211.66 kB │ gzip: 646.08 kB
✓ built in 4.88s
```

## Known pre-existing failures
- `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean` and `python -m lint.check_tenant_scale` (exit 1): `dispatch/vector_transports/pinecone_transport.py:140` "whole-collection store read". Proven pre-existing this run: on a detached worktree of `suraj-revamp` (1634da0c) the lint exits 1 with the identical line and the pytest gives `1 failed, 12 passed`. This card does not touch that file (empty diff vs suraj-revamp).
- `mypy --strict`: 9 `import-not-found` errors, all in `dispatch/vector_transports/*` for optional vendor SDKs (weaviate, qdrant_client, pinecone, pymongo, elasticsearch, opensearchpy, chromadb) not installed in this venv. No file touched by this card. (Not re-run against suraj-revamp; the cause is the missing packages, not code.)
- `python -m lint.admin_ledger --require-signed` exits 1 by design until the owner signs the three D-11 rows (`/v1/observability`, `/v1/policy/check`, `/v1/usage`). Plain `admin_ledger` exits 0.
- `state_control/valkey.py:100` tenant-scale finding (R2-05) is documented, untouched.
- Control-plane Django suites were run in the earlier card step (41 tests OK, throwaway postgres); not re-run in this step. See `.agents/tasks/gw35/verification.md`.

## Remaining for the human
- Live Docker/staging gate (L35-2) with `AMF_GATEWAY_BACKEND=v2` and `AMF_V2_AUDIT_ORG_MAP=dev-org=zeroshield,_unauthenticated=zeroshield` in the control environment.
- Playwright sweep (L35-1): needs the running stack; cannot run offline.
- Browser walk of every console page (L14b-3), including the 276 unclassified v1-field reads listed in `page-audit.md` (module pages: RAG, routing audit, output governance, module charts).
- Owner signatures on the three D-11 ledger rows and the corrected dropped-stage wording; owner decision on `_UNAUTH_AUDIT_RATE_MULT = 2` (waits on signed capacity).
- Decide on the pre-existing pinecone tenant-scale finding (separate card).
