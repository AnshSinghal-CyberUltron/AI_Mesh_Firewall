# GW31 baseline

- Venv: `/tmp/gw31venv` (python 3.12) with `pip install -e ./gateway_v2[dev]` plus `httpx`,
  `fakeredis`, `chromadb`, `pytest-asyncio`, `anyio` (the `[dev]` extra did not provide them; without
  `pytest-asyncio` 12 tests in `test_b4_*` fail, without `httpx` collection errors).
- Line counts before the work: `runtime/resources.py` 800, `edge/routes.py` 799, `edge/app.py` 800,
  `edge/dev_profile.py` 521, `edge/surface_entries.py` 40, `edge/rag.py` 3.
- Baseline gate state, measured on a clean checkout of `5bf82feb`: `tests/gates` + `tests/lint`
  125 passed, 1 failed (`test_check_tenant_scale::test_the_shipped_data_plane_is_clean`, caused by
  `dispatch/vector_transports/pinecone_transport.py:140`, GW30, not touched). `mypy --strict` reports 8
  `import-not-found` errors for optional store client libraries (weaviate, qdrant_client, pinecone,
  pymongo, elasticsearch, opensearchpy) in 5 files; `ruff` clean.
- A full-suite baseline on the untouched tree was NOT run. A full run taken after the first four tasks
  (before pytest-asyncio was installed) showed 12 env failures and 5 live-server errors unrelated to GW31.
- `compile_plan(` callers confirmed; the design list missed `edge/dev_components.py::load_dev_plan`
  and `edge/dev_plan_feed.py::_compile` (see deviations.md item 3).
