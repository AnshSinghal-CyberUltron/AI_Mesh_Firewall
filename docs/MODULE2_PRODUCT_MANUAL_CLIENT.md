# Module 2 Product Manual (Client)

## 1) What Module 2 Is

Module 2 is the SOC operations workspace of AI Mesh Firewall.

It helps security and operations teams answer four business questions:

1. Are attacks or misuse increasing right now?
2. Which API keys, models, tools, or data paths are highest risk?
3. Which incidents need immediate action?
4. What containment action should be taken now?

Module 2 is part of the same platform as Module 1, but its focus is analyst visibility and response.

## 2) Who Uses Module 2

- SOC analyst: monitors, triages, investigates, resolves/escalates
- Security lead/manager: watches trends and escalation load
- Platform owner: validates policy impact and containment posture
- Compliance reviewer: inspects incident evidence and response trail

## 3) Core Screens and Outcomes

### Dashboard

Business outcome:
- fast posture snapshot across all lanes (chat, RAG, vector, MCP)

Key decisions enabled:
- Is pressure increasing?
- Do we need to drill down into UEBA, threat intel, or incidents?

### UEBA API Keys

Business outcome:
- identify risky keys and abnormal usage behavior

Key decisions enabled:
- disable key now?
- activate containment (kill switch)?
- escalate for deeper investigation?

### Model/RAG Exposure

Business outcome:
- understand where model and retrieval paths are being stressed or abused

Key decisions enabled:
- adjust model usage policy?
- focus on specific RAG stages or vector collections?

### MCP Risk

Business outcome:
- visibility into risky tool-call behavior and server usage

Key decisions enabled:
- investigate tool abuse vectors?
- tighten controls around MCP routes?

### Threat Intel

Business outcome:
- manage IOC patterns and monitor match activity

Key decisions enabled:
- add/remove indicators?
- sync IOC changes to active gateway runtime?

### Incident Queue + Incident Detail

Business outcome:
- manage formal security cases end-to-end

Key decisions enabled:
- escalate or resolve?
- validate evidence and timeline before closure?

## 4) KPI Meaning (Client View)

### Event and protection KPIs

- **Total Events**: all observed enforcement events in selected window.
- **Blocked**: requests fully denied by policy/control.
- **Redacted**: requests allowed after sensitive content masking.
- **Monitored**: allowed traffic flagged for analyst review.
- **Rerouted**: requests sent to a different model route than initially requested.

### Incident KPIs

- **Active Queue**: open + investigating + escalated cases.
- **Open / Escalated / Resolved**: workflow status distribution.
- **Critical / High**: severe active incident concentration.

### UEBA/containment KPIs

- **High Behavioral Risk Keys**: keys with elevated risk score.
- **Disabled Keys**: keys currently disabled at gateway level.
- **Active Kill Switches**: live containment switches in effect.

## 5) Standard Analyst Workflow

1. Open Dashboard and check trend + open incidents.
2. Move to Incident Queue and filter active/high-severity work.
3. Open Incident Detail for chain-of-custody and evidence.
4. Decide action:
   - escalate (needs higher-tier review)
   - resolve (investigation complete)
5. If key behavior is suspicious, open UEBA and apply containment if needed.
6. If pattern-driven abuse is seen, update Threat Intel and sync.

## 6) Roles and Access Expectations

- Read access is available to authenticated users in org scope.
- Sensitive write operations (example: threat intel edits) require privileged role access.
- Incident and telemetry views are expected to remain organization-scoped.

## 7) What “Production Ready” Means Here

For Module 2 release readiness, clients should expect:

- reliable KPI/chart rendering with live refresh behavior
- consistent incident actions and queue updates
- validated security controls for access and metadata exposure
- documented runbook and test evidence

Reference:
- [Module 2 Release Readiness Checklist](./MODULE2_RELEASE_READINESS_CHECKLIST.md)

## 8) Known Operational Limits

- Real-time behavior can fall back to activity-based refresh when websocket is unavailable.
- Some checks still require manual QA (stress-path UX/accessibility) before final sign-off.

## 9) Success Criteria for Client Acceptance

- SOC users can monitor, triage, and close incidents without engineering intervention.
- Containment actions are visible and auditable.
- KPI semantics are understandable by analysts and managers.
- Manual and automated release gates are complete for the target release window.

## 10) Related Module 2 Docs

- [Module 2 Docs Index](./MODULE2_DOCS_INDEX.md)
- [Module 2 Architecture](./MODULE2_ARCHITECTURE.md)
- [Module 2 Operations Runbook](./MODULE2_OPERATIONS_RUNBOOK.md)
- [Module 2 Technical Manual (Developers)](./MODULE2_TECHNICAL_MANUAL_DEVELOPERS.md)

