# Module 3 — Future Enhancements & Product Roadmap

> **Scope notice:** The capabilities described in this document are **not** part of the current Module 3 MVP. They represent the planned product evolution of AI Infrastructure Security and should not be presented as shipped features.

This roadmap outlines how Module 3 is expected to grow from a practical MVP into a comprehensive enterprise AI Infrastructure Security Platform. It is intended for product planning, client conversations about future direction, and engineering prioritization.

---

## Phase 1 – Enterprise Readiness (Near-Term)

Near-term enhancements that strengthen governance, visibility, and executive reporting on top of the current MVP foundations.

### 1. Policy Engine

Allow administrators to define custom deployment and admission policies instead of relying on fixed verification logic.

Example policies:

- Require signed images for production
- Allow unsigned images in development
- Restrict deployments to approved container registries
- Require specific model metadata before deployment

Policies should be editable through the UI and enforceable through admission verification.

### 2. AI Asset Inventory

Maintain a centralized inventory of all AI assets.

Each asset should include:

- Model name
- Version
- Owner
- Environment
- Cluster
- Registry
- Deployment status
- Risk level
- Last deployment time

Provide filtering and search capabilities.

### 3. Deployment Timeline

Track the lifecycle of every AI model deployment.

Timeline should include:

- Build
- Signature
- Registration
- Verification
- Deployment
- Runtime Status

Help administrators understand where failures occur.

### 4. AI Infrastructure Health Score

Calculate an overall security score for each cluster or environment.

Factors may include:

- Signed model coverage
- Sidecar coverage
- mTLS coverage
- Runtime agent availability
- Failed admission attempts
- Quarantined embeddings

Display as an executive dashboard KPI.

---

## Phase 2 – Advanced Security & Operations

Operational depth for production environments: approvals, drift detection, trust boundaries, auditability, and richer risk signal.

### 1. Deployment Approval Workflow

Allow deployments to require security approval before production.

Support approval chains such as:

- Developer → Security Reviewer → Production Deployment

### 2. Configuration Drift Detection

Detect differences between approved deployment configurations and live Kubernetes workloads.

Highlight unexpected model versions, image changes, or security configuration drift.

### 3. Registry Trust Management

Allow administrators to define trusted container registries.

Display registry trust status for every deployed model.

Warn about deployments originating from unknown registries.

### 4. Deployment History & Rollback Insights

Maintain historical deployment records.

Show version history and rollback events.

Help audit production changes over time.

### 5. Enhanced Risk Scoring

Calculate deployment risk based on multiple security signals rather than simple pass/fail checks.

Explain why a deployment is considered High, Medium, or Low risk.

---

## Phase 3 – Long-Term Vision

The following items are documented as future product vision capabilities. They are directional goals for a mature enterprise platform and are not committed deliveries of the current release.

> **Note:** Gateway Cosign **blob** and **OCI image** verification (`MODULE3_ADMISSION_MODE=verify`) is already shipped for Module 3 Phase 1 — see [MODULE3_COSIGN_PRODUCTION.md](./MODULE3_COSIGN_PRODUCTION.md). The items below extend that foundation (keyless Fulcio/Rekor-centric trust policies, policy-controller, multi-cluster attestation, etc.).

- Advanced Sigstore trust roots (keyless Fulcio identity policies, Rekor-centric attestation workflows, policy-controller)
- SBOM (Software Bill of Materials) support
- CVE and container vulnerability scanning
- Multi-cluster management
- Automatic policy remediation
- AI model lineage tracking
- Canary deployment monitoring
- GPU utilization and AI infrastructure resource monitoring
- Compliance evidence automation
- Automatic incident enrichment using Module 2
- Cross-cluster AI security posture management

---

## Roadmap Note

The current Module 3 implementation focuses on delivering a practical MVP for AI infrastructure security, covering model artifact management, Cosign admission verification, Kubernetes runtime visibility via ingest APIs, and SOC integration. The capabilities described above represent the planned evolution toward a comprehensive enterprise AI Infrastructure Security Platform.
