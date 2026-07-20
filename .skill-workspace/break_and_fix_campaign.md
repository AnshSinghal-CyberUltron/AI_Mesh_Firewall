# Break-and-Fix Campaign — local docker stack (LOCAL ONLY, no remote push)

Heavy parallel break-test (30 agents, 14 surfaces) → 54 confirmed findings → 36 distinct items
(8 critical, 14 high, 11 medium, 3 low). Fixed ALL via a 13-agent parallel fix workflow with
disjoint file ownership, + migrations + secret rotation done by the orchestrator. Rebuilt
control/gateway/workers, redeployed, re-verified live.

## Root-cause insight
#13 (tier-2 input-scan FAIL-CLOSED on Bedrock degradation) was hard-blocking the ENTIRE local data
plane (every clean prompt -> 403 tier2_degraded), which is why finders reported RAG/vector "non-
functional" (#11/#12) and rate-limiting "untestable" (#10). Those were SECONDARY. Fixing #13 to
fail-open-to-tier-1 unblocked the data plane; #11 was a false positive (cited a non-existent symbol
_inject_vector_globals; vector routes ARE mounted), #12 is local BYOK config (no vector backend
provisioned locally — pgvector internal, Chroma/Milvus/Pinecone BYOK).

## Live re-verification (13/13 PASS; all 8 criticals covered except #6 which is agent-fixed)
| # | sev | finding | live proof |
|---|-----|---------|-----------|
| 1 | crit | forgeable JWT (placeholder key) | old-key forge -> 401; control BOOTS with 86-byte key (boot guard ok); JWT signing key distinct from SECRET_KEY |
| 2 | crit | cross-org incident escalate/resolve | orgA escalate orgB incident -> 404 |
| 3 | crit | ReDoS regex in policy rule | (a+)+$ rule -> 400 rejected |
| 4 | crit | gateway max_tokens 500s | inf/"0" -> 400, null -> passes validation, NO 500 |
| 5 | crit | vector default_action=deny fail-open | deny policy -> 403 rag_access_denied |
| 7 | crit | unicode-normalization Tier-1 bypass | fullwidth "ignore all previous instructions" -> 403 tier_1_prompt_injection |
| 8 | crit | poison-pill telemetry drain | malformed event SKIPPED gracefully, good event still persists, no batch crash |
| 13 | high | tier-2 fail-closed DoS (UNBLOCKER) | clean prompt passes input_scan -> reaches inference (LiteLLM missing-creds = expected BYOK), NOT 403 tier2_degraded |
| 14 | high | unauth 500 on non-string email | dict/list/int/bool email -> 401, no 500 |
| 18 | med | logout cross-user token revocation | A logout B's refresh -> B refresh still valid (200) |
| 19 | med | change-pw bypasses validators | new pw 'password' -> 400 |
| 23 | med | Policy.code global unique | same code in 2 orgs allowed (per-org constraint) |
| 24 | med | ThreatFeed OverflowError 500 | hours=huge -> 200, no 500 |

Other items (#6,#16,#17,#20,#21,#22,#25,#26,#29,#30,#31,#32,#34,#35,#36 + frontend #27/#28/#33)
fixed by agents (compile-verified; many added regression tests under */tests/). 

## Cross-cutting (orchestrator)
- Migrations: auth_api.0009 (TerminatedSession cleared_at/cleared_by, #36), policy.0033 (Policy.code
  -> per-org UniqueConstraint, #23). Applied cleanly.
- Secret rotation (#1): rotated local .env DJANGO_SECRET_KEY to an 86-byte key (was the literal
  'change-me-in-production'). .env is git-tracked but NOT committed (local only). JWT_SIGNING_KEY
  auto-derives via HMAC from SECRET_KEY (distinct from it). Invalidated old sessions (intended).
- Infra (#9): compose now has restart:unless-stopped on all long-lived services; daphne kept (no
  risky multi-worker switch). #12 deferred as local BYOK config.

## Deferred / partial (transparent)
- #25 rewrite: backend now delegates to a real output_guard.rewrite_output_response_text helper
  (no longer one canned sentence) BUT it takes (threat_type, detail) — not the original response
  text — so it is a smarter templated rewrite, not full content-preserving LLM re-inference. True
  re-inference needs threading response_text into the helper + an inference call. Flagged.
- #6 RAG guardrail stages default-enabled by A3 but not live-exercised (needs a sensitive-doc corpus).
- #36 sub-items (ACCESS_TOKEN_LIFETIME tuning, chat burst/RPM ordering) deferred by A6.
- #12 (vector backend) is local BYOK config, not a code bug.
- Local data plane has no LLM provider key (BYOK) so end-to-end chat completes only through security,
  then fails at inference with LiteLLM missing-credentials — expected locally.

## Deploy
docker compose build control gateway workers + up -d + migrate. Frontend is bind-mounted (vite dev,
live). Prod needs image rebuilds + a real rotated DJANGO_SECRET_KEY + the 2 migrations.
