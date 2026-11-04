# Design Review (iteration 3 re-review): GW32 / GW17b / GW17c

Reviewed `design.md` (706 lines, includes "Iteration 3" responses) against `requirements.md` and the code in `.worktrees/gw32/gateway_v2`.

Verdict: **APPROVED** (0 HIGH, 0 MEDIUM, 2 LOW, 2 NIT).

Scope limit: I re-read the design's sections for identity, paths, sessions, credential refresh, threat table and implementation order, and spot-checked code. I did not re-read requirements 10-18 and 20-22 line by line, and did not open the control-side Django files.

## Prior findings status
- Prior HIGH (DNS rebinding on gateway OAuth calls): fixed. `resolve_checked` returns approved IPs; `PinnedTransport` dials only those with original Host/SNI; rebinding tests are specified (zero bytes to the second address); threat-table row added. Feasible with httpx/httpcore (SNI override via transport extension).
- Prior MEDIUM 2 (credential length 6/8): fixed; one constant `credential_min_len = 8` (DD-15).
- Prior MEDIUM 3 (path grammar vs `BROKER_SLUG`): fixed; parser calls `BROKER_SLUG.match` (verified `dispatch/mcp_broker.py:45` is `^(?!-)[A-Za-z0-9_.-]{1,64}(?<!-)$`); `.`/`..` rejected; separate `ORG_SEGMENT`.
- Prior MEDIUM 4 (session ZSET scores): fixed; `check` does PEXPIRE + `ZADD XX` on all three ZSETs under WATCH/MULTI; DD-2 corrected; property test specified.
- Prior LOW 5-13 and NIT 14-15: addressed in the design's iteration-3 list (compare-and-delete lock, attempts charged, `t0` before store call, well-known prefixes, transport defaults, issuer check, D-8 relabelled).

## Findings

### 1. LOW: refresh bullet still says "`check_remote` on the token endpoint" beside the pinned-dial rule
Where: "Refresh and `needs_reauth`", step 2, "Lock holder" bullet vs "Pinned dial" bullet.
The holder bullet names plain `check_remote`; the pinned bullet requires `resolve_checked` + `PinnedTransport` for the same call. An implementer could follow the weaker line.
Fix: change the holder bullet to "`resolve_checked` and the `PinnedTransport` dial on the token endpoint".

### 2. LOW: redirect wording is internally inconsistent
Where: same "Pinned dial" bullet: "Each redirect hop is not followed; a 3xx is a failure (setup follows at most `oauth_redirects` hops, each re-resolved and pinned)".
Refresh/token/DCR calls refuse 3xx; setup discovery follows up to `oauth_redirects`. The sentence reads as both.
Fix: split it: "token, refresh, DCR and callback calls: a 3xx is a failure. Discovery only: follow at most `oauth_redirects` hops, each re-resolved, re-checked and re-pinned."

### 3. NIT: `poll_once` call shape differs between sections
Where: "Credential client and cache" says `poll_once(extra_keys=cache.keys())` (a value); "Registry polling signature" defines `extra_keys: Callable[[], Iterable[str]]`.
Fix: use `poll_once(mget, extra_keys=cache.keys)` (callable) in the first place.

### 4. NIT: refresh `SET NX PX refresh_total_budget_s` lets the lock outlive a crashed holder for the full budget
Acceptable (losers fail with 503 inside the budget). State it in one sentence so it is not read as a defect later.

## Verified Assumptions
- `runtime/resources.py` and `edge/app.py` are each exactly 800 lines, so the no-edit plan holds.
- `BROKER_SLUG` regex as above; `url_guard.check_remote(... resolver ...)` exists with `Resolver = Callable[[str, int | None], Awaitable[Sequence[str]]]` (returns a refusal, not addresses), so a new `resolve_checked` is genuinely needed and additive.
- `McpRegistry.poll_once(self, mget)` is the shipped signature; the keyword-only default addition is backward compatible.
- Layering: `admit` with injected store client, `dispatch` with injected httpx transport, HTTP/WebSocket objects only in `edge`, `pinned_transport.py` in `dispatch` (httpx lazy) are consistent with the layer order.
- Capacity literals: new numbers live in `runtime/resources_mcp.py` with a gate allowlist change plus a test running the gate over every new module.
- GW17b secret paths covered: `RenderedHeaders` goes only into `upstream.headers`, `_request_id` excludes headers, a new AST gate `check_credential_flow` replaces the PEM-only gate, reflection guard withholds replies, failure paths carry codes only, pinned dial protects refresh/client secrets.

## Unverified / Wrong Assumptions
- Unverified by me: control-side behaviour (`IsGatewayInternalOnly`, `EncryptedCharField` serializers, throttle scopes, `lint/admin_ledger.py` accepting `retire -> build`). The design assigns confirmation to implementation step 1 and no design choice depends on a different answer; the ledger fallback (stay `retire`, list as DEFERRED) is stated.
- Unverified: requirements 10-18, 20-22 read in full (see scope limit).
- No wrong assumptions remain from the prior review.
