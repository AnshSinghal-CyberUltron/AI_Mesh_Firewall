# GW27 build review: firewall config fields (15 built, 30 retired, 1 delegated)

GW27 gives every one of the 45 firewall config fields an explicit disposition. 15 are built, 30 are retired by name with an owner and reason, and org_token_budget is delegated to GW36. Built controls travel as a plan schema 1 `controls` member, with a gateway-side `OrgLimits` limiter and audit gating, and the audit exporter gets a retention sweep.

Watch for: live gates L27-1 and L27-2 are open (confirmed, disclosed). Two file-size caps are at their limits (confirmed, minor).

**Verdict**: APPROVED

## High-level view

The control-side field map is the single source of truth. Its `FIELDS` table classifies each field as identity, mapped, built or retired. A retired field's changed value is refused at save with its owner and reason, so a value is never stored and then ignored. Controls-backed fields are refused while `V2_PLAN_CONTROLS_ENABLED` is off, and the plan carries them as an optional `controls` member. Plans without controls hash as before (Property 5).

On the gateway, `admit/org_limits.py` holds the per-org request and token buckets. The limiter is held on the instance, not at module level. Any internal fault returns a 429 verdict. A full table refuses and counts rather than resetting a limiter. Bounds come from `runtime/resources_org.py`, and the capacity-literal gate allowlist is pinned by a test.

## Checks against the brief

- Field accounting (confirmed). Property 1 asserts that the snapshot holds 46 rows. It also asserts 15 built, 30 retired and 1 delegated, and that these sets cover the snapshot exactly. Every retired entry has an owner and a reason and keeps its off values. The committed `retire-list.json` ledger is tested equal to the map.
- Layering (confirmed from imports). `admit/org_limits.py` imports only `admit.grant`, `domain.plan` and `runtime`. The verification file reports import-linter with 0 broken and every AST gate at exit 0.
- Module-level mutable state (confirmed). A grep of the new gateway modules found no module-level mutable assignments. The AST gate `check_no_module_mutable` is reported clean.
- Fail-closed (confirmed). `OrgLimits.check` refuses on any exception, and Property 8 injects faults to prove it. An unusable contract is a boot failure. A full table refuses. A decode that omits a required rate or audit member is refused.
- Tests (confirmed). 15 `# Feature: gw27` tagged property tests exist, covering Properties 1 to 13. Verification reports seeded `random.Random` runs of 10,000 iterations, and I saw `range(10_000)` in the tests. A full-suite result of 3208 passed, with 1 failure identical to baseline, is recorded. No suite was re-run, as instructed.

## Issues (non-blocking)

1. LOW: `routes.py` and `app.py` sit exactly at the 800-line cap. The next change to either file must split it first.
2. LOW: Property 3 is exercised gateway-side. The control-side check is example tests only. This is disclosed and acceptable.
3. LOW: Live gates L27-1, L27-2, the live Postgres EXPLAIN arm and the live entry-size model are open. They are declared in `offline-gate.txt` and are not claimed as passed.
4. LOW: Embeddings emit no audit records, so audit gating covers chat, responses and MCP only. This is disclosed.
