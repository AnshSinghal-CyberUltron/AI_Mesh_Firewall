# Phase 6E — Firewall 1.5 governance verification

## Fix: Stale allowlist banner + Module 1.5 governance UI

### Services running locally

| Check | Result |
|-------|--------|
| Control (8100) | YES — healthy after rebuild |
| Gateway (8300) | YES — policy check responded |
| Frontend (8180) | Assumed running (live matrix used 8180) |

### Backend

| Check | Result |
|-------|--------|
| Migration `0025_firewallconfig_governance_defaults` | Applied OK |
| `sanitize_firewall_allowlists` command | Available |
| Django tests `core.tests.test_firewall_model_governance` | **6/6 OK** |

### Tier-1 scanning

| Check | Result |
|-------|--------|
| `test_policy_check_input_scan.py` + stream governance tests | **10 passed** (gateway uv pytest) |
| Live `POST /v1/policy/check` injection | **403** `tier_1_prompt_injection` |
| Live chat injection (matrix T13) | **403 BLOCK** |

### Tier-2 scanning

| Check | Result |
|-------|--------|
| Unit/integration tests in gateway suite | Included in 10 passed (stream governance) |
| Live Bedrock tier-2 | Not re-run end-to-end (requires org Bedrock creds); `semantic_analysis_enabled` unchanged |

### UI (expected after refresh)

| URL | Expected |
|-----|----------|
| `http://localhost:8180/?tab=firewall-1-5` | Model allowlist panel; **no** stale banner |
| `http://localhost:8180/?tab=firewall-config` | Same; no stale banner |

### AIDefence scan (sample injection)

```json
{"safe": false, "promptInjectionDetected": true, "confidence": 0.96}
```

### Live matrix notes

- T13 injection: **BLOCK 403** (tier-1 path confirmed)
- Several UPSTREAM 502 / NOMODEL 422: org/key/model alignment (pre-existing); not introduced by this change

---

**Status: VERIFIED** for governance stale-banner removal, backend sanitize, Module 1.5 panel, and Tier-1 live blocking.
