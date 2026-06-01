# Triage verdict — Firewall 1.5 governance (5-agent synthesis)

## Summary

All seven decisions **APPROVED** with minor modifications from triage.

| ID | Verdict | Notes |
|----|---------|-------|
| D1 | **APPROVE** | Management command + `sanitize_allowlist_for_org()` helper; idempotent, `--dry-run`, `--org` |
| D1-mod | **MODIFY** | Add migration `0025` RunPython calling same helper (addresses anti-migration deploy automation) |
| D2 | **APPROVE** | Remove stale banner and `staleModels` prop entirely |
| D3 | **APPROVE** | `allowed_models=""`, `default_model=""` on model fields |
| D4 | **APPROVE** | `ModelGovernancePanel` on `firewall-1-5` with `refreshToken` from `ModelConnectionPanel` |
| D5 | **APPROVE** | `RoutingGovernancePanel` no longer PUTs `default_model`; read-only display points to governance panel |
| D6 | **APPROVE** | Keep `governance_stale_models` API field for tests; not rendered in UI |
| D7 | **APPROVE** | Gateway pytest + live matrix + verify artifact |

## Agent positions (synthesized)

- **T1 Pro-migration:** Command fixes root cause; edge cases handled (empty connected → clear allowlist).
- **T2 Anti-migration:** Serializer-only filtering hides debt; **MODIFY** to add RunPython migration at deploy.
- **T3 Security:** Stripping `zeroshield-guard-120b` from allowlist is correct (not user-managed); gateway uses connected configs; no bypass.
- **T4 UX:** Single owner for default model on 1.5; routing panel read-only avoids conflicting saves.
- **T5 Ops:** `docker compose build control` required; migration runs sanitize on deploy; command for manual re-run.

## Execution approved

Proceed with implementation as revised above.
