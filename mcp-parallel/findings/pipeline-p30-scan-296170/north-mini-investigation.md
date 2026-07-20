# PIPELINE-0030 — OpenRouter north-mini vs nemotron (scan zs-cbbd43e9b779)

## Question

Why did OpenRouter show `cohere/north-mini-code:free` when the Attack Simulator selected
`nvidia/nemotron-3-super-120b-a12b:free` and the pipeline trace said routing disabled?

## Evidence (gateway logs, request_id=zs-cbbd43e9b779)

```
llm_router: Requested model 'nvidia/nemotron-3-super-120b-a12b:free' is not active in router model groups; remapping to 'cohere/north-mini-code:free'
LiteLLM completion() model= cohere/north-mini-code:free; provider = openai
```

Separate tier-2 input scan (not the chat completion):

```
bedrock: BEDROCK REQUEST | reqid=zs-cbbd43e9b779 model=zeroshield-guard ... call_site=tier2_scan
```

## Conclusion

| Observation | Explanation |
|-------------|-------------|
| Trace "Routing disabled" | Attack Simulator sent `enable_routing: false` (PIPELINE-0028 pin); accurate. |
| OpenRouter north-mini | `LLMRouter._resolve_runtime_model` — requested nemotron not in `_active_model_names` → fallback to first active model (north-mini). Deployment/config gap, not UI trace bug. |
| Bedrock zeroshield-guard | Expected tier-2 scanner call; unrelated to chat completion model. |

## Remediation (ops, not code in PIPELINE-0030)

Register org BYOK nemotron in LiteLLM router model groups so `_active_model_names` includes
`nvidia/nemotron-3-super-120b-a12b:free` (or org-qualified `zeroshield::…` key). Until then,
remap warning is intentional fail-soft behavior in `llm_router.py` L823-828.
