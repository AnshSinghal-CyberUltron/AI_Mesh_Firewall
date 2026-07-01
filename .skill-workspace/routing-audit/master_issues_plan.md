# AI Mesh Routing — Master Issue Plan (Steps 1-3)

## Method
Step 1 trace (file:line map) + Step 2 backend validation on the LIVE local stack (org 3, slug zeroshield).
Testbed: 3 real models connected VIA THE FRONTEND (key typed in UI, never in env/code):
 haiku-cheap (anthropic/claude-3.5-haiku, prio90, restricted, HIPAA/SOC2), gpt4o-mini (openai/gpt-4o-mini, prio50, public),
 gemma-free (prio10, public). Validation by routing-probe + log capture + isolated function repro.

## CONFIRMED CORRECT (proven, not bugs)
- Weighted scoring honors priority / cost / latency: priority_weight=1 -> haiku-cheap(90); cost_weight=1 -> gemma-free(cheapest, log-confirmed). [C1]
- Hard filters correct: data_sensitivity=restricted -> haiku-cheap (only restricted); compliance_requirements=[HIPAA] -> haiku-cheap; [PCI] -> compliance_block (none). [C2]
- Config propagation correct: LLMModelConfig.save()/FirewallConfig.save() -> signal -> Redis -> gateway reload (verified 7-model reload). [C3]
- Isolated-function repro proves adjudicate picks rt-expensive/rt-hipaa correctly when fed candidates. [C4]

## FALSE POSITIVES CAUGHT BY EVIDENCE (would have been wrongly filed)
- FP1 "auto always routes to gemma-free / restricted 403": was the org model-isolation allowlist (FirewallConfig.allowed_models=['gemma-free']) correctly restricting routing. Not a bug.
- FP2 "weights/compliance ignored": wrong request keys. Correct keys: routing_preferences.{risk_weight,cost_weight,latency_weight,priority_weight} and compliance_requirements (NOT weights/required_compliance).
- FP3 "per-worker stale cache": post-restart behavior identical -> not staleness.

## CONFIRMED BUGS
### B1 [HIGH | routing+fallback] Model-state isolation (and kill-switch) BYPASSED by auto/adjudicate routing
- Root cause: _score_routing_models (llm_router.py:1321) hard-filters only on LLMModelConfig.is_active (DB), NOT runtime model_state:{org}:{model} isolation or kill_switch:*. Post-routing selected model (main.py:5887) not re-validated.
- Repro: isolate a model via ModelState(status=isolated); DIRECT request -> 503 blocked (correct); model=auto with priority weighting -> SELECTS + SERVES the isolated model (200, token usage logged, req zs-db083a29b73c).
- Expected: an isolated model must not be an auto-routing target; routing should exclude it / reroute.
- Blast radius: operator isolates a compromised/misbehaving model but auto traffic still lands on it. Governance control half-enforced.
- Evidence: code (llm_router.py:1321, main.py:5886-5887) + live logs (direct 503 vs auto 200-served).

## SUSPECTED BUGS (need triage challenge)
### S1 [MED | routing] Tie-break non-determinism
- When models score equally, selection is by candidate/storage order (stable sort), not a stable semantic key (priority,name). Two identical requests can route differently if storage order changes (e.g., after re-sync). Evidence: scoring harness ties differed from a name-ordered expectation.
### S2 [LOW/UX | routing] Explicit model request silently overridden
- With routing enabled, a concrete model=gpt4o-mini is treated as a SOFT preference; adjudicate served haiku-cheap instead. Documented as soft-preference, but customers may expect explicit model = honored. Needs product decision (header to force exact model?).
### S3 [INFO | observability] decision_source/telemetry divergence
- Reroute logged differently by trigger (adjudicator event_type=model_routed vs kill_switch/model_state) -> traces hard to correlate by event_type (from Step-1 trace).
