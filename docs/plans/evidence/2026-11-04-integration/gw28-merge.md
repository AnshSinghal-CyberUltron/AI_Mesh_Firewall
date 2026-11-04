# GW28 merge into integration-gw27-35
- Merge commit: 428aab15 (git merge --no-ff gw28-multi-model-routing onto GW27 merge 749e0c82)
- Fix commit: 03cae668 (function-size gate, see below)
- Handoff read: .kiro/specs/gw28-multi-model-routing/HANDOFF.md (no HANDOFF.md at gw28 root).
## Conflicts + resolutions
- edge/app.py: build_app signature kept BOTH `org_limits` (GW27) and `routing` (GW28); other hunks auto-merged (OrgLimits import + routing/app_defaults imports).
- edge/request_holds.py: __init__ kept GW27 `_controls/_org_id_charged/_org_tokens/_org_refund` AND GW28 `_routing` (__slots__ already merged both).
- No conflicts in AGENTS.md, progress doc, resources.py, routes.py, production_profile.py, dev_profile.py, sse.py. Line counts: app.py 755, routes.py 776, resources.py 770, request_holds.py 216 (all under gate).
## Branch interaction fix (03cae668)
After merge, check_sizes failed: build_app 121 lines and build_dev_dependencies 121 lines (max 120; GW27 + GW28 each added lines). Trimmed one docstring/comment line in build_app, and condensed comment + exports/holdback lines in build_dev_dependencies. No behaviour change; limit not raised.
## Gates (from gateway_v2/, venv /tmp/intv)
| Tool | Result |
|---|---|
| pytest -n 24 (AMF_EDGE_DOCKER=0) | 3319 passed, 107 skipped, 1 failed (test_check_tenant_scale::test_the_shipped_data_plane_is_clean = known pinecone_transport.py:140) |
| ruff check gateway_v2 state_control lint tests | all passed |
| mypy --strict | 8 errors, all import-not-found for optional vector libs (as at GW27); 0 other (256 files) |
| lint-imports | 3 kept, 0 broken |
| check_sizes/http_outside_edge_resolve/no_module_mutable/capacity_literals/frozen_dataclasses/dispatch_provenance x both trees | all exit 0 |
| check_tenant_scale | only known: pinecone_transport.py:140; state_control/valkey.py:100 |
| check_console_inventory | exit 0 |
Live Docker/staging gates not run in this step. GW28 deviation 12 (dev profile refuses non-_IDENT model names with 404) needs owner awareness.
