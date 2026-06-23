<div align="center">

# 🛡️ _ZeroShield — AI Mesh Firewall_

### _End-to-End AI Firewalling, Model Governance & SOC Operations (Module 1 + Module 2)_
<img width="365" height="100" alt="image" src="https://github.com/user-attachments/assets/8faee856-708c-4574-b10a-cb5eced85a6c" />

_[AI Mesh Firewall](https://aisecshield.zeroshield.ai/?tab=firewall-1-4), a core module of [ZeroShield](https://zeroshield.ai)_

### Tech Stack

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)
![Gateway](https://img.shields.io/badge/ZeroShield-Gateway-009688?style=flat-square)
![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)
![Django](https://img.shields.io/badge/Django-Control%20Plane-0C4B33?style=flat-square&logo=django&logoColor=white)
![Router](https://img.shields.io/badge/ZeroShield-Model%20Router-7c3aed?style=flat-square)
![Policy](https://img.shields.io/badge/ZeroShield-Policy%20Engine-F5A623?style=flat-square)
![SOC](https://img.shields.io/badge/ZeroShield-SOC%20Analytics-ef4444?style=flat-square)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)

## 🚀 [Explore ZeroShield Solutions](https://zeroshield.ai)

</div>

---

## Table of Contents

* [About the AI Mesh Firewall](#about-the-ai-mesh-firewall)
* [System Architecture](#system-architecture)
  * [Architecture Diagram](#architecture-diagram)
  * [Request + SOC Flow](#request--soc-flow)
* [Platform Modules at a Glance](#platform-modules-at-a-glance)
  * [Module 1 — Runtime Firewall (1.1 to 1.7)](#module-1--runtime-firewall-11-to-17)
  * [Module 2 — SOC Intelligence & Response (2.1 to 2.7)](#module-2--soc-intelligence--response-21-to-27)
* [Deploy → Monitor → Enforce Lifecycle](#deploy--monitor--enforce-lifecycle)
* [Quick Start (Local)](#quick-start-local)
* [Module Documentation](#module-documentation)
* [Governance & Compliance](#governance--compliance)
* [Target Users & Real-World Scenarios](#target-users--real-world-scenarios)
* [Use Cases](#use-cases)
* [Platform Screenshots & Demo](#platform-screenshots--demo)
* [Support](#support)

---

## About the AI Mesh Firewall

ZeroShield's **AI Mesh Firewall** is a **server-side security, governance, and operations platform** for enterprise AI systems.

It secures the full runtime path (Module 1) and provides SOC-grade visibility and response workflows (Module 2):

- **Module 1** controls live traffic before and after model execution (ingress, RAG, vector, MCP, routing, kill-switch, output guardrails)
- **Module 2** provides dashboarding, UEBA, threat intel operations, incident triage, and analyst response workflows

No endpoint agent is required. Existing AI apps route through the gateway, and governance is controlled centrally from the platform UI.

### Why It Exists

| Problem | What ZeroShield Does |
|---|---|
| Prompt injection reaches models undetected | Inline detection and enforcement before model execution |
| Multi-tenant RAG leaks cross-tenant data | Vector and retrieval controls with namespace isolation |
| No unified governance across model providers | Single gateway endpoint + policy-driven model routing |
| No immediate response mechanism during incidents | Kill-switch and reroute controls at request time |
| Security team lacks operational visibility | SOC dashboards, UEBA analytics, threat intel, incidents |
| No compliance-grade traceability | Full audit trail with policy/action context |

---

## System Architecture

All AI clients call the AI Mesh Firewall gateway. The control plane manages policy, keys, and governance state, while asynchronous workers and analytics power monitoring and incident workflows.

### Architecture Diagram

```mermaid
flowchart LR
  subgraph clients [AI Clients]
    chat[Chatbots]
    agents[Agents]
    backend[Backend APIs]
    ragapps[RAG Apps]
  end

  subgraph dataplane [Module 1 Runtime Firewall]
    gateway[Gateway + Policy Engine]
    scanners[Input/Output Security Engines]
  end

  subgraph control [Control Plane]
    controlApi[Control API]
    policy[Policy + Config]
    module2[Module 2 Analytics APIs]
    audit[(Audit/Event Store)]
  end

  subgraph async [Async Plane]
    workers[Celery Workers]
    redis[(Redis)]
    rabbit[(RabbitMQ)]
    postgres[(Postgres)]
  end

  subgraph downstream [AI + Data Providers]
    llm[LLM Providers]
    vectordb[Vector DBs]
    mcp[MCP Servers]
  end

  chat --> gateway
  agents --> gateway
  backend --> gateway
  ragapps --> gateway
  gateway --> scanners
  gateway --> llm
  gateway --> vectordb
  gateway --> mcp
  controlApi --> policy
  controlApi --> module2
  module2 --> audit
  workers --> redis
  workers --> rabbit
  workers --> postgres
  gateway --> controlApi
  controlApi --> workers
```

### Request + SOC Flow

```mermaid
flowchart TD
  req[Client Request] --> ingress[1. Ingress Auth + Policy]
  ingress --> enforce[2. Runtime Enforcement]
  enforce --> route[3. Model/Tool Routing]
  route --> out[4. Output Guardrails]
  out --> resp[Response]
  out --> events[Enforcement Events]
  events --> analytics[Module 2 Analytics]
  analytics --> soc[Dashboard / UEBA / Incidents]
  soc --> actions[Containment + Incident Actions]
  actions --> ingress
```

---

## Platform Modules at a Glance

### Module 1 — Runtime Firewall (1.1 to 1.7)

| Module | Capability |
|---|---|
| **1.1** | AI Gateway & traffic ingress |
| **1.2** | Pipeline-aware RAG firewall |
| **1.3** | Vector DB firewall |
| **1.4** | Context assembly & MCP guardrails |
| **1.5** | Multi-model governance & routing |
| **1.6** | Inline model isolation & kill-switch |
| **1.7** | Generator-level output guardrails |

### Module 2 — SOC Intelligence & Response (2.1 to 2.7)

| Module | Capability |
|---|---|
| **2.1** | Unified SOC dashboard and KPI telemetry |
| **2.2** | UEBA for API key behavioral risk and containment |
| **2.3** | Model and gateway exposure analytics |
| **2.4** | RAG lane and pipeline risk observability |
| **2.5** | MCP risk and tool governance analytics |
| **2.6** | Incident queue, detail, escalation, and resolution workflows |
| **2.7** | Threat intelligence operations and IOC-driven response |

---

## Deploy → Monitor → Enforce Lifecycle

```mermaid
graph LR
    D[Deploy] --> M[Monitor]
    M --> E[Enforce]
    E --> R[Respond]
    R --> M
```

- **Deploy:** onboard models, vector stores, API keys, and baseline policy.
- **Monitor:** observe traffic and risk posture across runtime and SOC layers.
- **Enforce:** apply block/redact/reroute controls and containment actions.
- **Respond:** investigate incidents, escalate/resolve, update threat intel.

---

## Quick Start (Local)

```bash
cp .env.sample .env
docker compose up -d postgres redis rabbitmq
docker compose up -d control workers gateway frontend
```

| Service | URL |
|---------|-----|
| UI | http://127.0.0.1:8180 |
| Control API | http://127.0.0.1:8100 |
| Gateway (direct) | http://127.0.0.1:8300 |
| RabbitMQ UI | http://127.0.0.1:15772 |

**Login (dev):** `admin@zeroshield.io` / `Adm1n!Pass#2024`

**Optional checks:**

```bash
node scripts/playwright_nine_modules.mjs
```

```powershell
cd frontend
npm run test:module2
```

```powershell
.\scripts\run-control-tests-postgres.ps1 module2.tests
```

---

## Module Documentation

### Module 1 (Runtime Firewall)

- [Architecture](docs/ARCHITECTURE.md)
- [Repository layout](docs/REPOSITORY_LAYOUT.md)
- [100k concurrency deployment](docs/DEPLOYMENT_100K.md)
- [Migration from monorepo](docs/MIGRATION_FROM_AIGUARDX.md)
- [Contracts](docs/contracts/)

### Module 2 (SOC Intelligence & Response)

- [Module 2 Docs Index](docs/MODULE2_DOCS_INDEX.md)
- [Module 2 Architecture](docs/MODULE2_ARCHITECTURE.md)
- [Module 2 GitHub Guide](docs/MODULE2_GITHUB_GUIDE.md)
- [Module 2 Product Manual (Client)](docs/MODULE2_PRODUCT_MANUAL_CLIENT.md)
- [Module 2 Technical Manual (Developers)](docs/MODULE2_TECHNICAL_MANUAL_DEVELOPERS.md)
- [Module 2 Operations Runbook](docs/MODULE2_OPERATIONS_RUNBOOK.md)
- [Module 2 Release Readiness Checklist](docs/MODULE2_RELEASE_READINESS_CHECKLIST.md)

---

## Governance & Compliance

| Area | Control |
|---|---|
| Identity & Access | API key auth, org scoping, role-gated write operations |
| Input Security | Prompt injection and sensitive-data controls |
| Retrieval Security | Namespace isolation and guarded retrieval policies |
| Model Governance | Risk-aware routing + kill-switch + fallback behavior |
| Output Security | Stream-time redaction/blocking controls |
| SOC Response | Incident workflows, threat intel operations, analyst actions |
| Auditability | Event telemetry and policy-linked audit trail |

---

## Target Users & Real-World Scenarios

| Persona | Primary Outcome |
|---|---|
| CISO / Security Teams | Centralized AI traffic control and incident response |
| Platform / DevOps Teams | Operable, policy-driven AI runtime with clear runbooks |
| Compliance Teams | Evidence-backed governance and audit readiness |
| AI/ML Teams | Safe model usage without slowing delivery |
| Product/SOC Teams | Visibility from live events to incident closure |

---

## Use Cases

- Prevent prompt injection and jailbreaks in production AI traffic
- Enforce PII/PHI and policy controls for regulated workloads
- Govern multi-tenant RAG retrieval and context assembly
- Route sensitive workloads to approved/private model lanes
- Trigger kill-switch/reroute actions for compromised providers
- Detect key misuse and high-risk patterns with UEBA
- Triage, escalate, and resolve incidents with analyst evidence
- Drive IOC-based response workflows from threat intel operations

---

## Platform Screenshots & Demo

| | |
|---|---|
| 📸 **Dashboard Overview** | <img width="2880" height="1800" alt="image" src="https://github.com/user-attachments/assets/b6cb0291-f2ae-4282-9b76-d3bed3df8ed6" /> |
| 📸 **Policy Builder** | <img width="2880" height="1800" alt="image" src="https://github.com/user-attachments/assets/2598c4c9-beac-4c13-8080-d96a6e8b1217" /> |
| 📸 **Vector Collection Policies** | <img width="2880" height="1800" alt="image" src="https://github.com/user-attachments/assets/572941cb-f3e6-4489-82ce-3bc86ee8099e" /> |
| 📸 **MCP Scanner** | <img width="2880" height="1800" alt="image" src="https://github.com/user-attachments/assets/73f741c9-439e-4b91-bf31-7626c7e30cf5" /> |
| 📸 **Kill Switch Management** | <img width="2880" height="1800" alt="image" src="https://github.com/user-attachments/assets/df91fade-509c-445d-9b79-19d4d13ed1d7" /> |
| 📸 **SOC / Incident Operations (Module 2)** | <img width="2880" height="1800" alt="image" src="https://github.com/user-attachments/assets/b9d9e9b8-8ece-4eb5-ad17-bfee552dcb69" /> |

🎬 **Demo Video**

https://github.com/user-attachments/assets/bc2f38f6-7123-4b56-8b79-fa0621fa4ab2

---

## Support

* 📧 **Contact**: [vartul@zeroshield.ai](mailto:vartul@zeroshield.ai)
* 📧 **Support Queries**: [support@zeroshield.ai](mailto:support@zeroshield.ai)

For enterprise deployments, integrations, or custom requirements, please reach out via the addresses above.

---

> **Value Proposition:** ZeroShield AI Mesh Firewall delivers centralized runtime protection (Module 1) plus SOC intelligence and response workflows (Module 2) in one platform, enabling secure, governable, and production-ready AI operations.

---

All rights reserved. This software and its documentation are the intellectual property of [ZeroShield](https://zeroshield.ai).
