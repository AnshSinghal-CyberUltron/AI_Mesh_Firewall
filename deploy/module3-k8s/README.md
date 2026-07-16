# Module 3 Phase 2 — Kind / Helm Zero-Trust stack

Spreadsheets **Phase 2 (3.2)** delivered as an in-repo Kind cluster + Helm chart.

## Components

| Component | Role |
|-----------|------|
| NetworkPolicy / Cilium | Default: K8s NetworkPolicy; optional Cilium (`KIND_USE_CILIUM=1`) — only `role=ai-model` → vector-db :8443 |
| Envoy sidecars | mTLS on vector-db :8443; AI model egress via Envoy |
| Localhost gauntlet | Vector DB HTTP binds `127.0.0.1:8000` only |
| Mesh agent DaemonSet | Heartbeats + unauthorized probe → Module 3 ingest / SOC |
| River worker | Redis queue → embedding inspect → quarantine ingest |
| Redis | In-cluster queue for River |
| OPA + authz-shim | Rego token quotas; Envoy ext_authz kill-switch |
| state-sync | Control quota snapshot → OPA data |
| llm-edge | Demo AI API edge (Envoy :8080 → stub) |

## Prerequisites

- Docker Desktop
- [kind](https://kind.sigs.k8s.io/), kubectl, helm, openssl
- ZeroShield compose up (`control` on `:8100`, workers)
- `AGENT_API_KEY` set (global key + `ORG_SLUG`, or OrganizationAgentKey)

## Quick start

```bash
export AGENT_API_KEY=...
export ORG_SLUG=zeroshield
export CONTROL_URL=http://host.docker.internal:8100
bash scripts/module3_kind_up.sh
```

Tear down:

```bash
bash scripts/module3_kind_down.sh
```

E2E:

```bash
export AGENT_API_KEY=...
python scripts/module3_phase2_e2e.py
python scripts/module3_phase3_seed_quotas.py
python scripts/module3_phase3_e2e.py
```

UI: `/infrastructure/k8s-firewall` and `/infrastructure/api-governance`.  
Phase 3 runbook: [docs/MODULE3_PHASE3_API_GOVERNANCE.md](../../docs/MODULE3_PHASE3_API_GOVERNANCE.md).

Full runbook: [docs/MODULE3_PHASE2_K8S.md](../../docs/MODULE3_PHASE2_K8S.md)
