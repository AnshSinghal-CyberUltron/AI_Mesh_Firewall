# Firewall 1.6 — Brainstorm (H1–H3)

## H1 (UX): org/key/model_name contract hidden on 1.6
**Validated:** No GatewayKeyPanel on Firewall16Page; CircuitBreakerSimulator uses free-text models.

## H2 (Data): empty ModelState when LLM configs exist
**Validated:** ModelState only created on isolate/PATCH get_or_create; no bootstrap from LLMModelConfig.

## H3 (Runtime): corrupt Redis kill_switch keys → 503
**Validated:** gateway kill_switch.py fails closed on malformed JSON; no operator UI repair.

## Mitigations
- De-dupe by root cause; file:line evidence; cross-check Critical with second agent.
