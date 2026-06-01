---
name: Module 1 Demo Script
overview: A detailed client presentation script for Module 1 (AI Mesh Firewall) that maps each frontend screen and UI element to talking points, ties value to retail/e-commerce customers (e.g., FirstCry, Amazon, Lenskart), and connects to the 3 Ps (People, Process, Product).
todos: []
isProject: false
---

# Module 1 AI Mesh Firewall – Detailed Client Demo Script

This plan gives you a **presentation script** you can follow while demoing the Module 1 frontend. It assumes your audience is a **customer** (e.g., FirstCry, Amazon, Lenskart, or similar) and frames benefits in terms of **People, Process, and Product**.

---

## Navigation & Entry Point

- **Where to start:** Sidebar → **AI Mesh Firewall** (Module 1). Click it to land on the **AI Mesh Firewall Overview**.
- **Flow:** Overview → click any sub-module card (e.g. "1.1 AI Gateway") to drill in → use **View All Results & Detailed Logs** for investigation-level data → **Firewall Configuration (Inputs)** for policy and settings.

---

## Part 1: Opening – Why Module 1 Matters for You (3 Ps)

**Say:**  
*"Module 1 is our AI Mesh Firewall. Think of it like a Kubernetes service mesh, but for every AI request and response. For a company like yours—whether you run chatbots, search, recommendations, or internal agents—all AI traffic should be controlled, inspected, and governed in real time. That’s what we deliver here."*

**3 Ps hook (customize with customer name):**

- **People:** *"Your teams—support, product, data—need to use AI safely. We give you one place to see who’s calling which model, from which app, and whether anything risky got through. So People use AI with guardrails, not blind."*
- **Process:** *"Your processes—RAG pipelines, retrieval, context assembly, model routing—all flow through this firewall. We enforce at each stage: query, retriever, ranker, generator. So Process stays consistent and auditable."*
- **Product:** *"Your product—the experience you ship to customers—stays safe. No PII in answers, no jailbreaks, no model meltdowns. Product quality and trust are protected."*

---

## Part 2: AI Mesh Firewall Overview Page

**Route:** Sidebar → **AI Mesh Firewall** (first level; no sub-item). You see [AIMeshFirewallOverview.jsx](frontend/src/pages/firewall/AIMeshFirewallOverview.jsx).

### 2.1 Page header and status

**Show:** The teal header with “Module 1 – AI Mesh Firewall” and the **ACTIVE** badge.

**Say:**  
*"This is the command center for Module 1. The green ACTIVE badge means the firewall is live and enforcing. The line below sums it up: we control AI traffic at every stage, from entry to output."*

### 2.2 Quick stats bar (4 numbers)

**Show:** Total Events (24h), Active Sub-Modules (7/7), Blocked, Critical.

**Say:**  
*"At a glance you see volume, how many requests we blocked, and how many were critical. For a retailer with millions of AI calls—recommendations, search, chatbots—this is your real-time health check."*

### 2.3 Global Traffic Overview

**Show:** The stats row (Total Events, Blocked, Redacted, Critical), the **Global Traffic Overview (24h)** area chart (Total vs Blocked), and the **Action Distribution** pie (Allowed / Blocked / Redacted).

**Say:**  
*"Here we show all firewall traffic in one place. The chart is total requests over time; the red line is what we blocked. The pie shows the split: mostly allowed, some blocked, some redacted. For compliance and ops, you can say exactly how many requests were blocked or redacted in the last 24 hours."*

### 2.4 Sub-module cards (1.1–1.7 + Config)

**Show:** The grid of 8 cards: 1.1 AI Gateway, 1.2 Pipeline-Aware RAG, 1.3 Vector DB, 1.4 Context Assembly & MCP, 1.5 Multi-Model Governance, 1.6 Model Isolation & Kill-Switch, 1.7 Output Guardrails, and **Firewall Configuration**.

**Say:**  
*"We break the firewall into seven sub-modules plus configuration. Each card shows events and blocked counts for that layer. Click any card to go deep. I’ll walk through a few that matter most for your use case."*

**Customer angle:**  
*"For you, 1.1 is your front door—all traffic. 1.2 is where we protect RAG—search and retrieval. 1.3 protects vector DBs. 1.4 is where we control what context goes to the model, including MCP. 1.5 is how we route between models. 1.6 is your emergency kill-switch. 1.7 is the last line—output guardrails."*

### 2.5 Module Activity Summary bar chart

**Show:** The bar chart (1.1 Gateway, 1.2 RAG, … 1.7 Guards) with Total Events and Blocked per module.

**Say:**  
*"This shows where volume and blocks happen across the pipeline. You can quickly see if most risk is at ingress, in RAG, or at output—so you know where to tune policies."*

### 2.6 OWASP Threat Coverage & Policy Analytics

**Show:** OWASP Stats Panel (LLM, MCP, Agentic families; detected/blocked) and Policy Analytics Panel.

**Say:**  
*"We map detections to OWASP categories—LLM, MCP, Agentic. You get coverage and block rates per family. Policy Analytics shows how your policies are performing over time. That’s what auditors and security teams want to see."*

---

## Part 3: Sub-Module Deep Dives (What to Show and Say)

Each sub-module uses [SubmoduleDetailPage.jsx](frontend/src/components/firewall/SubmoduleDetailPage.jsx): header, **AI Traffic Flow** (flow nodes), **4 metric cards**, **Request Volume** and **Enforcement Actions** charts, **Action Distribution** pie, **Recent Activity Preview** table, and **View All Results & Detailed Logs**. Optional: Latency, System Health, Module-Specific Analytics.

### 3.1 – 1.1 AI Gateway & Traffic Ingress

**Navigate:** Click card **1.1 AI Gateway & Traffic Ingress**.

**Flow diagram:** Client → Auth Layer → Rate Limiter → Gateway.

**Say:**  
*"Every AI request hits the gateway first. We authenticate the caller—user, service, or agent. We apply tenant isolation and token budgets. The rate limiter stops DDoS-style spikes. So only legitimate, within-budget traffic reaches your models. For a large e-commerce site, this is how you prevent one bad integration from taking down AI for everyone."*

**Panels on this page:**

- **Gateway API Keys:** *"Here we manage API keys for the gateway. Each key can have a name, project, allowed models, rate limit (tokens per minute), and expiry. Different apps or teams get different keys—so you control who can call what."* (Show create/list; mention token budgets and tenant isolation.)
- **Attack Simulator:** *"This is a live demo. We send prompts—including malicious ones like prompt injection, PII leakage, jailbreak—to your gateway and show whether they’re allowed, blocked, or flagged."* Run one safe and one attack scenario; point out BLOCKED vs ALLOWED. *"So you can prove to security that the firewall is actually blocking attacks."*

**Customer angle (People/Process):**  
*"People get controlled access via keys; process is standardized at ingress with auth and rate limits."*

---

### 3.2 – 1.2 Pipeline-Aware RAG Firewall

**Navigate:** Click card **1.2 Pipeline-Aware RAG Firewall**.

**Flow:** Query → Retriever → Ranker → Generator.

**Say:**  
*"We detect RAG vs non-RAG and enforce at each stage: query, retriever, ranker, generator. At query we do prompt injection and jailbreak detection, PII and credential checks, intent classification. We can block, rewrite, mask, or downgrade the model. So your RAG pipeline—search, recommendations, support—is protected stage by stage."*

**Panel:** **Policy Management** – *"Policies and rules live here. You define what to block or redact (keywords, patterns, severity) and the action—block, redact, monitor. This is the brain of the RAG firewall."*

**Customer angle (Process/Product):**  
*"Process is consistent across query → retrieval → generation; product stays safe from injected prompts and bad context."*

---

### 3.3 – 1.3 Vector DB Firewall

**Navigate:** Click card **1.3 Vector DB Firewall**.

**Flow:** Query → Vector DB → Documents.

**Say:**  
*"We protect your vector stores—Chroma, Pinecone, Milvus. We enforce collection-level access, namespace isolation, and embedding access control. We look for vector poisoning, sensitive document retrieval, cross-tenant leakage, and embedding anomalies. So your RAG data layer is as governed as the rest of the mesh."*

**Panel:** **Vector Policy Panel** – *"Here you configure which collections and namespaces are allowed, default allow/deny, max results per query, sensitive fields, and anomaly thresholds. So you decide what can be retrieved and by whom."*

**Customer angle (Product/Process):**  
*"Product: no sensitive docs leaking. Process: retrieval is audited and constrained."*

---

### 3.4 – 1.4 Context Assembly & MCP Guardrails

**Navigate:** Click card **1.4 Context Assembly & MCP**.

**Flow:** Context Fields → PII Redaction → Size Check → Final Context.

**Say:**  
*"Before the final context goes to the model, we minimize it and apply field-level redaction. We enforce MCP guardrails—what context each user or agent is allowed. We tag PII, IP, regulated data. This is where we prevent MCP-level data leakage."*

**Panels:** **MCP Scanner Panel** (scan MCP servers/tools, risk scores), **Device Management**, **Agent Download/Install/Status/Onboarding**. *"For agents and MCP, we scan what tools and context they use and score risk. You can onboard devices and agents from here and see their status."*

**Customer angle (People/Process):**  
*"People and agents get only the context they’re allowed; process keeps context minimal and compliant."*

---

### 3.5 – 1.5 Multi-Model Governance & AI Mesh Routing

**Navigate:** Click card **1.5 Multi-Model Governance**.

**Flow:** Request → Router → Model Pool → Execute.

**Say:**  
*"We support multiple models—OpenAI, Claude, Llama, Gemini. The router decides which model handles each request based on data sensitivity, compliance, cost, token budgets, latency SLAs, and risk. Models behave like services in a mesh: you can route high-risk or regulated traffic to a specific model and keep others for general use."*

**Customer angle (Process/Product):**  
*"Process: consistent routing rules. Product: right model for the right use case and compliance."*

---

### 3.6 – 1.6 Inline Model Isolation & Kill-Switch

**Navigate:** Click card **1.6 Model Isolation & Kill-Switch**.

**Flow:** Model Active → Risk Analysis → Threshold → Status.

**Say:**  
*"Each model can be isolated—separate credentials, rate limits, risk scoring. If something goes wrong, you have kill-switches: immediate disable or auto-reroute to a fallback model. So one bad model doesn’t take down the whole AI layer."*

**Panel:** **Kill-Switch Management** – *"Here we create and manage kill-switches. You pick the model, the action—block or reroute—and optionally a fallback model. You can activate or deactivate instantly. In a crisis, you flip the switch and traffic stops or goes to the fallback."*

**Customer angle (People/Process):**  
*"People (ops) get a clear emergency control; process stays under control even when a model misbehaves."*

---

### 3.7 – 1.7 Generator-Level Output Guardrails

**Navigate:** Click card **1.7 Output Guardrails**.

**Flow:** Raw Output → Content Filter → PII Detector → Final Output.

**Say:**  
*"After the model generates, we scan the output for PII, credentials, IP leakage, policy violations, and hallucination risk. We can block, redact, rewrite, or send to human review and log security incidents. So what goes back to the user or into your product is safe."*

**Customer angle (Product/People):**  
*"Product: no PII or bad content in responses. People: customers and support get safe answers."*

---

## Part 4: View All Results & Log Detail

**From any sub-module:** Click **View All Results & Detailed Logs**.

**Show:** [SubmoduleResultsPage](frontend/src/components/firewall/SubmoduleResultsPage.jsx) – same flow diagram, then a **large table** (e.g. Request ID, Timestamp, App, Identity, Type, Tokens, Decision, Latency for 1.1).

**Say:**  
*"This is investigation-level data. You see every request (or a large sample) with decision and metadata. Click a row or View Details to open the full log."*

**Log Detail:** *"We show the full event—request, response, detections, actions. Useful for debugging, compliance, and incident response."*

---

## Part 5: Firewall Configuration (Inputs)

**Navigate:** Sidebar → **AI Mesh Firewall** → **Inputs**, or click the **Firewall Configuration** card on the overview.

**Show:** [AIMeshFirewallConfig.jsx](frontend/src/pages/firewall/AIMeshFirewallConfig.jsx) – header, Save/Reset, then the **configuration sections**.

**Say:**  
*"This is where you set how the firewall behaves. Changes require Save and take effect across enforcement points. Let me walk the main sections."*

- **General Firewall Settings:** Firewall on/off, enforcement mode (Block / Monitor / Audit), log level. *"You can run in monitor-only first, then switch to block when you’re confident."*
- **Rate Limiting & Throttling:** Enable rate limit, requests per minute, burst. *"This is your DDoS-style protection for AI."*
- **Content Filtering & Detection:** Content filter, PII detection, toxicity threshold, blocked keywords. *"Core input/output safety."*
- **Model Governance & Routing:** Model isolation, allowed models, default model. *"You lock down which models can be used."*
- **Prompt Security & Injection Protection:** Jailbreak detection, semantic analysis (Tier-2 ML), prompt injection threshold. *"This is where we stop prompt injection and jailbreaks."*
- **Response Guardrails:** Response filtering, factuality check, max response tokens. *"Output safety and hallucination control."*
- **RAG & Vector DB:** RAG on/off, vector DB isolation, max RAG docs, relevance threshold. *"RAG and retrieval guardrails."*
- **Threat Intelligence:** Threat intel on/off, auto-block, threat score threshold. *"Integrate with threat feeds and auto-block."*
- **Audit Logging & Compliance:** Audit logging, retention days, frameworks (SOC2, ISO27001, HIPAA, GDPR, PCI-DSS, NIST). *"For compliance and audits."*
- **Alerting:** Alerting on/off, critical threshold, alert recipients. *"So security gets notified when something critical happens."*

**Bottom:** Model Connection, Database Connection, Bedrock Test, Log Viewer. *"You can plug in your models and vector DBs and test from here."*

**Customer angle (3 Ps):**  
*"People: who gets alerts and what they can change. Process: how we enforce and log. Product: what’s allowed in and out."*

---

## Part 6: Optional – Dashboard Widget

**Route:** Sidebar → **Dashboard / Home**.

**Show:** [AITrafficFirewallActivity.jsx](frontend/src/components/widgets/AITrafficFirewallActivity.jsx) – **AI Service Mesh Flow** (User/App → AI Gateway → RAG Pipeline → AI Mesh Firewall → Models).

**Say:**  
*"On the main dashboard we show the same idea: traffic flows from users and apps through the gateway and RAG pipeline into the central AI Mesh Firewall, then to models. So even from the home page, leadership sees that every AI call is protected."*

---

## Script Summary Table


| Section               | Where to go                 | What to show                                                                | 3 P link                 |
| --------------------- | --------------------------- | --------------------------------------------------------------------------- | ------------------------ |
| Intro & 3 Ps          | Verbal                      | People, Process, Product                                                    | All                      |
| Overview              | AI Mesh Firewall (overview) | Header, stats, global traffic, sub-module cards, activity bar, OWASP/Policy | All                      |
| 1.1 Gateway           | Card 1.1                    | Flow, metrics, Gateway Keys, Attack Simulator                               | People, Process          |
| 1.2 RAG Firewall      | Card 1.2                    | Flow, Policy Management                                                     | Process, Product         |
| 1.3 Vector DB         | Card 1.3                    | Flow, Vector Policy Panel                                                   | Product, Process         |
| 1.4 Context & MCP     | Card 1.4                    | Flow, MCP Scanner, devices/agents                                           | People, Process          |
| 1.5 Multi-Model       | Card 1.5                    | Flow, routing idea                                                          | Process, Product         |
| 1.6 Kill-Switch       | Card 1.6                    | Flow, Kill-Switch panel                                                     | People, Process          |
| 1.7 Output Guardrails | Card 1.7                    | Flow, output pipeline                                                       | Product, People          |
| Results & Logs        | View All Results (any 1.x)  | Table, Log Detail                                                           | Process                  |
| Config                | Inputs / Firewall Config    | All config sections, Save/Reset                                             | People, Process, Product |
| Dashboard             | Dashboard                   | AI Service Mesh Flow widget                                                 | All                      |


---

## Closing Line (Customer + 3 Ps)

**Say:**  
*"Module 1 is your single control plane for AI traffic. For **People**, you get visibility and access control. For **Process**, you get stage-by-stage enforcement and audit. For **Product**, you get safe, compliant AI in every channel—chatbots, search, recommendations, agents. We’re happy to go deeper on any sub-module or run a live attack simulation with your own gateway next."*

---

## Files Referenced (for your prep)

- Overview: [frontend/src/pages/firewall/AIMeshFirewallOverview.jsx](frontend/src/pages/firewall/AIMeshFirewallOverview.jsx)
- Sub-modules: [frontend/src/pages/firewall/firewall-submodules.jsx](frontend/src/pages/firewall/firewall-submodules.jsx)
- Detail layout: [frontend/src/components/firewall/SubmoduleDetailPage.jsx](frontend/src/components/firewall/SubmoduleDetailPage.jsx)
- Config: [frontend/src/pages/firewall/AIMeshFirewallConfig.jsx](frontend/src/pages/firewall/AIMeshFirewallConfig.jsx)
- Panels: GatewayKeyPanel, KillSwitchPanel, AttackSimulatorPanel, PolicyManagementPanel, VectorPolicyPanel, MCPScannerPanel (in `frontend/src/components/firewall/`)
- Navigation: [frontend/src/components/layout/Sidebar.jsx](frontend/src/components/layout/Sidebar.jsx) – AI Mesh Firewall and sub-items; Config = "Inputs"

Use this script as your runbook: follow the navigation, show the UI elements listed, and use the “Say” bullets (and 3 P angles) as your talking points. Customize customer name and examples (FirstCry, Amazon, Lenskart) as needed.