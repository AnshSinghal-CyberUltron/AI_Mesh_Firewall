# Product

## Register

product

## Users

Enterprise security engineers, platform/SRE admins, and AI-governance owners who
operate ZeroShield as a live control plane in front of production LLM traffic.
Their context is high-stakes and operational: they are configuring policy,
watching real-time defense telemetry, responding to incidents, and proving
compliance. They arrive already fluent in the domain (injection, PII/redaction,
OWASP-LLM, MCP, RAG trust) and want dense, trustworthy, fast-loading surfaces —
not onboarding hand-holding. The primary task on any given screen is *decide and
act on real data*, then verify the action took effect.

## Product Purpose

ZeroShield — AI Mesh Firewall & Multi-Model Governance — is the unified security
control plane for enterprise AI. It provides real-time input/output scanning and
redaction, prompt-injection & jailbreak defense, multi-model routing governance,
kill-switch / model-state control, MCP connector security & sandbox scanning,
RAG feature/trust testing, output guardrails, policy management, OWASP-LLM
posture, and full audit/log observability. Success = an operator can trust every
number on screen, take a governance action, and immediately see the system state
change — with zero secret/PII leakage and no ambiguity about what is real.

## Brand Personality

Authoritative, precise, and calm under pressure. Three words: **trustworthy,
technical, exact.** A security console speaks with quiet confidence — no
marketing exuberance, no playful mascoting. Density is respected, not feared.
The interface should feel like instrumentation you would stake an incident on.

## Anti-references

- Consumer-SaaS "cream/sand + soft gradient" dashboards — this is a security tool, not a wellness app.
- The hero-metric marketing template (giant number + gradient accent + supporting stats).
- Identical icon-card grids repeated endlessly; decorative glassmorphism; gradient text.
- Playful/rounded "friendly" styling that undercuts the seriousness of security decisions.
- Any surface that shows fake/demo/placeholder numbers as if they were live — trust is the product.

## Design Principles

1. **Every number is real or honestly absent.** Bind to backend data; when there is no data, say so with an intentional empty state. Never fabricate.
2. **Never leak.** Raw API keys, secrets, PII, and internal topology must never render — masking and redaction are first-class, not afterthoughts.
3. **State you can trust.** Every control reflects and mutates real state; after an action the UI proves the change (optimistic UI must reconcile).
4. **Dense but legible.** Operators want information density; earn it with strong hierarchy and AA-contrast, not by shrinking or graying text into illegibility.
5. **Both themes are production.** Dark and light are equal first-class targets, not a toggle afterthought — contrast, focus, hover, and disabled states must be correct in both.

## Accessibility & Inclusion

- Target **WCAG 2.1 AA**: body text ≥ 4.5:1, large/bold ≥ 3:1, placeholder text held to the same 4.5:1 (not muted-gray default).
- Visible keyboard focus on every interactive element in both themes; no focus trap on modals except intentional dialog focus management.
- `prefers-reduced-motion: reduce` honored for every animation (shimmer, lift, chart transitions).
- Color is never the sole signal for status (pair with icon/label); verify against common color-vision deficiencies.
- Responsive and non-overflowing at 1440 / 1024 / 768 / 375.
