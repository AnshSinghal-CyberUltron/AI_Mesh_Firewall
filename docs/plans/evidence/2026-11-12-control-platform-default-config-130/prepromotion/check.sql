-- #139 pre-promotion check (run read-only on each environment BEFORE promoting #139 to staging/main).
-- Promotion gate: issue #155. Written by Reviewer B (#139 review 5871024207); the author added this header,
-- the P1 note on the 409 and, in check_redis.py, Reviewer A's stale-key check (#139 review 5870996702, S1).
--
-- Run (both read-only):
--   PGOPTIONS='-c default_transaction_read_only=on' psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f check.sql
--   cd control && python manage.py shell < ../docs/plans/evidence/2026-11-12-control-platform-default-config-130/prepromotion/check_redis.py
--
-- Limits (Reviewer A, S1):
--   * Writes before #139 were never audited, so these queries can only use updated_by / updated_at.
--   * updated_by is overwritten by the next write: a moved row that its new org has since edited is not flagged.
--   * Deduplicating is a human choice. Keep the NULL row last written by an org-less platform operator, or never
--     written (updated_by NULL); NOT the newest, which is usually the re-homed row. Repair from a shell as an
--     org-less operator and write an AuditLog row for it. A partial unique index follows the repair (#155).
--   * The real Organization with slug 'default' shares the Redis key firewall:config:default (P3; issue #152).
-- P1. Platform-default rows (organization IS NULL). Pass: at most 1 row, last written by nobody or by an
--     org-less platform operator. More than 1 row => the operator PUT answers 409 naming the ids (500 before
--     #139's review fixes), every platform_default() read logs an ERROR, and GET shows the lowest id while
--     Redis serves whichever row was saved/reconciled last.
SELECT fc.id, fc.updated_at, fc.updated_by_id, u.username, u.is_staff, up.is_platform_operator,
       up.organization_id AS updater_org_id, fc.firewall_enabled, fc.enforcement_mode, fc.retention_days
FROM core_firewallconfig fc
LEFT JOIN auth_user u ON u.id = fc.updated_by_id
LEFT JOIN auth_api_userprofile up ON up.user_id = fc.updated_by_id
WHERE fc.organization_id IS NULL
ORDER BY fc.id;

-- P2. Suspect rows. Pass: 0 rows.
--   (a) a NULL row last written by anyone but an org-less platform operator (org-less write, or a re-homed org row);
--   (b) an org row last written by a user of ANOTHER org (moved there through the writable `organization` field).
SELECT fc.id, fc.organization_id, o.slug, fc.updated_by_id, u.username, up.organization_id AS updater_org_id,
       (COALESCE(u.is_staff, false) AND COALESCE(up.is_platform_operator, false)) AS updater_is_operator, fc.updated_at
FROM core_firewallconfig fc
LEFT JOIN auth_api_organization o ON o.id = fc.organization_id
LEFT JOIN auth_user u ON u.id = fc.updated_by_id
LEFT JOIN auth_api_userprofile up ON up.user_id = fc.updated_by_id
WHERE fc.updated_by_id IS NOT NULL
  AND (
        (fc.organization_id IS NULL
         AND NOT (COALESCE(u.is_staff, false) AND COALESCE(up.is_platform_operator, false) AND up.organization_id IS NULL))
     OR (fc.organization_id IS NOT NULL AND up.organization_id IS DISTINCT FROM fc.organization_id)
      )
ORDER BY fc.id;

-- P3. The real Organization with slug 'default' (auth migration 0005). Its FirewallConfig row publishes to the SAME
--     Redis key as the platform default (firewall:config:default). Report it; the owner decides (see review finding B1).
SELECT o.id, o.slug, o.is_active, fc.id AS config_id, fc.firewall_enabled, fc.updated_by_id, fc.updated_at,
       (SELECT count(*) FROM auth_api_userprofile p WHERE p.organization_id = o.id) AS members
FROM auth_api_organization o
LEFT JOIN core_firewallconfig fc ON fc.organization_id = o.id
WHERE o.slug = 'default';

-- P4. Org-NULL kill switches: invisible and undeletable through the API. Pass: 0 rows (or each one explained).
SELECT id, model_name, api_key_prefix, is_active, action, activated_by_id, created_at
FROM core_killswitch WHERE organization_id IS NULL ORDER BY id;

-- P5. Platform-default audit trail present (after promotion): rows exist for every operator write; none purged.
SELECT count(*) AS platform_default_audit_rows, min(created_at), max(created_at)
FROM core_auditlog WHERE action = 'platform_default_config_updated';
