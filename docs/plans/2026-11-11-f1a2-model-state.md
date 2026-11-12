# F1a-2: MODEL state — design plan (for review, no product code)

Status: 11 Nov 2026, revision 1. Base: `develop` at `25c33c86`. Every file:line below was re-read
at that commit. Parent: `docs/plans/2026-11-11-f1-production-serving.md` (PR #103, branch
`feat/f1a-production-serving` at `e2c3e2cf`), §6, whose items are binding. Owner decisions D1
(all v1 providers), D3 (console model changes live within seconds), D4 (env-var keys refused in
production) and D5 (local first, hardware-independent) apply. Credentials are F1a-3's (§7 of the
parent); this plan defines only the interface F1a-2 leaves for them (§4.9).

Evidence (prototypes, outputs and measurements) is in
`docs/plans/evidence/2026-11-11-f1a2-model-state/` (§11). Timings are thread CPU unless stated;
the 2-CPU host was shared (load average 2.4–6.3) throughout.

**How to read this.** §2 is what exists. §3 is release 1 (the stamp), which ships alone. §4 is
release 2 (MODEL). §5–§8 are scaling, security, tests and rollout. §9 lists the parent
requirements I believe are inaccurate or in tension, with evidence; §10 holds owner questions.
The requirement-to-section table is §12.

## 1. Goal and exit

- A console change to an org's model connections reaches every gateway worker **within seconds**
  (D3), through a signed MODEL record per org.
- The gateway's catalogue, `/v1/models`, alias resolution, filters and GW29 kill/isolation all
  read that record, through a per-org view whose request-path cost does not depend on the number
  of orgs.
- The fleet can be rolled forward and back between any two adjacent releases without one build
  refusing another build's state.

**Exit of F1a-2** (the production provider is still the F1a-1 stub; dispatch is F1a-3):
1. Live: adding, removing and deactivating a model in the console changes `/v1/models` and the
   chat refusal code on a production-profile gateway with p95 ≤ 3 s and max ≤ 5 s over 50
   edits (§4.10).
2. Live: with the projector stopped, a disabled model is either refused or the gateway is
   unverified (503) within 21 s (`lag_ms + fresh_ms`), never served (§4.10).
3. Live: a mixed fleet (release-1 and release-2 images, gateways and re-hydrators) stays ready
   through every rollout step, and the stored stamp's `verified_at` never moves backwards (§7.4).
4. The 100k orgs × 5 models projection test passes its budgets (§5, §7.2 T2.16).
5. Every §6 requirement is closed or its bound is stated (§12).

## 2. Current behaviour (develop `25c33c86`)

### 2.1 Signed-state plane

| Fact | Where |
|---|---|
| Four kinds: `PLAN`, `KEY`, `KS`, `BUDGET` | `gateway_v2/domain/state.py:43-49` |
| A stamp must cover **every** `StateKind`; an unknown kind name is refused **before** the signature is checked | `runtime/state_sig.py:412-438` (unknown 418-424, missing 432-437) |
| The stamp signature is over sorted `kind=epoch.seq:feed_seq` lines plus a cursor count | `state_sig.py:124-152` |
| `make_stamp` refuses to mint a stamp that misses any `StateKind` | `state_sig.py:216-221` |
| A gateway adopts a stamp whole: floors per kind, one `verified_at` | `runtime/state_stamp.py:136-174` |
| The re-hydrator stamps only if it watches every `StateKind`, and withholds unless every kind verified | `state_control/rehydrate.py:63,274-282,604-641` |
| **Held-stamp comparison**: a held stamp that does not decode is treated as unverifiable and **overwritten**, whatever its time | `state_control/valkey.py:138-154` (overwrite 143-145) |
| Nudges for an unknown kind are ignored by readers | `runtime/state_nudge.py:106-110` |
| Records are hash-stored for KEY/KS/BUDGET, key-stored for PLAN | `runtime/store_keys.py:31` |
| A delta round pairs each record with its index score and the manifest | `runtime/state_feed.py:299-327` |
| The KEY cold fetch reads manifest + record in one MULTI; it does **not** read the index score | `edge/state_runtime.py:299-329`, `runtime/store_valkey.py:191-210` |
| Deep verify reads the kind's **whole snapshot** (bodies included) to compare `key → feed_seq` | `rehydrate.py:342-352` |
| `publish_kind` (repair) rewrites the whole kind in **one MULTI** | `state_control/valkey.py:208-243` |
| `StateWriter.rollback` re-publishes any logged record at a new epoch, for any kind | `state_control/writer.py:366-381` |
| `put(if_changed=True)` skips identical bodies inside the counter lock | `writer.py:405-412` |
| `put_many(require_new=True)` is the batch path, new keys only | `writer.py:144-180` |
| Projector lag bound 16 s, freshness 5 s | `domain/state_knobs.py:192-195`, `domain/locks.py:8-9` |
| GW17c rollout precedent: a projector flag, off by default, flipped after every gateway enforces | `domain/state_knobs.py:199-202` |
| GW29 rollout precedent: flag + declared `AMF_GATEWAY_MIN_SCOPE_GENERATION`, refusal logged | `state_control/incident_scopes.py:39-75` |

**Kind isolation is incomplete in the gateway synchroniser.** `round_once` catches only
`StoreDataUnavailable` from an applier (`runtime/state_task.py:153-160`), and `drain_all` runs
the kinds in one list comprehension (`state_task.py:232-234`). Any other exception from one
applier therefore aborts the whole cycle (`edge/state_runtime.py:436-438`), so the kinds after
it are not drained and their gates age out. `PlanDeltaApplier` catches only `CompileError`
(`plan/delta.py:107-112`). One unexpected exception class in one record stalls every kind. A new
kind must not add a second path to this; §3.1 fixes it.

### 2.2 Projector

- Round order: KS on → KEY revokes → PLAN → **heartbeat** → KS off → BUDGET → KEY adds
  (`state_control/projector.py:17-27,204-233`).
- Every round reads **every** row of every source table (`state_control/source_pg.py:57-86,95-139`)
  and the **whole** `amf_state_record` snapshot of every projected kind (`projector.py:220`).
- Every statement is bounded by `statement_timeout = 800 ms` (`state_control/service.py:71`).
- A round that raises skips the heartbeat (`state_control/projector_service.py:277-281`).
- **Live bug (parent rev-5 review B, item 2).** `validate_plan` catches only `ValueError`,
  `UnicodeEncodeError` and `CompileError` (`projector.py:565,578`). A 499-deep nested group
  raises `RecursionError`, escapes `round_once`, and after 16 s the re-hydrators withhold the
  stamp fleet-wide. MODEL derivation must catch every exception per row (§4.2.6).
- Omission notes are logged once each (`projector.py:214-217`).

### 2.3 Gateway

- Appliers: PLAN, KS, KEY, BUDGET (`edge/state_runtime.py:220-232`); readiness needs all four
  (`state_runtime.py:84`). KEY cold start is a fast-forward; fills are on demand
  (`state_runtime.py:238,404-422`).
- Identity cache: LRU of 50,000 principals, `_touched` retry for a fill raced by a delta
  (`admit/identity.py:42,161-307`).
- Routing table: **boot-time**, `org → CandidateSet` as a tuple; `candidates_for` is a linear
  scan (`dispatch/routing_types.py:183-215`). `serves_alias` walks every candidate of every org
  (`dispatch/routed_provider.py:201-203`). Both run per chat request (`edge/chat_upstream.py:75`,
  `routed_provider.py:184-194,220-235`). Measured at HEAD: `candidates_for` 12 µs at 1k orgs,
  1.2 ms at 100k; `serves_alias` 55 µs → 5.1 ms (`routing_scan_head.txt`).
- Production wiring: provider is the stub (`edge/serve.py:176,233`), catalogue is
  `empty_catalog_source` (`serve.py:258`, `edge/app.py:191-213`), so `/v1/models` is `[]`.
  `CatalogSource.catalog_for` is synchronous (`app.py:177-188`).
- `no_provider_configured` is the empty-catalogue code (`dispatch/catalogue.py:54`).

### 2.4 Model name grammars (four, not one)

| Place | Grammar | Where |
|---|---|---|
| Wire | `[A-Za-z0-9._:/-]{1,128}` | `edge/wire/openai_params.py:95` |
| Gate | same (`MODEL_NAME`), canonical form lower-case | `domain/incident.py:76,86-87` |
| Routing | `[A-Za-z0-9._:-]{1,64}` — **no `/`**, cap 64 | `dispatch/routing_types.py:72-74`, `runtime/resources.py:66` |
| Routing upstream | `[A-Za-z0-9._:/@+-]{1,max}` | `routing_types.py:77-79` |
| Console | non-empty only | `control/.../core/llm_model_serializer.py:79-82` |

### 2.5 GW29

- Phase 4b runs after the plan pin and before admission (`edge/openai_chat.py:247-271`).
- The gate canonicalises the requested name, then checks stale, global, org, `om:<org>:<model>`,
  `model:<model>`, then isolation (`admit/incident_gate.py:135-162`, `admit/killswitch.py:183-207`).
- Isolation is fleet-level `iso:<model>` (`state_control/incident_scopes.py:88-91`); a reroute
  goes to a fleet-named fallback (`dispatch/incident_routing.py:64-130`). There is **no per-org
  isolation scope** today; control's `ModelState` is not projected.
- Profiles come from a fleet catalogue file `AMF_MODEL_CATALOG` (`edge/incident_catalog.py:1-20`).
- Auto-isolation keys on the canonical requested name (`admit/auto_isolation.py:1-19`).
- Scope generation 2 adds `om:`/`iso:` (`domain/incident.py:83`).

### 2.6 Control's source rows

`LLMModelConfig` (`control/ai_mesh_control/core/models.py:1571-1683`):

| Column | Shape | Where |
|---|---|---|
| `organization` | FK, **nullable** | 1583-1589 |
| `provider` | one of 12 choices (D1) | 1542-1553, 1590-1594 |
| `model_name` (the alias) | ≤ 128 | 1595-1598 |
| `model_id` (LiteLLM id, e.g. `openai/gpt-4o`) | ≤ 256 | 1599-1602 |
| `api_key_env_var` | ≤ 128 | 1603-1608 |
| `encrypted_api_key` | Fernet token | 1609-1613 |
| `api_base` / `region` | ≤ 512 / ≤ 64 | 1620-1631 |
| `is_active` | bool | 1632-1636 |
| `data_sensitivity_level`, `compliance_tags` | enum, list | 1639-1648 |
| uniqueness | `(organization, model_name)`, case-sensitive | 1683 |

- **No** `weight`, `api_version` or `mode` column (parent §6.7 confirmed).
- One candidate per alias: `unique_together(organization, model_name)`.
- v1 skips NULL-org rows (`core/signals.py:503-508`) and platform guard rows
  (`signals.py:513-519`, `core/models.py:1504-1539,1693-1708`).
- Org filters on `FirewallConfig` (one row per org, `models.py:909-916`): `allowed_models`
  (comma text, `models.py:999-1003`) and `default_model` (**≤ 64**, `models.py:1004-1009`). Both
  are retired for v2 (`core/firewall_v2_map.py:75-84`).
- Key filter `GatewayAPIKey.allowed_models` (JSON list, `models.py:326-330`) is **accepted** by
  the key serializer (`core/gateway_serializers.py:83-88`) and **not** projected
  (`projector.py:584-599`) nor read (`admit/identity.py:99-108`): today it is stored and ignored.
- v1 semantics: `default_model` resolves the `auto`/empty sentinel (`gateway/ai_mesh_gateway/main.py:4610-4637`);
  a key allowlist miss is a 403 (`main.py:7798-7815`).

## 3. Release 1: forward-compatible stamp and the fleet floor (state generation 2)

Ships alone, before any MODEL code. It changes no record, no kind and no wire format a current
reader depends on.

### 3.1 Changes

1. **Sign and verify over the raw cursor map.** `stamp_signature` takes kind *names*
   (`Mapping[str, (epoch, seq, feed_seq)]`). For the four current kinds the bytes are identical
   to HEAD's: 5,000/5,000 seeded HEAD stamps produce the same MAC, and HEAD's `decode_stamp`
   accepts 5,000/5,000 stamps from the new signer (`stamp_compat.txt` [1], [1b]).
2. **`decode_stamp(secret, raw, *, required)`** verifies the signature over every name, refuses a
   name outside `[a-z][a-z0-9_]{0,31}`, a non-integer or negative cursor, and a missing
   *required* kind, then **ignores** names this build does not know. HEAD refuses such a stamp
   ("unknown kind", 5,000/5,000, `stamp_compat.txt` [2]); that is why this release goes first.
   Cost 20.5 µs per stamp against HEAD's 26.5 µs.
3. **Required kinds** come from the reader, not the enum:
   - gateway `StampView(required=BASE_KINDS)`, where `BASE_KINDS = (PLAN, KEY, KS, BUDGET)`;
   - re-hydrator's held-stamp comparison (`valkey.py:142`, `publisher.py:275-291`):
     `required = ∅`. It compares `verified_at` only, so a newer held stamp that names a kind the
     comparer does not know is kept. HEAD overwrites it and the stored stamp time moves back
     (`stamp_compat.txt` [3]).
4. **Per-kind coverage.** `StampView` keeps `covered_at[kind]`, the newest verified stamp that
   named the kind. `verified_at` and `fresh()` mean what they mean today (the base kinds). A
   later kind gets its own `KindGate` that also requires `covered_at[kind]` within `fresh_ms`
   (§4.4.4). A coverage gap in MODEL then stales only MODEL, never identity, plans or the kill
   switch.
5. **Stamped kinds come from the floor** (§3.2), not from `set(StateKind)` (`rehydrate.py:276,623`).
6. **State generation.** `STATE_GENERATION = 2` in `domain/state.py`. Gateways export
   `amf_state_generation`; the re-hydrator and projector export the same gauge on their
   metrics ports.
7. **Synchroniser kind isolation.** `round_once` reports any applier exception (class name only)
   as that kind's error and leaves its cursor (§2.1). Negative control: the test fails at HEAD.
   The per-record fix inside `PlanDeltaApplier` (catch `Exception` per record, as rejected) is
   one line and is included.

### 3.2 The fleet floor (enforced, not only documented)

Parent §6.1 asks for the rollout floor to be checked, and GW29's env-var declaration
(`incident_scopes.py:39-75`) is the precedent. An env var alone is not enough here: an image
rollback (`kubectl rollout undo`) restores the old pod spec, env included, so the declaration
rolls back with the image. The floor therefore lives in Postgres.

- Table `amf_state_floor (id smallint PK CHECK (id = 1), min_generation int, stamp_kinds text[],
  updated_at, updated_by)`. `init_schema` creates it with `(1, {plan,key,ks,budget})`.
- CLI: `python -m state_control floor show | set --min-generation N --stamp-kinds a,b,c`.
  It refuses to lower either value without `--allow-lower`, which logs at CRITICAL.
- **Re-hydrator**, each round (one primary-key read, same session bounds):
  - stamps exactly `floor.stamp_kinds`;
  - refuses to stamp (CRITICAL log, `amf_rehydrate_floor_refusals_total`) when
    `STATE_GENERATION < floor.min_generation`, when a floor kind is unknown to this build, or
    when its own capability flag for a floor kind is off.

  So every re-hydrator stamps the same set, switched for the whole fleet by one row update.
  No mixed pair can starve a kind. A rolled-back re-hydrator image of generation ≥ 2 refuses
  instead of overwriting.
- **Store copy.** The re-hydrator publishes the row as a signed `{ns}:floor` (new signature
  domain `floor1`) when it changes.
- **Gateway** reads `{ns}:floor` in the same round trip as the stamp. It refuses readiness, with
  a reason naming the floor, when `STATE_GENERATION < floor.min_generation`. An absent floor key
  means no constraint: deleting the key only removes a self-check.
- **Projector**: refuses to emit a kind or field whose generation exceeds `floor.min_generation`
  (§4.2.1).
- **Generation-1 images cannot be bound by code.** They neither read the floor nor tolerate
  unknown kinds. The registry tag policy removes generation-1 images from the deployable set
  before `min_generation` is raised to 2. An alert fires on
  `min(amf_state_generation) < floor.min_generation` across scraped targets.

### 3.3 Mixed-version matrix for release 1

| Writer | Reader gen 1 (HEAD) | Reader gen 2 | Reader gen 3 (MODEL) |
|---|---|---|---|
| gen-1 re-hydrator | accepts | accepts (same bytes) | accepts base; MODEL uncovered |
| gen-2/3 re-hydrator, floor = base kinds | accepts (same bytes) | accepts | accepts base; MODEL uncovered |
| gen-3 re-hydrator, floor names `model` | **refuses** (unknown kind) | accepts, ignores `model` | accepts; MODEL covered |

| Re-hydrator pair | Held-stamp comparison | Stamp time |
|---|---|---|
| gen-1 + gen-1 | today's | monotonic |
| gen-2 + gen-3, floor = base | both decode both | monotonic |
| gen-2 + gen-3, floor names `model` | the gen-2 one refuses to stamp | monotonic |
| gen-1 + gen-3, floor names `model` | gen 1 overwrites newer stamps | **moves back**; forbidden by §3.2 |

### 3.4 Tests

Listed in §7.1 (T1.1–T1.7) and §7.4 (L1).

## 4. Release 2: the MODEL kind (state generation 3)

### 4.1 The record

- **Kind** `StateKind.MODEL = "model"`. **Key** = the org slug, which passes the projector's
  `_SLUG` (`projector.py:67-70`). One record per org. **Key-stored** at `{ns}:model:<slug>`,
  not in `HASH_KINDS` (parent §6.11).
- **Body**: canonical JSON from `canonical_body` (`state_sig.py:50-58`). There are no floats and
  no timestamps of the write, so identical source gives identical bytes:

```json
{
  "schema": 1,
  "org_id": "acme",
  "models": [
    {"key": "prod-chat", "alias": "Prod-Chat", "row": 4242, "provider": "azure",
     "upstream": "gpt-4o-prod", "base_url": "https://acme.openai.azure.com", "region": null,
     "created": 1731300000, "tags": ["HIPAA", "SOC2"], "sensitivity": "confidential",
     "cred": {"t": "pending"}}
  ],
  "omitted": [{"key": "legacy", "alias": "Legacy", "reason": "credential_env_var"}],
  "omitted_overflow": 0,
  "allowed": null,
  "default": "prod-chat"
}
```

| Field | Rule |
|---|---|
| `key` | `alias.casefold()`; the dict key; sort order of `models` and `omitted` |
| `alias` | the console name, alias grammar (§4.5); listed by `/v1/models` |
| `row` | `LLMModelConfig.id`; an AAD input for F1a-3 |
| `provider` | one of D1's 12; anything else is omitted `unknown_provider` |
| `upstream` | derived from `model_id` (§4.2.3), `[A-Za-z0-9._:/@+-]{1,256}` |
| `base_url` | `null` or ≤ 512, structural grammar (§4.2.3); network checks are F1a-3's |
| `region` | `null` or ≤ 64 |
| `created` | integer seconds of the row's `created_at` (OpenAI `created`) |
| `tags`, `sensitivity` | control's canonical compliance tags (sorted) and sensitivity enum; the GW29 reroute profile |
| `cred` | tagged union, §4.9 |
| `allowed` | `null` (unrestricted) or the sorted casefolded aliases of the org allowlist |
| `default` | `null` or a served `key`: the org's target for `auto` (§4.6) |

- **Not carried**:
  - cost, latency, risk and priority (GW28 weighted routing stays retired,
    `firewall_v2_map.py:186-216`);
  - `rate_limit_rpm` (no v2 per-model limiter; declared);
  - `weight`, `api_version`, `mode`: no column exists. `weight` would be meaningless with one
    candidate per alias. `api_version` is F1b's; it needs a control migration, and the
    gateway's record parser gets a new optional field under a new generation then.
- **Bounds.** ≤ 64 served entries and ≤ 64 listed omissions per org, the rest counted in
  `omitted_overflow`, and ≤ 512 KiB per record. The worst case at every field maximum with
  F1a-3's 4 KiB key cap measures 452,009 bytes; a typical 5-model org is 2,068 bytes
  (`model_record_bench.txt`). The projector refuses to write over the bound (it keeps the
  previous record and raises an alarm). The gateway refuses to parse over it (the org becomes
  unavailable).
- **Signing**: unchanged `make_record` / `decode_record` (`state_sig.py:155-180,326-363`). The
  kind is inside the MAC, so a MODEL body cannot pass as another kind's.
- **Absence and tombstones** (parent §6.5):
  - every active org has a record, possibly empty;
  - an org that leaves the active set gets `deleted=true, op=OFFBOARD, body {"org_id": slug}`,
    as PLAN does (`projector.py:272-280`);
  - an **absent** record is never read as empty: it is `StoreDataUnavailable` (503).

**Omission reasons** (closed vocabulary; each maps to a gateway answer, §4.4.6):

| Reason | Cause | Breaker class (§4.2.7) |
|---|---|---|
| `inactive` | `is_active = false` | console fact |
| `credential_missing` | no key and no env var, provider not keyless | console fact |
| `credential_env_var` | key only by `api_key_env_var` (**D4**) | console fact |
| `alias_invalid` / `alias_collision` | grammar / case-fold duplicate in the org | console fact |
| `upstream_invalid` | `model_id` fails the grammar or names another provider's prefix | console fact |
| `upstream_unsupported` | a `vertex_ai/` id (OAuth, not a key; §10 Q3) | console fact |
| `base_url_invalid` / `region_invalid` | structural grammar; Bedrock without a region | console fact |
| `org_model_cap` | over 64 served entries | console fact |
| `unknown_provider` | provider outside D1 (a new control choice before the gateway knows it) | systemic |
| `credential_invalid_format` | not a Fernet token shape | systemic |
| `row_invalid` | the derivation raised (any exception) | systemic |
| `credential_undecryptable`, `credential_kid_unknown` | F1a-3 | systemic |

Platform guard rows (`models.py:1504-1539`) and NULL-org rows are **skipped**, not omitted: they
are not the org's models. They are counted (`amf_projector_model_rows_skipped_total{why}`).

### 4.2 Projection

#### 4.2.1 Flags and floor

- `ProjectorKnobs.project_models` (`AMF_STATE_PROJECT_MODELS`, default off) and
  `project_key_model_filter` (default off), following `state_knobs.py:199-202`.
- Each requires `floor.min_generation ≥ 3`. Otherwise the projector writes nothing for it and
  logs the GW29-style refusal once (`incident_scopes.py:62-75`).

#### 4.2.2 Source: per-org revisions instead of a full read

At 100k orgs × 5 models, HEAD's pattern (read every row every round, then snapshot every
record) does not fit the session bound (`pg_source_bench.txt`):

| Read | Result (run 1 / run 2) |
|---|---|
| Every model row (HEAD pattern) | **cancelled** by the 800 ms `statement_timeout` in both runs; 6.3 / 4.1 s server execution; 15.9 / 17.7 s unbounded |
| Every MODEL state record (HEAD `projector.py:220` pattern) | 221 MB per round; 1.43 s wall in run 1 (the server finished in 68 ms, so the timeout did not fire during the transfer), **cancelled** at 842 ms in run 2 |
| Per-org revisions (design) | 100,000 rows; 98 / 72 ms wall; 10 ms server |
| Rows of 1,000 changed orgs (design) | 289 / 297 ms wall; 155 / 300 ms server |
| Rows of 2,000 changed orgs (design) | 469 / 583 ms wall; 396 / 375 ms server (too close to 800 ms under load; chunk at 500) |
| Index only `(key, feed_seq)` (design, deep verify) | 183 / 142 ms wall |
| Memo seed `(key, content_hash)` (design, new leader) | 158 / 119 ms wall |

So MODEL reads **what changed**:

- **Control migration** (Django `RunSQL` in `core`): table
  `amf_model_source_rev (org_id bigint PK, rev bigint, changed_at timestamptz)`, backfilled with
  one row per org. Row triggers bump `rev` and `changed_at` for the old and new
  `organization_id`:
  - `AFTER INSERT/UPDATE/DELETE ON core_llmmodelconfig`;
  - `AFTER INSERT/DELETE OR UPDATE OF allowed_models, default_model ON core_firewallconfig`;
  - `AFTER INSERT/DELETE OR UPDATE OF slug, is_active ON auth_api_organization`;
  - a statement trigger on `TRUNCATE` of the first two bumps every row.

  Triggers catch the ORM, `QuerySet.update()` (which skips `auto_now`), admin and raw SQL. In
  the prototype, after 6,000 updates of 2,000 random rows, the number of orgs whose revision
  moved equals the number of distinct orgs owning those rows (`pg_source_bench.txt`). The
  trigger's write cost was not distinguishable from noise on the loaded host (4.7–7.2 ms per
  autocommit update either way, two runs); the implementation measures it server-side with
  `track_functions`.
- **Each round**, in the existing REPEATABLE READ snapshot (`source_pg.py:95-96`), so the
  revisions and the rows agree:
  1. `SELECT org_id, rev FROM amf_model_source_rev` (72–98 ms);
  2. for orgs whose `rev` differs from the memo (§4.2.5), their model rows and FirewallConfig
     filters, in chunks of 500 orgs per statement.

  The org list (`_ORGS`) is already read every round and gives the active set and slugs.
- **Continuous reconcile.** After the heartbeat, each round re-derives the next 1,000 orgs by
  keyset (500 per statement) regardless of revision and compares them with the memo. A missed
  trigger (disabled during a restore, a promoted replica without triggers) is therefore repaired
  and counted (`amf_projector_model_reconcile_fixes_total`, alarm on > 0) within one full pass:
  `orgs / 1,000` rounds, 100 s at 100k orgs. The cost is the full derivation spread over the
  pass, 8.2 s CPU per 100 s (~8% of one core, `projector_bench.txt`). The pass also counts
  NULL-org rows. A missed trigger is the one case where the heartbeat can over-claim, and this
  pass is its bound.
- A missing revision table (control not migrated) is a refusal of MODEL projection, logged.

#### 4.2.3 Deriving one row

In order, first failure wins (each is an omission reason):
1. `is_active`.
2. `provider` ∈ D1.
3. Alias grammar.
4. **Upstream from `model_id`.** If its first segment is a known LiteLLM prefix, it must be
   one of the row's provider's prefixes, and it is stripped. Otherwise the id is kept whole.
   `custom` never strips (`shared/ai_mesh_shared/litellm_byok.py:1-10`). The upstream grammar is
   then checked.

   | Provider | Accepted prefixes |
   |---|---|
   | openai | `openai/` |
   | anthropic | `anthropic/` |
   | azure | `azure/` |
   | google | `gemini/`; `vertex_ai/` is unsupported |
   | aws_bedrock | `bedrock/` |
   | mistral | `mistral/` |
   | cohere | `cohere/`, `cohere_chat/` |
   | deepseek | `deepseek/` |
   | huggingface | `huggingface/` |
   | meta | `meta_llama/` |
   | ollama | `ollama/`, `ollama_chat/` |
   | custom | none |

5. **Per-provider required fields:** azure needs `base_url`; aws_bedrock needs `region`.
6. **`base_url` structure:**
   - scheme `https`, or `http` (F1a-3 limits `http` to org-private entries);
   - a host;
   - no userinfo, query, fragment, backslash or dot segments;
   - ≤ 512 characters.

   Address and DNS checks are F1a-3's (§7.3).
7. **Credential.**
   - An encrypted key present → shape check (Fernet prefix and charset) → `cred` per §4.9.
     The encrypted key wins over an env var, as in v1 (`models.py:1757-1761`).
   - Otherwise an env var → `credential_env_var` (**D4**).
   - Otherwise → `credential_missing` (keyless providers: §10 Q2).

Then per org:
- Every row whose key collides case-insensitively is omitted `alias_collision`.
- Entries beyond 64 (by key order) are omitted `org_model_cap`.
- `allowed` and `default` come from FirewallConfig. A name that is not a served alias of the
  org is dropped with a note; `default` must be a served key or it becomes `null`.

#### 4.2.4 The round

```
1. KS on
2. KEY revokes / changes
3. PLAN set / offboard
3m. MODEL safety: tombstones; for orgs with a changed source, the SAFETY version
--- heartbeat (written only after every 3m write is durable) ---
4. KS off
5. BUDGET
5m. MODEL liveness: full desired versions (adds); first records for new orgs (bounded)
6. KEY adds, un-revokes (bounded)
```

For an org with held record `H` and desired record `D`:
- **Safety version** `S`:
  - `D`'s entries restricted to keys **already served in `H`**, so removals, omissions and
    changed entries (upstream, base URL, provider, credential) take effect pre-heartbeat;
  - `allowed` = the more restrictive of `H` and `D`;
  - `default` = `D.default` if it is in `S`, else `null`.
- **Liveness version** = `D`.
- If `S == H`, nothing is written pre-heartbeat. If `S == D`, one write covers both. A mixed
  edit (one removed, one added) is two versions (parent §6.6).

Writes:
- **Renames.** An org whose slug changed gets its tombstone *and* its record under the new slug
  in 3m: the KEY phase moves its keys to the new slug pre-heartbeat (`projector.py:249-264`), so
  the new record must exist by then. It is not an add.
- **Orgs with no held record** are classified without reading their rows (nothing can be removed),
  so a first projection reads nothing before the heartbeat.
- **Safety writes are never deferred.** A removal or a key rotation that cannot be made durable
  must not be covered by a heartbeat that says it was. They use a new
  `StateWriter.put_existing(kind, entries, fence)`:
  - one Postgres transaction of ≤ 100 records (one version each, `if_changed` per record);
  - one MULTI with their records, index entries and the manifest.

  An existing key's score and the manifest move in the same MULTI, so no reader sees the
  mid-batch count error that keeps `put_many` new-key-only (`writer.py:144-158`, S2 review R8).
  Cost: `make_record` is 42 µs for a typical body (`model_record_bench.txt`); I/O per chunk is
  measured in T2.16 and L2.7.
- **Liveness writes**:
  - adds use `put_existing`;
  - first records for new orgs use `put_many(require_new=True)`, within the round's add
    budget (`add_chunks_per_round × chunk`), before that round's KEY adds, so an org's key
    resolves only after its MODEL record exists or within the same round.

**Coalescing.** An org is written at most twice per round, so a tenant saving 100 times a second
produces at most 2 MODEL writes per second.

#### 4.2.5 Memo and idempotence (parent §6.6)

- **Per org**: `org_id → (slug, source_rev, content_hash)`, 264 B per org, 26 MB at 100k
  (`memo_memory.txt`).
  - It is persisted in a projector-private `amf_state_model_memo` table, upserted after each
    chunk's writes. A new leader resumes without recomputing the estate; seeding from
    `amf_state_record` takes 119–158 ms.
  - A crash between a write and its memo upsert re-derives that org next round. That is
    idempotent (`if_changed`).
- **Per row** (F1a-3's credential memo): `row_id → (digest of Fernet token + AAD tuple,
  published {kid, nonce, ct})`. A row is decrypted only when its digest changes. A compact dict
  is 358 B per row, 179 MB at 500k rows (`memo_memory.txt`), so F1a-3 should keep it in a
  projector-private table and cache only changed orgs. F1a-2 has no decryption and needs no
  per-row memo. Storing derived entries per row would cost 1,046 B per row (523 MB) and is
  rejected (`projector_bench.txt`).
- **No version bump on identical bodies**:
  - canonical bytes are deterministic: 1,000/1,000 shuffled sources give identical bytes
    (`model_record_bench.txt`);
  - re-projecting 1,000 unchanged orgs gives 0 body changes (`projector_bench.txt`);
  - `if_changed` re-checks under the counter lock (`writer.py:405-412`).
- **Plan validation memo** (parent §6.6 bullet 2) is F1a-1's (§5.2.3 of the parent: parse-only,
  memoised by body hash, time-boxed). MODEL adds nothing to it.

#### 4.2.6 Failure isolation (the nested-regex lesson)

- `derive_row` runs inside `except Exception` per row. Any exception (`RecursionError`,
  `MemoryError`, `SystemError`, a bad type from the driver) makes that row `row_invalid` and
  the round continues. The prototype estate carries a poison row that raises `RecursionError`;
  the 100k-org round completes with `row_invalid: 1` (`projector_bench.txt`).
- Per-org assembly also runs inside `except Exception`. On failure the previous record stays,
  `amf_projector_model_invalid_orgs_total` counts it, and a note is logged.
- Each driver value is type-checked before use (`None`, wrong types).
- **Notes**:
  - at most 20 per reason per round, plus counters;
  - each carries the org slug, the alias (escaped, ≤ 64 characters) and the reason;
  - **never** `base_url`, `model_id`, the env var name or any credential byte (parent §6.6,
    `projector.py:214-216`).

#### 4.2.7 Breaker (parent §6.6)

- **Newly-omitted** entries for a reason `r` are entries served in `H` and omitted for `r` in
  `D`, with an **unchanged source credential digest**.
- If their number reaches `max(128, 5% of served entries)` in a round, and `r` is **systemic**
  (table in §4.1):
  - the breaker opens for `r`;
  - every org write that would make such a transition is held, and the previous record stays;
  - every other change in the round proceeds;
  - `amf_projector_model_breaker_open{reason}` is 1 and the alarm fires.
- 128 is twice an org's maximum, so one org can never trip it.
- **Console facts are never held.** An operator deactivating every OpenAI model in an incident
  is a fail-closed action the breaker must not stop.
- **A changed source credential is never held.** A rotation after a compromise must not keep
  the old key alive because the projector cannot open the new one.
- The breaker closes when the condition clears. `AMF_PROJECTOR_BREAKER_ALLOW=<reason>` forces
  it shut and logs at CRITICAL.

### 4.3 State-plane changes for MODEL

- **Writer**: `put_existing` (§4.2.4). `StateWriter.rollback` raises for MODEL (parent §6.2): the
  projector would overwrite it next round, and replaying an old body could re-publish a rotated
  credential.
- **Re-hydrator**:
  - `diagnose` is unchanged (O(1));
  - `verify` reads `key, feed_seq` only for **every** kind (`SELECT key, feed_seq ...`,
    142–183 ms at 100k), instead of the full snapshot (`rehydrate.py:349`);
  - `repair` for MODEL publishes in chunks (≤ 1 MiB per pipeline, manifest last under WATCH)
    instead of one MULTI of the whole kind (`valkey.py:208-243`). A `store_ahead` repair
    re-signs every record, so readers refuse for the duration of that repair (rare, fail
    closed), which is stated rather than hidden;
  - MODEL is stamped only when `floor.stamp_kinds` names it (§3.2). The capability flag is
    `AMF_STATE_REHYDRATE_MODEL`; a re-hydrator with it off refuses to stamp at all while the floor
    names `model`, rather than writing a stamp without it.
- **Projector**: MODEL is **not** added to the per-round `snapshot` list (`projector.py:220,425`).

### 4.4 Gateway

#### 4.4.1 Reader: sparse delta

`FeedReader.poll_sparse(kind, cursor, limit, want)`:
- runs the same head and index checks as `poll` (`state_feed.py:228-297`), including
  `total_at_or_below == manifest.count`;
- fetches and verifies bodies **only for keys `want` accepts**: orgs held in this worker's view.

For the other changed keys only the key is used: an org with a fill in flight is marked
`_touched` (§4.4.3); the rest are skipped. Bytes per round are O(changes × ~40 B
+ cached changes × body), not O(changes × body): a full re-projection of 100k orgs would
otherwise be 260 MB per worker. Signature, hash, index or pairing failures on a fetched record
are round-fatal (cursor stays) and alert (`amf_state_rounds_failed_total{kind="model"}`, parent
§6.2).

#### 4.4.2 Applier contract (parent §6.2)

For each fetched record, inside `except Exception`:
- A **body defect** (JSON, schema, `org_id ≠ key`, size, duplicate key, bad omitted entry, an
  `aead1` credential whose kid is unknown, a failed trial decrypt in F1a-3) sets the org to
  `Unavailable(version, reason)` in the view. The cursor advances and
  `amf_model_view_unavailable_total{reason}` counts it.
- An entry with an **unknown provider or unknown field** is dropped and counted
  (`amf_model_entries_dropped_total`); the rest of the org is served.
- A record **older** than the held version for a warm org is refused and the held one kept
  (`amf_model_rollback_refused_total`).
- **Readiness never depends on one org.** The MODEL gate is about the kind's rounds, not a
  record's parse.

Measured cost per typical record: decode 31 µs + parse and build 77 µs (`model_record_bench.txt`).

#### 4.4.3 The view (parent §6.3, §6.4)

- `ModelView`: `OrderedDict[org → OrgModels | Unavailable]`, an LRU under a lock (the applier
  may run in the offload thread).
- **Capacity is contract-derived**:
  `floor(per_worker_rss × MODEL_VIEW_SHARE / VIEW_BYTES_PER_ORG)` with share 0.10 and the
  measured 6,179 B per typical org (`model_record_bench.txt`). At the 400 MiB default
  (`resources.py:25`) that is 6,788 orgs.
- Metrics: `amf_model_view_entries`, `amf_model_view_evictions_total`.
- **`OrgModels`** is immutable:
  - `by_key: Mapping[key → ModelEntry]`, `omitted: Mapping[key → reason]`, `allowed`,
    `default`;
  - the precomputed `/v1/models` listing;
  - one precomputed `Candidate` per entry (candidate id `row-<row>`).
- **Ciphertext only.** The view holds `{kid, nonce, ct}`, never plaintext. F1a-3 decrypts per
  request: 0.86 µs with an `AESGCM` cached per kid, 2.46 µs if built per call
  (`aead_bench.txt`), confirming parent §6.4's 0.8 µs.
- **Fills.** `pin(org)` is async: a hit is O(1); a miss is one single-flight fetch per org.
  - One MULTI: `GET` manifest, `GET {ns}:model:<org>`, `ZSCORE idx:model <org>`.
  - Checks: the manifest's anti-regress against this worker's cursor and floor (as
    `state_runtime.py:311-318`); `record.key == org`; **`record.feed_seq == score`**;
    `feed_seq ≤ manifest.feed_seq`; `version ≤ manifest.version`. Record absent → absent score
    required → `StoreDataUnavailable` (503), never empty.
  - A fill above this worker's applied MODEL position is used for the request and not cached
    (the C36 rule, `identity.py:325-335`).
  - **`_touched` retry**: a delta naming the org while its fill is in flight forces one
    refetch; a second hit is a 503 (`identity.py:256-277`).
- **Rollback bound for cold fetches (parent §6.2).**
  - The score pairing closes the non-malicious cases: a manifest newer than a torn or partially
    restored record.
  - A **store writer** can replay an older genuinely signed record *and* its score, because the
    index score is not signed. The re-hydrator's deep verify compares `key → feed_seq` with
    Postgres and repairs, so this is detected within `deep_every × rehydrate_period` (60 s by
    default).
  - A worker that cached the replayed record would then keep it, because the repair rewrites
    the same positions and no delta follows. So each worker runs an **index sweep** every 30 s:
    one `ZMSCORE idx:model <cached orgs>` (one command, ≤ 6,788 members) that evicts entries
    whose score differs (`amf_model_view_sweep_evictions_total`; > 0 is an alarm).
  - **Stated bound**: replay by a store writer is served for at most 60 s + 30 s.
  - Full closure needs a signed per-record commitment, which is out of scope. §7.8 of the
    parent (gateway read-only ACL) shrinks the set of writers. KEY has the same gap today.

#### 4.4.4 Gates and readiness

- MODEL `KindGate` is fresh when:
  - the base stamp is fresh;
  - `covered_at[MODEL]` is within `fresh_ms` (§3.1 item 4);
  - this worker's MODEL rounds are caught up within `fresh_ms` (`runtime/state_gate.py:46-64`).
- `AMF_GATEWAY_MODEL_STATE=on` builds the reader and adds MODEL to `READY_KINDS`. Cold start is
  `fast_forward` (as KEY, `state_task.py:236-256`): O(head).
- With the flag off the gateway behaves as today (empty catalogue, stub routing). A generation-3
  image can therefore deploy before re-hydrators stamp MODEL.

#### 4.4.5 Request path, O(1) in orgs (parent §6.3)

- **Phase 4m** sits after the plan pin and before phase 4b (`openai_chat.py:256-266`):
  `models = await deps.models.pin(identity.org_id)`. The gate, routing preflight, `_plan` and
  dispatch all read **this** object (parent §6.9: pinned before the gate).
- **`models.resolve(requested, principal)`**, in order:
  1. `auto` → `models.default`, else `model_not_found`. An **absent** model is never defaulted
     (GW29, `incident_gate.py:10-11`).
  2. Casefold → `by_key`. Else `omitted` → `model_unavailable` (cause = reason). Else
     `model_not_found`.
  3. Org allowlist, then the key allowlist (§4.6) → `model_not_allowed`.
- **Replacements:**
  - `serves_alias(org, alias)` → `models.serves(key)`;
  - `preflight` → `models.candidate(key)`;
  - `_plan` uses a one-element `CandidateSet` (one candidate per alias; any reroute is decided
    by the gate, §4.7).
- Per-entry client and validation checks run once, at apply. `all_candidates()` is no longer on
  the request path or the apply path. `RoutedProvider.__init__`'s per-candidate client check
  (`routed_provider.py:170-175`) becomes F1a-3's per-origin pool lookup; F1a-2 maps every entry
  to the stub.
- Measured: one alias probe per request at 1k and at 100k orgs (`routing_scan_head.txt`), versus
  HEAD's 12 µs → 1.2 ms (`candidates_for`) and 55 µs → 5.1 ms (`serves_alias`).

#### 4.4.6 Answers (codes; HTTP rows are F1a-3's §7.5 table)

| Condition | Code | Proposed status |
|---|---|---|
| MODEL gate unverified, record absent, org unavailable, tombstone | `model_state_unavailable` (cause `unverified`/`absent`/`unavailable`/`offboarded`) | 503, retry |
| Org has no served and no omitted model | `no_provider_configured` (`catalogue.py:54`) | as today |
| Alias omitted | `model_unavailable` (cause = omission reason) | 400 `invalid_request_error`, `x-should-retry: false` |
| Filtered by org or key allowlist | `model_not_allowed` | 403 (v1 parity, `main.py:7798-7815`) |
| Unknown alias | `model_not_found` | 404 (today's mapping, `openai_chat.py:710`) |

#### 4.4.7 Catalogue and `/v1/models`

- `CatalogSource.catalog_for` becomes `async` (`app.py:177-188`).
- The MODEL source returns `OrgModels.listing` filtered by the key allowlist. Each item is
  `CatalogModel(id=alias, created=created, owned_by=provider)`.
- An empty org lists `[]`.
- Retrieve: served → the model; omitted → `model_unavailable`; else `model_not_found`.
- Unverified → `model_state_unavailable`.

#### 4.4.8 In-flight requests

A request finishes on the view it pinned. A console removal does not cut a stream; kill
switches remain the safety tool (`STREAM_KILLED`, GW29). See §10 Q6.

### 4.5 One alias grammar (parent §6.8)

`MODEL_ALIAS = [A-Za-z0-9._:/-]{1,128}` lives in `domain/incident.py` as today's `MODEL_NAME`
and is used by:

1. **Wire**: `openai_params.py:95` imports it instead of its own literal.
2. **Gate**: unchanged.
3. **Routing**: `is_valid_model_name` uses it; `_ROUTING_MAX_IDENT_LEN` 64 → 128
   (`resources.py:66`). `ident_pattern` remains for candidate and provider ids only.
4. **Console**: `validate_model_name` enforces the same pattern from `ai_mesh_shared`. A CI test
   asserts the two literals are byte-identical (the parent's §5.2.3 precedent).

- `@` and `+` are refused in aliases. Upstream ids keep them, capped at 256 to match
  `model_id` (`models.py:1599-1602`).
- **Case-fold collisions** are omitted `alias_collision` at the projector. The console refuses a
  new case-variant duplicate (`model_name__iexact` check).
- The gateway casefolds before lookup, so `GPT-4o` and `gpt-4o` resolve alike.

### 4.6 Filters (parent §6.7)

| Filter | Carrier | Notes |
|---|---|---|
| Org `allowed_models` | MODEL `allowed` | Un-retired in `firewall_v2_map.py:75-79` behind a deployment setting `AMF_V2_MODEL_FILTERS_SERVED` (the `rewrite_served_by_v2` pattern), set only once gateways run MODEL. Names must be org aliases (`stale_allowed_models` exists) |
| Org `default_model` | MODEL `default` | Same un-retirement. **Semantics: the per-org target of `auto` only** (§10 Q1). Control migration widens it 64 → 128 to fit the alias grammar |
| Key `allowed_models` | **KEY** record field `allowed_models` (sorted casefolded aliases, ≤ 64) | Behind `project_key_model_filter` + floor ≥ 3: `principal_of` ignores unknown fields (`identity.py:88-108`), so an older gateway would not enforce it (GW17c's reason). Strict parse: malformed → the key does not resolve. Until on, the key serializer refuses a non-empty value (no more "stored and ignored") |

Filters apply to resolution and to the `/v1/models` listing.

### 4.7 GW29 semantics (parent §6.9)

- **Per-org scopes key on `(org, alias key)`**: `om:<slug>:<key>`. That is what control's
  `KillSwitch.model_name` already holds, and `org_model_projection` canonicalises it
  (`incident_scopes.py:94-103`). There is no per-org isolation scope today (§2.5); none is added.
- **Fleet scopes also match the resolved upstream.** For a resolved entry, the match set is
  `{canonical(alias), canonical(upstream), canonical(provider + "/" + upstream)}`, skipping names
  that do not canonicalise. A fleet `model:gpt-4o` therefore refuses org A's `prod-chat → gpt-4o`.
  - The alias stays in the set, as today, so nothing that is refused today stops being refused.
  - The `provider/upstream` form lets an operator stop one provider's deployment
    (`model:azure/gpt-4o`) without stopping another's.
  - `KillSwitchSnapshot.evaluate_resolved(org, key, names)` reads all facts under one lock.
- **Isolation**:
  - an `iso:` on any name in the match set isolates the entry; when several match, precedence
    is `provider/upstream` > `upstream` > alias;
  - **the fallback is resolved inside the pinned org view**: first an entry whose key equals the
    fallback, else the lowest key whose `upstream` or `provider/upstream` canonicalises to it;
  - none → `REROUTE_REFUSED` with the new reason `fallback_not_served` (added to
    `ROUTE_REASONS`, `incident_routing.py:34-49`);
  - the fallback entry then passes the same filters, kills and single-hop checks
    (`incident_routing.py:98-130`);
  - profiles (tags, sensitivity) come from the entries, so per-org data replaces the fleet file
    for reroute constraints.
- **Auto-isolation and the risk tracker** key on `canonical(provider/upstream)`, falling back to
  `canonical(upstream)`, so two orgs' aliases of one upstream aggregate. An entry with neither
  form is not tracked and is counted.
- **Generations**: no new KS key shape, so `KS_SCOPE_GENERATION` stays 2 and older gateways read
  every KS record as today. Any later shape goes behind generation 3 and
  `AMF_GATEWAY_MIN_SCOPE_GENERATION` (parent §6.9).
- **Recheck** (per chunk) evaluates the bound entry's match set.
- **Residual**: fleet scopes match exact canonical ids. A dated id (`gpt-4o-2024-08-06`), an id
  over 128 characters, an id with `@`/`+`, or a `custom` proxy in front of a model is not matched
  by `model:gpt-4o`. Stated, not fixed (§6).

### 4.8 D4

- An env-var-only row is omitted `credential_env_var` at the projector, always, because MODEL
  state is the production path.
- A request names it and gets `model_unavailable` / `credential_env_var` with zero provider calls
  (T2.12).
- The dev profile does not read MODEL state.

### 4.9 Interface left for F1a-3 (credentials)

- **`cred`** is one of:
  - `{"t":"none"}` — keyless providers only (`ollama`; §10 Q2);
  - `{"t":"pending"}` — F1a-2: a key exists in control and is not yet published;
  - `{"t":"aead1","kid","nonce","ct"}` — F1a-3: AES-256-GCM, kid ≤ 32 `[a-z0-9_-]`, base64
    nonce and ct, ct ≤ 8 KiB.

  F1a-2 gateways parse all three structurally. An unknown tag drops the entry.
- **AAD inputs** are all in the record: namespace, org (record key), `row`, `key`, `provider`,
  `base_url`, `kid` (parent §7.1).
- **Projector hooks**:
  - `CredentialSealer.seal(row, plaintext, aad) → triple`, a null sealer in F1a-2;
  - `CredentialMemo.get/put(row_id, digest)`, a null memo in F1a-2.
- **Gateway hooks**:
  - `KeyringProbe.known(kid)`: F1a-2's default answers no, so an `aead1` org becomes
    `Unavailable` on a generation-3 gateway;
  - `CredentialOpener.open(org, entry) → secret`: F1a-2's default raises
    `credential_unavailable`.
- **Rollout**: F1a-3 bumps `STATE_GENERATION` to 4, and its projector emits `aead1` only at
  `floor.min_generation ≥ 4`. A re-wrap to a new kid is a post-heartbeat backlog; a real key
  change is pre-heartbeat (§4.2.4).

### 4.10 Freshness and the live latency budget (D3, parent §6.10)

| Step | Budget |
|---|---|
| Console commit → revision bump | same transaction |
| Projector notices | ≤ 1 period (1 s), mean 0.5 s |
| Round to the MODEL phase | revision read 72–98 ms + changed rows (≤ 297 ms per 1,000 orgs) + derivation 47 ms per 1,000 orgs |
| Write + publish + nudge | one transaction + one MULTI |
| Worker wakes | nudge, or ≤ `refresh_period` 0.5 s if the nudge is lost |
| Apply | ~108 µs per typical record |

- **Target**: p50 ≈ 0.7 s, p95 ≤ 3 s, max ≤ 5 s for a single console edit (exit 1).
- **Safety bound** (parent §6.10): `lag_ms + fresh_ms` = 16 + 5 = **21 s**. Past it, a stopped
  projector withholds the stamp and every gateway fails closed.
- **Measurement**:
  - Live **L2.1/L2.2**: 50 edits through the console API.
    - Each run records `t0` from the client, just before the request.
    - It polls `/v1/models` (add) or a chat request (deactivate → `model_unavailable/inactive`)
      every 50 ms, on a one-worker gateway and on a two-worker gateway; the two-worker run
      needs both workers to agree (S3's lesson).
    - It reports p50, p95 and max.
  - Supporting series: `amf_projector_model_source_lag_seconds` (write time − `changed_at`) and
    `amf_state_records_applied_total{kind="model"}`.
- **L2.3** (the bound): disable a model and stop the projector at the same moment. Every probe
  after `t0 + 21 s` is a refusal or a 503, never a served model. The rotation variant is
  F1a-3's.

## 5. Scaling (measured; 100k orgs × 5 models unless stated)

| Quantity | Value | Source |
|---|---|---|
| Record: empty / typical / 20 models / worst | 109 B / 2,068 B / 8,086 B / 452,009 B | `model_record_bench.txt` |
| `make_record` typical / worst | 42 µs / 2.6 ms | same |
| Gateway apply typical (decode + parse) | 31 + 77 = 108 µs | same |
| Gateway apply worst | 4.1 ms (offloaded: > 32 records or worst bodies run in the thread) | same |
| View memory | 6,179 B per typical org; 419 B per empty org | same |
| View capacity at the 400 MiB default, share 10% | 6,788 typical orgs per worker | derived |
| Request-path lookup | 1 alias probe at any estate size; HEAD 1.2 ms / 5.1 ms per request at 100k | `routing_scan_head.txt` |
| Projector steady round (MODEL part) | 3.8 ms CPU + 72–98 ms revision read | `projector_bench.txt`, `pg_source_bench.txt` |
| Projector, 1% churn (1,000 orgs) | 47 ms CPU + 2 × ~150 ms reads (500-org chunks) | same |
| Projector, full derivation (new estate) | 8.2 s CPU, spread over rounds as liveness | `projector_bench.txt` |
| New leader resume | memo seed 119–158 ms; then only changed orgs | `pg_source_bench.txt` |
| Projector memory | 26 MB per-org memo; F1a-3 row memo 179 MB if in RAM (use a table) | `memo_memory.txt` |
| Feed rate | ≤ 2 writes per org per round; human edits are rare; bulk events (F1a-3 re-wrap, grammar change) are bounded backlogs | design |
| Worker bytes for a 100k-org re-projection | ~4 MB of index + bodies of cached orgs only (≤ 6,788 × 2.6 KB ≈ 18 MB) | §4.4.1 |
| Re-hydrator deep verify | 142–183 ms (index only) vs 221 MB per read for HEAD's snapshot (1.4 s, or cancelled) | `pg_source_bench.txt` |

**Hardware independence (D5).** Every bound derives from the contract (view capacity), the knobs
(periods, `lag_ms`) or the estate (orgs, changes). None is a host constant. More workers cost one
view each; more orgs cost revision-read time, linearly (72–98 ms per 100k).

**Stated limit.** Changed *existing* orgs are read before the heartbeat, at 150–300 ms per
500 orgs on the loaded host: about 26k–53k such orgs fit in one `lag_ms` (16 s). A bulk edit
beyond that in one round delays the heartbeat past `lag_ms`, and the fleet fails closed until
the round completes. Bulk first-time onboarding is not affected (liveness, bounded per round).
The org-wide kill switch, not a mass model edit, is the tool for a fleet-wide stop.

**What remains to measure live**: `put_existing` chunk I/O (T2.16, L2.7), the trigger's
server-side cost, and Valkey operations per MODEL write (the F1d baseline, parent §8).

## 6. Security

- **Tenant isolation.**
  - The record key is the slug of the row's `organization_id` (an FK, never body data). The
    gateway refuses a body whose `org_id` differs from the key.
  - A request resolves only in the view pinned for its authenticated org.
  - Nothing on the request path iterates or reads another org.
- **Signatures.** MODEL records use the existing record MAC with the kind inside it; the floor
  record uses its own domain `floor1`. Verification failures are round-fatal and alarmed.
- **A malicious console admin of org A can:**
  - configure A's aliases, upstreams, base URLs and keys;
  - make A's record up to 512 KiB (bounded);
  - name an alias after a fleet model (the fleet kill then over-refuses A only);
  - churn edits (coalesced to ≤ 2 writes per round);
  - put a 499-deep or `RecursionError`-raising value in any field (`row_invalid`; the round
    continues).
- **They cannot:**
  - write another org's record;
  - stop or slow the projector round (per-row and per-org isolation; bounded per-org work);
  - trip the breaker (console facts never hold; 128 > one org's maximum);
  - evade a fleet `model:` kill by renaming the alias (the upstream is in the match set);
  - get a plaintext key resident in a gateway (ciphertext only).
- **They can still** reach an upstream through a dated id or their own OpenAI-compatible proxy
  that a fleet kill does not name (§4.7 residual).
- **SSRF**: F1a-2 makes no outbound connection (stub provider). Structural URL checks run at the
  projector; address checks are F1a-3's.
- **Downgrade and rollback of state:**
  - floors and manifest anti-regress per kind;
  - per-warm-org version refusal;
  - cold-fetch score pairing;
  - the index sweep (bound 60 s + 30 s against a store writer);
  - `StateWriter.rollback` refuses MODEL;
  - a stamp lacking MODEL cannot make MODEL fresh;
  - the floor only rises without `--allow-lower`.
- **Generation-1 images** cannot be bound by code; tag policy plus the generation alarm (§3.2).
- **Logs**: notes carry slug, escaped alias and reason only (§4.2.6). L2.6 greps logs, traces and
  metrics for a planted `base_url`, `model_id` and env var name.

## 7. Test plan

House idiom: seeded `random.Random`, at least 10,000 iterations for properties, seed in messages.
Locally the targeted tests run under `nice`; heavy runs go through `.heavy.lock`, control under
`.ctl-db.lock`; CI runs the full suites.

### 7.1 Release 1 (unit and property)

| ID | Test | Pass when |
|---|---|---|
| T1.1 | Raw-map signer vs HEAD signer, every HEAD-mintable stamp | 10,000/10,000 byte-identical MACs |
| T1.2 | `decode_stamp` table: unknown kind ignored; forged, malformed name (`\n`, `=`), bool or negative cursor, missing required → refused | every row as stated (prototype: `stamp_compat.txt` [4]) |
| T1.3 | `StampView`: floors only rise per known kind; unknown kinds ignored; `covered_at` per kind; a stamp missing a base kind refused and counted | as stated |
| T1.4 | Two re-hydrators (fakeredis and `MemoryStore`), 10,000 random interleavings, mixed generation-2 and generation-3 configurations | stored `verified_at` never decreases; HEAD comparator fixture shows the regression (negative control) |
| T1.5 | Floor: lower refused without `--allow-lower`; re-hydrator refuses to stamp on an unknown floor kind or a low generation; projector refuses MODEL below 3; gateway not ready below the floor | as stated, with log and metric |
| T1.6 | Synchroniser: an applier raising `RuntimeError` or `RecursionError` in one kind | that kind reports an error; other kinds drain in the same cycle; fails at HEAD |
| T1.7 | Mixed-version matrix (§3.3) with HEAD's `decode_stamp` and `_stamp_is_newer` vendored as a fixture, hash-pinned to `25c33c86` | every cell as in §3.3 |

### 7.2 Release 2 (unit and property)

| ID | Test | Pass when |
|---|---|---|
| T2.1 | Golden canonical bodies (empty, typical, worst); size bound at the projector and the gateway | bytes stable; over-bound refused both sides |
| T2.2 | Derivation table: every omission reason, the prefix table per provider, platform and NULL-org rows skipped, collisions, cap, encrypted key beats env var | as stated |
| T2.3 | **Idempotence property**: random estates and edit sequences, projected twice, rows shuffled | 0 writes on the second pass; identical bytes; versions move only on body change |
| T2.4 | **Two versions property**: random `(H, D)` | `S` has no adds; every removed or changed entry of `H` is out of `S` or changed in it; liveness = `D`; heartbeat after `S` durable |
| T2.5 | **Failure isolation**: poison rows (`RecursionError` raiser, 499-deep value, lone surrogate, wrong types) and a raising per-org assembly | round completes; heartbeat within `lag_ms`; other orgs and other rows of the org written |
| T2.6 | **Breaker**: mass systemic omission held; mass `inactive` never held; changed credential never held; one org never trips | as stated, alarm series set |
| T2.7 | **Gateway applier**: body defects → `Unavailable(version)`, cursor advances; unknown provider or field → entry dropped; signature or pairing defect → round error, cursor stays; readiness unaffected by one org | as stated |
| T2.8 | **View**: capacity from the contract; eviction metric; `_touched` (refetch once, then 503); score mismatch → 503; regressed manifest → 503; fill above the cursor not cached; warm rollback refused; sweep evicts | as stated |
| T2.9 | **O(1)**: instrumented probe count at 1k and 100k orgs (estate and view); HEAD control linear | equal counts; HEAD ≥ 50× between sizes |
| T2.10 | **Grammar**: one constant for wire, gate, routing and console (import plus literal identity across planes); 128-character alias with `/` end to end; `@`/`+` refused in aliases and accepted in upstreams | as stated |
| T2.11 | **GW29**: fleet `model:` matches alias, upstream and `provider/upstream`; `om:` on the alias; `iso:` reroute resolved per org; **isolate A→B then delete B → `REROUTE_REFUSED`/`fallback_not_served`**; auto-isolation aggregates two orgs; a MODEL delta between gate and dispatch does not change the dispatched entry | as stated |
| T2.12 | **D4**: env-var-only → `model_unavailable/credential_env_var`, zero provider calls | as stated |
| T2.13 | **Filters**: org `allowed`/`default` (`auto`); key `allowed_models` in KEY behind its flag; flag refused below floor 3 | as stated |
| T2.14 | **`/v1/models`**: list filtered; retrieve omitted → `model_unavailable`; empty org → `[]`; unverified → 503 | as stated |
| T2.15 | `StateWriter.rollback` refuses MODEL; deep verify uses the index-only query; chunked MODEL repair keeps readers consistent except during `store_ahead` | as stated |
| T2.16 | **Scale**: 100k orgs × 5 against the revision-based twin source and a counting `ControlDB` | steady MODEL phase < 100 ms CPU; 1% churn < 500 ms CPU; new leader with a persisted memo writes 0 records; first projection never delays the heartbeat (all liveness); `put_existing` of 100 records measured and recorded |

### 7.3 Control tests

- Migration: the revision table is backfilled and every trigger path bumps the revision: ORM
  save, `QuerySet.update`, delete, FirewallConfig filter edit, org slug and `is_active` edits,
  `TRUNCATE`.
- Serializer: alias grammar; case-variant duplicate refused; 65th active model refused.
- The control suite under the ratchet (`scripts/ci/known_failures.py`).

### 7.4 Live (compose, `gateway-v2-prod` with the stub provider)

| ID | Scenario | Pass when |
|---|---|---|
| L1 | Release 1: two re-hydrators (release-1 image and `develop` image) and a gateway of each for 10 min | 0 `stamp_invalid`; stored `verified_at` monotonic; all gateways ready; floor `set` to 2 accepted |
| L2.1 | 50 adds through the console API | `/v1/models` shows each: p95 ≤ 3 s, max ≤ 5 s, one-worker and two-worker |
| L2.2 | 50 deactivations | chat → `model_unavailable/inactive`, same budget, zero provider calls |
| L2.3 | Disable + stop the projector | no served answer after `t0 + 21 s` |
| L2.4 | Fleet `model:<upstream>` kill | refuses an org alias that maps to it; `om:` refuses only that org |
| L2.5 | Mixed fleet through rollout steps R2→R5 (§8): generation-2 and generation-3 gateways and re-hydrators | every gateway stays ready at every step; no `StoreDataUnavailable` on KS/PLAN/KEY |
| L2.6 | Planted `base_url`, `model_id` and env var name | absent from logs, traces, metrics and audit rows |
| L2.7 | Kill the projector leader during a 1,000-org edit burst | standby resumes from the memo; heartbeat age < `lag_ms`; no duplicate versions |
| L2.8 | Metrics, logs and console agree on omission counts per reason for a seeded org set | equal |

### 7.5 Exit checklist (F1a-2)

T1.x, T2.x and the control tests green; CI green; L1 and L2.1–L2.8 pass with recorded outputs;
§5's live items measured; two plan reviews and two code reviews approved.

## 8. Rollout and rollback

| Step | Action | Gate to proceed | Rollback |
|---|---|---|---|
| R1 | Deploy release 1 (generation 2) everywhere | `min(amf_state_generation) ≥ 2` over every scraped gateway, re-hydrator and projector for 24 h; generation-1 tags removed from the registry | Redeploy generation 1 (safe: release 1 changes no bytes) |
| R2 | `floor set --min-generation 2` | — | `--allow-lower` |
| R3 | Deploy release 2 (generation 3) everywhere, all MODEL flags off | `min ≥ 3` for 24 h | Redeploy generation 2 |
| R4 | `floor set --min-generation 3`; control migration (revision table, triggers); projector `AMF_STATE_PROJECT_MODELS=on` | initial projection done (`amf_projector_model_backlog == 0`) | Flag off (records stay; nobody reads them) |
| R5 | Re-hydrators' MODEL capability on; `floor set --stamp-kinds +model` | `covered_at[model]` fresh on every gateway | Remove `model` from `stamp_kinds` (gateways with MODEL on go MODEL-unverified: do R6's rollback first) |
| R6 | Gateways `AMF_GATEWAY_MODEL_STATE=on` (rolling) | L2.x pass | Flag off (rolling) |
| R7 | Control `AMF_V2_MODEL_FILTERS_SERVED=1`; projector `project_key_model_filter=on` | T2.13 and live filter probe | Off |

**Forbidden, and refused by code:**
- a generation-2 re-hydrator while the floor names `model` (it refuses to stamp);
- a gateway image below `floor.min_generation` (not ready);
- a projector flag above the floor (refusal).

**Forbidden, and refused only by policy:** any generation-1 image after R2.

## 9. Parent requirements I believe are inaccurate or in tension (with evidence)

1. **§6.7: "allowed_models exists on keys and on the org, and both are retired in
   `firewall_v2_map.py:75-84`."** Only the org fields are retired there. The key field is
   accepted by `core/gateway_serializers.py:83-88` and read by nothing in v2
   (`projector.py:584-599`, `identity.py:99-108`). Today it is "stored and ignored", the state
   `firewall_v2_map` exists to prevent. §4.6 fixes it; the premise needs correcting.
2. **§6.2 lists "unknown provider or field" twice**: as an org-level `unavailable(version)`
   cause and as entry-only. I apply it entry-only and keep whole-body defects org-level (§4.4.2).
3. **§6.1: "requires only the kinds this build applies."** Taken literally, a MODEL-applying
   gateway would refuse whole stamps without MODEL, so a MODEL coverage gap would stale identity,
   plans and the kill switch. §3.1 item 4 keeps the base stamp strict and puts the MODEL
   requirement on the MODEL gate. Same fail-closed result for MODEL, without widening the blast
   radius.
4. **§6.3: "a test shows the cost does not change between 1k and 100k orgs."** With an unbounded
   dict, wall time does change through cache effects (287 vs 1,461 ns per lookup,
   `routing_scan_head.txt`) while the algorithm is O(1). T2.9 asserts probe counts and uses the
   bounded view; wall time is reported, not asserted.
5. **§6.6: "a round-time test at 100k × 5 with a budget well under `lag_ms`."** This is
   unattainable with HEAD's projector pattern: the full source read is cancelled by the 800 ms
   bound, and the per-round snapshot is 221 MB (`pg_source_bench.txt`). Meeting it needs the
   **control migration** (revision table and triggers), which §6 does not mention.
6. **§6.9: "per-org isolation key on `(org, alias)`."** No per-org isolation scope exists at HEAD
   (`incident_scopes.py:88-91`; `ModelState` is not projected). There is nothing to re-key;
   noted in §4.7.
7. **Not in §6, found at HEAD and fixed here**:
   - a non-`StoreDataUnavailable` applier exception aborts the whole state cycle
     (`state_task.py:153-160,232-234`; `delta.py:107-112`);
   - deep verify reads every body (`rehydrate.py:349`), and a repair is one MULTI of the whole
     kind (`valkey.py:208-243`); neither is viable for MODEL at scale.

   The PLAN half of the first may be wanted sooner as a hotfix.
8. **Line drift in parent §3**: `serve.py:175,237,242` are `176,233,252` at `25c33c86`.

## 10. Open questions for the owner

- **Q1** `default_model` = the per-org target of `auto` only. An absent `model` stays a 400
  (GW29). v1 also defaulted an empty model when routing was on (`main.py:4610-4637`). Confirm.
- **Q2** Keyless endpoints. `ollama` is keyless (`{"t":"none"}`). A `custom` row with no key and
  no env var is omitted `credential_missing` (parent §7.6 "keyless rows are omitted"). Is an
  explicit console "keyless" flag for custom endpoints wanted later?
- **Q3** `google` = Gemini API (AI Studio, `gemini/`). `vertex_ai/` ids need OAuth service
  accounts and are omitted `upstream_unsupported`. Confirm Vertex is out of F1.
- **Q4** One upstream per alias (the schema allows no more). Multi-upstream aliases (weighted
  HRW across providers) would need a control model-group table. Confirm out of F1.
- **Q5** 64 models per org: control enforces it at save, the projector by omission. Confirm the
  number.
- **Q6** A console removal mid-stream: finish in-flight requests (proposed) or cut them?
- **Q7** The control migration adds a table and triggers to control's database, owned by `core`.
  Acceptable, or should control write the revision from its save paths instead (this misses
  `QuerySet.update` and raw SQL)?

## 11. Evidence (`docs/plans/evidence/2026-11-11-f1a2-model-state/`)

| File | What it shows |
|---|---|
| `stamp_compat.py` / `.txt` | Release-1 decoder against HEAD's real `state_sig` and `valkey`: identical MACs; HEAD refuses `model`; HEAD's held-stamp overwrite moves time back; refusal table; cost |
| `model_record_bench.py` / `.txt` | Record sizes, sign/verify/parse cost, idempotence, view memory per org, lookup |
| `routing_scan_head.py` / `.txt` | HEAD's O(orgs) routing lookups vs one probe per request |
| `projector_bench.py` / `.txt` | Derivation at 100k × 5: cold, steady, 1% churn, poison row, idempotence, row-memo memory |
| `memo_memory.txt` | Compact per-row and per-org memo memory |
| `pg_source_bench.py` / `.txt` | Postgres 16 scratch database at 100k × 5: naive reads vs the revision design under the 800 ms bound; trigger behaviour |
| `aead_bench.py` / `.txt` | AES-256-GCM decrypt per request; Fernet decrypt per changed row |

Reproduce: the commands are in each script's docstring.

## 12. Parent §6 traceability

| §6 item | Requirement | Section |
|---|---|---|
| 6.1 | Stamp verifies over the raw map, ignores unknown kinds, requires the applied kinds | §3.1 (1–4), §9.3 |
| 6.1 | Held-stamp comparison ignores missing kinds | §3.1 (3), T1.4 |
| 6.1 | Re-hydrator names MODEL and projector emits it only behind flags | §3.2, §4.2.1 |
| 6.1 | Rollout floor enforced: generation exported; flags checked against the fleet minimum; images run step 1 first; re-hydrator rollback refused | §3.1 (6), §3.2, §8 |
| 6.1 | Tests: mixed-build re-hydrator pair; stamp time never backwards | T1.4, T1.7, L1 |
| 6.2 | Signature and index failures round-fatal and alerted | §4.4.1 |
| 6.2 | Body failures per record → org `unavailable(version)`, cursor advances | §4.4.2, T2.7 |
| 6.2 | Unknown provider or field → entry omitted, counted | §4.4.2, §9.2 |
| 6.2 | Readiness never depends on one org | §4.4.2, §4.4.4 |
| 6.2 | Rollback refusal per warm org; cold-fetch gap closed or bounded | §4.4.3 (pairing, sweep, 60 s + 30 s bound) |
| 6.2 | `StateWriter.rollback` refuses MODEL | §4.3, T2.15 |
| 6.3 | `serves_alias`, `preflight`, `_plan` read a per-org `(org, casefolded alias)` dict | §4.4.5 |
| 6.3 | Per-entry checks at apply; `all_candidates()` off request and apply paths | §4.4.5 |
| 6.3 | Test: same cost at 1k and 100k orgs | T2.9, §9.4 |
| 6.4 | Contract-derived LRU, eviction metric, `_touched` retry | §4.4.3 |
| 6.4 | Evaluate ciphertext-only plus per-request decrypt | §4.4.3 (0.86 µs measured), §4.9 |
| 6.5 | Every active org has a record; empty → `no_provider_configured` | §4.1, §4.4.6 |
| 6.5 | Offboarding writes a tombstone | §4.1 |
| 6.5 | Omitted rows carried with a reason; declared code instead of `model_not_found` | §4.1, §4.4.6 |
| 6.6 | Memo per `row_id` (Fernet, AAD, triple); decrypt only on change | §4.2.5, §4.9 |
| 6.6 | Plan validation memoised by body hash | §4.2.5 (F1a-1 §5.2.3) |
| 6.6 | Notes one per note, never `base_url` | §4.2.6 |
| 6.6 | Removals and key changes before the heartbeat; re-wraps a bounded backlog after | §4.2.4, §4.9 |
| 6.6 | Breaker | §4.2.7, T2.6 |
| 6.6 | Mixed edit as two versions | §4.2.4, T2.4 |
| 6.6 | Round-time test at 100k × 5 | §4.2.2, §5, T2.16, §9.5 |
| 6.7 | No `weight`/`api_version`/`mode`; one candidate per alias | §2.6, §4.1 |
| 6.7 | Filters: carrier named; un-retired | §4.6, §9.1 |
| 6.8 | One grammar in four places; `@`/`+` refused; collisions omitted; upstream cap 256 | §4.5, T2.10 |
| 6.9 | `om:` on `(org, alias)` | §4.7 |
| 6.9 | Fleet `model:`, `iso:`, auto-isolation match `(provider, upstream)` | §4.7, T2.11, L2.4 |
| 6.9 | New key shapes behind the scope generation | §4.7 (none added) |
| 6.9 | View pinned before 4b; gate reads the pin | §4.4.5 |
| 6.9 | Tests: isolate A→B then delete B; old generation keeps serving | T2.11, L2.5 |
| 6.10 | Freshness bound 21 s asserted in disable and rotation tests | §4.10, L2.3 (rotation: F1a-3) |
| 6.11 | Key-stored, not in `HASH_KINDS` | §4.1 |
| D3 | Live within seconds, with a measured budget | §4.10, L2.1–L2.2 |
| D4 | Env-var keys refused in production | §4.8, T2.12 |

## 13. Not in F1a-2

Credentials and encryption, SSRF, pools, dispatch, the HTTP codes table (F1a-3). Azure
`api_version`, the Anthropic adapter (F1b). Bedrock SigV4 (F1c). Valkey sharding (F1d).
Multi-upstream aliases (§10 Q4). Per-model rate limits. GW28 weighted routing (stays retired).

---
_Generated by [Claude Code](https://claude.ai/code/session_01SDCVQVj8LRKW3CZ99zfdW1)_
