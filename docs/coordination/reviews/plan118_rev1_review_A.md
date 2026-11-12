**Plan Reviewer A (completeness & correctness): REQUEST CHANGES**

Reviewed at head `b284dc9e`. Every file:line claim about current behaviour was re-checked against `origin/develop` `943a5f21`. The only drift since the plan's base `25c33c86` is `edge/scan_calibration.py`, some tests and CI files, and no cited file changed. Citations are accurate to within a line or two, and none is wrong. The parent is `e2c3e2cf` (§6, D1–D5, §10).

The plan is strong. The stamp work is correct and reproduced, and the scale analysis is the right kind of evidence. Two design holes remain. Both lead to a fleet-wide wrong answer that the plan's own mechanisms report as fresh or fail to recover from.

## Reproduction (plan's own scripts, at head)

| Claim | Plan | Reproduced here | Verdict |
|---|---|---|---|
| Stamp byte-identity, 4 kinds (`stamp_compat.py`) | 5,000/5,000 identical; HEAD accepts step-1; HEAD refuses `model`; HEAD overwrite moves time back 400 ms | identical results, all asserts pass | reproduced |
| Stamp decode cost | 20.5 vs 26.5 µs | 20.6 vs 21.6 µs | "faster than HEAD" is noise; immaterial |
| Projector CPU (`projector_bench.py`) | cold 8.24 s, steady 3.8 ms, 1% churn 47 ms, poison → `row_invalid: 1` | 7.95 s / 3.3 ms / 46 ms / `row_invalid: 1` | reproduced (see NIT 6 on the row memo) |
| View memory (`model_record_bench.py`) | 6,179 B/org, 6,788 orgs at 40 MiB | 6,179 B/org exactly | reproduced |
| Record sizes and costs | 2,068 B typical, 452,009 B worst; apply 108 µs | same sizes; apply 70 µs | reproduced |
| O(1) lookup (`routing_scan_head.py`) | 1.00 probes at 1k and 100k; HEAD 12 µs → 1.2 ms, 55 µs → 5.1 ms | 1.00 / 1.00; HEAD 13 µs → 1.3 ms, 80 µs → 5.7 ms | reproduced |
| AES-GCM (`aead_bench.py`, py3.12) | 0.86 µs cached, 2.46 µs per call | 0.82 / 2.29 µs | reproduced (see NIT 9) |
| Postgres at 100k × 5 (`pg_source_bench.py`, under `.ctl-db.lock`) | naive row read cancelled; revisions 72–98 ms; changed 1k 289–297 ms; MODEL snapshot 221 MB, cancelled in run 2; trigger 1,983 = 1,983 | row read cancelled at 904 ms; revisions 133 ms; 1k changed 281 ms; 2k 516 ms; **MODEL snapshot completed, 1,003 ms wall, 221 MB** (server 33 ms); trigger 1,983 = 1,983 | reproduced. Whether the 221 MB snapshot is cancelled depends on load: 1 of 3 runs. Material to B2 |

## BLOCKERS

### B1. MODEL freshness is not tied to MODEL projection, so a projector that is not projecting MODEL serves frozen catalogues under a fresh stamp (§6.10, §6.6, §6.1)

- **The mechanism.** The re-hydrator withholds the stamp only on the **single** projector heartbeat (`rehydrate.py:477-490` `_source_check`; `projector.py:224` `mark_projected`). Otherwise it stamps every kind whose store matches Postgres (`rehydrate.py:427-440`).
- **What the plan adds.** Once `floor.stamp_kinds` names `model` (R5), a re-hydrator with the capability on stamps MODEL whenever `amf_state_record(kind='model')` matches the store (§4.3).
- **The gap.** Nothing checks that the projector is deriving MODEL from control. In every case below, the projector keeps heartbeating, the frozen MODEL records still match Postgres, MODEL stays "covered", and every gateway with MODEL on serves the old catalogue indefinitely. A console deactivation, and in F1a-3 a key rotation after compromise, never takes effect, with no bound.
  1. `AMF_STATE_PROJECT_MODELS` turned off after R6. That is **§8's own R4 rollback** ("Flag off (records stay; nobody reads them)"). After R6, gateways do read them.
  2. A projector image rolled back to generation 2 at floor 3. §3.2 makes gateways and re-hydrators refuse below the floor, but **not the projector**: it only "refuses to emit a kind … whose generation exceeds" the floor. Generation-2 images stay deployable after R2. A generation-1 projector does the same, and is barred only by policy.
  3. `floor set --min-generation 2 --allow-lower` while `stamp_kinds` still names `model`. The projector then refuses MODEL (§4.2.1) and keeps heartbeating.
  4. The revision table is missing, for example after a restore of control without the migration. §4.2.2 makes this "a refusal of MODEL projection, logged", with the heartbeat still advancing.
- **Why the tests miss it.** L2.3 tests only a *stopped* projector, which is the one case that does fail closed.
- **Required.**
  - Make MODEL coverage depend on MODEL projection. One way: a MODEL projection heartbeat (a column or row in `amf_state_projector`, written only by a round that ran 3m in full). The re-hydrator then leaves `model` out of the stamp, or refuses it, when that heartbeat is older than `lag_ms`. With the per-kind coverage of §3.1(4), only MODEL then fails closed.
  - A projector below `floor.min_generation` refuses to heartbeat.
  - Fix the R4 rollback text.
  - Add L2.3 variants (flag off, projector image rolled back to generation 2) with the same 21 s assertion.

### B2. MODEL whole-kind repair at 100k orgs: the read side is unchanged, and routine faults trigger it (§6.2, §6.1, exit 4)

- **What triggers a repair.** Every non-`None` diagnosis triggers `repair(kind)`, a republish of the whole kind (`rehydrate.py:433-440`, `381-412`). That includes `STALE` (`rehydrate.py:328-329`): the store is behind Postgres after any `OK_PUBLISH_PENDING`, which is what every Valkey publish hiccup during a console edit produces (`writer.py:489-506` `_publish_one`, `166-178` batches). It also includes `MISSING`/`INVALID` after a Valkey restart or failover, and `CONTENTS` from deep verify. Deep verify is also the detection path for the §4.4.3 replay bound.
- **What a repair reads.** `repair` reads the **whole kind in one statement**: `_snapshot` → `pg.py:592-621` `tx.records(kind)`. For MODEL at 100k orgs that is the 221 MB read the plan itself measures: cancelled by the 800 ms bound in author run 2, 1.0–1.4 s wall otherwise.
- **What happens on cancellation.** `ControlPlaneBoundExceeded` withholds the stamp rather than degrading it (`rehydrate.py:441-451`). `_claimable` withholds when *any* stamped kind is unverified (`rehydrate.py:623-636`). So once the floor names `model`, one failed MODEL publish can withhold the stamp for **every** kind, fleet-wide, every round, until a repair read happens to finish in time.
- **When it does complete.** Each repair still moves 221 MB out of Postgres and about 221 MB into Valkey.
- **What §4.3 covers and misses.** §4.3 makes verify index-only and the *publish* chunked. It does not address the repair *read*, or the fact that `STALE` repairs the whole kind.
- **Required.**
  - An incremental `STALE` repair: republish records with `feed_seq` above the store's manifest, using the `amf_state_record (kind, feed_seq)` index (`schema.py:62`, "changed since position P … without a whole-kind scan").
  - A keyset-paginated full-repair read, each statement well under the bound, for `MISSING`/`INVALID`/`STORE_AHEAD`/`CONTENTS`.
  - A T2.15 extension at 100k × 5 against real Postgres, plus a live repair after `FLUSHALL`.

## SHOULD-FIX

1. **§6.6 "round-time test at 100k × 5" is weakened to MODEL-phase CPU against twins, and the full round still cannot meet it.**
   - T2.16 asserts CPU budgets against an in-memory source. The Postgres and Valkey I/O is "measured and recorded", with no budget.
   - At 100k orgs, `projector.py:220` still snapshots PLAN, KEY, KS and BUDGET whole every round, and the re-hydrator repairs them whole. PLAN bodies are the same size class as MODEL bodies (`dev/plan.example.json` is 1,762 B against MODEL's 2,068 B), so the PLAN snapshot is the same ~200 MB read that §4.2.2 shows does not fit.
   - Exit 4 and §12 therefore claim a requirement the system cannot meet.
   - Either extend the design to the other kinds' snapshots (index-only or revision reads), or add the gap to §9 with evidence and give T2.16 a real-Postgres full-round wall-time budget. As written it is neither.
2. **The fleet floor (§3.2) is incomplete.**
   - **(a) Bootstrapping.** The initial `min_generation` is unstated: "(1, {plan,key,ks,budget})" gives the id and the kinds only. R2 implies it is below 2. State it.
   - **(b) Postgres outage.** The floor is read from Postgres every round. The plan does not say what a re-hydrator does when that read fails during the 16 s ride-through (`rehydrate.py:643-666`, L05b-4 requires zero 503s). If the read failure withholds the stamp, release 1 regresses L05b-4. State "last floor read wins during ride-through; none read → no stamp", and test it.
   - **(c) Publication race.** `{ns}:floor` is published "when it changes" by every re-hydrator, with no version or compare-and-set. A re-hydrator that read the old row can overwrite the newer one. Add a monotonic floor revision and publish it like the stamp.
   - **(d) CLI checks.**
     - `floor set --stamp-kinds +model` does not check that every live re-hydrator has `AMF_STATE_REHYDRATE_MODEL` on. Every re-hydrator with it off "refuses to stamp at all" (§4.3), so a misordered R5 withholds the stamp for every kind, fleet-wide.
     - The CLI should also refuse a `stamp_kinds` set missing a base kind.
     - Have re-hydrators and the projector register their generation and capabilities in Postgres each round, and make the CLI refuse when they do not cover the requested floor.
   - **(e) Generation-1 alarm is blind.** The R1 gate and the alarm "`min(amf_state_generation) < floor`" cannot see generation-1 targets: they do not export `amf_state_generation`, which is new in release 1. Use scraped targets that lack the series (for example `up unless on(instance) amf_state_generation`).
3. **L2.5 and exit 3 contradict the floor.** L2.5 runs generation-2 and generation-3 gateways and re-hydrators "through rollout steps R2→R5" and passes when "every gateway stays ready at every step". At R4 the floor becomes 3, so by §3.2 generation-2 gateways go unready and generation-2 re-hydrators refuse to stamp. Split the test: mixed through R3, then assert that generation-2 is refused from R4.
4. **MODEL in `READY_KINDS` (§4.4.4) undoes §9.3's blast-radius argument.**
   - `/readyz` fails if any listed kind lags (`state_gate.py:69-77`, `production_profile.py:470-475`).
   - With MODEL listed, one MODEL round fault (a round-fatal fetched-record defect, §4.4.1) or a coverage lapse takes the whole worker out of the load balancer: MCP, embeddings and moderations included. That is the same blast radius §9.3 rejects for the stamp.
   - Decide and state the choice: for example, MODEL gates readiness only until its first catch-up, and is a request-level 503 after that.
5. **GW29 under MODEL leaves the fleet catalogue undefined (§6.9).**
   - Today `_resolve_model` (`incident_gate.py:148-162`) has three fleet behaviours:
     - it applies the **fleet alias hop** (`canonical_model(raw, aliases)`, `incident.py:426-427`; aliases from `AMF_MODEL_CATALOG`, wired at `production_profile.py:492`);
     - it refuses any name without a fleet profile (`unknown_model`);
     - it resolves `auto` through `AMF_MODEL_AUTO_TARGET`.
   - §4.4.5 and §4.7 say the gate "reads the pinned view", but never say what happens to these three.
   - If they are kept, every org alias missing from the fleet file is refused. A fleet alias can also rewrite an org's `gpt4` into `gpt-4o` before the org lookup and pick the wrong entry.
   - If they are removed, the kill-switch parser's alias canonicalisation of `model:` scopes changes too.
   - State the rule, the precedence of `AMF_MODEL_AUTO_TARGET` against the org `default`, and the fate of `AMF_MODEL_FALLBACKS`, and add them to T2.11.
6. **Auto-isolation's "only if a destination exists" invariant breaks with per-org fallbacks.**
   - `auto_isolation.py:6-12` (C6/C20) isolates only when `decide_route` finds an eligible fallback, "so auto-isolation cannot turn a degraded model into a self-inflicted outage".
   - With isolation aggregated per `provider/upstream` across orgs (§4.7) and the fallback resolved per org view, an org with no matching entry gets `REROUTE_REFUSED`/`fallback_not_served`. That is exactly the outage C6/C20 forbids.
   - Define the per-org application: for example, a process-local isolation applies to an org only when its view has an eligible fallback.
7. **Embeddings is not covered.**
   - `/v1/embeddings` names a model, runs its own phase-4a gate (`openai_misc.py:382-383`, `incident_misc.py:1-12`) and dispatches through `deps.provider` (`openai_misc.py:631`).
   - §4.4.5 defines phase 4m and resolution for chat only (responses inherit it through `run_lifecycle`).
   - Add embeddings: pin, resolve, filters, the D4 answer and the GW29 match set. Otherwise say it stays on the stub with a declared refusal.
8. **A per-org assembly failure keeps the previous record (§4.2.6).** That skips removals while the heartbeat claims they are durable, against §6.6. Fail closed for that org instead (for example, every held key omitted `row_invalid`), and add it to T2.5.
9. **Filters (§4.6) are not v1-exact, and the safety merge is ambiguous.**
   - **(a) v1 gating.** v1 enforces the org `allowed_models` only when `model_isolation_enabled` is true (`main.py:7899-7905`). That field is retired in v2 with "off" allowed (`firewall_v2_map.py:69-73`), and §4.6 does not carry it. Orgs with isolation off and a stale list would be over-refused at R7.
   - **(b) Legacy values.** Values stored before retirement, including key `allowed_models` that are "stored and ignored" today, become enforced at R7. A key list over 64 entries makes the key fail to resolve (401). The GW17c precedent in `_mcp_policy` is "over-bound or malformed → deny-all", projected, not an unresolvable key.
   - **(c) The safety merge.** For `allowed`, "the more restrictive of H and D" must be the **intersection**. Picking one side keeps a removed name allowed before the heartbeat.
   - **(d) Empty after drops.** An allowlist whose names are all dropped must stay `[]`, not become `null`.
   - Add an R7 pre-flight report of affected orgs and keys.
10. **§9.6 is a dodge.**
    - Control has per-org isolation today: `ModelState` with status `isolated`, `action` reroute/block and `fallback_model` (`core/models.py:1800-1870`).
    - v1 enforces it (`model_state:{org}:{model}`, `main.py:10567`). v2 never reads it (no reference in `gateway_v2/` or `state_control/`).
    - "Nothing to re-key" is true of v2's code. The console feature is still silently unenforced, the same "stored and ignored" state §9.1 objects to.
    - Either project it (it is per-org model data and fits the MODEL record) or list it in §10/§13 as an owner decision.
11. **The test plan has unmeasured items and missing tests.**
    - **No budget:** §5 "what remains to measure live" (`put_existing` I/O, the trigger's server-side cost, Valkey operations per MODEL write) and T2.16 "measured and recorded". Give budgets or mark them informational. Note that the trigger's write cost on this host is noise-level: 0.78 / 0.56 / 1.37 ms here.
    - **Missing tests:**
      - B1 and B2, as above;
      - the floor read during a Postgres outage;
      - slug renames (§4.2.4 says the record must exist pre-heartbeat; nothing tests it);
      - the continuous reconcile (disable the trigger, edit, expect a repair within `orgs/1000` rounds and `reconcile_fixes_total > 0`);
      - re-onboarding after a tombstone (`put_many(require_new=True)` raises `RecordExists` on a tombstoned slug, so say these orgs go through `put_existing`);
      - compliance-tag canonicalisation (NIT 2).
12. **The pre-heartbeat read of H is unaccounted for.** S and the breaker need the served keys of H for every changed org. The memo holds only `(slug, rev, content_hash)`. That is one more read of `amf_state_record` bodies per 500-org chunk before the heartbeat. Add it to §4.10, §5 and the "26k–53k orgs per `lag_ms`" limit, or put the served key set in the memo.
13. **`amf_state_log` growth.**
    - Every MODEL write appends its full body to `amf_state_log` (`pg.py:203`), which has no pruning anywhere.
    - A malicious admin toggling one model produces up to 2 writes per round. With F1a-2 bodies near 100 KB at the field maxima, that is roughly 17 GB/day from one org, and roughly 80 GB/day with F1a-3 ciphertexts.
    - §6 lists churn as bounded. Bound the liveness writes per org (safety writes stay immediate), or add log retention.

## Author's §9 list, judged

| §9 | Verdict | Evidence |
|---|---|---|
| 1. Key `allowed_models` is not retired, it is stored and ignored | **Plan right**, parent premise wrong | `gateway_serializers.py:83-88` accepts it; `_key_body` (`projector.py:584-599`) and `principal_of` (`identity.py:88-108`) never touch it |
| 2. "Unknown provider or field" listed twice | **Plan's reading is sound**: entry-only drop is fail-closed and forward-compatible; whole-body defects and unknown kid stay org-level | parent §6.2 bullets 2–3. Also say what an unknown *top-level* body field does |
| 3. "Requires only applied kinds" | **Plan's refinement is right** for the stamp. It is undone by MODEL in `READY_KINDS` (SHOULD-FIX 4) | `state_gate.py:69-77` |
| 4. Constant wall time is unattainable; assert probes | **Plan right** (reproduced: 298 → 1,308 ns at 1 probe). Also bound the wall-time ratio (NIT 11) | `routing_scan_head.txt` |
| 5. Round-time needs a control migration | **Right but incomplete**: PLAN and KEY at 100k hit the same wall | SHOULD-FIX 1 |
| 6. No per-org isolation scope exists | **True of v2, a dodge of the requirement** | SHOULD-FIX 10 |
| 7. Synchroniser kind isolation; deep verify reads bodies; repair is one MULTI | **Right**, verified (`state_task.py:153-160,232-234`; `delta.py:107-112`; `rehydrate.py:342-352`). Missed: the repair *read* (B2) | — |
| 8. Line drift in parent §3 | **Right**: `serve.py:176,233,252` | — |

## Owner questions (§10): blocking or not

- **Q7 blocks release 2.** The trigger-based revision table is the projection design (§4.2.2). The alternative (control writes the revision from its save paths) misses `QuerySet.update` and raw SQL, and would leave the continuous reconcile as the only safety net, with its 100 s over-claim. It does not block release 1.
- **Q1–Q6 have safe, fail-closed defaults and do not block:**
  - Q1: `auto` only; an absent model stays 400.
  - Q2: keyless `custom` rows omitted.
  - Q3: Vertex omitted `upstream_unsupported`. Confirm before the F1a-3 exit, because D1 is "all v1 providers".
  - Q4: one upstream per alias.
  - Q5: projector omission at 64. The console refusal of a 65th model needs confirmation before the control change ships.
  - Q6: in-flight requests finish.
- **Missing questions:**
  - per-org `ModelState` isolation (SHOULD-FIX 10);
  - the fate of the fleet catalogue, auto target and fallbacks (SHOULD-FIX 5);
  - `model_isolation_enabled` (SHOULD-FIX 9);
  - who owns generation-1 registry tag removal, since R1 depends on it.

## NITs

1. The projector must replicate `platform_guard_model_names()`, which reads **control's** env var `ZEROSHIELD_GUARD_MODEL_NAME` (`core/models.py:1520-1526`). Name the projector's source for it.
2. Compliance tags are canonicalised only by the serializer (`llm_model_serializer.py:89-116`). Admin and legacy rows can carry non-canonical tags, and the prototype parser *drops the entry* on an unknown tag (`model_record_bench.py` `_entry`). Define this in §4.2.3.
3. v1 strips `/chat/completions` from `api_base` (`litellm_byok.py:41` `sanitize_api_base`). Without that normalisation, v1-valid base URLs break under F1a-3's structural path join.
4. "Known LiteLLM prefix" (§4.2.3 step 4) is undefined. For example, `openrouter/…` on an `openai` row is ambiguous between invalid and kept whole. Pin the set.
5. `_ORGS` selects no `id` (`source_pg.py:57`), but the revision table is keyed by org id. Note the query change.
6. The churn figure (47 ms per 1,000 orgs) uses the per-row memo that §4.2.5 rejects. Without it, about 5× the derivations: roughly 80 ms, still within budget. Say so.
7. `memo_memory.txt` has no script, but §11 says "commands are in each script's docstring".
8. The benches ran on Python 3.13; the parent pins `==3.12.*`.
9. 0.86 µs excludes building the AAD and base64 decoding. State that both are precomputed at apply.
10. The FirewallConfig trigger misses `UPDATE OF organization_id`.
11. T2.9 counts probes only through the instrumented dict. Also bound the wall-time ratio (for example ≤ 10× against HEAD's ~100×), so an uninstrumented scan is caught.
12. Index sweep: a score above this worker's applied MODEL position is a pending delta, not a replay. Exclude it, or the "> 0 is an alarm" fires on races.
13. `put_existing` is bounded by count (100), not bytes. With F1a-3's worst case that is a 45 MB MULTI. Bound the bytes, as repair is (≤ 1 MiB).
14. A `row_invalid` entry is keyed `row-<id>`: it can collide with a real alias, and a request for the real alias then gets `model_not_found` instead of `model_unavailable`.
15. "A key resolves only after its MODEL record exists or within the same round" (§4.2.4) does not hold when MODEL adds are deferred by their budget and KEY adds are not. Couple the budgets, or state the transient 503.
16. The memo has no derivation version. A projector upgrade that tightens derivation reaches unchanged orgs only through the 100 s reconcile, after the heartbeat.
17. The fate of `RoutedProvider.fallback_set` under MODEL is unstated.
18. Offloading "worst bodies" needs a byte-based trigger: `state_task.py:152` offloads by record count only.
19. §2.4 says "four, not one" but lists five rows.
20. An R4 pre-flight report of rows that would be omitted per reason (case-variant collisions that v1 served, `alias_invalid`, env-var rows). Console facts bypass the breaker, so the owner should see the impact before the flip.

## Traceability (§6 → plan)

| Item | Status |
|---|---|
| 6.1 | Stamp: closed and reproduced. Floor: gaps (B1 projector side, SHOULD-FIX 2) |
| 6.2 | Closed, except the cold-fetch bound and repair (B2) |
| 6.3 | Closed for chat; embeddings missing (SHOULD-FIX 7) |
| 6.4 | Closed |
| 6.5 | Closed |
| 6.6 | Removals before the heartbeat broken by B1 and SHOULD-FIX 8; round-time weakened (SHOULD-FIX 1) |
| 6.7 | Closed except v1 gating and legacy values (SHOULD-FIX 9) |
| 6.8 | Closed |
| 6.9 | Gaps: fleet catalogue (5), auto-isolation (6), per-org isolation (10) |
| 6.10 | Violated in B1's states; tested only with the projector stopped |
| 6.11 | Closed |
| D3 / D4 / D5 | Closed |

Reviewer-run artefacts are not committed. Everything ran under `nice -n 10`. `pg_source_bench.py` ran under `.ctl-db.lock`. The other scripts are under 2 min of CPU each and ran without the heavy lock, which was queued behind several full suites.


---
_Generated by [Claude Code](https://claude.ai/code)_
