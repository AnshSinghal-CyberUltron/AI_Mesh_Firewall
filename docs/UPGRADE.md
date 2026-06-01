# AI_Mesh_Firewall — Upgrade Guide (Phase 0 → 0.1)

This guide covers **only** changes that require operator action before, during,
or after the Phase-0 upgrade. For the full feature changelog see
[CHANGELOG.md](../CHANGELOG.md). For routine deploy procedure (image tags,
container sequencing, dashboard tuning) see the deployment runbook.

**Audience:** operators upgrading an existing AI_Mesh_Firewall deployment from
the pre-Phase-0 release to the Phase-0.1 release.

**Time budget:** ~10 minutes of operator-attended steps plus migration time
(~5 s on a fresh DB, longer on collections with millions of `EnforcementEvent`
rows).

---

## TL;DR Upgrade Checklist

1. [ ] Generate and inject `POLICY_SIGNING_KEY` into **both** control-plane and
       gateway environments (see [Blocking #1](#-blocking-1--policy_signing_key-required)).
2. [ ] Decide per-org `tier2_strict` posture and pre-stage `FirewallConfig`
       rows (see [Config Required](#%EF%B8%8F-breaking-change--tier2_strict-default-flips-to-true)).
3. [ ] Run migrations **before** rolling new gateway/control code:
       ```
       python manage.py migrate core 0021_firewallconfig_tier2_per_org
       python manage.py migrate policy 0022_enforcementevent_event_class
       ```
4. [ ] Deploy new gateway and control code.
5. [ ] Verify `/health` returns 200 on the gateway and operational telemetry
       events tagged with the new `event_class` discriminator appear in Mongo
       and Postgres.

---

## ⛔ BLOCKING #1 — `POLICY_SIGNING_KEY` required

**What changed:** The gateway no longer falls back to `DJANGO_SECRET_KEY` for
policy-bundle HMAC verification. A dedicated `POLICY_SIGNING_KEY` env var must
be set on **both** the control plane and the gateway.

**Impact if you do nothing:**

* Gateway emits a `CRITICAL` log on startup and on every policy-sync attempt.
* Gateway `/health` returns **HTTP 503** with body
  `{"status":"degraded","reason":"policy_signing_misconfig"}`.
* A `policy_hmac_misconfig` operational event is recorded in
  `enforcement_events` (Postgres + Mongo).
* In release **N+2** (two releases after Phase 0.1) the gateway will `SystemExit`
  at startup if the key is unset — silent degraded mode is removed.

**Action:**

```bash
# generate once, store in secrets manager
python -c "import secrets; print(secrets.token_urlsafe(48))"

# inject into both .env files (or your secret store of choice)
echo 'POLICY_SIGNING_KEY=<paste-output-here>' >> control/.env
echo 'POLICY_SIGNING_KEY=<paste-output-here>' >> gateway/.env

# verify gateway picks it up
docker compose up -d --build gateway
curl -fsS http://localhost:8088/health   # must be 200, not 503
```

The control plane and the gateway **must agree on the value** — they sign and
verify against the same secret.

---

## ⚠️ BREAKING CHANGE — `tier2_strict` default flips to True

**What changed:** `FirewallConfig.tier2_strict` is now a per-org tri-state with
default `True`. Pre-Phase-0 deployments behaved as `False` (fail-open) globally.

**Why this is a breaking change even though the happy path is unchanged:**

The Bedrock Tier-2 circuit breaker only OPENs when the Bedrock backend itself
is failing (≥50% failure rate over a 60 s window). When it OPENs:

* Pre-upgrade behavior: requests pass with Tier-1 result only (fail-open).
* Post-upgrade behavior (`tier2_strict=True`): requests get **HTTP 451** +
  `Retry-After` header (fail-closed).

The first Bedrock incident after upgrade will surface this change to end-users
and your on-call rotation. **Decide and document the org-level posture before
upgrading**, not after the page fires.

**Action — pick one per org:**

* **Default (recommended, fail-closed):** do nothing. Existing rows pick up
  `tier2_strict=True` via the migration default.
* **Fail-open (preserve pre-Phase-0 behavior):** update each affected
  `FirewallConfig` before deploying new gateway code:
  ```python
  # in `python manage.py shell`
  from policy.models import FirewallConfig
  FirewallConfig.objects.filter(organization__slug="acme").update(tier2_strict=False)
  ```

A `tier2_breaker_state_change` event is emitted on every CLOSED ↔ OPEN
↔ HALF_OPEN transition; a `tier2_degraded_pass` event is emitted whenever
a request passes Tier-1 only because the breaker is OPEN. Both show up in
`enforcement_events` with the new `event_class` column.

---

## Migration walkthrough

Both migrations are forward-only schema-safe additions. Add-column with default
is constant-time on Postgres 11+; the new composite index builds online but
will hold a brief metadata lock at the end.

```bash
# from control plane
python manage.py migrate core 0021_firewallconfig_tier2_per_org
python manage.py migrate policy 0022_enforcementevent_event_class

# verify in psql
\d+ enforcement_events
# expect new column:   event_class | character varying(64)
# expect new index:    ev_org_evclass_ts_idx
```

Mongo telemetry creates its own composite index lazily on the first
gateway-write per process (`ev_org_evclass_ts` on
`{org_slug:1, event_class:1, ts:-1}`). No manual Mongo step required.

**Order matters:** run migrations **before** deploying new gateway code.
Old gateway code does not write the new `event_class` column, so legacy rows
are backfilled by the migration default. New gateway code expects the column
to exist and the index to be present.

---

## Rollback

> **CRITICAL:** roll back **code first**, then schema, then env vars. The
> reverse order will crash the gateway on every request — the gateway at
> Phase 0.1 reads `tier2_strict` and `tier2_enabled` columns added by core
> migration `0021`, and the EnforcementEvent writes assume the
> `event_class` column exists.

```bash
# 1. roll gateway + control code back to N-1 (the pre-Phase-0 tag)
git checkout <pre-phase-0-tag>
docker compose up -d --build gateway control

# 2. reverse migrations (now safe — N-1 code does not read these columns)
python manage.py migrate policy 0021_vector_provider_choices_and_attack_vault
python manage.py migrate core 0020_<previous_migration_name>

# 3. only now remove the env var (gateway at N-1 doesn't require it)
sed -i '' '/^POLICY_SIGNING_KEY=/d' control/.env gateway/.env
docker compose up -d gateway control
```

If you remove `POLICY_SIGNING_KEY` first while Phase-0.1 gateway code is still
running, every request returns 503 from `/health` until you also roll the code
back.

---

## New operational event classes

The Phase-0 release introduces a discriminator column, `event_class`, on every
operational event recorded to `enforcement_events` (Postgres) and the
`aiguardx_telemetry.enforcement_events` collection (Mongo). Canonical values
are defined in
`gateway/ai_mesh_gateway/telemetry_ops.py:KNOWN_EVENT_CLASSES`:

| event_class                  | Emitted when |
|------------------------------|--------------|
| `enforcement`                | Pre-existing block/redact/monitor decisions (default for backfill) |
| `policy_hmac_failure`        | A signed policy bundle failed HMAC verification |
| `policy_hmac_misconfig`      | `POLICY_SIGNING_KEY` missing or empty at startup/sync |
| `tier2_breaker_state_change` | Bedrock Tier-2 circuit-breaker transitioned CLOSED ↔ OPEN ↔ HALF_OPEN |
| `tier2_degraded_pass`        | Request passed Tier-1 only because the breaker was OPEN (fail-open orgs) |

**Sample Mongo query** — count HMAC failures per org in the last 24 h:

```javascript
db.enforcement_events.aggregate([
  { $match: {
      event_class: "policy_hmac_failure",
      ts: { $gte: new Date(Date.now() - 24*60*60*1000) }
  }},
  { $group: { _id: "$org_slug", n: { $sum: 1 } } },
  { $sort: { n: -1 } }
])
```

Postgres equivalent (uses the new `ev_org_evclass_ts_idx` composite index):

```sql
SELECT organization_id, COUNT(*) AS n
FROM enforcement_events
WHERE event_class = 'policy_hmac_failure'
  AND created_at >= NOW() - INTERVAL '24 hours'
GROUP BY organization_id
ORDER BY n DESC;
```
