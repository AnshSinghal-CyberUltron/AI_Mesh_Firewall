# Module 3 — Phase 3 API Governance (Spreadsheet 3.3)

Network-edge kill-switch: **Envoy `ext_authz` → authz-shim → OPA Rego** token quotas per tenant/environment, synced from Module 3 control.

## Architecture

1. Control stores `ApiQuotaPolicy` + usage counters  
2. `module3-state-sync` polls `/api/module3/ingest/quota-snapshot/` and `PUT`s into OPA `data.module3.quotas`  
3. `llm-edge` Envoy forwards requests through `ext_authz` to `module3-authz-shim`  
4. Shim evaluates OPA `module3.authz.allow`; deny → 403 + governance ingest → Module 2 SOC  

## Bootstrap

Requires Phase 2 Kind stack (same chart):

```bash
export AGENT_API_KEY=...
export ORG_SLUG=zeroshield
export CONTROL_URL=http://host.docker.internal:8100
bash scripts/module3_kind_up.sh
python scripts/module3_phase3_seed_quotas.py
```

Compose `control` + `workers` must be up. Control `ALLOWED_HOSTS` must include `host.docker.internal`.

## Verify

```bash
python scripts/module3_phase3_e2e.py
```

Asserts: OPA synced, under-limit **200**, over-limit **403**, Module 3 deny event, Module 2 kill-switch incident.

## UI

`/infrastructure/api-governance` — policies and edge allow/deny events.

## Probe manually

```bash
# under-limit (acme/prod TPM=1000)
kubectl -n ai-mesh-m3 run curl-under --rm -i --restart=Never --image=curlimages/curl -- \
  curl -s -o /dev/null -w "%{http_code}\n" \
  -H "x-tenant-id: acme" -H "x-environment: prod" -H "x-estimated-tokens: 5" \
  http://llm-edge:8080/v1/chat

# over-limit (acme/dev TPM=10)
kubectl -n ai-mesh-m3 run curl-over --rm -i --restart=Never --image=curlimages/curl -- \
  curl -s -o /dev/null -w "%{http_code}\n" \
  -H "x-tenant-id: acme" -H "x-environment: dev" -H "x-estimated-tokens: 50" \
  http://llm-edge:8080/v1/chat
```
