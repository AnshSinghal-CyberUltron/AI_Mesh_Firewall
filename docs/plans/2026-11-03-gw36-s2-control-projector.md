# GW36 S2 — Control-to-state projector (plan)

Status date: 3 November 2026. Branch `suraj-revamp`. Parent plan:
`docs/plans/2026-11-02-gw36-production-boot.md` §9.4 row S2. Nothing below is implemented until the
reviews in §8 have challenged it.

## 1. Goal

Control (Django) stays the system of record. A new projector process turns control's rows into the
signed v2 state records the gateway already knows how to read, so S3 can resolve real keys and
plans. Exit evidence (live): console key create → signed KEY in Valkey; config save → signed PLAN;
key deactivated → REVOKE record; kill switch engaged → KS on; projector stopped → freshness stamp
withheld.

## 2. Facts this plan rests on (verified in code, 3 Nov)

| # | Fact | Evidence |
|---|---|---|
| F1 | Control and `state_control` share one database (`ai_mesh_firewall` via `pgbouncer:6432`, transaction pooling) | `docker-compose.yml:123,572` |
| F2 | Source tables: `core_gatewayapikey` (`key_hash` = sha256 hex, `is_active`, `expires_at`, `organization_id` nullable), `auth_api_organization` (`slug` unique, `is_active`), `core_killswitch` (`organization_id`, `model_name`, `api_key_prefix`, `is_active`, `action`) | `core/models.py:281-374,760-867`, `auth/models.py:6-20` |
| F3 | Control has **no budget or token-pool field**; the closest is `FirewallConfig.org_tpm_limit` (default 0 = off) | `core/models.py:956-959` |
| F4 | Control writes the UNSIGNED plan document to `{rv2}:plan:{org}`, the key the signed PLAN feed owns; it skips orgs absent from `AMF_V2_AUDIT_ORG_MAP` | `firewall_v2_publish.py:56-58,96-98,281` |
| F5 | An org with no `FirewallConfig` row compiles to "firewall off" (`{"firewall_enabled": False}`); v1 instead treats it as enabled with model defaults (`FirewallConfig.load` → `get_or_create`), and `drafts_for` defaults every flag to on | `firewall_v2_publish.py:262-272`, `core/models.py:919,1364-1369`, `firewall_v2_map.py:115-126` |
| F6 | `StateWriter.put` bumps version + feed_seq on every call, even for an identical body; `key_revoke` reads and writes in two transactions | `writer.py:87-103,168-178`, `db.py:233-252` |
| F7 | No advisory locks; writers serialise on `amf_state_counter ... FOR UPDATE`; re-hydrators are deliberately leaderless | `pg.py:134-151`, `service.py:19-22` |
| F8 | `init_schema()` has no production caller | `pg.py:379`; tests only |
| F9 | Gateway readers: KEY body `{key_id, org_id, rate_per_s, burst}` (deleted = revoked); PLAN body = plan document whose `org_id` equals the record key; KS body `{"on": bool}` keyed `global` / `org:<id>` / `model:<name>` (model scope is global, not per org) | `admit/identity.py:59-93`, `plan/delta.py:48-73`, `admit/killswitch.py:38-40,65-86` |
| F10 | A stamp must cover all four kinds; the re-hydrator repairs an empty kind to a count-0 manifest at feed_seq 0, which readers accept | `state_sig.py:216-221`, `rehydrate.py:287-288`, `state_feed.py:250` |
| F11 | **`store_ahead` is worse than the xfail says.** After `repair_epoch`, the manifest sits at N+1 while every record is at ≤ N. The re-hydrator loops on INDEX, and every gateway reader refuses the kind too (`index head N behind attested N+1`). Separately, a reader that applied the store's higher position M rejects any manifest below M whatever its epoch (`feed_seq < feed_floor`) | `writer.py:184-200`, `rehydrate.py:312`, `state_feed.py:240-244`, `state_sig.py:398-401` |

## 3. Design

### 3.1 Control: plan documents move to Postgres (G2, N16, F4, F5)

- New model `V2PlanDocument`, `db_table = "core_v2_plan_document"`: `organization` OneToOne
  (CASCADE), `body` text (the compact sorted-key JSON control already builds), `revision` bigint,
  `updated_at`. Migration `core/0041`.
- `publish_saved_plan(slug, payload)` builds the document with `org_id = slug` and upserts the row
  (revision bumps only when the body changed). It no longer writes `{rv2}:plan:*`. For an org
  mapped by `AMF_V2_AUDIT_ORG_MAP` it ALSO writes the dev copy, with the alias as `org_id`, to
  `{rv2}:devplan:{alias}` (dev profile only; a failed dev write never fails the save).
- No `FirewallConfig` row → serialise an unsaved `FirewallConfig(organization=org)`, i.e. the model
  defaults (v1 parity, F5). This **is** the "platform default plan" of parent §9.3, compiled by the
  one mapper control already has; the projector never invents rules.
- `Organization` created → `on_commit` publish. New command `manage.py sync_v2_plan_documents
  [--org-slug S]` recomputes every active org (backfill on deploy, and convergence after seed
  commands and bulk updates, which bypass the views).

### 3.2 Gateway dev feed (G2)

`edge/dev_plan_feed.py` reads `{rv2}:devplan:{org}` instead of `{rv2}:plan:{org}`; the dev key
fallback is unchanged. After this, no reader sees two formats under one key. The
`vector_credentials.py:131` docstring is corrected.

### 3.3 Writer: compare-and-skip and a fence (N3)

- `put(..., if_changed=False, fence=None)`. With `if_changed`, inside the counter lock: an existing
  record with the same `content_hash`, the same `deleted` and (for KS) the same engaged flag is not
  rewritten; the outcome is the new status `unchanged` (durable, nothing published).
- `fence=holder`: inside the same transaction, after the counter lock, `tx.holds_lease(holder)`
  (`SELECT ... FOR SHARE` on the lease row, holder matches, `lease_until > now()`); false raises
  `LeaseLost` and the transaction rolls back. `put_many` gains `fence` too.
- Defaults are unchanged, so every existing caller and test keeps its behaviour.

### 3.4 Projector lease and heartbeat (N2, F7)

New table `amf_state_projector (id smallint PK CHECK (id = 1), holder text, lease_until
timestamptz, last_ok_at timestamptz)`.

- Why a lease when the re-hydrators have none: a re-hydrator copies Postgres to the store, so two
  of them converge. A projector copies a **moving source** into Postgres. Two leaderless projectors
  can order writes against source time: A reads "key active" at t1, B reads "revoked" at t2 and
  writes the revoke, then A writes "active". That reactivates a revoked key until the next round.
  With the lease plus the in-transaction fence, a takeover waits for in-flight fenced writes to
  commit (row lock against `FOR SHARE`), and every write by the old holder after that fails.
- Acquire or renew each round: `INSERT ... ON CONFLICT (id) DO UPDATE ... WHERE holder = me OR
  lease_until < now() RETURNING holder`. A standby does nothing but retry.
- Heartbeat: at the end of a complete round the leader sets `last_ok_at = now()` (database clock).
  "Complete" means every kind was diffed and written. A bad document for one org is skipped and
  counted, not a round failure, because one tenant's bad data must not stop the fleet's stamp.
- Freshness proof: `Rehydrator(source_lag_ms=...)` reads `now() - last_ok_at` once per round, an
  O(1) read. A missing row or a lag over the bound withholds the stamp, with the reason logged and
  counted through the existing withheld counter. While Postgres is down, the existing ride-through
  is unchanged. `source_lag_ms=0` means not enforced and is the constructor default, so existing
  tests keep working. The service default is `PG_GRACE_MS` (16 s), the owner's 0.7-2 ride-through
  bound.
- Knobs (new `ProjectorKnobs` in `domain/state_knobs.py`, named-constant defaults): period 1000 ms,
  lease 5000 ms, lag bound 16000 ms; validated `lease ≥ 2 × period` and `lag ≥ lease + period` (a
  failover must not withhold the stamp).
- `init_schema()` runs in `RehydratorService.build` and `ProjectorService.build` (G13). The DDL
  takes `pg_advisory_xact_lock` first. That lock is transaction-scoped, so it works through
  PgBouncer transaction pooling, and it serialises two processes creating the tables at once.

### 3.5 Projection (G1, N15, N16)

One source read per round in a read-only REPEATABLE READ transaction, through
`PostgresControlDB.cursor_tx()` so the session bounds apply. It selects explicit columns, never
`SELECT *` (some databases still carry a stray `key_purpose` column).

| Kind | Desired state | Removal |
|---|---|---|
| KEY (key = `key_hash`) | Key and its org both active, and `expires_at` null or in the future, give `{key_id: str(id), org_id: slug, rate_per_s: 0.0, burst: 0.0}`. Rate 0 = no per-key request limit, declared (parent §9.3); the org token budget is S4 | A live record not in the desired set becomes a REVOKE (`deleted`, previous body). This covers inactive, expired, deleted and org-less keys, and inactive or deleted orgs |
| PLAN (key = slug) | An active org's document row. It is decoded and compiled with `gateway_v2.plan` before writing; `org_id` must equal the slug | A live record for an org that is no longer active becomes an OFFBOARD record |
| KS (key = `org:<slug>`) | An engaged row with `model_name='__global__'`, `api_key_prefix=''`, `action='disable'` and an active org gives `{"on": true}`, engaged | A record that is on but no longer engaged gets `{"on": false}`. A scope that was never engaged gets no record (N15) |

- A bad document for one org keeps that org's previous record and is counted. An active org with
  no document row gets no PLAN record (the gateway refuses that org) and is counted. The fix is
  `sync_v2_plan_documents`.
- Unsupported kill switches are counted and named in the log. These are per-model,
  per-credential, `reroute`, and NULL-org (v1 "default") switches. v2's `model:` scope is global, so
  mapping an org's model switch onto it would kill that model for every tenant. Request-path
  kill-switch enforcement is GW29.
- Writes: records absent from `amf_state_record` go through `put_many` in chunks (one manifest and
  one nudge per chunk; first-run onboarding). Changed records go through `put(if_changed=True)`.
  Every write is fenced.
- BUDGET: not projected in S2 (F3). The kind keeps the re-hydrator's count-0 manifest, which
  satisfies the stamp (F10). BUDGET records and pool seeding move to S4, where the quota owner
  decides what control field feeds them.
- Diff cost: O(keys + orgs) per round, both from Postgres. It is measured live; incremental
  projection waits until a measurement says it is needed.

### 3.6 `store_ahead` repair (F11)

`repair_epoch(kind, *, store_feed_seq)` re-signs every record of the kind in one transaction at
the new epoch and at fresh positions above `max(pg.feed_seq, store_feed_seq)`. The re-hydrator
passes the position it just read. The repaired manifest is then above every reader's floor, the
index top equals the manifest, and the next round is a no-op. The strict xfail flips to a pass.

An empty kind cannot be raised above a reader's floor, because readers refuse count 0 at a
position above 0. In that case it keeps today's epoch bump, and readers holding the higher
position stay refusing (fail closed) until restart. That edge is declared, not hidden.

### 3.7 Process and deployment

- `state_control/projector.py` (pure round logic), `state_control/source.py` (types +
  `MemorySource`), `state_control/source_pg.py` (SQL), `state_control/projector_service.py` (env,
  loop, `/healthz` + `/metrics`, `python -m state_control.projector_service`). It writes through
  the bare `ValkeyPublisher`, not the coalescer: projector writes are rare and must not wait on a
  window.
- Metrics, label-free: rounds, round failures, leader gauge, last success timestamp, writes,
  unchanged, revokes, invalid plans, orgs without a plan, unsupported switches, round duration.
- Compose: a `state-projector` service using the state-control image, with the idle-stop
  exemption, a healthcheck and metrics on 9109. One replica; the lease makes a second one safe.
  The prod anchor is added and both re-hydrators get `AMF_PROJECTOR_LAG_MS`. The R2-04 deployment
  gate is extended.

## 4. Not in S2

BUDGET records and pool seeding (S4). The gateway state runtime, hashed identity and the 401/503
split (S3). Per-key request rates and model allow-lists (GW27/GW28). Kill-switch enforcement
(GW29).

## 5. Tests (house idiom: seeded `random.Random`, ≥ 10,000 iterations, seed in the message)

1. Writer: unchanged bodies are skipped (version untouched), a changed body bumps once, and the
   KS engaged flag is compared. Fence: lease lost → rollback, nothing written.
2. Projector property: random sequences of source mutations (create, deactivate, expire, delete,
   org deactivate and reactivate, slug rename, document edit and invalid document, KS
   engage/disengage/unsupported). After each round the live records equal the desired set, a
   revoked key is never live, and a second round with no source change writes nothing.
3. Lease: two projectors over one twin; only the holder writes, and takeover happens after the
   TTL. A paused old holder that resumes is fenced.
4. Heartbeat: lag within the bound stamps; missing or stale withholds; a Postgres outage still
   rides through.
5. `store_ahead`: the xfail passes; a gateway `FeedReader` that applied the higher position
   accepts the repaired kind.
6. Gateway readers accept every projected record: real `FeedReader` + appliers over fakeredis.
7. Control: publish writes the row and the devplan key, never `{rv2}:plan:*`; a missing config
   gives the model defaults; the gw26 tests are updated.
8. Live Postgres (gated on `AMF_PG_DSN`): source SQL against minimal Django-shaped tables, the
   fence through a real `FOR SHARE`, and concurrent `init_schema`.

## 6. Live check (real stack)

Migrate control; rebuild the state-control image; start the projector. Then:

1. Create a key through the console API; a signed KEY appears.
2. Save the firewall config; a signed PLAN appears with `org_id` = slug.
3. Deactivate the key; a REVOKE appears.
4. Engage an org kill switch; KS goes on.
5. Run a second projector; it stays standby.
6. Stop the projector; the stamp is withheld after the lag bound. Restart it; stamping resumes.

Every record is decoded with gateway_v2's own `FeedReader` and appliers, not just by inspecting
bytes.

## 7. Estimate

3–4 agent-days, as the parent plan says. BUDGET moved out, the `store_ahead` repair moved in.

## 8. Review outcome (supersedes §3–§6 where they disagree)

Five contradictory reviewers attacked the plan; each finding was re-checked in code. F1–F11 were
all confirmed. The corrections below are accepted; they change the design as stated.

| # | Finding (reviewer) | Evidence | Accepted change |
|---|---|---|---|
| R1 | `cursor_tx()` is read-WRITE READ COMMITTED; only `_connect(read_only=True)` gives REPEATABLE READ, so §3.5's "one consistent read" was false (1, 3) | `pg.py:336-339,397` | `cursor_tx(read_only=True)`; key, org and switch reads share one snapshot; expiry compared with `transaction_timestamp()` in SQL |
| R2 | Heartbeat gate + one replica = fleet-wide single point of failure; a Postgres failover leaves the heartbeat older than the bound after recovery (2, 4) | `service.py:19-22`, `rehydrate.py:536-594` | Two projector replicas (`state-projector-a/-b`). Heartbeat = the source snapshot's `transaction_timestamp()`, not "end of round". During an outage the ride-through is unchanged, so the post-recovery gap is about one round, well inside `FRESH_MS` |
| R3 | A round longer than the lease fences itself out, so the heartbeat stalls during onboarding (2) | `writer.py:267-300` | The fence RENEWS: each fenced write runs `UPDATE amf_state_projector SET lease_until = clock_timestamp() + ttl WHERE holder = me AND lease_until > clock_timestamp()`. That is a row lock to commit, stronger than `FOR SHARE`. A standby only SELECTs, and UPDATEs only when the lease has expired (no lock contention) |
| R4 | Safety vs liveness: bulk onboarding must not age the revocation heartbeat (2, 3, 5) | — | Round order: KS on → KEY revokes → PLAN set / offboard → **safety heartbeat** → KS off → KEY adds. Adds are capped per round (`DEFAULT_PROJECTOR_ADD_CHUNKS_PER_ROUND`); the rest go next round. Slow onboarding then delays new keys only, never revocations. The order also covers slug rename and org create (plan before keys; switch on before anything) |
| R5 | The `store_ahead` re-sign must keep `deleted`, `op`, body bytes and `engaged`, and must clear the store's EPOCH, not just its position (2) | `writer.py:278,289-296`, `state_sig.py:394-396` | `repair_epoch(kind, store_version, store_feed_seq)` re-signs each stored record as-is at epoch `max(pg, store) + 1` and positions above `max(pg, store)` feed_seq, preserving engaged |
| R6 | Today an empty kind's `store_ahead` repair writes count 0 at feed_seq 1, which EVERY reader refuses for good. That is BUDGET's state in S2 (1) | `state_feed.py:250` | An empty kind gets the epoch bump at feed_seq 0, which cold readers accept. Warm readers holding the higher position stay fail-closed until restart (declared) |
| R7 | Bulk KS writes without `engaged` leave `on_count` wrong, and readers then refuse the whole KS kind (5) | `writer.py:277-287`, `killswitch.py:216-221` | KS and PLAN writes always go through single `put` with `engaged`; `put_many(require_new=True)` is used for KEY adds only, and raises inside the transaction if a key already exists |
| R8 | `put_many` on an EXISTING key moves its index score above the manifest mid-publish (2) | `valkey.py:191-197`, `state_feed.py:267-271` | Covered by R7's `require_new`. Changes and revokes use atomic single `put` |
| R9 | Two concurrent console saves can write plan documents out of order; `revision` does not order them (3) | `firewall_config_views.py:51-79`, `policy/views.py:645-647` | `publish_saved_plan` runs in its own `transaction.atomic()` and takes `select_for_update()` on the org row. It re-reads FirewallConfig and policies inside the lock, so the last writer holds the newest source |
| R10 | `compile_plan` never compiles regexes; a bad pattern surfaces at request time as a per-rule refusal (5) | `compiler.py:119-120`, `conditions.py:84-111` | The projector also runs `compile_conditions` and counts refused rules; it still projects, because that is the gateway's own per-rule fail-closed. It validates the canonical bytes (no NUL, no lone surrogate) so one document cannot abort the round |
| R11 | Slugs are not validated in the database; an empty slug makes the scope `org:`, which breaks the KS kind for every tenant (5) | `auth/models.py:10`, `killswitch.py:243-245` | Org slug must match `^[-a-zA-Z0-9_]{1,64}$` and `key_hash` must match `^[0-9a-f]{64}$`; anything else is skipped and counted |
| R12 | Stale raw documents under `{rv2}:plan:*` (e.g. `org-b`) are never overwritten, and would wedge the PLAN kind if a key ever collides (3, 4) | `state_feed.py:309`, `rehydrate.py:285-330` | `sync_v2_plan_documents --purge-legacy-plan-keys` deletes `{rv2}:plan:*` values that are raw documents (not signed envelopes). Run once on deploy |
| R13 | Lag looks like a broken kind; `/healthz` would say ok for a dead leader (4) | `metrics.py`, `service.py:174-176` | Re-hydrator: `amf_state_source_lag_seconds` gauge and `amf_state_stamp_withheld_source_lag_total`. Projector: `/healthz` = loop alive within 3 periods, plus a leader gauge. Alerts for stale heartbeat and no leader |
| R14 | Every state write carries the fixed actor `amf.state.control` (4) | `writer.py:74` | Projector writer actor `amf.projector:<id>`; re-hydrator writer actor `amf.rehydrator:<id>` |
| R15 | The console only understands `published` / `skipped` (4) | `firewall_config_views.py`, frontend | Keep `published` (document saved; projection follows within the declared lag) and add `revision` and `dev_copy`; check and update the console text |
| R16 | `init_schema` under `lock_timeout` 250 ms fails a concurrent start, and `CREATE INDEX IF NOT EXISTS` takes a lock even when the index exists (2) | `pg.py:379-382` | `to_regclass` check first, so no DDL and no lock when the tables exist; otherwise `SET LOCAL lock_timeout` 5 s plus `pg_advisory_xact_lock` |

Corrections found while implementing (evidence in the tests and the live run):

- **R10 was half right.** A NUL cannot reach Postgres: `json.dumps` escapes every control character as `\u0000`, even with `ensure_ascii=False` (`test_a_nul_in_a_document_is_escaped_and_therefore_harmless`). A lone surrogate does fail to encode and is refused.
- **Graceful stop.** A cleanly stopped projector releases its lease. Takeover went from 4.9 s (TTL) to 0.8 s live; a crash still takes the TTL (5.3 s live).
- **N25 (live).** The console API refuses an org-wide kill switch ("Global ('__global__') kill-switch scope is disabled. Target a specific model in your organization."), so every console switch is per-model. v2 has no org-plus-model scope. The projector counts and logs each one as not enforced, and invents no scope. The org-wide mapping is proven through the model (admin/ORM) path. Owner: GW29.
- **Two fail-open paths fixed in control's publisher.** A policy-load exception published a document without the org's policies, and a config-import failure published "firewall off". Both now refuse to store, and the projector keeps serving the previous record.

Declared, not fixed in S2 (each with evidence, owner slice):

- **N18 (S3).** An identity fetch in flight while a revoke delta applies can fill a revoked principal with no expiry; a fill must be refused below an "evicted at" floor (`admit/identity.py:241-251`).
- **N19 (S3).** `ReplicaSnapshot._served` is not cleared on offboard (`plan/snapshot.py:81-84`).
- **N20 (S4).** `GcraParams` rejects `rate_per_s <= 0` (`admit/gcra.py:76-77`), so "rate 0 = no per-key limit" must be a branch, not a GCRA.
- **N21 (control).** The simulator key fallback can assign the creator's profile org (`core/models.py:559-600`); the projector carries whatever org the row holds.
- **N22 (registered differences from v1).** Org-less keys authenticate in v1 and not in v2, counted as `keys_without_org`. A deactivated org's keys stop working in v2. A key created already expired is 401 in v2 and 403 in v1.
- **N23 (scale).** The diff is O(records), revoked records included. A `store_ahead` re-sign holds the counter lock for the whole kind. Both are measured live, not optimised.
- **N24 (GW29/GW28).** Engaged `reroute` and per-model or per-credential switches are counted and logged, not enforced.
