# Output Guardrail Assessment — Locked Plan

**Date:** 2026-07-10  
**Swarm:** `swarm-1783661513101-m6hab9`  
**Evidence root:** `mcp-parallel/findings/output-guardrail-assessment/`

## Scope (user-confirmed)

- **Surface:** All egress, **OUTPUT-only** (chat, MCP results, RAG/tool egress to clients).
- **Process:** Full report first → fix by severity (auto-fix after report).
- **Matrix:** Literal full detector × action × path permutations.
- **Env:** Live shared stack; mutate §1.7 config then restore.
- **Proof:** Crafted output injection + live model spot-checks; OpenAI SDK priority.
- **Hallucination/rewrite:** Must enforce live (not degrade-only).
- **Policy:** CISO package + custom adversarial rules.
- **Parity:** UI ↔ runtime byte-proof is highest priority.
- **Models:** Every org-connected model.
- **Hardening posture:** Prefer fail-closed.

## AskQuestion note

`AskQuestion` is **not available** in this Cursor MCP catalog (search returned 0 tools). Clarifications used markdown Q&A.

## Phases

1. Map architecture (in progress — parallel explore agents)
2. Adversarial red team (20 attack lanes)
3. Config matrix + UI parity
4. Models + OpenAI SDK
5. Security report pack
6. Fix by severity + regression

## Non-goals (this engagement)

- Input-path scanning (except where it affects output honesty)
- Infra gVisor/egress lockdown (unless it blocks output proof)
