# GW27 merge into integration-gw27-35
- Merge commit:  (git merge --no-ff gw27-firewall-config-fields onto suraj-revamp 1634da0c)
- Conflicts: none (clean merge; GW27 branch sat on suraj-revamp tip). No fix commits.
- Env: fresh venv /tmp/intv, editable from integration worktree, extras dev,serve,conformance,vectors + pytest-xdist. Run from gateway_v2/.
## Gates
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 3208 passed, 107 skipped, 1 failed (tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean = known pre-existing pinecone_transport.py:140) |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict | 8 errors, all import-not-found for optional vector client libs not installed in venv (weaviate/qdrant/pinecone/pymongo/elasticsearch/opensearchpy/chromadb); 0 other errors (285 files) |
| lint-imports | 3 kept, 0 broken |
| lint.check_sizes/http_outside_edge_resolve/no_module_mutable/capacity_literals/frozen_dataclasses/dispatch_provenance x gateway_v2,state_control | all exit 0 |
| lint.check_tenant_scale | gateway_v2: 1 violation pinecone_transport.py:140 (known); state_control: valkey.py:100 (known R2-05) |
| lint.check_console_inventory | exit 0 |
Live Docker/staging gates (L27-1, L27-2) not run in this step.
