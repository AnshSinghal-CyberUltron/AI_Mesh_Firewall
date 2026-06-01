# Triage synthesis — firewall-1-5 flicker & UX (2026-05-28)

## Initial decision (D1–D7)

| ID | Decision | Reasoning |
|----|----------|-----------|
| D1 | `modelListSignature` + stable `onModelsChanged` | Breaks feedback loop: every fetch was bumping `refreshToken` → full governance reload |
| D2 | Silent panel refresh after first hydrate | ui-ux-pro-max `progressive-loading`: keep content visible, update in place |
| D3 | `showGatewayCatalog={false}` on 1.5 | User asked to remove pre-configured gateway catalog; cuts extra `/v1/models` fetch |
| D4 | `showProviderForm={false}` on 1.5 | Provider/API key only in Add modal (progressive disclosure) |
| D5 | Background `useFirewallData` polling | 15s poll set `loading=true` → hero spinner flicker on entire module page |
| D6 | `RoutingGovernancePanel` shares `refreshToken` | Read-only default model stays in sync after governance save / model add |
| D7 | Signature includes `api_key_set` | T5: credential-only edits still notify dependent panels |

## Triage positions (5 agents)

- **T1 (approve):** Endorses D1–D7; risk: empty allowlist + isolation semantics unchanged.
- **T2 (reject token):** Prefers shared React Context / SWR — valid long-term; deferred to avoid large refactor this pass.
- **T3 (minimal):** Would only remove `onModelsChanged` from fetch — insufficient for hero flicker and gateway removal ask.
- **T4 (UX):** Wants inline skeleton on silent refresh — adopted partial: keep panel mounted, no full replacement loader.
- **T5 (security):** Removing gateway catalog is UI-only; ops use gateway directly; signature must include credential fields — adopted D7.

## Final plan (post-triage)

Implement D1–D7; defer shared config Context to follow-up. Reject T3-only scope as under-fixes user report.
