# DESIGN_REVIEW — Routing isolation honesty

Five contradiction agents + Devil’s Advocate, 2026-09-14. Spec remains **DRAFT** until the operator approves.

Agents:

| ID | Lens | Cursor agent |
|---|---|---|
| 1 | Assume current code is correct (false positives) | [Agent 1](ff886bc7-2123-46c0-bbef-2207fe677ed1) |
| 2 | Assume fundamentally broken (hidden failures) | [Agent 2](ac6382e5-9805-473f-93fd-fbf597313c97) |
| 3 | State propagation | [Agent 3](66c6dbd2-0197-446a-bc5d-b912a951d4d2) |
| 4 | Observability | [Agent 4](901bb8ee-d4ae-48e6-b09e-0579e68bb2a5) |
| 5 | Edge cases | [Agent 5](c16569c3-ab0b-4775-abcd-f6c7e6d48c1f) |

## Devil’s Advocate (accepted)

**Agent 1 DROP Req 1/2/6/7 is REJECTED as a product decision.** The operator locked: Attack Simulator is a preference hint; `model_routing` must not look like org routing is off when 1.5 is Enabled; dead KS alias must not 404; live Vendor-incident KS must be cleared; already-masked PII must complete on a Callable model. PIPELINE-0028’s Attack pin was intentional **then**; it is the **wrong default now**. Reversing it for Module 1.1 is the work. SDK pin (`test_s9_*`) stays (Req 9).

**Agent 1 KEEP on composition is ACCEPTED.** Pin-without-KS and KS-with-audit are **two request shapes**. They do not both stamp `routing_disabled` and `kill_switch` on the same envelope (`main.py` 9862 vs 9867). The screenshot “Org routing is off … Haiku” is the **pin copy** (`routing_disabled`) after KS already rewrote `body.model` to the fallback **or** a pin-only request that landed on Haiku. Spec intro must not claim one HTTP response always contains skip + org-off + KS 404.

**Agent 1 “select_model after lock is theater” is PARTIALLY ACCEPTED.** Constraining remaining to `{callable fallback}` then scoring is still required so the stage is not skip and copy is not org-off. It is not a second weighted choice among many models. Copy must say isolation constrained the set.

**Agent 4 `honestStageAction` (allow + 0ms → UI skip) is ACCEPTED as in-scope.** Gateway may emit `action=allow` with `model_routing_ms=0.0`; the timeline still paints SKIPPED. That is a product lie after org-on routing “runs” in <1ms.

**Agent 2 CB Redis writer, stream sanitizer, `rewriteChatBodyModelAuto`, fail-open drop, `chain_next` catalog_scan are ACCEPTED as in-scope amendments** (same class as the live 404).

**Agent 3 Vite ≠ public, Redis leftover after deactivate, `?? true` while config loads are ACCEPTED.**

**Agent 5 Req 6.3 vs live CISO 403, scan-only ≠ routing proof, `fallback=auto`, `model=auto`+KS are ACCEPTED.**

**Rejected as this-spec scope (document only):** embeddings/RAG KS semantics overhaul; Cloudflare cache everything; rewriting all telemetry dual-emit 1.5 vs 1.6 (note in risks; do not block the honesty fix).

## Requirement verdict after DA

| Req | After DA |
|---|---|
| 1 Attack Sim prefs | **KEEP** (explicit PIPELINE-0028 reversal for 1.1 only) |
| 2 Org-on runs routing | **KEEP**, weaken “among remaining” copy when singleton |
| 3 Callable target | **KEEP** including 3.3; isolation-intent not leftover lock |
| 4 Save-time Callable | **KEEP**; will not catch LiteLLM-invalid id; 3.3 is backstop |
| 5 Copy | **KEEP** + 0ms UI skip + pass `org_routing_enabled` everywhere |
| 6 PII complete | **KEEP** as post-routing; 200 only if no terminal block |
| 7 Live KS clear | **KEEP** + Redis/ModelState/CB twins |
| 8 E2E + MIG | **KEEP**; Vite non-authoritative |
| 9 Non-goals | **KEEP** |

Amendments landed in `requirements.md` / `design.md` / `tasks.md` after this review.
