# Post-diff triage — Firewall 1.5 governance

## Scope reviewed

- `firewall_model_governance.py` — `sanitize_allowlist_for_org`
- `sanitize_firewall_allowlists` management command
- `0025_firewallconfig_governance_defaults` migration
- `ModelGovernanceFields.jsx`, `ModelGovernancePanel.jsx`, `firewall-submodules.jsx`, `RoutingGovernancePanel.jsx`, `AIMeshFirewallConfig.jsx`

## Verdicts (5 perspectives)

| Agent lens | Verdict | Finding |
|------------|---------|---------|
| Pro-migration | **APPROVE** | Data fix at source; migration + command; idempotent |
| Anti-migration | **APPROVE** | Migration uses historical models correctly (org_id filter); no GET-side writes |
| Security | **APPROVE** | Intersection-only sanitize; guard models excluded; PUT validation unchanged |
| UX | **APPROVE** | Single default-model editor on 1.5; routing panel read-only; banner removed |
| Ops | **APPROVE** | Rebuild control required; tests pass in Docker |

## Residual risks (accepted)

1. Empty allowlist after sanitize when only legacy names existed — operators must re-select models in UI (intentional).
2. Live matrix 502s when API key org ≠ model org — documented in verify artifact; out of scope.

## Final decision

**SHIP** — all D1–D7 objectives met for this task.
