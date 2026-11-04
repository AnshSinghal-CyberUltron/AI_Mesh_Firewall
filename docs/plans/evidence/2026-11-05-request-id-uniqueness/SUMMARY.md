# Live check: request-id-uniqueness fix (task 5, 5 Nov 2026)

Branch `bugfix-f2-request-id`. Tasks 1–4 are done (offline gate green). This task runs the fix
against a REAL Docker stack and compares it to the unfixed code.

## Stacks

- **BEFORE = `amfint`** (compose project `amfint`, gateway-v2 at `127.0.0.1:38301`). This is the
  pre-existing integration live stack, built from the integration worktree and therefore running
  the OLD content-hash ids (finding F2). I did NOT stop, restart, rebuild or modify it. It is the
  BEFORE baseline only.
- **AFTER = `amff2`** (compose project `amff2`, gateway-v2 at `127.0.0.1:63301`), built and run
  from THIS worktree with the fix. Same compose files and override recipe as the integration run
  (`up.sh`, `docker-compose.amff2.yml`), adapted to the new project and ports and to this
  worktree's code. Minimal service set: gateway-v2, control, postgres, pgbouncer, redis, rabbitmq,
  the two state services, v1 gateway, mcp-broker/mcp-stub, and the fake OpenAI upstream +
  fake MCP server test doubles. Frontend not started (optional). `AMF_AUDIT_RETENTION_SWEEP` was
  never set. `V2_PLAN_CONTROLS_ENABLED`/`V2_PLAN_CONTROLS_ORGS` scoped to `zeroshield,dev-org` only.

### Port note (deviation from the brief)

The task asked for base **+60000** (control 68100, gateway-v2 68301, ...). Those numbers exceed
the maximum TCP port **65535** and Docker refuses them (`invalid hostPort: 66432` / `65770`). I
used base **+55000** instead so every mapped port stays ≤ 65535 (gateway-v2 → 63301, control →
63100, v1 gateway → 63300, redis → 61379, postgres → 60432, pgbouncer → 61432, mcp-broker →
63311). All bound to 127.0.0.1, all distinct from `amfint` (+30000) and the main tree (+20000).
No collisions (`ss -ltn` checked). Full map: `stack-ports.txt`.

### Containers before/after

`docker-ps-BEFORE.txt` vs `docker-ps-AFTER-full.txt`: every non-`amff2` container (all `amfint-*`
and all `ai_mesh_firewall-*`) is unchanged — same name and same state word before and after this
run. Only `amff2-*` resources were created. (`amfint` happened to already have the F2-era stack
running, which is exactly why it is the BEFORE baseline.)

## Results

PASS = executed on real HTTP/wire traffic and the criterion was met. FAIL = executed, criterion
not met. NOT RUN = not executed, reason given.

| # | Check | Result | Evidence / reason |
|---|---|---|---|
| a | BEFORE (amfint, old code): five identical calls per surface repeat one id | **PASS** | `amfint-five-identical.jsonl`, `before-after-uniqueness.json`. chat non-stream, chat stream, MCP `tools/list`, RAG each return ONE id ×5 (seq and concurrent): e.g. chat `zs-dev-org-822c92d6321115fcf0d2db33` ×5, stream `zs-dev-org-2752bf6f...` ×5, MCP `zs-dev-org-68da2c3c...` ×5, RAG `zs-dev-org-348d66d9...` ×5. The amfint audit stream shows the same collision at scale (one chat id appears 40×). Embeddings: see (b). |
| b | AFTER (amff2, fixed): five identical calls per surface return five DISTINCT `zs-{org}-<24 hex>` ids (2.1–2.4) | **PASS** for chat non-stream, chat stream, MCP `tools/list`, RAG | `amff2-five-identical.jsonl`, `before-after-uniqueness.json`. 5 distinct ids per surface, seq and concurrent, all matching `zs-dev-org-[0-9a-f]{24}`. Embeddings: **NOT RUN**, see (b-emb). |
| b-emb | Embeddings id uniqueness on the wire | **NOT RUN** | The dev echo profile answers `/v1/embeddings` with `200 {"object":"list","data":[]}` — no `request_id` in the body or headers, and it writes NO audit row (route `embeddings` count = 0 in `{rv2}:audit:dev-org`). So no id is observable on the wire, on EITHER stack (BEFORE embeddings ids are also null). The id IS minted internally via `begin_request`; embeddings uniqueness + mint-failure refusal are covered offline (property test A + `test_embeddings_mint_failure.py`). |
| b-parity | `x-request-id` == body id, and for chat stream the SSE start header == the id | **PASS** | `id-parity.json`. Chat stream: start header `x-request-id` = `zs-dev-org-26dda473...` and the SSE chunk id = `chatcmpl-zs-dev-org-26dda473...` (derived `chatcmpl-{request_id}`) — equal. Chat block/error envelope: `x-request-id` header = `error.request_id` = `zeroshield.request_id`. Chat 200-allow non-stream sets NO `x-request-id` header (frozen GW35 golden shape); the id is in the body `zeroshield.request_id`. |
| c | One chat request: audit row, control Scan Detail (EnforcementEvent) row and `x-request-id` share one id (2.6, 3.5); five identical prompts → five separate rows (1.6) | **PASS** | `control-scan-detail-rows.json`. Five identical prompts `conflation probe identical` → 5 distinct response ids. The amff2 audit stream `{rv2}:audit:dev-org` then held exactly those 5 distinct ids (10 rows = input+output phases). After `ingest_v2_audit`, control created **5 EnforcementEvent rows, one per request_id**, all 5 of my response ids present and distinct (no conflation). How queried: `docker exec amff2-control-1 python manage.py ingest_v2_audit` then `EnforcementEvent.objects` filtered on `metadata__request_id`. (Setup: ran `migrate` + created the `zeroshield` org on the fresh DB, as the integration run did.) |
| d | A client-supplied `X-Request-ID` is still not the canonical id (3.2) | **PASS** | `amff2-client-header-ignored.jsonl`. Three calls pinning `X-Request-ID: client-pinned-CAFEBABE-0001` → 3 distinct gateway ids, none containing the client value. |
| e | Zero-provider-call refusal refunds exactly the spent cost (3.6) and admission slots return to baseline (3.7) | **PASS** | `refund-and-burst.json`. 10 input-block refusals (prompt-injection, `input_scan=block`, model stages skip, all 400 `input_blocked`, 10 distinct ids). Budget lease `{rv2}:budget:dev-org:remaining` = 96723200 before AND after → net spend 0 (spend refunded). Admission: `amf_admit_org_limits_slots_used` returned to baseline 1, `amf_admit_queue_depth{queue="request"}` = 0, `amf_admit_fail_open_total` = 0, `amf_quota_lease_overshoot` = 0. Metrics used: the GW06 budget lease counter in redis for the refund, and `amf_admit_org_limits_slots_used` + `amf_admit_queue_depth{queue=request}` for the slot release. (NB: the earlier AKIA secret-block rule was overwritten when control republished the zeroshield plan on org creation (plan 1.1→1.2), so a prompt-injection block — a platform floor — is the stable zero-provider refusal here.) |
| e-burst | Concurrency burst: 50 identical chat, 10 at a time → 50 distinct ids, no leaked slots | **PASS** | `refund-and-burst.json`. 50 requests, 50 distinct ids, 0 null, all `zs-dev-org-[0-9a-f]{24}`; afterwards `slots_used` = 1 (baseline), `queue_depth{request}` = 0, `fail_open` = 0. |
| f | Control-plane `proxy_chat` path unchanged: `git diff --stat HEAD -- gateway/` is empty (3.9) | **PASS** | `git diff --stat HEAD -- gateway/` prints nothing; `git status` shows no tracked change under `gateway/`. PIPELINE-0023's `_bind_gateway_request_id` path is untouched; all edits are under `gateway_v2/` plus the spec/evidence. |
| g | Mint-failure refusal, forced live | **NOT RUN** | There is no runtime seam (no env var) to force `RequestIdUnavailable`; the entropy source is a function-default parameter injected only in tests. Forcing it live would require monkeypatching `secrets.token_hex` inside the running container, which is invasive and unrepresentative of a clean build. Covered offline: `test_request_ids.py` (failing/short/non-hex entropy → `RequestIdUnavailable`), `test_request_holds_begin_request.py` (propagation), the per-surface `test_*_mint_failure.py` (refuse with `request_id_unavailable` / 503, zero provider calls, no admit, no spend), and property test B (mint failure injected). |

**Counts: 8 PASS, 3 NOT RUN (b-emb, g, and embeddings within b).**

## Observations

- **GW28 routing with random ids (asked):** the dev profile builds exactly ONE passthrough
  candidate (`build_dev_routing`, `model_routing` deciding_rule `primary|dev|0|-|-`,
  `candidate_count` absent). Multi-candidate routing is wired only in the production profile
  (GW36, not built here), so the effect of a random request_id on routing CANNOT be observed on
  this stack. With a single candidate the id does not influence routing.
- **Embeddings** on the dev echo profile returns a 200 empty list with no id and no audit row;
  this is a profile limitation, not a fix gap (the same on the old amfint code).
- **Plan republish side effect:** creating the `zeroshield` org (needed for the control Scan Detail
  check) caused control to republish the plan (`plan_version` 1.1→1.2), which dropped the dev
  `dev-secret-block` AKIA rule. The injection/jailbreak input blocks are platform floors and
  remained, so the zero-provider refusal check used an injection prompt.

## Reproduce / teardown

- Bring up: `bash docs/plans/evidence/2026-11-05-request-id-uniqueness/up.sh` (project `amff2`).
- Drivers: `driver.py` (five-identical per surface, BEFORE+AFTER), `refund_check.py` (refund +
  burst). Control ingest: `manage.py ingest_v2_audit`.
- Teardown (only `amff2` resources): `COMPOSE_PROJECT_NAME=amff2 docker compose -f
  docker-compose.yml -f docker-compose.override.yml -f
  docs/plans/evidence/2026-11-05-request-id-uniqueness/docker-compose.amff2.yml --profile services
  down -v` then `docker rm -f amff2-fake-mcp amff2-fake-openai`.
