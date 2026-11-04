# GW29 Incident response: kill switch, isolation, reroute, auto-isolation (plan pointer)

Status date: 4 November 2026. Branch `gw29-killswitch-isolation` (worktree `.worktrees/gw29`). Local half closed; live half open.

The spec lives in `.kiro/specs/gw29-killswitch-isolation/`:

- `requirements.md` and `design.md` (revision 7) say what is built and why.
- `tasks.md` is the executed plan (tasks 1 to 22), bottom-up on the import-linter layer order.

## What shipped

- Kill scopes `global`, `org:`, `om:<org>:<model>` and `model:` are checked by one gate in phase 4b, before admission and budget. A refusal charges nothing, takes no slot and makes no provider call.
- Isolation (`iso:<model>`) reroutes to a fallback chosen by one pure decision that never lowers compliance tags or sensitivity, or refuses.
- Live streams are re-checked per chunk and cut on a kill, an isolation, a revoke or a stale snapshot.
- Auto-isolation: a bounded risk tracker and an isolator that only tighten, plus a notifier pump and a ticker.
- Writer side: `state_control` projects org-model scopes and isolation records behind a rollout flag.

## Evidence

Everything is under `docs/plans/evidence/2026-11-04-gw29/`:

- `00_baseline.txt` and `01_caller_grep.txt` (task 1), `02_checkpoint_a.txt` (task 10)
- `02_suite_and_gates.txt` and `03_gate_exits.txt` (final gate, task 22)
- `04_negative_controls.txt` (four controls, each reverted)
- `05_bench_hot_path.txt` (per-request and per-chunk cost)
- `gates.sh` (the exact commands, container image `gw29-dev`)

## Declared open

Live L29-1 to L29-4 on a real stack, real notification delivery, fleet-wide convergence timing of a signed auto-isolation, and console gauge agreement in a browser (GW35). Local equivalents are `gateway_v2/tests/edge/test_lgw29_l29_*.py`.
