# GW36 S2 evidence — control-to-state projector (3 November 2026)

Plan: `docs/plans/2026-11-03-gw36-s2-control-projector.md`. Every live result was read back with
the gateway's own `FeedReader`, appliers and `StampView` (`verify_state.py`), not by inspecting bytes.

| File | What it shows |
|---|---|
| `00_prefix_state.txt` | Before S2: re-hydrators failing every round (`amf_state_counter` does not exist), no stamp, two unsigned documents at `{rv2}:plan:*` |
| `01_rehydrators_without_projector.txt` | S2 re-hydrators create the tables, then withhold the stamp (no projector heartbeat); the withheld counter names the cause |
| `02_backfill_and_purge.txt` | `sync_v2_plan_documents --purge-legacy-plan-keys`: documents stored, 2 legacy raw documents purged |
| `03_gateway_view_after_backfill.txt` | Gateway reader: PLAN compiles (168 / 7 rules), live keys resolve to `zeroshield`, stamp fresh |
| `04_console_scenarios.txt` | Console API: key create, config save (change and identical save), deactivate, per-model switch (not enforced, logged), org-wide switch on/off, hard delete; each with its lag |
| `05_failover_and_outage.txt` | Clean-stop takeover 0.8 s with 0 withheld stamps; both down: first withheld at 16.9 s; recovery |
| `06_crash_takeover.txt` | SIGKILL of the writer: takeover at the lease TTL (5.3 s) |
| `07_round_cost_10k_keys.txt` | Round cost at 10,000 keys / 200 orgs on real Postgres (N23) |
| `08_full_offline_suite.txt` | 2916 passed, 100 skipped, 0 failed |
| `09_gates.txt` | mypy --strict, ruff, import-linter, AST gates |
| `10_state_control_live_pg.txt` | `tests/state_control` with a live Postgres 16 |
| `11_dev_profile.txt` | The dev gateway loads its plan from `{rv2}:devplan:{alias}` |

Scripts: `verify_state.py`, `run_live_s2.sh` (console scenarios; needs a console JWT in
`/tmp/gw36_s2_token`), `run_failover_s2.sh`, `measure_scale.py`.
