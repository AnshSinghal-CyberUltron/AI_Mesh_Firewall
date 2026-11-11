# GW31 verification (offline only)

Live Docker/staging gate: **NOT run, NOT claimed.** Real-Chroma L31-3: **NOT run** (deferred).
Environment: `/tmp/gw31venv`, run from `gateway_v2/`. Commit range `5bf82feb..HEAD` on `gw31-rag-pipeline`.

| Gate | Command | Result |
| --- | --- | --- |
| Full suite | `python -m pytest -q -p no:cacheprovider` | **3120 passed, 111 skipped, 1 failed** in 567 s |
| The 1 failure | `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean` | Pre-existing: `pinecone_transport.py:140` (GW30). Same failure on a clean `5bf82feb` checkout. |
| GW31 tests | `pytest tests/edge/test_gw31_* tests/dispatch/test_gw31_* tests/plan/test_gw31_* tests/domain/test_gw31_* tests/runtime/test_gw31_*` | all pass (included in the full run) |
| Size gate | `python -m lint.check_sizes gateway_v2` | exit 0 (`resources.py` 800, `app.py` 800, `routes.py` 798) |
| Capacity literals | `python -m lint.check_capacity_literals gateway_v2` | exit 0, allowed file list unchanged |
| Module mutable | `python -m lint.check_no_module_mutable gateway_v2` | exit 0 |
| Frozen dataclasses | `python -m lint.check_frozen_dataclasses gateway_v2` | exit 0 |
| HTTP outside edge | `python -m lint.check_http_outside_edge_resolve gateway_v2` | exit 0 |
| Dispatch provenance | `python -m lint.check_dispatch_provenance gateway_v2` | exit 0 |
| Tenant scale | `python -m lint.check_tenant_scale gateway_v2` | exit 1, only the pre-existing GW30 pinecone line |
| Admin ledger | `python -m lint.admin_ledger` | exit 0 |
| Console inventory | `python -m lint.check_console_inventory` | exit 0 (after `--write`) |
| import-linter | `lint-imports` | 3 contracts kept, 0 broken |
| ruff | `ruff check gateway_v2 state_control lint tests` | all checks passed |
| mypy | `mypy --strict` | 8 errors, all pre-existing `import-not-found` for optional store libs (identical on `5bf82feb`); no error in any GW31 file |

## Property tests (seeded `random.Random`, tagged `# Feature: gw31-rag-pipeline, Property N`)

P1 100, P2 100, P3 10,000, P4 10,000, P5 10,000, P6 1,000, P7/P12 1,000 (settings round trip),
P8 (queue, scripted cases), P9 100, P10 100, P11 1,000, P13 1,000, P14 200, P15 10,000, P16 1,000.
Floors in tasks.md: P3/P4/P5/P15 >= 10,000 met; P6/P7/P11/P13 >= 1,000 met; P1/P2/P9/P10/P14/P16
>= 100 met. P8 is covered by scripted adapter outcomes (raise, short count, writes-then-raises,
wrote-nothing code, chunk store full), not a random loop.

## Local L31 measurements (`tests/edge/test_gw31_live_local.py -s`)

- L31-1 recall on `/v1/rag/query` over an 8-document injection family: 1.00 (8/8), clean doc kept.
- L31-2 flood of 12 near-duplicates beside 2 related docs: post-cap flood share 0.50 of 4.
- L31-4 two projects, one org: each reads only its own documents and collections.
- Loop lag, 20 chunks at `chunk_text_bytes` (8192 B): worst tick delay about 6 ms (one run).

## Known gaps a reviewer should look at

- Req 6.5 publish/block/delete/allow rule cycle through a live plan feed is not tested (only
  in-process plan rules per stage).
- `/v1/vector/query` and the generator path were exercised against the dev in-memory adapter only.
- No test uses a real connector transport; the id round trip per shipped transport (design D-13) is
  covered by the table/coverage assertion on transport naming and by the metadata-free adapter fake,
  not by running each transport's fake client.
- `max_project_len` is not injected at boot (deviations.md item 7).
