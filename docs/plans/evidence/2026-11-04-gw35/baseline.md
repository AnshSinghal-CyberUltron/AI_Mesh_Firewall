# GW35 baseline (the untouched head, `1634da0c`)

Measured on a detached worktree of `1634da0c`, the commit this branch is based on.

- `python -m lint.check_console_inventory` exits 0. The GW14b ledgers were clean before this card, so every drift line seen mid-card came from this card's edits and was fixed by regenerating the ledgers last.
- `python -m pytest tests/gates/test_check_tenant_scale.py tests/lint`: 1 failed, 18 passed. The failure is `test_the_shipped_data_plane_is_clean`, caused by `gateway_v2/dispatch/vector_transports/pinecone_transport.py:140` ("whole-collection store read"). It is pre-existing, outside this card, and unchanged by it.
- `python -m lint.check_sizes` exits 0 (`egress/stream.py` and `runtime/resources.py` at exactly 800, `edge/routes.py` 799, `edge/app.py` 800).
- Full offline pytest on the same tree, run from a copy outside the repo, is not a clean baseline: tests that read repo-relative paths fail from a copy (`test_audit_emit`, `test_v2_audit_assemble`, ledger and plan tests). The comparable numbers are the in-tree runs in `.agents/tasks/gw35/verification.md`.
- Golden responses for ten refusal and upstream-failure cases plus a streamed identity refusal were captured from this commit into `gateway_v2/tests/edge/golden/gw35_pre_change.json` (status, headers, body). The pre-change `upstream_error` response carried `x-zeroshield-action: block`; that is the defect this card fixes.
